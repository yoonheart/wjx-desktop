"""本地存储。

三张表：任务、逐份记录、历史问卷。用 SQLite 而不是 localStorage，
是因为「换版本就丢历史」和「没法按原因聚合」这两件事必须解决。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task (
    task_id     TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    title       TEXT DEFAULT '',
    total       INTEGER DEFAULT 0,
    state       TEXT DEFAULT '',
    created_at  REAL DEFAULT 0,
    finished_at REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS unit_record (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    unit_index INTEGER NOT NULL,
    seed       INTEGER,
    state      TEXT,
    exit_addr  TEXT,
    ua_profile TEXT,
    status     INTEGER DEFAULT 0,
    failure    TEXT DEFAULT '',
    detail     TEXT DEFAULT '',
    ttf_ms     INTEGER DEFAULT 0,
    retries    INTEGER DEFAULT 0,
    at         REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_record_task ON unit_record(task_id);
CREATE TABLE IF NOT EXISTS kv (
    name       TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS history (
    url       TEXT PRIMARY KEY,
    title     TEXT DEFAULT '',
    last_used REAL DEFAULT 0,
    runs      INTEGER DEFAULT 0
);
"""


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    url: str
    title: str
    last_used: float
    runs: int


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # -- 任务 ---------------------------------------------------------------

    def create_task(self, task_id: str, url: str, title: str, total: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO task(task_id,url,title,total,state,created_at) VALUES(?,?,?,?,?,?)",
                (task_id, url, title, total, "RUNNING", time.time()),
            )
            self._conn.commit()

    def finish_task(self, task_id: str, state: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE task SET state=?, finished_at=? WHERE task_id=?",
                (state, time.time(), task_id),
            )
            self._conn.commit()

    # -- 记录 ---------------------------------------------------------------

    def add_records(self, task_id: str, records: Iterable[dict]) -> None:
        rows = [
            (
                task_id,
                int(r.get("index", 0)),
                int(r.get("seed", 0)),
                str(r.get("state", "")),
                str(r.get("exit", "")),
                str(r.get("ua", "")),
                int(r.get("status", 0)),
                str(r.get("failure", "")),
                str(r.get("detail", "")),
                int(r.get("ttf_ms", 0)),
                int(r.get("retries", 0)),
                float(r.get("at", time.time())),
            )
            for r in records
        ]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO unit_record(task_id,unit_index,seed,state,exit_addr,ua_profile,status,"
                "failure,detail,ttf_ms,retries,at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self._conn.commit()

    def records(self, task_id: str) -> list[dict]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM unit_record WHERE task_id=? ORDER BY unit_index", (task_id,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def reason_summary(self, task_id: str) -> dict[str, int]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT failure, COUNT(*) AS n FROM unit_record WHERE task_id=? AND failure<>'' "
                "GROUP BY failure ORDER BY n DESC",
                (task_id,),
            )
            return {row["failure"]: row["n"] for row in cursor.fetchall()}

    # -- 通用键值（原 localStorage 的 settings / panels 等） ------------------

    def get_state(self, name: str, default: str = "") -> str:
        with self._lock:
            cursor = self._conn.execute("SELECT value FROM kv WHERE name=?", (name,))
            row = cursor.fetchone()
            return row["value"] if row else default

    def set_state(self, name: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv(name,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (name, value, time.time()),
            )
            self._conn.commit()

    def all_state(self) -> dict[str, str]:
        with self._lock:
            cursor = self._conn.execute("SELECT name, value FROM kv")
            return {row["name"]: row["value"] for row in cursor.fetchall()}

    # -- 历史 ---------------------------------------------------------------

    def touch_history(self, url: str, title: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO history(url,title,last_used,runs) VALUES(?,?,?,1) "
                "ON CONFLICT(url) DO UPDATE SET last_used=excluded.last_used,"
                "runs=runs+1,title=CASE WHEN excluded.title<>'' THEN excluded.title ELSE title END",
                (url, title, time.time()),
            )
            self._conn.commit()

    def recent(self, limit: int = 5) -> list[HistoryEntry]:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM history ORDER BY last_used DESC LIMIT ?", (limit,)
            )
            return [
                HistoryEntry(row["url"], row["title"], row["last_used"], row["runs"])
                for row in cursor.fetchall()
            ]

    # -- 导出 ---------------------------------------------------------------

    def export_records_csv(self, task_id: str, path: Path) -> Path:
        rows = self.records(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        headers = [
            "unit_index", "seed", "state", "failure", "detail",
            "exit_addr", "ua_profile", "status", "ttf_ms", "retries", "at",
        ]
        lines = [",".join(headers)]
        for row in rows:
            lines.append(",".join(str(row.get(h, "")) for h in headers))
        path.write_text("\n".join(lines), encoding="utf-8-sig")
        return path

    def export_json(self, task_id: str, path: Path) -> Path:
        payload = {"task_id": task_id, "records": self.records(task_id)}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def merge_reasons(rows: Sequence[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        key = str(row.get("failure") or "")
        if key:
            out[key] = out.get(key, 0) + 1
    return out
