from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from app.config import Settings
from app.time_utils import utc_now_iso


SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT,
        created_at TEXT NOT NULL,
        timezone TEXT NOT NULL DEFAULT 'Asia/Seoul',
        last_successful_scan_at TEXT,
        last_scheduled_run_date TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS connections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE,
        notion_access_token_encrypted TEXT,
        notion_workspace_id TEXT,
        notion_workspace_name TEXT,
        notion_bot_id TEXT,
        notion_owner_info TEXT,
        notion_inbox_database_id TEXT,
        notion_inbox_url TEXT,
        notification_email TEXT,
        notification_email_verified INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL UNIQUE,
        default_channel TEXT NOT NULL DEFAULT 'email',
        notify_time TEXT NOT NULL DEFAULT '08:00',
        scan_time TEXT NOT NULL DEFAULT '02:00',
        timezone TEXT NOT NULL DEFAULT 'Asia/Seoul',
        notify_zero INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scan_targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        notion_object_id TEXT NOT NULL,
        notion_object_type TEXT NOT NULL CHECK(notion_object_type IN ('page', 'database')),
        title TEXT NOT NULL,
        url TEXT,
        include_children INTEGER NOT NULL DEFAULT 1,
        excluded_page_ids TEXT NOT NULL DEFAULT '[]',
        active INTEGER NOT NULL DEFAULT 1,
        last_checked_at TEXT,
        last_result TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_graph_nodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        notion_object_id TEXT NOT NULL,
        object_type TEXT NOT NULL CHECK(object_type IN ('page', 'database')),
        title TEXT NOT NULL,
        url TEXT,
        parent_id TEXT,
        parent_type TEXT,
        source_target_id INTEGER,
        last_edited_time TEXT,
        first_seen_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(user_id, notion_object_id),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_graph_edges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        source_object_id TEXT NOT NULL,
        target_object_id TEXT NOT NULL,
        relation_type TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(user_id, source_object_id, target_object_id, relation_type),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS knowledge_graph_syncs (
        user_id INTEGER PRIMARY KEY,
        status TEXT NOT NULL,
        node_count INTEGER NOT NULL DEFAULT 0,
        edge_count INTEGER NOT NULL DEFAULT 0,
        last_synced_at TEXT,
        error_message TEXT,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        run_id TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        last_successful_scan_at_before_run TEXT,
        scanned_page_count INTEGER NOT NULL DEFAULT 0,
        changed_page_count INTEGER NOT NULL DEFAULT 0,
        proposal_count INTEGER NOT NULL DEFAULT 0,
        error_count INTEGER NOT NULL DEFAULT 0,
        omission_count INTEGER NOT NULL DEFAULT 0,
        contradiction_count INTEGER NOT NULL DEFAULT 0,
        held_count INTEGER NOT NULL DEFAULT 0,
        applied_count INTEGER NOT NULL DEFAULT 0,
        apply_failed_count INTEGER NOT NULL DEFAULT 0,
        notification_status TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS proposals_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        run_id TEXT NOT NULL,
        notion_proposal_page_id TEXT,
        source_page_id TEXT NOT NULL,
        block_id TEXT NOT NULL,
        issue_type TEXT NOT NULL,
        apply_mode TEXT NOT NULL,
        original_sentence_hash TEXT NOT NULL,
        suggested_sentence_hash TEXT NOT NULL,
        original_sentence TEXT,
        suggested_sentence TEXT,
        rationale TEXT,
        source_urls TEXT,
        status TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT,
        UNIQUE(user_id, source_page_id, block_id, original_sentence_hash, suggested_sentence_hash),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS nocturne_edits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        source_page_id TEXT NOT NULL,
        block_id TEXT NOT NULL,
        proposal_id TEXT,
        applied_at TEXT NOT NULL,
        before_text_hash TEXT NOT NULL,
        after_text_hash TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS email_verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        email TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        verified_at TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        run_id TEXT,
        event TEXT NOT NULL,
        level TEXT NOT NULL DEFAULT 'info',
        payload TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS onboarding_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        progress_key TEXT NOT NULL,
        acknowledged_at TEXT NOT NULL,
        UNIQUE(user_id, progress_key),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """,
)


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.database_path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            for statement in SCHEMA:
                conn.execute(statement)
            self._ensure_schema_compat(conn)
            self._ensure_default_records(conn)
            conn.commit()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_default_records(self, conn: sqlite3.Connection) -> None:
        row = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        now = utc_now_iso()
        if row is None:
            cursor = conn.execute(
                "INSERT INTO users (email, created_at, timezone) VALUES (?, ?, ?)",
                (self.settings.default_user_email, now, "Asia/Seoul"),
            )
            user_id = cursor.lastrowid
        else:
            user_id = row["id"]
        conn.execute(
            """
            INSERT OR IGNORE INTO connections (user_id, created_at, updated_at)
            VALUES (?, ?, ?)
            """,
            (user_id, now, now),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO notification_settings
                (user_id, default_channel, notify_time, scan_time, timezone, notify_zero, created_at, updated_at)
            VALUES (?, 'email', '08:00', '02:00', 'Asia/Seoul', 1, ?, ?)
            """,
            (user_id, now, now),
        )

    def _ensure_schema_compat(self, conn: sqlite3.Connection) -> None:
        proposal_columns = {row["name"] for row in conn.execute("PRAGMA table_info(proposals_cache)").fetchall()}
        additions = {
            "original_sentence": "original_sentence TEXT",
            "suggested_sentence": "suggested_sentence TEXT",
            "rationale": "rationale TEXT",
            "source_urls": "source_urls TEXT",
            "updated_at": "updated_at TEXT",
        }
        for column, ddl in additions.items():
            if column not in proposal_columns:
                conn.execute(f"ALTER TABLE proposals_cache ADD COLUMN {ddl}")

    def default_user(self) -> sqlite3.Row:
        with self.connection() as conn:
            return conn.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone()

    def user_by_id(self, user_id: int) -> sqlite3.Row | None:
        return self.row("SELECT * FROM users WHERE id = ?", (user_id,))

    def user_for_notion_oauth(self, oauth_data: dict[str, Any]) -> sqlite3.Row:
        workspace_id = str(oauth_data.get("workspace_id") or "").strip()
        bot_id = str(oauth_data.get("bot_id") or "").strip()
        now = utc_now_iso()
        with self.connection() as conn:
            if workspace_id:
                row = conn.execute(
                    """
                    SELECT u.* FROM users u
                    JOIN connections c ON c.user_id = u.id
                    WHERE c.notion_workspace_id = ?
                    ORDER BY u.id LIMIT 1
                    """,
                    (workspace_id,),
                ).fetchone()
                if row:
                    return row
            if bot_id:
                row = conn.execute(
                    """
                    SELECT u.* FROM users u
                    JOIN connections c ON c.user_id = u.id
                    WHERE c.notion_bot_id = ?
                    ORDER BY u.id LIMIT 1
                    """,
                    (bot_id,),
                ).fetchone()
                if row:
                    return row

            cursor = conn.execute(
                "INSERT INTO users (email, created_at, timezone) VALUES (?, ?, ?)",
                (self._notion_owner_email(oauth_data.get("owner")) or self.settings.default_user_email, now, "Asia/Seoul"),
            )
            user_id = int(cursor.lastrowid)
            owner = oauth_data.get("owner")
            conn.execute(
                """
                INSERT INTO connections
                    (user_id, notion_workspace_id, notion_workspace_name, notion_bot_id, notion_owner_info, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    workspace_id or None,
                    str(oauth_data.get("workspace_name") or "").strip() or None,
                    bot_id or None,
                    json.dumps(owner, ensure_ascii=False, default=str) if owner is not None else None,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO notification_settings
                    (user_id, default_channel, notify_time, scan_time, timezone, notify_zero, created_at, updated_at)
                VALUES (?, 'email', '08:00', '02:00', 'Asia/Seoul', 1, ?, ?)
                """,
                (user_id, now, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            assert row is not None
            return row

    def row(self, query: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self.connection() as conn:
            return conn.execute(query, tuple(params)).fetchone()

    def rows(self, query: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self.connection() as conn:
            return list(conn.execute(query, tuple(params)).fetchall())

    def execute(self, query: str, params: Iterable[Any] = ()) -> int:
        with self.connection() as conn:
            cursor = conn.execute(query, tuple(params))
            conn.commit()
            return int(cursor.lastrowid or cursor.rowcount)

    def update(self, query: str, params: Iterable[Any] = ()) -> None:
        with self.connection() as conn:
            conn.execute(query, tuple(params))
            conn.commit()

    def log(self, event: str, *, user_id: int | None = None, run_id: str | None = None, level: str = "info", payload: Any = None) -> None:
        payload_text = json.dumps(payload, ensure_ascii=False, default=str) if payload is not None else None
        self.execute(
            """
            INSERT INTO audit_logs (user_id, run_id, event, level, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, run_id, event, level, payload_text, utc_now_iso()),
        )

    def create_run(self, user_id: int, run_id: str, last_successful_scan_at: str | None) -> None:
        now = utc_now_iso()
        self.execute(
            """
            INSERT INTO runs
                (user_id, run_id, status, last_successful_scan_at_before_run, created_at, updated_at)
            VALUES (?, ?, 'pending', ?, ?, ?)
            """,
            (user_id, run_id, last_successful_scan_at, now, now),
        )

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utc_now_iso()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        params = list(fields.values()) + [run_id]
        self.update(f"UPDATE runs SET {assignments} WHERE run_id = ?", params)

    def active_targets(self, user_id: int) -> list[sqlite3.Row]:
        return self.rows(
            "SELECT * FROM scan_targets WHERE user_id = ? AND active = 1 ORDER BY created_at DESC",
            (user_id,),
        )

    def connection_for_user(self, user_id: int) -> sqlite3.Row:
        now = utc_now_iso()
        self.execute(
            "INSERT OR IGNORE INTO connections (user_id, created_at, updated_at) VALUES (?, ?, ?)",
            (user_id, now, now),
        )
        row = self.row("SELECT * FROM connections WHERE user_id = ?", (user_id,))
        assert row is not None
        return row

    def notification_settings_for_user(self, user_id: int) -> sqlite3.Row:
        now = utc_now_iso()
        self.execute(
            """
            INSERT OR IGNORE INTO notification_settings
                (user_id, created_at, updated_at)
            VALUES (?, ?, ?)
            """,
            (user_id, now, now),
        )
        row = self.row("SELECT * FROM notification_settings WHERE user_id = ?", (user_id,))
        assert row is not None
        return row

    def last_successful_scan_at(self, user_id: int) -> str | None:
        row = self.row(
            """
            SELECT finished_at FROM runs
            WHERE user_id = ? AND status IN ('success', 'partial_success') AND finished_at IS NOT NULL
            ORDER BY finished_at DESC LIMIT 1
            """,
            (user_id,),
        )
        return row["finished_at"] if row else None

    def proposal_exists(self, user_id: int, source_page_id: str, block_id: str, original_hash: str, suggested_hash: str) -> bool:
        row = self.row(
            """
            SELECT id FROM proposals_cache
            WHERE user_id = ? AND source_page_id = ? AND block_id = ?
              AND original_sentence_hash = ? AND suggested_sentence_hash = ?
            LIMIT 1
            """,
            (user_id, source_page_id, block_id, original_hash, suggested_hash),
        )
        return row is not None

    @staticmethod
    def decode_json_array(value: str | None) -> list[str]:
        if not value:
            return []
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(decoded, list):
            return []
        return [str(item) for item in decoded]

    @staticmethod
    def _notion_owner_email(owner: Any) -> str | None:
        if not isinstance(owner, dict):
            return None
        user = owner.get("user")
        if not isinstance(user, dict):
            return None
        person = user.get("person")
        if isinstance(person, dict) and person.get("email"):
            return str(person["email"])
        if user.get("email"):
            return str(user["email"])
        return None
