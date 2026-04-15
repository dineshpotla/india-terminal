"""Manual mutual-fund watchlist, AMFI NAV history, and benchmark comparison."""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import requests


AMFI_NAV_ALL_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
AMFI_NAV_HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
NSE_INDEX_HISTORY_URL = "https://www.nseindia.com/api/historicalOR/indicesHistory"
NSE_BASE_URL = "https://www.nseindia.com"

MUTUAL_AMFI_MASTER_KEY = "mf:amfi:navall"
MUTUAL_AMFI_AMC_CODES_KEY = "mf:amfi:amc_codes"

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

_DIVERSIFIED_EQUITY_CATEGORIES = {
    "flexicap",
    "multicap",
    "large_cap",
    "midcap",
    "smallcap",
    "large_midcap",
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


def _normalize_scheme_code(value: str) -> str:
    return re.sub(r"[^0-9]", "", str(value or "").strip())


def _clean_float(value) -> Optional[float]:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def _infer_category(name: str) -> Optional[str]:
    text = _normalize_name(name)
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


def _range_options() -> List[dict]:
    return [
        {"key": "1y", "label": "1Y"},
        {"key": "3y", "label": "3Y"},
        {"key": "5y", "label": "5Y"},
        {"key": "max", "label": "Since Inception"},
    ]


def _query_category_hints(query_name: str) -> set[str]:
    text = f" {query_name} "
    hints: set[str] = set()
    patterns = {
        "flexicap": (" FLEXI CAP ", " FLEXICAP "),
        "multicap": (" MULTI CAP ", " MULTICAP "),
        "large_midcap": (" LARGE MID CAP ", " LARGE AND MID CAP ", " LARGE MIDCAP ", " LARGE MID "),
        "large_cap": (" LARGE CAP ",),
        "midcap": (" MID CAP ", " MIDCAP "),
        "smallcap": (" SMALL CAP ", " SMALLCAP "),
        "elss": (" ELSS ", " TAX SAVER ", " TAX SAVING "),
        "banking": (" BANK ", " BANKING "),
        "it": (" IT ", " TECHNOLOGY ", " TECH "),
        "pharma": (" PHARMA ", " HEALTHCARE "),
        "index": (" INDEX ", " ETF ", " NIFTY "),
    }
    for category, needles in patterns.items():
        if any(needle in text for needle in needles):
            hints.add(category)
    return hints


def _scheme_search_bucket(name: str, category: Optional[str], category_hints: set[str]) -> tuple:
    text = _normalize_name(name)
    is_direct = " DIRECT " in f" {text} "
    is_growth = " GROWTH " in f" {text} " and " IDCW " not in f" {text} " and " DIVIDEND " not in f" {text} "
    is_idcw = any(token in text for token in ("IDCW", "DIVIDEND", "MONTHLY", "DAILY", "WEEKLY", "QUARTERLY"))
    debt_like = any(
        token in text
        for token in (
            "LIQUID",
            "GILT",
            "OVERNIGHT",
            "ARBITRAGE",
            "ULTRA SHORT",
            "LOW DURATION",
            "MONEY MARKET",
            "SHORT TERM",
            "CORPORATE BOND",
            "BANKING AND PSU",
            "BANKING PSU",
            "TREASURY",
            "SAVINGS",
            "CHILDREN",
            "INCOME",
            "DYNAMIC BOND",
            "FLOATER",
            "FLOATING RATE",
        )
    )
    is_etf = " ETF " in f" {text} " or " EXCHANGE TRADED FUND " in text

    if category_hints:
        category_rank = 0 if category in category_hints else 1
    elif category in _DIVERSIFIED_EQUITY_CATEGORIES:
        category_rank = 0
    elif category in {"elss", "banking", "it", "pharma", "index"}:
        category_rank = 1
    else:
        category_rank = 2

    debt_rank = 1 if debt_like else 0
    etf_rank = 1 if is_etf else 0
    idcw_rank = 1 if is_idcw else 0
    direct_rank = 0 if is_direct else 1
    growth_rank = 0 if is_growth else 1
    return (
        category_rank,
        debt_rank,
        etf_rank,
        idcw_rank,
        direct_rank,
        growth_rank,
    )


class MutualFundsService:
    """Manual mutual-fund watchlist backed by AMFI and NSE data."""

    def __init__(self, store, watchlist_store):
        self._store = store
        self._watchlist_store = watchlist_store

    def search(self, query: str, limit: int = 20) -> List[dict]:
        raw_query = str(query or "").strip()
        query_digits = _normalize_scheme_code(raw_query)
        query_name = _normalize_name(raw_query)
        if len(query_name) < 2 and len(query_digits) < 2:
            return []
        category_hints = _query_category_hints(query_name)
        master = self._amfi_master()
        matches = []
        for scheme in master.get("schemes") or []:
            name_key = scheme.get("normalized_name") or ""
            scheme_name = scheme.get("name") or ""
            code = scheme.get("scheme_code") or ""
            category = _infer_category(scheme_name)
            if query_digits and code.startswith(query_digits):
                score = (0 if code == query_digits else 1, len(scheme.get("name") or ""))
            elif query_name and query_name in name_key:
                starts = 0 if name_key.startswith(query_name) else 1
                word_hit = 0 if any(part.startswith(query_name) for part in name_key.split()) else 1
                bucket = _scheme_search_bucket(scheme_name, category, category_hints)
                score = (2, starts, word_hit, *bucket, len(scheme_name))
            else:
                continue
            matches.append((score, scheme))
        matches.sort(key=lambda item: item[0])
        tracked_codes = {item.get("scheme_code") for item in self._watchlist_store.list_entries()}
        return [
            {
                **self._tracked_entry_from_scheme(scheme),
                "tracked": scheme.get("scheme_code") in tracked_codes,
            }
            for _, scheme in matches[: max(1, min(limit, 50))]
        ]

    def list_watchlist(self) -> dict:
        items = [self._enrich_tracked_entry(entry) for entry in self._watchlist_store.list_entries()]
        return {
            "items": items,
            "count": len(items),
            "storage": self._watchlist_store.storage_mode,
            "durable": self._watchlist_store.durable,
            "shared": True,
        }

    def add(self, scheme_code: str) -> dict:
        scheme = self._resolve_scheme_by_code(scheme_code)
        self._watchlist_store.add_entry(self._tracked_entry_from_scheme(scheme))
        return self.list_watchlist()

    def remove(self, scheme_code: str) -> dict:
        code = _normalize_scheme_code(scheme_code)
        if not code:
            raise RuntimeError("Invalid scheme code")
        self._watchlist_store.remove_entry(code)
        return self.list_watchlist()

    def compare(self, scheme_code: str, benchmark: Optional[str], range_key: str = "max") -> dict:
        code = _normalize_scheme_code(scheme_code)
        target = self._watchlist_store.get_entry(code)
        if not target:
            raise RuntimeError("Mutual fund not found in watchlist")
        target = self._enrich_tracked_entry(target)
        benchmark_name = benchmark or (target.get("benchmark_options") or ["NIFTY 500"])[0]
        benchmark_name = _INDEX_NAME_CLEANUPS.get(benchmark_name, benchmark_name)
        start_date = self._comparison_start_date(range_key)
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
            "fund": target,
            "benchmark": benchmark_name,
            "range": range_key,
            "range_options": _range_options(),
            "benchmark_options": target.get("benchmark_options") or _benchmark_suggestions(target.get("scheme_name", ""), target.get("category")),
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

    def _comparison_start_date(self, range_key: str) -> date:
        today = date.today()
        if range_key == "1y":
            return today - timedelta(days=366)
        if range_key == "3y":
            return today - timedelta(days=366 * 3)
        if range_key == "5y":
            return today - timedelta(days=366 * 5)
        return date(2000, 1, 1)

    def _tracked_entry_from_scheme(self, scheme: dict) -> dict:
        category = _infer_category(scheme.get("name") or "")
        return {
            "scheme_code": scheme.get("scheme_code"),
            "scheme_name": scheme.get("name"),
            "isin_primary": scheme.get("isin_primary"),
            "category": category,
            "benchmark_options": _benchmark_suggestions(scheme.get("name") or "", category),
            "latest_nav": scheme.get("nav"),
            "latest_nav_date": scheme.get("date"),
        }

    def _enrich_tracked_entry(self, entry: dict) -> dict:
        scheme = self._resolve_scheme_by_code(entry.get("scheme_code") or "")
        merged = dict(entry)
        merged.update(
            {
                "scheme_name": scheme.get("name"),
                "isin_primary": scheme.get("isin_primary"),
                "latest_nav": scheme.get("nav"),
                "latest_nav_date": scheme.get("date"),
            }
        )
        if not merged.get("category"):
            merged["category"] = _infer_category(merged.get("scheme_name") or "")
        if not merged.get("benchmark_options"):
            merged["benchmark_options"] = _benchmark_suggestions(
                merged.get("scheme_name") or "",
                merged.get("category"),
            )
        return merged

    def _amfi_master(self) -> dict:
        cached = _load_snapshot(self._store, MUTUAL_AMFI_MASTER_KEY) or {}
        cached_codes = cached.get("by_scheme_code") or {}
        if cached.get("as_of") and cached_codes and (time.time() - float(cached.get("_saved_at") or 0.0) < 12 * 60 * 60):
            return cached
        text = requests.get(AMFI_NAV_ALL_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=60).text
        by_scheme_code: Dict[str, dict] = {}
        schemes: List[dict] = []
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
            scheme_code = _normalize_scheme_code(parts[0])
            if not scheme_code:
                continue
            isin_one = parts[1].upper()
            isin_two = parts[2].upper()
            name = parts[3]
            entry = {
                "scheme_code": scheme_code,
                "house": current_house,
                "name": name,
                "nav": _clean_float(parts[4]),
                "date": parts[5],
                "isin_primary": isin_one if isin_one and isin_one != "-" else (isin_two if isin_two and isin_two != "-" else None),
                "isin_secondary": isin_two if isin_two and isin_two != "-" else None,
                "normalized_name": _normalize_name(name),
            }
            by_scheme_code[scheme_code] = entry
            schemes.append(entry)
        schemes.sort(key=lambda item: item.get("name") or "")
        payload = {
            "as_of": _utc_now_iso(),
            "_saved_at": time.time(),
            "by_scheme_code": by_scheme_code,
            "schemes": schemes,
        }
        _save_snapshot(self._store, MUTUAL_AMFI_MASTER_KEY, payload)
        return payload

    def _resolve_scheme_by_code(self, scheme_code: str) -> dict:
        code = _normalize_scheme_code(scheme_code)
        master = self._amfi_master()
        scheme = (master.get("by_scheme_code") or {}).get(code)
        if scheme:
            return scheme
        raise RuntimeError(f"Could not resolve AMFI scheme for code {scheme_code}")

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

    def _fund_history(self, fund: dict, start_date: date) -> Dict[date, float]:
        scheme = self._resolve_scheme_by_code(fund.get("scheme_code") or "")
        amc_code = self._resolve_amc_code(scheme.get("house") or "", scheme.get("scheme_code") or "")
        key = f"mf:history:{scheme.get('scheme_code')}:{start_date.isoformat()}"
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
            raise RuntimeError(f"No AMFI history returned for {fund.get('scheme_name') or scheme.get('name')}")
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
