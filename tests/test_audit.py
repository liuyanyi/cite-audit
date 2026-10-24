import copy
import unittest

from app.audit import assess, authors_equivalent, normalize_doi
from app.bibtex import parse_bibtex


def entry(**fields):
    values = {'title': 'An exact research title', 'author': 'Smith, John and Doe, Jane', 'year': '2024', **fields}
    return {'citation_key': 'KeepKey', 'entry_type': 'article', 'fields': values, 'raw': '@article{KeepKey,}', 'issues': []}


def record(identifier='r1', version='published', method='api', **fields):
    values = {'title': 'An exact research title', 'author': 'John Smith and Jane Doe', 'year': '2024', **fields}
    return {'id': identifier, 'provider': 'crossref', 'source_id': identifier, 'url': 'https://example.org/' + identifier,
            'retrieved_at': '2026-10-24T00:00:00+00:00', 'expires_at': '2027-01-01T00:00:00+00:00',
            'fields': values, 'raw': {}, 'method': method, 'provenance': 'api', 'version': version}


def field(result, name):
    return next(item for item in result['assessments'] if item['field'] == name)


class EvidenceAuditTests(unittest.TestCase):
    def test_exact_record_doi_and_author_abbreviations(self):
        result = assess(entry(author='Smith, J. and Doe, J.', doi='https://doi.org/10.1234/TEST'), [record(doi='10.1234/test')])
        self.assertEqual(result['identity_state'], 'unique')
        self.assertEqual(result['doi_state'], 'verified')
        self.assertEqual(field(result, 'author')['judgment'], 'normalization')
        self.assertIsNone(field(result, 'author')['suggested'])
        self.assertEqual(result['field_state'], 'consistent')

    def test_author_order_not_interchangeable(self):
        self.assertFalse(authors_equivalent('John Smith and Jane Doe', 'Jane Doe and John Smith'))
        self.assertTrue(authors_equivalent('Smith, J. R.', 'John Robert Smith'))
        self.assertFalse(authors_equivalent('John Smith', 'Jane Smith'))
        self.assertTrue(authors_equivalent('Smith, J.\nand Doe, J.', 'John Smith and Jane Doe'))
        self.assertTrue(authors_equivalent('{Research and Development Group}', '{Research and Development Group}'))

    def test_wrong_doi_does_not_replace_work(self):
        source = entry(doi='10.1234/wrong')
        wrong = record('wrong', title='A completely different paper', author='Someone Else', doi='10.1234/wrong')
        result = assess(source, [wrong])
        self.assertEqual(result['identity_state'], 'insufficient')
        self.assertEqual(result['doi_state'], 'conflict')
        self.assertNotIn('A completely different paper', result['suggested_bibtex'])
        self.assertEqual(field(result, 'doi')['evidence_ids'], ['wrong'])

    def test_wrong_doi_can_be_corrected_only_with_matching_work(self):
        wrong = record('wrong', title='Unrelated', author='Someone Else', doi='10.1234/wrong')
        correct = record('correct', doi='10.1234/correct')
        result = assess(entry(doi='10.1234/wrong'), [wrong, correct])
        self.assertEqual(result['identity_state'], 'unique')
        self.assertEqual(result['doi_state'], 'conflict')
        self.assertEqual(field(result, 'doi')['suggested'], '10.1234/correct')
        self.assertEqual(field(result, 'doi')['evidence_ids'], ['correct'])

    def test_similar_title_is_not_unique(self):
        result = assess(entry(), [record(title='An exact research title: An extended study', doi='10.1234/extended')])
        self.assertEqual(result['identity_state'], 'insufficient')
        self.assertTrue(result['candidates'])
        self.assertTrue(all(item['suggested'] is None for item in result['assessments']))

    def test_matching_metadata_with_two_dois_is_ambiguous(self):
        result = assess(entry(), [record('a', doi='10.1234/a'), record('b', doi='10.1234/b')])
        self.assertEqual(result['identity_state'], 'multiple')
        self.assertTrue(all(item['suggested'] is None for item in result['assessments']))

    def test_same_doi_records_are_one_identity_but_field_conflict_visible(self):
        result = assess(entry(doi='10.1234/a', volume='3'), [record('a', doi='10.1234/a', volume='3'), record('b', doi='10.1234/a', volume='4')])
        self.assertEqual(result['identity_state'], 'unique')
        self.assertEqual(result['field_state'], 'conflict')
        self.assertEqual(field(result, 'volume')['judgment'], 'unverified')
        self.assertEqual(set(field(result, 'volume')['evidence_ids']), {'a', 'b'})
        self.assertIsNone(field(result, 'volume')['suggested'])

    def test_missing_source_field_not_original_error(self):
        result = assess(entry(volume='7'), [record(doi='10.1234/a')])
        self.assertEqual(field(result, 'volume')['judgment'], 'unverified')
        self.assertIsNone(field(result, 'volume')['suggested'])
        self.assertIn('volume = {7}', result['suggested_bibtex'])

    def test_missing_author_completed_from_doi_and_title(self):
        source = entry(doi='10.1234/a')
        source['fields'].pop('author')
        result = assess(source, [record(doi='10.1234/a')])
        self.assertEqual(result['identity_state'], 'unique')
        self.assertEqual(field(result, 'author')['judgment'], 'completion')

    def test_missing_title_completed_from_doi_authors_year(self):
        source = entry(doi='10.1234/a')
        source['fields'].pop('title')
        result = assess(source, [record(doi='10.1234/a')])
        self.assertEqual(result['identity_state'], 'unique')
        self.assertEqual(field(result, 'title')['judgment'], 'completion')

    def test_title_only_source_and_snippet_cannot_verify(self):
        title_only = record()
        title_only['fields'] = {'title': 'An exact research title'}
        result = assess(entry(), [title_only])
        self.assertEqual(result['identity_state'], 'insufficient')
        snippet = record(doi='10.1234/a')
        snippet['provenance'] = 'search_snippet'
        self.assertEqual(assess(entry(doi='10.1234/a'), [snippet])['identity_state'], 'insufficient')

    def test_online_vs_volume_year_not_silently_corrected(self):
        result = assess(entry(doi='10.1234/a', year='2023'), [record(doi='10.1234/a', year='2024')])
        self.assertEqual(field(result, 'year')['judgment'], 'unverified')
        self.assertIsNone(field(result, 'year')['suggested'])

    def test_article_number_in_pages_is_not_wrong(self):
        result = assess(entry(pages='e1234', doi='10.1234/a'), [record(eid='e1234', doi='10.1234/a')])
        self.assertEqual(field(result, 'pages')['judgment'], 'normalization')
        self.assertEqual(field(result, 'eid')['judgment'], 'not_applicable')
        self.assertNotIn('eid =', result['suggested_bibtex'])

    def test_equivalent_page_dashes_and_venue_abbreviation(self):
        result = assess(entry(pages='123--130', journal='Int. J. Testing', doi='10.1234/a'), [record(pages='123–130', journal='International J. Testing', doi='10.1234/a')])
        self.assertEqual(field(result, 'pages')['judgment'], 'normalization')
        self.assertEqual(field(result, 'journal')['judgment'], 'normalization')

    def test_unknown_venue_acronym_not_declared_wrong(self):
        result = assess(entry(journal='JMLR', doi='10.1234/a'), [record(journal='Journal of Machine Learning Research', doi='10.1234/a')])
        self.assertEqual(field(result, 'journal')['judgment'], 'unverified')

    def test_network_failure_does_not_prove_absence(self):
        result = assess(entry(doi='10.1234/a'), [], [{'provider': 'crossref', 'kind': 'timeout'}])
        self.assertEqual(result['identity_state'], 'insufficient')
        self.assertEqual(result['doi_state'], 'failed')
        self.assertTrue(any('not evidence' in issue for issue in result['issues']))

    def test_no_results_no_failure_is_not_found(self):
        result = assess(entry(), [])
        self.assertEqual(result['identity_state'], 'not_found')
        self.assertEqual(result['doi_state'], 'not_found')

    def test_real_work_without_doi_remains_valid_identity(self):
        result = assess(entry(), [record()])
        self.assertEqual(result['identity_state'], 'unique')
        self.assertEqual(result['doi_state'], 'not_found')
        self.assertNotIn('doi =', result['suggested_bibtex'])

    def test_mock_and_manual_provenance_explicit(self):
        result = assess(entry(), [record(method='mock')])
        self.assertTrue(result['mock'])
        self.assertTrue(any('MOCK' in issue for issue in result['issues']))
        self.assertTrue(field(result, 'title')['reason'].startswith('[MOCK]'))
        result = assess(entry(), [record(method='manual')])
        self.assertTrue(field(result, 'title')['reason'].startswith('[MANUAL IMPORT]'))

    def test_all_factual_suggestions_have_real_evidence_ids(self):
        records = [record(doi='10.1234/a', volume='4', pages='4--9')]
        result = assess(entry(), records)
        known = {source['id'] for source in result['evidence']}
        for assessment in result['assessments']:
            if assessment['suggested'] is not None:
                self.assertTrue(assessment['evidence_ids'])
                self.assertLessEqual(set(assessment['evidence_ids']), known)

    def test_inputs_are_not_mutated(self):
        source, records = entry(custom=r'Keep {GPU} \LaTeX'), [record(doi='10.1234/a')]
        before = copy.deepcopy((source, records))
        assess(source, records)
        self.assertEqual((source, records), before)

    def test_published_entry_can_retain_related_arxiv_id(self):
        result = assess(entry(doi='10.1234/a', eprint='2401.12345', journal='Real Journal'),
                        [record(doi='10.1234/a', eprint='2401.12345', journal='Real Journal')])
        self.assertEqual(result['identity_state'], 'unique')
        self.assertEqual(result['publication_state'], 'published')
        self.assertEqual(result['doi_state'], 'verified')

    def test_preprint_and_published_fields_are_never_mixed(self):
        source = entry(eprint='2401.12345', archiveprefix='arXiv', journal='arXiv', doi='10.48550/arXiv.2401.12345')
        preprint = record('pre', version='preprint', eprint='2401.12345', doi='10.48550/arXiv.2401.12345')
        published = record('pub', doi='10.1234/published', journal='Real Journal', volume='10', pages='1--20')
        result = assess(source, [preprint, published])
        self.assertEqual(result['identity_state'], 'unique')
        self.assertEqual(result['publication_state'], 'candidate')
        self.assertNotIn('publication_suggestion', result)
        self.assertNotIn('Real Journal', result['suggested_bibtex'])
        self.assertNotIn('volume = {10}', result['suggested_bibtex'])

    def test_explicit_published_relation_allows_title_change_and_coherent_replacement(self):
        source = entry(eprint='2401.12345v1', archiveprefix='arXiv', journal='arXiv', custom='keep me', volume='old-volume')
        preprint = record('pre', version='preprint', eprint='2401.12345v1')
        preprint['related_doi'] = '10.1234/final'
        preprint['relations'] = [{'type': 'is-preprint-of', 'target_doi': '10.1234/final', 'source': 'arxiv.doi'}]
        published = record('pub', title='The renamed final work', year='2025', doi='10.1234/final', journal='Final Journal', eid='e17')
        result = assess(source, [preprint, published])
        suggestion = result['publication_suggestion']
        self.assertEqual(suggestion['original_arxiv_id'], '2401.12345v1')
        self.assertEqual(suggestion['relation_evidence_ids'], ['pre'])
        output = parse_bibtex(suggestion['suggested_bibtex'])['entries'][0]
        self.assertEqual(output['citation_key'], 'KeepKey')
        self.assertEqual(output['fields']['title'], 'The renamed final work')
        self.assertEqual(output['fields']['year'], '2025')
        self.assertEqual(output['fields']['custom'], 'keep me')
        self.assertNotIn('volume', output['fields'])
        self.assertNotIn('eprint', output['fields'])
        self.assertEqual(suggestion['evidence_ids'], ['pub'])

    def test_publisher_explicit_arxiv_relation_is_sufficient(self):
        published = record('pub', title='Changed title', doi='10.1234/pub', journal='J')
        published['related_arxiv'] = '2401.12345'
        published['relations'] = [{'type': 'has-preprint', 'target_arxiv': '2401.12345', 'source': 'crossref.relation'}]
        result = assess(entry(eprint='2401.12345v2'), [published])
        self.assertIn('publication_suggestion', result)
        self.assertEqual(result['publication_suggestion']['relation_evidence_ids'], ['pub'])

    def test_linked_semantic_scholar_publication_can_use_separate_registry_evidence(self):
        published = record('s2', title='The renamed final work', doi='10.1234/pub', journal='Final Journal', volume='3')
        published['provider'] = 'semantic_scholar'
        published['related_arxiv'] = '2401.12345'
        published['relations'] = [{'type': 'same-work', 'target_arxiv': '2401.12345', 'source': 'semantic_scholar.externalIds'}]
        registry = record('registry', title='The renamed final work', doi='10.1234/pub', journal='Final Journal')
        source = entry(eprint='2401.12345')
        self.assertNotIn('publication_suggestion', assess(source, [published]))
        result = assess(source, [published, registry])
        self.assertEqual(set(result['publication_suggestion']['evidence_ids']), {'s2', 'registry'})
        self.assertEqual(result['publication_suggestion']['relation_evidence_ids'], ['s2'])

    def test_multiple_published_versions_not_automatically_selected(self):
        a, b = record('a', doi='10.1234/a', journal='J'), record('b', doi='10.1234/b', journal='J')
        a['related_arxiv'] = b['related_arxiv'] = '2401.12345'
        a['relations'] = b['relations'] = [{'type': 'same-work', 'target_arxiv': '2401.12345', 'source': 'semantic_scholar.externalIds'}]
        result = assess(entry(eprint='2401.12345'), [a, b])
        self.assertEqual(result['publication_state'], 'candidate')
        self.assertNotIn('publication_suggestion', result)

    def test_incomplete_published_metadata_not_assembled_from_other_versions(self):
        published = record('pub', doi='10.1234/pub')
        published['related_arxiv'] = '2401.12345'
        published['relations'] = [{'type': 'has-preprint', 'target_arxiv': '2401.12345', 'source': 'crossref.relation'}]
        result = assess(entry(eprint='2401.12345', journal='arXiv'), [published])
        self.assertNotIn('publication_suggestion', result)

    def test_arxiv_v1_not_rewritten_from_v2(self):
        result = assess(entry(eprint='2401.12345v1'), [record('pre', version='preprint', eprint='2401.12345v2', volume='wrong')])
        self.assertEqual(result['identity_state'], 'insufficient')
        self.assertNotIn('volume = {wrong}', result['suggested_bibtex'])

    def test_invalid_or_duplicate_evidence_rejected(self):
        result = assess(entry(), [{}, record(), record()])
        self.assertEqual(len(result['evidence']), 1)
        self.assertTrue(any('invalid evidence' in issue for issue in result['issues']))
        self.assertTrue(any('Duplicate evidence' in issue for issue in result['issues']))

    def test_absent_and_explicitly_empty_values_remain_distinct(self):
        source = entry(doi='10.1234/a', volume='')
        result = assess(source, [record(doi='10.1234/a', volume='2', number='3')])
        self.assertEqual(field(result, 'volume')['original'], '')
        self.assertIsNone(field(result, 'number')['original'])

    def test_untyped_arxiv_hint_cannot_authorize_publication_replacement(self):
        published = record('pub', title='Changed title', doi='10.1234/pub', journal='J')
        published['related_arxiv'] = '2401.12345'
        result = assess(entry(eprint='2401.12345'), [published])
        self.assertNotIn('publication_suggestion', result)
        self.assertEqual(result['publication_state'], 'candidate')

    def test_reference_and_generic_version_relation_are_only_leads(self):
        for relation_type in ('references', 'is-version-of', 'has-version', 'is-supplement-to'):
            published = record('pub', doi='10.1234/pub', journal='J')
            published['related_arxiv'] = '2401.12345'
            published['relations'] = [{'type': relation_type, 'target_arxiv': '2401.12345', 'source': 'crossref.relation'}]
            result = assess(entry(eprint='2401.12345'), [published])
            self.assertNotIn('publication_suggestion', result)

    def test_relation_target_must_match_original_preprint(self):
        published = record('pub', doi='10.1234/pub', journal='J')
        published['related_arxiv'] = '2401.12345'
        published['relations'] = [{'type': 'has-preprint', 'target_arxiv': '2401.54321', 'source': 'crossref.relation'}]
        self.assertNotIn('publication_suggestion', assess(entry(eprint='2401.12345'), [published]))

    def test_lead_scope_and_model_extraction_never_become_authority(self):
        for scope in ('search_lead_only', 'page_content_not_structured_metadata'):
            source = record(doi='10.1234/a')
            source['raw']['evidence_scope'] = scope
            self.assertEqual(assess(entry(doi='10.1234/a'), [source])['identity_state'], 'insufficient')
        source = record(method='model_extraction', doi='10.1234/a')
        self.assertEqual(assess(entry(doi='10.1234/a'), [source])['identity_state'], 'insufficient')

    def test_semantic_scholar_doi_requires_matching_registration_evidence(self):
        scholarly = record('s2', doi='10.1234/a')
        scholarly['provider'] = 'semantic_scholar'
        result = assess(entry(doi='10.1234/a'), [scholarly])
        self.assertEqual(result['doi_state'], 'unverified')
        self.assertEqual(field(result, 'doi')['judgment'], 'unverified')
        missing = assess(entry(), [scholarly])
        self.assertEqual(missing['doi_state'], 'missing')
        self.assertNotIn('doi =', missing['suggested_bibtex'])
        registered = assess(entry(doi='10.1234/a'), [scholarly, record('registry', doi='10.1234/a')])
        self.assertEqual(registered['doi_state'], 'verified')

    def test_manual_doi_cannot_claim_online_verification(self):
        result = assess(entry(doi='10.1234/a'), [record(method='manual', doi='10.1234/a')])
        self.assertEqual(result['doi_state'], 'unverified')
        self.assertEqual(field(result, 'doi')['judgment'], 'unverified')

    def test_self_reported_doi_verification_without_evidence_is_ignored(self):
        scholarly = record('s2', doi='10.1234/a')
        scholarly['provider'] = 'semantic_scholar'
        scholarly['doi_verification'] = {'status': 'registered', 'evidence_ids': ['does-not-exist']}
        self.assertEqual(assess(entry(doi='10.1234/a'), [scholarly])['doi_state'], 'unverified')

    def test_doi_prefix_normalization(self):
        self.assertEqual(normalize_doi(' DOI: 10.1234/ABC '), '10.1234/abc')
        self.assertEqual(normalize_doi('https://dx.doi.org/10.1234/ABC'), '10.1234/abc')

    def test_significant_title_symbols_not_ignored(self):
        result = assess(entry(title='A C++ toolkit'), [record(title='A C toolkit')])
        self.assertNotEqual(result['identity_state'], 'unique')

    def test_chinese_identity_and_fields(self):
        result = assess(entry(title='中文研究', author='张三 and 李四'), [record(title='中文研究', author='张三 and 李四', doi='10.1234/cn')])
        self.assertEqual(result['identity_state'], 'unique')
        self.assertEqual(field(result, 'doi')['suggested'], '10.1234/cn')


if __name__ == '__main__':
    unittest.main()
