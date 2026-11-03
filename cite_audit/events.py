"""Bounded, durable UI invalidations; bibliography evidence stays in its own tables."""
from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager

from .storage import dump

TERMINAL = ("completed", "partial", "failed", "cancelled")


def frame(kind, data, cursor):
    return f"id: {cursor}\nevent: {kind}\ndata: {dump(data)}\n\n"


class TaskEvents:
    def __init__(self, store):
        self.store = store
        self._lock = threading.Lock()
        self._listeners = set()
        store.on_change = self.notify

    def notify(self):
        with self._lock:
            for loop, wake in self._listeners:
                if not loop.is_closed():
                    loop.call_soon_threadsafe(wake.set)

    @contextmanager
    def listen(self):
        listener = (asyncio.get_running_loop(), asyncio.Event())
        with self._lock:
            self._listeners.add(listener)
        try:
            yield listener[1]
        finally:
            with self._lock:
                self._listeners.discard(listener)

    def read(self, cursor, task_id):
        # Cursor, changes and active tasks describe one SQLite read snapshot.
        with self.store.connect() as db:
            db.execute("BEGIN")
            bounds = db.execute("SELECT MIN(seq) AS first,MAX(seq) AS last FROM task_events").fetchone()
            latest = bounds["last"] or 0
            reset = cursor is None or cursor > latest or cursor < (bounds["first"] or 1) - 1
            args = [0 if reset else cursor]
            where = "seq > ?"
            if task_id:
                where += " AND task_id=?"
                args.append(task_id)
            changes = [] if reset else [dict(row) for row in db.execute(
                f"SELECT seq,task_id,kind,entry_id FROM task_events WHERE {where} ORDER BY seq", args)]
            tasks = db.execute("SELECT id,status,report IS NOT NULL AS frozen FROM tasks"
                               + (" WHERE id=?" if task_id else ""), (task_id,) if task_id else ()).fetchall()
            active = [row["id"] for row in tasks if row["status"] not in TERMINAL or not row["frozen"]]
        return latest, reset, changes, active

    async def stream(self, cursor=None, task_id=None, heartbeat=15):
        # Subscribe before reading to avoid losing a commit between read and wait.
        with self.listen() as wake:
            first = True
            while True:
                wake.clear()
                latest, reset, changes, active = self.read(cursor, task_id)
                if reset or first:
                    # Re-fetch once even with a valid reconnect cursor: the page's
                    # HTTP request may have been interrupted while it was hidden.
                    yield frame("sync", {"reset": reset, "task_ids": active}, latest if reset else cursor)
                if changes:
                    grouped = {}
                    for change in changes:
                        item = grouped.setdefault(change["task_id"], {
                            "task_id": change["task_id"], "changed": set(), "entry_ids": set(),
                        })
                        item["changed"].add(change["kind"])
                        if change["entry_id"]:
                            item["entry_ids"].add(change["entry_id"])
                    items = [{**item, "changed": sorted(item["changed"]), "entry_ids": sorted(item["entry_ids"])}
                             for item in grouped.values()]
                    yield frame("change", {"items": items}, latest)
                cursor, first = latest, False
                if not active:
                    yield frame("done", {"task_id": task_id}, cursor)
                    return
                try:
                    await asyncio.wait_for(wake.wait(), timeout=heartbeat)
                except TimeoutError:
                    yield ": heartbeat\n\n"
