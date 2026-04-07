"""
Market data engine — fetches live data from NSE India + RSS news.
Uses NSE's own API for indices & stocks (single request per type).
Falls back to yfinance when hosted environments cannot access NSE cleanly.
"""

import asyncio
import calendar
import json
import os
import random
import re
import time
import traceback
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from urllib.parse import quote_plus

from bs4 import BeautifulSoup
from dotenv import load_dotenv
import feedparser
import pytz
import requests
import yfinance as yf

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=False)

IST = pytz.timezone("Asia/Kolkata")

_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
]

NSE_BASE = "https://www.nseindia.com"
NSE_INDICES_URL = NSE_BASE + "/api/allIndices"
NSE_NIFTY50_URL = NSE_BASE + "/api/equity-stockIndices?index=NIFTY%2050"
NSE_OC_INDEX_URL = NSE_BASE + "/api/option-chain-indices?symbol={symbol}"
NSE_OC_EQUITY_URL = NSE_BASE + "/api/option-chain-equities?symbol={symbol}"
OC_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}

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

YF_INDEX_MAP = {
    "NIFTY 50": "^NSEI",
    "NIFTY BANK": "^NSEBANK",
    "NIFTY MIDCAP 50": "^NSEMDCP50",
    "NIFTY FINANCIAL SERVICES": "^CNXFIN",
    "NIFTY IT": "^CNXIT",
    "INDIA VIX": "^INDIAVIX",
}

YF_STOCK_SYMBOLS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM",
    "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO", "HINDUNILVR",
    "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFIN", "JSWSTEEL",
    "KOTAKBANK", "LT", "M&M", "MARUTI", "MAXHEALTH", "NESTLEIND",
    "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN",
    "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TATASTEEL", "TCS",
    "TECHM", "TITAN", "TMPV", "TRENT", "ULTRACEMCO", "WIPRO",
]

YF_COMPANY_NAMES = {
    "ADANIENT": "Adani Enterprises Limited",
    "ADANIPORTS": "Adani Ports and Special Economic Zone Limited",
    "APOLLOHOSP": "Apollo Hospitals Enterprise Limited",
    "ASIANPAINT": "Asian Paints Limited",
    "AXISBANK": "Axis Bank Limited",
    "BAJAJ-AUTO": "Bajaj Auto Limited",
    "BAJAJFINSV": "Bajaj Finserv Limited",
    "BAJFINANCE": "Bajaj Finance Limited",
    "BEL": "Bharat Electronics Limited",
    "BHARTIARTL": "Bharti Airtel Limited",
    "CIPLA": "Cipla Limited",
    "COALINDIA": "Coal India Limited",
    "DRREDDY": "Dr. Reddy's Laboratories Limited",
    "EICHERMOT": "Eicher Motors Limited",
    "ETERNAL": "ETERNAL LIMITED",
    "GRASIM": "Grasim Industries Limited",
    "HCLTECH": "HCL Technologies Limited",
    "HDFCBANK": "HDFC Bank Limited",
    "HDFCLIFE": "HDFC Life Insurance Company Limited",
    "HINDALCO": "Hindalco Industries Limited",
    "HINDUNILVR": "Hindustan Unilever Limited",
    "ICICIBANK": "ICICI Bank Limited",
    "INDIGO": "InterGlobe Aviation Limited",
    "INFY": "Infosys Limited",
    "ITC": "ITC Limited",
    "JIOFIN": "Jio Financial Services Limited",
    "JSWSTEEL": "JSW Steel Limited",
    "KOTAKBANK": "Kotak Mahindra Bank Limited",
    "LT": "Larsen & Toubro Limited",
    "M&M": "Mahindra & Mahindra Limited",
    "MARUTI": "Maruti Suzuki India Limited",
    "MAXHEALTH": "Max Healthcare Institute Limited",
    "NESTLEIND": "Nestle India Limited",
    "NTPC": "NTPC Limited",
    "ONGC": "Oil & Natural Gas Corporation Limited",
    "POWERGRID": "Power Grid Corporation of India Limited",
    "RELIANCE": "Reliance Industries Limited",
    "SBILIFE": "SBI Life Insurance Company Limited",
    "SBIN": "State Bank of India",
    "SHRIRAMFIN": "Shriram Finance Limited",
    "SUNPHARMA": "Sun Pharmaceutical Industries Limited",
    "TATACONSUM": "TATA CONSUMER PRODUCTS LIMITED",
    "TATASTEEL": "Tata Steel Limited",
    "TCS": "Tata Consultancy Services Limited",
    "TECHM": "Tech Mahindra Limited",
    "TITAN": "Titan Company Limited",
    "TMPV": "Tata Motors Passenger Vehicles Limited",
    "TRENT": "Trent Limited",
    "ULTRACEMCO": "UltraTech Cement Limited",
    "WIPRO": "Wipro Limited",
}

NEWS_FEEDS_INDIA = [
    "https://feeds.feedburner.com/ndtvprofit-latest",
    "https://www.livemint.com/rss/markets",
    "https://www.livemint.com/rss/news",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://economictimes.indiatimes.com/rssfeedstopstories.cms",
    "https://www.moneycontrol.com/rss/MCtopnews.xml",
    "https://www.moneycontrol.com/rss/latestnews.xml",
    "https://www.thehindubusinessline.com/markets/feeder/default.rss",
    "https://news.google.com/rss/search?q=NSE+OR+NIFTY+OR+SENSEX+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=RBI+OR+SEBI+OR+%22rate+cut%22+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=india+IPO+OR+%22quarterly+results%22+OR+%22Q4+results%22+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=Adani+OR+Tata+OR+Reliance+stock+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=india+market+OR+nifty+OR+sensex+site:reuters.com+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=india+market+OR+nifty+OR+sensex+site:bloomberg.com+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://www.business-standard.com/rss/markets/news.xml",
]
NEWS_FEEDS_GLOBAL = [
    # Direct RSS (fast, real-time)
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://feeds.bloomberg.com/economics/news.rss",
    "https://feeds.bloomberg.com/politics/news.rss",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.cnbc.com/id/100727362/device/rss/rss.html",
    "http://feeds.marketwatch.com/marketwatch/topstories/",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
    "https://www.investing.com/rss/news_25.rss",
    "https://www.investing.com/rss/news_95.rss",
    "https://www.investing.com/rss/news_11.rss",
    "https://www.forexlive.com/feed/",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://feeds.finance.yahoo.com/rss/2.0/headline",
    "https://seekingalpha.com/market_currents.xml",
    "https://www.livemint.com/rss/news",

    # Major global publishers / wires (direct RSS)
    "http://rss.cnn.com/rss/edition.rss",
    "http://feeds.foxnews.com/foxnews/latest",
    "https://abcnews.go.com/abcnews/topstories",
    "https://feeds.nbcnews.com/feeds/topstories",
    "https://www.cbsnews.com/latest/rss/main",
    # AP RSS domain sometimes fails DNS in some environments; keep it disabled for now.
    # "https://feeds.apnews.com/rss/apf-topnews",
    "https://www.theguardian.com/world/rss",
    "https://rss.dw.com/xml/rss-en-all",
    "https://www.france24.com/en/rss",
    "https://www3.nhk.or.jp/rss/news/cat0.xml",

    # Google News macro queries (tight windows to reduce backlog)
    "https://news.google.com/rss/search?q=crude+oil+brent+price+when:6h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=federal+reserve+OR+dollar+index+OR+treasury+yield+when:6h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=iran+war+OR+sanctions+OR+tariff+trade+war+when:6h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=S%26P+500+OR+Nasdaq+OR+Dow+Jones+when:6h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=global+selloff+OR+risk-off+OR+credit+spreads+OR+treasury+auction+when:6h&hl=en&gl=US&ceid=US:en",
]
NEWS_FEEDS_GOLD_SILVER = [
    # India-focused
    "https://news.google.com/rss/search?q=gold+price+OR+%22gold+rate%22+OR+%22gold+today%22+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=silver+price+OR+%22silver+rate%22+OR+%22silver+today%22+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=%22MCX+gold%22+OR+%22MCX+silver%22+OR+%22COMEX+gold%22+OR+%22COMEX+silver%22+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=%22precious+metals%22+OR+%22gold+jewellery%22+OR+%22gold+import%22+india+when:1d&hl=en-IN&gl=IN&ceid=IN:en",

    # Global price / instruments / FX tickers
    "https://news.google.com/rss/search?q=gold+price+OR+silver+price+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=XAUUSD+OR+XAGUSD+OR+%22spot+gold%22+OR+%22spot+silver%22+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+futures+OR+silver+futures+OR+%22COMEX+gold%22+OR+%22COMEX+silver%22+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+ETF+OR+GLD+OR+IAU+OR+SLV+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+rally+OR+gold+crash+OR+gold+forecast+OR+silver+rally+OR+silver+forecast+when:1d&hl=en&gl=US&ceid=US:en",

    # Wider global publishers (via Google News site filters)
    "https://news.google.com/rss/search?q=gold+OR+silver+site:reuters.com+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+OR+silver+site:bloomberg.com+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+OR+silver+site:cnbc.com+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+OR+silver+site:ft.com+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+OR+silver+site:marketwatch.com+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+OR+silver+site:barrons.com+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+OR+silver+site:investing.com+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+OR+silver+site:fxstreet.com+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+OR+silver+site:kitco.com+when:1d&hl=en&gl=US&ceid=US:en",
    # Direct Kitco RSS
    "https://www.kitco.com/news/category/markets/rss",
    "https://www.kitco.com/news/category/mining/rss",
]
NEWS_FEEDS = NEWS_FEEDS_INDIA + NEWS_FEEDS_GLOBAL + NEWS_FEEDS_GOLD_SILVER
_GLOBAL_FEEDS_SET = set(NEWS_FEEDS_GLOBAL + NEWS_FEEDS_GOLD_SILVER)

TRADIENT_NEWS_URL = "https://api.tradient.org/v1/api/market/news"

NV_API_KEY = os.getenv("NV_API_KEY", "")
NV_API_URL = os.getenv("NV_API_URL", "https://integrate.api.nvidia.com/v1/chat/completions")
NV_API_MODEL = os.getenv("NV_NEWS_MODEL", "moonshotai/kimi-k2.5")
NV_FAST_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1"
LLM_CACHE_SIZE = 500

_NIFTY50_SYMBOLS_STR = ", ".join(sorted(SECTOR_MAP.keys()))
_LLM_SYSTEM = (
    "You output ONLY a raw JSON array. No markdown, no explanation, no text before or after. "
    "Each element: "
    '{"idx":N,"stocks":[],"sentiment":"bullish"|"bearish"|"neutral",'
    '"impact":"high"|"medium"|"low","breaking":true|false,"gold_silver":true|false}'
)
_LLM_PROMPT_PREFIX = (
    f"NIFTY 50 symbols: {_NIFTY50_SYMBOLS_STR}.\n"
    "Rules:\n"
    "- stocks: relevant NIFTY50 symbols, [] if none\n"
    "- breaking: true ONLY for macro/market-wide news (war, central bank, oil/gold shock, "
    "market crash/rally, FII/FPI, sanctions, geopolitics). "
    "false for stock results, SEBI filings, dividends, company-specific events.\n"
    "- gold_silver: true if about gold, silver, precious metals, bullion, MCX/COMEX metals, "
    "gold ETFs, sovereign gold bonds. false otherwise.\n"
    "- Output the JSON array and nothing else.\n\n"
)

_GOLD_SILVER_KW = {
    "gold", "silver", "precious metal", "precious metals",
    "bullion", "mcx gold", "mcx silver", "comex gold", "comex silver",
    "gold price", "silver price", "gold rate", "silver rate",
    "gold etf", "silver etf", "gold futures", "silver futures",
    "gold import", "gold export", "gold jewellery", "gold jewelry",
    "gold mining", "silver mining", "gold reserve", "gold standard",
    "xau", "xag", "gold rally", "gold crash", "gold forecast",
    "sovereign gold bond", "sgb", "hallmark",
    "gold demand", "gold supply", "gold smuggling",
    "yellow metal", "white metal",
}

_GLOBAL_MARKET_KW = {
    # markets / assets
    "market", "markets", "stocks", "shares", "equities", "futures", "options",
    "bonds", "yield", "yields", "treasury", "gilts", "bund", "credit", "spread",
    "dow", "nasdaq", "s&p", "ftse", "dax", "nikkei", "hang seng", "sensex", "nifty",
    # macro
    "fed", "federal reserve", "ecb", "boe", "boj", "pbo", "central bank",
    "rate cut", "rate hike", "interest rate", "inflation", "cpi", "ppi", "pce",
    "gdp", "pmi", "unemployment", "jobs report", "recession", "growth",
    # fx / commodities
    "forex", "fx", "currency", "dollar", "usd", "eur", "yen", "rupee", "inr",
    "oil", "crude", "brent", "wti", "gas", "lng", "gold", "silver", "copper",
    # risk / geopolitics that moves markets
    "sanction", "tariff", "embargo", "war", "geopolit", "strait of hormuz",
    # company/market structure
    "earnings", "profit", "revenue", "guidance", "ipo", "listing", "buyback",
    "downgrade", "upgrade", "rating", "bank", "banking",
}

_GLOBAL_EXCLUDE_KW = {
    # obvious non-market categories that flood general news feeds
    "nasa", "space", "rocket", "astronaut", "mars", "moon", "spacex",
    "sports", "football", "soccer", "nba", "nfl", "mlb", "tennis", "cricket",
    "celebrity", "movie", "music", "tv", "hollywood", "fashion",
    "recipe", "cooking", "travel", "weather",
}

_TICKER_PAREN_RE = re.compile(r"\([A-Z]{1,5}(?:[:.][A-Z]{1,5})?\)")
_SHARES_MOVE_RE = re.compile(
    r"\bshares?\b.*\b(rise|rises|rose|fall|falls|fell|jump|jumps|jumped|slump|slumps|slumped|surge|surges|surged|plunge|plunges|plunged)\b",
    re.IGNORECASE,
)

_BREAKING_MARKET_KW = {
    "breaking", "just in", "flash", "alert", "urgent", "exclusive",
    "rbi rate", "rbi policy", "rbi governor", "rate cut", "rate hike",
    "monetary policy", "repo rate",
    "sebi ban", "sebi order", "sebi probe", "sebi investigation",
    "halted", "circuit", "crash", "plunge", "plummet", "tumble", "bloodbath",
    "sensex crash", "nifty crash", "market crash", "flash crash",
    "rally", "surge", "soar", "record high", "all-time high",
    "fraud", "scam", "default", "crisis",
    "war", "sanction", "tariff", "embargo", "geopolit",
    "fii", "dii", "fpi", "inflation", "gdp",
    "crude oil", "brent", "oil price", "gold price", "dollar index",
    "fed ", "federal reserve", "treasury yield",
    "black swan", "recession", "bear market", "bull market",
    "global sell-off", "circuit breaker",
    "nuclear", "iran", "trump", "china trade",
}
_STOCK_EVENT_KW = {
    "acquisition", "merger", "buyback", "dividend", "bonus", "split",
    "results", "q1", "q2", "q3", "q4", "profit", "loss", "revenue",
    "upgrade", "downgrade", "target", "rating", "ipo", "listing",
    "block deal", "bulk deal", "stake",
    # Corporate filings / compliance noise (should NEVER be macro breaking)
    "sebi compliance", "compliance certificate", "compliance cert",
    "demat", "demat certificate", "depository", "encumbrance", "share encumbrance",
    "disclosure", "annual disclosure", "non-large corporate", "non large corporate",
    "trading window", "special transfer window", "physical share transfer",
    "regulation 74(5)", "reg 74(5)", "74(5)",
    "regulation 7(3)", "reg 7(3)", "7(3)",
    "regulation 40", "reg 40", "40(10)", "40(9)",
    "clarifies bse query", "bse query", "seeks clarification", "clarification",
    "cirs", "cirp", "insolvency", "resolution professional",
}

_WATCHLIST_NAMES = {}
for _sym, _sec in SECTOR_MAP.items():
    _WATCHLIST_NAMES[_sym.lower()] = _sym
_WATCHLIST_ALIASES = {
    "reliance": "RELIANCE", "ril": "RELIANCE", "jio": "RELIANCE",
    "tcs": "TCS", "tata consultancy": "TCS",
    "infosys": "INFY", "infy": "INFY",
    "hdfc bank": "HDFCBANK", "hdfcbank": "HDFCBANK",
    "icici bank": "ICICIBANK", "icici": "ICICIBANK",
    "sbi": "SBIN", "state bank": "SBIN",
    "kotak": "KOTAKBANK", "kotak mahindra": "KOTAKBANK",
    "airtel": "BHARTIARTL", "bharti": "BHARTIARTL",
    "wipro": "WIPRO",
    "hcl tech": "HCLTECH", "hcltech": "HCLTECH",
    "asian paints": "ASIANPAINT", "asianpaint": "ASIANPAINT",
    "maruti": "MARUTI", "maruti suzuki": "MARUTI",
    "sun pharma": "SUNPHARMA", "sunpharma": "SUNPHARMA",
    "titan": "TITAN",
    "tata motors": "TATAMOTORS", "tata steel": "TATASTEEL",
    "tata consumer": "TATACONSUM",
    "nestle": "NESTLEIND",
    "l&t": "LT", "larsen": "LT",
    "axis bank": "AXISBANK", "axis": "AXISBANK",
    "bajaj finance": "BAJFINANCE", "bajaj finserv": "BAJAJFINSV",
    "bajaj auto": "BAJAJ-AUTO",
    "ultratech": "ULTRACEMCO",
    "power grid": "POWERGRID", "ntpc": "NTPC", "ongc": "ONGC",
    "coal india": "COALINDIA", "jsw steel": "JSWSTEEL", "jsw": "JSWSTEEL",
    "adani ports": "ADANIPORTS", "adani enterprises": "ADANIENT",
    "indusind": "INDUSINDBK", "tech mahindra": "TECHM",
    "ltimindtree": "LTIM", "cipla": "CIPLA", "apollo": "APOLLOHOSP",
    "dr reddy": "DRREDDY", "divis": "DIVISLAB", "eicher": "EICHERMOT",
    "hero moto": "HEROMOTOCO", "britannia": "BRITANNIA",
    "bpcl": "BPCL", "hindalco": "HINDALCO", "grasim": "GRASIM",
    "hdfc life": "HDFCLIFE", "sbi life": "SBILIFE",
    "hindustan unilever": "HINDUNILVR", "hul": "HINDUNILVR",
    "itc": "ITC",
    "mahindra": "M&M", "m&m": "M&M",
    "shriram": "SHRIRAMFIN", "indigo": "INDIGO", "interglobe": "INDIGO",
    "trent": "TRENT", "bel": "BEL", "bharat electronics": "BEL",
}
_WATCHLIST_ALIASES.update(_WATCHLIST_NAMES)

_SHORT_ALIAS_RE: Dict[str, re.Pattern] = {}
_LONG_ALIASES: Dict[str, str] = {}
for _alias, _sym in _WATCHLIST_ALIASES.items():
    if len(_alias) <= 4:
        _SHORT_ALIAS_RE[_alias] = re.compile(r"\b" + re.escape(_alias) + r"\b", re.IGNORECASE)
    else:
        _LONG_ALIASES[_alias] = _sym

_SECTOR_NEWS_KEYWORDS: Dict[str, List[str]] = {}
for _sym, _sec in SECTOR_MAP.items():
    _SECTOR_NEWS_KEYWORDS.setdefault(_sec, []).append(_sym)

_SECTOR_HEADLINE_TRIGGERS = {
    "it stock": "IT", "tech stock": "IT", "software stock": "IT",
    "bank stock": "Banking", "banking stock": "Banking", "psu bank": "Banking",
    "private bank": "Banking", "nbfc": "Finance", "finance stock": "Finance",
    "pharma stock": "Pharma", "drug": "Pharma", "healthcare": "Pharma",
    "auto stock": "Auto", "automobile": "Auto", "automaker": "Auto",
    "ev stock": "Auto", "vehicle": "Auto",
    "fmcg stock": "FMCG", "consumer stock": "FMCG",
    "metal stock": "Metal", "steel stock": "Metal",
    "oil stock": "Energy", "energy stock": "Energy",
    "infra stock": "Infra", "infrastructure": "Infra",
    "cement stock": "Infra", "telecom stock": "Telecom",
    "realty stock": "Infra",
}

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
        self._llm_cache: OrderedDict = OrderedDict()

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
        tasks = [t for t in (self._market_task, self._news_task) if t]
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
        """Fetch news every 60 s for real-time coverage."""
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
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            return

    # ── NSE data (indices + stocks in 2 API calls) ─────────────────────

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _fetch_nse_data(self):
        self._fetch_nse_stocks()
        time.sleep(1)
        self._fetch_nse_indices()
        if not self._stocks:
            print("[Market] NSE stocks unavailable — using Yahoo Finance fallback")
            self._fetch_yf_stocks()
        if not self._indices:
            print("[Market] NSE indices unavailable — using Yahoo Finance fallback")
            self._fetch_yf_indices()

    def _fetch_yf_indices(self):
        results = []
        for name, yf_symbol in YF_INDEX_MAP.items():
            try:
                info = yf.Ticker(yf_symbol).fast_info
                price = self._to_float(info.get("lastPrice"))
                prev = self._to_float(info.get("previousClose"))
                if not price or not prev:
                    continue
                change = price - prev
                pct = (change / prev * 100) if prev else 0
                results.append({
                    "symbol": name,
                    "name": name,
                    "price": round(price, 2),
                    "change": round(change, 2),
                    "change_pct": round(pct, 2),
                    "open": round(self._to_float(info.get("open"), price), 2),
                    "high": round(self._to_float(info.get("dayHigh"), price), 2),
                    "low": round(self._to_float(info.get("dayLow"), price), 2),
                    "advances": None,
                    "declines": None,
                })
            except Exception as e:
                print(f"[YF Index] {name}: {e}")
        if results:
            self._indices = results

    def _fetch_yf_stocks(self):
        yf_symbols = [f"{sym}.NS" for sym in YF_STOCK_SYMBOLS]
        try:
            data = yf.download(
                yf_symbols,
                period="5d",
                interval="1d",
                auto_adjust=False,
                progress=False,
                group_by="ticker",
                threads=True,
            )
        except Exception as e:
            print(f"[YF Stocks] download failed: {e}")
            return

        columns = set(data.columns.get_level_values(0))
        stocks: Dict[str, dict] = {}
        for sym in YF_STOCK_SYMBOLS:
            yf_symbol = f"{sym}.NS"
            if yf_symbol not in columns:
                continue
            frame = data[yf_symbol][["Open", "High", "Low", "Close", "Volume"]]
            closes = frame["Close"].dropna()
            if closes.empty:
                continue
            row = frame.loc[closes.index[-1]]
            price = self._to_float(row.get("Close"))
            if not price:
                continue
            prev_close = self._to_float(closes.iloc[-2] if len(closes) > 1 else price, price)
            change = price - prev_close if prev_close else 0
            pct = (change / prev_close * 100) if prev_close else 0
            open_price = self._to_float(row.get("Open"), price)
            high = self._to_float(row.get("High"), price)
            low = self._to_float(row.get("Low"), price)
            volume = int(self._to_float(row.get("Volume"), 0))
            stocks[sym] = {
                "symbol": sym,
                "name": YF_COMPANY_NAMES.get(sym, sym),
                "sector": SECTOR_MAP.get(sym, "Other"),
                "price": round(price, 2),
                "change": round(change, 2),
                "change_pct": round(pct, 2),
                "open": round(open_price, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "volume": volume,
                "prev_close": round(prev_close, 2),
                "year_high": round(high, 2),
                "year_low": round(low, 2),
            }
        if stocks:
            self._stocks = stocks

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
    def _classify_news(title: str, body: str = "") -> dict:
        lower = title.lower()
        combined = lower + " " + body.lower() if body else lower
        is_breaking = any(kw in lower for kw in _BREAKING_MARKET_KW)
        is_stock_event = any(kw in combined for kw in _STOCK_EVENT_KW)
        if is_stock_event:
            is_breaking = False
        is_gold_silver = any(kw in combined for kw in _GOLD_SILVER_KW)
        is_company_specific = bool(_TICKER_PAREN_RE.search(title)) or bool(_SHARES_MOVE_RE.search(combined)) or is_stock_event
        # Generic market relevance: used for GLOBAL tab filtering
        is_market_rel = any(kw in combined for kw in _GLOBAL_MARKET_KW) and not any(
            kw in combined for kw in _GLOBAL_EXCLUDE_KW
        )
        matched = []
        for alias, sym in _LONG_ALIASES.items():
            if alias in combined and sym not in matched:
                matched.append(sym)
        for alias, pat in _SHORT_ALIAS_RE.items():
            sym = _WATCHLIST_ALIASES[alias]
            if sym not in matched and pat.search(combined):
                matched.append(sym)
        for trigger, sector in _SECTOR_HEADLINE_TRIGGERS.items():
            if trigger in combined:
                for sym in _SECTOR_NEWS_KEYWORDS.get(sector, []):
                    if sym not in matched:
                        matched.append(sym)
        return {
            "breaking": is_breaking,
            "stock_event": is_stock_event,
            "gold_silver": is_gold_silver,
            "market_relevant": is_market_rel,
            "company_specific": is_company_specific,
            "watchlist_stocks": matched,
        }

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

    @staticmethod
    def _display_news_source(feed_url: str, feed_level_source: str, entry_title: str, entry) -> str:
        """
        Aggregator feeds (Google News search RSS) use the same feed title for every item.
        Prefer the original outlet: entry['source']['title'], or the suffix after em/en dash
        in the headline (e.g. '... - Reuters').
        """
        lvl = (feed_level_source or "").strip()
        url = feed_url or ""
        is_google = lvl == "Google News" or "news.google.com" in url
        if is_google:
            src = getattr(entry, "source", None)
            if isinstance(src, dict):
                t = src.get("title") or src.get("value")
                if t and str(t).strip():
                    return str(t).strip()[:20]
            elif isinstance(src, str) and src.strip():
                return src.strip()[:20]
            t = (entry_title or "").strip()
            for sep in (" — ", " – ", " - "):
                if sep in t:
                    pub = t.rsplit(sep, 1)[-1].strip()
                    if 2 <= len(pub) <= 100 and "when:1d" not in pub.lower():
                        return pub[:20]
        if lvl:
            return lvl[:20]
        return "News"[:20]

    @staticmethod
    def _llm_call_one(headline: str, model: str) -> Optional[dict]:
        """Classify a single headline via NVIDIA API. Returns parsed dict or None."""
        user_msg = _LLM_PROMPT_PREFIX + f'1. "{headline}"'
        try:
            if model == "moonshotai/kimi-k2.5":
                r = requests.post(
                    NV_API_URL,
                    headers={"Authorization": f"Bearer {NV_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": _LLM_SYSTEM},
                            {"role": "user", "content": user_msg},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 2048,
                        "stream": True,
                    },
                    timeout=90,
                    stream=True,
                )
                if r.status_code != 200:
                    return None
                content = ""
                for line in r.iter_lines(decode_unicode=True):
                    if not line or not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    try:
                        delta = json.loads(line[6:]).get("choices", [{}])[0].get("delta", {})
                        content += delta.get("content", "") or ""
                    except Exception:
                        pass
                text = content.strip()
            else:
                r = requests.post(
                    NV_API_URL,
                    headers={"Authorization": f"Bearer {NV_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": _LLM_SYSTEM},
                            {"role": "user", "content": user_msg},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 512,
                    },
                    timeout=30,
                )
                if r.status_code != 200:
                    return None
                text = r.json()["choices"][0]["message"]["content"].strip()

            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            start = text.find("[")
            end = text.rfind("]")
            if start != -1 and end != -1:
                text = text[start:end + 1]
            elif text.startswith("{"):
                text = "[" + text + "]"
            arr = json.loads(text)
            return arr[0] if arr else None
        except Exception:
            return None

    def _llm_classify_all(self, items: List[dict]):
        """Classify headlines: apply cache, then LLM for each new one."""
        if not NV_API_KEY or not items:
            return

        valid_syms = set(SECTOR_MAP.keys())
        uncached = []
        for item in items:
            cache_key = item["title"][:80].lower()
            if cache_key in self._llm_cache:
                self._llm_cache.move_to_end(cache_key)
                cached = self._llm_cache[cache_key]
                if cached["stocks"]:
                    item["watchlist_stocks"] = cached["stocks"]
                item["sentiment"] = cached["sentiment"]
                item["impact"] = cached["impact"]
                item["breaking"] = cached.get("breaking", item.get("breaking", False))
                item["gold_silver"] = cached.get("gold_silver", False) or item.get("gold_silver", False)
            else:
                uncached.append(item)

        if not uncached:
            return

        model = NV_FAST_MODEL if len(uncached) > 10 else NV_API_MODEL
        ok = 0
        for item in uncached:
            entry = self._llm_call_one(item["title"], model)
            if not entry or not isinstance(entry, dict):
                continue
            stocks = [s for s in entry.get("stocks", []) if s in valid_syms]
            sentiment = entry.get("sentiment", "neutral")
            if sentiment not in ("bullish", "bearish", "neutral"):
                sentiment = "neutral"
            impact = entry.get("impact", "low")
            if impact not in ("high", "medium", "low"):
                impact = "low"
            is_brk = bool(entry.get("breaking", False))
            if item.get("stock_event"):
                is_brk = False
            is_gs = bool(entry.get("gold_silver", False)) or item.get("gold_silver", False)
            result = {
                "stocks": stocks,
                "sentiment": sentiment,
                "impact": impact,
                "breaking": is_brk,
                "gold_silver": is_gs,
            }

            cache_key = item["title"][:80].lower()
            self._llm_cache[cache_key] = result
            while len(self._llm_cache) > LLM_CACHE_SIZE:
                self._llm_cache.popitem(last=False)

            if stocks:
                item["watchlist_stocks"] = stocks
            item["sentiment"] = sentiment
            item["impact"] = impact
            item["breaking"] = is_brk
            item["gold_silver"] = is_gs
            ok += 1

        print(f"[LLM] {ok}/{len(uncached)} new via {model.split('/')[-1]}, "
              f"{len(items)-len(uncached)} cached")

    def _fetch_all_news(self):
        """Fetch RSS feeds + Tradient API, merge, sort by recency."""
        now = datetime.now(IST)
        cutoff = now - timedelta(hours=18)
        global_cutoff = now - timedelta(hours=6)
        raw: List[dict] = []

        for url in NEWS_FEEDS:
            try:
                feed = feedparser.parse(url)
                is_global_feed = url in _GLOBAL_FEEDS_SET
                source = feed.feed.get("title", "")
                if " - " in source:
                    parts = source.split(" - ")
                    if parts[-1].strip().lower().startswith("google"):
                        source = "Google News"
                    else:
                        source = parts[0]
                source = source.strip()[:20]
                for entry in feed.entries[:30]:
                    pub_dt = self._parse_pub_time(entry)
                    if pub_dt:
                        if is_global_feed and pub_dt < global_cutoff:
                            continue
                        if (not is_global_feed) and pub_dt < cutoff:
                            continue
                    else:
                        # Avoid undated backlog especially for global feeds
                        if is_global_feed:
                            continue
                    title = entry.get("title", "").strip()
                    if not title:
                        continue
                    summary = entry.get("summary", "").strip()[:300]
                    tags = self._classify_news(title, summary)
                    age_secs = int((now - pub_dt).total_seconds()) if pub_dt else 999999
                    display_src = self._display_news_source(url, source, title, entry)
                    raw.append({
                        "title": title,
                        "link": entry.get("link", ""),
                        "source": display_src,
                        "age_secs": age_secs,
                        "time": self._relative_time(pub_dt) if pub_dt else "",
                        "is_fresh": age_secs < 900,
                        "global_news": is_global_feed,
                        "breaking": tags["breaking"],
                        "stock_event": tags["stock_event"],
                        "gold_silver": tags["gold_silver"],
                        "market_relevant": tags.get("market_relevant", False),
                        "company_specific": tags.get("company_specific", False),
                        "watchlist_stocks": tags["watchlist_stocks"],
                    })
            except Exception as e:
                print(f"[News] RSS error: {e}")

        self._fetch_tradient_news(raw, now, cutoff)

        raw.sort(key=lambda x: x["age_secs"])

        seen_keys: Set[str] = set()
        unique: List[dict] = []
        for item in raw:
            title_lower = item["title"].lower()
            words = set(re.findall(r"[a-z]{4,}", title_lower))
            exact_key = title_lower[:50]
            if exact_key in seen_keys:
                continue
            is_dup = False
            for prev_key in list(seen_keys):
                prev_words = set(re.findall(r"[a-z]{4,}", prev_key))
                if prev_words and words:
                    overlap = len(words & prev_words) / max(len(words | prev_words), 1)
                    if overlap > 0.6:
                        is_dup = True
                        break
            if not is_dup:
                seen_keys.add(exact_key)
                unique.append(item)
        if unique:
            unique = unique[:80]
            self._llm_classify_all(unique)
            self._news = unique

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
                body = obj.get("text", "")[:300]
                stock = item.get("stock_name", "")
                tags = self._classify_news(title, body + " " + stock)
                source = "Tradient"
                if stock:
                    source = stock[:15]
                # Tradient API doesn't provide a canonical public article URL.
                # Provide a safe fallback link so items are clickable.
                link = "https://www.google.com/search?q=" + quote_plus(title)
                raw.append({
                    "title": title,
                    "link": link,
                    "source": source,
                    "age_secs": age_secs,
                    "time": self._relative_time(pub_dt) if pub_dt else "",
                    "is_fresh": age_secs < 900,
                    "global_news": False,
                    "breaking": tags["breaking"],
                    "stock_event": tags["stock_event"],
                    "gold_silver": tags["gold_silver"],
                    "market_relevant": tags.get("market_relevant", False),
                    "company_specific": tags.get("company_specific", False),
                    "watchlist_stocks": tags["watchlist_stocks"],
                })
                tradient_sent = obj.get("overall_sentiment", "").lower()
                if tradient_sent in ("positive", "very positive"):
                    raw[-1]["sentiment"] = "bullish"
                elif tradient_sent in ("negative", "very negative"):
                    raw[-1]["sentiment"] = "bearish"
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
        if not adv and self._stocks:
            adv = sum(1 for stock in self._stocks.values() if stock.get("change_pct", 0) > 0)
            dec = sum(1 for stock in self._stocks.values() if stock.get("change_pct", 0) < 0)
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

    # ── Option Chain ─────────────────────────────────────────────────────

    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> dict:
        symbol = symbol.upper()
        url_tpl = NSE_OC_INDEX_URL if symbol in OC_INDEX_SYMBOLS else NSE_OC_EQUITY_URL
        data = self._nse.get(url_tpl.format(symbol=symbol))
        if not data or "records" not in data:
            return {"error": "No data — market may be closed", "strikes": [], "expiries": []}

        records = data["records"]
        all_expiries = records.get("expiryDates", [])
        spot = records.get("underlyingValue", 0)
        timestamp = records.get("timestamp", "")

        selected = expiry if expiry and expiry in all_expiries else (all_expiries[0] if all_expiries else "")

        total_ce_oi = 0
        total_pe_oi = 0
        max_oi = 1
        strikes = []
        pain_map: Dict[float, float] = {}

        for row in records.get("data", []):
            if row.get("expiryDate") != selected:
                continue
            strike = row.get("strikePrice", 0)
            ce_raw = row.get("CE", {})
            pe_raw = row.get("PE", {})

            ce = {
                "ltp": ce_raw.get("lastPrice", 0),
                "oi": ce_raw.get("openInterest", 0),
                "chgOI": ce_raw.get("changeinOpenInterest", 0),
                "vol": ce_raw.get("totalTradedVolume", 0),
                "iv": ce_raw.get("impliedVolatility", 0),
                "chg": ce_raw.get("change", 0),
            } if ce_raw else None
            pe = {
                "ltp": pe_raw.get("lastPrice", 0),
                "oi": pe_raw.get("openInterest", 0),
                "chgOI": pe_raw.get("changeinOpenInterest", 0),
                "vol": pe_raw.get("totalTradedVolume", 0),
                "iv": pe_raw.get("impliedVolatility", 0),
                "chg": pe_raw.get("change", 0),
            } if pe_raw else None

            if ce:
                total_ce_oi += ce["oi"]
                max_oi = max(max_oi, ce["oi"])
            if pe:
                total_pe_oi += pe["oi"]
                max_oi = max(max_oi, pe["oi"])

            ce_pain = sum(max(0, spot - s) * (ce["oi"] if ce else 0) for s in [strike])
            pe_pain = sum(max(0, s - spot) * (pe["oi"] if pe else 0) for s in [strike])
            pain_map[strike] = ce_pain + pe_pain

            strikes.append({"strike": strike, "ce": ce, "pe": pe})

        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0
        max_pain = min(pain_map, key=pain_map.get) if pain_map else 0

        atm_strike = 0
        if spot and strikes:
            atm_strike = min(strikes, key=lambda s: abs(s["strike"] - spot))["strike"]

        return {
            "symbol": symbol,
            "spot": spot,
            "timestamp": timestamp,
            "expiries": all_expiries,
            "selected_expiry": selected,
            "pcr": pcr,
            "max_pain": max_pain,
            "atm_strike": atm_strike,
            "max_oi": max_oi,
            "total_ce_oi": total_ce_oi,
            "total_pe_oi": total_pe_oi,
            "strikes": strikes,
        }

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
