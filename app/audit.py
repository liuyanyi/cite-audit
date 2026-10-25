"""Deterministic, conservative, evidence-linked bibliographic assessments.

No provider calls or model-memory facts live here. A record with a close title is
only a candidate. Identity, current-version corrections, and publication-version
replacement are intentionally separate decisions.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata
from urllib.parse import unquote

from .bibtex import BibTeXError, render_suggestion

RULE_VERSION = "2026-10-25.2"
_FIELDS = ("title", "author", "year", "journal", "booktitle", "volume", "number", "pages", "eid", "doi", "publisher", "editor", "month", "address", "url", "isbn", "issn", "series", "edition", "organization", "abstract", "keywords")
_SUPPORTED = {"article", "inproceedings", "conference", "misc", "book", "inbook", "incollection", "proceedings", "phdthesis", "mastersthesis", "techreport", "report", "unpublished", "online"}
_VERSION_FIELDS = {"title", "author", "year", "date", "journal", "journaltitle", "booktitle", "volume", "number", "issue", "pages", "eid", "doi", "url", "eprint", "eprinttype", "archiveprefix", "primaryclass", "crossref", "publisher", "editor", "month", "address", "isbn", "issn", "series", "edition", "organization", "abstract", "keywords"}


def normalize_doi(value: object) -> str:
    value = unquote(str(value or "")).strip()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", value, flags=re.I).strip()
    return value.casefold()


def valid_doi(value: object) -> bool:
    return bool(re.fullmatch(r"10\.\d{4,9}/[^\s{}]+", normalize_doi(value)))


def _text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    # TeX is compared, never executed. Keep command names and all protected text.
    text = re.sub(r"\\['`\^\"~=.]\s*\{?([A-Za-z])\}?", r"\1", text)
    text = re.sub(r"\\(?:textit|textbf|emph|mathrm|textrm|textsc)\s*", "", text)
    text = re.sub(r"\\([A-Za-z]+)", r"\1", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join("".join(c.casefold() if c.isalnum() or c in "+=<>/" else " " for c in text).split())


def _arxiv_full(value: object) -> str:
    value = str(value or "").strip()
    value = re.sub(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/", "", value, flags=re.I)
    value = re.sub(r"^(?:arxiv\s*:\s*|10\.48550/arxiv\.)", "", value, flags=re.I)
    value = value.removesuffix(".pdf")
    if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", value, re.I):
        return value.casefold()
    return ""


def _arxiv(value: object) -> str:
    return re.sub(r"v\d+$", "", _arxiv_full(value))


def _entry_arxiv(fields: dict) -> str:
    for key in ("eprint", "arxiv", "doi", "url"):
        candidate = _arxiv_full(fields.get(key))
        if candidate:
            return candidate
    return ""


def _preprint(fields: dict) -> bool:
    journal = str(fields.get("journal", fields.get("journaltitle", ""))).casefold()
    if "arxiv" in journal or normalize_doi(fields.get("doi")).startswith("10.48550/arxiv."):
        return True
    # Published entries often retain an arXiv eprint as a related identifier.
    # That identifier alone must not turn the journal version into a preprint.
    if journal or fields.get("booktitle"):
        return False
    return bool(_entry_arxiv(fields) or str(fields.get("archiveprefix", fields.get("eprinttype", ""))).casefold() == "arxiv")


def _split_authors(value: str) -> list[str]:
    result, start, depth, pos = [], 0, 0, 0
    while pos < len(value):
        if value[pos] == "\\":
            pos += 2
            continue
        if value[pos] == "{":
            depth += 1
        elif value[pos] == "}":
            depth = max(0, depth - 1)
        elif depth == 0 and value[pos:pos + 5].lower() == " and ":
            result.append(value[start:pos].strip())
            pos += 5
            start = pos
            continue
        pos += 1
    result.append(value[start:].strip())
    return [part for part in result if part]


def _names(value: object) -> list[tuple[str, tuple[str, ...]]]:
    if isinstance(value, list):
        value = " and ".join(str(part.get("name", "")) if isinstance(part, dict) else str(part) for part in value)
    result = []
    for name in _split_authors(" ".join(str(value or "").split())):
        if name.startswith("{") and name.endswith("}"):
            result.append((_text(name), ()))
            continue
        comma = name.split(",")
        if len(comma) >= 2:
            family, given = comma[0], comma[-1]
        else:
            words = name.split()
            family_start = max(0, len(words) - 1)
            while family_start > 0 and words[family_start - 1].lower() in {"van", "von", "de", "del", "der", "den", "da", "di", "la", "le"}:
                family_start -= 1
            family, given = " ".join(words[family_start:]), " ".join(words[:family_start])
        given_names = tuple(_text(given).split())
        result.append((_text(family), given_names))
    return result


def authors_equivalent(left: object, right: object) -> bool:
    a, b = _names(left), _names(right)
    if not a or not b or len(a) != len(b):
        return False
    return all(fa == fb and all(ga == gb or (len(ga) == 1 and gb.startswith(ga)) or (len(gb) == 1 and ga.startswith(gb))
                               for ga, gb in zip(ia, ib))
               for (fa, ia), (fb, ib) in zip(a, b))


def _year(value: object) -> int | None:
    match = re.match(r"^\s*(\d{4})(?:\D|$)", str(value or ""))
    return int(match.group(1)) if match else None


def _venue_equivalent(left: object, right: object) -> bool:
    a, b = _text(left).split(), _text(right).split()
    if a == b:
        return True
    # Recognize per-word abbreviations, never use a guessed journal alias map.
    if len(a) != len(b) or not a:
        return False
    return all(x == y or (len(x) >= 3 and y.startswith(x)) or (len(y) >= 3 and x.startswith(y)) for x, y in zip(a, b))


def _equivalent(field: str, left: object, right: object) -> bool:
    if field == "doi":
        return normalize_doi(left) == normalize_doi(right)
    if field == "author":
        return authors_equivalent(left, right)
    if field in {"journal", "booktitle"}:
        return _venue_equivalent(left, right)
    if field == "month":
        months = {name: str(index) for index, name in enumerate(("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"), 1)}
        months.update({name[:3]: value for name, value in list(months.items())})
        normalize = lambda value: months.get(str(value).strip().lower(), str(value).strip().lstrip('0'))
        return normalize(left) == normalize(right)
    if field == "url":
        return str(left).strip() == str(right).strip()
    if field == "pages":
        return re.sub(r"[\s\-–—]+", "-", str(left)).strip("-") == re.sub(r"[\s\-–—]+", "-", str(right)).strip("-")
    return _text(left) == _text(right)


def _fields(record: dict) -> dict:
    fields = {str(key).lower(): value for key, value in record.get("fields", {}).items()
              if value is not None and isinstance(value, (str, int, float)) and not isinstance(value, bool)}
    for old, new in (("journaltitle", "journal"), ("issue", "number"), ("article_number", "eid"), ("article-number", "eid")):
        if old in fields and new not in fields:
            fields[new] = fields[old]
    return fields


def _is_snippet(record: dict) -> bool:
    raw = record.get("raw") if isinstance(record.get("raw"), dict) else {}
    return (str(record.get("provenance", "")).lower() in {"search_snippet", "snippet", "search", "search_result", "model_extraction"}
            or record.get("method") in {"model_extraction", "model_suggestion"}
            or raw.get("evidence_scope") in {"search_lead_only", "page_content_not_structured_metadata"})


def _match(original: dict, record: dict) -> tuple[bool, bool, str]:
    """Return (strong identity, related candidate, explanation)."""
    fields = _fields(record)
    original_title, candidate_title = _text(original.get("title")), _text(fields.get("title"))
    exact_title = bool(original_title and candidate_title and original_title == candidate_title)
    similarity = SequenceMatcher(None, original_title, candidate_title).ratio() if original_title and candidate_title else 0.0
    author_match = authors_equivalent(original.get("author"), fields.get("author"))
    author_conflict = bool(original.get("author") and fields.get("author") and not author_match)
    same_doi = bool(valid_doi(original.get("doi")) and normalize_doi(original.get("doi")) == normalize_doi(fields.get("doi")))
    original_arxiv, source_arxiv = _entry_arxiv(original), _entry_arxiv(fields)
    same_arxiv = bool(original_arxiv and source_arxiv and _arxiv(original_arxiv) == _arxiv(source_arxiv))
    source_year, original_year = _year(fields.get("year")), _year(original.get("year"))
    compatible_year = not (source_year and original_year) or abs(source_year - original_year) <= 1
    if _is_snippet(record):
        return False, exact_title or similarity >= .72 or same_doi, "A search snippet is a discovery lead, not verified metadata"
    if same_doi and exact_title and not author_conflict:
        return True, True, "DOI and title agree, with no author contradiction"
    if same_doi and not original_title and author_match and compatible_year:
        return True, True, "DOI, ordered authors and compatible year corroborate the missing title"
    if same_arxiv and exact_title and not author_conflict:
        return True, True, "arXiv identifier and title agree, with no author contradiction"
    if exact_title and author_match and compatible_year:
        return True, True, "Title, ordered authors and compatible year agree"
    if same_doi:
        return False, True, "The input DOI does not corroborate the original title and authors"
    return False, exact_title or similarity >= .72 or (author_match and similarity >= .5) or same_arxiv, "Similar metadata alone does not uniquely establish identity"


def _record_version(record: dict) -> str:
    version = record.get("version", "unknown")
    if version in {"published", "preprint"}:
        return version
    fields = _fields(record)
    if _preprint(fields):
        return "preprint"
    if fields.get("journal") or fields.get("booktitle"):
        return "published"
    return "unknown"


def _group(records: list[dict]) -> list[list[dict]]:
    """Group same identifiers; identifier-free metadata cannot join two works."""
    groups: dict[str, list[dict]] = {}
    unresolved = []
    for record in records:
        fields = _fields(record)
        doi, arxiv = normalize_doi(fields.get("doi")), _entry_arxiv(fields)
        if valid_doi(doi):
            key = "doi:" + doi
        elif arxiv:
            key = "arxiv:" + arxiv
        else:
            unresolved.append(record)
            continue
        groups.setdefault(key, []).append(record)
    for record in unresolved:
        fields = _fields(record)
        matching = [group for group in groups.values() if _record_version(group[0]) == _record_version(record)
                    and _text(_fields(group[0]).get("title")) == _text(fields.get("title"))
                    and authors_equivalent(_fields(group[0]).get("author"), fields.get("author"))]
        if len(matching) == 1:
            matching[0].append(record)
        else:
            key = "metadata:" + "|".join((_record_version(record), _text(fields.get("title")), str(_names(fields.get("author"))), str(fields.get("year", ""))))
            groups.setdefault(key, []).append(record)
    return list(groups.values())


def _doi_registration_sources(records: list[dict], doi: object) -> list[dict]:
    """Registration and matching identity must both exist in actual evidence.

    A provider/model's self-declared verified flag is not an extra source. Manual
    imports stay imported evidence, not a claim of a live registry lookup.
    """
    target = normalize_doi(doi)
    return [record for record in records if normalize_doi(_fields(record).get("doi")) == target
            and not _is_snippet(record)
            and ((record.get("method") == "api" and record.get("provider") in {"crossref", "doi", "datacite"})
                 or record.get("method") == "mock")]


def _assessment(field: str, original: object, suggested: object, judgment: str, reason: str, evidence: list[dict]) -> dict:
    if evidence and all(record.get("method") == "mock" for record in evidence):
        reason = "[MOCK] " + reason
    elif evidence and all(record.get("method") == "manual" for record in evidence):
        reason = "[MANUAL IMPORT] " + reason
    return {"field": field, "original": original, "suggested": suggested, "judgment": judgment,
            "reason": reason, "evidence_ids": list(dict.fromkeys(record["id"] for record in evidence))}


def _explicit_relation(record: dict, *, doi: str = "", arxiv: str = "") -> bool:
    """Only typed identity/preprint relations support an automatic replacement.

    Cites, references, supplements and generic versions may concern extensions or
    different works. Legacy related_* hints remain candidate leads only.
    """
    if _is_snippet(record):
        return False
    for relation in record.get("relations", []):
        if not isinstance(relation, dict):
            continue
        kind, source = relation.get("type"), relation.get("source")
        if source == "mock" and record.get("method") != "mock":
            continue
        registration = source in {"crossref.relation", "doi.relation", "mock"}
        identical = kind == "same-work" and source in {"semantic_scholar.externalIds", "datacite.relatedIdentifiers", "mock"}
        if doi and valid_doi(doi) and normalize_doi(relation.get("target_doi")) == normalize_doi(doi):
            if identical or (kind == "is-preprint-of" and (registration or source == "arxiv.doi")):
                return True
        if arxiv and _arxiv(relation.get("target_arxiv")) == _arxiv(arxiv):
            if identical or (kind == "has-preprint" and registration):
                return True
    return False


def _publication(entry: dict, records: list[dict], original: dict, issues: list[str]) -> tuple[str, dict | None, list[dict]]:
    if not _preprint(original):
        return "unclear", None, []
    original_arxiv = _entry_arxiv(original)
    related: list[dict] = []
    links: dict[str, list[dict]] = {}
    for record in records:
        fields = _fields(record)
        strong, _, _ = _match(original, record)
        if (_record_version(record) == "preprint" and strong and valid_doi(record.get("related_doi"))
                and _explicit_relation(record, doi=record["related_doi"])):
            links.setdefault(normalize_doi(record["related_doi"]), []).append(record)
    for record in records:
        fields = _fields(record)
        if _record_version(record) != "published" or _is_snippet(record):
            continue
        if (original_arxiv and _arxiv(record.get("related_arxiv")) == _arxiv(original_arxiv)) or normalize_doi(fields.get("doi")) in links:
            related.append(record)
        else:
            _, candidate, _ = _match(original, record)
            if candidate:
                related.append(record)
    if not related:
        return "not_found", None, []
    linked = [record for record in related if (original_arxiv and _explicit_relation(record, arxiv=original_arxiv))
              or normalize_doi(_fields(record).get("doi")) in links]
    # A typed S2 linkage and a separate registry lookup can corroborate the
    # same published version, even when its final title differs from the input.
    for record in records:
        if record in linked or _record_version(record) != "published" or _is_snippet(record):
            continue
        candidate_doi = normalize_doi(_fields(record).get("doi"))
        if candidate_doi and any(candidate_doi == normalize_doi(_fields(reference).get("doi"))
                                 and _match(_fields(reference), record)[0] for reference in linked):
            linked.append(record)
            if record not in related:
                related.append(record)
    groups = _group(linked)
    if len(groups) != 1:
        issues.append("Published records are candidates only: an explicit, unique version relationship has not been established")
        return "candidate", None, related
    group = groups[0]
    for field in _FIELDS:
        values = [(_fields(record).get(field), record) for record in group if _fields(record).get(field)]
        if values and any(not _equivalent(field, values[0][0], value) for value, _ in values[1:]):
            issues.append(f"Published-version sources disagree on {field}; no combined replacement is generated")
            return "candidate", None, related
    complete = [record for record in group if all(_fields(record).get(field) for field in ("title", "author", "year", "doi"))
                and valid_doi(_fields(record).get("doi")) and _doi_registration_sources(group, _fields(record).get("doi"))
                and (_fields(record).get("journal") or _fields(record).get("booktitle"))]
    if not complete:
        issues.append("An explicit published-version link exists, but no single complete publication record supports a replacement")
        return "candidate", None, related
    chosen = max(complete, key=lambda record: len(_fields(record)))
    values = _fields(chosen)
    kind = str(values.get("entrytype", "inproceedings" if values.get("booktitle") else "article"))
    changes = {field: None for field in _VERSION_FIELDS}
    changes.update({field: value for field, value in values.items() if field in _VERSION_FIELDS and field not in {"eprint", "archiveprefix", "primaryclass", "crossref", "eprinttype"}})
    relation_records = links.get(normalize_doi(values["doi"]), []) + [record for record in group if original_arxiv and _explicit_relation(record, arxiv=original_arxiv)]
    try:
        suggestion = render_suggestion(entry, changes, kind)
    except BibTeXError as error:
        issues.append(str(error))
        return "candidate", None, related
    return "candidate", {
        "entry_type": kind, "fields": {key: value for key, value in changes.items() if value is not None},
        "suggested_bibtex": suggestion, "evidence_ids": list(dict.fromkeys([chosen["id"]] + [record["id"] for record in _doi_registration_sources(group, values["doi"])])),
        "relation_evidence_ids": list(dict.fromkeys(record["id"] for record in relation_records)),
        "original_arxiv_id": original_arxiv, "original_entry": entry.get("raw", ""),
        "warning": "Confirm that the published version supports the same claims; BibTeX alone cannot establish substantive equivalence.",
    }, related


def assess(entry: dict, records: list[dict], errors: list[dict] | None = None) -> dict:
    """Assess an immutable entry against explicit, source-separated evidence."""
    errors = errors or []
    issues = list(entry.get("issues", []))
    original = _fields({"fields": entry.get("fields", {})})
    evidence = []
    seen = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("fields"), dict) or not isinstance(record.get("id"), str) or not record["id"]:
            issues.append("An invalid evidence record was excluded")
            continue
        if record["id"] in seen:
            issues.append(f"Duplicate evidence ID {record['id']!r} was excluded")
            continue
        seen.add(record["id"])
        evidence.append(record)
    mock = any(record.get("method") == "mock" for record in evidence)
    if mock:
        issues.append("MOCK evidence is synthetic offline demonstration data, not online verification")
    if any(record.get("method") == "manual" for record in evidence):
        issues.append("Manually imported evidence has not been independently retrieved online")
    if entry.get("entry_type", "").lower() not in _SUPPORTED:
        issues.append("This entry type has limited support; only comparable supplied fields are assessed")
    for error in errors:
        provider = str(error.get("provider", "source"))
        kind = str(error.get("kind", error.get("code", error.get("type", "request_failed"))))
        issues.append(f"{provider}: {kind}; failed retrieval is not evidence that the work or DOI does not exist")

    is_preprint = _preprint(original)
    matches, candidates, doi_conflicts = [], [], []
    for record in evidence:
        fields = _fields(record)
        strong, candidate, reason = _match(original, record)
        if candidate:
            candidates.append(record)
        supplied_doi = normalize_doi(original.get("doi"))
        if supplied_doi and supplied_doi == normalize_doi(fields.get("doi")) and not strong and not _is_snippet(record):
            # Missing titles/authors are insufficient, not proof of wrong ownership.
            title_conflict = bool(original.get("title") and fields.get("title") and _text(original["title"]) != _text(fields["title"]))
            author_conflict = bool(original.get("author") and fields.get("author") and not authors_equivalent(original["author"], fields["author"]))
            if title_conflict or author_conflict:
                doi_conflicts.append(record)
        record_version = _record_version(record)
        same_version = record_version != "published" if is_preprint else record_version != "preprint"
        source_arxiv = _entry_arxiv(fields)
        original_arxiv = _entry_arxiv(original)
        if original_arxiv and source_arxiv and re.search(r"v\d+$", original_arxiv) and original_arxiv != source_arxiv:
            same_version = False
            issues.append("A different or unspecified arXiv version was found; version-specific metadata is not silently substituted")
        if strong and same_version:
            matches.append(record)
    groups = _group(matches)
    selected = groups[0] if len(groups) == 1 else []
    identity_state = "unique" if selected else "multiple" if len(groups) > 1 else "insufficient" if candidates or errors else "not_found"
    if len(groups) > 1:
        issues.append("Multiple distinct identifiers satisfy the metadata; no candidate is selected automatically")
    publication_state, publication_suggestion, publication_candidates = _publication(entry, evidence, original, issues)
    if not is_preprint and selected and any(_record_version(record) == "published" for record in selected):
        publication_state = "published"
    for record in publication_candidates:
        if record not in candidates:
            candidates.append(record)

    assessments, changes = [], {}
    authoritative = [record for record in selected if record.get("provider") == "acl"
                     and record.get("method") == "api" and record.get("provenance") == "acl_anthology_bibtex"]
    kinds = {str(_fields(record).get("entrytype", "")).lower() for record in authoritative}
    suggested_type = None
    type_dependencies = []
    if len(kinds) == 1:
        kind = next(iter(kinds))
        if kind in {"article", "inproceedings", "conference"} and kind != entry.get("entry_type"):
            target_venue = "journal" if kind == "article" else "booktitle"
            old_venue = "booktitle" if kind == "article" else "journal"
            venues = [_fields(record).get(target_venue) for record in authoritative]
            if all(venues) and all(_equivalent(target_venue, venues[0], value) for value in venues[1:]):
                suggested_type = kind
                assessments.append(_assessment("entry_type", entry.get("entry_type"), kind, "correction",
                    "The identity-matched ACL publication record supplies this entry type; venue fields change together", authoritative))
                type_dependencies = ["entry_type", target_venue]
                if original.get(old_venue):
                    changes[old_venue] = None
                    type_dependencies.append(old_venue)
                    assessments.append(_assessment(old_venue, original[old_venue], None, "correction",
                        "Remove the incompatible venue field only together with the verified entry-type correction", authoritative))
                if old_venue == "journal" and entry.get("fields", {}).get("journaltitle"):
                    changes["journaltitle"] = None
                    type_dependencies.append("journaltitle")
                    assessments.append(_assessment("journaltitle", entry["fields"]["journaltitle"], None, "correction",
                        "Remove the incompatible BibLaTeX venue alias together with the entry-type correction", authoritative))
    source_conflict = len(kinds) > 1
    checked = set(_FIELDS[:3]) | {field for field in _FIELDS if field in original or any(_fields(record).get(field) for record in selected)}
    checked.add("doi")
    for field in _FIELDS:
        if field not in checked:
            continue
        original_value = original.get(field)  # Absent and explicitly empty remain distinct.
        if field in changes and changes[field] is None:
            continue
        available = [record for record in selected if _fields(record).get(field) not in (None, "")]
        # Official publisher bibliographic fields outrank aggregator exports for
        # the already-established same-version identity, not for title/author identity.
        official = [record for record in authoritative if _fields(record).get(field) not in (None, "")]
        if official and field in {"journal", "booktitle", "publisher", "editor", "month", "address", "url", "isbn", "issn", "series", "edition", "organization", "abstract", "keywords"}:
            available = official
        if not selected:
            reason = "No uniquely established same-version record is available for this field"
            if field == "doi" and doi_conflicts:
                assessments.append(_assessment(field, original_value, None, "unverified", "The supplied DOI points to conflicting title or author metadata; it is not used to replace this work", doi_conflicts))
            else:
                assessments.append(_assessment(field, original_value, None, "unverified", reason, []))
            continue
        if not available:
            if field == "pages" and original_value and any(_equivalent("eid", original_value, _fields(record).get("eid")) for record in selected if _fields(record).get("eid")):
                same_eid = [record for record in selected if _fields(record).get("eid") and _equivalent("eid", original_value, _fields(record)["eid"])]
                assessments.append(_assessment(field, original_value, None, "normalization", "This value matches the source's article number, not a page range; it is not treated as an error", same_eid))
            else:
                assessments.append(_assessment(field, original_value, None, "unverified", "The available source does not provide this field; absence is not evidence of an error", selected))
            continue
        proposed = _fields(available[0])[field]
        if any(not _equivalent(field, proposed, _fields(record)[field]) for record in available[1:]):
            source_conflict = True
            assessments.append(_assessment(field, original_value, None, "unverified", "Sources disagree on this field for the selected identity; no value is chosen", available))
            continue
        if field == "doi" and not valid_doi(proposed):
            assessments.append(_assessment(field, original_value, None, "unverified", "The source identifier is not a valid DOI-shaped identifier", available))
            continue
        if field == "doi" and not _doi_registration_sources(selected, proposed):
            assessments.append(_assessment(field, original_value, None, "unverified", "This is a sourced DOI candidate, but no matching online registration or resolution evidence verifies its ownership and version", available))
            continue
        if original_value is None or not str(original_value).strip():
            if field == "eid" and original.get("pages") and _equivalent("eid", original["pages"], proposed):
                assessments.append(_assessment(field, original_value, None, "not_applicable", "The article number is already represented in pages; no duplicate field is required", available))
                continue
            proposed = normalize_doi(proposed) if field == "doi" else proposed
            assessments.append(_assessment(field, original_value, proposed, "completion", "The selected same-version evidence supplies this missing field; absence alone was not an error", available))
            changes[field] = proposed
        elif _equivalent(field, original_value, proposed):
            if str(original_value) == str(proposed):
                judgment, reason = "consistent", "The supplied field agrees with the selected same-version evidence"
            else:
                judgment, reason = "normalization", "Equivalent formatting, protected text, or author initials; the original value is retained"
            assessments.append(_assessment(field, original_value, None, judgment, reason, available))
        elif field == "year" and _year(original_value) and _year(proposed) and abs(_year(original_value) - _year(proposed)) == 1:
            assessments.append(_assessment(field, original_value, None, "unverified", "A one-year difference can reflect online versus volume publication; the relevant publication date needs confirmation", available))
        elif field in {"journal", "booktitle"} and len(_text(original_value).split()) <= 2 < len(_text(proposed).split()):
            assessments.append(_assessment(field, original_value, None, "unverified", "This may be an abbreviated venue name; no unsupported alias expansion is applied", available))
        else:
            proposed = normalize_doi(proposed) if field == "doi" else proposed
            assessments.append(_assessment(field, original_value, proposed, "correction", "The uniquely established same-version evidence supplies a different value", available))
            changes[field] = proposed

    original_doi = original.get("doi")
    doi_sources = [record for record in selected if valid_doi(_fields(record).get("doi"))]
    if doi_conflicts or (original_doi and not valid_doi(original_doi)) or (original_doi and doi_sources and all(normalize_doi(original_doi) != normalize_doi(_fields(record)["doi"]) for record in doi_sources)):
        doi_state = "conflict"
    elif (doi_sources and len({normalize_doi(_fields(record)["doi"]) for record in doi_sources}) == 1
          and _doi_registration_sources(selected, _fields(doi_sources[0])["doi"])):
        doi_state = "verified"
    elif any(str(error.get("provider", "")).lower() in {"doi", "crossref", "datacite", "all"} or str(error.get("operation", "")).lower().startswith("doi") for error in errors):
        doi_state = "failed"
    elif original_doi:
        doi_state = "failed" if errors else "unverified" if candidates else "not_found"
    else:
        doi_state = "missing" if doi_sources or identity_state in {"insufficient", "multiple"} else "not_found"
    if source_conflict or doi_state == "conflict" or any(item["judgment"] == "correction" for item in assessments):
        field_state = "conflict"
    elif any(item["judgment"] == "unverified" for item in assessments):
        field_state = "partial"
    elif any(item["judgment"] == "completion" for item in assessments):
        field_state = "incomplete"
    else:
        field_state = "consistent"
    try:
        suggested_bibtex = render_suggestion(entry, changes, suggested_type)
    except BibTeXError as error:
        suggested_bibtex = None
        issues.append(str(error))
    result = {
        "identity_state": identity_state, "field_state": field_state, "doi_state": doi_state,
        "publication_state": publication_state, "assessments": assessments, "candidates": candidates,
        "evidence": evidence, "suggested_bibtex": suggested_bibtex, "issues": list(dict.fromkeys(issues)),
        "mock": mock, "rule_version": RULE_VERSION,
        "accepted_changes": changes, "suggested_entry_type": suggested_type, "type_dependencies": type_dependencies,
    }
    if publication_suggestion:
        result["publication_suggestion"] = publication_suggestion
    return result
