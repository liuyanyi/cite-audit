"""Conservative publication identity and same-version field integration.

No task input enters this module. Only retrieved source records can become facts.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime

from .storage import Store, dump, load, now, uid

RULE_VERSION = "publication-identity-v1"
STRONG_IDS = ("doi", "arxiv", "acl", "pmlr")
ALLOWED_RELATIONS = {"preprint_of", "published_as", "same_work_version"}


def normalize(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = re.sub(r"\\[a-zA-Z]+", "", text)
    return re.sub(r"[^\w+#]", "", text)


def field_normalize(field: str, value: str | None) -> str:
    """Compare semantics without deleting meaningful identifier/range punctuation."""
    text = str(value or "").strip()
    if field in {"doi", "eprint"}:
        return normalize_id("arxiv" if field == "eprint" else field, text)
    if field in {"pages", "volume", "number", "articleno", "year"}:
        return re.sub(r"[-–—]+", "-", re.sub(r"\s+", "", text)).casefold()
    if field == "url":
        return text.rstrip("/")
    return normalize(text)


def normalize_id(kind: str, value: str) -> str:
    value = str(value).strip().casefold()
    if kind == "doi":
        value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", value)
    if kind == "arxiv":
        value = re.sub(r"^(?:https?://arxiv\.org/(?:abs|pdf)/|arxiv\s*:\s*)", "", value)
        value = re.sub(r"\.pdf$", "", value)
        # Explicit preprint revisions retain identity; base lookup handled separately.
    return value.rstrip("/")


def identifiers(record: dict) -> dict:
    ids = dict(record.get("identifiers", {}))
    for key in ("doi", "eprint"):
        if record.get("fields", {}).get(key):
            ids.setdefault("arxiv" if key == "eprint" else key, record["fields"][key])
    return {key: normalize_id(key, value) for key, value in ids.items()
            if key in STRONG_IDS and value}


def authors(value: str) -> list[str]:
    names = re.split(r"\s+and\s+", value or "", flags=re.IGNORECASE)
    return [normalize(name.split(",")[0] if "," in name else name.split()[-1])
            for name in names if name.strip() and normalize(name) not in {"others", "etal"}]


def identity_compatible(left: dict, right: dict) -> bool:
    """Title/author corroboration prevents a wrong DOI binding from spreading."""
    lt, rt = normalize(left.get("title")), normalize(right.get("title"))
    if lt and rt and lt != rt:
        return False
    la, ra = authors(left.get("author", "")), authors(right.get("author", ""))
    return not (la and ra and la[0] != ra[0])


def same_ids(left: dict, right: dict) -> bool:
    # A preprint and its formal DOI must not collapse merely because an aggregator
    # repeats a DOI. Presence of different arXiv identities is also disqualifying.
    if bool(left.get("arxiv")) != bool(right.get("arxiv")):
        return False
    if left.get("arxiv") and right.get("arxiv") and left["arxiv"] != right["arxiv"]:
        return False
    if any(left.get(k) and right.get(k) and left[k] != right[k] for k in STRONG_IDS):
        return False
    return any(left.get(k) and left.get(k) == right.get(k) for k in STRONG_IDS)


def publication_tuple_matches(left: dict, right: dict) -> bool:
    """A complete publication tuple can corroborate disjoint official IDs."""
    required = ("title", "author", "year", "pages")
    if not all(left.get(k) and right.get(k) and field_normalize(k, left[k]) == field_normalize(k, right[k])
               for k in required):
        return False
    venue = "booktitle" if left.get("booktitle") else "journal"
    return bool(left.get(venue) and right.get(venue) and normalize(left[venue]) == normalize(right[venue]))


def publication_type(record: dict) -> str:
    f = record.get("fields", {})
    if (identifiers(record).get("arxiv") or f.get("archiveprefix", "").casefold() == "arxiv"
            or f.get("publication_type") == "preprint"):
        return "preprint"
    if f.get("entrytype", "").casefold() in {"inproceedings", "conference"} or f.get("booktitle"):
        return "conference"
    if f.get("journal"):
        return "journal"
    return "other"


class Knowledge:
    def __init__(self, store: Store):
        self.store = store

    def source(self, source_id: str) -> dict | None:
        row = self.store.one("SELECT * FROM sources WHERE id=?", (source_id,))
        if not row:
            return None
        result = load(row["data"])
        result.update({"id": row["id"], "source_content_id": row["id"], "snapshot_id": row["snapshot_id"],
                       "version_id": row["version_id"], "assignment_status": row["status"],
                       "namespace": row["namespace"]})
        snapshot = self.store.one("SELECT hash,fetched_at FROM snapshots WHERE id=?", (row["snapshot_id"],))
        result["snapshot_hash"] = snapshot["hash"]
        result.setdefault("fetched_at", snapshot["fetched_at"])
        check = self.store.one("SELECT checked_at FROM source_checks WHERE source_id=?", (source_id,))
        result["checked_at"] = check["checked_at"] if check else result["fetched_at"]
        settings = self.store.one("SELECT data FROM settings WHERE id=1")
        ttl = load(settings["data"]).get("knowledge_ttl_seconds", 86400) if settings else 86400
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(result["checked_at"])).total_seconds()
            result["stale"] = age > ttl
        except (ValueError, TypeError):
            result["stale"] = True
        result["fields"] = result.get("fields", {})
        return result

    def version(self, version_id: str) -> dict | None:
        row = self.store.one("SELECT * FROM versions WHERE id=?", (version_id,))
        if not row:
            return None
        row["identifiers"] = load(row["identifiers"])
        row["integrated_fields"] = load(row.pop("fields"))
        row["field_selections"] = load(row.pop("selections"))
        row["fields"] = row["integrated_fields"]
        row["sources"] = [self.source(x["id"]) for x in self.store.all(
            "SELECT id FROM sources WHERE version_id=? ORDER BY created_at,id", (version_id,))]
        row["source_ids"] = [x["id"] for x in row["sources"]]
        row["conflicts"] = [dict(field=k, **v) for k, v in row["field_selections"].items()
                            if v["status"] == "conflicting"]
        row["relations"] = self.store.all(
            "SELECT * FROM relations WHERE from_version_id=? OR to_version_id=?", (version_id, version_id))
        row["rule_version"] = RULE_VERSION
        return row

    def article(self, article_id: str) -> dict | None:
        row = self.store.one("SELECT * FROM articles WHERE id=?", (article_id,))
        if not row:
            return None
        row["recommendation"] = load(row["recommendation"])
        row["versions"] = [self.version(v["id"]) for v in self.store.all(
            "SELECT id FROM versions WHERE article_id=? ORDER BY created_at,id", (article_id,))]
        row["version_count"] = len(row["versions"])
        row["source_count"] = sum(len(v["sources"]) for v in row["versions"])
        row["author"] = next((v["fields"].get("author", "") for v in row["versions"]), "")
        row["recommended_version"] = next((v for v in row["versions"]
                                            if v["id"] == row["recommended_version_id"]), None)
        return row

    def ingest(self, records: list[dict], namespace: str) -> list[dict]:
        source_ids = []
        for record in records:
            # Online tasks must never ingest fixture records into online knowledge.
            mock = record.get("status") == "mock" or record.get("source_type") == "mock"
            if mock != (namespace == "mock"):
                continue
            source_ids.append(self._ingest(record, namespace))
        self._link_relations(source_ids, namespace)
        article_ids = {r["article_id"] for r in self.store.all(
            "SELECT DISTINCT article_id FROM versions WHERE namespace=?", (namespace,))}
        for article_id in article_ids:
            self._recommend(article_id)
        return [self.source(sid) for sid in source_ids]

    def _ingest(self, record: dict, namespace: str) -> str:
        snapshot_id = self.store.snapshot(record)
        existing = self.store.one(
            "SELECT id FROM sources WHERE namespace=? AND provider=? AND record_id=? AND snapshot_id=?",
            (namespace, record["provider"], record["record_id"], snapshot_id))
        if existing:
            self.store.execute("INSERT INTO source_checks VALUES(?,?) ON CONFLICT(source_id) "
                               "DO UPDATE SET checked_at=excluded.checked_at",
                               (existing["id"], record.get("fetched_at", now())))
            return existing["id"]
        sid = uid("src")
        ids = identifiers(record)
        source_fields = record.get("fields", {})
        trusted = record.get("status") in {"verified", "mock"} and record.get("identity_basis") in {
            "doi", "arxiv", "official_id"} and bool(ids)
        version_id = None
        incompatible = False
        if trusted:
            matches = []
            for row in self.store.all("SELECT * FROM versions WHERE namespace=?", (namespace,)):
                stored_ids = load(row["identifiers"])
                contradictory = any(ids.get(k) and stored_ids.get(k) and ids[k] != stored_ids[k] for k in STRONG_IDS)
                shared = any(ids.get(k) and ids.get(k) == stored_ids.get(k) for k in STRONG_IDS)
                if contradictory and shared:
                    incompatible = True
                    continue
                tuple_match = (not contradictory and not ids.get("arxiv") and not stored_ids.get("arxiv")
                               and publication_type(record) == row["version_type"]
                               and publication_tuple_matches(source_fields, load(row["fields"])))
                if same_ids(ids, stored_ids) or tuple_match:
                    if identity_compatible(source_fields, load(row["fields"])):
                        matches.append(row)
                    else:
                        incompatible = True
            if len(matches) == 1:
                version_id = matches[0]["id"]
                merged_ids = {**load(matches[0]["identifiers"]), **ids}
                self.store.execute("UPDATE versions SET identifiers=? WHERE id=?", (dump(merged_ids), version_id))
            elif not matches and not incompatible:
                article_id, version_id = uid("art"), uid("ver")
                timestamp = now()
                with self.store.connect() as db:
                    db.execute("INSERT INTO articles VALUES(?,?,?,?,?,?)",
                               (article_id, namespace, source_fields.get("title", "Untitled source record"),
                                None, dump({"status": "unavailable", "reason": "Not evaluated", "revision": 0}), timestamp))
                    db.execute("INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (version_id, article_id, namespace, publication_type(record), dump(ids), "{}", "{}", 0,
                                timestamp, timestamp))
        assignment = "confirmed" if version_id else "unresolved"
        data = dict(record)
        data["assignment_reason"] = ("Verified publication identifier and corroborating identity" if version_id
                                     else "Identity evidence insufficient or contradictory; retained as candidate")
        with self.store.connect() as db:
            db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)",
                       (sid, namespace, record["provider"], record["record_id"], snapshot_id, version_id,
                        assignment, dump(data), now()))
        self.store.execute("INSERT INTO source_checks VALUES(?,?)", (sid, record.get("fetched_at", now())))
        if version_id:
            self._merge(version_id)
        return sid

    def _merge(self, version_id: str):
        rows = self.store.all("SELECT * FROM sources WHERE version_id=? ORDER BY created_at,id", (version_id,))
        values: dict[str, list[dict]] = {}
        # New snapshots supersede only the same provider record, never other sources.
        latest = {(row["provider"], row["record_id"]): row for row in rows}
        for row in latest.values():
            rec = load(row["data"])
            for field, value in rec.get("fields", {}).items():
                if value is not None and str(value).strip():
                    values.setdefault(field, []).append({
                        "value": str(value), "source_content_id": row["id"], "snapshot_id": row["snapshot_id"],
                        "source_url": rec.get("source_url", ""), "field_path": f"fields.{field}",
                        "source_type": rec.get("source_type", "unknown")})
        fields, selections = {}, {}
        for field, choices in values.items():
            # Recompute from sources, not previous integrated values. No majority vote.
            groups = {field_normalize(field, choice["value"]) for choice in choices}
            status = "selected" if len(groups) == 1 else "conflicting"
            selected = choices[0] if status == "selected" else None
            if selected:
                fields[field] = selected["value"]
            selections[field] = {
                "value": selected["value"] if selected else None, "status": status,
                "normalized_value": field_normalize(field, selected["value"]) if selected else None,
                "evidence": choices, "reason": "Equivalent source values" if selected else
                "Substantive source disagreement; no automatic majority selection"}
        self.store.execute(
            "UPDATE versions SET fields=?,selections=?,merge_revision=merge_revision+1,updated_at=? WHERE id=?",
            (dump(fields), dump(selections), now(), version_id))

    def _link_relations(self, source_ids: list[str], namespace: str):
        for source_id in source_ids:
            source = self.source(source_id)
            if not source or not source["version_id"]:
                continue
            current = self.version(source["version_id"])
            for rel in source.get("relations", []):
                if rel.get("relation") not in ALLOWED_RELATIONS or not rel.get("evidence_url"):
                    continue
                target_ids = {k: normalize_id(k, v) for k, v in rel.get("target_identifiers", {}).items()
                              if k in STRONG_IDS and v}
                matches = []
                for row in self.store.all("SELECT id,identifiers FROM versions WHERE namespace=?", (namespace,)):
                    if row["id"] == current["id"]:
                        continue
                    stored = load(row["identifiers"])
                    if any(stored.get(k) == v for k, v in target_ids.items()):
                        target = self.version(row["id"])
                        if identity_compatible(current["fields"], target["fields"]):
                            matches.append(target)
                if len(matches) != 1:
                    continue
                target = matches[0]
                with self.store.connect() as db:
                    # Explicit source-backed same-work relation is required; title alone never links.
                    if target["article_id"] != current["article_id"]:
                        old = target["article_id"]
                        db.execute("UPDATE versions SET article_id=? WHERE article_id=?",
                                   (current["article_id"], old))
                        db.execute("DELETE FROM articles WHERE id=?", (old,))
                    db.execute("INSERT OR IGNORE INTO relations VALUES(?,?,?,?,?,?)",
                               (uid("rel"), current["id"], target["id"], rel["relation"], source_id,
                                rel["evidence_url"]))

    def _recommend(self, article_id: str):
        article = self.article(article_id)
        if not article:
            return
        eligible = []
        for version in article["versions"]:
            f = version["fields"]
            if not all(f.get(k) for k in ("title", "author", "year")):
                continue
            if any(k in f and str(f[k]).casefold() in {"true", "yes", "retracted"}
                   for k in ("retracted", "withdrawn")):
                continue
            if version["version_type"] == "conference" and not f.get("booktitle"):
                continue
            if version["version_type"] == "journal" and not f.get("journal"):
                continue
            if version["conflicts"]:
                continue
            eligible.append(version)
        formal = [v for v in eligible if v["version_type"] in {"conference", "journal"}]
        pool = formal or eligible
        chosen = pool[0] if len(pool) == 1 else None
        reason = ("Verified formal publication with explicit same-work evidence" if chosen and formal else
                  "Verified current publication; no confirmed preferable publication" if chosen else
                  "No unique sufficiently evidenced publication; recommendation withheld")
        old = article["recommendation"]
        target = chosen["id"] if chosen else None
        revision = old.get("revision", 0) + (old.get("version_id") != target or old.get("reason") != reason)
        rec = {"version_id": target, "status": "available" if chosen else "unavailable", "reason": reason,
               "revision": revision, "rule_version": RULE_VERSION,
               "source_ids": chosen["source_ids"] if chosen else [], "is_mock": article["namespace"] == "mock"}
        self.store.execute("UPDATE articles SET recommended_version_id=?,recommendation=? WHERE id=?",
                           (target, dump(rec), article_id))
