"""Versioned semantic audit vocabulary; never rewrites uploaded/source fields."""
from dataclasses import dataclass

FIELD_RULE_VERSION = "semantic-fields-v2"


@dataclass(frozen=True)
class AuditField:
    key: str
    label: str
    aliases: tuple[str, ...]
    types: tuple[str, ...] = ()


# Subfields retain distinct meanings: a year is not a full date, an article
# number is not a page range, and a journal is not a proceedings title.
FIELDS = (
    AuditField("entry_type", "文献类型", ("entrytype",)),
    AuditField("title", "标题", ("title",)),
    AuditField("authors", "作者", ("author",)),
    AuditField("editors", "编者", ("editor",), ("book", "inbook", "incollection", "proceedings", "inproceedings")),
    AuditField("publication_year", "出版年份", ("year",)),
    AuditField("publication_date", "出版日期", ("date",)),
    AuditField("first_online_date", "在线首发日期", ("first_online_date",)),
    AuditField("first_online_year", "在线首发年份", ("first_online_year",)),
    AuditField("doi", "DOI", ("doi",)),
    AuditField("journal", "期刊", ("journal", "journaltitle"), ("article",)),
    AuditField("proceedings", "论文集 / 收录书名", ("booktitle",), ("inproceedings", "conference", "incollection", "inbook")),
    AuditField("volume", "卷", ("volume",), ("article", "book", "inbook", "incollection", "inproceedings", "proceedings")),
    AuditField("issue", "期", ("number",), ("article",)),
    AuditField("pages", "页码", ("pages",), ("article", "inproceedings", "conference", "inbook", "incollection")),
    AuditField("article_number", "文章号", ("articleno", "eid", "article-number"), ("article", "inproceedings")),
    AuditField("publisher", "出版社", ("publisher",), ("article", "book", "inbook", "incollection", "inproceedings", "proceedings")),
    AuditField("edition", "版次", ("edition",), ("book", "inbook", "incollection")),
    AuditField("institution", "报告机构", ("institution",), ("techreport", "report")),
    AuditField("school", "学位授予机构", ("school",), ("phdthesis", "mastersthesis", "thesis")),
    AuditField("eprint", "预印本 / 仓储标识", ("eprint",)),
    AuditField("eprint_namespace", "仓储命名空间", ("archiveprefix", "eprinttype")),
    AuditField("eprint_class", "仓储分类", ("primaryclass", "eprintclass")),
    AuditField("url", "引用地址", ("url",)),
)
AUDIT_BIB_FIELDS = tuple(dict.fromkeys(name for spec in FIELDS for name in spec.aliases))


def specifications(entry_type: str):
    for spec in FIELDS:
        if spec.key == "issue" and entry_type in {"report", "techreport"}:
            yield AuditField("report_number", "报告编号", ("number",), ("report", "techreport"))
        else:
            yield spec


def comparison_fields(fields: dict) -> dict:
    """Only unambiguous aliases may contribute to candidate identification."""
    from .knowledge import field_normalize
    result = dict(fields)
    for spec in FIELDS:
        values = [fields[k] for k in spec.aliases if fields.get(k)]
        if values and len({field_normalize(spec.aliases[0], v) for v in values}) == 1:
            result[spec.aliases[0]] = values[0]
        elif values:
            for name in spec.aliases:
                result.pop(name, None)
    return result


def audit_items(original: dict, current: dict | None):
    """One assessment per meaning, retaining input aliases and source locators."""
    from .knowledge import field_normalize
    entry_type = (current or {}).get("fields", {}).get("entrytype", original["entrytype"]).lower()
    selections = (current or {}).get("field_selections", {})
    for spec in specifications(entry_type):
        names = [name for name in spec.aliases if name in original]
        source = [selections[name] for name in spec.aliases if name in selections]
        applicable = not spec.types or entry_type in spec.types
        if (current or {}).get("identifiers", {}).get("arxiv") and spec.key in {
            "journal", "proceedings", "volume", "issue", "pages", "article_number", "publisher",
        }:
            applicable = False
        # Optional absent fields are not missing requirements.
        if not names and not source:
            continue
        field = names[0] if names else spec.aliases[0]
        values = [original[name] for name in names]
        source_values = [s["value"] for s in source if s.get("value") is not None]
        input_conflict = len({field_normalize(spec.aliases[0], v) for v in values}) > 1
        source_conflict = (any(s.get("status") == "conflicting" for s in source)
                           or len({field_normalize(spec.aliases[0], v) for v in source_values}) > 1)
        selection = {"value": source_values[0] if source_values and not source_conflict else None,
                     "status": "conflicting" if source_conflict else "selected",
                     "evidence": [e for s in source for e in s.get("evidence", [])]}
        yield field, selection, {
            "semantic_key": spec.key, "label": spec.label,
            "comparison_field": spec.aliases[0],
            "input_fields": {name: original[name] for name in names},
            "applicable": applicable, "input_conflict": input_conflict,
        }


def auxiliary_fields(original: dict) -> list[dict]:
    return [{"field": name, "value": value, "reason": "不在本版语义核查范围内；保留原始信息，不代表已确认"}
            for name, value in original.items() if name not in AUDIT_BIB_FIELDS]
