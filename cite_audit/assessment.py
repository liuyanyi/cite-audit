"""Create a task-local, frozen assessment from source-backed versions."""
from __future__ import annotations

import re

from .knowledge import (
    RULE_VERSION,
    Knowledge,
    authors,
    field_normalize,
    identifiers,
    identity_compatible,
    normalize,
    normalize_id,
)
from .storage import uid

BIB_FIELDS = ("entrytype", "title", "author", "editor", "year", "doi", "journal", "booktitle", "volume",
              "number", "pages", "articleno", "publisher", "url", "eprint", "archiveprefix", "primaryclass")


def evidence(items: list[dict], field: str) -> list[dict]:
    return [{**item, "field": field, "locator": item.get("field_path", f"fields.{field}")} for item in items]


def brief_bib(key: str, fields: dict) -> str:
    from .bib import serialize_entry
    try:
        return serialize_entry(key, fields.get("entrytype", "misc"),
                               {k: v for k, v in fields.items() if k != "entrytype" and v is not None})
    except ValueError:
        return ""


def current_match(input_fields: dict, version: dict) -> tuple[bool, str]:
    vf, vi = version["fields"], version["identifiers"]
    if not identity_compatible(input_fields, vf):
        return False, "Input title/author contradict this publication identifier"
    input_ids = identifiers({"fields": input_fields})
    if input_ids.get("arxiv"):
        if (vi.get("arxiv") == input_ids["arxiv"] or
                (not re.search(r"v\d+$", input_ids["arxiv"]) and
                 re.sub(r"v\d+$", "", vi.get("arxiv", "")) == input_ids["arxiv"])):
            return True, "arXiv identifier and bibliographic identity agree"
        return False, "Different publication identity from the input arXiv record"
    if input_ids.get("doi") and vi.get("doi") == input_ids["doi"]:
        return True, "DOI resolved to a compatible title/author identity"
    # Input identifiers are not truth. When DOI is wrong, a separately retrieved
    # record may still match by the full publication tuple; title alone cannot.
    title_ok = bool(normalize(input_fields.get("title"))) and normalize(input_fields.get("title")) == normalize(vf.get("title"))
    la, ra = authors(input_fields.get("author", "")), authors(vf.get("author", ""))
    author_ok = bool(la and ra) and la == ra
    year_ok = bool(input_fields.get("year")) and input_fields.get("year") == vf.get("year")
    venue = input_fields.get("booktitle") or input_fields.get("journal")
    target_venue = vf.get("booktitle") or vf.get("journal")
    venue_ok = bool(venue and target_venue) and normalize(venue) == normalize(target_venue)
    pages_ok = bool(input_fields.get("pages")) and normalize(input_fields["pages"]) == normalize(vf.get("pages"))
    if title_ok and author_ok and year_ok and (venue_ok or pages_ok):
        return True, "Title, ordered authors, year and publication venue/pages jointly agree"
    return False, "Candidate lacks sufficient corroborated publication identity"


def assess(knowledge: Knowledge, entry: dict, source_records: list[dict], mode: str, calls: list[dict]) -> dict:
    original = dict(entry["fields"])
    original["entrytype"] = entry["entry_type"]
    versions = {}
    candidates = []
    mismatch_dois = []
    for source in source_records:
        if source.get("version_id"):
            version = knowledge.version(source["version_id"])
            versions[version["id"]] = version
            if (original.get("doi") and version["identifiers"].get("doi") == normalize_id("doi", original["doi"])
                    and not identity_compatible(original, version["fields"])):
                mismatch_dois.append(source)
        else:
            candidates.append({"title": source.get("fields", {}).get("title", "Untitled candidate"),
                               "provider": source["provider"], "record_id": source["record_id"],
                               "url": source.get("source_url"), "reason": source.get("assignment_reason", "Unresolved source"),
                               "status": "unresolved"})
    matches = [(v, reason) for v in versions.values() for valid, reason in [current_match(original, v)] if valid]
    failed_calls = [c for c in calls if c.get("status") in {"failed", "rate_limited", "budget_exhausted", "partial"}]
    current = matches[0][0] if len(matches) == 1 else None
    if current:
        identity_state, reason = "confirmed", matches[0][1]
    elif len(matches) > 1:
        identity_state, reason = "ambiguous", "More than one publication record matches; no automatic replacement"
    elif failed_calls and not source_records:
        identity_state, reason = "source_failed", "Source access failed or budget exhausted; existence is not determined"
    elif candidates or versions:
        identity_state, reason = "ambiguous", "Retrieved candidates cannot establish this exact publication identity"
    elif not original.get("title") and not (original.get("doi") or original.get("eprint")):
        identity_state, reason = "insufficient", "No usable title or identifier in this entry"
    else:
        identity_state, reason = "not_found", "No matching record in the checked sources; this does not prove nonexistence"
    if mode == "demo":
        reason += "; demonstration fixture only, not online verification"
    article = knowledge.article(current["article_id"]) if current else None
    recommended = article["recommended_version"] if article else None
    rec_status = ("same_version" if recommended and recommended["id"] == current["id"] else
                  "upgrade_available" if recommended else "unavailable")
    assessments = []
    for field in dict.fromkeys([*BIB_FIELDS, *original.keys()]):
        before = original.get(field)
        selection = current["field_selections"].get(field, {}) if current else {}
        after = selection.get("value")
        refs = evidence(selection.get("evidence", []), field)
        if not current or selection.get("status") == "conflicting":
            judgment = "unverifiable"
            field_reason = ("Current publication is not confirmed" if not current else "Sources conflict within this version")
        elif before is None and after is None:
            judgment, field_reason = "not_applicable", "No value supplied or evidenced for this version"
        elif after is None:
            judgment, field_reason = "unverifiable", "No source evidence for this field; original retained"
        elif before is None:
            judgment, field_reason = "completion", "Missing value is supplied by current-version evidence"
        elif str(before) == str(after):
            judgment, field_reason = "consistent", "Matches the current publication version"
        elif field_normalize(field, before) == field_normalize(field, after):
            judgment, field_reason = "normalization", "Equivalent punctuation, escaping or letter case"
        elif field == "author" and authors(before) == authors(after):
            judgment, field_reason = "unverifiable", "Author surnames/order agree but abbreviation cannot prove full-name equality"
        else:
            judgment, field_reason = "conflict", "Differs from the confirmed current publication record"
        if field == "doi" and mismatch_dois:
            judgment, field_reason = "conflict", "The supplied DOI resolves to a different title/author identity"
            refs = [{"source_content_id": s["id"], "snapshot_id": s["snapshot_id"], "field": "doi", "locator": "fields.doi"}
                    for s in mismatch_dois]
        assessments.append({"field": field, "original_value": before, "reference_value": after,
                            "judgment": judgment, "reason": field_reason, "evidence": refs})
    differences = []
    if recommended:
        for field in dict.fromkeys([*BIB_FIELDS, *original.keys(), *recommended["fields"].keys()]):
            before, after = original.get(field), recommended["fields"].get(field)
            selection = recommended["field_selections"].get(field, {})
            differences.append({"field": field, "original_value": before, "reference_value": after,
                                "judgment": "unchanged" if field_normalize(field, before) == field_normalize(field, after) else
                                "version_change" if after is not None or field in BIB_FIELDS else "target_unverified",
                                "evidence": evidence(selection.get("evidence", []), field)})
    proposals = []
    patch = {a["field"]: a["reference_value"] for a in assessments if a["judgment"] in {
        "conflict", "completion", "normalization"} and a["reference_value"] is not None}
    if current and patch:
        proposal_fields = {**original, **patch}
        proposals.append({"id": uid("prop"), "proposal_type": "same_version_patch", "target_version_id": current["id"],
                          "fields": patch, "remove_fields": [], "bibtex": brief_bib(entry["citation_key"], proposal_fields),
                          "reason": "Apply only evidenced fields within the current publication version",
                          "evidence": [e for a in assessments if a["field"] in patch for e in a["evidence"]],
                          "safe": bool(entry.get("editable", True)),
                          "blocked_reason": None if entry.get("editable", True) else "Parser cannot safely locate edits"})
    if rec_status == "upgrade_available":
        fields = dict(recommended["fields"])
        proposals.append({"id": uid("prop"), "proposal_type": "whole_version_switch", "target_version_id": recommended["id"],
                          "fields": fields, "remove_fields": [k for k in BIB_FIELDS if k not in fields],
                          "bibtex": brief_bib(entry["citation_key"], fields),
                          "reason": "Explicit source-backed same-work relation; switch as one coherent publication",
                          "evidence": [e for sel in recommended["field_selections"].values() for e in sel["evidence"]],
                          "safe": bool(entry.get("editable", True)),
                          "blocked_reason": None if entry.get("editable", True) else "Parser cannot safely locate edits"})
    for proposal in proposals:
        if not proposal["bibtex"]:
            proposal["safe"] = False
            proposal["blocked_reason"] = "Source values cannot be safely serialized as BibTeX"
    used_sources = {s["id"]: s for v in [current, recommended] if v for s in v["sources"]}
    # Keep wrong-DOI evidence inspectable even when no current version is confirmed.
    used_sources.update({s["id"]: s for s in mismatch_dois})
    for version in versions.values():
        if not current or version["id"] != current["id"]:
            candidates.append({"title": version["fields"].get("title", "Untitled"),
                               "provider": version["sources"][0]["provider"],
                               "record_id": version["sources"][0]["record_id"],
                               "url": version["sources"][0].get("source_url"),
                               "reason": "Retrieved publication; current-version membership must be confirmed",
                               "status": "unresolved"})
    return {
        "identity_state": identity_state, "identity_reason": reason,
        "version_status": "current_version_confirmed" if current else "candidate" if candidates else "unknown",
        "recommendation_status": rec_status, "article_id": current["article_id"] if current else None,
        "current_version_id": current["id"] if current else None,
        "recommended_version_id": recommended["id"] if recommended else None,
        "field_assessments": assessments,
        "recommendation": {"status": rec_status, "version_id": recommended["id"] if recommended else None,
                           "reason": article["recommendation"]["reason"] if article else "Current publication not confirmed",
                           "revision": article["recommendation"]["revision"] if article else 0,
                           "differences": differences},
        "current_version": current, "recommended_version": recommended,
        "version_relations": [r for v in article["versions"] for r in v["relations"]] if article else [],
        "proposals": proposals, "sources": list(used_sources.values()), "candidates": candidates, "calls": calls,
        "warnings": ["Demonstration data — no online verification"] if mode == "demo" else [],
        "evidence_revision": {"merge_revision": current["merge_revision"] if current else None,
                              "recommendation_revision": article["recommendation"]["revision"] if article else None,
                              "rule_version": RULE_VERSION},
        "has_conflict": any(a["judgment"] == "conflict" for a in assessments),
        "conflict_count": sum(a["judgment"] == "conflict" for a in assessments),
        "completion_count": sum(a["judgment"] == "completion" for a in assessments),
    }
