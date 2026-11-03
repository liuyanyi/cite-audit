"""Conservative publication identity and same-version field integration.

No task input enters this module. Only retrieved source records can become facts.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from .matching import author_match, name_match, names, normalize, plain_title, venue_normalize
from .storage import Store, dump, load, now, uid

RULE_VERSION = "publication-identity-v10"
STRONG_IDS = ("doi", "arxiv", "acl", "pmlr", "tmlr", "openreview", "iclr", "neurips", "cnki")
ALLOWED_RELATIONS = {"preprint_of", "published_as", "same_work_version"}


def forum_track(source: dict) -> str:
    if not source.get("identifiers", {}).get("openreview"):
        return ""
    venue_id = source.get("venue_id", "")
    venue = source.get("fields", {}).get("booktitle", "")
    if source["provider"] == "openreview":
        if "/Workshop/" in venue_id or "workshop" in venue.casefold():
            return "workshop"
        if venue_id.endswith("/Conference"):
            return "main"
    if source["provider"] == "iclr" and venue_normalize(venue) == "iclr":
        return "main"
    return ""


def field_normalize(field: str, value: str | None) -> str:
    """Compare semantics without deleting meaningful identifier/range punctuation."""
    text = str(value or "").strip()
    if field == "title":
        return normalize(plain_title(text))
    if field in {"doi", "eprint"}:
        return normalize_id("arxiv" if field == "eprint" else field, text)
    if field in {"pages", "volume", "number", "articleno", "year"}:
        return re.sub(r"[-–—]+", "-", re.sub(r"\s+", "", text)).casefold()
    if field == "url":
        return text.rstrip("/")
    if field in {"journal", "booktitle"}:
        return venue_normalize(text)
    return normalize(text)


def normalize_id(kind: str, value: str) -> str:
    if kind in {"tmlr", "openreview"}:
        return str(value).strip()  # OpenReview forum IDs are case-sensitive.
    value = str(value).strip().casefold()
    if kind == "doi":
        value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", value)
    if kind == "arxiv":
        value = re.sub(r"^(?:https?://arxiv\.org/(?:abs|pdf)/|arxiv\s*:\s*)", "", value)
        value = re.sub(r"\.pdf$", "", value)
        # Explicit preprint revisions retain identity; base lookup handled separately.
    return value.rstrip("/")


def arxiv_family(ids: dict) -> str | None:
    doi = normalize_id("doi", ids.get("doi", ""))
    value = ids.get("arxiv") or (doi.removeprefix("10.48550/arxiv.") if doi.startswith("10.48550/arxiv.") else "")
    match = re.fullmatch(r"(\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?", normalize_id("arxiv", value))
    return match[1] if match else None


def identifiers(record: dict) -> dict:
    ids = dict(record.get("identifiers", {}))
    for key in ("doi", "eprint"):
        if record.get("fields", {}).get(key):
            value = record["fields"][key]
            if key == "eprint" and not arxiv_family({"arxiv": value}):
                continue
            ids.setdefault("arxiv" if key == "eprint" else key, value)
    return {key: normalize_id(key, value) for key, value in ids.items()
            if key in STRONG_IDS and value}


def authors(value: str) -> list[str]:
    names = re.split(r"\s+and\s+", value or "", flags=re.IGNORECASE)
    return [normalize(name.split(",")[0] if "," in name else name.split()[-1])
            for name in names if name.strip() and normalize(name) not in {"others", "etal"}]


def model_repository_match(left: dict, right: dict) -> bool:
    url = str(left.get("url", "")).rstrip("/")
    return bool(re.fullmatch(r"https://huggingface\.co/[\w.-]+/[\w.-]+", url)
                and url == str(right.get("url", "")).rstrip("/")
                and left.get("doi") and field_normalize("doi", left["doi"]) == field_normalize("doi", right.get("doi"))
                and left.get("year") and left["year"] == right.get("year")
                and str(left.get("publisher", "")).casefold() == str(right.get("publisher", "")).casefold() == "hugging face")


def identity_compatible(left: dict, right: dict) -> bool:
    """Title/author corroboration prevents a wrong DOI binding from spreading."""
    lt, rt = (field_normalize("title", f.get("title")) for f in (left, right))
    if lt and rt and lt != rt:
        return False
    la, ra = names(left.get("author", "")), names(right.get("author", ""))
    if author_match(left.get("author", ""), right.get("author", "")) == "order_variant":
        return True
    return not (la and ra and name_match(la[0], ra[0]) == "different")


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
    required = ("title", "year", "pages")
    if not all(left.get(k) and right.get(k) and field_normalize(k, left[k]) == field_normalize(k, right[k])
               for k in required):
        return False
    if author_match(left.get("author", ""), right.get("author", "")) != "full":
        return False
    venue = "booktitle" if left.get("booktitle") else "journal"
    return bool(left.get(venue) and right.get(venue) and venue_normalize(left[venue]) == venue_normalize(right[venue]))


def title_with_subtitle(left: dict, right: dict) -> bool:
    """Called only for matching identifiers within one publication type."""
    if author_match(left.get("author", ""), right.get("author", "")) != "full":
        return False
    for short, long in ((left, right), (right, left)):
        main, sep, subtitle = plain_title(long.get("title", "")).partition(":")
        title = field_normalize("title", short.get("title"))
        if sep and subtitle.strip() and title and title == field_normalize("title", main):
            return True
    return False


def publication_type(record: dict) -> str:
    f = record.get("fields", {})
    if (identifiers(record).get("arxiv") or arxiv_family(identifiers(record)) or f.get("archiveprefix", "").casefold() == "arxiv"
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

    def articles(self, q="", mode="", page=1, page_size=25) -> dict:
        """Filter and page summaries without hydrating versions or source evidence."""
        conditions, args = [], []
        if q:
            conditions.append("""(instr(casefold(a.title), ?) > 0 OR EXISTS (
                SELECT 1 FROM versions v WHERE v.article_id=a.id AND (
                    instr(casefold(json_extract(v.fields, '$.author')), ?) > 0 OR EXISTS (
                        SELECT 1 FROM json_each(v.identifiers) i
                        WHERE instr(casefold(i.value), ?) > 0))))""")
            args.extend([q.casefold()] * 3)
        if mode:
            conditions.append("(CASE WHEN a.namespace='mock' THEN 'demo' ELSE 'online' END)=?")
            args.append(mode)
        where = " AND ".join(conditions) or "1=1"
        result = self.store.page(
            f"articles a WHERE {where}", args, order="a.created_at DESC,a.id", page=page, page_size=page_size,
            columns="""a.*,
                COALESCE((SELECT json_extract(v.fields, '$.author') FROM versions v
                          WHERE v.article_id=a.id ORDER BY v.created_at,v.id LIMIT 1), '') AS author,
                (SELECT COUNT(*) FROM versions v WHERE v.article_id=a.id) AS version_count,
                (SELECT COUNT(*) FROM versions v JOIN sources s ON s.version_id=v.id
                 WHERE v.article_id=a.id) AS source_count,
                COALESCE((SELECT MAX(v.updated_at) FROM versions v WHERE v.article_id=a.id),
                         a.created_at) AS updated_at""")
        for item in result["items"]:
            item["recommendation"] = load(item["recommendation"])
        return result

    def local_arxiv_source(self, identifier: str, discovery_ttl: int) -> dict | None:
        """Pinned revisions are durable. Latest-version checks expire independently."""
        pinned = bool(re.search(r"v\d+$", identifier))
        rows = self.store.all(
            "SELECT s.id,s.record_id,s.snapshot_id,p.fetched_at FROM sources s "
            "JOIN snapshots p ON p.id=s.snapshot_id WHERE s.namespace='online' "
            "AND s.provider='arxiv' AND s.status='confirmed' "
            "AND (s.record_id=? OR s.record_id GLOB ?) ORDER BY p.fetched_at DESC",
            (identifier, identifier if pinned else identifier + "v[0-9]*"))
        for row in rows:
            if not pinned:
                age = (datetime.now(UTC) - datetime.fromisoformat(row["fetched_at"])).total_seconds()
                if age > discovery_ttl:
                    continue
                # A request for a pinned revision cannot establish the latest revision.
                raw = self.store.one("SELECT content FROM snapshots WHERE id=?", (row["snapshot_id"],))
                url = load(raw["content"]).get("url", "")
                queried = parse_qs(urlsplit(url).query).get("id_list", [""])[0].split(",")
                if identifier not in queried:
                    continue
            source = self.source(row["id"])
            if source["source_type"] == "official" and source["status"] == "verified":
                return source
        return None

    def source(self, source_id: str) -> dict | None:
        row = self.store.one("SELECT * FROM sources WHERE id=?", (source_id,))
        if not row:
            return None
        result = load(row["data"])
        result.pop("raw_payload", None)
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
        if row["fields"].get("title"):
            row["fields"]["title"] = plain_title(row["fields"]["title"])
        title_selection = row["field_selections"].get("title", {})
        if title_selection.get("value"):
            title_selection["value"] = plain_title(title_selection["value"])
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
        target_ids = set()
        for record in records:
            # Online tasks must never ingest fixture records into online knowledge.
            mock = record.get("status") == "mock" or record.get("source_type") == "mock"
            if mock != (namespace == "mock"):
                continue
            source_ids.append(self._ingest(record, namespace))
            target_ids.update(identifiers(record).items())
        if not source_ids:
            return []
        # A relation can arrive before its target, including in another task.
        relation_ids = set(source_ids)
        if target_ids:
            for row in self.store.all(
                    "SELECT id,json_extract(data,'$.relations') AS links FROM sources "
                    "WHERE namespace=? AND version_id IS NOT NULL AND json_array_length(data,'$.relations') > 0",
                    (namespace,)):
                if any((k, normalize_id(k, v)) in target_ids for rel in load(row["links"])
                       for k, v in rel.get("target_identifiers", {}).items()):
                    relation_ids.add(row["id"])
        # Reconsider all official forums for this title when either track arrives.
        for sid in source_ids:
            source = self.source(sid)
            if source["version_id"] and forum_track(source):
                rows = self.store.all(
                    "SELECT id FROM sources WHERE namespace=? AND provider IN ('iclr','openreview') "
                    "AND version_id IS NOT NULL AND json_extract(data,'$.fields.title')=?",
                    (namespace, source["fields"]["title"]))
                relation_ids.update(row["id"] for row in rows)
        self._link_relations(sorted(relation_ids), namespace)
        # Resolve owners after linking: a same-work relation can move versions
        # into the surviving article and remove the previous owner.
        for row in self.store.all(
                "SELECT DISTINCT v.article_id FROM sources s JOIN versions v ON s.version_id=v.id "
                "WHERE s.id IN (SELECT value FROM json_each(?)) ORDER BY v.article_id",
                (dump(sorted(relation_ids)),)):
            self._recommend(row["article_id"])
        return [self.source(sid) for sid in source_ids]

    def _ingest(self, record: dict, namespace: str) -> str:
        if namespace == "online" and record["provider"] == "cnki":
            # The search-row token rotates. The official journal export carries its own file key.
            from .bib import parse_bib
            raw = record.get("raw_payload", {})
            endpoint = urlsplit(raw.get("url", ""))
            if endpoint.netloc == "kns.cnki.net" and endpoint.path == "/dm8/manage/export.html":
                doc = parse_bib(raw.get("response_body", ""))
                if len(doc.entries) == 1 and not doc.diagnostics:
                    entry = doc.entries[0]
                    if (entry.entry_type == "article" and not entry.diagnostics
                            and re.fullmatch(r"[A-Za-z]{2,8}[0-9]{7,15}", entry.key.strip())
                            and all(entry.fields.get(k) for k in ("title", "author", "journal"))):
                        record = {**record, "identifiers": {**record["identifiers"], "cnki": entry.key.strip()},
                                  "status": "verified", "identity_basis": "official_id"}
        snapshot_id = self.store.snapshot(record)
        existing = self.store.one(
            "SELECT id,data,version_id FROM sources WHERE namespace=? AND provider=? AND record_id=? AND snapshot_id=?",
            (namespace, record["provider"], record["record_id"], snapshot_id))
        if existing:
            data = load(existing["data"])
            if record.get("relations") and data.get("relations") != record["relations"]:
                data["relations"] = record["relations"]
                self.store.execute("UPDATE sources SET data=? WHERE id=?", (dump(data), existing["id"]))
            self.store.execute("INSERT INTO source_checks VALUES(?,?) ON CONFLICT(source_id) "
                               "DO UPDATE SET checked_at=excluded.checked_at",
                               (existing["id"], record.get("fetched_at", now())))
            if existing["version_id"] or record.get("status") not in {"verified", "mock"}:
                return existing["id"]
        sid = existing["id"] if existing else uid("src")
        ids = identifiers(record)
        source_fields = record.get("fields", {})
        trusted = record.get("status") in {"verified", "mock"} and record.get("identity_basis") in {
            "doi", "arxiv", "official_id"} and bool(ids)
        version_id = None
        incompatible = False
        if trusted:
            matches = []
            family_articles = set()
            family = arxiv_family(ids)
            for row in self.store.all("SELECT * FROM versions WHERE namespace=?", (namespace,)):
                stored_ids = load(row["identifiers"])
                if (family and arxiv_family(stored_ids) == family and
                        publication_type(record) == row["version_type"] == "preprint" and
                        identity_compatible(source_fields, load(row["fields"]))):
                    family_articles.add(row["article_id"])
                contradictory = any(ids.get(k) and stored_ids.get(k) and ids[k] != stored_ids[k] for k in STRONG_IDS)
                shared = any(ids.get(k) and ids.get(k) == stored_ids.get(k) for k in STRONG_IDS)
                if contradictory and shared:
                    incompatible = True
                    continue
                tuple_match = (not contradictory and not ids.get("arxiv") and not stored_ids.get("arxiv")
                               and publication_type(record) == row["version_type"]
                               and publication_tuple_matches(source_fields, load(row["fields"])))
                if same_ids(ids, stored_ids) or tuple_match:
                    known = load(row["fields"])
                    compatible = identity_compatible(source_fields, known) or model_repository_match(source_fields, known)
                    if known.get("title") is None:
                        # A conflicted title must not become a wildcard for later records.
                        version = self.version(row["id"])
                        if version["field_selections"].get("title", {}).get("status") == "conflicting":
                            compatible = any(identity_compatible(source_fields, {"title": c["value"], "author": known.get("author")})
                                             for c in version["field_selections"]["title"]["evidence"])
                    if compatible or (same_ids(ids, stored_ids) and publication_type(record) == row["version_type"]
                                      and title_with_subtitle(source_fields, known)):
                        matches.append(row)
                    else:
                        incompatible = True
            if len(matches) == 1:
                version_id = matches[0]["id"]
                merged_ids = {**load(matches[0]["identifiers"]), **ids}
                self.store.execute("UPDATE versions SET identifiers=? WHERE id=?", (dump(merged_ids), version_id))
            elif not matches and not incompatible:
                existing_article = next(iter(family_articles)) if len(family_articles) == 1 else None
                article_id, version_id = existing_article or uid("art"), uid("ver")
                timestamp = now()
                with self.store.connect() as db:
                    if not existing_article:
                        db.execute("INSERT INTO articles VALUES(?,?,?,?,?,?)",
                                   (article_id, namespace, source_fields.get("title", "Untitled source record"),
                                    None, dump({"status": "unavailable", "reason": "Not evaluated", "revision": 0}), timestamp))
                    db.execute("INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (version_id, article_id, namespace, publication_type(record), dump(ids), "{}", "{}", 0,
                                timestamp, timestamp))
        assignment = "confirmed" if version_id else "unresolved"
        data = dict(record)
        # The immutable snapshot already owns the response body. Keep only
        # extracted metadata here, including relations needed for integration.
        data.pop("raw_payload", None)
        data["assignment_reason"] = ("Verified publication identifier and corroborating identity" if version_id
                                     else "Identity evidence insufficient or contradictory; retained as candidate")
        with self.store.connect() as db:
            if existing:
                db.execute("UPDATE sources SET version_id=?,status=?,data=? WHERE id=?",
                           (version_id, assignment, dump(data), sid))
            else:
                db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?)",
                           (sid, namespace, record["provider"], record["record_id"], snapshot_id, version_id,
                            assignment, dump(data), now()))
        if not existing:
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
            if field == "author" and all(author_match(choices[0]["value"], c["value"]) == "full" for c in choices):
                groups = {"equivalent full names"}
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
        sources = [self.source(sid) for sid in source_ids]
        forum_sources = {}
        for source in sources:
            if source and source["version_id"] and forum_track(source):
                forum_sources.setdefault(source["fields"]["title"], []).append(source)
        for source in sources:
            if not source or not source["version_id"]:
                continue
            current = self.version(source["version_id"])
            links = list(source.get("relations", []))
            if forum_track(source) == "main":
                targets = set()
                mains = set()
                for candidate in forum_sources.get(source["fields"]["title"], []):
                    if author_match(source["fields"].get("author", ""), candidate["fields"].get("author", "")) != "full":
                        continue
                    forum = candidate["identifiers"]["openreview"]
                    (targets if forum_track(candidate) == "workshop" else mains).add(forum)
                if len(mains) == len(targets) == 1 and mains.isdisjoint(targets):
                    links.append({"relation": "same_work_version", "target_identifiers": {"openreview": targets.pop()},
                                  "evidence_url": source["source_url"]})
            for rel in links:
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
                               (uid("rel"), current["id"], target["id"], rel["relation"], source["id"],
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
        main = [v for v in formal if any(forum_track(s) == "main" for s in v["sources"])]
        workshop_pair = (len(formal) == 2 and len(main) == 1
                         and any(forum_track(s) == "workshop" for v in formal for s in v["sources"]))
        pool = main if workshop_pair else formal or eligible
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
