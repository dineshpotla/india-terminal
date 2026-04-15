"""Mutual fund sync, NAV history, and benchmark comparison helpers."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional

import requests


ZERODHA_BASE_URL = "https://api.kite.trade"
AMFI_NAV_ALL_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
AMFI_NAV_HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
NSE_INDEX_HISTORY_URL = "https://www.nseindia.com/api/historicalOR/indicesHistory"
NSE_BASE_URL = "https://www.nseindia.com"

MUTUAL_HOLDINGS_KEY = "mf:holdings"
MUTUAL_INSTRUMENTS_KEY = "mf:zerodha:instruments"
MUTUAL_AMFI_MASTER_KEY = "mf:amfi:navall"
MUTUAL_AMFI_AMC_CODES_KEY = "mf:amfi:amc_codes"

_CATEGORY_ORDER = [
    "flexicap",
    "multicap",
    "large_cap",
    "midcap",
    "smallcap",
    "large_midcap",
    "elss",
    "banking",
    "it",
    "pharma",
    "index",
]

_CATEGORY_BENCHMARKS = {
    "flexicap": ["NIFTY 500"],
    "multicap": ["NIFTY500 MULTICAP 50:25:25", "NIFTY 500"],
    "large_cap": ["NIFTY 50", "NIFTY 100"],
    "midcap": ["NIFTY MIDCAP 150"],
    "smallcap": ["NIFTY SMALLCAP 250"],
    "large_midcap": ["NIFTY LARGEMIDCAP 250"],
    "elss": ["NIFTY 500", "NIFTY 200"],
    "banking": ["NIFTY BANK"],
    "it": ["NIFTY IT"],
    "pharma": ["NIFTY PHARMA"],
}

_INDEX_NAME_CLEANUPS = {
    "NIFTY 50": "NIFTY 50",
    "NIFTY 100": "NIFTY 100",
    "NIFTY 200": "NIFTY 200",
    "NIFTY 500": "NIFTY 500",
    "NIFTY NEXT 50": "NIFTY NEXT 50",
    "NIFTY MIDCAP 150": "NIFTY MIDCAP 150",
    "NIFTY SMALLCAP 250": "NIFTY SMALLCAP 250",
    "NIFTY LARGEMIDCAP 250": "NIFTY LARGEMIDCAP 250",
    "NIFTY500 MULTICAP 50:25:25": "NIFTY500 MULTICAP 50:25:25",
    "NIFTY BANK": "NIFTY BANK",
    "NIFTY IT": "NIFTY IT",
    "NIFTY PHARMA": "NIFTY PHARMA",
}

_AMFI_HISTORY_DATE_FMT = "%d-%b-%Y"
_NSE_HISTORY_DATE_FMT = "%d-%m-%Y"


def _utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _load_snapshot(store, key: str) -> Optional[dict]:
    try:
        return store.load_snapshot(key)
    except Exception:
        return None


def _save_snapshot(store, key: str, payload: dict):
    try:
        store.save_snapshot(payload, key)
    except Exception as exc:
        print(f"[MutualFunds] save {key} failed: {exc}")


def _normalize_name(value: str) -> str:
    text = (value or "").upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return " ".join(text.split())


def _clean_float(value) -> Optional[float]:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def _infer_category(name: str, scheme_type: str = "") -> Optional[str]:
    text = _normalize_name(name)
    coarse = (scheme_type or "").strip().lower()
    if "INDEX FUND" in text or "NIFTY INDEX" in text:
        return "index"
    if "FLEXI CAP" in text or "FLEXICAP" in text:
        return "flexicap"
    if "MULTI CAP" in text or "MULTICAP" in text:
        return "multicap"
    if "LARGE MID CAP" in text or "LARGE AND MID CAP" in text or "LARGE & MID CAP" in text:
        return "large_midcap"
    if "LARGE CAP" in text:
        return "large_cap"
    if "MID CAP" in text or "MIDCAP" in text:
        return "midcap"
    if "SMALL CAP" in text or "SMALLCAP" in text:
        return "smallcap"
    if "ELSS" in text or "TAX" in text:
        return "elss"
    if "BANK" in text or "BANKING" in text or "PSU BANK" in text:
        return "banking"
    if "TECH" in text or re.search(r"\bIT\b", text):
        return "it"
    if "PHARMA" in text or "HEALTHCARE" in text:
        return "pharma"
    if coarse == "elss":
        return "elss"
    if coarse == "equity":
        return "flexicap"
    return None


def _benchmark_suggestions(name: str, category: Optional[str]) -> List[str]:
    text = _normalize_name(name)
    if category == "index":
        for benchmark in _INDEX_NAME_CLEANUPS:
            if benchmark in text:
                return [benchmark]
        if "NIFTY NEXT 50" in text:
            return ["NIFTY NEXT 50"]
    picks = list(_CATEGORY_BENCHMARKS.get(category or "", []))
    if not picks:
        picks = ["NIFTY 500"]
    return picks


def _range_options(first_invested_at: Optional[str]) -> List[dict]:
    opts = [
        {"key": "1y", "label": "1Y"},
        {"key": "3y", "label": "3Y"},
        {"key": "5y", "label": "5Y"},
        {"key": "max", "label": "Since Inception"},
    ]
    if first_invested_at:
        opts.insert(0, {"key": "holding", "label": "Holding Period"})
    return opts


class MutualFundsService:
    """Sync Zerodha Coin holdings and compare fund NAVs to benchmark indices."""

    def __init__(self, store):
        self._store = store
        self._lock = threading.Lock()
        self._kite_api_key = (os.getenv("ZERODHA_API_KEY") or os.getenv("KITE_API_KEY") or "").strip()
        self._kite_access_token = (
            os.getenv("ZERODHA_ACCESS_TOKEN")
            or os.getenv("KITE_ACCESS_TOKEN")
            or os.getenv("ZERODHA_SESSION_TOKEN")
            or ""
        ).strip()

    @property
    def provider_configured(self) -> bool:
        return bool(self._kite_api_key and self._kite_access_token)

    def status(self) -> dict:
        snapshot = _load_snapshot(self._store, MUTUAL_HOLDINGS_KEY) or {}
        holdings = snapshot.get("holdings") or []
        return {
            "provider": snapshot.get("provider") or ("kite_connect" if self.provider_configured else "unconfigured"),
            "configured": self.provider_configured,
            "connected": bool(snapshot.get("synced_at")),
            "synced_at": snapshot.get("synced_at"),
            "holdings_count": len(holdings),
            "equity_symbols_count": len(snapshot.get("equity_symbols") or []),
            "message": None if self.provider_configured else "Set ZERODHA_API_KEY and ZERODHA_ACCESS_TOKEN to enable sync",
        }

    def list_holdings(self) -> dict:
        snapshot = _load_snapshot(self._store, MUTUAL_HOLDINGS_KEY) or {}
        holdings = snapshot.get("holdings") or []
        total_value = round(sum(float(row.get("current_value") or 0.0) for row in holdings), 2)
        total_cost = round(sum(float(row.get("cost_value") or 0.0) for row in holdings), 2)
        return {
            **self.status(),
            "total_value": total_value,
            "total_cost": total_cost,
            "total_pnl": round(total_value - total_cost, 2),
            "holdings": holdings,
        }

    def sync(self) -> dict:
        if not self.provider_configured:
            raise RuntimeError("Zerodha provider is not configured")
        equities = self._kite_get_json("/portfolio/holdings").get("data") or []
        fund_holdings = self._kite_get_json("/mf/holdings").get("data") or []
        instruments = self._kite_instruments_by_isin()
        transformed = []
        for raw in fund_holdings:
            isin = str(raw.get("tradingsymbol") or "").strip().upper()
            instrument = instruments.get(isin) or {}
            fund_name = raw.get("fund") or instrument.get("name") or isin
            category = _infer_category(fund_name, instrument.get("scheme_type") or "")
            average_price = _clean_float(raw.get("average_price")) or 0.0
            last_price = _clean_float(raw.get("last_price")) or 0.0
            quantity = _clean_float(raw.get("quantity")) or 0.0
            current_value = round(quantity * last_price, 2)
            cost_value = round(quantity * average_price, 2)
            transformed.append(
                {
                    "isin": isin,
                    "fund": fund_name,
                    "folio": raw.get("folio"),
                    "average_price": average_price,
                    "last_price": last_price,
                    "last_price_date": raw.get("last_price_date") or instrument.get("last_price_date"),
                    "quantity": quantity,
                    "pnl": _clean_float(raw.get("pnl")) or round(current_value - cost_value, 2),
                    "current_value": current_value,
                    "cost_value": cost_value,
                    "scheme_type": instrument.get("scheme_type"),
                    "plan": instrument.get("plan"),
                    "dividend_type": instrument.get("dividend_type"),
                    "category": category,
                    "benchmark_options": _benchmark_suggestions(fund_name, category),
                    "range_options": _range_options(None),
                }
            )
        transformed.sort(key=lambda row: row.get("current_value", 0.0), reverse=True)
        equity_symbols = []
        for row in equities:
            symbol = str(row.get("tradingsymbol") or "").strip().upper()
            if symbol and symbol not in equity_symbols:
                equity_symbols.append(symbol)
        snapshot = {
            "provider": "kite_connect",
            "synced_at": _utc_now_iso(),
            "equity_symbols": equity_symbols,
            "holdings": transformed,
        }
        _save_snapshot(self._store, MUTUAL_HOLDINGS_KEY, snapshot)
        return snapshot

    def compare(self, isin: str, benchmark: Optional[str], range_key: str = "max") -> dict:
        holdings = self.list_holdings().get("holdings") or []
        target = next((row for row in holdings if str(row.get("isin")).upper() == str(isin or "").upper()), None)
        if not target:
            raise RuntimeError("Mutual fund holding not found")
        benchmark_name = benchmark or (target.get("benchmark_options") or ["NIFTY 500"])[0]
        if benchmark_name not in _INDEX_NAME_CLEANUPS:
            benchmark_name = _INDEX_NAME_CLEANUPS.get(benchmark_name, benchmark_name)
        start_date = self._comparison_start_date(range_key, target.get("first_invested_at"))
        fund_series = self._fund_history(target, start_date)
        benchmark_series = self._benchmark_history(benchmark_name, start_date, date.today())
        common_dates = sorted(set(fund_series) & set(benchmark_series))
        if len(common_dates) < 2:
            raise RuntimeError("Not enough overlapping history to compare this fund with the selected benchmark")
        base_fund = fund_series[common_dates[0]]
        base_bm = benchmark_series[common_dates[0]]
        chart = []
        for dt in common_dates:
            fund_nav = fund_series[dt]
            bench_close = benchmark_series[dt]
            chart.append(
                {
                    "time": int(datetime(dt.year, dt.month, dt.day).timestamp()),
                    "fund": round(100.0 * fund_nav / base_fund, 2),
                    "benchmark": round(100.0 * bench_close / base_bm, 2),
                    "fund_nav": round(fund_nav, 4),
                    "benchmark_close": round(bench_close, 2),
                }
            )
        fund_return = round(chart[-1]["fund"] - 100.0, 2)
        benchmark_return = round(chart[-1]["benchmark"] - 100.0, 2)
        return {
            "holding": target,
            "benchmark": benchmark_name,
            "range": range_key,
            "range_options": _range_options(target.get("first_invested_at")),
            "benchmark_options": target.get("benchmark_options") or _benchmark_suggestions(target.get("fund", ""), target.get("category")),
            "from_date": common_dates[0].isoformat(),
            "to_date": common_dates[-1].isoformat(),
            "points": len(chart),
            "series": chart,
            "fund_return_pct": fund_return,
            "benchmark_return_pct": benchmark_return,
            "alpha_pct": round(fund_return - benchmark_return, 2),
            "source": {
                "fund": "AMFI",
                "benchmark": "NSE",
            },
        }

    def _comparison_start_date(self, range_key: str, first_invested_at: Optional[str] = None) -> date:
        today = date.today()
        if range_key == "holding" and first_invested_at:
            try:
                return datetime.fromisoformat(str(first_invested_at)).date()
            except Exception:
                pass
        if range_key == "1y":
            return today - timedelta(days=366)
        if range_key == "3y":
            return today - timedelta(days=366 * 3)
        if range_key == "5y":
            return today - timedelta(days=366 * 5)
        return date(2000, 1, 1)

    def _kite_headers(self) -> dict:
        return {
            "X-Kite-Version": "3",
            "Authorization": f"token {self._kite_api_key}:{self._kite_access_token}",
            "User-Agent": "IndiaMarketTerminal/1.0",
        }

    def _kite_get_json(self, path: str) -> dict:
        url = ZERODHA_BASE_URL + path
        resp = requests.get(url, headers=self._kite_headers(), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(data.get("message") or f"Kite request failed: {path}")
        return data

    def _kite_instruments_by_isin(self) -> Dict[str, dict]:
        cached = _load_snapshot(self._store, MUTUAL_INSTRUMENTS_KEY) or {}
        if cached.get("as_of") and (time.time() - float(cached.get("_saved_at") or 0.0) < 24 * 60 * 60):
            return cached.get("items") or {}
        resp = requests.get(ZERODHA_BASE_URL + "/mf/instruments", headers=self._kite_headers(), timeout=60)
        resp.raise_for_status()
        text = resp.text
        reader = csv.DictReader(io.StringIO(text))
        items: Dict[str, dict] = {}
        for row in reader:
            isin = str(row.get("tradingsymbol") or "").strip().upper()
            if not isin:
                continue
            items[isin] = {
                "name": row.get("name"),
                "scheme_type": row.get("scheme_type"),
                "plan": row.get("plan"),
                "dividend_type": row.get("dividend_type"),
                "last_price": _clean_float(row.get("last_price")),
                "last_price_date": row.get("last_price_date"),
            }
        payload = {"as_of": _utc_now_iso(), "_saved_at": time.time(), "items": items}
        _save_snapshot(self._store, MUTUAL_INSTRUMENTS_KEY, payload)
        return items

    def _amfi_master(self) -> dict:
        cached = _load_snapshot(self._store, MUTUAL_AMFI_MASTER_KEY) or {}
        cached_names = cached.get("by_name") or {}
        if (
            cached.get("as_of")
            and "SCHEME NAME" not in cached_names
            and (time.time() - float(cached.get("_saved_at") or 0.0) < 12 * 60 * 60)
        ):
            return cached
        text = requests.get(AMFI_NAV_ALL_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=60).text
        by_isin: Dict[str, dict] = {}
        by_name: Dict[str, dict] = {}
        current_house = None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if ";" not in line:
                upper = line.upper()
                if "OPEN ENDED" in upper or "CLOSE ENDED" in upper or "INTERVAL FUND" in upper or "SCHEME CODE" in upper:
                    continue
                current_house = line
                continue
            parts = [part.strip() for part in line.split(";")]
            if len(parts) < 6:
                continue
            if parts[0].upper() == "SCHEME CODE" or parts[3].upper() == "SCHEME NAME":
                continue
            scheme_code = parts[0]
            isin_one = parts[1].upper()
            isin_two = parts[2].upper()
            name = parts[3]
            nav = _clean_float(parts[4])
            nav_date = parts[5]
            entry = {
                "scheme_code": scheme_code,
                "house": current_house,
                "name": name,
                "nav": nav,
                "date": nav_date,
            }
            if isin_one and isin_one != "-":
                by_isin[isin_one] = entry
            if isin_two and isin_two != "-":
                by_isin[isin_two] = entry
            by_name[_normalize_name(name)] = entry
        payload = {
            "as_of": _utc_now_iso(),
            "_saved_at": time.time(),
            "by_isin": by_isin,
            "by_name": by_name,
        }
        _save_snapshot(self._store, MUTUAL_AMFI_MASTER_KEY, payload)
        return payload

    def _resolve_scheme(self, holding: dict) -> dict:
        master = self._amfi_master()
        isin = str(holding.get("isin") or "").upper()
        by_isin = master.get("by_isin") or {}
        if isin in by_isin:
            return by_isin[isin]
        name_key = _normalize_name(holding.get("fund") or "")
        by_name = master.get("by_name") or {}
        if name_key in by_name:
            return by_name[name_key]
        raise RuntimeError(f"Could not resolve AMFI scheme for {holding.get('fund') or isin}")

    def _amfi_amc_codes(self) -> Dict[str, int]:
        cached = _load_snapshot(self._store, MUTUAL_AMFI_AMC_CODES_KEY) or {}
        return {k: int(v) for k, v in (cached.get("items") or {}).items()}

    def _save_amfi_amc_codes(self, mapping: Dict[str, int]):
        _save_snapshot(
            self._store,
            MUTUAL_AMFI_AMC_CODES_KEY,
            {"as_of": _utc_now_iso(), "items": mapping},
        )

    def _resolve_amc_code(self, house: str, sample_scheme_code: str) -> int:
        house_key = _normalize_name(house)
        mapping = self._amfi_amc_codes()
        if house_key in mapping:
            return int(mapping[house_key])
        probe_day = date.today() - timedelta(days=5)
        probe_from = probe_day.strftime(_AMFI_HISTORY_DATE_FMT)
        probe_to = probe_from

        def probe(code: int):
            try:
                text = requests.get(
                    AMFI_NAV_HISTORY_URL,
                    params={"mf": code, "frmdt": probe_from, "todt": probe_to},
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=20,
                ).text
                return code if f"{sample_scheme_code};" in text else None
            except Exception:
                return None

        found = None
        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(probe, code) for code in range(1, 80)]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    found = int(result)
                    break
        if not found:
            raise RuntimeError(f"Could not resolve AMFI AMC code for {house}")
        mapping[house_key] = found
        self._save_amfi_amc_codes(mapping)
        return found

    def _fund_history(self, holding: dict, start_date: date) -> Dict[date, float]:
        scheme = self._resolve_scheme(holding)
        amc_code = self._resolve_amc_code(scheme.get("house") or "", scheme.get("scheme_code") or "")
        key = f"mf:history:{holding.get('isin')}:{start_date.isoformat()}"
        cached = _load_snapshot(self._store, key) or {}
        items = cached.get("items") or {}
        if items:
            return {
                datetime.strptime(dt, "%Y-%m-%d").date(): float(val)
                for dt, val in items.items()
            }
        resp = requests.get(
            AMFI_NAV_HISTORY_URL,
            params={
                "mf": amc_code,
                "frmdt": start_date.strftime(_AMFI_HISTORY_DATE_FMT),
                "todt": date.today().strftime(_AMFI_HISTORY_DATE_FMT),
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=90,
        )
        resp.raise_for_status()
        history: Dict[date, float] = {}
        prefix = f"{scheme.get('scheme_code')};"
        for raw_line in resp.text.splitlines():
            line = raw_line.strip()
            if not line.startswith(prefix):
                continue
            parts = [part.strip() for part in line.split(";")]
            if len(parts) < 8:
                continue
            nav = _clean_float(parts[4])
            try:
                dt = datetime.strptime(parts[7], _AMFI_HISTORY_DATE_FMT).date()
            except Exception:
                continue
            if nav is None:
                continue
            history[dt] = nav
        if not history:
            raise RuntimeError(f"No AMFI history returned for {holding.get('fund')}")
        payload = {
            "as_of": _utc_now_iso(),
            "items": {dt.isoformat(): val for dt, val in sorted(history.items())},
        }
        _save_snapshot(self._store, key, payload)
        return history

    def _benchmark_history(self, benchmark_name: str, start_date: date, end_date: date) -> Dict[date, float]:
        clean_name = _INDEX_NAME_CLEANUPS.get(benchmark_name, benchmark_name)
        key = f"mf:benchmark:{clean_name}:{start_date.isoformat()}:{end_date.isoformat()}"
        cached = _load_snapshot(self._store, key) or {}
        items = cached.get("items") or {}
        if items:
            return {
                datetime.strptime(dt, "%Y-%m-%d").date(): float(val)
                for dt, val in items.items()
            }
        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.nseindia.com/reports-indices-historical-index-data",
        }
        session.get(NSE_BASE_URL, headers=headers, timeout=30)
        cursor = start_date
        history: Dict[date, float] = {}
        while cursor <= end_date:
            chunk_end = min(cursor + timedelta(days=360), end_date)
            resp = session.get(
                NSE_INDEX_HISTORY_URL,
                params={
                    "indexType": clean_name,
                    "from": cursor.strftime(_NSE_HISTORY_DATE_FMT),
                    "to": chunk_end.strftime(_NSE_HISTORY_DATE_FMT),
                },
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            for row in data.get("data") or []:
                raw_date = row.get("EOD_TIMESTAMP")
                close_val = _clean_float(row.get("EOD_CLOSE_INDEX_VAL"))
                if not raw_date or close_val is None:
                    continue
                try:
                    dt = datetime.strptime(raw_date, "%d-%b-%Y").date()
                except Exception:
                    continue
                history[dt] = close_val
            cursor = chunk_end + timedelta(days=1)
        if not history:
            raise RuntimeError(f"No NSE history returned for {clean_name}")
        _save_snapshot(
            self._store,
            key,
            {"as_of": _utc_now_iso(), "items": {dt.isoformat(): val for dt, val in sorted(history.items())}},
        )
        return history
