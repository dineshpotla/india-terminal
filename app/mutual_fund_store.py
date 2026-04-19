"""Persistent shared mutual-fund watchlist storage."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

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
            self._sqlite.execute(
                """
                CREATE TABLE IF NOT EXISTS mutual_fund_nav_history (
                    scheme_code TEXT NOT NULL,
                    nav_date TEXT NOT NULL,
                    nav REAL NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (scheme_code, nav_date)
                )
                """
            )
            self._sqlite.execute(
                """
                CREATE TABLE IF NOT EXISTS mutual_fund_benchmark_history (
                    benchmark_name TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    close_value REAL NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (benchmark_name, trade_date)
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
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mutual_fund_nav_history (
                        scheme_code TEXT NOT NULL,
                        nav_date DATE NOT NULL,
                        nav DOUBLE PRECISION NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (scheme_code, nav_date)
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mutual_fund_benchmark_history (
                        benchmark_name TEXT NOT NULL,
                        trade_date DATE NOT NULL,
                        close_value DOUBLE PRECISION NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        PRIMARY KEY (benchmark_name, trade_date)
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
    def _coerce_date(value) -> Optional[date]:
        if value is None or value == "":
            return None
        if isinstance(value, date):
            return value
        text = str(value).strip()
        for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d %b %Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except Exception:
                continue
        return None

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

    def latest_nav_point(self, scheme_code: str) -> Optional[Tuple[date, float]]:
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return None
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT nav_date, nav
                        FROM mutual_fund_nav_history
                        WHERE scheme_code = %s
                        ORDER BY nav_date DESC
                        LIMIT 1
                        """,
                        (scheme_code,),
                    )
                    row = cur.fetchone()
                    if not row:
                        return None
                    return (self._coerce_date(row[0]), float(row[1]))

        with self._lock:
            row = self._sqlite.execute(
                """
                SELECT nav_date, nav
                FROM mutual_fund_nav_history
                WHERE scheme_code = ?
                ORDER BY nav_date DESC
                LIMIT 1
                """,
                (scheme_code,),
            ).fetchone()
        if not row:
            return None
        return (self._coerce_date(row[0]), float(row[1]))

    def nav_history(self, scheme_code: str, start_date: Optional[date] = None, end_date: Optional[date] = None) -> Dict[date, float]:
        clauses = ["scheme_code = ?"] if self._mode == "sqlite" else ["scheme_code = %s"]
        params: List[object] = [scheme_code]
        if start_date:
            clauses.append("nav_date >= ?" if self._mode == "sqlite" else "nav_date >= %s")
            params.append(start_date.isoformat())
        if end_date:
            clauses.append("nav_date <= ?" if self._mode == "sqlite" else "nav_date <= %s")
            params.append(end_date.isoformat())
        sql = (
            "SELECT nav_date, nav FROM mutual_fund_nav_history "
            f"WHERE {' AND '.join(clauses)} ORDER BY nav_date ASC"
        )
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return {}
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    rows = cur.fetchall()
        else:
            with self._lock:
                rows = self._sqlite.execute(sql, tuple(params)).fetchall()
        history: Dict[date, float] = {}
        for row in rows:
            dt = self._coerce_date(row[0] if not isinstance(row, sqlite3.Row) else row["nav_date"])
            val = row[1] if not isinstance(row, sqlite3.Row) else row["nav"]
            if dt is None:
                continue
            history[dt] = float(val)
        return history

    def upsert_nav_history(self, scheme_code: str, points: Iterable[Tuple[date, float]]):
        normalized = []
        for raw_date, raw_nav in points:
            dt = self._coerce_date(raw_date)
            if dt is None or raw_nav is None:
                continue
            normalized.append((scheme_code, dt.isoformat(), float(raw_nav)))
        if not normalized:
            return
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO mutual_fund_nav_history (scheme_code, nav_date, nav)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (scheme_code, nav_date) DO UPDATE
                        SET nav = EXCLUDED.nav
                        """,
                        normalized,
                    )
                conn.commit()
            return
        with self._lock, self._sqlite:
            self._sqlite.executemany(
                """
                INSERT INTO mutual_fund_nav_history (scheme_code, nav_date, nav)
                VALUES (?, ?, ?)
                ON CONFLICT(scheme_code, nav_date) DO UPDATE SET
                    nav = excluded.nav
                """,
                normalized,
            )

    def delete_nav_history(self, scheme_code: str):
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM mutual_fund_nav_history WHERE scheme_code = %s",
                        (scheme_code,),
                    )
                conn.commit()
            return
        with self._lock, self._sqlite:
            self._sqlite.execute(
                "DELETE FROM mutual_fund_nav_history WHERE scheme_code = ?",
                (scheme_code,),
            )

    def latest_benchmark_point(self, benchmark_name: str) -> Optional[Tuple[date, float]]:
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return None
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT trade_date, close_value
                        FROM mutual_fund_benchmark_history
                        WHERE benchmark_name = %s
                        ORDER BY trade_date DESC
                        LIMIT 1
                        """,
                        (benchmark_name,),
                    )
                    row = cur.fetchone()
                    if not row:
                        return None
                    return (self._coerce_date(row[0]), float(row[1]))
        with self._lock:
            row = self._sqlite.execute(
                """
                SELECT trade_date, close_value
                FROM mutual_fund_benchmark_history
                WHERE benchmark_name = ?
                ORDER BY trade_date DESC
                LIMIT 1
                """,
                (benchmark_name,),
            ).fetchone()
        if not row:
            return None
        return (self._coerce_date(row[0]), float(row[1]))

    def benchmark_history(self, benchmark_name: str, start_date: Optional[date] = None, end_date: Optional[date] = None) -> Dict[date, float]:
        clauses = ["benchmark_name = ?"] if self._mode == "sqlite" else ["benchmark_name = %s"]
        params: List[object] = [benchmark_name]
        if start_date:
            clauses.append("trade_date >= ?" if self._mode == "sqlite" else "trade_date >= %s")
            params.append(start_date.isoformat())
        if end_date:
            clauses.append("trade_date <= ?" if self._mode == "sqlite" else "trade_date <= %s")
            params.append(end_date.isoformat())
        sql = (
            "SELECT trade_date, close_value FROM mutual_fund_benchmark_history "
            f"WHERE {' AND '.join(clauses)} ORDER BY trade_date ASC"
        )
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return {}
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    rows = cur.fetchall()
        else:
            with self._lock:
                rows = self._sqlite.execute(sql, tuple(params)).fetchall()
        history: Dict[date, float] = {}
        for row in rows:
            dt = self._coerce_date(row[0] if not isinstance(row, sqlite3.Row) else row["trade_date"])
            val = row[1] if not isinstance(row, sqlite3.Row) else row["close_value"]
            if dt is None:
                continue
            history[dt] = float(val)
        return history

    def upsert_benchmark_history(self, benchmark_name: str, points: Iterable[Tuple[date, float]]):
        normalized = []
        for raw_date, raw_value in points:
            dt = self._coerce_date(raw_date)
            if dt is None or raw_value is None:
                continue
            normalized.append((benchmark_name, dt.isoformat(), float(raw_value)))
        if not normalized:
            return
        if self._mode == "postgres":
            if not self._ensure_postgres_ready():
                return
            with self._postgres_connect() as conn:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO mutual_fund_benchmark_history (benchmark_name, trade_date, close_value)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (benchmark_name, trade_date) DO UPDATE
                        SET close_value = EXCLUDED.close_value
                        """,
                        normalized,
                    )
                conn.commit()
            return
        with self._lock, self._sqlite:
            self._sqlite.executemany(
                """
                INSERT INTO mutual_fund_benchmark_history (benchmark_name, trade_date, close_value)
                VALUES (?, ?, ?)
                ON CONFLICT(benchmark_name, trade_date) DO UPDATE SET
                    close_value = excluded.close_value
                """,
                normalized,
            )
