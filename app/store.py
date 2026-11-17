"""SQLite 持久化：思考流、记忆、对话、开放问题与心智状态。"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS thoughts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    reasoning TEXT NOT NULL DEFAULT '',
    importance REAL NOT NULL DEFAULT 5,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_thoughts_ts ON thoughts(ts);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 5,
    last_access TEXT NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    source_ids TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_memories_ts ON memories(ts);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    proactive INTEGER NOT NULL DEFAULT 0,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    heat REAL NOT NULL DEFAULT 1,
    last_touched TEXT NOT NULL,
    resolved_note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_questions_status ON questions(status);

CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def local_date() -> str:
    """自然日按服务器本地时区计算，避免 UTC 用户在早上 8 点“跨天”。"""
    return datetime.now().astimezone().date().isoformat()


def local_day_start_iso(days_ago: int = 0) -> str:
    local = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    local -= timedelta(days=days_ago)
    return local.astimezone(UTC).isoformat(timespec="seconds")


def parse_ts(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def _loads(raw: str, default: Any) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- thoughts ----------

    def add_thought(
        self,
        kind: str,
        content: str = "",
        *,
        reasoning: str = "",
        importance: float = 5,
        meta: dict | None = None,
    ) -> dict:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO thoughts(ts, kind, content, reasoning, importance, meta) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (utc_now(), kind, content, reasoning, importance, json.dumps(meta or {}, ensure_ascii=False)),
            )
            self._conn.commit()
            thought_id = cur.lastrowid
        return self.get_thought(thought_id)  # type: ignore[return-value]

    def update_thought(self, thought_id: int, **fields: Any) -> dict | None:
        allowed = {"content", "reasoning", "importance", "meta", "kind"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_thought(thought_id)
        if "meta" in updates:
            updates["meta"] = json.dumps(updates["meta"], ensure_ascii=False)
        cols = ", ".join(f"{key} = ?" for key in updates)
        with self._lock:
            self._conn.execute(
                f"UPDATE thoughts SET {cols} WHERE id = ?", (*updates.values(), thought_id)
            )
            self._conn.commit()
        return self.get_thought(thought_id)

    def get_thought(self, thought_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM thoughts WHERE id = ?", (thought_id,)).fetchone()
        return self._thought_row(row) if row else None

    def recent_thoughts(self, limit: int = 40, kind: str | None = None) -> list[dict]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            if kind:
                rows = self._conn.execute(
                    "SELECT * FROM thoughts WHERE kind = ? ORDER BY id DESC LIMIT ?", (kind, limit)
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM thoughts ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
        return [self._thought_row(row) for row in reversed(rows)]

    def _thought_row(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["meta"] = _loads(item.get("meta", "{}"), {})
        return item

    # ---------- memories ----------

    def add_memory(
        self,
        kind: str,
        content: str,
        *,
        importance: float = 5,
        source_ids: list[int] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        now = utc_now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO memories(ts, kind, content, importance, last_access, access_count, source_ids, tags) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    now,
                    kind,
                    content,
                    float(importance),
                    now,
                    json.dumps(source_ids or []),
                    json.dumps(tags or [], ensure_ascii=False),
                ),
            )
            self._conn.commit()
            memory_id = cur.lastrowid
        return self.get_memory(memory_id)  # type: ignore[return-value]

    def update_memory(self, memory_id: int, **fields: Any) -> dict | None:
        allowed = {"content", "kind", "importance", "source_ids", "tags", "last_access", "access_count"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_memory(memory_id)
        for key in ("source_ids", "tags"):
            if key in updates:
                updates[key] = json.dumps(updates[key], ensure_ascii=False)
        cols = ", ".join(f"{key} = ?" for key in updates)
        with self._lock:
            self._conn.execute(
                f"UPDATE memories SET {cols} WHERE id = ?", (*updates.values(), memory_id)
            )
            self._conn.commit()
        return self.get_memory(memory_id)

    def get_memory(self, memory_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return self._memory_row(row) if row else None

    def recent_memories(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)
            ).fetchall()
        return [self._memory_row(row) for row in rows]

    def memory_candidates(self, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memories ORDER BY id DESC LIMIT ?", (max(1, min(limit, 5000)),)
            ).fetchall()
        return [self._memory_row(row) for row in rows]

    def bump_memory_access(self, ids: list[int]) -> None:
        if not ids:
            return
        now = utc_now()
        with self._lock:
            self._conn.executemany(
                "UPDATE memories SET last_access = ?, access_count = access_count + 1 WHERE id = ?",
                [(now, memory_id) for memory_id in ids],
            )
            self._conn.commit()

    def _memory_row(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["source_ids"] = _loads(item.get("source_ids", "[]"), [])
        item["tags"] = _loads(item.get("tags", "[]"), [])
        return item

    # ---------- messages ----------

    def add_message(
        self,
        role: str,
        content: str,
        *,
        proactive: bool = False,
        meta: dict | None = None,
    ) -> dict:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages(ts, role, content, proactive, meta) VALUES (?, ?, ?, ?, ?)",
                (utc_now(), role, content, 1 if proactive else 0, json.dumps(meta or {}, ensure_ascii=False)),
            )
            self._conn.commit()
            message_id = cur.lastrowid
        return self.get_message(message_id)  # type: ignore[return-value]

    def get_message(self, message_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return self._message_row(row) if row else None

    def recent_messages(self, limit: int = 60) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)
            ).fetchall()
        return [self._message_row(row) for row in reversed(rows)]

    def count_proactive_since(self, since_iso: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE proactive = 1 AND ts >= ?", (since_iso,)
            ).fetchone()
        return int(row["n"] if row else 0)

    def count_proactive_today(self) -> int:
        return self.count_proactive_since(local_day_start_iso())

    def count_unanswered_proactive(self, limit: int = 50) -> int:
        """最近一条用户消息之后，连续发出了多少条主动消息。"""
        count = 0
        for message in reversed(self.recent_messages(limit)):
            if message["role"] == "user":
                break
            if message["role"] == "agent" and message["proactive"]:
                count += 1
        return count

    def _message_row(self, row: sqlite3.Row) -> dict:
        item = dict(row)
        item["proactive"] = bool(item.get("proactive"))
        item["meta"] = _loads(item.get("meta", "{}"), {})
        return item

    # ---------- questions ----------

    def add_question(self, text: str) -> dict:
        now = utc_now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO questions(ts, text, status, heat, last_touched) VALUES (?, ?, 'open', 1, ?)",
                (now, text, now),
            )
            self._conn.commit()
            question_id = cur.lastrowid
        return self.get_question(question_id)  # type: ignore[return-value]

    def get_question(self, question_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
        return dict(row) if row else None

    def open_questions(self, limit: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM questions WHERE status = 'open' ORDER BY heat DESC, id DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def touch_question(self, question_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE questions SET heat = heat + 1, last_touched = ? WHERE id = ?",
                (utc_now(), question_id),
            )
            self._conn.commit()

    def resolve_question(self, question_id: int, note: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE questions SET status = 'resolved', resolved_note = ?, last_touched = ? WHERE id = ?",
                (note[:500], utc_now(), question_id),
            )
            self._conn.commit()

    # ---------- state ----------

    def get_state(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        return _loads(row["value"], default)

    def set_state(self, key: str, value: Any) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO state(key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, json.dumps(value, ensure_ascii=False), utc_now()),
            )
            self._conn.commit()

    def get_all_state(self) -> dict:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM state").fetchall()
        return {row["key"]: _loads(row["value"], None) for row in rows}

    # ---------- stats ----------

    def stats(self) -> dict:
        with self._lock:
            counts = {}
            for table in ("thoughts", "memories", "messages", "questions"):
                row = self._conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
                counts[table] = int(row["n"])
            open_row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM questions WHERE status = 'open'"
            ).fetchone()
            proactive_today = self._conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE proactive = 1 AND ts >= ?",
                (local_day_start_iso(),),
            ).fetchone()
        size = self.path.stat().st_size if self.path.exists() else 0
        return {
            **counts,
            "open_questions": int(open_row["n"]),
            "proactive_today": int(proactive_today["n"]),
            "db_bytes": size,
        }
