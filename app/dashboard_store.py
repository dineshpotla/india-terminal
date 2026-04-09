"""Persistent dashboard snapshot storage with Postgres or SQLite backends."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

try:
    import psycopg
except ImportError:  # pragma: no cover - optional dependency locally
    psycopg = None


class DashboardStore:
    """Stores the latest dashboard payload for cache-first startup."""

    def __init__(self):
        db_url = (os.getenv("DATABASE_URL") or "").strip()
        if db_url.startswith("postgres://"):
            db_url = "postgresql://" + db_url[len("postgres://"):]
        self._db_url = db_url
        self._lock = threading.Lock()
        self._sqlite_path = Path(
            os.getenv(
                "DASHBOARD_DB_PATH",
                str(Path(__file__).resolve().parent.parent / "data" / "dashboard.db"),
            )
        )
        self._postgres_ready = False
        self._postgres_log_at = 0.0

        if self._db_url and psycopg is not None:
            self._mode = "postgres"
            self._sqlite = None
            self._ensure_postgres_ready()
        else:
            self._mode = "sqlite"
            self._sqlite = self._open_sqlite()
            self._sqlite.row_factory = sqlite3.Row
            self._init_sqlite()

    def _open_sqlite(self) -> sqlite3.Connection:
        primary = self._sqlite_path
        try:
            primary.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(primary, check_same_thread=False)
            conn.execute("CREATE TABLE IF NOT EXISTS _write_test (x INTEGER)")
            conn.execute("DROP TABLE _write_test")
            return conn
        except (OSError, sqlite3.OperationalError):
            pass
        fallback = Path("/tmp") / "dashboard.db"
        self._sqlite_path = fallback
        return sqlite3.connect(str(fallback), check_same_thread=False)

    def _postgres_connect(self):
        return psycopg.connect(self._db_url, connect_timeout=5)

    def _log_postgres_error(self, exc: Exception, prefix: str):
        now = time.time()
        if now - self._postgres_log_at < 30:
            return
        self._postgres_log_at = now
        print(f"{prefix}: {exc}")

    def _ensure_postgres_ready(self) -> bool:
        if self._mode != "postgres":
            return False
        if self._postgres_ready:
            return True
        with self._lock:
            if self._postgres_ready:
                return True
            try:
                self._init_postgres()
                self._postgres_ready = True
                return True
            except Exception as exc:
                self._postgres_ready = False
                self._log_postgres_error(exc, "[DashboardStore] Postgres unavailable")
                return False

    def _init_sqlite(self):
        with self._lock, self._sqlite:
            self._sqlite.execute(
                """
                CREATE TABLE IF NOT EXISTS dashboard_snapshots (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _init_postgres(self):
        with self._postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS dashboard_snapshots (
                        key TEXT PRIMARY KEY,
                        payload JSONB NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            conn.commit()

    def load_snapshot(self, key: str = "dashboard") -> Optional[dict]:
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return None
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT payload::text
                        FROM dashboard_snapshots
                        WHERE key = %s
                        """,
                        (key,),
                    )
                    row = cur.fetchone()
                    if not row or not row[0]:
                        return None
                    return json.loads(row[0])

        with self._lock:
            row = self._sqlite.execute(
                """
                SELECT payload
                FROM dashboard_snapshots
                WHERE key = ?
                """,
                (key,),
            ).fetchone()
        if not row or not row["payload"]:
            return None
        return json.loads(row["payload"])

    def save_snapshot(self, payload: dict, key: str = "dashboard"):
        payload_text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO dashboard_snapshots (key, payload, updated_at)
                        VALUES (%s, %s::jsonb, NOW())
                        ON CONFLICT (key) DO UPDATE
                        SET payload = EXCLUDED.payload,
                            updated_at = EXCLUDED.updated_at
                        """,
                        (key, payload_text),
                    )
                conn.commit()
            return

        with self._lock, self._sqlite:
            self._sqlite.execute(
                """
                INSERT INTO dashboard_snapshots (key, payload, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE
                SET payload = excluded.payload,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, payload_text),
            )
