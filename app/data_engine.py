"""
Market data engine — fetches live data from NSE India + RSS news.
Uses NSE's own API for indices & stocks (single request per type).
Falls back to yfinance for chart data only.
"""

import asyncio
import calendar
import json
import random
import time
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

from bs4 import BeautifulSoup
import feedparser
import pytz
import requests
import yfinance as yf

IST = pytz.timezone("Asia/Kolkata")

_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
]

NSE_BASE = "https://www.nseindia.com"
NSE_INDICES_URL = NSE_BASE + "/api/allIndices"
NSE_NIFTY50_URL = NSE_BASE + "/api/equity-stockIndices?index=NIFTY%2050"

SECTOR_MAP = {
    "RELIANCE":   "Energy",   "TCS":        "IT",         "HDFCBANK":   "Banking",
    "INFY":       "IT",       "ICICIBANK":  "Banking",    "HINDUNILVR": "FMCG",
    "ITC":        "FMCG",     "SBIN":       "Banking",    "BHARTIARTL": "Telecom",
    "KOTAKBANK":  "Banking",  "LT":         "Infra",      "AXISBANK":   "Banking",
    "BAJFINANCE": "Finance",  "HCLTECH":    "IT",         "ASIANPAINT": "Consumer",
    "MARUTI":     "Auto",     "SUNPHARMA":  "Pharma",     "TITAN":      "Consumer",
    "TATAMOTORS": "Auto",     "NESTLEIND":  "FMCG",       "NTPC":       "Energy",
    "TATASTEEL":  "Metal",    "ULTRACEMCO": "Infra",      "POWERGRID":  "Energy",
    "WIPRO":      "IT",       "ONGC":       "Energy",     "JSWSTEEL":   "Metal",
    "ADANIPORTS": "Infra",    "BAJAJFINSV": "Finance",    "TECHM":      "IT",
    "LTIM":       "IT",       "COALINDIA":  "Energy",     "INDUSINDBK": "Banking",
    "HDFCLIFE":   "Finance",  "GRASIM":     "Infra",      "CIPLA":      "Pharma",
    "APOLLOHOSP": "Pharma",   "TATACONSUM": "FMCG",       "DIVISLAB":   "Pharma",
    "EICHERMOT":  "Auto",     "HEROMOTOCO": "Auto",       "DRREDDY":    "Pharma",
    "BRITANNIA":  "FMCG",     "BPCL":       "Energy",     "ADANIENT":   "Infra",
    "HINDALCO":   "Metal",    "M&M":        "Auto",       "BAJAJ-AUTO": "Auto",
    "SBILIFE":    "Finance",  "UPL":        "Chemical",
}

TRACKED_INDICES = {"NIFTY 50", "NIFTY BANK", "NIFTY NEXT 50", "NIFTY IT",
                   "NIFTY MIDCAP 50", "NIFTY FINANCIAL SERVICES", "INDIA VIX"}

NEWS_FEEDS = [
    "https://feeds.feedburner.com/ndtvprofit-latest",
    "https://www.livemint.com/rss/markets",
    "https://news.google.com/rss/search?q=NSE+BSE+NIFTY+SENSEX+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=indian+stock+market+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.moneycontrol.com/rss/marketreports.xml",
]

TRADIENT_NEWS_URL = "https://api.tradient.org/v1/api/market/news"

_BREAKING_KW = {
    "breaking", "just in", "flash", "alert", "urgent", "exclusive",
    "sebi", "rbi", "halted", "circuit", "ban", "fraud", "scam",
    "acquisition", "merger", "buyback", "dividend", "bonus", "split",
    "results", "q1", "q2", "q3", "q4", "profit", "loss", "revenue",
    "upgrade", "downgrade", "target", "rating", "ipo", "listing",
    "fii", "dii", "block deal", "bulk deal", "stake",
}

_WATCHLIST_NAMES = {}
for _sym, _sec in SECTOR_MAP.items():
    _WATCHLIST_NAMES[_sym.lower()] = _sym
_WATCHLIST_ALIASES = {
    "reliance": "RELIANCE", "tcs": "TCS", "infosys": "INFY",
    "hdfc bank": "HDFCBANK", "icici bank": "ICICIBANK",
    "sbi": "SBIN", "state bank": "SBIN", "kotak": "KOTAKBANK",
    "airtel": "BHARTIARTL", "bharti": "BHARTIARTL",
    "wipro": "WIPRO", "hcl tech": "HCLTECH", "hcl": "HCLTECH",
    "asian paints": "ASIANPAINT", "maruti": "MARUTI",
    "sun pharma": "SUNPHARMA", "titan": "TITAN",
    "tata motors": "TATAMOTORS", "tata steel": "TATASTEEL",
    "tata consumer": "TATACONSUM", "nestle": "NESTLEIND",
    "l&t": "LT", "larsen": "LT", "axis bank": "AXISBANK",
    "bajaj finance": "BAJFINANCE", "bajaj finserv": "BAJAJFINSV",
    "bajaj auto": "BAJAJ-AUTO", "ultratech": "ULTRACEMCO",
    "power grid": "POWERGRID", "ntpc": "NTPC", "ongc": "ONGC",
    "coal india": "COALINDIA", "jsw steel": "JSWSTEEL",
    "adani ports": "ADANIPORTS", "adani enterprises": "ADANIENT",
    "indusind": "INDUSINDBK", "tech mahindra": "TECHM",
    "ltimindtree": "LTIM", "cipla": "CIPLA", "apollo": "APOLLOHOSP",
    "dr reddy": "DRREDDY", "divis": "DIVISLAB", "eicher": "EICHERMOT",
    "hero moto": "HEROMOTOCO", "britannia": "BRITANNIA",
    "bpcl": "BPCL", "hindalco": "HINDALCO", "grasim": "GRASIM",
    "hdfc life": "HDFCLIFE", "sbi life": "SBILIFE",
    "hindustan unilever": "HINDUNILVR", "hul": "HINDUNILVR",
    "itc": "ITC",
}
_WATCHLIST_ALIASES.update(_WATCHLIST_NAMES)

# ── NSE Session ─────────────────────────────────────────────────────────


class NseSession:
    """Manages a requests.Session with auto-refreshing NSE cookies."""

    def __init__(self):
        self._session = requests.Session()
        self._last_cookie_time = 0

    def _refresh_cookies(self):
        now = time.time()
        if now - self._last_cookie_time < 120:
            return
        self._session.headers.update({
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": NSE_BASE,
        })
        try:
            self._session.get(NSE_BASE, timeout=10)
        except Exception:
            pass
        self._last_cookie_time = now

    def get(self, url: str, retries: int = 2) -> Optional[dict]:
        self._refresh_cookies()
        for attempt in range(retries + 1):
            try:
                r = self._session.get(url, timeout=15)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 401:
                    self._last_cookie_time = 0
                    self._refresh_cookies()
                print(f"[NSE] {url.split('?')[0]} → HTTP {r.status_code}")
            except Exception as e:
                print(f"[NSE] Request error (attempt {attempt+1}): {e}")
            time.sleep(1)
        return None


# ── Data Engine ─────────────────────────────────────────────────────────


class DataEngine:
    """Background engine: fetches, caches, and broadcasts market data."""

    def __init__(self):
        self._nse = NseSession()
        self._indices: List[dict] = []
        self._stocks: Dict[str, dict] = {}
        self._news: List[dict] = []
        self._sectors: List[dict] = []
        self._movers: dict = {"gainers": [], "losers": []}
        self._gift_nifty: Optional[dict] = None
        self._last_update: Optional[str] = None
        self._running = False
        self._market_task: Optional[asyncio.Task] = None
        self._news_task: Optional[asyncio.Task] = None
        self._ws_clients: Set = set()
        self._refresh_count = 0

    @property
    def market_status(self) -> str:
        now = datetime.now(IST)
        if now.weekday() >= 5:
            return "CLOSED"
        t = now.hour * 60 + now.minute
        if t < 540:
            return "PRE-MARKET"
        if t < 555:
            return "PRE-OPEN"
        if t < 930:
            return "LIVE"
        if t < 960:
            return "POST-CLOSE"
        return "CLOSED"

    # ── lifecycle ───────────────────────────────────────────────────────

    async def start(self):
        if self._running:
            return
        self._running = True
        self._market_task = asyncio.create_task(self._market_loop())
        self._news_task = asyncio.create_task(self._news_loop())

    async def stop(self):
        self._running = False
        tasks = [task for task in (self._market_task, self._news_task) if task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._market_task = None
        self._news_task = None

    async def _market_loop(self):
        """Fetch NSE indices + stocks + GIFT Nifty every 60 s."""
        try:
            while self._running:
                t0 = time.time()
                try:
                    await asyncio.gather(
                        asyncio.to_thread(self._fetch_nse_data),
                        asyncio.to_thread(self._fetch_gift_nifty),
                    )
                    self._compute_movers()
                    self._compute_sectors()
                    self._last_update = datetime.now(IST).strftime("%H:%M:%S")
                    self._refresh_count += 1
                    elapsed = round(time.time() - t0, 1)
                    print(f"[Market] #{self._refresh_count} in {elapsed}s "
                          f"— {len(self._indices)} idx, {len(self._stocks)} stk")
                    await self._broadcast("update")
                except Exception:
                    traceback.print_exc()
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            return

    async def _news_loop(self):
        """Fetch news every 120 s on its own cadence."""
        try:
            while self._running:
                t0 = time.time()
                try:
                    await asyncio.to_thread(self._fetch_all_news)
                    elapsed = round(time.time() - t0, 1)
                    print(f"[News] {len(self._news)} headlines in {elapsed}s")
                    await self._broadcast("news")
                except Exception:
                    traceback.print_exc()
                await asyncio.sleep(120)
        except asyncio.CancelledError:
            return

    # ── NSE data (indices + stocks in 2 API calls) ─────────────────────

    def _fetch_nse_data(self):
        self._fetch_nse_stocks()
        time.sleep(1)
        self._fetch_nse_indices()

    def _fetch_nse_indices(self):
        data = self._nse.get(NSE_INDICES_URL)
        if not data or "data" not in data:
            return
        results = []
        for idx in data["data"]:
            name = idx.get("index", "")
            if name not in TRACKED_INDICES:
                continue
            try:
                price = float(idx.get("last", 0))
                prev = float(idx.get("previousClose", 0))
                change = price - prev if prev else 0
                pct = float(idx.get("percentChange", 0))
                results.append({
                    "symbol": name,
                    "name": name,
                    "price": round(price, 2),
                    "change": round(change, 2),
                    "change_pct": round(pct, 2),
                    "open": round(float(idx.get("open", 0)), 2),
                    "high": round(float(idx.get("high", 0)), 2),
                    "low": round(float(idx.get("low", 0)), 2),
                    "advances": idx.get("advances"),
                    "declines": idx.get("declines"),
                })
            except (ValueError, TypeError):
                pass
        if results:
            self._indices = results

    def _fetch_gift_nifty(self):
        """Scrape GIFT Nifty price from giftnifty.org."""
        try:
            r = requests.get(
                "https://giftnifty.org/",
                headers={"User-Agent": random.choice(_USER_AGENTS)},
                timeout=8,
            )
            if r.status_code != 200:
                return
            soup = BeautifulSoup(r.text, "html.parser")
            price_el = soup.select_one("span.font-number")
            pct_el = soup.select_one("div.percent > div")
            if not price_el or not pct_el:
                return
            price = float(price_el.get_text(strip=True).replace(",", ""))
            raw = pct_el.get_text(strip=True)
            parts = raw.split("%")
            pct = float(parts[0]) if parts else 0
            change = float(parts[1]) if len(parts) > 1 and parts[1] else 0
            self._gift_nifty = {
                "price": round(price, 2),
                "change": round(change, 2),
                "change_pct": round(pct, 2),
            }
        except Exception as e:
            print(f"[GIFT Nifty] {e}")

    def _fetch_nse_stocks(self):
        data = self._nse.get(NSE_NIFTY50_URL)
        if not data or "data" not in data:
            return
        stocks: Dict[str, dict] = {}
        for item in data["data"]:
            sym = item.get("symbol", "")
            if sym == "NIFTY 50":
                continue
            try:
                price = float(item.get("lastPrice", 0))
                prev = float(item.get("previousClose", 0))
                change = float(item.get("change", 0))
                pct = float(item.get("pChange", 0))
                stocks[sym] = {
                    "symbol": sym,
                    "name": item.get("meta", {}).get("companyName", sym)
                             if isinstance(item.get("meta"), dict)
                             else sym,
                    "sector": SECTOR_MAP.get(sym, "Other"),
                    "price": round(price, 2),
                    "change": round(change, 2),
                    "change_pct": round(pct, 2),
                    "open": round(float(item.get("open", 0)), 2),
                    "high": round(float(item.get("dayHigh", 0)), 2),
                    "low": round(float(item.get("dayLow", 0)), 2),
                    "volume": int(item.get("totalTradedVolume", 0)),
                    "prev_close": round(prev, 2),
                    "year_high": round(float(item.get("yearHigh", 0)), 2),
                    "year_low": round(float(item.get("yearLow", 0)), 2),
                }
            except (ValueError, TypeError):
                pass
        if stocks:
            self._stocks = stocks

    # ── news (RSS) ──────────────────────────────────────────────────────

    @staticmethod
    def _classify_news(title: str) -> dict:
        lower = title.lower()
        is_breaking = any(kw in lower for kw in _BREAKING_KW)
        matched = []
        for alias, sym in _WATCHLIST_ALIASES.items():
            if alias in lower:
                if sym not in matched:
                    matched.append(sym)
        return {"breaking": is_breaking, "watchlist_stocks": matched}

    @staticmethod
    def _parse_pub_time(entry) -> Optional[datetime]:
        """Extract a timezone-aware datetime from an RSS entry."""
        for attr in ("published_parsed", "updated_parsed"):
            tp = getattr(entry, attr, None)
            if tp:
                try:
                    return datetime.fromtimestamp(calendar.timegm(tp), tz=IST)
                except Exception:
                    pass
        return None

    @staticmethod
    def _relative_time(dt: datetime) -> str:
        """Human-readable relative timestamp like '3m ago'."""
        now = datetime.now(IST)
        delta = now - dt
        secs = int(delta.total_seconds())
        if secs < 0:
            secs = 0
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        days = secs // 86400
        if days == 1:
            return "1d ago"
        return f"{days}d ago"

    def _fetch_all_news(self):
        """Fetch RSS feeds + Tradient API, merge, sort by recency."""
        now = datetime.now(IST)
        cutoff = now - timedelta(hours=36)
        raw: List[dict] = []

        for url in NEWS_FEEDS:
            try:
                feed = feedparser.parse(url)
                source = feed.feed.get("title", "")
                if " - " in source:
                    source = source.split(" - ")[0]
                source = source.strip()[:20]
                for entry in feed.entries[:20]:
                    pub_dt = self._parse_pub_time(entry)
                    if pub_dt and pub_dt < cutoff:
                        continue
                    title = entry.get("title", "").strip()
                    if not title:
                        continue
                    tags = self._classify_news(title)
                    age_secs = int((now - pub_dt).total_seconds()) if pub_dt else 999999
                    raw.append({
                        "title": title,
                        "link": entry.get("link", ""),
                        "source": source,
                        "age_secs": age_secs,
                        "time": self._relative_time(pub_dt) if pub_dt else "",
                        "is_fresh": age_secs < 900,
                        "breaking": tags["breaking"],
                        "watchlist_stocks": tags["watchlist_stocks"],
                    })
            except Exception as e:
                print(f"[News] RSS error: {e}")

        self._fetch_tradient_news(raw, now, cutoff)

        raw.sort(key=lambda x: x["age_secs"])

        seen, unique = set(), []
        for item in raw:
            key = item["title"][:50].lower()
            if key and key not in seen:
                seen.add(key)
                unique.append(item)
        if unique:
            self._news = unique[:60]

    def _fetch_tradient_news(self, raw: list, now: datetime, cutoff: datetime):
        """Pull corporate filings from Tradient and append to raw list."""
        try:
            r = requests.get(TRADIENT_NEWS_URL, timeout=10)
            if r.status_code != 200:
                return
            items = r.json().get("data", {}).get("latest_news", [])
            for item in items:
                obj = item.get("news_object", {})
                title = obj.get("title", "").strip()
                if not title:
                    continue
                ts_ms = item.get("publish_date", 0)
                if ts_ms > 1e12:
                    pub_dt = datetime.fromtimestamp(ts_ms / 1000, tz=IST)
                    if pub_dt < cutoff:
                        continue
                    age_secs = int((now - pub_dt).total_seconds())
                else:
                    age_secs = 999999
                    pub_dt = None
                tags = self._classify_news(title)
                stock = item.get("stock_name", "")
                source = "Tradient"
                if stock:
                    source = stock[:15]
                raw.append({
                    "title": title,
                    "link": "",
                    "source": source,
                    "age_secs": age_secs,
                    "time": self._relative_time(pub_dt) if pub_dt else "",
                    "is_fresh": age_secs < 900,
                    "breaking": tags["breaking"],
                    "watchlist_stocks": tags["watchlist_stocks"],
                    "sentiment": obj.get("overall_sentiment", ""),
                })
        except Exception as e:
            print(f"[News] Tradient error: {e}")

    # ── computed views ──────────────────────────────────────────────────

    def _compute_movers(self):
        if not self._stocks:
            return
        by_change = sorted(self._stocks.values(),
                           key=lambda s: s["change_pct"], reverse=True)
        self._movers = {
            "gainers": by_change[:10],
            "losers": list(reversed(by_change[-10:])),
        }

    def _compute_sectors(self):
        if not self._stocks:
            return
        agg: Dict[str, dict] = {}
        for s in self._stocks.values():
            sec = s["sector"]
            agg.setdefault(sec, {"total": 0.0, "n": 0})
            agg[sec]["total"] += s["change_pct"]
            agg[sec]["n"] += 1
        self._sectors = sorted(
            [{"name": k, "change_pct": round(v["total"] / v["n"], 2), "count": v["n"]}
             for k, v in agg.items()],
            key=lambda x: x["change_pct"], reverse=True,
        )

    # ── public API ──────────────────────────────────────────────────────

    def get_dashboard(self) -> dict:
        adv = dec = 0
        for idx in self._indices:
            if idx.get("advances"):
                adv = idx["advances"]
                dec = idx.get("declines", 0)
                break
        return {
            "indices": self._indices,
            "stocks": list(self._stocks.values()),
            "movers": self._movers,
            "news": self._news,
            "sectors": self._sectors,
            "gift_nifty": self._gift_nifty,
            "market_status": self.market_status,
            "last_update": self._last_update,
            "time": datetime.now(IST).strftime("%H:%M:%S"),
            "breadth": {"advances": adv, "declines": dec},
        }

    def get_stock(self, symbol: str) -> Optional[dict]:
        return self._stocks.get(symbol.upper())

    def search(self, query: str) -> list:
        q = query.upper()
        return [
            s for s in self._stocks.values()
            if q in s["symbol"] or q in s.get("name", "").upper()
        ][:12]

    async def get_chart(self, symbol: str, period: str = "1d",
                        interval: str = "5m") -> list:
        def _fetch():
            try:
                hist = yf.Ticker(f"{symbol}.NS").history(
                    period=period, interval=interval)
                return [
                    {
                        "time": int(ts.timestamp()),
                        "open": round(float(r["Open"]), 2),
                        "high": round(float(r["High"]), 2),
                        "low": round(float(r["Low"]), 2),
                        "close": round(float(r["Close"]), 2),
                        "volume": int(r["Volume"]),
                    }
                    for ts, r in hist.iterrows()
                ]
            except Exception as e:
                print(f"[Chart] {symbol}: {e}")
                return []
        return await asyncio.to_thread(_fetch)

    # ── WebSocket helpers ───────────────────────────────────────────────

    def register(self, ws):
        self._ws_clients.add(ws)

    def unregister(self, ws):
        self._ws_clients.discard(ws)

    async def _broadcast(self, msg_type: str = "update"):
        if not self._ws_clients:
            return
        if msg_type == "news":
            payload = json.dumps({"type": "news", "news": self._news})
        else:
            payload = json.dumps({"type": "update", "data": self.get_dashboard()})
        dead = set()
        for ws in self._ws_clients:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead
