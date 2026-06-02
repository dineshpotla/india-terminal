"""Daily FII/FPI and DII flow service backed by NSE latest data."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Optional

from .data_engine import IST, NseSession

FII_FLOW_HISTORY_KEY = "fii:flow-history:v1"
NSE_FII_DII_API_URL = "https://www.nseindia.com/api/fiidiiTradeReact"
NSE_FII_DII_SOURCE_URL = "https://www.nseindia.com/reports/fii-dii"


class FiiFlowService:
    """Fetch official NSE daily institutional flow and persist daily rows."""

    def __init__(self, store, nse_session: Optional[NseSession] = None):
        self._store = store
        self._nse = nse_session or NseSession()
        self._lock = threading.Lock()

    @staticmethod
    def _parse_float(value) -> Optional[float]:
        if value is None:
            return None
        try:
            return round(float(str(value).replace(",", "").strip()), 2)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_date(value: str) -> tuple[str, str]:
        raw = str(value or "").strip()
        if not raw:
            return "", ""
        for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw, fmt)
                return dt.strftime("%Y-%m-%d"), dt.strftime("%d %b %Y")
            except ValueError:
                continue
        return raw, raw

    @staticmethod
    def _category(raw: str) -> str:
        value = str(raw or "").upper()
        if "FII" in value or "FPI" in value:
            return "FII"
        if "DII" in value:
            return "DII"
        return value.strip() or "OTHER"

    def _load_history(self) -> list[dict]:
        payload = self._store.load_snapshot(FII_FLOW_HISTORY_KEY) or {}
        history = payload.get("history") if isinstance(payload, dict) else None
        if not isinstance(history, list):
            return []
        return [row for row in history if isinstance(row, dict)]

    def _save_history(self, history: list[dict]):
        self._store.save_snapshot(
            {
                "history": history[-260:],
                "updated_at": datetime.now(IST).isoformat(),
                "source": "NSE",
            },
            FII_FLOW_HISTORY_KEY,
        )

    def _fetch_latest_rows(self) -> list[dict]:
        data = self._nse.get(NSE_FII_DII_API_URL)
        if not isinstance(data, list):
            return []
        rows: list[dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            date_iso, date_label = self._parse_date(str(item.get("date") or ""))
            category = self._category(str(item.get("category") or ""))
            if category not in {"FII", "DII"} or not date_iso:
                continue
            rows.append(
                {
                    "category": category,
                    "date": date_iso,
                    "date_label": date_label,
                    "buy": self._parse_float(item.get("buyValue")),
                    "sell": self._parse_float(item.get("sellValue")),
                    "net": self._parse_float(item.get("netValue")),
                }
            )
        return rows

    @staticmethod
    def _row_from_latest(latest_rows: list[dict]) -> Optional[dict]:
        if not latest_rows:
            return None
        date_iso = latest_rows[0].get("date") or ""
        date_label = latest_rows[0].get("date_label") or date_iso
        row = {
            "date": date_iso,
            "date_label": date_label,
            "fii_buy": None,
            "fii_sell": None,
            "fii_net": None,
            "dii_buy": None,
            "dii_sell": None,
            "dii_net": None,
        }
        for item in latest_rows:
            prefix = "fii" if item.get("category") == "FII" else "dii"
            row[f"{prefix}_buy"] = item.get("buy")
            row[f"{prefix}_sell"] = item.get("sell")
            row[f"{prefix}_net"] = item.get("net")
        row["combined_net"] = round(
            float(row.get("fii_net") or 0) + float(row.get("dii_net") or 0),
            2,
        )
        return row

    @staticmethod
    def _items_from_history_row(row: Optional[dict]) -> list[dict]:
        if not row:
            return []
        return [
            {
                "category": "FII",
                "label": "FII / FPI",
                "date": row.get("date"),
                "date_label": row.get("date_label"),
                "buy": row.get("fii_buy"),
                "sell": row.get("fii_sell"),
                "net": row.get("fii_net"),
            },
            {
                "category": "DII",
                "label": "DII",
                "date": row.get("date"),
                "date_label": row.get("date_label"),
                "buy": row.get("dii_buy"),
                "sell": row.get("dii_sell"),
                "net": row.get("dii_net"),
            },
        ]

    def latest_panel(self) -> dict:
        error = None
        with self._lock:
            history = self._load_history()
            latest_rows = []
            try:
                latest_rows = self._fetch_latest_rows()
            except Exception as exc:
                error = str(exc)

            latest_history_row = self._row_from_latest(latest_rows)
            if latest_history_row:
                by_date = {str(row.get("date") or ""): row for row in history}
                by_date[str(latest_history_row["date"])] = latest_history_row
                history = sorted(
                    [row for row in by_date.values() if row.get("date")],
                    key=lambda row: str(row.get("date") or ""),
                )[-260:]
                self._save_history(history)

            latest = history[-1] if history else latest_history_row
            items = self._items_from_history_row(latest)
            return {
                "source": "NSE",
                "source_url": NSE_FII_DII_SOURCE_URL,
                "latest_date": latest.get("date") if latest else None,
                "latest_date_label": latest.get("date_label") if latest else None,
                "items": items,
                "history": history[-90:],
                "history_count": len(history),
                "updated_at": datetime.now(IST).strftime("%H:%M:%S"),
                "error": error,
            }
