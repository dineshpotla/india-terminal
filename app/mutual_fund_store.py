"""Persistent shared mutual-fund watchlist storage."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional

try:
    import psycopg
except ImportError:  # pragma: no cover - optional dependency locally
    psycopg = None


class MutualFundWatchlistStore:
    """Stores a shared manual mutual-fund watchlist."""

    def __init__(self):
        db_url = (os.getenv("DATABASE_URL") or "").strip()
        if db_url.startswith("postgres://"):
            db_url = "postgresql://" + db_url[len("postgres://"):]
        self._db_url = db_url
        self._lock = threading.Lock()
        self._sqlite_path = Path(
            os.getenv(
                "MUTUAL_FUND_DB_PATH",
                str(Path(__file__).resolve().parent.parent / "data" / "mutual_funds.db"),
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
        fallback = Path("/tmp") / "mutual_funds.db"
        self._sqlite_path = fallback
        return sqlite3.connect(str(fallback), check_same_thread=False)

    @property
    def storage_mode(self) -> str:
        return self._mode

    @property
    def durable(self) -> bool:
        return self._mode == "postgres"

    def _init_sqlite(self):
        with self._lock, self._sqlite:
            self._sqlite.execute(
                """
                CREATE TABLE IF NOT EXISTS mutual_fund_watchlist_items (
                    scheme_code TEXT PRIMARY KEY,
                    scheme_name TEXT NOT NULL,
                    isin_primary TEXT,
                    category TEXT,
                    benchmark_options TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def _postgres_connect(self):
        return psycopg.connect(self._db_url, connect_timeout=5)

    def _init_postgres(self):
        with self._postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mutual_fund_watchlist_items (
                        scheme_code TEXT PRIMARY KEY,
                        scheme_name TEXT NOT NULL,
                        isin_primary TEXT,
                        category TEXT,
                        benchmark_options TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            conn.commit()

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
                self._log_postgres_error(exc, "[MutualFundStore] Postgres unavailable")
                return False

    @staticmethod
    def _serialize_row(row) -> dict:
        if row is None:
            return {}
        benchmark_options_raw = row["benchmark_options"] if isinstance(row, sqlite3.Row) else row[4]
        try:
            benchmark_options = json.loads(benchmark_options_raw or "[]")
        except Exception:
            benchmark_options = []
        if isinstance(row, sqlite3.Row):
            created_at = row["created_at"]
            scheme_code = row["scheme_code"]
            scheme_name = row["scheme_name"]
            isin_primary = row["isin_primary"]
            category = row["category"]
        else:
            scheme_code, scheme_name, isin_primary, category, _, created_at = row
        return {
            "scheme_code": str(scheme_code or "").strip(),
            "scheme_name": scheme_name,
            "isin_primary": isin_primary,
            "category": category,
            "benchmark_options": benchmark_options if isinstance(benchmark_options, list) else [],
            "added_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
        }

    def list_entries(self) -> List[dict]:
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return []
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT scheme_code, scheme_name, isin_primary, category, benchmark_options, created_at
                        FROM mutual_fund_watchlist_items
                        ORDER BY created_at ASC, scheme_name ASC
                        """
                    )
                    return [self._serialize_row(row) for row in cur.fetchall()]

        with self._lock:
            rows = self._sqlite.execute(
                """
                SELECT scheme_code, scheme_name, isin_primary, category, benchmark_options, created_at
                FROM mutual_fund_watchlist_items
                ORDER BY created_at ASC, scheme_name ASC
                """
            ).fetchall()
        return [self._serialize_row(row) for row in rows]

    def get_entry(self, scheme_code: str) -> Optional[dict]:
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return None
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT scheme_code, scheme_name, isin_primary, category, benchmark_options, created_at
                        FROM mutual_fund_watchlist_items
                        WHERE scheme_code = %s
                        """,
                        (scheme_code,),
                    )
                    row = cur.fetchone()
                    return self._serialize_row(row) if row else None

        with self._lock:
            row = self._sqlite.execute(
                """
                SELECT scheme_code, scheme_name, isin_primary, category, benchmark_options, created_at
                FROM mutual_fund_watchlist_items
                WHERE scheme_code = ?
                """,
                (scheme_code,),
            ).fetchone()
        return self._serialize_row(row) if row else None

    def add_entry(self, entry: dict):
        payload = (
            str(entry.get("scheme_code") or "").strip(),
            entry.get("scheme_name"),
            entry.get("isin_primary"),
            entry.get("category"),
            json.dumps(entry.get("benchmark_options") or []),
        )
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO mutual_fund_watchlist_items (
                            scheme_code, scheme_name, isin_primary, category, benchmark_options
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (scheme_code) DO UPDATE SET
                            scheme_name = EXCLUDED.scheme_name,
                            isin_primary = EXCLUDED.isin_primary,
                            category = EXCLUDED.category,
                            benchmark_options = EXCLUDED.benchmark_options
                        """,
                        payload,
                    )
                conn.commit()
            return

        with self._lock, self._sqlite:
            self._sqlite.execute(
                """
                INSERT INTO mutual_fund_watchlist_items (
                    scheme_code, scheme_name, isin_primary, category, benchmark_options
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scheme_code) DO UPDATE SET
                    scheme_name = excluded.scheme_name,
                    isin_primary = excluded.isin_primary,
                    category = excluded.category,
                    benchmark_options = excluded.benchmark_options
                """,
                payload,
            )

    def remove_entry(self, scheme_code: str):
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM mutual_fund_watchlist_items WHERE scheme_code = %s",
                        (scheme_code,),
                    )
                conn.commit()
            return

        with self._lock, self._sqlite:
            self._sqlite.execute(
                "DELETE FROM mutual_fund_watchlist_items WHERE scheme_code = ?",
                (scheme_code,),
            )
