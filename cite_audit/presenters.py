"""Public DTOs; internal storage shapes are not exposed as accidental APIs."""
from copy import deepcopy


def source(data):
    if data is None:
        return None
    item = deepcopy(data)
    mock = item.get("namespace") == "mock" or item.get("status") == "mock"
    return {
        "id": item["id"], "provider": item["provider"], "record_id": item["record_id"],
        "object_type": item.get("object_type", "publication"), "version_id": item.get("version_id"),
        "attribution_status": item.get("assignment_status", "unresolved"),
        "source_type": "demo" if mock else item.get("source_type", "aggregator"),
        "mode": "demo" if mock else "online", "url": item.get("source_url"),
        "fetched_at": item.get("fetched_at"), "checked_at": item.get("checked_at", item.get("fetched_at")),
        "status": "demo" if mock else "stale" if item.get("stale") else "available" if item.get("status") == "verified" else "manual_unverified",
        "snapshot_id": item["snapshot_id"], "snapshot_sha256": item.get("snapshot_hash"),
        "fields": item.get("fields", {}), "field_coverage": list(item.get("fields", {})),
        "extraction_method": item.get("extraction_method", "fixture" if mock else "provider_adapter"),
        "snapshot_url": f"/api/snapshots/{item['snapshot_id']}",
        "warnings": ["Demonstration fixture, not online evidence"] if mock else [],
    }


def refs(items, field=""):
    return [{**i, "field": field or i.get("field", ""), "locator": i.get("field_path", i.get("locator", ""))}
            for i in items]


def relation(item):
    return {**item, "relation_type": item.get("relation", item.get("relation_type")), "status": "confirmed",
            "reason": "Explicit source-backed publication relation",
            "evidence": [{"source_content_id": item.get("source_id"), "url": item.get("evidence_url")}]}


def version(data):
    if data is None:
        return None
    item = deepcopy(data)
    selections = item["field_selections"]
    item["field_selections"] = [dict(field=k, **v, alternatives=v.get("evidence", []) if v["status"] == "conflicting" else [])
                                for k, v in selections.items()]
    for selection in item["field_selections"]:
        selection["evidence"] = refs(selection["evidence"], selection["field"])
    item["conflicts"] = [{"field": k, "values": [{"value": e["value"], "evidence": refs([e], k)}
                                                 for e in v["evidence"]], "reason": v["reason"]}
                         for k, v in selections.items() if v["status"] == "conflicting"]
    item["sources"] = [source(s) for s in item["sources"]]
    item["source_count"] = len(item["sources"])
    item["status"] = "confirmed"
    item["mode"] = "demo" if item.pop("namespace") == "mock" else "online"
    item["relations"] = [relation(r) for r in item.get("relations", [])]
    return item


def article(data, detail=True):
    if data is None:
        return None
    item = deepcopy(data)
    rec = item.pop("recommendation")
    item["mode"] = "demo" if item.pop("namespace") == "mock" else "online"
    item["recommendation_reason"] = rec["reason"]
    item["recommendation_revision"] = rec["revision"]
    item["updated_at"] = max((v["updated_at"] for v in item["versions"]), default=item["created_at"])
    item.pop("recommended_version", None)
    if detail:
        relations = {r["id"]: relation(r) for v in item["versions"] for r in v["relations"]}
        item["version_relations"] = list(relations.values())
        item["versions"] = [version(v) for v in item["versions"]]
    else:
        item.pop("versions", None)
    return item


def assessment(data):
    item = deepcopy(data)
    item["current_version"] = version(item.get("current_version"))
    item["recommended_version"] = version(item.get("recommended_version"))
    item["sources"] = [source(s) for s in item.get("sources", [])]
    item["version_relations"] = list({r["id"]: relation(r) for r in item.get("version_relations", [])}.values())
    return item
