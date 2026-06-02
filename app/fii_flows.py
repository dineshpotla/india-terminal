"""Daily FII/FPI and DII flow service backed by NSE latest data."""

from __future__ import annotations

import html
import json
import re
import threading
from datetime import datetime, timedelta
from typing import Optional

import requests

from .data_engine import IST, NseSession

FII_FLOW_HISTORY_KEY = "fii:flow-history:v1"
FII_FLOW_ARCHIVE_KEY = "fii:flow-archive:v1"
NSE_FII_DII_API_URL = "https://www.nseindia.com/api/fiidiiTradeReact"
NSE_FII_DII_SOURCE_URL = "https://www.nseindia.com/reports/fii-dii"
GROWW_FII_DII_SOURCE_URL = "https://groww.in/fii-dii-data"
MONEYCONTROL_FII_DII_SOURCE_URL = "https://www.moneycontrol.com/markets/fii-dii-data/"
MONEYCONTROL_FII_DII_ARCHIVE_URL = "https://api.moneycontrol.com/swiftapi/v1/fii_dii/cash"
FII_CHART_RANGES = {"1m", "3m", "6m", "1y", "5y", "max"}
FII_CHART_RANGE_DAYS = {
    "1m": 31,
    "3m": 93,
    "6m": 186,
    "1y": 366,
    "5y": 5 * 366,
}


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

    def _load_archive(self) -> tuple[list[dict], str]:
        payload = self._store.load_snapshot(FII_FLOW_ARCHIVE_KEY) or {}
        history = payload.get("history") if isinstance(payload, dict) else None
        fetched_on = str(payload.get("fetched_on") or "") if isinstance(payload, dict) else ""
        if not isinstance(history, list):
            return [], fetched_on
        return [row for row in history if isinstance(row, dict)], fetched_on

    def _save_archive(self, history: list[dict], fetched_on: str):
        self._store.save_snapshot(
            {
                "history": history,
                "fetched_on": fetched_on,
                "updated_at": datetime.now(IST).isoformat(),
                "source": "Moneycontrol",
            },
            FII_FLOW_ARCHIVE_KEY,
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

    def _fetch_recent_history_rows(self) -> list[dict]:
        """Backfill recent rows from Groww's page data, then NSE latest overwrites today."""
        try:
            resp = requests.get(
                GROWW_FII_DII_SOURCE_URL,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
                timeout=15,
            )
            resp.raise_for_status()
        except Exception as exc:
            print(f"[FII] Groww recent history fetch failed: {exc}")
            return []
        match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            resp.text,
            re.S,
        )
        if not match:
            return []
        try:
            payload = json.loads(html.unescape(match.group(1)))
            raw_rows = payload["props"]["pageProps"]["initialData"]
        except Exception as exc:
            print(f"[FII] Groww recent history parse failed: {exc}")
            return []
        rows: list[dict] = []
        for item in raw_rows:
            if not isinstance(item, dict):
                continue
            date_iso, date_label = self._parse_date(str(item.get("date") or ""))
            fii = item.get("fii") if isinstance(item.get("fii"), dict) else {}
            dii = item.get("dii") if isinstance(item.get("dii"), dict) else {}
            if not date_iso:
                continue
            row = {
                "date": date_iso,
                "date_label": date_label,
                "fii_buy": self._parse_float(fii.get("grossBuy")),
                "fii_sell": self._parse_float(fii.get("grossSell")),
                "fii_net": self._parse_float(fii.get("netBuySell")),
                "dii_buy": self._parse_float(dii.get("grossBuy")),
                "dii_sell": self._parse_float(dii.get("grossSell")),
                "dii_net": self._parse_float(dii.get("netBuySell")),
                "source": "Groww",
            }
            row["combined_net"] = round(
                float(row.get("fii_net") or 0) + float(row.get("dii_net") or 0),
                2,
            )
            rows.append(row)
        return sorted(rows, key=lambda row: str(row.get("date") or ""))

    @staticmethod
    def _read_varint(payload: bytes, offset: int) -> tuple[int, int]:
        value = 0
        shift = 0
        while offset < len(payload):
            byte = payload[offset]
            offset += 1
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value, offset
            shift += 7
        raise ValueError("truncated protobuf varint")

    @classmethod
    def _protobuf_fields(cls, payload: bytes) -> list[tuple[int, int, object]]:
        fields: list[tuple[int, int, object]] = []
        offset = 0
        while offset < len(payload):
            tag, offset = cls._read_varint(payload, offset)
            field_number = tag >> 3
            wire_type = tag & 0x07
            if wire_type == 0:
                value, offset = cls._read_varint(payload, offset)
            elif wire_type == 1:
                value = payload[offset : offset + 8]
                offset += 8
            elif wire_type == 2:
                length, offset = cls._read_varint(payload, offset)
                value = payload[offset : offset + length]
                offset += length
            elif wire_type == 5:
                value = payload[offset : offset + 4]
                offset += 4
            else:
                raise ValueError(f"unsupported protobuf wire type {wire_type}")
            fields.append((field_number, wire_type, value))
        return fields

    @classmethod
    def _decode_moneycontrol_archive(cls, payload: bytes) -> list[dict]:
        archive_payload = next(
            (
                value
                for field_number, wire_type, value in cls._protobuf_fields(payload)
                if field_number == 3 and wire_type == 2 and isinstance(value, bytes)
            ),
            b"",
        )
        rows: list[dict] = []
        for field_number, wire_type, value in cls._protobuf_fields(archive_payload):
            if field_number != 1 or wire_type != 2 or not isinstance(value, bytes):
                continue
            raw = {
                nested_number: nested_value.decode("utf-8", errors="replace")
                for nested_number, nested_wire_type, nested_value in cls._protobuf_fields(value)
                if nested_wire_type == 2 and isinstance(nested_value, bytes)
            }
            date_iso, date_label = cls._parse_date(str(raw.get(1) or ""))
            fii_net = cls._parse_float(raw.get(4))
            dii_net = cls._parse_float(raw.get(7))
            if not date_iso or fii_net is None or dii_net is None:
                continue
            rows.append(
                {
                    "date": date_iso,
                    "date_label": date_label,
                    "fii_buy": cls._parse_float(raw.get(2)),
                    "fii_sell": cls._parse_float(raw.get(3)),
                    "fii_net": fii_net,
                    "dii_buy": cls._parse_float(raw.get(5)),
                    "dii_sell": cls._parse_float(raw.get(6)),
                    "dii_net": dii_net,
                    "combined_net": round(fii_net + dii_net, 2),
                    "nifty_close": cls._parse_float(raw.get(8)),
                    "nifty_prev_close": cls._parse_float(raw.get(9)),
                    "source": "Moneycontrol",
                }
            )
        return sorted(rows, key=lambda row: str(row.get("date") or ""))

    def _fetch_archive_history(self) -> list[dict]:
        """Fetch complete daily cash-flow history once, then persist it locally."""
        try:
            resp = requests.get(
                MONEYCONTROL_FII_DII_ARCHIVE_URL,
                params={
                    "section": "daily",
                    "startDate": "2000-01-01",
                    "endDate": datetime.now(IST).strftime("%Y-%m-%d"),
                },
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/x-protobuf"},
                timeout=25,
            )
            resp.raise_for_status()
            return self._decode_moneycontrol_archive(resp.content)
        except Exception as exc:
            print(f"[FII] Moneycontrol daily archive fetch failed: {exc}")
            return []

    @staticmethod
    def _merge_history(*groups: list[dict]) -> list[dict]:
        by_date: dict[str, dict] = {}
        for group in groups:
            for row in group:
                date_iso = str(row.get("date") or "")
                if not date_iso:
                    continue
                previous = by_date.get(date_iso, {})
                merged = dict(previous)
                merged.update({key: value for key, value in row.items() if value is not None})
                by_date[date_iso] = merged
        return sorted(by_date.values(), key=lambda row: str(row.get("date") or ""))

    @staticmethod
    def _slice_chart_history(history: list[dict], chart_range: str) -> list[dict]:
        days = FII_CHART_RANGE_DAYS.get(chart_range)
        if not days or not history:
            return history
        cutoff = datetime.now(IST).date() - timedelta(days=days)
        rows: list[dict] = []
        for row in history:
            try:
                if datetime.strptime(str(row.get("date") or ""), "%Y-%m-%d").date() >= cutoff:
                    rows.append(row)
            except ValueError:
                continue
        return rows

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

    def latest_panel(self, chart_range: str = "1m") -> dict:
        normalized_range = chart_range if chart_range in FII_CHART_RANGES else "1m"
        error = None
        with self._lock:
            today = datetime.now(IST).strftime("%Y-%m-%d")
            history, archive_fetched_on = self._load_archive()
            if not history:
                history = self._load_history()
            latest_rows = []
            try:
                latest_rows = self._fetch_latest_rows()
            except Exception as exc:
                error = str(exc)

            latest_history_row = self._row_from_latest(latest_rows)
            latest_official_date = str((latest_history_row or {}).get("date") or "")
            latest_archive_date = str((history[-1] if history else {}).get("date") or "")
            should_refresh_archive = (
                len(history) < 1000
                or archive_fetched_on != today
                or (latest_official_date and latest_official_date > latest_archive_date)
            )
            if should_refresh_archive:
                archive_rows = self._fetch_archive_history()
                if archive_rows:
                    history = self._merge_history(history, archive_rows)
                    archive_fetched_on = today
            if len(history) < 5:
                history = self._merge_history(history, self._fetch_recent_history_rows())
            if latest_history_row:
                history = self._merge_history(history, [latest_history_row])
            if history:
                self._save_archive(history, archive_fetched_on or today)
                self._save_history(history)

            latest = history[-1] if history else latest_history_row
            items = self._items_from_history_row(latest)
            chart_history = self._slice_chart_history(history, normalized_range)
            return {
                "source": "NSE",
                "source_url": NSE_FII_DII_SOURCE_URL,
                "history_source": "Moneycontrol",
                "history_source_url": MONEYCONTROL_FII_DII_SOURCE_URL,
                "chart_source": "Moneycontrol",
                "chart_source_url": MONEYCONTROL_FII_DII_SOURCE_URL,
                "chart_range": normalized_range,
                "chart_bucket": "1 day",
                "chart": chart_history,
                "chart_count": len(chart_history),
                "latest_date": latest.get("date") if latest else None,
                "latest_date_label": latest.get("date_label") if latest else None,
                "items": items,
                "history": history[-90:],
                "history_count": len(history),
                "updated_at": datetime.now(IST).strftime("%H:%M:%S"),
                "error": error,
            }
