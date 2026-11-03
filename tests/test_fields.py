from copy import deepcopy

import pytest

from cite_audit.assessment import assess, current_match
from cite_audit.fields import FIELD_RULE_VERSION, audit_items, comparison_fields
from cite_audit.knowledge import Knowledge
from cite_audit.sources import MOCK_BERT_RECORDS
from cite_audit.storage import Store


def assessment(tmp_path, extra=None, source_extra=None):
    kb = Knowledge(Store(tmp_path / "fields.sqlite3"))
    record = deepcopy(MOCK_BERT_RECORDS[1])
    record["fields"].update(source_extra or {})
    sources = kb.ingest([record], "mock")
    fields = {**record["fields"], **(extra or {})}
    entry = {"fields": fields, "entry_type": fields.pop("entrytype"), "citation_key": "sample"}
    return assess(kb, entry, sources, "demo", [])


def test_auxiliary_fields_are_preserved_without_fact_judgments(tmp_path):
    result = assessment(tmp_path, {"file": "local.pdf", "keywords": "mine", "custom": "keep"})
    assert {a["field"] for a in result["auxiliary_fields"]} == {"file", "keywords", "custom"}
    assert not {"file", "keywords", "custom"} & {a["field"] for a in result["field_assessments"]}
    assert result["evidence_revision"]["field_rule_version"] == FIELD_RULE_VERSION


def test_alias_uses_original_name_and_actual_source_locator(tmp_path):
    result = assessment(tmp_path, {"eid": "42"}, {"articleno": "42"})
    rows = [a for a in result["field_assessments"] if a["semantic_key"] == "article_number"]
    assert len(rows) == 1
    assert rows[0]["judgment"] == "consistent"
    assert rows[0]["input_fields"] == {"articleno": "42", "eid": "42"}
    assert rows[0]["evidence"][0]["locator"] == "fields.articleno"


def test_conflicting_input_aliases_never_generate_patch(tmp_path):
    result = assessment(tmp_path, {"eid": "99"}, {"articleno": "42"})
    row = next(a for a in result["field_assessments"] if a["semantic_key"] == "article_number")
    assert row["judgment"] == "unverifiable" and row["input_conflict"]
    assert all(not {"eid", "articleno"} & p["fields"].keys() for p in result["proposals"])


def test_absent_optional_fields_are_not_missing_requirements(tmp_path):
    result = assessment(tmp_path)
    assert "number" not in {a["field"] for a in result["field_assessments"]}


@pytest.mark.parametrize("kind,key", [("article", "issue"), ("techreport", "report_number")])
def test_number_is_interpreted_by_type(kind, key):
    row = next(meta for _, _, meta in audit_items({"entrytype": kind, "number": "17"}, None)
               if meta["semantic_key"] == key)
    assert row["applicable"]


def test_dates_and_article_locators_do_not_collapse():
    fields = {"entrytype": "article", "year": "2020", "date": "2020-02-01",
              "first_online_year": "2019", "pages": "1--10", "eid": "10"}
    keys = {meta["semantic_key"] for _, _, meta in audit_items(fields, None)}
    assert {"publication_year", "publication_date", "first_online_year", "pages", "article_number"} <= keys


def test_conflicting_source_aliases_remain_unresolved():
    current = {"fields": {"entrytype": "article"}, "field_selections": {
        name: {"value": value, "status": "selected", "evidence": []}
        for name, value in [("journal", "Journal A"), ("journaltitle", "Journal B")]}}
    _, selection, _ = next(row for row in audit_items({"entrytype": "article", "journal": "Journal A"}, current)
                           if row[2]["semantic_key"] == "journal")
    assert selection["status"] == "conflicting" and selection["value"] is None
    assert "journal" not in comparison_fields({"journal": "A", "journaltitle": "B"})


def test_alias_can_support_identity_without_mutating_input():
    fields = {"title": "A sample", "author": "Smith, Jane", "year": "2020", "journaltitle": "Journal A"}
    original = dict(fields)
    version = {"fields": {**fields, "journal": fields["journaltitle"]}, "identifiers": {}}
    assert current_match(fields, version)[0]
    assert fields == original


def test_preprint_does_not_gain_journal_fields():
    current = {"identifiers": {"arxiv": "1234.56789"}, "fields": {"entrytype": "article"},
               "field_selections": {"journal": {"value": "arXiv", "status": "selected", "evidence": []}}}
    row = next(meta for _, _, meta in audit_items({"entrytype": "article"}, current)
               if meta["semantic_key"] == "journal")
    assert not row["applicable"]


@pytest.mark.asyncio
async def test_export_cannot_apply_only_one_alias_and_report_stays_frozen(tmp_path):
    from cite_audit.service import AuditService
    from cite_audit.sources import BERT_AUTHORS, BERT_TITLE
    service = AuditService(tmp_path)
    raw = (f'@article{{key, title={{{BERT_TITLE}}}, author={{{BERT_AUTHORS}}}, '
           'year={2018}, eprint={1810.04805}, archiveprefix={ARXIV}, eprinttype={ARXIV}, custom={keep}}').encode()
    task = service.create_task(raw, "aliases.bib", "", "demo")
    await service.run_task(task["id"])
    entry = service.entries(task["id"], True)[0]
    proposal = next(p for p in entry["proposals"] if p["proposal_type"] == "same_version_patch")
    selection = {"entry_id": entry["id"], "proposal_id": proposal["id"], "fields": ["eprinttype"]}
    with pytest.raises(ValueError, match="Aliases"):
        service.export_preview(task["id"], [selection])
    frozen = service.freeze(task["id"])
    selection["fields"] = ["eprinttype", "archiveprefix"]
    preview, content = service.export_preview(task["id"], [selection])
    assert not preview["blocked"] and preview["warnings"]
    assert b"custom={keep}" in content and b"ARXIV" not in content
    assert service.original(task["id"]) == raw
    assert service.freeze(task["id"]) == frozen
