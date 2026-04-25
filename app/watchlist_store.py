"""Persistent watchlist storage with Postgres or SQLite backends."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable, List

try:
    import psycopg
except ImportError:  # pragma: no cover - optional dependency locally
    psycopg = None


class WatchlistStore:
    """Stores a single shared watchlist for the deployed app."""

    def __init__(self):
        db_url = (os.getenv("DATABASE_URL") or "").strip()
        if db_url.startswith("postgres://"):
            db_url = "postgresql://" + db_url[len("postgres://"):]
        self._db_url = db_url
        self._lock = threading.Lock()
        self._sqlite_path = Path(
            os.getenv(
                "WATCHLIST_DB_PATH",
                str(Path(__file__).resolve().parent.parent / "data" / "watchlist.db"),
            )
        )
        self._seed_symbols = self._parse_seed_symbols(
            os.getenv("WATCHLIST_SEED_SYMBOLS", "")
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
        self._seed_if_empty()

    def _open_sqlite(self) -> sqlite3.Connection:
        """Open a writable SQLite connection, falling back to /tmp on read-only fs."""
        primary = self._sqlite_path
        try:
            primary.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(primary, check_same_thread=False)
            conn.execute("CREATE TABLE IF NOT EXISTS _write_test (x INTEGER)")
            conn.execute("DROP TABLE _write_test")
            return conn
        except (OSError, sqlite3.OperationalError):
            pass
        fallback = Path("/tmp") / "watchlist.db"
        self._sqlite_path = fallback
        return sqlite3.connect(str(fallback), check_same_thread=False)

    @staticmethod
    def _parse_seed_symbols(raw: str) -> List[str]:
        seen = set()
        items = []
        for part in raw.replace("\n", ",").split(","):
            sym = part.strip().upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            items.append(sym)
        return items

    @property
    def storage_mode(self) -> str:
        return self._mode

    @property
    def durable(self) -> bool:
        return self._mode == "postgres" or (
            self._mode == "sqlite" and self._sqlite_path.parent != Path("/tmp")
        )

    @property
    def initialized(self) -> bool:
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return False
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT value
                        FROM watchlist_meta
                        WHERE key = 'initialized'
                        """
                    )
                    row = cur.fetchone()
                    return bool(row and row[0] == "1")

        with self._lock:
            row = self._sqlite.execute(
                """
                SELECT value
                FROM watchlist_meta
                WHERE key = 'initialized'
                """
            ).fetchone()
        return bool(row and row["value"] == "1")

    def _init_sqlite(self):
        with self._lock, self._sqlite:
            self._sqlite.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist_items (
                    symbol TEXT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._sqlite.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def _init_postgres(self):
        with self._postgres_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS watchlist_items (
                        symbol TEXT PRIMARY KEY,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS watchlist_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
            conn.commit()

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
                self._log_postgres_error(exc, "[Watchlist] Postgres unavailable")
                return False

    def _mark_initialized(self):
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO watchlist_meta (key, value)
                        VALUES ('initialized', '1')
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                        """
                    )
                conn.commit()
            return

        with self._lock, self._sqlite:
            self._sqlite.execute(
                """
                INSERT INTO watchlist_meta (key, value)
                VALUES ('initialized', '1')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """
            )

    def _seed_if_empty(self):
        if self.initialized or not self._seed_symbols or self.list_symbols():
            return
        self.merge_symbols(self._seed_symbols)

    def list_symbols(self) -> List[str]:
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return list(self._seed_symbols)
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT symbol
                        FROM watchlist_items
                        ORDER BY created_at ASC, symbol ASC
                        """
                    )
                    return [row[0] for row in cur.fetchall()]

        with self._lock:
            rows = self._sqlite.execute(
                """
                SELECT symbol
                FROM watchlist_items
                ORDER BY created_at ASC, symbol ASC
                """
            ).fetchall()
        return [row["symbol"] for row in rows]

    def add_symbol(self, symbol: str):
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO watchlist_items (symbol)
                        VALUES (%s)
                        ON CONFLICT (symbol) DO NOTHING
                        """,
                        (symbol,),
                    )
                    cur.execute(
                        """
                        INSERT INTO watchlist_meta (key, value)
                        VALUES ('initialized', '1')
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                        """
                    )
                conn.commit()
            return

        with self._lock, self._sqlite:
            self._sqlite.execute(
                """
                INSERT OR IGNORE INTO watchlist_items (symbol)
                VALUES (?)
                """,
                (symbol,),
            )
            self._sqlite.execute(
                """
                INSERT INTO watchlist_meta (key, value)
                VALUES ('initialized', '1')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """
            )

    def merge_symbols(self, symbols: Iterable[str]):
        symbols = list(symbols)
        if not symbols:
            return

        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO watchlist_items (symbol)
                        VALUES (%s)
                        ON CONFLICT (symbol) DO NOTHING
                        """,
                        [(symbol,) for symbol in symbols],
                    )
                    cur.execute(
                        """
                        INSERT INTO watchlist_meta (key, value)
                        VALUES ('initialized', '1')
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                        """
                    )
                conn.commit()
            return

        with self._lock, self._sqlite:
            self._sqlite.executemany(
                """
                INSERT OR IGNORE INTO watchlist_items (symbol)
                VALUES (?)
                """,
                [(symbol,) for symbol in symbols],
            )
            self._sqlite.execute(
                """
                INSERT INTO watchlist_meta (key, value)
                VALUES ('initialized', '1')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """
            )

    def remove_symbol(self, symbol: str):
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM watchlist_items WHERE symbol = %s",
                        (symbol,),
                    )
                    cur.execute(
                        """
                        INSERT INTO watchlist_meta (key, value)
                        VALUES ('initialized', '1')
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                        """
                    )
                conn.commit()
            return

        with self._lock, self._sqlite:
            self._sqlite.execute(
                "DELETE FROM watchlist_items WHERE symbol = ?",
                (symbol,),
            )
            self._sqlite.execute(
                """
                INSERT INTO watchlist_meta (key, value)
                VALUES ('initialized', '1')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """
            )
