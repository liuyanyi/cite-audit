"""Deterministic source/transport tests. No DNS, remote API or paid model calls."""
import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from cite_audit import sources
from cite_audit.sources import (
    BERT_TITLE,
    MAX_BYTES,
    Response,
    SourceRegistry,
    UnsafeURL,
    normalize_arxiv,
    normalize_doi,
    parse_official_bib,
    validate_addresses,
    validate_url,
)

CROSSREF = {"DOI": "10.18653/v1/N19-1423", "type": "proceedings-article", "title": [BERT_TITLE],
            "author": [{"family": "Devlin", "given": "Jacob"}], "published": {"date-parts": [[2019, 6]]},
            "published-online": {"date-parts": [[2018, 12]]}, "page": "4171-4186",
            "container-title": ["NAACL 2019"], "URL": "https://doi.org/10.18653/v1/N19-1423"}
ACL_BIB = ('@inproceedings{devlin2019, title = "{BERT}: Pre-training of Deep Bidirectional Transformers '
           'for Language Understanding", author = "Devlin, Jacob", year = "2019", '
           'booktitle = "Proceedings of NAACL", url = "https://aclanthology.org/N19-1423/", '
           'doi = "10.18653/v1/N19-1423", pages = "4171--4186"}')
PMLR_BIB = ('@InProceedings{pmlr-v139-example21a, title = {A Synthetic Paper}, author = {Example, Alice}, '
            'year = {2021}, booktitle = {Proceedings of a Synthetic Conference}, pages = {1--10}, '
            'volume = {139}, url = {https://proceedings.mlr.press/v139/example21a.html}}')
ATOM = '''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"
 xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>
 <id>http://arxiv.org/abs/1810.04805v2</id><title>BERT: Pre-training of Deep Bidirectional Transformers
 for Language Understanding</title><author><name>Jacob Devlin</name></author>
 <published>2018-10-11T00:00:00Z</published><arxiv:doi>10.18653/v1/N19-1423</arxiv:doi>
 <arxiv:journal_ref>NAACL 2019</arxiv:journal_ref></entry></feed>'''


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(sources, "MIN_INTERVAL", dict.fromkeys(sources.PROVIDERS, 0))


async def public_dns(host):
    return ["93.184.216.34"]


class Network:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    async def __call__(self, url, addresses, timeout, max_bytes):
        self.calls.append((url, addresses, timeout, max_bytes))
        result = self.handler(url)
        if asyncio.iscoroutine(result):
            return await result
        if isinstance(result, Exception):
            raise result
        if isinstance(result, Response):
            return result
        status, body, headers = result if len(result) == 3 else (*result, {})
        if not isinstance(body, (str, bytes)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode()
        return Response(status, headers, body, url, "2026-10-30T00:00:00+00:00")


@pytest.mark.asyncio
async def test_iclr_rejects_page_whose_pdf_identifies_a_different_paper():
    base = "https://proceedings.iclr.cc/paper_files/paper/2025"
    url = f"{base}/hash/{'a' * 32}-Abstract-Conference.html"
    metadata = {"title": "Same title", "author": "Author, Alice",
                "journal_title": "International Conference on Learning Representations",
                "publication_date": "2025-05-01", "pdf_url": f"{base}/file/{'b' * 32}-Paper-Conference.pdf"}
    page = "".join(f'<meta name="citation_{key}" content="{value}">' for key, value in metadata.items())
    records, events, _ = await lookup(Network(lambda _: (200, page)), {"url": url}, ["iclr"])
    assert not records
    assert any(event["status"] == "error" and event["provider"] == "iclr" for event in events)


async def lookup(network, fields=None, enabled=None, settings=None, registry=None, mode="online", cancelled=None):
    registry = registry or SourceRegistry(transport=network, resolver=public_dns)
    events = []

    async def audit(event):
        events.append(event)

    options = {"source_enabled": {p: p in (enabled or ["crossref"]) for p in sources.PROVIDERS},
               "retries": 0, **(settings or {})}
    records = await registry.lookup(fields or {"title": BERT_TITLE}, mode, options,
                                    audit=audit, cancelled=cancelled or (lambda: False))
    return records, events, registry


async def test_force_refresh_bypasses_source_cache():
    net = Network(lambda url: (200, {"message": {"items": [CROSSREF]}}))
    _, _, registry = await lookup(net)
    count = len(net.calls)
    _, events, _ = await lookup(net, registry=registry, settings={"force_refresh": True})
    assert len(net.calls) > count
    assert not any(e["cache_hit"] for e in events)


@pytest.mark.parametrize('input_name,returned_issn,expected', [
    ('CMC-COMPUTERS MATERIALS \\& CONTINUA', '1546-2226', 'normalization'),
    ('Unrelated Journal', '1546-2226', 'conflict'),
    ('CMC-COMPUTERS MATERIALS \\& CONTINUA', '1234-5679', 'conflict'),
])
async def test_issn_recheck_end_to_end(tmp_path, monkeypatch, input_name, returned_issn, expected):
    from cite_audit.service import AuditService
    item = {**CROSSREF, 'type': 'journal-article', 'ISSN': ['1546-2226'],
            'container-title': ['Computers, Materials &amp; Continua']}
    net = Network(lambda url: (200, {'message': {
        'title': 'Computers Materials & Continua', 'ISSN': [returned_issn]
    }}) if '/journals/' in url else (200, {'message': item}))
    service = AuditService(tmp_path, registry=SourceRegistry(transport=net, resolver=public_dns))
    settings = service.settings.get()
    settings['source_enabled'] = {p: p == 'crossref' for p in sources.PROVIDERS}
    monkeypatch.setattr(service.settings, 'get', lambda: settings)
    raw = ('@article{x,title={' + BERT_TITLE + '},author={Devlin, Jacob},doi={10.18653/v1/N19-1423},'
           'year={2019},journal={' + input_name + '}}').encode()
    task = service.create_task(raw, 'journal.bib', '', 'online')
    await service.run_task(task['id'])
    detail = service.entries(task['id'], True)[0]
    assert detail['execution_status'] == 'completed'
    journal = next(a for a in detail['field_assessments'] if a['field'] == 'journal')
    assert journal['judgment'] == expected
    assert journal['reference_value'] == 'Computers, Materials & Continua'
    assert detail['sources'][0]['fields']['issn'] == '1546-2226'
    assert len(detail['journal_checks']) == 1
    snapshot = service.store.one('SELECT * FROM snapshots WHERE id=?', (detail['journal_checks'][0]['snapshot_id'],))
    assert snapshot is not None
    frozen = service.freeze(task['id'])
    assert service.freeze(task['id']) == frozen
    assert any('/journals/1546-2226' in call[0] for call in net.calls)


def test_issn_validation_and_no_generic_prefix_removal():
    from cite_audit.journals import issns, journal_matches
    assert issns(['15462226', '1546-2226', '1546-2227', 'not-an-id']) == ['1546-2226']
    assert not journal_matches('FAKE-Computers Materials & Continua', 'Computers Materials & Continua', ['1546-2226'])


@pytest.mark.parametrize("url", [
    "http://api.crossref.org/works", "https://127.0.0.1/", "https://localhost/", "file:///tmp/a",
    "https://api.crossref.org.evil.example/", "https://api.crossref.org:444/",
    "https://secret@api.crossref.org/", "https://api.crossref.org/#fragment", "https://api.crossref.org/\n",
    "https://[::1]/", "https://example.com/", "https://api.crossref.org:bad/",
])
def test_url_allowlist(url):
    with pytest.raises(UnsafeURL):
        validate_url(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "::1",
                                     "fc00::1", "fe80::1", "224.0.0.1", "0.0.0.0", "192.0.2.1",
                                     "100.64.0.1", "::ffff:127.0.0.1", "invalid"])
def test_nonpublic_dns_rejected(address):
    with pytest.raises(UnsafeURL):
        validate_addresses(["93.184.216.34", address])


def test_public_dns_and_safe_url():
    validate_addresses(["93.184.216.34", "2606:4700:4700::1111"])
    assert validate_url("https://api.crossref.org/works/10.1234/test") == "api.crossref.org"


def test_identifiers_and_minimal_bib_parser():
    assert normalize_doi("https://doi.org/10.18653/v1/N19-1423") == "10.18653/v1/n19-1423"
    assert normalize_arxiv("https://arxiv.org/abs/1810.04805v2") == "1810.04805v2"
    assert normalize_arxiv("http://localhost/1810.04805") == ""
    assert parse_official_bib(ACL_BIB)["title"].startswith("{BERT}")
    assert parse_official_bib(PMLR_BIB)["pages"] == "1--10"
    assert parse_official_bib('@article{x,title={Incomplete,author={X}}') == {}
    assert parse_official_bib('@article{x,title={X} # unknown,author={A}}') == {}


async def test_crossref_dates_are_not_conflated_and_snapshot_is_original():
    net = Network(lambda url: (200, {"message": {"items": [CROSSREF]}}))
    records, events, _ = await lookup(net)
    assert len(records) == 1
    record = records[0]
    assert record["fields"]["year"] == "2019"
    assert record["fields"]["first_online_year"] == "2018"
    assert record["identifiers"] == {"doi": "10.18653/v1/n19-1423"}
    assert record["source_type"] == "registry" and record["status"] == "verified"
    assert record["raw_payload"]["response"] == CROSSREF
    assert record["raw_payload"]["sha256"]
    assert all(e["cost"] is None for e in events)


async def test_wrong_doi_does_not_disable_title_author_search():
    calls = []

    def handler(url):
        calls.append(url)
        return (200, {"message": {"items": [CROSSREF]}}) if "?" in url else (404, {})

    net = Network(handler)
    records, _, _ = await lookup(net, {"doi": "10.1234/wrong", "title": BERT_TITLE, "author": "Devlin, Jacob"})
    assert len(records) == 1 and len(calls) == 2
    query = parse_qs(urlsplit(calls[1]).query)
    assert query["query.title"] == [BERT_TITLE]
    assert query["query.author"] == ["Devlin, Jacob"]


async def test_partial_provider_results_survive_following_request_failure():
    net = Network(lambda url: (503, {}) if "?" in url else (200, {"message": CROSSREF}))
    records, events, _ = await lookup(net, {"doi": "10.18653/v1/N19-1423", "title": "Intended different paper"})
    assert len(records) == 1
    assert any(e["status"] == "error" for e in events)


async def test_confirmed_doi_avoids_redundant_title_search():
    net = Network(lambda url: (200, {"message": CROSSREF}))
    records, events, _ = await lookup(net, {"doi": "10.18653/v1/N19-1423", "title": BERT_TITLE},
                                      ["crossref", "arxiv"], {"max_source_requests": 1})
    assert len(records) == len(net.calls) == 1
    assert not any(e["status"] == "budget_exhausted" for e in events)


async def test_verified_preprint_still_searches_for_formal_publication():
    preprint = {**CROSSREF, 'type': 'posted-content'}
    net = Network(lambda url: (200, {'message': {'items': []} if '?' in url else preprint}))
    records, _, registry = await lookup(net, {'doi': CROSSREF['DOI'], 'title': BERT_TITLE})
    assert len(net.calls) == 2
    assert not registry._formal_match({'doi': CROSSREF['DOI'], 'title': BERT_TITLE}, records)


async def test_patents_do_not_search_paper_indexes():
    net = Network(lambda url: pytest.fail('Patent must not query paper indexes'))
    records, events, _ = await lookup(net, {'entrytype': 'patent', 'title': 'A cell segmentation device'})
    assert not records and not net.calls
    assert any(e['provider'] == 'coverage' and e['status'] == 'unsupported' for e in events)


async def test_relevant_index_doi_is_verified_and_noise_is_not_expanded():
    def handle(url):
        if 'api.openalex.org' in url:
            return 200, {'results': [
                {'id': 'W1', 'title': BERT_TITLE, 'doi': 'https://doi.org/10.18653/v1/N19-1423'},
                {'id': 'W2', 'title': 'Unrelated microscopy article', 'doi': 'https://doi.org/10.1234/noise'}]}
        if '?' in url:
            return 200, {'message': {'items': []}}
        assert url.endswith('10.18653/v1/n19-1423')
        return 200, {'message': CROSSREF}
    net = Network(handle)
    records, _, registry = await lookup(net, {'title': BERT_TITLE}, ['crossref', 'openalex'])
    assert any(r['provider'] == 'crossref' and r['status'] == 'verified' for r in records)
    assert len(net.calls) == 3
    noise = {'fields': {'title': 'Unrelated microscopy article'}, 'identifiers': {'acl': 'N19-1423'},
             'raw_payload': {}}
    assert registry._official_hints('acl', {'title': BERT_TITLE}, [noise]) == []


async def test_arxiv_published_doi_is_relation_not_preprint_identity():
    net = Network(lambda url: (200, ATOM))
    records, _, _ = await lookup(net, {"eprint": "1810.04805"}, ["arxiv"])
    assert len(records) == 1
    record = records[0]
    assert record["identifiers"] == {"arxiv": "1810.04805v2"}
    assert "doi" not in record["fields"] and "journal" not in record["fields"]
    assert record["fields"]["year"] == "2018"
    assert record["relations"][0]["target_identifiers"] == {"doi": "10.18653/v1/n19-1423"}


async def test_arxiv_title_and_author_discovery():
    net = Network(lambda url: (200, ATOM))
    records, _, _ = await lookup(net, {"title": BERT_TITLE, "author": "Devlin, Jacob and Another, Author"}, ["arxiv"])
    query = parse_qs(urlsplit(net.calls[0][0]).query)["search_query"][0]
    assert 'ti:"' in query and 'au:"Devlin"' in query
    assert records


@pytest.mark.parametrize('author', ['Liu Yanyi', 'Yanyi Liu', 'Liu, Yanyi'])
async def test_arxiv_does_not_assume_romanized_surname_position(author):
    net = Network(lambda url: (200, ATOM))
    await lookup(net, {'title': BERT_TITLE, 'author': author}, ['arxiv'])
    query = parse_qs(urlsplit(net.calls[0][0]).query)['search_query'][0]
    assert 'au:"Liu"' in query and 'au:"Yanyi"' in query


async def test_arxiv_error_feed_and_xml_entities_are_not_negative_facts():
    for xml in [ATOM.replace("http://arxiv.org/abs/1810.04805v2", "http://arxiv.org/api/errors#incorrect_id_format"),
                '<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x>&secret;</x>']:
        net = Network(lambda url, xml=xml: (200, xml))
        records, events, registry = await lookup(net, {"eprint": "1810.04805"}, ["arxiv"])
        assert not records and any(e["status"] == "error" for e in events)
        assert not registry._cache


async def test_aggregator_cannot_prove_cross_version_relation():
    item = {"paperId": "synthetic-paper", "title": BERT_TITLE, "authors": [{"name": "Jacob Devlin"}],
            "year": 2019, "externalIds": {"DOI": "10.18653/v1/N19-1423", "ArXiv": "1810.04805"}}
    net = Network(lambda url: (200, {"data": [item]}))
    records, _, _ = await lookup(net, enabled=["semantic_scholar"])
    assert records[0]["status"] == "candidate"
    assert records[0]["identity_basis"] == "candidate"
    assert not records[0]["relations"]
    assert records[0]["source_type"] == "aggregator"


async def test_acl_title_discovery_gets_official_record_not_index_fields():
    def handler(url):
        if "api.crossref.org" in url:
            return 200, {"message": {"items": [CROSSREF]}}
        assert url == "https://aclanthology.org/N19-1423.bib"
        return 200, ACL_BIB

    net = Network(handler)
    records, _, _ = await lookup(net, enabled=["crossref", "acl"])
    official = next(r for r in records if r["provider"] == "acl")
    assert official["fields"]["booktitle"] == "Proceedings of NAACL"
    assert official["raw_payload"]["response"] == ACL_BIB
    assert official["identifiers"]["acl"] == "N19-1423"


async def test_arxiv_explicit_relation_can_discover_acl_without_title():
    net = Network(lambda url: (200, ATOM if "arxiv" in url else ACL_BIB))
    records, _, _ = await lookup(net, {"eprint": "1810.04805"}, ["arxiv", "acl"])
    assert {r["provider"] for r in records} == {"arxiv", "acl"}


async def test_pmlr_title_discovery_via_openalex_requires_official_confirmation():
    item = {"id": "https://openalex.org/W123", "title": "A Synthetic Paper", "publication_year": 2020,
            "authorships": [], "locations": [{"landing_page_url": "https://proceedings.mlr.press/v139/example21a.html"}]}

    def handler(url):
        return (200, {"results": [item]}) if "openalex" in url else (200, f"<pre>{PMLR_BIB}</pre>")

    net = Network(handler)
    records, _, _ = await lookup(net, {"title": "A Synthetic Paper"}, ["openalex", "pmlr"])
    official = next(r for r in records if r["provider"] == "pmlr")
    assert official["fields"]["year"] == "2021"  # not the index's incorrect year
    assert official["fields"]["volume"] == "139"
    assert official["identifiers"] == {"pmlr": "v139/example21a"}


async def test_pmlr_hinted_volume_discovery_is_bounded():
    def handler(url):
        if url.endswith("/v139/"):
            return 200, ('<a href="example21a.html">A Synthetic Paper</a>'
                         '<a href="unrelated21.html">Unrelated object</a>')
        return 200, f"<pre><code>{PMLR_BIB}</code></pre>"

    net = Network(handler)
    records, _, _ = await lookup(net, {"title": "A Synthetic Paper", "volume": "139"}, ["pmlr"])
    assert len(records) == 1 and len(net.calls) == 2
    assert all("v139" in call[0] for call in net.calls)


async def test_official_record_must_corroborate_requested_identity():
    net = Network(lambda url: (200, ACL_BIB.replace("N19-1423", "N19-9999")))
    records, events, _ = await lookup(net, {"url": "https://aclanthology.org/N19-1423/"}, ["acl"])
    assert not records
    assert any("identity does not match" in (e["error"] or "") for e in events)


async def test_official_no_candidates_truthfully_reports_limited_coverage():
    net = Network(lambda url: pytest.fail("No entire index download is allowed"))
    records, events, _ = await lookup(net, enabled=["acl", "pmlr"])
    assert not records and not net.calls
    assert len([e for e in events if e["status"] == "unsupported"]) == 2


async def test_invalid_provider_response_does_not_drop_other_sources():
    net = Network(lambda url: (200, {"message": []}) if "crossref" in url else (200, ATOM))
    records, events, _ = await lookup(net, enabled=["crossref", "arxiv"])
    assert [r["provider"] for r in records] == ["arxiv"]
    assert any(e["provider"] == "crossref" and e["status"] == "error" for e in events)


async def test_error_is_not_negative_cache_but_empty_has_short_ttl(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(sources, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    net = Network(lambda url: (503, {}))
    _, events, registry = await lookup(net)
    assert not registry._cache and any(e["status"] == "error" for e in events)
    net.handler = lambda url: (200, {"message": {"items": []}})
    _, events, registry = await lookup(net, registry=registry)
    assert any(e["status"] == "empty" for e in events)
    assert registry._cache
    assert all(expiry == 1060.0 for expiry, _ in registry._cache.values())
    requests = len(net.calls)
    clock[0] = 1059.0
    await lookup(net, registry=registry)
    assert len(net.calls) == requests
    clock[0] = 1060.0
    await lookup(net, registry=registry)
    assert len(net.calls) == requests + 1


async def test_malformed_json_is_not_cached():
    net = Network(lambda url: (200, "{garbage"))
    _, events, registry = await lookup(net)
    assert not registry._cache and any(e["status"] == "error" for e in events)


async def test_cache_reuses_original_fetched_at_and_clear_is_explicit():
    net = Network(lambda url: (200, {"message": {"items": [CROSSREF]}}))
    first, _, registry = await lookup(net)
    first[0]["fields"]["title"] = "mutated in caller"
    second, events, _ = await lookup(net, registry=registry)
    assert len(net.calls) == 1 and any(e["cache_hit"] for e in events)
    assert second[0]["fields"]["title"] == BERT_TITLE
    assert second[0]["fetched_at"] == "2026-10-30T00:00:00+00:00"
    assert registry.clear_cache() == 1
    assert registry.clear_cache() == 0


async def test_budget_counts_each_wire_request_and_preserves_partial_results():
    net = Network(lambda url: (200, {"message": CROSSREF}))
    records, events, _ = await lookup(net, {"doi": "10.18653/v1/N19-1423", "title": "Intended different paper"},
                                      ["crossref", "arxiv"], {"max_source_requests": 1})
    assert len(net.calls) == 1 and len(records) == 1
    assert any(e["status"] == "budget_exhausted" for e in events)
    assert sum(e["request_count"] for e in events) == len(net.calls)


async def test_disabled_providers_never_called():
    net = Network(lambda url: pytest.fail("disabled provider reached"))
    records, _, _ = await lookup(net, settings={"source_enabled": dict.fromkeys(sources.PROVIDERS, False)})
    assert not records and not net.calls


async def test_cancel_before_network_and_during_network():
    net = Network(lambda url: pytest.fail("cancelled lookup reached network"))
    _, events, _ = await lookup(net, cancelled=lambda: True)
    assert any(e["status"] == "cancelled" for e in events)
    state = {"cancelled": False}

    async def handler(url):
        state["cancelled"] = True
        await asyncio.sleep(100)

    net = Network(handler)
    records, events, _ = await asyncio.wait_for(lookup(net, cancelled=lambda: state["cancelled"]), 1)
    assert not records and any(e["status"] == "cancelled" for e in events)


async def test_private_dns_never_reaches_transport():
    net = Network(lambda url: pytest.fail("private DNS reached transport"))

    async def resolver(host):
        return ["127.0.0.1"]

    registry = SourceRegistry(transport=net, resolver=resolver)
    _, events, _ = await lookup(net, registry=registry)
    assert not net.calls and any("non-public" in (e["error"] or "") for e in events)


async def test_redirect_to_private_url_rejected_and_dns_rechecked_on_allowed_hop():
    net = Network(lambda url: (302, "", {"location": "http://127.0.0.1/internal"}))
    _, events, _ = await lookup(net)
    assert len(net.calls) == 1 and any(e["status"] == "error" for e in events)
    calls = []

    async def resolver(host):
        calls.append(host)
        return ["93.184.216.34"] if len(calls) == 1 else ["10.0.0.1"]

    net = Network(lambda url: (302, "", {"location": "https://api.crossref.org/other"}))
    _, events, _ = await lookup(net, registry=SourceRegistry(transport=net, resolver=resolver))
    assert len(calls) == 2 and len(net.calls) == 1
    assert any("non-public" in (e["error"] or "") for e in events)


async def test_response_bytes_are_bounded():
    net = Network(lambda url: (200, b"x" * (MAX_BYTES + 1)))
    _, events, registry = await lookup(net)
    assert not registry._cache and any("byte limit" in (e["error"] or "") for e in events)


async def test_401_no_secrets_in_request_or_audit():
    net = Network(lambda url: (401, {"secret": "do-not-log-body"}))
    _, events, _ = await lookup(net, settings={"credentials": {"crossref": "do-not-send-key"}})
    assert "do-not-send-key" not in str(net.calls) + str(events)
    assert "do-not-log-body" not in str(events)
    assert any("keyless access unavailable" in (e["error"] or "") for e in events)


async def test_retry_is_bounded_and_counted(monkeypatch):
    waits = []

    async def pause(seconds, cancelled):
        waits.append(seconds)

    monkeypatch.setattr(sources, "_pause", pause)
    net = Network(lambda url: (429, {}, {"retry-after": "999999"}))
    _, events, _ = await lookup(net, settings={"retries": 1})
    assert len(net.calls) == 2 and max(waits) <= 5
    assert [e["attempt"] for e in events if "attempt" in e] == [1, 2]
    assert sum(e["request_count"] for e in events) == len(net.calls)


async def test_configured_timeout_and_positive_ttl_are_not_silently_shortened(monkeypatch):
    monkeypatch.setattr(sources, "time", SimpleNamespace(monotonic=lambda: 1000.0))
    net = Network(lambda url: (200, {"message": {"items": [CROSSREF]}}))
    _, _, registry = await lookup(net, settings={"request_timeout_seconds": 60, "source_cache_ttl_seconds": 604800})
    assert net.calls[0][2] == 60
    assert all(expiry == 605800.0 for expiry, _ in registry._cache.values())


async def test_mock_is_fixed_isolated_and_not_derived_from_input():
    net = Network(lambda url: pytest.fail("mock reached network"))
    records, _events, registry = await lookup(net, {"title": BERT_TITLE, "year": "1900", "pages": "FAKE"}, mode="mock")
    assert len(records) == 3
    assert {r["fields"]["year"] for r in records} == {"2018", "2019"}
    assert all(r["status"] == "mock" and r["source_type"] == "mock" and r["provider"].startswith("mock:")
               for r in records)
    assert all(r["fields"].get("pages") != "FAKE" for r in records)
    assert not registry._cache and not net.calls
    records[0]["fields"]["title"] = "caller mutation"
    again, _, _ = await lookup(net, {"eprint": "1810.04805"}, mode="mock")
    assert again[0]["fields"]["title"] == BERT_TITLE
    unknown, _, _ = await lookup(net, {"title": "Unknown imaginary paper", "doi": "10.1234/unknown"}, mode="mock")
    assert not unknown


def test_pinned_connection_uses_checked_ip_and_original_tls_hostname(monkeypatch):
    calls = []

    class Sock:
        def close(self):
            pass

    class Context:
        def wrap_socket(self, sock, server_hostname):
            calls.append(("sni", server_hostname))
            return sock

    monkeypatch.setattr(sources.socket, "create_connection", lambda address, timeout: calls.append(
        ("connect", address, timeout)) or Sock())
    monkeypatch.setattr(sources.ssl, "create_default_context", lambda: Context())
    connection = sources._PinnedHTTPSConnection("api.crossref.org", "93.184.216.34", 3)
    connection.connect()
    assert calls == [("connect", ("93.184.216.34", 443), 3), ("sni", "api.crossref.org")]


@pytest.mark.parametrize("provider,host,expected", [
    ("openalex", "api.openalex.org", {"Authorization": "Bearer configured-test-key"}),
    ("openreview", "api2.openreview.net", {"Authorization": "Bearer configured-test-key"}),
    ("semantic_scholar", "api.semanticscholar.org", {"x-api-key": "configured-test-key"}),
])
async def test_supported_credentials_header_scoped_and_never_logged(provider, host, expected):
    calls = []
    async def transport(url, addresses, timeout, max_bytes, *, headers=None):
        calls.append((url, headers))
        body = b'{"results":[],"data":[],"notes":[]}'
        return Response(200, {}, body, url, "2026-10-30T00:00:00Z")
    records, events, registry = await lookup(transport, fields={"title": BERT_TITLE, "url": "https://openreview.net/forum?id=example"}, enabled=[provider],
        settings={"credentials": {provider: "configured-test-key"}})
    assert calls and calls[0][1] == expected and urlsplit(calls[0][0]).hostname == host
    assert "configured-test-key" not in str(events) + str(records) + str(registry._cache)


async def test_credential_is_not_forwarded_to_other_allowlisted_host():
    calls = []
    async def transport(url, addresses, timeout, max_bytes, *, headers=None):
        calls.append((url, headers))
        if len(calls) == 1:
            return Response(302, {"location": "https://api.crossref.org/redirect"}, b"", url)
        return Response(200, {}, b'{"results":[]}', url)
    await lookup(transport, enabled=["openalex"], settings={"credentials": {"openalex": "test-key"}})
    assert calls[0][1] and calls[1][1] is None


async def test_authenticated_openreview_restricted_field_is_not_audited_or_cached():
    async def transport(url, addresses, timeout, max_bytes, *, headers=None):
        note = {"readers": ["everyone"], "content": {"authors": {"readers": ["chairs"], "value": "private author"}}}
        return Response(200, {}, json.dumps({"notes": [note]}).encode(), url)
    records, events, registry = await lookup(transport, {"url": "https://openreview.net/forum?id=example"},
                                              ["openreview"], {"credentials": {"openreview": "example-token"}})
    assert not records and not registry._cache
    assert "private author" not in str(events) and "example-token" not in str(events)


async def test_posted_content_remains_preprint_not_journal():
    record = {**CROSSREF, "type": "posted-content"}
    net = Network(lambda url: (200, {"message": {"items": [record]}}))
    records, _, _ = await lookup(net)
    assert records[0]["fields"]["publication_type"] == "preprint"
    assert records[0]["fields"]["entrytype"] == "misc"
    assert "journal" not in records[0]["fields"]
    assert json.loads(records[0]["raw_payload"]["response_body"])["message"]["items"][0] == record

@pytest.mark.parametrize("route", ["forum", "title", "wrong_record"])
async def test_tmlr_confirms_only_the_official_forum_bibliography(route):
    forum = "FPVCb0WMuN"
    title = "Process Reward Models That Think"
    journal = "Transactions on Machine Learning Research"
    url = f"https://openreview.net/forum?id={forum}"
    returned_url = url.lower() if route == "wrong_record" else url
    bib = (f"@article{{paper, title={{{title}}}, author={{Muhammad Khalifa}}, "
           f"journal={{{journal}}}, year={{2026}}, url={{{returned_url}}}}}")
    def handler(request):
        if request.startswith("https://api2.openreview.net/notes?"):
            return 200, {"notes": [{"id": forum}]}
        assert request == f"https://jmlr.org/tmlr/papers/bib/{forum}.bib"
        return 200, bib

    fields = {"title": title, "journal": journal} if route == "title" else {"url": url}
    records, events, _ = await lookup(Network(handler), fields, ["tmlr"])
    if route == "wrong_record":
        assert not records and any(event["status"] == "error" for event in events)
    else:
        assert records[0]["identifiers"] == {"tmlr": forum}
        assert records[0]["fields"]["journal"] == journal
        assert records[0]["raw_payload"]["response_body"] == bib
@pytest.mark.parametrize("state", ["accepted", "submission", "withdrawn", "reply"])
async def test_openreview_forum_requires_accepted_top_level_note(state):
    paper = "v8L0pN6EOi"
    venue = "ICLR.cc/2024/Conference"
    fields = {"title": "{Let's Verify Step by Step}", "author": "Lightman, Hunter and Kosaraju, Vineet",
              "url": f"https://openreview.net/forum?id={paper}", "year": "2024",
              "booktitle": "International Conference on Learning Representations"}
    content = {"title": {"value": "Let's Verify Step by Step"},
               "authors": {"value": ["Hunter Lightman", "Vineet Kosaraju"]},
               "venueid": {"value": venue if state == "accepted" else venue + "/" + state}}
    note = {"id": paper, "forum": "different" if state == "reply" else paper, "content": content,
            "invitations": [venue + "/-/Submission"], "pdate": None if state == "submission" else 1700000000000}
    net = Network(lambda url: (200, {"notes": [note]}))
    records, events, _ = await lookup(net, fields, ["openreview"])
    assert [call[0] for call in net.calls] == [f"https://api2.openreview.net/notes?id={paper}"]
    if state == "reply":
        assert not records and any(e["status"] == "error" for e in events)
    elif state == "accepted":
        assert records[0]["status"] == "verified" and sources.current_match(fields, records[0])[0]
    else:
        assert records[0]["status"] == "candidate" and "booktitle" not in records[0]["fields"]


async def test_openreview_challenge_and_shared_rate_cooldown():
    fields = {"url": "https://openreview.net/forum?id=v8L0pN6EOi"}
    challenge = Network(lambda url: (403, {"name": "ChallengeRequiredError"}))
    _, events, _ = await lookup(challenge, fields, ["openreview"], {"retries": 2})
    assert len(challenge.calls) == 1
    assert any("访问验证" in (e.get("error") or "") for e in events)
    limited = Network(lambda url: (429, {}, {"retry-after": "60"}))
    _, events, registry = await lookup(limited, fields, ["openreview"], {"retries": 2})
    assert any(e["status"] == "rate_limited" for e in events)
    await lookup(limited, {"journal": "TMLR", "title": "Second paper"}, ["tmlr"], registry=registry)
    assert len(limited.calls) == 1
