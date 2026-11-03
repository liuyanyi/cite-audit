"""Read-only result groups, separate from execution and field judgments."""
import re

PATENT_NUMBER_PATTERN = r"(?i)\ACN\d{8,}[A-Z]\d?\Z"

SUPPORTED_SQL = (
    f"(casefold(entry_type) != 'patent' AND NOT regexp('{PATENT_NUMBER_PATTERN}',"
    "json_extract(input_fields,'$.edition')) AND NOT EXISTS(SELECT 1 FROM "
    "json_each(result,'$.calls') WHERE json_extract(value,'$.provider')='coverage' AND "
    "(json_extract(value,'$.status')='unsupported' OR json_extract(value,'$.error_code')='coverage_unavailable')))"
)

GROUPS = ("version_confirmed", "wrong_paper", "not_found", "unverifiable", "source_failed", "budget_exhausted")

CLASSIFICATION_SQL = (
    "CASE WHEN status IN ('queued','running') THEN NULL "
    f"WHEN status IN ('failed','cancelled') OR NOT {SUPPORTED_SQL} THEN 'unverifiable' "
    "WHEN json_extract(result,'$.identity_state')='wrong_paper' THEN 'wrong_paper' "
    "WHEN json_extract(result,'$.identity_state')='confirmed' AND "
    "json_extract(result,'$.version_status')='current_version_confirmed' THEN 'version_confirmed' "
    "WHEN json_extract(result,'$.identity_state')='source_failed' THEN CASE WHEN EXISTS "
    "(SELECT 1 FROM json_each(result,'$.calls') WHERE json_extract(value,'$.status') "
    "IN ('budget_exhausted','budget_exceeded')) THEN 'budget_exhausted' ELSE 'source_failed' END "
    "WHEN json_extract(result,'$.identity_state')='not_found' THEN 'not_found' ELSE 'unverifiable' END"
)


def verification_supported(entry_type, fields, result):
    return not (
        entry_type.casefold() == "patent"
        or re.fullmatch(PATENT_NUMBER_PATTERN, fields.get("edition", ""))
        or any(call.get("provider") == "coverage" and (
            call.get("status") == "unsupported" or call.get("error_code") == "coverage_unavailable")
            for call in result.get("calls", []))
    )


def classification(entry, calls):
    if entry["execution_status"] in {"queued", "running"}:
        return None
    if entry["execution_status"] in {"failed", "cancelled"} or not entry["verification_supported"]:
        return "unverifiable"
    if entry["identity_state"] == "wrong_paper":
        return "wrong_paper"
    if entry["identity_state"] == "confirmed" and entry["version_status"] == "current_version_confirmed":
        return "version_confirmed"
    if entry["identity_state"] == "source_failed":
        return "budget_exhausted" if any(c.get("status") in {"budget_exhausted", "budget_exceeded"} for c in calls) else "source_failed"
    return "not_found" if entry["identity_state"] == "not_found" else "unverifiable"


def summary_expressions():
    conflict = "COALESCE(json_extract(result,'$.has_conflict'),0)"
    unresolved = "COALESCE(json_extract(result,'$.identity_state'),'insufficient') != 'confirmed'"
    parse_error = "EXISTS(SELECT 1 FROM json_each(diagnostics) WHERE json_extract(value,'$.severity')='error')"
    counts = {status: f"status='{status}'" for status in ("running", "queued", "failed", "cancelled")}
    counts.update({"completed": "status IN ('completed','partial')", "parse_errors": parse_error,
                   "confirmed": f"NOT ({unresolved})", "conflicts": conflict, "unresolved": unresolved,
                   "upgrade_available": "json_extract(result,'$.recommendation_status')='upgrade_available'",
                   "completion_available": "json_extract(result,'$.completion_count')>0",
                   "problem_entries": f"({conflict}) OR ({unresolved}) OR ({parse_error})"})
    counts.update({key: f"({CLASSIFICATION_SQL})='{key}'"
                   for key in GROUPS})
    counts["unprocessed"] = "status NOT IN ('completed','partial')"
    return {"total": "1", **counts}
