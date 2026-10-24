import unittest
from unittest.mock import patch

from app.bibtex import BibTeXError, parse_bibtex, render_suggestion


class BibTeXParserTests(unittest.TestCase):
    def one(self, text):
        return parse_bibtex(text)["entries"][0]

    def test_exact_raw_snapshot_and_unicode(self):
        source = '\ufeff% comment\r\n  @Article { Key,\r\n title={中文 {GPU}}, author={张三}, year=2024 }\r\n'
        entry = self.one(source)
        self.assertEqual(entry['raw'], source[source.index('@'):source.rfind('}') + 1])
        self.assertEqual(entry['fields']['title'], '中文 {GPU}')
        self.assertTrue(entry['is_chinese'])

    def test_nested_braces_quotes_escapes_and_parentheses(self):
        entry = self.one(r'''@article(k, title="A {nested \"quote\"} and \{ brace", author={Garc{\'i}a, A.}, year=2024)''')
        self.assertIn(r'\"quote\"', entry['fields']['title'])
        self.assertIn(r"Garc{\'i}a", entry['fields']['author'])
        self.assertFalse(entry['issues'])

    def test_comments_and_preamble_are_not_entries(self):
        result = parse_bibtex('''% @article{fake, title={X}}
@comment{a {nested} comment}
@preamble{"\\newcommand{\\x}{x}"}
@article{real,title={T},author={A},year=2024}''')
        self.assertEqual([e['citation_key'] for e in result['entries']], ['real'])

    def test_forward_macros_concatenation_and_month(self):
        entry = self.one('''@article{k,title=prefix # " world",author={A},year=2024,month=jan}
@string{prefix=later}
@string{later={Hello}}''')
        self.assertEqual(entry['fields']['title'], 'Hello world')
        self.assertEqual(entry['fields']['month'], 'January')
        self.assertFalse(entry['issues'])

    def test_undefined_and_cyclic_macros_reported(self):
        entry = self.one('@string{a=b}\n@string{b=a}\n@misc{k,title=a,author=unknown,year=2024}')
        self.assertTrue(any('Cyclic' in issue for issue in entry['issues']))
        self.assertTrue(any('Undefined' in issue for issue in entry['issues']))

    def test_crossref_inherits_booktitle_not_parent_author_or_title(self):
        entries = parse_bibtex('''@inproceedings{child,title={Child},author={Me},crossref={parent}}
@proceedings{parent,title={Proceedings},editor={Editor},year=2025,publisher={Publisher}}''')['entries']
        self.assertEqual(entries[0]['fields']['title'], 'Child')
        self.assertEqual(entries[0]['fields']['booktitle'], 'Proceedings')
        self.assertEqual(entries[0]['fields']['year'], '2025')
        self.assertEqual(entries[0]['fields']['author'], 'Me')

    def test_crossref_cycle_missing_and_duplicate_are_reported(self):
        result = parse_bibtex('''@misc{a,crossref={b}}
@misc{b,crossref={a}}
@misc{c,crossref={missing}}
@misc{d,crossref={same}}
@misc{same,title={One}}
@misc{same,title={Two}}''')
        self.assertTrue(any('Cyclic crossref' in s for s in result['issues']))
        self.assertTrue(any('missing or ambiguous' in s for s in result['issues']))
        self.assertEqual(len(result['entries']), 6)
        self.assertTrue(any('Duplicate citation key' in s for s in result['issues']))

    def test_malformed_entry_does_not_hide_next_entry(self):
        result = parse_bibtex('''@article{broken,title={Unclosed
@article{okay,title={Fine},author={A},year=2024}
@article{last,title={Last},author={B},year=2025}''')
        self.assertEqual([entry['citation_key'] for entry in result['entries']], ['broken', 'okay', 'last'])
        self.assertEqual(result['entries'][1]['fields']['title'], 'Fine')
        self.assertTrue(result['entries'][0]['issues'])

    def test_entry_header_text_inside_valid_literal_not_recovered(self):
        entry = self.one('''@misc{k,title={Document example:
@article{example}
End},author={A},year=2024}''')
        self.assertIn('@article{example}', entry['fields']['title'])
        self.assertFalse(entry['issues'])

    def test_missing_comma_fields_recovered(self):
        entry = self.one('@article{k,title={T} author={A}, nonsense,year=2024}')
        self.assertEqual(entry['fields']['author'], 'A')
        self.assertEqual(entry['fields']['year'], '2024')
        self.assertTrue(any('Missing comma' in item for item in entry['issues']))

    def test_newline_before_key_comma(self):
        entry = self.one('@article{k\n ,title={T},author={A},year=2024}')
        self.assertEqual(entry['citation_key'], 'k')
        self.assertFalse(entry['issues'])

    def test_duplicate_field_retains_first_and_reports(self):
        entry = self.one('@misc{k,title={First},title={Second},author={A},year=2024}')
        self.assertEqual(entry['fields']['title'], 'First')
        self.assertTrue(any('Duplicate field' in item for item in entry['issues']))

    def test_fingerprint_ignores_format_order_and_key_not_custom_fields(self):
        a = self.one('@article{k,title={A title},author={A},year=2024,custom={foo}}')
        b = self.one('@ARTICLE(new, custom="foo", year = {2024}, author = "A", TITLE="A   title")')
        c = self.one('@article{k,title={A title},author={A},year=2024,custom={bar}}')
        self.assertEqual(a['fingerprint'], b['fingerprint'])
        self.assertNotEqual(a['fingerprint'], c['fingerprint'])

    def test_fingerprint_tracks_inherited_macro_changes(self):
        template = '@string{t={%s}}\n@article{k,title=t,author={A},crossref={p}}\n@misc{p,year=2024}'
        self.assertNotEqual(self.one(template % 'One')['fingerprint'], self.one(template % 'Two')['fingerprint'])

    def test_source_and_entry_limits(self):
        with patch('app.bibtex.MAX_SOURCE_CHARS', 5):
            with self.assertRaises(BibTeXError):
                parse_bibtex('@misc{k}')
        with patch('app.bibtex.MAX_ENTRIES', 1):
            with self.assertRaises(BibTeXError):
                parse_bibtex('@misc{a}\n@misc{b}')

    def test_depth_limits_and_recovery(self):
        source = '@misc{deep,title={' + '{' * 140 + 'x' + '}' * 140 + '}}\n@misc{okay,title={T}}'
        result = parse_bibtex(source)
        self.assertEqual(result['entries'][-1]['citation_key'], 'okay')
        self.assertTrue(any('nesting' in s for s in result['issues']))

    def test_macro_expansion_has_bounded_size(self):
        source = '@string{x={1234567890}}\n@string{y=x # x # x}\n@misc{k,title=y # y}'
        with patch('app.bibtex.MAX_VALUE_CHARS', 25):
            result = parse_bibtex(source)
        self.assertTrue(any('Expanded field exceeds' in s for s in result['issues']))
        self.assertLess(len(result['entries'][0]['fields']['title']), 100)

    def test_total_expansion_limit(self):
        with patch('app.bibtex.MAX_TOTAL_VALUE_CHARS', 5):
            with self.assertRaises(BibTeXError):
                parse_bibtex('@misc{k,title={123456}}')

    def test_render_preserves_key_custom_tex_and_does_not_mutate(self):
        entry = self.one(r'@article{myKey,title={{GPU} and {\LaTeX}},author={A},year=2024,note={Keep \% exactly},custom={中文}}')
        old_fields = dict(entry['fields'])
        rendered = render_suggestion(entry, {'year': '2025', 'doi': '10.1234/a_b'})
        self.assertIn('@article{myKey,', rendered)
        self.assertIn(r'{{GPU} and {\LaTeX}}', rendered)
        self.assertIn(r'{Keep \% exactly}', rendered)
        self.assertIn('{10.1234/a_b}', rendered)
        self.assertEqual(entry['fields'], old_fields)
        self.assertEqual(self.one(rendered)['fields']['custom'], '中文')

    def test_suggestion_is_standalone_after_macro_resolution(self):
        entry = self.one('@string{x={A title}}\n@misc{k,title=x,author={A},year=2024}')
        reparsed = self.one(render_suggestion(entry, {}))
        self.assertEqual(reparsed['fields']['title'], 'A title')
        self.assertFalse(reparsed['issues'])

    def test_suggestion_escapes_unbalanced_metadata_and_tex_specials(self):
        entry = self.one('@misc{k,title={Old},author={A},year=2024}')
        rendered = render_suggestion(entry, {'title': 'A & B } {C', 'custom': None})
        self.assertIn(r'A \& B \} \{C', rendered)
        self.assertEqual(len(parse_bibtex(rendered)['entries']), 1)

    def test_invalid_suggestion_fields_keys_and_types_rejected(self):
        entry = self.one('@misc{k,title={T}}')
        for changes in ({'bad key': 'X'}, {'title': {'nested': 'X'}}):
            with self.assertRaises(BibTeXError):
                render_suggestion(entry, changes)
        with self.assertRaises(BibTeXError):
            render_suggestion(entry, {}, 'article}injected')

    def test_conflicting_macro_redefinitions_remain_explicitly_unresolved(self):
        result = parse_bibtex('@string{x={First}}\n@misc{k,title=x}\n@string{x={Second}}')
        self.assertEqual(result['entries'][0]['fields']['title'], 'x')
        self.assertTrue(any('Ambiguous redefinition' in issue for issue in result['issues']))

    def test_crossref_chain_is_bounded(self):
        source = '\n'.join('@misc{k%d,crossref={k%d}}' % (i, i + 1) for i in range(140))
        result = parse_bibtex(source)
        self.assertEqual(len(result['entries']), 140)
        self.assertTrue(any('depth limit' in issue for issue in result['issues']))

    def test_malformed_duplicate_semantics_do_not_share_a_fingerprint(self):
        a = self.one('@misc{k,title={First},title={Second}}')
        b = self.one('@misc{k,title={First},title={Third}}')
        self.assertNotEqual(a['fingerprint'], b['fingerprint'])

    def test_xdata_limit_is_explicit(self):
        entry = self.one('@misc{k,title={T},author={A},year=2024,xdata={data}}')
        self.assertTrue(any('xdata inheritance is not supported' in s for s in entry['issues']))


if __name__ == '__main__':
    unittest.main()
