"""Create a task-local, frozen assessment from source-backed versions."""
from __future__ import annotations

import re

from .fields import AUDIT_BIB_FIELDS, FIELD_RULE_VERSION, audit_items, auxiliary_fields, comparison_fields
from .journals import issns
from .knowledge import (
    RULE_VERSION,
    Knowledge,
    arxiv_family,
    authors,
    field_normalize,
    identifiers,
    identity_compatible,
    model_repository_match,
    normalize,
    normalize_id,
)
from .matching import author_match, iclr_paper_id, openreview_id, relevance, venue_normalize
from .storage import uid

BIB_FIELDS = AUDIT_BIB_FIELDS


def evidence(items: list[dict], field: str) -> list[dict]:
    return [{**item, "field": field, "locator": item.get("field_path", f"fields.{field}")} for item in items]


def brief_bib(key: str, fields: dict) -> str:
    from .bib import serialize_entry
    try:
        return serialize_entry(key, fields.get("entrytype", "misc"),
                               {k: v for k, v in fields.items() if k != "entrytype" and v is not None})
    except ValueError:
        return ""


def arxiv_match(fields: dict, candidate: str) -> tuple[bool, str]:
    supplied = normalize_id("arxiv", fields["eprint"])
    base = re.sub(r"v\d+$", "", supplied)
    target = normalize_id("arxiv", candidate)
    if re.sub(r"v\d+$", "", target) != base:
        return False, "arXiv 基础编号不同"
    revisions = {supplied[len(base):]} - {""}
    version = str(fields.get("version", "")).strip().lower()
    if re.fullmatch(r"v?\d+", version):
        revisions.add("v" + version.removeprefix("v"))
    url = str(fields.get("url", ""))
    if re.match(r"^https?://arxiv\.org/(abs|pdf)/", url, re.IGNORECASE):
        linked = normalize_id("arxiv", url.split("?", 1)[0].split("#", 1)[0])
        if re.sub(r"v\d+$", "", linked) != base:
            return False, "输入 URL 与 eprint 的 arXiv 基础编号不同"
        if linked != base:
            revisions.add(linked[len(base):])
    if len(revisions) > 1:
        return False, "输入字段指定了不同的 arXiv 修订"
    if revisions and target[len(base):] not in revisions:
        return False, "来源未核实输入指定的 arXiv 修订"
    return True, "arXiv 基础编号一致，来源与输入指定的修订相容"


def current_match(input_fields: dict, version: dict) -> tuple[bool, str]:
    if any(c["field"] in {"title", "author", "year", "journal", "booktitle", "pages", "url"} for c in version.get("conflicts", [])):
        latest = {(s["provider"], s["record_id"]): s for s in version["sources"]}
        for source in latest.values():
            valid, reason = current_match(input_fields, {"fields": source["fields"], "identifiers": identifiers(source)})
            if valid:
                return True, f"{reason}；依据 {source['provider']} 的独立记录，来源间字段分歧另列"
        return False, "来源字段存在分歧，单条来源记录不足以确认当前版本"
    input_fields = comparison_fields(input_fields)
    vf, vi = comparison_fields(version["fields"]), version["identifiers"]
    if not input_fields.get("eprint") and model_repository_match(input_fields, vf):
        return True, "DOI、模型仓库地址、发布平台和年份一致，资源身份已确认"
    input_ids = identifiers({"fields": input_fields})
    url = input_fields.get("url", "").rstrip("/")
    same_doi = bool(input_ids.get("doi") and input_ids["doi"] == vi.get("doi"))
    same_url = bool(url and url == vf.get("url", "").rstrip("/") and not input_ids.get("doi"))
    if (same_doi and not input_ids.get("arxiv") and url and url == vf.get("url", "").rstrip("/")
            and all(input_fields.get(k) and field_normalize(k, input_fields[k]) == field_normalize(k, vf.get(k))
                    for k in ("title", "year", "booktitle", "pages"))
            and (roster := authors(input_fields.get("author", ""))) and roster == authors(vf.get("author", ""))):
        return True, "DOI、原文链接、题名、年份、会场、页码和有序作者姓氏一致；姓名写法单独核验"
    if (not input_ids.get("arxiv") and (same_doi or same_url)
            and input_fields.get("year") and input_fields["year"] == vf.get("year")
            and author_match(input_fields.get("author", ""), vf.get("author", "")) == "full"
            and any(input_fields.get(k) and vf.get(k) and venue_normalize(input_fields[k]) == venue_normalize(vf[k])
                    for k in ("journal", "booktitle"))):
        return True, "标识、完整有序作者、年份和出版来源一致，版本身份已确认"
    if not identity_compatible(input_fields, vf):
        return False, "Input title/author contradict this publication identifier"
    if input_ids.get("arxiv"):
        return arxiv_match(input_fields, vi.get("arxiv", ""))
    if input_ids.get("doi") and vi.get("doi") == input_ids["doi"]:
        return True, "DOI resolved to a compatible title/author identity"
    forum = openreview_id(input_fields.get("url", ""))
    if (forum and forum == vi.get("openreview") and not input_ids.get("doi")
            and normalize(input_fields.get("title")) == normalize(vf.get("title"))):
        for field in ("year", "booktitle", "journal"):
            if (input_fields.get(field) and vf.get(field)
                    and field_normalize(field, input_fields[field]) != field_normalize(field, vf[field])):
                return False, "OpenReview forum 指向的发表记录与输入年份或会场不符"
        return True, "OpenReview forum and accepted publication identity agree"
    # Input identifiers are not truth. When DOI is wrong, a separately retrieved
    # record may still match by the full publication tuple; title alone cannot.
    title_ok = bool(normalize(input_fields.get("title"))) and normalize(input_fields.get("title")) == normalize(vf.get("title"))
    author_ok = author_match(input_fields.get("author", ""), vf.get("author", "")) in {"full", "order_variant"}
    year_ok = bool(input_fields.get("year")) and input_fields.get("year") == vf.get("year")
    venue = input_fields.get("booktitle") or input_fields.get("journal")
    target_venue = vf.get("booktitle") or vf.get("journal")
    venue_ok = bool(venue and target_venue) and venue_normalize(venue) == venue_normalize(target_venue)
    paper = iclr_paper_id(input_fields.get("url", ""))
    if (paper and paper == vi.get("iclr") and title_ok and year_ok and venue_ok
            and author_match(input_fields.get("author", ""), vf.get("author", "")) in {"full", "order_variant", "partial"}):
        return True, "ICLR 官方论文集标识、题名、作者、年份与会场相符"
    pages_ok = bool(input_fields.get("pages")) and field_normalize("pages", input_fields["pages"]) == field_normalize("pages", vf.get("pages"))
    if (vi.get("cnki") and not input_ids and title_ok and pages_ok and venue_ok
            and input_fields.get("journal") and vf.get("journal")
            and author_match(input_fields.get("author", ""), vf.get("author", "")) == "full"
            and set(issns(input_fields.get("issn"))) & set(issns(vf.get("issn")))
            and all(not input_fields.get(k) or input_fields[k] == vf.get(k)
                    for k in ("year", "volume", "number"))
            and input_fields.get("entrytype", "article") == vf.get("entrytype") == "article"):
        return True, "知网期刊题录的题名、完整有序作者、刊名、ISSN 和页码均与输入一致"
    if title_ok and author_ok and year_ok and (venue_ok or pages_ok):
        return True, "Title, ordered authors, year and publication venue/pages jointly agree"
    missing = [label for valid, label in [(title_ok, "题名"), (author_ok, "完整有序作者"),
               (year_ok, "年份"), (venue_ok or pages_ok, "会刊或页码")] if not valid]
    return False, "出版身份尚未确认：" + "、".join(missing) + "不一致或缺少证据"


def assess(knowledge: Knowledge, entry: dict, source_records: list[dict], mode: str, calls: list[dict],
           journal_checks: list[dict] | None = None) -> dict:
    original = dict(entry["fields"])
    original["entrytype"] = entry["entry_type"]
    unsupported = original["entrytype"].casefold() == "patent" or bool(
        re.fullmatch(r"CN\d{8,}[A-Z]\d?", original.get("edition", ""), re.IGNORECASE))
    if unsupported:
        source_records = []
    versions = {}
    candidates = []
    excluded = []
    wrong_targets = {}
    for source in source_records:
        if source.get("version_id"):
            version = knowledge.version(source["version_id"])
            versions[version["id"]] = version
            comparison = relevance(original, source)
            if comparison["status"] != "rejected" or comparison["author_match"] in {"full", "order_variant"}:
                continue
            ids = identifiers(source)
            url = original.get("url", "").rstrip("/")
            if original.get("doi") and normalize_id("doi", original["doi"]) == ids.get("doi"):
                wrong_targets.setdefault("doi", []).append(source)
            if url and (url in {source.get("source_url"), source["fields"].get("url")}
                        or normalize_id("doi", url) == ids.get("doi")
                        or openreview_id(url) and openreview_id(url) == ids.get("openreview")
                        or iclr_paper_id(url) and iclr_paper_id(url) == ids.get("iclr")
                        or re.match(r"^https?://arxiv\.org/(?:abs|pdf)/", url, re.IGNORECASE)
                        and normalize_id("arxiv", url) == ids.get("arxiv")):
                wrong_targets.setdefault("url", []).append(source)
        else:
            comparison = relevance(original, source)
            item = {"title": source.get("fields", {}).get("title", "Untitled candidate"),
                               "provider": source["provider"], "record_id": source["record_id"],
                               "url": source.get("source_url"), "source_content_id": source["id"],
                               "snapshot_id": source["snapshot_id"], "identifiers": source.get("identifiers", {}),
                               **comparison}
            if source.get("publication_state") == "submission":
                item["reason"] += "；OpenReview 有投稿记录，未确认正式录用"
            (excluded if comparison["status"] == "rejected" else candidates).append(item)
    matches = [(v, reason) for v in versions.values() for valid, reason in [current_match(original, v)] if valid]
    failed_calls = [c for c in calls if c.get("status") in {"failed", "rate_limited", "budget_exhausted", "budget_exceeded", "partial"}
                    and c.get("error_code") != "coverage_unavailable"]
    queried = {c["provider"] for c in calls if c.get("request_count") or c.get("cache_hit")}
    completed_sources = {c["provider"] for c in calls if c.get("operation") == "result"
                         and c.get("status") in {"success", "no_result"} and c["provider"] in queried}
    budget_hit = any(c.get("status") in {"budget_exhausted", "budget_exceeded"} for c in failed_calls)
    current = matches[0][0] if len(matches) == 1 else None
    if current:
        wrong_targets = {field: [s for s in items if versions[s["version_id"]]["article_id"] != current["article_id"]]
                         for field, items in wrong_targets.items()}
        wrong_targets = {field: items for field, items in wrong_targets.items() if items}
    if wrong_targets:
        current = None
    for version in versions.values():
        if current and version["id"] == current["id"]:
            continue
        comparison = relevance(original, version)
        _valid, match_reason = current_match(original, version)
        source = version["sources"][0]
        item = {"title": version["fields"].get("title", "Untitled"),
                "provider": source["provider"], "record_id": source["record_id"],
                "url": source.get("source_url"), "source_content_id": source["id"],
                "snapshot_id": source["snapshot_id"], "identifiers": version["identifiers"], "version_id": version["id"],
                "article_id": version["article_id"],
                **comparison}
        if comparison["status"] != "rejected":
            item["reason"] = match_reason
        (excluded if comparison["status"] == "rejected" else candidates).append(item)
    # Group citations for display without transferring fields between versions.
    articles_by_id = {}
    for version in versions.values():
        for kind, value in version["identifiers"].items():
            articles_by_id.setdefault((kind, value), set()).add(version["article_id"])
        family = arxiv_family(version["identifiers"])
        if family:
            articles_by_id.setdefault(("arxiv_family", family), set()).add(version["article_id"])
    grouped = {}
    for candidate in candidates:
        ids = candidate["identifiers"]
        family = arxiv_family(ids)
        related = set().union(*(articles_by_id.get(pair, set()) for pair in ids.items()))
        if family:
            related.update(articles_by_id.get(("arxiv_family", family), set()))
        if candidate.get("article_id") or len(related) == 1:
            key = ("article", candidate.get("article_id") or next(iter(related)))
        elif family:
            key = ("arxiv", family)
        elif ids.get("doi"):
            key = ("doi", ids["doi"])
        else:
            key = (candidate["provider"], candidate["record_id"])
        if current and any(ids.get(k) == v for k, v in current["identifiers"].items()) and not ids.get("arxiv"):
            continue
        if key not in grouped:
            grouped[key] = {**candidate, "records": []}
        elif candidate.get("version_id"):
            grouped[key].update({k: v for k, v in candidate.items() if k != "records"})
        grouped[key]["records"].append(candidate)
    candidates = list(grouped.values())
    if unsupported:
        identity_state, reason = "insufficient", "当前来源不支持专利身份核验；需要专利官方来源，不能用论文搜索结果替代"
    elif wrong_targets:
        identity_state = "wrong_paper"
        reason = "；".join(f"{field.upper()} 指向另一篇论文《{items[0]['fields']['title']}》"
                          for field, items in wrong_targets.items())
    elif current:
        identity_state, reason = "confirmed", matches[0][1]
    elif len(matches) > 1:
        identity_state, reason = "ambiguous", "More than one publication record matches; no automatic replacement"
    elif failed_calls and not candidates and (budget_hit or not completed_sources):
        identity_state = "source_failed"
        reason = "请求预算耗尽，尚未确认文献身份" if budget_hit else "来源访问失败，尚未确认文献身份"
    elif candidates:
        identity_state, reason = "insufficient", "存在相关候选，但出版版本证据不足；不是多个已匹配身份之间的歧义"
    elif any(c.get("provider") == "coverage" and c.get("status") == "unsupported" for c in calls):
        identity_state, reason = "insufficient", "当前来源不支持此文献类型的身份核验（例如专利）；不能据此判断文献不存在"
    elif not original.get("title") and not (original.get("doi") or original.get("eprint")):
        identity_state, reason = "insufficient", "No usable title or identifier in this entry"
    elif any(c.get("provider") == "discovery" for c in calls) and not any(
        c.get("request_count") or c.get("cache_hit") for c in calls if c.get("provider") != "knowledge"
    ):
        identity_state, reason = "insufficient", "没有适用的已启用来源执行查询，无法核验该条目"
    else:
        identity_state, reason = "not_found", "已完成查询的来源未找到匹配记录，无法据此判断文献不存在"
    if mode == "demo":
        reason += "; demonstration fixture only, not online verification"
    article = knowledge.article(current["article_id"]) if current else None
    recommended = article["recommended_version"] if article else None
    rec_status = ("same_version" if recommended and recommended["id"] == current["id"] else
                  "upgrade_available" if recommended else "unavailable")
    assessments = []
    arxiv_sources = {s["id"] for s in current["sources"] if s["provider"] == "arxiv"} if current else set()
    for field, selection, metadata in audit_items(original, current):
        before = original.get(field)
        after = selection.get("value")
        refs = evidence(selection.get("evidence", []), field)
        if metadata["input_conflict"]:
            judgment, field_reason = "unverifiable", "同一语义的输入字段相互冲突；请先审阅原值，不自动选择或覆盖"
        elif not metadata["applicable"]:
            judgment, field_reason = "not_applicable", "不适用于当前文献类型；保留输入，不自动补全或改写"
        elif (field == "title" and current and model_repository_match(original, current["fields"])
              and selection.get("status") == "conflicting"
              and len(registered := {r["value"] for r in refs if r.get("source_url", "").startswith("https://api.datacite.org/dois/")}) == 1):
            after = registered.pop()
            judgment = "consistent" if field_normalize(field, before) == field_normalize(field, after) else "conflict"
            field_reason = "按 DOI 登记题名核验；模型卡的推荐引用题名不同，两处来源均予保留"
        elif not current or selection.get("status") == "conflicting":
            judgment = "unverifiable"
            field_reason = ("Current publication is not confirmed" if not current else "Sources conflict within this version")
        elif before is None and after is None:
            judgment, field_reason = "not_applicable", "No value supplied or evidenced for this version"
        elif after is None:
            judgment, field_reason = "unverifiable", "No source evidence for this field; original retained"
        elif before is None:
            judgment, field_reason = "completion", "Missing value is supplied by current-version evidence"
        elif field == "entrytype" and after == "article" and before != after and refs and all(
            ref["source_content_id"] in arxiv_sources for ref in refs
        ):
            judgment, field_reason = "unverifiable", "arXiv 来源的默认 article 不足以确定 Bib 条目类型"
        elif field == "eprint" and current["identifiers"].get("arxiv") and arxiv_match(original, after)[0]:
            judgment, field_reason = "consistent", "arXiv 基础编号及修订相容"
        elif (field == "url" and openreview_id(before) and openreview_id(after)
              and openreview_id(before) != openreview_id(after)
              and any(v["article_id"] == current["article_id"] and v["identifiers"].get("openreview") == openreview_id(before)
                      for v in versions.values())):
            judgment, field_reason = "conflict", "原 URL 指向同一论文的另一发表版本；建议改为当前会场的 OpenReview forum"
        elif str(before) == str(after):
            judgment, field_reason = "consistent", "Matches the current publication version"
        elif field_normalize(metadata["comparison_field"], before) == field_normalize(metadata["comparison_field"], after):
            judgment, field_reason = "normalization", "Equivalent punctuation, escaping or letter case"
        elif field == "journal" and journal_checks and all(c.get("equivalent") for c in journal_checks):
            judgment, field_reason = "normalization", "ISSN 二次核查确认同一期刊，名称为有依据的等价变体"
        elif field == "author" and author_match(before, after) == "full":
            judgment, field_reason = "normalization", "完整姓名及作者顺序一致，仅姓/名顺序或拼写分隔不同"
        elif field == "author" and author_match(before, after) == "order_variant":
            judgment, field_reason = "unverifiable", "多位作者完整姓名及署名顺序相符，但来源可能倒置姓/名字段；保留原姓名，不自动改写逗号格式"
        elif field == "author" and (author_match(before, after) == "partial" or authors(before) == authors(after)):
            judgment, field_reason = "unverifiable", "Author surnames/order agree but abbreviation cannot prove full-name equality"
        else:
            judgment, field_reason = "conflict", "Differs from the confirmed current publication record"
        if field in wrong_targets:
            judgment, field_reason = "unverifiable", reason
            refs = [{"source_content_id": s["id"], "snapshot_id": s["snapshot_id"], "field": field,
                     "locator": "fields.title"} for s in wrong_targets[field]]
        assessments.append({"field": field, "original_value": before, "reference_value": after,
                            "judgment": judgment, "reason": field_reason, "evidence": refs, **metadata})
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
    patch = {name: a["reference_value"] for a in assessments if a["judgment"] in {
        "conflict", "completion", "normalization"} and a["reference_value"] is not None
        for name in (a["input_fields"] or {a["field"]: None})}
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
    used_sources.update({s["id"]: s for items in wrong_targets.values() for s in items})
    return {
        "identity_state": identity_state, "identity_reason": reason,
        "version_status": "current_version_confirmed" if current else "candidate" if candidates else "unknown",
        "recommendation_status": rec_status, "article_id": current["article_id"] if current else None,
        "current_version_id": current["id"] if current else None,
        "recommended_version_id": recommended["id"] if recommended else None,
        "field_assessments": assessments,
        "auxiliary_fields": auxiliary_fields(original),
        "recommendation": {"status": rec_status, "version_id": recommended["id"] if recommended else None,
                           "reason": article["recommendation"]["reason"] if article else "Current publication not confirmed",
                           "revision": article["recommendation"]["revision"] if article else 0,
                           "differences": differences},
        "current_version": current, "recommended_version": recommended,
        "version_relations": [r for v in article["versions"] for r in v["relations"]] if article else [],
        "proposals": proposals, "sources": list(used_sources.values()), "candidates": candidates,
        "excluded_candidates": excluded, "calls": calls, "journal_checks": journal_checks or [],
        "warnings": (["Demonstration data — no online verification"] if mode == "demo" else [])
                    + ([("请求预算耗尽，发现范围受限。" if budget_hit else
                         "部分来源未完成查询，发现范围受限：" + "、".join(sorted({c["provider"] for c in failed_calls})))]
                       if failed_calls and (current or completed_sources or candidates) else []),
        "evidence_revision": {"merge_revision": current["merge_revision"] if current else None,
                              "field_rule_version": FIELD_RULE_VERSION,
                              "recommendation_revision": article["recommendation"]["revision"] if article else None,
                              "rule_version": RULE_VERSION},
        "has_conflict": any(a["judgment"] == "conflict" for a in assessments),
        "conflict_count": sum(a["judgment"] == "conflict" for a in assessments),
        "completion_count": sum(a["judgment"] == "completion" for a in assessments),
    }
