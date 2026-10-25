import unittest
from app.audit import assess
from app.bibtex import BibTeXError, parse_bibtex
from app.bib_export import export_bib, patch_entry, preview_export
from app.acl import anthology_id, collect_acl


class ExportTests(unittest.TestCase):
    def task(self, text):
        entries = parse_bibtex(text)['entries']
        for i, entry in enumerate(entries):
            entry.update(id=str(i), status='completed')
            entry['result'] = {'accepted_changes': {'pages': '1--9'}, 'assessments': [
                {'field': 'pages', 'original': entry['fields'].get('pages'), 'suggested': '1--9',
                 'judgment': 'completion', 'evidence_ids': ['source'], 'reason': 'official record'}]}
        return {'id': 'task', 'filename': 'test.bib', 'mode': 'live', 'entries': entries}

    def test_preserves_bom_crlf_macros_comments_and_untouched_entries(self):
        text = '\ufeff% header\r\n@string{j = "Venue"}\r\n@preamble{"keep"}\r\n@article{a,\r\n title={Protected {ABC}}, % keep me\r\n author={A},year={2024},journal=j,custom={unchanged}\r\n}\r\n% between\r\n@article{b,title={B},author={B},year={2024}}\r\n'
        task = self.task(text)
        result = export_bib(task, text.encode(), [{'entry_id': '0', 'choice': 'current'}])
        self.assertTrue(result['bibtex'].startswith('\ufeff% header\r\n@string{j = "Venue"}'))
        self.assertIn('journal=j,custom={unchanged}', result['bibtex'])
        self.assertIn('% keep me\r\n', result['bibtex'])
        self.assertIn('@preamble{"keep"}', result['bibtex'])
        self.assertIn(task['entries'][1]['raw'], result['bibtex'])
        self.assertEqual(parse_bibtex(result['bibtex'])['entries'][0]['fields']['pages'], '1--9')
        self.assertEqual(export_bib(task, text.encode(), [])['bibtex'].encode(), text.encode())

    def test_partial_selection_and_no_client_edits(self):
        text = '@article{a,title={A},author={B},year={2024}}'
        task = self.task(text)
        self.assertEqual(export_bib(task, text.encode(), [{'entry_id': '0', 'choice': 'current', 'fields': []}])['bibtex'], text)
        for selection in ({'entry_id': '0', 'choice': 'current', 'fields': ['title']},
                          {'entry_id': '0', 'choice': 'current', 'changes': {'title': 'fake'}}):
            with self.assertRaises(BibTeXError):
                export_bib(task, text.encode(), [selection])

    def test_duplicate_keys_and_malformed_stay_unchanged(self):
        text = '@article{a,title={A},author={B},year={2024}}\n@article{a,title={Other},author={C},year={2024}}'
        task = self.task(text)
        self.assertFalse(any(row['eligible'] for row in preview_export(task, text.encode())['entries']))
        with self.assertRaises(BibTeXError):
            export_bib(task, text.encode(), [{'entry_id': '0', 'choice': 'current'}])
        self.assertEqual(export_bib(task, text.encode(), [])['bibtex'], text)

    def test_field_change_preserves_inline_comment(self):
        entry = parse_bibtex('@article{a,title={A},author={B},year={2024},pages={2} % comment\n}')['entries'][0]
        result = patch_entry(entry, {'pages': '1--8', 'publisher': 'ACL'})
        self.assertIn('pages={1--8}, % comment\n', result)
        self.assertEqual(parse_bibtex(result)['entries'][0]['fields']['publisher'], 'ACL')

    def test_crossref_parent_not_modified(self):
        text = '@proceedings{p,title={Venue},year={2024},editor={E}}\n@inproceedings{a,title={A},author={B},crossref={p}}'
        task = self.task(text)
        result = export_bib(task, text.encode(), [{'entry_id': '1', 'choice': 'current'}])
        self.assertTrue(result['bibtex'].startswith(task['entries'][0]['raw']))
        self.assertIn('crossref={p}', result['bibtex'])
        with self.assertRaises(BibTeXError):
            patch_entry(task['entries'][1], {'year': None})


class ACLTests(unittest.TestCase):
    def record(self, fields):
        return {'id': 'acl:1', 'provider': 'acl', 'method': 'api', 'provenance': 'acl_anthology_bibtex',
                'version': 'published', 'fields': fields}

    def test_explicit_ids_only(self):
        self.assertEqual(anthology_id({'url': 'https://aclanthology.org/2023.acl-long.1/'}), '2023.acl-long.1')
        self.assertEqual(anthology_id({'doi': '10.18653/v1/P19-1001'}), 'P19-1001')
        self.assertEqual(anthology_id({'title': 'ACL paper'}), '')
        self.assertEqual(anthology_id({'url': 'https://aclanthology.org.evil.test/2023.acl-long.1/'}), '')

    def test_official_type_and_venue_atomic_export(self):
        text = '@article{a,title={A Study},author={Smith, Jane},year={2023},journal={ACL},note={keep}}'
        entry = parse_bibtex(text)['entries'][0]
        record = self.record({'title': 'A Study', 'author': 'Smith, Jane', 'year': '2023', 'entrytype': 'inproceedings',
                              'booktitle': 'Proceedings of ACL', 'publisher': 'ACL', 'month': 'July', 'editor': 'Editor, A'})
        entry.update(id='a', status='completed', result=assess(entry, [record]))
        self.assertEqual(entry['result']['suggested_entry_type'], 'inproceedings')
        task = {'id': 't', 'filename': 'test.bib', 'entries': [entry], 'mode': 'live'}
        result = export_bib(task, text.encode(), [{'entry_id': 'a', 'choice': 'current'}])
        parsed = parse_bibtex(result['bibtex'])['entries'][0]
        self.assertEqual(parsed['entry_type'], 'inproceedings')
        self.assertNotIn('journal', parsed['fields'])
        self.assertEqual(parsed['fields']['booktitle'], 'Proceedings of ACL')
        self.assertEqual(parsed['fields']['note'], 'keep')
        with self.assertRaises(BibTeXError):
            export_bib(task, text.encode(), [{'entry_id': 'a', 'choice': 'current', 'fields': ['entry_type']}])

    def test_wrong_paper_and_preprint_do_not_get_type_changed(self):
        record = self.record({'title': 'A Study', 'author': 'Smith, Jane', 'year': '2023', 'entrytype': 'inproceedings', 'booktitle': 'ACL'})
        for text in ('@article{a,title={Different},author={Jones, A},year={2023}}',
                     '@misc{a,title={A Study},author={Smith, Jane},year={2023},eprint={2301.12345},archiveprefix={arXiv}}'):
            self.assertIsNone(assess(parse_bibtex(text)['entries'][0], [record])['suggested_entry_type'])

    def test_tacl_article_not_forced_into_conference(self):
        entry = parse_bibtex('@article{a,title={A Study},author={Smith, Jane},year={2023},journal={TACL}}')['entries'][0]
        record = self.record({'title': 'A Study', 'author': 'Smith, Jane', 'year': '2023', 'entrytype': 'article', 'journal': 'TACL'})
        self.assertIsNone(assess(entry, [record])['suggested_entry_type'])

    def test_fetch_uses_request_callback_and_rejects_wrong_endpoint_record(self):
        calls = []
        def request(*args, **kwargs):
            calls.append((args, kwargs))
            return {'data': '@inproceedings{x,title={A},author={B},year={2023},url={https://aclanthology.org/2023.acl-long.2/}}'}
        with self.assertRaises(ValueError):
            collect_acl({'url': 'https://aclanthology.org/2023.acl-long.1/'}, request, lambda *args, **kwargs: {})
        self.assertEqual(calls[0][0][2], 'https://aclanthology.org/2023.acl-long.1.bib')
        self.assertTrue(calls[0][1]['text'])

class ExportAPITests(unittest.TestCase):
    def test_preview_export_rejects_untrusted_edits_and_preserves_original(self):
        import tempfile
        from fastapi.testclient import TestClient
        from app.main import create_app
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, start_worker=False)
            with TestClient(app) as client:
                text = b'% keep\r\n@article{a,title={A},author={B},year={2024}}\r\n'
                response = client.post('/api/tasks', files={'file': ('x.bib', text)}, data={'mode': 'mock'})
                identity = response.json()['id']
                while app.state.worker.run_next():
                    pass
                preview = client.get(f'/api/tasks/{identity}/export-preview')
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertIn('entries', preview.json())
                original = client.post(f'/api/tasks/{identity}/export', json={'selections': []})
                self.assertEqual(original.status_code, 200, original.text)
                self.assertEqual(original.json()['bibtex'].encode(), text)
                bad = client.post(f'/api/tasks/{identity}/export', json={'selections': [{'entry_id': 'other', 'choice': 'current'}]})
                self.assertEqual(bad.status_code, 422)
                extra = client.post(f'/api/tasks/{identity}/export', json={'selections': [], 'bibtex': 'injected'})
                self.assertEqual(extra.status_code, 422)
                self.assertEqual(client.get(f'/api/tasks/{identity}/source').content, text)

class ExportSafetyTests(unittest.TestCase):
    task = ExportTests.task
    def test_stale_evidence_cannot_be_exported_as_new_correction(self):
        text = '@article{a,title={A},author={B},year={2024}}'
        task = self.task(text)
        task['entries'][0]['result']['evidence'] = [{'id': 'source', 'cache_state': 'stale'}]
        row = preview_export(task, text.encode())['entries'][0]
        self.assertFalse(row['eligible'])
        self.assertIn('revalidation', row['reason'])
        with self.assertRaises(BibTeXError):
            export_bib(task, text.encode(), [{'entry_id': '0', 'choice': 'current'}])

    def test_expression_internal_comments_not_lost(self):
        text = '@string{x="A"}\n@article{a,title=x % protected comment\n # {B},author={C},year={2024}}'
        entry = parse_bibtex(text)['entries'][0]
        with self.assertRaises(BibTeXError):
            patch_entry(entry, {'title': 'Different'})
        result = patch_entry(entry, {'pages': '1--9'})
        self.assertIn('title=x % protected comment\n # {B}', result)

    def test_publication_requires_explicit_whole_version_choice(self):
        from test_audit import record
        text = '% original\n@article{KeepKey,title={An exact research title},author={Smith, John and Doe, Jane},year={2024},eprint={2401.12345v1},archiveprefix={arXiv},journal={arXiv},custom={keep me},volume={old}}'
        entry = parse_bibtex(text)['entries'][0]
        pre = record('pre', version='preprint', eprint='2401.12345v1')
        pre['related_doi'] = '10.1234/final'
        pre['relations'] = [{'type': 'is-preprint-of', 'target_doi': '10.1234/final', 'source': 'arxiv.doi'}]
        pub = record('pub', title='The renamed final work', year='2025', doi='10.1234/final', journal='Final Journal', eid='e17')
        entry.update(id='a', status='completed', result=assess(entry, [pre, pub]))
        task = {'id': 't', 'filename': 'x.bib', 'entries': [entry]}
        self.assertIsNotNone(preview_export(task, text.encode())['entries'][0]['publication'])
        self.assertEqual(export_bib(task, text.encode(), [])['bibtex'], text)
        with self.assertRaises(BibTeXError):
            export_bib(task, text.encode(), [{'entry_id': 'a', 'choice': 'publication', 'fields': ['title']}])
        output = export_bib(task, text.encode(), [{'entry_id': 'a', 'choice': 'publication'}])['bibtex']
        self.assertTrue(output.startswith('% original\n'))
        fields = parse_bibtex(output)['entries'][0]['fields']
        self.assertEqual(fields['custom'], 'keep me')
        self.assertEqual(fields['title'], 'The renamed final work')
        self.assertNotIn('eprint', fields)
        self.assertNotIn('volume', fields)

    def test_acl_provider_uses_guarded_transport_and_records_provenance(self):
        import httpx
        from app.providers import ProviderService
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, text='@inproceedings{x,title={A Study},author={Smith, Jane},year={2023},booktitle={ACL},url={https://aclanthology.org/2023.acl-long.1/}}')
        service = ProviderService({'mode': 'live', 'budget': {'per_entry': 4}}, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None)
        result = service.collect({'fields': {'url': 'https://aclanthology.org/2023.acl-long.1/'}}, sources=['acl'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(str(calls[0].url), 'https://aclanthology.org/2023.acl-long.1.bib')
        self.assertEqual(result['records'][0]['provenance'], 'acl_anthology_bibtex')
        self.assertEqual(result['records'][0]['fields']['entrytype'], 'inproceedings')
        self.assertEqual(result['calls'][0]['operation'], 'anthology_bibtex')


if __name__ == '__main__':
    unittest.main()
