"""Manual mutual-fund watchlist, AMFI NAV history, and benchmark comparison."""

from __future__ import annotations

import hashlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import requests


AMFI_NAV_ALL_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
AMFI_NAV_HISTORY_URL = "https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
NSE_INDEX_HISTORY_URL = "https://www.nseindia.com/api/historicalOR/indicesHistory"
NSE_BASE_URL = "https://www.nseindia.com"
MFAPI_HISTORY_URL = "https://api.mfapi.in/mf/{scheme_code}"

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
MF_COMPARE_MAX_RENDER_POINTS = 240
MF_MULTI_COMPARE_MAX_RENDER_POINTS = 180
MF_WATCHLIST_SYNC_INTERVAL_SECONDS = 15 * 60
MF_INCREMENTAL_HISTORY_LOOKBACK_DAYS = 7


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


def _parse_date_value(value) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", _AMFI_HISTORY_DATE_FMT, "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            continue
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


def _downsample_compare_series(points: List[dict], max_points: int = MF_COMPARE_MAX_RENDER_POINTS) -> List[dict]:
    if len(points) <= max_points:
        return list(points)
    if max_points <= 2:
        return [points[0], points[-1]]
    step = (len(points) - 1) / float(max_points - 1)
    sampled: List[dict] = []
    seen_times = set()
    for idx in range(max_points):
        point = points[int(round(idx * step))]
        time_key = point.get("time")
        if time_key in seen_times:
            continue
        seen_times.add(time_key)
        sampled.append(point)
    if sampled[-1].get("time") != points[-1].get("time"):
        sampled[-1] = points[-1]
    return sampled


def _series_point(dt: date, value: float) -> dict:
    return {
        "time": int(datetime(dt.year, dt.month, dt.day).timestamp()),
        "value": round(float(value), 2),
    }


def _raw_series_row(dt: date, value: float) -> list:
    return [int(datetime(dt.year, dt.month, dt.day).timestamp()), round(float(value), 4)]


def _rebased_chart_data(dates: List[date], series: Dict[date, float], max_points: int) -> List[dict]:
    ordered_dates = [dt for dt in dates if dt in series]
    if len(ordered_dates) < 2:
        return []
    base = float(series[ordered_dates[0]] or 0)
    if not base:
        return []
    points = [
        {
            "time": int(datetime(dt.year, dt.month, dt.day).timestamp()),
            "value": round((float(series[dt]) / base) * 100, 2),
        }
        for dt in ordered_dates
    ]
    return _downsample_compare_series(points, max_points)


def _chart_return_pct(points: List[dict]) -> float:
    if not points:
        return 0.0
    return round(float(points[-1].get("value") or 0) - 100.0, 2)


def _cache_key(prefix: str, *parts: str) -> str:
    joined = "|".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _multi_compare_point_budget(selected_count: int) -> int:
    count = max(1, int(selected_count or 0))
    if count <= 2:
        return MF_MULTI_COMPARE_MAX_RENDER_POINTS
    if count <= 4:
        return 144
    if count <= 6:
        return 120
    return 96


def _hydrate_master_payload(payload: Optional[dict]) -> dict:
    payload = payload or {}
    schemes = payload.get("schemes") or []
    if not schemes:
        by_scheme_code = payload.get("by_scheme_code") or {}
        if isinstance(by_scheme_code, dict):
            schemes = list(by_scheme_code.values())
            schemes.sort(key=lambda item: item.get("name") or "")
    by_scheme_code = {}
    for scheme in schemes:
        code = _normalize_scheme_code(scheme.get("scheme_code") or "")
        if not code:
            continue
        by_scheme_code[code] = scheme
    return {
        "as_of": payload.get("as_of"),
        "_saved_at": float(payload.get("_saved_at") or 0.0),
        "schemes": schemes,
        "by_scheme_code": by_scheme_code,
    }


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
        self._master_lock = threading.Lock()
        self._master_cache: Optional[dict] = None
        self._watchlist_sync_lock = threading.Lock()
        self._watchlist_sync_checked_at = 0.0

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
        self._sync_watchlist_daily()
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
        entry = self._tracked_entry_from_scheme(scheme)
        self._watchlist_store.add_entry(entry)
        try:
            self._ensure_fund_history_current(entry, self._comparison_start_date("max"))
        except Exception as exc:
            print(f"[MutualFunds] initial history backfill failed for {scheme_code}: {exc}")
        return self.list_watchlist()

    def remove(self, scheme_code: str) -> dict:
        code = _normalize_scheme_code(scheme_code)
        if not code:
            raise RuntimeError("Invalid scheme code")
        self._watchlist_store.remove_entry(code)
        self._watchlist_store.delete_nav_history(code)
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
        fund_series = self._ensure_fund_history_current(target, start_date)
        benchmark_series = self._ensure_benchmark_history_current(benchmark_name, start_date, date.today())
        common_dates = sorted(set(fund_series) & set(benchmark_series))
        if len(common_dates) < 2:
            raise RuntimeError("Not enough overlapping history to compare this fund with the selected benchmark")
        fund_chart_data = _rebased_chart_data(common_dates, fund_series, MF_COMPARE_MAX_RENDER_POINTS)
        benchmark_chart_data = _rebased_chart_data(common_dates, benchmark_series, MF_COMPARE_MAX_RENDER_POINTS)
        if not fund_chart_data or not benchmark_chart_data:
            raise RuntimeError("Unable to build comparison series")
        fund_return_pct = _chart_return_pct(fund_chart_data)
        benchmark_return_pct = _chart_return_pct(benchmark_chart_data)
        payload = {
            "fund": target,
            "benchmark": benchmark_name,
            "range": range_key,
            "range_options": _range_options(),
            "benchmark_options": target.get("benchmark_options") or _benchmark_suggestions(target.get("scheme_name", ""), target.get("category")),
            "from_date": common_dates[0].isoformat(),
            "to_date": common_dates[-1].isoformat(),
            "points": len(common_dates),
            "render_points": min(len(fund_chart_data), len(benchmark_chart_data)),
            "fund_chart_data": fund_chart_data,
            "benchmark_chart_data": benchmark_chart_data,
            "fund_return_pct": fund_return_pct,
            "benchmark_return_pct": benchmark_return_pct,
            "alpha_pct": round(fund_return_pct - benchmark_return_pct, 2),
            "source": {
                "fund": "Stored NAV",
                "benchmark": "Stored Benchmark",
            },
        }
        return payload

    def compare_many(self, scheme_codes: List[str], range_key: str = "max") -> dict:
        normalized_codes = []
        seen = set()
        for raw_code in scheme_codes or []:
            code = _normalize_scheme_code(raw_code)
            if not code or code in seen:
                continue
            seen.add(code)
            normalized_codes.append(code)
        if not normalized_codes:
            raise RuntimeError("No mutual funds selected")

        start_date = self._comparison_start_date(range_key)
        selected_map = {
            str(entry.get("scheme_code") or "").strip(): self._enrich_tracked_entry(entry)
            for entry in self._watchlist_store.list_entries()
        }
        items = []
        earliest = None
        latest = None
        for code in normalized_codes:
            target = selected_map.get(code)
            if not target:
                continue
            history = self._ensure_fund_history_current(target, start_date)
            dates = sorted(history)
            if len(dates) < 2:
                continue
            chart_data = _rebased_chart_data(
                dates,
                history,
                _multi_compare_point_budget(len(normalized_codes)),
            )
            if len(chart_data) < 2:
                continue
            first_dt = dates[0]
            last_dt = dates[-1]
            earliest = first_dt if earliest is None or first_dt < earliest else earliest
            latest = last_dt if latest is None or last_dt > latest else latest
            items.append(
                {
                    "scheme_code": target.get("scheme_code"),
                    "scheme_name": target.get("scheme_name"),
                    "category": target.get("category"),
                    "latest_nav": target.get("latest_nav"),
                    "latest_nav_date": target.get("latest_nav_date"),
                    "points": len(dates),
                    "render_points": len(chart_data),
                    "return_pct": _chart_return_pct(chart_data),
                    "chart_data": chart_data,
                }
            )

        if not items:
            raise RuntimeError("Not enough NAV history to chart the selected funds")

        payload = {
            "range": range_key,
            "range_options": _range_options(),
            "selected_count": len(items),
            "from_date": earliest.isoformat() if earliest else None,
            "to_date": latest.isoformat() if latest else None,
            "items": items,
            "source": {"fund": "Stored NAV"},
        }
        return payload

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
        latest_db = self._watchlist_store.latest_nav_point(str(entry.get("scheme_code") or "").strip())
        merged.update(
            {
                "scheme_name": scheme.get("name"),
                "isin_primary": scheme.get("isin_primary"),
                "latest_nav": float(latest_db[1]) if latest_db else scheme.get("nav"),
                "latest_nav_date": latest_db[0].isoformat() if latest_db and latest_db[0] else scheme.get("date"),
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
        now = time.time()
        with self._master_lock:
            if self._master_cache and self._master_cache.get("schemes") and (now - float(self._master_cache.get("_saved_at") or 0.0) < 12 * 60 * 60):
                return self._master_cache

        raw_cached = _load_snapshot(self._store, MUTUAL_AMFI_MASTER_KEY) or {}
        cached = _hydrate_master_payload(raw_cached)
        if raw_cached.get("by_scheme_code") and cached.get("schemes"):
            _save_snapshot(
                self._store,
                MUTUAL_AMFI_MASTER_KEY,
                {
                    "as_of": cached.get("as_of"),
                    "_saved_at": cached.get("_saved_at") or now,
                    "schemes": cached.get("schemes") or [],
                },
            )
        if cached.get("as_of") and cached.get("schemes") and (now - float(cached.get("_saved_at") or 0.0) < 12 * 60 * 60):
            with self._master_lock:
                self._master_cache = cached
            return cached

        text = requests.get(AMFI_NAV_ALL_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=60).text
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
            schemes.append(entry)
        schemes.sort(key=lambda item: item.get("name") or "")
        payload = {
            "as_of": _utc_now_iso(),
            "_saved_at": now,
            "schemes": schemes,
        }
        _save_snapshot(self._store, MUTUAL_AMFI_MASTER_KEY, payload)
        hydrated = _hydrate_master_payload(payload)
        with self._master_lock:
            self._master_cache = hydrated
        return hydrated

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

    def _sync_watchlist_daily(self, force: bool = False):
        now = time.time()
        if not force and now - self._watchlist_sync_checked_at < MF_WATCHLIST_SYNC_INTERVAL_SECONDS:
            return
        with self._watchlist_sync_lock:
            now = time.time()
            if not force and now - self._watchlist_sync_checked_at < MF_WATCHLIST_SYNC_INTERVAL_SECONDS:
                return
            self._watchlist_sync_checked_at = now
            for entry in self._watchlist_store.list_entries():
                try:
                    self._ensure_latest_fund_nav(entry)
                except Exception as exc:
                    print(f"[MutualFunds] watchlist daily sync failed for {entry.get('scheme_code')}: {exc}")

    def _ensure_latest_fund_nav(self, fund: dict):
        scheme = self._resolve_scheme_by_code(fund.get("scheme_code") or "")
        code = str(scheme.get("scheme_code") or "").strip()
        master_nav = _clean_float(scheme.get("nav"))
        master_date = _parse_date_value(scheme.get("date"))
        latest_db = self._watchlist_store.latest_nav_point(code)
        if master_date and master_nav is not None:
            if latest_db and latest_db[0] and latest_db[0] >= master_date:
                return
            if latest_db and latest_db[0]:
                fetch_from = max(latest_db[0] - timedelta(days=MF_INCREMENTAL_HISTORY_LOOKBACK_DAYS), master_date - timedelta(days=MF_INCREMENTAL_HISTORY_LOOKBACK_DAYS))
                try:
                    self._fetch_and_store_fund_history(scheme, fetch_from, date.today())
                    return
                except Exception as exc:
                    print(f"[MutualFunds] incremental NAV sync fallback for {code}: {exc}")
            self._watchlist_store.upsert_nav_history(code, [(master_date, master_nav)])

    def _ensure_fund_history_current(self, fund: dict, start_date: date) -> Dict[date, float]:
        scheme = self._resolve_scheme_by_code(fund.get("scheme_code") or "")
        code = str(scheme.get("scheme_code") or "").strip()
        self._ensure_latest_fund_nav(scheme)
        history = self._watchlist_store.nav_history(code, start_date, date.today())
        if self._needs_fund_backfill(history, start_date):
            self._fetch_and_store_fund_history(scheme, start_date, date.today())
            history = self._watchlist_store.nav_history(code, start_date, date.today())
        if not history:
            raise RuntimeError(f"No stored NAV history for {fund.get('scheme_name') or scheme.get('name')}")
        return history

    def _needs_fund_backfill(self, history: Dict[date, float], start_date: date) -> bool:
        if not history:
            return True
        dates = sorted(history)
        earliest = dates[0]
        latest = dates[-1]
        coverage_days = max(0, (latest - earliest).days)
        requested_days = max(0, (date.today() - start_date).days)
        if requested_days <= 40:
            return coverage_days + 7 < requested_days
        if coverage_days < min(120, requested_days // 2):
            return True
        return False

    def _fetch_and_store_fund_history(self, scheme: dict, start_date: date, end_date: date):
        history: List[tuple[date, float]] = []
        try:
            amc_code = self._resolve_amc_code(scheme.get("house") or "", scheme.get("scheme_code") or "")
            resp = requests.get(
                AMFI_NAV_HISTORY_URL,
                params={
                    "mf": amc_code,
                    "frmdt": start_date.strftime(_AMFI_HISTORY_DATE_FMT),
                    "todt": end_date.strftime(_AMFI_HISTORY_DATE_FMT),
                },
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=90,
            )
            resp.raise_for_status()
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
                history.append((dt, nav))
        except Exception as exc:
            print(f"[MutualFunds] AMFI history fallback for {scheme.get('scheme_code')}: {exc}")
        if not history:
            history = self._fetch_mfapi_history(str(scheme.get("scheme_code") or "").strip(), start_date, end_date)
        if not history:
            raise RuntimeError(f"No AMFI history returned for {scheme.get('name')}")
        self._watchlist_store.upsert_nav_history(str(scheme.get("scheme_code") or "").strip(), history)

    def _fetch_mfapi_history(self, scheme_code: str, start_date: date, end_date: date) -> List[tuple[date, float]]:
        resp = requests.get(
            MFAPI_HISTORY_URL.format(scheme_code=scheme_code),
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=60,
        )
        resp.raise_for_status()
        payload = resp.json()
        history: List[tuple[date, float]] = []
        for row in payload.get("data") or []:
            raw_date = row.get("date")
            nav = _clean_float(row.get("nav"))
            if not raw_date or nav is None:
                continue
            try:
                dt = datetime.strptime(raw_date, "%d-%m-%Y").date()
            except Exception:
                continue
            if dt < start_date or dt > end_date:
                continue
            history.append((dt, nav))
        return history

    def _ensure_benchmark_history_current(self, benchmark_name: str, start_date: date, end_date: date) -> Dict[date, float]:
        clean_name = _INDEX_NAME_CLEANUPS.get(benchmark_name, benchmark_name)
        latest_db = self._watchlist_store.latest_benchmark_point(clean_name)
        history = self._watchlist_store.benchmark_history(clean_name, start_date, end_date)
        needs_backfill = not history
        if history:
            dates = sorted(history)
            coverage_days = max(0, (dates[-1] - dates[0]).days)
            requested_days = max(0, (end_date - start_date).days)
            if requested_days > 40 and coverage_days < min(120, requested_days // 2):
                needs_backfill = True
        if needs_backfill:
            self._fetch_and_store_benchmark_history(clean_name, start_date, end_date)
        elif latest_db and latest_db[0] and latest_db[0] < end_date - timedelta(days=2):
            fetch_from = max(latest_db[0] - timedelta(days=MF_INCREMENTAL_HISTORY_LOOKBACK_DAYS), start_date)
            self._fetch_and_store_benchmark_history(clean_name, fetch_from, end_date)
        history = self._watchlist_store.benchmark_history(clean_name, start_date, end_date)
        if not history:
            raise RuntimeError(f"No stored benchmark history for {clean_name}")
        return history

    def _fetch_and_store_benchmark_history(self, clean_name: str, start_date: date, end_date: date):
        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.nseindia.com/reports-indices-historical-index-data",
        }
        session.get(NSE_BASE_URL, headers=headers, timeout=30)
        cursor = start_date
        history: List[tuple[date, float]] = []
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
                history.append((dt, close_val))
            cursor = chunk_end + timedelta(days=1)
        if not history:
            raise RuntimeError(f"No NSE history returned for {clean_name}")
        self._watchlist_store.upsert_benchmark_history(clean_name, history)
