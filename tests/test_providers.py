import copy
import json
import socket
import unittest
from unittest.mock import patch

import httpx

from app.providers import ProviderError, ProviderService, normalize_arxiv, normalize_doi


class Cache:
    def __init__(self):
        self.values = {}
        self.ttls = {}
    def get(self, key):
        return copy.deepcopy(self.values.get(key))
    def put(self, key, value, ttl):
        self.values[key], self.ttls[key] = copy.deepcopy(value), ttl


def record(title="Example Paper", doi="10.1234/example"):
    return {"DOI": doi, "title": [title], "author": [{"family": "Smith", "given": "Alice"}],
            "published": {"date-parts": [[2024]]}, "container-title": ["Example Journal"],
            "type": "journal-article", "volume": "8", "issue": "2", "article-number": "e100", "URL": "https://doi.org/" + doi}


class ProviderTests(unittest.TestCase):
    def setUp(self):
        from app.providers import _LAST_ATTEMPT, _NOT_BEFORE
        _LAST_ATTEMPT.clear()
        _NOT_BEFORE.clear()

    def service(self, handler, settings=None, cache=None):
        merged = {"mode": "live", "budget": {"per_entry": 24}, "deepseek": {"api_key": "test-secret"}}
        if settings:
            merged.update(settings)
        return ProviderService(merged, cache, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None)

    def test_identifiers(self):
        self.assertEqual(normalize_doi("https://doi.org/10.1234/AbC"), "10.1234/abc")
        self.assertEqual(normalize_doi("CNKI:123"), "")
        self.assertEqual(normalize_arxiv("https://arxiv.org/abs/1706.03762v2"), "1706.03762v2")
        self.assertEqual(normalize_arxiv("math.GT/0309136v1"), "math.GT/0309136v1")
        self.assertEqual(normalize_arxiv("10.48550/arXiv.1706.03762"), "1706.03762")

    def test_crossref_contract_and_article_number(self):
        requests = []
        def handler(req):
            requests.append(req)
            return httpx.Response(200, json={"message": record()})
        service = self.service(handler)
        result = service.collect({"fields": {"title": "Example Paper", "author": "Smith, Alice", "doi": "10.1234/example"}}, sources=["crossref"])
        self.assertEqual(len(result["records"]), 1)
        evidence = result["records"][0]
        self.assertEqual(evidence["fields"]["eid"], "e100")
        self.assertNotIn("pages", evidence["fields"])
        self.assertEqual(evidence["fields"]["author"], "Smith, Alice")
        self.assertEqual(evidence["version"], "published")
        self.assertIn("10.1234%2Fexample", str(requests[0].url))

    def test_crossref_wrong_doi_runs_title_discovery(self):
        def handler(req):
            raw = {"message": {"items": [record()]}} if req.url.params else {"message": record("Unrelated Paper", "10.1234/wrong")}
            return httpx.Response(200, json=raw)
        service = self.service(handler)
        result = service.collect({"fields": {"title": "Example Paper", "author": "Smith, Alice", "doi": "10.1234/wrong"}}, sources=["crossref"])
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual([row["operation"] for row in result["calls"]], ["doi_lookup", "title_search"])

    def test_same_title_wrong_authors_does_not_suppress_discovery(self):
        requests = []
        def handler(req):
            requests.append(req)
            raw = record()
            if not req.url.params:
                raw["author"] = [{"family": "Other", "given": "Carol"}]
            return httpx.Response(200, json={"message": {"items": [raw]} if req.url.params else raw})
        result = self.service(handler).collect({"fields": {"title": "Example Paper", "author": "Smith, Alice", "doi": "10.1234/example"}}, sources=["crossref"])
        self.assertEqual(len(requests), 2)
        self.assertEqual(result["calls"][1]["operation"], "title_search")

    def test_discovered_scholar_doi_gets_registry_lookup(self):
        requests = []
        def handler(req):
            requests.append(req)
            if req.url.host == "api.crossref.org":
                return httpx.Response(200, json={"message": {"items": []} if req.url.params else record()})
            return httpx.Response(200, json={"data": [{"paperId": "p1", "title": "Example Paper", "authors": [{"name": "Alice Smith"}], "year": 2024, "externalIds": {"DOI": "10.1234/example"}}]})
        result = self.service(handler).collect({"fields": {"title": "Example Paper", "author": "Smith, Alice"}}, sources=["crossref", "semantic_scholar"])
        self.assertEqual(len(requests), 3)
        scholar = next(row for row in result["records"] if row["provider"] == "semantic_scholar")
        registered = next(row for row in result["records"] if row["provider"] == "crossref")
        self.assertEqual(scholar["doi_verification"], {"status": "registered", "evidence_ids": [registered["id"]]})

    def test_source_filtered_scholar_doi_remains_unverified(self):
        payload = {"data": [{"paperId": "p1", "title": "Example Paper", "authors": [{"name": "Alice Smith"}], "year": 2024, "externalIds": {"DOI": "10.1234/example"}}]}
        result = self.service(lambda req: httpx.Response(200, json=payload)).collect({"fields": {"title": "Example Paper", "author": "Smith, Alice"}}, sources=["semantic_scholar"])
        self.assertEqual(result["records"][0]["doi_verification"]["status"], "candidate_unverified")
        self.assertEqual(len(result["calls"]), 1)

    def test_doi_csl_content_negotiation(self):
        requests = []
        def handler(req):
            requests.append(req)
            return httpx.Response(200, json={**record(), "title": "Example Paper", "type": "article-journal"})
        result = self.service(handler).collect({"fields": {"doi": "10.1234/example"}}, sources=["doi"])
        self.assertEqual(requests[0].headers["accept"], "application/vnd.citationstyles.csl+json")
        self.assertEqual(result["records"][0]["provider"], "doi")

    def test_redirect_hops_count_against_external_call_budget(self):
        requests = []
        def handler(req):
            requests.append(req)
            return httpx.Response(302, headers={"Location": "https://publisher.org/metadata"}) if req.url.host == "doi.org" else httpx.Response(200, json=record())
        result = self.service(handler, {"budget": {"per_entry": 1}}).collect({"fields": {"doi": "10.1234/example"}}, sources=["doi"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(result["errors"][0]["kind"], "budget_exhausted")
        self.assertEqual(sum(call["external_call"] for call in result["calls"]), 1)
        self.assertEqual(result["calls"][0]["status"], "redirect")

    def test_redirect_success_records_both_external_requests(self):
        def handler(req):
            return httpx.Response(302, headers={"Location": "https://publisher.org/metadata"}) if req.url.host == "doi.org" else httpx.Response(200, json=record())
        result = self.service(handler).collect({"fields": {"doi": "10.1234/example"}}, sources=["doi"])
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(sum(call["external_call"] for call in result["calls"]), 2)

    def test_datacite_contract(self):
        raw = {"data": {"id": "10.9999/data", "attributes": {"doi": "10.9999/data", "titles": [{"title": "Dataset"}], "creators": [{"name": "Researcher, A"}], "publicationYear": 2020, "types": {"resourceTypeGeneral": "Dataset"}}}}
        result = self.service(lambda req: httpx.Response(200, json=raw)).collect({"fields": {"doi": "10.9999/data"}}, sources=["datacite"])
        self.assertEqual(result["records"][0]["fields"]["title"], "Dataset")
        self.assertEqual(result["records"][0]["version"], "unknown")

    def test_arxiv_preserves_version_and_explicit_publication_link(self):
        xml = '''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry><id>http://arxiv.org/abs/2401.00001v2</id><title>Preprint title</title><published>2024-01-01T00:00:00Z</published><updated>2024-02-01T00:00:00Z</updated><author><name>Alice Smith</name></author><arxiv:doi>https://doi.org/10.1234/published</arxiv:doi><arxiv:journal_ref>Journal 8 (2025)</arxiv:journal_ref></entry></feed>'''
        service = self.service(lambda req: httpx.Response(200, text=xml))
        result = service.collect({"fields": {"title": "Preprint title", "eprint": "2401.00001v2"}}, sources=["arxiv"])
        evidence = result["records"][0]
        self.assertEqual(evidence["fields"]["eprint"], "2401.00001v2")
        self.assertEqual(evidence["related_doi"], "10.1234/published")
        self.assertNotIn("doi", evidence["fields"])
        self.assertEqual(evidence["version"], "preprint")

    def test_arxiv_error_feed_not_no_results_and_not_cached(self):
        xml = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Error</title><id>http://arxiv.org/api/errors</id></entry></feed>'
        cache = Cache()
        result = self.service(lambda req: httpx.Response(200, text=xml), cache=cache).collect({"fields": {"eprint": "2401.00001"}}, sources=["arxiv"])
        self.assertEqual(result["errors"][0]["kind"], "query_error")
        self.assertEqual(cache.values, {})

    def test_semantic_scholar_contract_and_header(self):
        requests = []
        def handler(req):
            requests.append(req)
            return httpx.Response(200, json={"paperId": "p1", "title": "Example Paper", "authors": [{"name": "Alice Smith"}], "year": 2024, "externalIds": {"DOI": "10.1234/example", "ArXiv": "2401.00001"}, "journal": {"name": "Journal", "volume": "8", "pages": "1-5"}, "url": "https://www.semanticscholar.org/paper/p1"})
        service = self.service(handler, {"semantic_scholar": {"api_key": "scholar-secret", "rate_per_second": 1}})
        result = service.collect({"fields": {"title": "Example Paper", "author": "Smith, Alice", "doi": "10.1234/example"}}, sources=["semantic_scholar"])
        self.assertEqual(requests[0].headers["x-api-key"], "scholar-secret")
        self.assertIn("DOI%3A10.1234%2Fexample", str(requests[0].url))
        self.assertNotIn("scholar-secret", json.dumps(result))
        self.assertEqual(result["records"][0]["related_arxiv"], "2401.00001")

    def test_persistent_cache_reused_and_force_bypasses(self):
        requests, cache = [], Cache()
        def handler(req):
            requests.append(req)
            return httpx.Response(200, json={"message": record()})
        entry = {"fields": {"doi": "10.1234/example", "title": "Example Paper", "author": "Smith, Alice"}}
        first = self.service(handler, cache=cache).collect(entry, sources=["crossref"])
        second = self.service(handler, cache=cache).collect(entry, sources=["crossref"], budget=lambda: False)
        self.assertEqual(len(requests), 1)
        self.assertEqual(second["calls"][0]["status"], "cache_hit")
        self.assertEqual(first["calls"][0]["expires_at"], second["calls"][0]["expires_at"])
        self.assertEqual(first["calls"][0]["retrieved_at"], second["calls"][0]["retrieved_at"])
        self.assertEqual(first["records"][0]["id"], second["records"][0]["id"])
        self.service(handler, cache=cache).collect(entry, sources=["crossref"], force=True)
        self.assertEqual(len(requests), 2)

    def test_echoed_secret_is_sanitized_before_cache_and_after_rotation(self):
        cache = Cache()
        raw = record()
        raw["debug"] = "old-secret"
        first = self.service(lambda req: httpx.Response(200, json={"message": raw}), {"deepseek": {"api_key": "old-secret"}}, cache)
        entry = {"fields": {"title": "Example Paper", "author": "Smith, Alice", "doi": "10.1234/example"}}
        original = first.collect(entry, sources=["crossref"])
        self.assertNotIn("old-secret", json.dumps(cache.values))
        self.assertNotIn("old-secret", json.dumps(original))
        rotated = self.service(lambda req: self.fail("must use sanitized cache"), {"deepseek": {"api_key": "new-secret"}}, cache)
        result = rotated.collect(entry, sources=["crossref"])
        self.assertEqual(result["calls"][0]["status"], "cache_hit")
        self.assertNotIn("old-secret", json.dumps(result))
        self.assertNotIn("new-secret", json.dumps(result))

    def test_empty_search_negative_ttl(self):
        cache = Cache()
        result = self.service(lambda req: httpx.Response(200, json={"message": {"items": []}}), cache=cache).collect({"fields": {"title": "Nothing"}}, sources=["crossref"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["calls"][0]["status"], "no_results")
        self.assertEqual(next(iter(cache.ttls.values())), 7 * 86400)

    def test_retry_after_bounded_and_every_attempt_budgeted(self):
        requests, budget_calls, sleeps = [], [], []
        def handler(req):
            requests.append(req)
            return httpx.Response(429, headers={"Retry-After": "1"}) if len(requests) < 3 else httpx.Response(200, json={"message": record()})
        service = self.service(handler)
        service.sleep = sleeps.append
        result = service.collect({"fields": {"doi": "10.1234/example", "title": "Example Paper", "author": "Smith, Alice"}}, sources=["crossref"], budget=lambda: budget_calls.append(1))
        self.assertEqual(len(requests), 3)
        self.assertEqual(len(budget_calls), 3)
        self.assertEqual([call["status"] for call in result["calls"]], ["error", "error", "ok"])
        self.assertLessEqual(max(sleeps), 10)

    def test_long_retry_after_defers_without_retrying_early(self):
        requests = []
        def handler(req):
            requests.append(req)
            return httpx.Response(429, headers={"Retry-After": "60"})
        result = self.service(handler).collect({"fields": {"title": "Paper"}}, sources=["crossref"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(result["errors"][0]["kind"], "rate_limited")
        self.assertEqual(result["errors"][0]["retry_after_seconds"], 60)
        # A new instance/entry must respect the same provider cooldown.
        second = self.service(handler).collect({"fields": {"title": "Other Paper"}}, sources=["crossref"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(second["calls"][0]["status"], "deferred")
        self.assertFalse(second["calls"][0]["external_call"])

    def test_crossref_non_version_relations_are_never_publication_links(self):
        for relation in ("references", "cites", "is-supplement-to", "is-version-of"):
            raw = record()
            raw["relation"] = {relation: [{"id": "https://arxiv.org/abs/2401.00001", "id-type": "uri"}]}
            with self.subTest(relation=relation):
                result = self.service(lambda req: httpx.Response(200, json={"message": raw})).collect(
                    {"fields": {"title": "Example Paper", "author": "Smith, Alice", "doi": "10.1234/example"}}, sources=["crossref"])
                evidence = result["records"][0]
                self.assertNotIn("related_arxiv", evidence)
                self.assertNotIn("relations", evidence)

    def test_crossref_preprint_relation_preserves_type_direction_and_source(self):
        raw = record()
        raw["relation"] = {"has-preprint": [{"id": "10.48550/arXiv.2401.00001", "id-type": "doi"}]}
        result = self.service(lambda req: httpx.Response(200, json={"message": raw})).collect(
            {"fields": {"title": "Example Paper", "author": "Smith, Alice", "doi": "10.1234/example"}}, sources=["crossref"])
        evidence = result["records"][0]
        self.assertEqual(evidence["related_arxiv"], "2401.00001")
        self.assertEqual(evidence["relations"], [{"type": "has-preprint", "target_arxiv": "2401.00001", "source": "crossref.relation"}])

    def test_datacite_references_and_supplements_are_not_version_links(self):
        for relation in ("References", "Cites", "IsSupplementTo"):
            raw = {"data": {"attributes": {"doi": "10.1234/example", "titles": [{"title": "Paper"}],
                    "relatedIdentifiers": [{"relationType": relation, "relatedIdentifier": "https://arxiv.org/abs/2401.00001"}]}}}
            with self.subTest(relation=relation):
                result = self.service(lambda req: httpx.Response(200, json=raw)).collect(
                    {"fields": {"doi": "10.1234/example"}}, sources=["datacite"])
                self.assertNotIn("related_arxiv", result["records"][0])
                self.assertNotIn("relations", result["records"][0])

    def test_authentication_and_endpoint_errors_never_retry(self):
        for status, kind in ((401, "authentication"), (402, "insufficient_balance"), (403, "permission_denied"), (404, "endpoint_not_found")):
            requests = []
            def handler(req):
                requests.append(req)
                return httpx.Response(status, json={"error": "token test-secret leaked"})
            with self.subTest(status=status):
                result = self.service(handler).collect({"fields": {"title": "Paper"}}, sources=["deepseek"])
                self.assertEqual(result["errors"][0]["kind"], kind)
                self.assertEqual(len(requests), 1)
                self.assertNotIn("test-secret", json.dumps(result))

    def test_budget_exhaustion_preserves_partial_records(self):
        service = self.service(lambda req: httpx.Response(200, json={"message": record()}), {"budget": {"per_entry": 1}})
        result = service.collect({"fields": {"doi": "10.1234/example", "title": "Example Paper", "author": "Smith, Alice"}}, sources=["crossref", "doi"])
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["errors"][0]["kind"], "budget_exhausted")

    def test_budget_exception_does_not_leak_message(self):
        def denied():
            raise RuntimeError("internal secret test-secret")
        result = self.service(lambda req: self.fail("must not call HTTP")).collect({"fields": {"title": "Paper"}}, sources=["crossref"], budget=denied)
        self.assertEqual(result["errors"][0]["kind"], "budget_exhausted")
        self.assertNotIn("test-secret", json.dumps(result))

    def test_network_error_not_negative_cached(self):
        cache = Cache()
        def handler(req):
            raise httpx.ConnectError("test-secret", request=req)
        result = self.service(handler, cache=cache).collect({"fields": {"title": "Paper"}}, sources=["crossref"])
        self.assertEqual(result["errors"][0]["kind"], "network_error")
        self.assertEqual(len(result["calls"]), 3)
        self.assertEqual(cache.values, {})
        self.assertNotIn("test-secret", json.dumps(result))

    def test_malformed_schema_not_cached(self):
        cache = Cache()
        result = self.service(lambda req: httpx.Response(200, json={"unexpected": []}), cache=cache).collect({"fields": {"title": "Paper"}}, sources=["crossref"])
        self.assertEqual(result["errors"][0]["kind"], "invalid_response")
        self.assertEqual(cache.values, {})

    def test_cnki_only_does_not_run_other_branches(self):
        requests = []
        def handler(req):
            requests.append(req)
            return httpx.Response(200, json={"results": []})
        result = self.service(handler).collect({"fields": {"title": "中文论文", "doi": "10.1234/example"}}, cnki_mode="all", sources=["cnki"])
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(req.url.host == "api.deepseek.com" for req in requests))
        self.assertEqual(json.loads(requests[0].content)["query"], "中文论文 site:cnki.net")
        self.assertEqual(json.loads(requests[1].content)["query"], "10.1234/example site:cnki.net")
        self.assertEqual(result["records"], [])
        self.assertEqual(result["errors"], [])

    def test_empty_cnki_search_uses_bounded_author_then_doi_fallback(self):
        requests = []
        def handler(req):
            requests.append(req)
            return httpx.Response(200, json={"results": []})
        result = self.service(handler).collect({"fields": {"title": "中文论文", "author": "张三 and 李四", "doi": "10.1234/example"}}, cnki_mode="all", sources=["cnki"])
        queries = [json.loads(req.content)["query"] for req in requests]
        self.assertEqual(queries, ["中文论文 site:cnki.net", "中文论文 张三 site:cnki.net", "10.1234/example site:cnki.net"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["records"], [])
        self.assertTrue(all(call["status"] == "no_results" for call in result["calls"]))

    def test_empty_deepseek_search_fallback_is_budgeted(self):
        requests = []
        def handler(req):
            requests.append(req)
            return httpx.Response(200, json={"results": []})
        result = self.service(handler, {"budget": {"per_entry": 1}}).collect({"fields": {"title": "Paper", "author": "Alice Smith"}}, sources=["deepseek"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(result["errors"][0]["kind"], "budget_exhausted")

    def test_missing_deepseek_key_makes_no_queries(self):
        service = self.service(lambda req: self.fail("unconfigured key must not call HTTP"), {"deepseek": {}})
        result = service.collect({"fields": {"title": "Paper", "author": "Alice Smith", "doi": "10.1234/example"}}, cnki_mode="all", sources=["deepseek", "cnki"])
        self.assertTrue(all(call["status"] == "skipped" for call in result["calls"]))
        self.assertTrue(all(not call["external_call"] for call in result["calls"]))

    def test_cnki_off_never_searches(self):
        result = self.service(lambda req: self.fail("must not call HTTP")).collect({"fields": {"title": "中文论文"}}, cnki_mode="off", sources=["cnki"])
        self.assertEqual(result["calls"], [])

    def test_deepseek_search_fetch_retains_only_lead_fields(self):
        requests = []
        def handler(req):
            requests.append(req)
            if req.url.path.endswith("search"):
                return httpx.Response(200, json={"results": [{"title": "Paper", "url": "https://example.org/paper", "snippet": "a lead"}]})
            return httpx.Response(200, json={"title": "Paper", "content": "Alice Smith. Paper. 2024."})
        with patch("app.security.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]):
            result = self.service(handler).collect({"fields": {"title": "Paper"}}, sources=["deepseek"])
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(set(result["records"][1]["fields"]), {"title"})
        self.assertIn("content", result["records"][1]["raw"])
        self.assertNotIn("test-secret", json.dumps(result))

    def test_fetch_failure_keeps_candidate_and_reports_failure(self):
        def handler(req):
            return httpx.Response(200, json={"results": [{"title": "Paper", "url": "https://example.org/paper"}]}) if req.url.path.endswith("search") else httpx.Response(403)
        with patch("app.security.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]):
            result = self.service(handler).collect({"fields": {"title": "Paper"}}, sources=["deepseek"])
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["errors"][0]["kind"], "permission_denied")
        self.assertEqual(result["records"][0]["raw"]["fetch_status"], "permission_denied")

    def test_grounded_model_rejects_invented_ids_and_values(self):
        records = [{"id": "e1", "fields": {"year": "2024"}, "raw": {"content": "Published in 2024."}}]
        valid = {"suggestions": [{"field": "year", "value": "2024", "evidence_ids": ["e1"], "reason": "Source year"}]}
        self.assertFalse(ProviderService._validate_suggestions(valid, records)[0]["verified"])
        for change in ({"evidence_ids": ["invented"]}, {"value": "2025"}, {"field": "script"}):
            invalid = copy.deepcopy(valid)
            invalid["suggestions"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                ProviderService._validate_suggestions(invalid, records)

    def test_grounded_model_actual_contract_and_token_trace(self):
        seen = []
        def handler(req):
            seen.append(req)
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"suggestions": []}'}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
        service = self.service(handler, {"llm": {"api_key": "model-secret", "base_url": "https://example.org/v1", "model": "test-model"}})
        result = service.test_connection("llm")
        self.assertTrue(result["ok"])
        self.assertEqual(seen[0].url.path, "/v1/chat/completions")
        self.assertEqual(json.loads(seen[0].content)["response_format"], {"type": "json_object"})
        self.assertEqual(result["calls"][0]["tokens"]["prompt_tokens"], 10)
        self.assertNotIn("model-secret", json.dumps(result))

    def test_invalid_model_output_is_not_cached(self):
        cache = Cache()
        service = self.service(lambda req: httpx.Response(200, json={"choices": [{"message": {"content": '{"suggestions": [{"field":"doi","value":"10.1234/fake","evidence_ids":["fake"]}]}'}}]}),
                               {"llm": {"api_key": "model-secret", "base_url": "https://example.org/v1", "model": "test-model"}}, cache)
        result = service.test_connection("llm")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "invalid_model_output")
        self.assertEqual(cache.values, {})

    def test_mock_is_unmistakable_and_offline(self):
        service = self.service(lambda req: self.fail("mock must not call HTTP"), {"mode": "mock"})
        for key in ("correct", "wrong_doi", "similar", "preprint_changed", "article_number", "failure", "no_doi"):
            with self.subTest(key=key):
                result = service.collect({"key": key, "fields": {"title": "Synthetic Paper"}})
                self.assertTrue(result["mock"])
                self.assertTrue(all(record["method"] == "mock" for record in result["records"]))
                self.assertTrue(all(not call["external_call"] for call in result["calls"]))

    def test_connection_mock_does_not_claim_real_connectivity(self):
        result = self.service(lambda req: self.fail("mock must not call HTTP"), {"mode": "mock"}).test_connection("deepseek")
        self.assertEqual(result["status"], "mock")
        self.assertIn("no API connection was tested", result["message"])


if __name__ == "__main__":
    unittest.main()
