"""
Market data engine — fetches live data from NSE India + RSS news.
Uses NSE's own API for indices & stocks (single request per type).
Falls back to yfinance when hosted environments cannot access NSE cleanly.
"""

import asyncio
import calendar
import csv
import hashlib
import io
import json
import os
import random
import re
import time
import traceback
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
import threading
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qsl, quote, quote_plus, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from dotenv import load_dotenv
import feedparser
import pytz
import requests
import yfinance as yf

from .dashboard_store import DashboardStore
from .watchlist_store import WatchlistStore

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
NSE_QUOTE_EQUITY_URL = NSE_BASE + "/api/quote-equity?symbol={symbol}"
NSE_EQUITY_LIST_URLS = (
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
)
NSE_OC_CONTRACT_INFO_URL = NSE_BASE + "/api/option-chain-contract-info?symbol={symbol}"
NSE_OC_V3_URL = NSE_BASE + "/api/option-chain-v3?type={type}&symbol={symbol}"
NSE_OC_INDEX_URL = NSE_BASE + "/api/option-chain-indices?symbol={symbol}"
NSE_OC_EQUITY_URL = NSE_BASE + "/api/option-chain-equities?symbol={symbol}"
OC_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}
OC_INDEX_LOT_SIZES = {
    "NIFTY": 65,
    "BANKNIFTY": 30,
    "FINNIFTY": 60,
    "MIDCPNIFTY": 120,
    "NIFTYNXT50": 25,
}

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

# Global markets: prefer true front-month futures where Yahoo exposes a reliable continuous
# contract (`ROOT=F`). Those tickers trade around the clock and Yahoo retargets them at rollover.
#
# For several Europe/Asia benchmark rows Yahoo has no usable continuous future symbol, and
# Twelve Data still does not offer futures at all (their support docs list futures as unsupported).
# For those markets we use verified Twelve Data **spot index** symbols first where available,
# then fall back to Yahoo cash indices if Twelve Data coverage or plan access is missing.
# USD/JPY uses CME **6J=F** (inverse tick → USD/JPY display below).
GLOBAL_FUTURES = [
    # US Markets (CME equity index + RTY E-mini)
    ("S&P 500", "ES=F", "US MARKETS"),
    ("NASDAQ", "NQ=F", "US MARKETS"),
    ("DOW JONES", "YM=F", "US MARKETS"),
    ("RUSSELL 2000", "RTY=F", "US MARKETS"),
    # European Markets — Yahoo has no reliable broad equity-index =F for these; keep cash indices
    ("FTSE 100", "^FTSE", "EUROPEAN MARKETS"),
    ("DAX", "^GDAXI", "EUROPEAN MARKETS"),
    ("CAC 40", "^FCHI", "EUROPEAN MARKETS"),
    ("EURO STOXX 50", "^STOXX50E", "EUROPEAN MARKETS"),
    # Asian Markets
    ("GIFT NIFTY", None, "ASIAN MARKETS"),
    ("NIKKEI 225", "NIY=F", "ASIAN MARKETS"),  # CBOT Nikkei future (JPY); trades nearly 24h vs ^N225 cash
    ("HANG SENG", "^HSI", "ASIAN MARKETS"),
    ("SHANGHAI", "000001.SS", "ASIAN MARKETS"),
    ("KOSPI", "^KS11", "ASIAN MARKETS"),
    ("TAIWAN", "^TWII", "ASIAN MARKETS"),
    ("STRAITS TIMES", "^STI", "ASIAN MARKETS"),
    ("SET COMPOSITE", "^SET.BK", "ASIAN MARKETS"),
    ("JAKARTA", "^JKSE", "ASIAN MARKETS"),
    # Commodities (all =F; monthly/quarterly rolls handled by Yahoo)
    ("CRUDE OIL (WTI)", "CL=F", "COMMODITIES"),
    ("BRENT CRUDE", "BZ=F", "COMMODITIES"),
    ("NATURAL GAS", "NG=F", "COMMODITIES"),
    ("GOLD", "GC=F", "COMMODITIES"),
    ("SILVER", "SI=F", "COMMODITIES"),
    ("COPPER", "HG=F", "COMMODITIES"),
    # Currencies — CME FX futures for extended hours; 6J=F shown as USD/JPY via inverse quote
    ("EUR/USD", "6E=F", "CURRENCIES"),
    ("GBP/USD", "6B=F", "CURRENCIES"),
    ("USD/JPY", "6J=F", "CURRENCIES"),
    ("USD/INR", "INR=X", "CURRENCIES"),
    ("DXY (Dollar Index)", "DX-Y.NYB", "CURRENCIES"),
    # Crypto — CME Bitcoin / Ether futures (nearly 24h); Yahoo rolls front month via =F
    ("BITCOIN", "BTC=F", "CRYPTO"),
    ("ETHEREUM", "ETH=F", "CRYPTO"),
    # Bonds — CME yield futures (10Y / 2Y); replaces ^TNX/^IRX (IRX is 13W bill, not 2Y)
    ("US 10Y YIELD", "10Y=F", "BONDS"),
    ("US 2Y YIELD", "2YY=F", "BONDS"),
]

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# Yahoo symbol -> validated Twelve Data spot-index symbol/exchange for cash-index rows.
_GLOBAL_TD_FALLBACK: Dict[str, tuple] = {
    "^FTSE": ("FTSE", "LSE"),
    "^GDAXI": ("GDAXI", "XETR"),
    "^FCHI": ("FCHI", "Euronext"),
    "^HSI": ("HSI", "HKEX"),
    "000001.SS": ("000001", "SSE"),
    "^KS11": ("KOSPI", "KRX"),
    "^STI": ("STI", "SGX"),
    "^SET.BK": ("SET", "SET"),
    "^JKSE": ("JKSE", "IDX"),
}

_GLOBAL_TD_PREFER_FIRST = {
    "^GDAXI",
    "^FCHI",
    "^HSI",
    "000001.SS",
    "^KS11",
    "^STI",
    "^SET.BK",
    "^JKSE",
}


def _mins(hour: int, minute: int = 0) -> int:
    return hour * 60 + minute


def _d(year: int, month: int, day: int) -> date:
    return date(year, month, day)


_WEEKDAY_SESSION_0900_1700 = {wd: [(_mins(9, 0), _mins(17, 0))] for wd in range(5)}
_WEEKDAY_SESSION_0900_1730 = {wd: [(_mins(9, 0), _mins(17, 30))] for wd in range(5)}
_WEEKDAY_SESSION_0930_1600 = {wd: [(_mins(9, 30), _mins(12, 0)), (_mins(13, 0), _mins(16, 0))] for wd in range(5)}
_WEEKDAY_SESSION_0930_1500 = {wd: [(_mins(9, 30), _mins(11, 30)), (_mins(13, 0), _mins(15, 0))] for wd in range(5)}

_GLOBEX_24X5_SESSIONS = {
    0: [(_mins(0, 0), _mins(17, 0)), (_mins(18, 0), _mins(24, 0))],
    1: [(_mins(0, 0), _mins(17, 0)), (_mins(18, 0), _mins(24, 0))],
    2: [(_mins(0, 0), _mins(17, 0)), (_mins(18, 0), _mins(24, 0))],
    3: [(_mins(0, 0), _mins(17, 0)), (_mins(18, 0), _mins(24, 0))],
    4: [(_mins(0, 0), _mins(17, 0))],
    5: [],
    6: [(_mins(18, 0), _mins(24, 0))],
}

_GIFT_NIFTY_SESSIONS = {
    0: [(_mins(0, 0), _mins(2, 45)), (_mins(6, 30), _mins(15, 40)), (_mins(16, 35), _mins(24, 0))],
    1: [(_mins(0, 0), _mins(2, 45)), (_mins(6, 30), _mins(15, 40)), (_mins(16, 35), _mins(24, 0))],
    2: [(_mins(0, 0), _mins(2, 45)), (_mins(6, 30), _mins(15, 40)), (_mins(16, 35), _mins(24, 0))],
    3: [(_mins(0, 0), _mins(2, 45)), (_mins(6, 30), _mins(15, 40)), (_mins(16, 35), _mins(24, 0))],
    4: [(_mins(0, 0), _mins(2, 45)), (_mins(6, 30), _mins(15, 40)), (_mins(16, 35), _mins(24, 0))],
    5: [(_mins(0, 0), _mins(2, 45))],
    6: [],
}

_IDX_SESSIONS = {
    0: [(_mins(9, 0), _mins(12, 0)), (_mins(13, 30), _mins(16, 0))],
    1: [(_mins(9, 0), _mins(12, 0)), (_mins(13, 30), _mins(16, 0))],
    2: [(_mins(9, 0), _mins(12, 0)), (_mins(13, 30), _mins(16, 0))],
    3: [(_mins(9, 0), _mins(12, 0)), (_mins(13, 30), _mins(16, 0))],
    4: [(_mins(9, 0), _mins(11, 30)), (_mins(14, 0), _mins(16, 0))],
}

_SET_SESSIONS = {
    wd: [(_mins(10, 0), _mins(12, 30)), (_mins(14, 0), _mins(16, 30))]
    for wd in range(5)
}

_SET_2026_HOLIDAYS = {
    _d(2026, 1, 1),
    _d(2026, 2, 3),
    _d(2026, 4, 6),
    _d(2026, 4, 13),
    _d(2026, 4, 14),
    _d(2026, 4, 15),
    _d(2026, 5, 1),
    _d(2026, 5, 4),
    _d(2026, 5, 6),
    _d(2026, 6, 1),
    _d(2026, 6, 3),
    _d(2026, 7, 30),
    _d(2026, 8, 12),
    _d(2026, 10, 13),
    _d(2026, 10, 23),
    _d(2026, 12, 7),
    _d(2026, 12, 10),
    _d(2026, 12, 31),
}

_HKEX_2026_HOLIDAYS = {
    _d(2026, 1, 1),
    _d(2026, 2, 17),
    _d(2026, 2, 18),
    _d(2026, 2, 19),
    _d(2026, 4, 3),
    _d(2026, 4, 6),
    _d(2026, 4, 7),
    _d(2026, 5, 1),
    _d(2026, 5, 25),
    _d(2026, 6, 19),
    _d(2026, 7, 1),
    _d(2026, 10, 1),
    _d(2026, 10, 19),
    _d(2026, 12, 25),
}

_IDX_2026_HOLIDAYS = {
    _d(2026, 1, 1),
    _d(2026, 1, 16),
    _d(2026, 2, 16),
    _d(2026, 2, 17),
    _d(2026, 3, 18),
    _d(2026, 3, 19),
    _d(2026, 3, 20),
    _d(2026, 3, 23),
    _d(2026, 3, 24),
    _d(2026, 4, 3),
    _d(2026, 5, 1),
    _d(2026, 5, 14),
    _d(2026, 5, 15),
    _d(2026, 5, 27),
    _d(2026, 5, 28),
    _d(2026, 6, 1),
    _d(2026, 6, 16),
    _d(2026, 8, 17),
    _d(2026, 8, 25),
    _d(2026, 12, 24),
    _d(2026, 12, 25),
    _d(2026, 12, 31),
}

GLOBAL_MARKET_SESSION_META = {
    "S&P 500": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "NASDAQ": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "DOW JONES": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "RUSSELL 2000": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "FTSE 100": {"tz": "Europe/London", "venue": "LSE", "sessions": {wd: [(_mins(8, 0), _mins(16, 30))] for wd in range(5)}},
    "DAX": {"tz": "Europe/Berlin", "venue": "XETRA", "sessions": _WEEKDAY_SESSION_0900_1730},
    "CAC 40": {"tz": "Europe/Paris", "venue": "EURONEXT PARIS", "sessions": _WEEKDAY_SESSION_0900_1730},
    "EURO STOXX 50": {"tz": "Europe/Paris", "venue": "EURONEXT", "sessions": _WEEKDAY_SESSION_0900_1730},
    "GIFT NIFTY": {"tz": "Asia/Kolkata", "venue": "NSE IX", "sessions": _GIFT_NIFTY_SESSIONS},
    "NIKKEI 225": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "HANG SENG": {"tz": "Asia/Hong_Kong", "venue": "HKEX", "sessions": _WEEKDAY_SESSION_0930_1600, "holidays": _HKEX_2026_HOLIDAYS},
    "SHANGHAI": {"tz": "Asia/Shanghai", "venue": "SSE", "sessions": _WEEKDAY_SESSION_0930_1500},
    "KOSPI": {"tz": "Asia/Seoul", "venue": "KRX", "sessions": {wd: [(_mins(9, 0), _mins(15, 30))] for wd in range(5)}},
    "TAIWAN": {"tz": "Asia/Taipei", "venue": "TWSE", "sessions": {wd: [(_mins(9, 0), _mins(13, 30))] for wd in range(5)}},
    "STRAITS TIMES": {"tz": "Asia/Singapore", "venue": "SGX", "sessions": _WEEKDAY_SESSION_0900_1700},
    "SET COMPOSITE": {"tz": "Asia/Bangkok", "venue": "SET", "sessions": _SET_SESSIONS, "holidays": _SET_2026_HOLIDAYS},
    "JAKARTA": {"tz": "Asia/Jakarta", "venue": "IDX", "sessions": _IDX_SESSIONS, "holidays": _IDX_2026_HOLIDAYS},
    "CRUDE OIL (WTI)": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "BRENT CRUDE": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "NATURAL GAS": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "GOLD": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "SILVER": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "COPPER": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "EUR/USD": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "GBP/USD": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "USD/JPY": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "USD/INR": {"tz": "Asia/Kolkata", "venue": "FX", "sessions": _GIFT_NIFTY_SESSIONS},
    "DXY (Dollar Index)": {"tz": "America/New_York", "venue": "ICE", "sessions": _GLOBEX_24X5_SESSIONS},
    "BITCOIN": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "ETHEREUM": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "US 10Y YIELD": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
    "US 2Y YIELD": {"tz": "America/New_York", "venue": "CME GLOBEX", "sessions": _GLOBEX_24X5_SESSIONS},
}

TWELVE_DATA_QUOTE_URL = "https://api.twelvedata.com/quote"

YF_INDEX_MAP = {
    "NIFTY 50": "^NSEI",
    "NIFTY BANK": "^NSEBANK",
    "NIFTY NEXT 50": "^NSMIDCP",
    "NIFTY MIDCAP 50": "^NSEMDCP50",
    "NIFTY FINANCIAL SERVICES": "^CNXFIN",
    "NIFTY IT": "^CNXIT",
    "INDIA VIX": "^INDIAVIX",
}

# Yahoo index ticker -> Twelve Data (symbol, exchange) when Yahoo/NSE omit prices.
INDIA_YF_INDEX_TO_TWELVE: Dict[str, tuple] = {
    "^NSEI": ("NIFTY", "NSE"),
    "^NSEBANK": ("BANKNIFTY", "NSE"),
    "^NSEMDCP50": ("NIFTY_MIDCAP_50", "NSE"),
    "^CNXFIN": ("FINNIFTY", "NSE"),
    "^CNXIT": ("NIFTY_IT", "NSE"),
    "^INDIAVIX": ("INDIAVIX", "NSE"),
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

def _gdelt_doc_rss_url(query: str, hours: int = 3, max_records: int = 40) -> str:
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": str(max_records),
        "timespan": f"{hours}h",
        "sort": "datedesc",
        "format": "rssarchive",
    }
    return "https://api.gdeltproject.org/api/v2/doc/doc?" + urlencode(params)


GDELT_NEWS_ENABLED = os.getenv("GDELT_NEWS_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")

NEWS_FEEDS_INDIA = [
    "https://www.livemint.com/rss/markets",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.thehindubusinessline.com/markets/feeder/default.rss",
    "https://news.google.com/rss/search?q=(NIFTY+OR+SENSEX+OR+%22GIFT+Nifty%22+OR+%22Indian+rupee%22+OR+%22India+stocks%22)+when:2h&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=(RBI+OR+SEBI+OR+%22repo+rate%22+OR+%22rate+cut%22+OR+%22rate+hike%22+OR+FII+OR+FPI)+when:2h&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=(%22India+market%22+OR+Nifty+OR+Sensex+OR+%22Indian+rupee%22)+site:reuters.com+when:3h&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=(%22India+market%22+OR+Nifty+OR+Sensex+OR+%22Indian+rupee%22)+site:bloomberg.com+when:3h&hl=en-IN&gl=IN&ceid=IN:en",
]
NEWS_FEEDS_GLOBAL = [
    # Direct market/economy RSS only. Broad world/topstory feeds are intentionally excluded.
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://feeds.bloomberg.com/economics/news.rss",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.cnbc.com/id/100727362/device/rss/rss.html",
    "https://www.investing.com/rss/news_25.rss",
    "https://www.investing.com/rss/news_95.rss",
    "https://www.investing.com/rss/news_11.rss",
    "https://www.forexlive.com/feed/",
    "https://seekingalpha.com/market_currents.xml",

    # Google News macro queries use short windows and exact market-moving terms only.
    "https://news.google.com/rss/search?q=(%22crude+oil%22+OR+%22Brent+crude%22+OR+OPEC+OR+%22oil+prices%22)+when:2h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=(%22Federal+Reserve%22+OR+%22dollar+index%22+OR+%22treasury+yields%22+OR+%22bond+yields%22)+when:2h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=((tariffs+OR+%22trade+war%22+OR+%22oil+sanctions%22+OR+%22iran+sanctions%22+OR+%22russia+sanctions%22+OR+%22risk-off%22)+(%22stock+market%22+OR+stocks+OR+oil+OR+bonds))+when:2h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=((%22Strait+of+Hormuz%22+OR+%22Red+Sea%22+OR+%22oil+tanker%22+OR+%22Middle+East%22)+(oil+OR+shipping+OR+markets))+when:3h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=(%22S%26P+500%22+OR+Nasdaq+OR+%22Dow+Jones%22+OR+%22VIX%22+OR+%22stock+futures%22)+when:2h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=(%22market+moving%22+OR+%22markets+fall%22+OR+%22stocks+sink%22+OR+%22stocks+rally%22+OR+%22futures+fall%22+OR+%22futures+rise%22)+site:reuters.com+when:3h&hl=en&gl=US&ceid=US:en",
]
NEWS_FEEDS_GOLD_SILVER = [
    # No retail city-rate feeds. Keep only market-moving bullion context.
    "https://news.google.com/rss/search?q=(%22MCX+gold%22+OR+%22MCX+silver%22+OR+%22COMEX+gold%22+OR+%22COMEX+silver%22)+when:2h&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=(%22spot+gold%22+OR+%22spot+silver%22+OR+XAUUSD+OR+XAGUSD+OR+%22gold+futures%22+OR+%22silver+futures%22)+when:2h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=(%22central+bank+gold%22+OR+%22gold+reserves%22+OR+PBOC+gold+OR+%22World+Gold+Council%22)+when:6h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=(%22gold+prices%22+OR+%22spot+gold%22+OR+%22gold+futures%22+OR+%22silver+prices%22+OR+%22silver+futures%22)+site:reuters.com+when:3h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=(%22gold+prices%22+OR+%22spot+gold%22+OR+%22gold+futures%22+OR+%22silver+prices%22+OR+%22silver+futures%22)+site:bloomberg.com+when:3h&hl=en&gl=US&ceid=US:en",
]
NEWS_FEEDS_GDELT = [
    _gdelt_doc_rss_url(
        '("RBI" OR "SEBI" OR "Nifty" OR "Sensex" OR "Indian rupee" OR "India stocks" OR '
        '"Federal Reserve" OR "treasury yields" OR "dollar index" OR "Brent crude" OR "OPEC" OR '
        '"tariff" OR "sanctions" OR "Strait of Hormuz" OR "risk-off")',
        hours=3,
        max_records=50,
    )
] if GDELT_NEWS_ENABLED else []
NEWS_FEEDS = NEWS_FEEDS_INDIA + NEWS_FEEDS_GLOBAL + NEWS_FEEDS_GOLD_SILVER + NEWS_FEEDS_GDELT
_GLOBAL_FEEDS_SET = set(NEWS_FEEDS_GLOBAL + NEWS_FEEDS_GOLD_SILVER)
_GOLD_FEEDS_SET = set(NEWS_FEEDS_GOLD_SILVER)
_INDIA_FEEDS_SET = set(NEWS_FEEDS_INDIA)
_GDELT_FEEDS_SET = set(NEWS_FEEDS_GDELT)

TRADIENT_NEWS_URL = "https://api.tradient.org/v1/api/market/news"

# Live blog pages: polled on a short interval. URLs are never tied to one story — they come from
# LIVE_STORY_URLS (optional) plus automatic discovery (Google News RSS → publisher links that look
# like /live/ pages). Uses only URL shape + allowlisted domains, not topic keywords.
_LIVE_TITLE_BAD = (
    "subscribe", "newsletter", "cookie policy", "sign in", "follow us", "skip to",
    "privacy policy", "ad choices", "terms of use", "copyright",
)

# Market-event live discovery only. Broad world-live pages are too noisy for the terminal.
LIVE_DISCOVERY_FEEDS = [
    "https://news.google.com/rss/search?q=site:reuters.com+(markets+OR+stocks+OR+oil+OR+Fed+OR+Iran+OR+tariff)+(live+OR+%22live+updates%22)+when:3h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:cnbc.com+(markets+OR+stocks+OR+oil+OR+Fed+OR+inflation)+(live+OR+%22live+updates%22)+when:3h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:bbc.co.uk+(oil+OR+markets+OR+Iran+OR+tariff+OR+inflation)+(live+OR+%22live+updates%22)+when:3h&hl=en&gl=UK&ceid=GB:en",
]

_LIVE_PATH_HINTS = (
    "/live/", "-live-", "live-updates", "liveblog", "live-blog", "as-it-happened",
)

_DEFAULT_LIVE_DISCOVERY_DOMAINS = (
    "reuters.com", "cnbc.com", "bbc.co.uk", "bbc.com",
)


def _live_discovery_domains() -> List[str]:
    raw = os.getenv("LIVE_DISCOVERY_DOMAINS", "").strip()
    if raw:
        return [d.strip().lower() for d in raw.split(",") if d.strip()]
    return list(_DEFAULT_LIVE_DISCOVERY_DOMAINS)


def _host_matches_live_domain(host: str, domains: List[str]) -> bool:
    h = (host or "").lower()
    return any(d in h for d in domains)


def _is_probable_live_page(url: str, domains: List[str]) -> bool:
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.netloc:
        return False
    if not _host_matches_live_domain(p.netloc, domains):
        return False
    path = (p.path or "").lower()
    return any(hint in path for hint in _LIVE_PATH_HINTS)


def _live_brand_from_url(url: str) -> str:
    try:
        h = urlparse(url).netloc.lower().replace("www.", "")
        if "reuters" in h:
            return "Reuters"
        if "bbc" in h:
            return "BBC"
        if "guardian" in h:
            return "The Guardian"
        part = h.split(".")[0]
        return part.upper()[:14] if part else "LIVE"
    except Exception:
        return "LIVE"


def _live_story_id(url: str) -> str:
    u = url.rstrip("/")
    tail = u.split("/")[-1]
    return (tail[:48] or "live")


def _live_title_ok(title: str) -> bool:
    tl = title.lower().strip()
    if len(tl) < 20:
        return False
    if title.strip().startswith("http"):
        return False
    return not any(b in tl for b in _LIVE_TITLE_BAD)

NV_API_KEY = os.getenv("NV_API_KEY") or os.getenv("NVIDIA_API_KEY", "")
NV_API_URL = os.getenv("NV_API_URL", "https://integrate.api.nvidia.com/v1/chat/completions")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
ALPHA_VANTAGE_NEWS_ENABLED = bool(ALPHA_VANTAGE_API_KEY) and os.getenv(
    "ALPHA_VANTAGE_NEWS_ENABLED", "1"
).strip().lower() in ("1", "true", "yes", "on")
IS_RENDER = os.getenv("RENDER", "").strip().lower() == "true"
# Optional backup for global **index** rows Yahoo sometimes throttles or omits. Twelve Data returns
# spot indices (not exchange-traded futures). EU/Asia equity index futures (Eurex/ICE/HKFE) are not
# exposed as Yahoo `=F` roots (chart API 404) — see module comment on GLOBAL_FUTURES.
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
# Basic (free) plan: 8 API credits/min & 800/day. Throttle + cache to avoid 429s.
_TWELVE_FREE = os.getenv("TWELVE_DATA_FREE_TIER", "1").strip().lower() in (
    "1", "true", "yes", "basic", "free", "on",
)
TWELVE_DATA_MAX_PER_MINUTE = max(
    1,
    min(
        120,
        int(os.getenv("TWELVE_DATA_MAX_PER_MINUTE", "8" if _TWELVE_FREE else "55")),
    ),
)
TWELVE_DATA_QUOTE_CACHE_SECS = max(
    5,
    min(
        900,
        int(os.getenv("TWELVE_DATA_QUOTE_CACHE_SECS", "120" if _TWELVE_FREE else "20")),
    ),
)
TWELVE_DATA_STOCK_FALLBACK_CAP = max(
    0,
    int(os.getenv("TWELVE_DATA_STOCK_FALLBACK_CAP", "3" if _TWELVE_FREE else "50")),
)
TWELVE_DATA_CACHE_MAX_KEYS = max(32, min(512, int(os.getenv("TWELVE_DATA_CACHE_MAX_KEYS", "128"))))
NV_API_MODEL = os.getenv("NV_NEWS_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
NV_FAST_MODEL = os.getenv("NV_FAST_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
NV_ENABLE_THINKING = os.getenv("NV_ENABLE_THINKING", "0").strip().lower() in ("1", "true", "yes", "on")
RENDER_MINIMAL_MODE = os.getenv("RENDER_MINIMAL_MODE", "1" if IS_RENDER else "0").strip().lower() in ("1", "true", "yes", "on")
BACKGROUND_NEWS_ENABLED = os.getenv("BACKGROUND_NEWS_ENABLED", "0" if RENDER_MINIMAL_MODE else "1").strip().lower() in ("1", "true", "yes", "on")
BACKGROUND_LLM_ENABLED = os.getenv("BACKGROUND_LLM_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
LIVE_STORIES_ENABLED = os.getenv("LIVE_STORIES_ENABLED", "0" if RENDER_MINIMAL_MODE else "1").strip().lower() in ("1", "true", "yes", "on")
GLOBAL_STREAM_ENABLED = os.getenv("GLOBAL_STREAM_ENABLED", "0" if RENDER_MINIMAL_MODE else "1").strip().lower() in ("1", "true", "yes", "on")
NEWS_FEED_TIMEOUT_SECS = max(2, min(12, int(os.getenv("NEWS_FEED_TIMEOUT_SECS", "4" if IS_RENDER else "6"))))
NEWS_FEED_WORKERS = max(2, min(12, int(os.getenv("NEWS_FEED_WORKERS", "4" if IS_RENDER else "8"))))
MARKET_REFRESH_SECS = max(45, min(600, int(os.getenv("MARKET_REFRESH_SECS", "90" if IS_RENDER else "60"))))
NEWS_REFRESH_SECS = max(20, min(600, int(os.getenv("NEWS_REFRESH_SECS", "90" if RENDER_MINIMAL_MODE else ("60" if IS_RENDER else "30")))))
NEWS_LOOKBACK_HOURS = max(1, min(18, int(os.getenv("NEWS_LOOKBACK_HOURS", "2"))))
NEWS_GLOBAL_LOOKBACK_HOURS = max(1, min(12, int(os.getenv("NEWS_GLOBAL_LOOKBACK_HOURS", "2"))))
NEWS_GOLD_LOOKBACK_HOURS = max(1, min(24, int(os.getenv("NEWS_GOLD_LOOKBACK_HOURS", "2"))))
NEWS_MAX_VISIBLE_AGE_SECS = max(600, min(24 * 3600, int(os.getenv("NEWS_MAX_VISIBLE_AGE_SECS", str(2 * 3600)))))
NEWS_PENDING_LLM_TAIL = max(0, min(40, int(os.getenv("NEWS_PENDING_LLM_TAIL", "8"))))
BREAKING_CLUSTER_MIN_SOURCES = max(2, min(5, int(os.getenv("BREAKING_CLUSTER_MIN_SOURCES", "2"))))
BREAKING_CLUSTER_WINDOW_SECS = max(300, min(7200, int(os.getenv("BREAKING_CLUSTER_WINDOW_SECS", "5400"))))
BREAKING_PIN_TTL_SECS = max(300, min(7200, int(os.getenv("BREAKING_PIN_TTL_SECS", "5400"))))
BREAKING_PIN_MAX_ITEMS = max(3, min(30, int(os.getenv("BREAKING_PIN_MAX_ITEMS", "12"))))
GLOBAL_POLL_SECS = max(30, min(900, int(os.getenv("GLOBAL_POLL_SECS", "300" if RENDER_MINIMAL_MODE else "120"))))
LIVE_STORY_POLL_SECS = max(30, min(900, int(os.getenv("LIVE_STORY_POLL_SECS", "180" if IS_RENDER else "90"))))
LIVE_STORY_DISCOVER_SECS = max(120, min(1800, int(os.getenv("LIVE_STORY_DISCOVER_SECS", "600" if IS_RENDER else "300"))))
RENDER_MARKET_BOOT_DELAY_SECS = max(0, int(os.getenv("RENDER_MARKET_BOOT_DELAY_SECS", "4" if IS_RENDER else "0")))
RENDER_GLOBAL_BOOT_DELAY_SECS = max(0, int(os.getenv("RENDER_GLOBAL_BOOT_DELAY_SECS", "12" if IS_RENDER else "0")))
RENDER_GIFT_BOOT_DELAY_SECS = max(0, int(os.getenv("RENDER_GIFT_BOOT_DELAY_SECS", "6" if IS_RENDER else "0")))
RENDER_NEWS_BOOT_DELAY_SECS = max(0, int(os.getenv("RENDER_NEWS_BOOT_DELAY_SECS", "20" if IS_RENDER else "0")))
RENDER_LIVE_BOOT_DELAY_SECS = max(0, int(os.getenv("RENDER_LIVE_BOOT_DELAY_SECS", "45" if IS_RENDER else "0")))
RENDER_LLM_BOOT_DELAY_SECS = max(0, int(os.getenv("RENDER_LLM_BOOT_DELAY_SECS", "10" if IS_RENDER else "0")))
GLOBAL_STALE_SECS = max(30, min(1800, int(os.getenv("GLOBAL_STALE_SECS", "300" if RENDER_MINIMAL_MODE else "120"))))
NEWS_STALE_SECS = max(20, min(900, int(os.getenv("NEWS_STALE_SECS", "60" if RENDER_MINIMAL_MODE else "30"))))
LLM_BATCH_THRESHOLD = max(2, min(64, int(os.getenv("LLM_BATCH_THRESHOLD", "8"))))
LLM_BATCH_SIZE = max(2, min(16, int(os.getenv("LLM_BATCH_SIZE", "6"))))
NV_REQUESTS_PER_MINUTE = max(1, min(240, int(os.getenv("NV_REQUESTS_PER_MINUTE", "40"))))
NV_MAX_PARALLEL = max(1, min(8, int(os.getenv("NV_MAX_PARALLEL", "3" if IS_RENDER else "4"))))
REQUEST_LLM_SYNC_MAX_ITEMS = max(
    4,
    min(24, int(os.getenv("REQUEST_LLM_SYNC_MAX_ITEMS", "12" if IS_RENDER else "16"))),
)
DASHBOARD_SNAPSHOT_MIN_SECS = max(
    3,
    min(60, int(os.getenv("DASHBOARD_SNAPSHOT_MIN_SECS", "15" if IS_RENDER else "5"))),
)
WATCHLIST_NEWS_BACKFILL_SECS = max(
    60,
    min(1800, int(os.getenv("WATCHLIST_NEWS_BACKFILL_SECS", "300"))),
)
WATCHLIST_NEWS_BACKFILL_LIMIT = max(
    1,
    min(12, int(os.getenv("WATCHLIST_NEWS_BACKFILL_LIMIT", "4" if IS_RENDER else "6"))),
)
WATCHLIST_NEWS_ANALYZE_LIMIT = max(
    1,
    min(12, int(os.getenv("WATCHLIST_NEWS_ANALYZE_LIMIT", "4" if IS_RENDER else "6"))),
)
WATCHLIST_NEWS_MAX_ITEMS = max(
    1,
    min(10, int(os.getenv("WATCHLIST_NEWS_MAX_ITEMS", "3" if IS_RENDER else "5"))),
)
WATCHLIST_ARTICLE_TIMEOUT_SECS = max(
    4,
    min(20, int(os.getenv("WATCHLIST_ARTICLE_TIMEOUT_SECS", "10" if IS_RENDER else "12"))),
)
WATCHLIST_LLM_TIMEOUT_SECS = max(
    6,
    min(20, int(os.getenv("WATCHLIST_LLM_TIMEOUT_SECS", "12" if IS_RENDER else "20"))),
)
WATCHLIST_ARTICLE_BODY_MAX_CHARS = max(
    1000,
    min(20000, int(os.getenv("WATCHLIST_ARTICLE_BODY_MAX_CHARS", "8000"))),
)
WATCHLIST_ARTICLE_CACHE_SIZE = max(
    64,
    min(1024, int(os.getenv("WATCHLIST_ARTICLE_CACHE_SIZE", "256"))),
)
WATCHLIST_PANEL_REFRESH_SYMBOL_CAP = max(
    1,
    min(8, int(os.getenv("WATCHLIST_PANEL_REFRESH_SYMBOL_CAP", "3" if IS_RENDER else "5"))),
)
WATCHLIST_PANEL_SPARSE_TARGET_ITEMS = max(
    1,
    min(8, int(os.getenv("WATCHLIST_PANEL_SPARSE_TARGET_ITEMS", "3"))),
)
WATCHLIST_PANEL_SPARSE_REFRESH_SYMBOL_CAP = max(
    WATCHLIST_PANEL_REFRESH_SYMBOL_CAP,
    min(12, int(os.getenv("WATCHLIST_PANEL_SPARSE_REFRESH_SYMBOL_CAP", "6" if IS_RENDER else "8"))),
)
WATCHLIST_PANEL_DEEP_REFRESH_SYMBOL_CAP = max(
    WATCHLIST_PANEL_SPARSE_REFRESH_SYMBOL_CAP,
    min(24, int(os.getenv("WATCHLIST_PANEL_DEEP_REFRESH_SYMBOL_CAP", "12" if IS_RENDER else "16"))),
)
WATCHLIST_REFRESH_WORKERS = max(
    1,
    min(6, int(os.getenv("WATCHLIST_REFRESH_WORKERS", "4" if IS_RENDER else "6"))),
)
LLM_CACHE_SIZE = 500

_LLM_SYSTEM = (
    "You output ONLY a raw JSON array. No markdown, no explanation, no text before or after. "
    "Each element: "
    '{"idx":N,"stocks":[],"sentiment":"bullish"|"bearish"|"neutral",'
    '"impact":"high"|"medium"|"low","breaking":true|false,'
    '"gold_silver":true|false,"india_market_impact":true|false,'
    '"market_relevant":true|false,"company_specific":true|false}'
)
_LLM_PROMPT_PREFIX = (
    "Rules:\n"
    "- stocks: relevant NSE equity symbols only when the company/ticker is clearly identifiable, [] if none\n"
    "- breaking: true ONLY for macro/market-wide news (war, central bank, oil/gold shock, "
    "market crash/rally, FII/FPI, sanctions, geopolitics). "
    "false for stock results, SEBI filings, dividends, company-specific events.\n"
    "- gold_silver: true if about gold, silver, precious metals, bullion, MCX/COMEX metals, "
    "gold ETFs, sovereign gold bonds, central bank gold reserves/buying/selling, "
    "PBOC/China gold purchases, IMF gold, World Gold Council, LBMA, Shanghai Gold Exchange, "
    "silver supply/demand/deficit, mining gold/silver. false otherwise.\n"
    "- india_market_impact: true if the headline has ANY plausible channel to affect Indian markets "
    "(even low/indirect impact): INR, crude/oil, yields, global risk-on/off, sanctions, geopolitics, "
    "Fed/ECB/BoJ policy, global equities spillover, commodities, shipping/energy. "
    "false ONLY if clearly irrelevant to markets (sports/celebrity/space/recipes etc.).\n"
    "- market_relevant: true if the headline is relevant to markets/trading anywhere (macro, rates, "
    "inflation, FX, commodities, geopolitics that moves risk, broad indexes). false for human-interest.\n"
    "- company_specific: true if primarily about ONE specific company/stock/earnings/shares. "
    "false for macro/geopolitics.\n"
    "- Output the JSON array and nothing else.\n\n"
)

_WATCHLIST_LLM_SYSTEM = (
    "You output ONLY a raw JSON array with exactly one object. No markdown, no explanation. "
    'Object schema: {"important":true|false,"stocks":[],"sentiment":"bullish"|"bearish"|"neutral",'
    '"impact":"high"|"medium"|"low","gold_silver":true|false,"india_market_impact":true|false,'
    '"market_relevant":true|false,"company_specific":true|false}'
)
_WATCHLIST_LLM_PROMPT_PREFIX = (
    "Rules:\n"
    "- Read the full article text, not just the headline.\n"
    "- important: true ONLY if the article contains a fresh, material development for the watched stock "
    "(results, guidance, order win/loss, regulation, litigation, management change, capital raise/buyback, "
    "brokerage action, major macro impact clearly tied to the company, or a significant operational update).\n"
    "- important: false for passing mentions, live-blog tangents, broad market roundups, stale background, "
    "generic sector summaries, or articles where the watched stock is not meaningfully affected.\n"
    "- sentiment is the likely read-through for the watched stock after reading the article body.\n"
    "- stocks: include relevant NSE symbols only when clearly supported by the article.\n"
    "- company_specific: true if the article is mainly about one or a few companies; false for macro-only news.\n"
    "- market_relevant: true if the article matters to investors or traders; false for noise.\n"
    "- Output the JSON array and nothing else.\n\n"
)

_GOLD_SILVER_KW = {
    "gold", "silver", "precious metal", "precious metals",
    "bullion", "mcx gold", "mcx silver", "comex gold", "comex silver",
    "gold price", "silver price", "gold rate", "silver rate",
    "gold etf", "silver etf", "gold futures", "silver futures",
    "gold import", "gold export", "gold jewellery", "gold jewelry",
    "gold mining", "silver mining", "gold reserve", "gold reserves",
    "gold standard", "official gold", "sovereign gold",
    "xau", "xag", "gold rally", "gold crash", "gold forecast",
    "sovereign gold bond", "sgb", "hallmark",
    "gold demand", "gold supply", "gold smuggling",
    "yellow metal", "white metal",
    # Official sector / institutions / supply–demand studies
    "pboc", "people's bank", "shanghai gold exchange", "lbma",
    "world gold council", "gold demand trends", "silver institute",
    "silver supply", "silver demand", "silver deficit",
    "imf gold", "tonnes of gold", "tons of gold", "troy ounce",
    "photovoltaic silver", "solar silver", "industrial silver",
}

_GLOBAL_MARKET_KW = {
    # markets / assets
    "market", "markets", "stocks", "shares", "equities", "futures", "options",
    "bonds", "yield", "yields", "treasury", "gilts", "bund", "credit", "spread",
    "dow", "nasdaq", "s&p", "ftse", "dax", "nikkei", "hang seng", "sensex", "nifty",
    # macro
    "fed", "federal reserve", "ecb", "boe", "boj", "pboc", "central bank", "central banks",
    "rate cut", "rate hike", "interest rate", "inflation", "cpi", "ppi", "pce",
    "gdp", "pmi", "unemployment", "jobs report", "recession", "growth",
    # fx / commodities
    "forex", "fx", "currency", "dollar", "usd", "eur", "yen", "rupee", "inr",
    "oil", "crude", "brent", "wti", "gas", "lng", "gold", "silver", "copper",
    # risk / geopolitics that moves markets
    "sanction", "sanctions", "tariff", "tariffs", "embargo", "war", "geopolit",
    "strait of hormuz", "hormuz", "middle east", "red sea", "iran", "israel",
    "missile strike", "missile strikes", "drone attack", "drone attacks", "hostile drone",
    "oil tanker", "shipping lane",
    # company/market structure
    "earnings", "profit", "profits", "revenue", "guidance", "ipo", "listing", "buyback",
    "downgrade", "upgrade", "rating", "bank", "banking",
}

_GLOBAL_EXCLUDE_KW = {
    # obvious non-market categories that flood general news feeds
    "nasa", "space", "rocket", "astronaut", "mars", "moon", "spacex",
    "sports", "football", "soccer", "nba", "nfl", "mlb", "tennis", "cricket",
    "baseball", "inning", "innings", "guardians", "twins", "buxton",
    "rbi double", "go-ahead rbi", "runs batted", "homer",
    "celebrity", "movie", "music", "tv", "hollywood", "fashion",
    "recipe", "cooking", "travel", "weather",
    # general human-interest / politics noise
    "immigration agents", "detained", "deported",
    "easter email", "jesus", "church", "prayer",
    "toilet paper",
    "photo of earth", "far side",
    "soldier's wife", "military spouse",
    "scrimping and saving",
    "impeachment",
    # wellness / awareness / lifestyle filler
    "stress awareness", "awareness month", "awareness day", "awareness week",
    "coping with", "mental health tip", "self-care",
    "watch:", "watch live:", "how to watch",
    "horoscope", "zodiac", "astrology",
    "obituary", "funeral", "memorial service",
    "dog", "cat", "pet", "puppy", "kitten",
}

_LOW_SIGNAL_NEWS_KW = {
    "happy mother's day", "mothers day", "quotes", "wishes", "whatsapp status",
    "price today", "rate today", "rates today", "city-wise", "city wise",
    "check latest", "what to know", "what it means", "explained", "opinion",
    "next move", "make or break", "over the next", "portfolio", "stocks to buy",
    "best stocks", "should you buy", "should investors", "watchlist stocks",
    "week in focus", "weekly preview", "week ahead", "technical analysis",
    "price prediction", "forecast", "outlook", "recap",
    "final score", "match preview", "team news", "injury report",
    "goes yard", "home run", "runway", "class-action lawsuit",
    "covers latest", "the war today", "newsletter",
    "petrol, diesel prices", "fuel prices hiked or unchanged",
    "personal finance", "daily voice", "stock trader's guide",
    "guide to navigating", "speech full text", "full text",
    "closing bell", "top gainers & losers", "why yellow metal",
    "how will this impact", "could be confirmed next week",
    "closed in red", "closed higher", "closed lower", "ended higher",
    "ended lower", "says experts", "traders looking for",
    "senate candidate", "war spending",
    "officers' union", "officers’ union", "promotion system",
    "promotion policy", "time-bound promotion",
    "buy areas", "will the rupee hit", "by year-end",
    "city rates", "per tola", "24k", "22k", "18k",
    "simple steps aussies", "protect their finances",
}

_LOW_SIGNAL_SOURCE_KW = {
    "msn", "aol", "india.com", "indexbox", "whalesbook",
    "discoveryalert", "chosunbiz", "dailyhunt", "yeni safak",
    "espn", "toronto star", "killeen", "journal gazette",
    "daily pioneer", "magzter", "voice of alexandria", "news.com.au",
    "israel defense", "bitcoin news", "cryptonews", "lokmattimes",
    "citizen digital", "7news", "timesbull",
}

_MARKET_MOVING_KW = {
    "rbi", "sebi", "repo rate", "monetary policy", "rate cut", "rate hike",
    "inflation", "cpi", "ppi", "pce", "gdp", "pmi", "jobs report",
    "fii", "fpi", "dii", "foreign flows", "msci india",
    "nifty", "sensex", "gift nifty", "india vix", "rupee", "inr",
    "crude oil", "brent", "wti", "opec", "oil prices", "natural gas",
    "fed", "federal reserve", "treasury yield", "bond yields", "dollar index",
    "dxy", "yen", "yuan", "carry trade",
    "tariff", "tariffs", "trade war", "embargo", "iran sanctions",
    "russia sanctions", "oil sanctions", "missile strike", "drone attack", "ceasefire",
    "strait of hormuz", "hormuz", "red sea", "oil tanker", "shipping lane",
    "global selloff", "sell-off", "risk-off", "risk on", "risk-on",
    "market crash", "flash crash", "stocks sink", "stocks rally", "futures fall",
    "futures rise", "record high", "all-time high", "vix",
    "mcx", "comex", "spot gold", "spot silver", "gold futures", "silver futures",
    "central bank gold", "gold reserves",
}

_INDIA_IMPACT_HINT_KW = {
    "nifty", "sensex", "bse", "nse", "rupee", "inr",
    "rbi", "sebi ban", "sebi order", "sebi probe",
    "fii", "fpi", "dii", "msci india",
    "adani", "reliance", "tata", "infosys", "tcs", "hdfc",
    "oil price", "crude oil", "brent", "opec",
    "fed rate", "rate cut", "rate hike", "federal reserve",
    "tariff", "tariffs", "trade war", "iran sanctions", "russia sanctions", "oil sanctions",
    "iran", "israel", "middle east", "red sea", "strait of hormuz", "hormuz",
    "war escalat", "missile strike", "missile strikes", "drone attack", "drone attacks",
    "hostile drone", "oil tanker", "shipping lane",
    "global selloff", "risk-off", "emerging market",
    "gold price", "silver price",
}

_INDIA_NEWS_KW = {
    "india", "indian", "nifty", "sensex", "bse", "nse", "rupee", "inr",
    "rbi", "sebi", "mumbai", "delhi", "modi",
    "fii", "fpi", "dii", "msci india",
    "adani", "reliance", "tata", "infosys", "tcs", "hdfc", "wipro",
    "hcl tech", "bajaj", "icici", "sbi", "kotak", "axis bank",
    "mcx", "ncdex", "bse sensex", "nse nifty",
    "ipo india", "q4 results", "q3 results", "q2 results", "q1 results",
    "quarterly results", "annual results",
    "hindunilvr", "itc", "maruti", "titan", "bharti",
    "larsen", "ultratech", "mahindra", "coal india", "ntpc",
    "ongc", "power grid", "sun pharma", "dr reddy",
    "nifty 50", "nifty bank", "nifty it", "india vix",
    "dalal street", "bombay stock",
}

_KEYWORD_REGEX_CACHE: Dict[tuple[str, bool], re.Pattern] = {}
_KEYWORD_STEMS = {
    "sanction", "tariff", "geopolit", "war escalat", "central bank",
}

_BREAKING_CLUSTER_STOPWORDS = {
    "about", "after", "against", "amid", "among", "around", "before", "being",
    "could", "from", "have", "into", "latest", "live", "more", "news", "over",
    "report", "reported", "reports", "says", "show", "shows", "than", "that",
    "their", "this", "today", "update", "updates", "what", "when", "where",
    "which", "while", "with", "will", "your",
    "google", "reuters", "bloomberg", "cnbc", "mint", "ndtv", "moneycontrol",
    "economic", "economictimes", "times", "business", "standard", "market",
    "markets", "india", "indian",
}
_BREAKING_CLUSTER_NOISE_KW = {
    "price today", "prices today", "rate today", "rates today", "city-wise",
    "city wise", "check latest", "current price of", "price forecast for today",
    "streaming chart", "technical analysis", "where are the stops",
    "next move", "make or break", "over the next", "next 12 months",
    "next 6 months", "returns over", "your returns", "portfolio",
    "stocks to buy", "best stocks", "should you buy", "should investors",
    "what it means", "explained", "price prediction", "forecast",
    "outlook", "opinion", "analysis",
}
_BREAKING_CLUSTER_EVENT_KW = {
    "breaking", "just in", "flash", "alert", "urgent",
    "rbi", "repo rate", "rate cut", "rate hike", "policy decision",
    "fed", "federal reserve", "inflation", "cpi", "gdp",
    "rupee", "inr", "dollar index", "dxy", "treasury yield", "bond yield",
    "crude", "brent", "oil price", "hormuz", "red sea",
    "missile strike", "drone attack", "hostile drone", "ceasefire",
    "sanction", "tariff", "embargo", "nuclear",
    "nifty", "sensex", "futures", "crash", "plunge", "sell-off",
    "surge", "soar", "record high", "all-time high",
}


def _keyword_pattern(keyword: str) -> re.Pattern:
    """Match standalone keywords so short tokens like `nfl` do not hit `inflation`."""
    normalized = str(keyword or "").strip().lower()
    if not normalized:
        return re.compile(r"a^")
    stem = normalized in _KEYWORD_STEMS
    cache_key = (normalized, stem)
    cached = _KEYWORD_REGEX_CACHE.get(cache_key)
    if cached:
        return cached
    escaped = re.escape(normalized)
    right_boundary = "" if stem else r"(?![a-z0-9])"
    pattern = re.compile(r"(?<![a-z0-9])" + escaped + right_boundary, re.IGNORECASE)
    _KEYWORD_REGEX_CACHE[cache_key] = pattern
    return pattern


def _has_keyword(text: str, keywords) -> bool:
    haystack = str(text or "")
    if not haystack:
        return False
    return any(_keyword_pattern(kw).search(haystack) for kw in keywords)


def _breaking_cluster_tokens(title: str) -> Set[str]:
    text = str(title or "").lower()
    for sep in (" — ", " – ", " - "):
        if sep in text:
            text = text.rsplit(sep, 1)[0]
            break
    phrase_tokens: Set[str] = set()
    if "reserve bank of india" in text or "reserve bank india" in text:
        phrase_tokens.add("rbi")
    if "federal reserve" in text:
        phrase_tokens.add("fed")
    if "basis points" in text or "basis point" in text:
        phrase_tokens.add("bps")
    if "strait of hormuz" in text:
        phrase_tokens.add("hormuz")
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9&]+", " ", text)
    tokens: Set[str] = set(phrase_tokens)
    aliases = {
        "cuts": "cut", "cutting": "cut", "lower": "cut", "lowers": "cut",
        "reduced": "cut", "reduces": "cut", "reduction": "cut",
        "hikes": "hike", "raises": "hike", "raised": "hike",
        "rises": "rise", "rise": "rise", "gains": "rise", "gain": "rise",
        "jumps": "rise", "surges": "rise", "soars": "rise",
        "falls": "fall", "drops": "fall", "slumps": "fall", "slides": "fall",
        "crude": "oil", "brent": "oil", "wti": "oil",
        "equities": "stock", "stocks": "stock", "shares": "stock",
        "yields": "yield", "treasuries": "treasury",
        "sanctions": "sanction", "tariffs": "tariff",
        "missiles": "missile", "drones": "drone",
        "points": "bps", "point": "bps",
    }
    for raw in text.split():
        token = raw.strip("&")
        if not token or token in _BREAKING_CLUSTER_STOPWORDS:
            continue
        if token.isdigit():
            continue
        if len(token) < 3 and token not in {"ai", "it", "us", "uk"}:
            continue
        if len(token) > 4 and token.endswith("s"):
            token = token[:-1]
        token = aliases.get(token, token)
        if token and token not in _BREAKING_CLUSTER_STOPWORDS:
            tokens.add(token)
    return tokens


_TICKER_PAREN_RE = re.compile(r"\([A-Z]{1,5}(?:[:.][A-Z]{1,5})?\)")
_SHARES_MOVE_RE = re.compile(
    r"\b(shares?|stock)\b.*\b(rise|rises|rose|fall|falls|fell|drop|drops|dropped|"
    r"jump|jumps|jumped|slump|slumps|slumped|slide|slides|slid|surge|surges|surged|"
    r"plunge|plunges|plunged|soar|soars|soared|rally|rallies|rallied)\b",
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
    "war", "sanction", "sanctions", "tariff", "tariffs", "embargo", "geopolit",
    "missile strike", "missile strikes", "drone attack", "drone attacks",
    "hostile drone", "hormuz", "red sea", "middle east",
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
    "upgrade", "downgrade", "price target", "target price", "target raised", "target cut",
    "rating", "ipo", "listing",
    "block deal", "bulk deal", "stake",
    # Corporate filings / compliance noise (should NEVER be macro breaking)
    "sebi compliance", "compliance certificate", "compliance cert",
    "demat", "demat certificate", "depository", "encumbrance", "share encumbrance",
    "disclosure", "annual disclosure", "non-large corporate", "non large corporate",
    "trading window", "special transfer window", "physical share transfer",
    "trading approval", "gets trading approval", "trading nod",
    "regulation 74(5)", "reg 74(5)", "74(5)",
    "regulation 7(3)", "reg 7(3)", "7(3)",
    "regulation 40", "reg 40", "40(10)", "40(9)",
    "clarifies bse query", "bse query", "seeks clarification", "clarification",
    "cirs", "cirp", "insolvency", "resolution professional",
    "independent director resigns", "director resigns", "company secretary resigns",
    "rights issue", "egm set for", "board meeting",
    "dp certificate", "dematerialization",
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
        user_agent = random.choice(_USER_AGENTS)
        self._session.headers.update({
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": NSE_BASE,
            "Referer": NSE_BASE + "/option-chain",
            "X-Requested-With": "XMLHttpRequest",
        })
        warm_headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": NSE_BASE,
        }
        try:
            self._session.get(NSE_BASE + "/option-chain", headers=warm_headers, timeout=10)
            self._session.get(NSE_BASE, headers=warm_headers, timeout=10)
        except Exception:
            pass
        self._last_cookie_time = now

    def get(self, url: str, retries: int = 2) -> Optional[dict]:
        self._refresh_cookies()
        for attempt in range(retries + 1):
            try:
                r = self._session.get(url, timeout=15)
                if r.status_code == 200:
                    try:
                        return r.json()
                    except Exception as exc:
                        print(f"[NSE] JSON decode failed for {url.split('?')[0]}: {exc}")
                        return None
                if r.status_code in {401, 403} or (r.status_code == 404 and "option-chain" in url):
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
        self._app_role = (os.getenv("APP_ROLE", "combined") or "combined").strip().lower()
        if self._app_role not in {"combined", "web", "worker"}:
            self._app_role = "combined"
        self._nse = NseSession()
        self._dashboard_store = DashboardStore()
        self._watchlist_store = WatchlistStore()
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
        self._llm_task: Optional[asyncio.Task] = None
        self._live_task: Optional[asyncio.Task] = None
        self._ws_clients: Set = set()
        self._refresh_count = 0
        self._llm_cache: OrderedDict = OrderedDict()
        self._global_futures: List[dict] = []
        self._live_lock = threading.Lock()
        self._news_lock = threading.Lock()
        self._live_seen: Set[str] = set()
        self._live_log_state: Dict[str, str] = {}
        self._asyncio_loop: Optional[asyncio.AbstractEventLoop] = None
        # Dynamic live-blog URLs (auto-discovered); LRU by insertion order
        self._discovered_live_urls: OrderedDict[str, bool] = OrderedDict()
        self._live_fetch_failures: Dict[str, int] = {}
        self._max_discovered_live_urls = max(3, min(20, int(os.getenv("LIVE_DISCOVERY_MAX_URLS", "8"))))
        self._live_fail_drop_after = max(5, int(os.getenv("LIVE_DISCOVERY_DROP_FAILS", "18")))
        # Global markets: Yahoo WebSocket streaming for true real-time prices.
        self._global_task: Optional[asyncio.Task] = None
        self._gift_task: Optional[asyncio.Task] = None
        self._last_global_update: Optional[str] = None
        self._last_gift_fetch_ts: float = 0.0
        self._global_tick_count = 0
        self._global_stream_connected = False
        # Snapshot of previous_close per symbol (seeded by first yfinance fetch)
        self._global_prev_close: Dict[str, float] = {}
        # Live prices from streaming (symbol -> {price, change, change_pct, ...})
        self._global_live: Dict[str, dict] = {}
        self._global_dirty = False  # True when new ticks arrived since last broadcast
        self._news_refresh_lock = threading.Lock()
        _gift_secs = int(os.getenv("GIFT_NIFTY_REFRESH_SECS", "15"))
        self._gift_refresh_secs = max(8, min(300, _gift_secs))
        _bcast_ms = int(os.getenv("GLOBAL_BROADCAST_MS", "1000"))
        self._global_broadcast_interval = max(200, min(10000, _bcast_ms)) / 1000.0
        self._last_market_refresh_ts = 0.0
        self._last_news_refresh_ts = 0.0
        self._last_global_refresh_ts = 0.0
        # LLM classification stack (LIFO). New headlines push here; worker pops one at a time.
        self._llm_stack: List[dict] = []
        self._llm_pending: Set[str] = set()
        self._llm_lock = threading.Lock()
        self._nv_rate_lock = threading.Lock()
        self._nv_call_times: deque = deque()
        self._nv_parallel_sem = threading.BoundedSemaphore(NV_MAX_PARALLEL)
        self._watchlist_article_lock = threading.Lock()
        self._watchlist_article_cache: OrderedDict = OrderedDict()
        self._watchlist_article_body_cache: OrderedDict = OrderedDict()
        self._search_cache: OrderedDict = OrderedDict()
        self._search_cache_ttl = 300
        self._search_cache_max = 64
        self._equity_catalog: List[dict] = []
        self._equity_catalog_loaded_at = 0.0
        self._equity_catalog_ttl = 12 * 60 * 60
        self._ensure_ready_lock = threading.Lock()
        self._snapshot_lock = threading.Lock()
        self._last_snapshot_save_ts = 0.0
        self._snapshot_loaded = False
        self._watchlist_news_refresh_at: Dict[str, float] = {}
        # Twelve Data rate limits (Basic plan: 8 credits/min); see TWELVE_DATA_* env vars.
        self._twelve_lock = threading.Lock()
        self._twelve_call_times: deque = deque()
        self._twelve_quote_cache: OrderedDict = OrderedDict()
        self._twelve_log_throttle = 0.0
        self._load_persisted_snapshot()

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

    @property
    def background_enabled(self) -> bool:
        return self._app_role in {"combined", "worker"}

    @staticmethod
    def _market_timepoint(tz_obj, day: date, minute: int) -> datetime:
        base = tz_obj.localize(datetime.combine(day, datetime.min.time()))
        return base + timedelta(minutes=minute)

    @staticmethod
    def _format_market_countdown(delta: timedelta) -> str:
        total_minutes = max(0, int(delta.total_seconds() // 60))
        days, rem = divmod(total_minutes, 24 * 60)
        hours, minutes = divmod(rem, 60)
        parts = []
        if days:
            parts.append(f"{days}d")
        if days or hours:
            parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        return " ".join(parts)

    @staticmethod
    def _fallback_global_market_meta(label: str, symbol: str) -> Optional[dict]:
        if label in GLOBAL_MARKET_SESSION_META:
            return GLOBAL_MARKET_SESSION_META[label]
        if symbol and symbol.endswith("=F"):
            return {
                "tz": "America/New_York",
                "venue": "CME GLOBEX",
                "sessions": _GLOBEX_24X5_SESSIONS,
            }
        return None

    def _describe_global_market_session(self, label: str, symbol: Optional[str]) -> dict:
        meta = self._fallback_global_market_meta(label, symbol or "")
        if not meta:
            return {}

        tz_obj = pytz.timezone(meta["tz"])
        now_local = datetime.now(tz_obj)
        today = now_local.date()
        current_minute = (
            now_local.hour * 60
            + now_local.minute
            + (now_local.second / 60.0)
        )
        holidays = meta.get("holidays") or set()
        sessions_by_day = meta.get("sessions") or {}
        today_sessions = list(sessions_by_day.get(today.weekday(), []))
        holiday_today = today in holidays

        current_session_end = None
        if not holiday_today:
            for start_minute, end_minute in today_sessions:
                if start_minute <= current_minute < end_minute:
                    current_session_end = self._market_timepoint(tz_obj, today, end_minute)
                    break

        next_open = None
        next_reason = "closed"
        for day_offset in range(0, 15):
            target_day = today + timedelta(days=day_offset)
            if target_day in holidays:
                continue
            day_sessions = list(sessions_by_day.get(target_day.weekday(), []))
            if not day_sessions:
                continue
            for start_minute, _ in day_sessions:
                open_dt = self._market_timepoint(tz_obj, target_day, start_minute)
                if open_dt > now_local:
                    next_open = open_dt
                    if day_offset == 0 and any(end_minute <= current_minute for _, end_minute in today_sessions):
                        next_reason = "break"
                    else:
                        next_reason = "closed"
                    break
            if next_open:
                break

        local_clock = now_local.strftime("%a %H:%M %Z")
        session_venue = meta.get("venue") or ""

        if current_session_end:
            return {
                "session_status": "OPEN",
                "session_reason": "open",
                "session_hint": f"Closes in {self._format_market_countdown(current_session_end - now_local)}",
                "session_local_time": local_clock,
                "session_venue": session_venue,
                "session_next_change_at": current_session_end.strftime("%a %H:%M %Z"),
                "session_countdown_mins": max(0, int((current_session_end - now_local).total_seconds() // 60)),
                "session_timezone": meta["tz"],
            }

        closed_prefix = "Reopens in" if holiday_today or next_reason == "break" else "Opens in"
        next_change_at = next_open.strftime("%a %H:%M %Z") if next_open else None
        hint = "Closed"
        countdown_mins = None
        if next_open:
            hint = f"{closed_prefix} {self._format_market_countdown(next_open - now_local)}"
            countdown_mins = max(0, int((next_open - now_local).total_seconds() // 60))
        status = "HOLIDAY" if holiday_today else "CLOSED"
        reason = "holiday" if holiday_today else next_reason
        return {
            "session_status": status,
            "session_reason": reason,
            "session_hint": hint,
            "session_local_time": local_clock,
            "session_venue": session_venue,
            "session_next_change_at": next_change_at,
            "session_countdown_mins": countdown_mins,
            "session_timezone": meta["tz"],
        }

    def _decorate_global_market_row(self, row: Optional[dict]) -> Optional[dict]:
        if not row:
            return None
        payload = dict(row)
        payload.update(
            self._describe_global_market_session(
                str(payload.get("name") or ""),
                payload.get("symbol"),
            )
        )
        return payload

    def _has_dashboard_state(self) -> bool:
        return bool(
            self._indices
            or self._stocks
            or self._news
            or self._global_futures
            or self._gift_nifty
            or self._sectors
        )

    def _apply_dashboard_snapshot(self, snapshot: dict):
        self._indices = list(snapshot.get("indices") or [])
        stock_rows = list(snapshot.get("stocks") or [])
        self._stocks = {
            item["symbol"]: item
            for item in stock_rows
            if isinstance(item, dict) and item.get("symbol")
        }
        self._movers = snapshot.get("movers") or {"gainers": [], "losers": []}
        self._news = list(snapshot.get("news") or [])
        self._sectors = list(snapshot.get("sectors") or [])
        self._gift_nifty = snapshot.get("gift_nifty")
        self._global_futures = list(snapshot.get("global_futures") or [])
        self._last_global_update = snapshot.get("last_global_update")
        self._global_stream_connected = bool(snapshot.get("global_streaming", False)) and self.background_enabled
        self._last_update = snapshot.get("last_update")
        self._snapshot_loaded = True

    def _load_persisted_snapshot(self) -> bool:
        try:
            snapshot = self._dashboard_store.load_snapshot()
        except Exception as exc:
            print(f"[DashboardStore] load failed: {exc}")
            return False
        if not snapshot:
            return False
        self._apply_dashboard_snapshot(snapshot)
        return True

    def _breadth_payload(self) -> dict:
        adv = dec = 0
        for idx in self._indices:
            if idx.get("advances"):
                adv = idx["advances"]
                dec = idx.get("declines", 0)
                break
        if not adv and self._stocks:
            adv = sum(1 for stock in self._stocks.values() if stock.get("change_pct", 0) > 0)
            dec = sum(1 for stock in self._stocks.values() if stock.get("change_pct", 0) < 0)
        return {"advances": adv, "declines": dec}

    def _overview_payload(self) -> dict:
        return {
            "indices": self._indices,
            "movers": self._movers,
            "sectors": self._sectors,
            "gift_nifty": self._gift_nifty,
            "market_status": self.market_status,
            "last_update": self._last_update,
            "time": datetime.now(IST).strftime("%H:%M:%S"),
            "breadth": self._breadth_payload(),
        }

    def _dashboard_payload(self) -> dict:
        return {
            **self._overview_payload(),
            "indices": self._indices,
            "stocks": list(self._stocks.values()),
            "news": self._news,
            "sector_map": SECTOR_MAP,
            "global_futures": self._global_futures,
            "last_global_update": self._last_global_update,
            "global_streaming": self._global_stream_connected,
            "news_llm_pending": self._llm_stack_pending_count(),
            "news_llm_enabled": bool(NV_API_KEY),
        }

    def persist_dashboard_snapshot(self, force: bool = False):
        if not self._has_dashboard_state():
            return False
        now = time.time()
        with self._snapshot_lock:
            if not force and now - self._last_snapshot_save_ts < DASHBOARD_SNAPSHOT_MIN_SECS:
                return False
            try:
                self._dashboard_store.save_snapshot(self._dashboard_payload())
                self._last_snapshot_save_ts = now
                return True
            except Exception as exc:
                print(f"[DashboardStore] save failed: {exc}")
                return False

    # ── lifecycle ───────────────────────────────────────────────────────

    async def _start_loop_after(self, label: str, delay_secs: int, loop_coro):
        try:
            if delay_secs > 0:
                print(f"[Startup] delaying {label} loop by {delay_secs}s")
                await asyncio.sleep(delay_secs)
            await loop_coro()
        except asyncio.CancelledError:
            return

    async def start(self):
        if self._running:
            return
        self._load_persisted_snapshot()
        if not self.background_enabled:
            print(f"[Startup] APP_ROLE={self._app_role} — serving cached dashboard only")
            return
        self._running = True
        self._asyncio_loop = asyncio.get_running_loop()
        self._market_task = asyncio.create_task(self._market_loop())
        self._gift_task = asyncio.create_task(
            self._start_loop_after("gift", RENDER_GIFT_BOOT_DELAY_SECS, self._gift_nifty_loop)
        )
        self._global_task = asyncio.create_task(
            self._start_loop_after("global", RENDER_GLOBAL_BOOT_DELAY_SECS, self._global_stream_loop)
        )
        self._news_task = asyncio.create_task(
            self._start_loop_after("news", RENDER_NEWS_BOOT_DELAY_SECS, self._news_loop)
        )
        self._llm_task = asyncio.create_task(
            self._start_loop_after("llm", RENDER_LLM_BOOT_DELAY_SECS, self._llm_loop)
        )
        self._live_task = asyncio.create_task(
            self._start_loop_after("live", RENDER_LIVE_BOOT_DELAY_SECS, self._live_stories_loop)
        )

    async def stop(self):
        if not self.background_enabled:
            return
        self._running = False
        tasks = [t for t in (self._market_task, self._global_task, self._gift_task,
                              self._news_task, self._llm_task, self._live_task) if t]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._market_task = None
        self._global_task = None
        self._gift_task = None
        self._news_task = None
        self._llm_task = None
        self._live_task = None
        self._asyncio_loop = None

    def _llm_stack_pending_count(self) -> int:
        with self._llm_lock:
            return len(self._llm_stack)

    def _valid_equity_symbols(self) -> Set[str]:
        symbols = set(SECTOR_MAP.keys()) | set(YF_COMPANY_NAMES.keys()) | set(self._stocks.keys())
        try:
            symbols.update(item["symbol"] for item in self._load_equity_catalog())
        except Exception:
            pass
        return symbols

    def _schedule_llm_stack_broadcast(self):
        loop = getattr(self, "_asyncio_loop", None)
        if not loop or not loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast("llm_queue"), loop)
        except Exception:
            pass

    def _push_llm_stack(self, items: List[dict]):
        """Push uncached items onto the LLM stack (LIFO). Thread-safe."""
        if not NV_API_KEY or not items:
            return
        added = False
        with self._llm_lock:
            for it in items:
                title = (it.get("title") or "").strip()
                if not title:
                    continue
                cache_key = title[:80].lower()
                if cache_key in self._llm_cache or cache_key in self._llm_pending:
                    continue
                self._llm_stack.append({"cache_key": cache_key, "title": title})
                self._llm_pending.add(cache_key)
                added = True
        if added:
            self._schedule_llm_stack_broadcast()

    def _pop_llm_stack(self) -> Optional[dict]:
        """Pop one item from LLM stack (LIFO). Thread-safe."""
        with self._llm_lock:
            if not self._llm_stack:
                return None
            return self._llm_stack.pop()

    def _pop_llm_stack_batch(self, limit: int) -> List[dict]:
        """Pop up to `limit` items from the LLM stack (LIFO). Thread-safe."""
        with self._llm_lock:
            if not self._llm_stack:
                return []
            count = max(1, min(limit, len(self._llm_stack)))
            jobs = self._llm_stack[-count:]
            del self._llm_stack[-count:]
            jobs.reverse()
            return jobs

    def _mark_llm_done(self, cache_key: str):
        with self._llm_lock:
            self._llm_pending.discard(cache_key)

    def _await_nv_request_budget(self):
        while True:
            wait_for = 0.0
            with self._nv_rate_lock:
                now = time.monotonic()
                while self._nv_call_times and now - self._nv_call_times[0] >= 60.0:
                    self._nv_call_times.popleft()
                if len(self._nv_call_times) < NV_REQUESTS_PER_MINUTE:
                    self._nv_call_times.append(now)
                    return
                wait_for = max(0.05, 60.0 - (now - self._nv_call_times[0]) + 0.05)
            time.sleep(wait_for)

    def _nv_post_json(self, payload: dict, timeout: float) -> Optional[requests.Response]:
        if not NV_API_KEY:
            return None
        self._nv_parallel_sem.acquire()
        try:
            self._await_nv_request_budget()
            return requests.post(
                NV_API_URL,
                headers={"Authorization": f"Bearer {NV_API_KEY}", "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
        except Exception:
            return None
        finally:
            self._nv_parallel_sem.release()

    @staticmethod
    def _split_llm_jobs(jobs: List[dict], max_parallel: int) -> List[List[dict]]:
        if not jobs:
            return []
        lane_count = max(1, min(max_parallel, len(jobs)))
        if len(jobs) <= 1:
            return [jobs]
        lane_count = min(lane_count, max(1, (len(jobs) + LLM_BATCH_SIZE - 1) // LLM_BATCH_SIZE))
        chunk_size = max(1, min(LLM_BATCH_SIZE, (len(jobs) + lane_count - 1) // lane_count))
        return [jobs[idx:idx + chunk_size] for idx in range(0, len(jobs), chunk_size)]

    def _llm_run_chunk(self, jobs: List[dict], model: str) -> List[Optional[dict]]:
        titles = [job["title"] for job in jobs]
        if not titles:
            return []
        if len(titles) == 1:
            return [self._llm_call_one(titles[0], model)]
        return self._llm_call_many(titles, model)

    def _apply_llm_result_to_news(self, cache_key: str, result: dict):
        """Apply cached LLM result to any matching items in current news list."""
        for item in self._news:
            if item.get("title", "")[:80].lower() != cache_key:
                continue
            stocks = result.get("stocks") or []
            if stocks:
                item["watchlist_stocks"] = list(dict.fromkeys(stocks))
            item["sentiment"] = result.get("sentiment", item.get("sentiment", "neutral"))
            item["impact"] = result.get("impact", item.get("impact", "low"))
            item["gold_silver"] = bool(result.get("gold_silver", item.get("gold_silver", False)))
            item["india_market_impact"] = bool(result.get("india_market_impact", False))
            item["market_relevant"] = bool(result.get("market_relevant", item.get("market_relevant", False)))
            item["company_specific"] = bool(result.get("company_specific", item.get("company_specific", False)))
            item["llm_classified"] = True
            # LLM output is a hint; final BREAKING requires 2+ media-source confirmation.
            brk = item.get("india_market_impact", False)
            if item.get("stock_event"):
                brk = False
            if item.get("company_specific"):
                brk = False
            item["breaking_hint"] = bool(item.get("breaking_hint") or brk)
            item["breaking"] = bool(
                item.get("breaking_confirmed")
                and float(item.get("breaking_expires_at") or 0) > time.time()
            )
            item["breaking_pinned"] = item["breaking"]

    def _recluster_current_news(self):
        """Re-evaluate breaking clusters after late LLM classification updates."""
        with self._news_lock:
            if not self._news:
                return
            self._apply_breaking_clusters(self._news)
            self._news = self._sort_news_with_breaking_pins(self._news)[:130]

    @staticmethod
    def _prepare_nv_messages(model: str, messages: List[dict]) -> List[dict]:
        if "nemotron" not in model.lower():
            return messages
        patched: List[dict] = []
        added_prefix = False
        for msg in messages:
            if msg.get("role") == "system" and isinstance(msg.get("content"), str):
                content = msg["content"]
                if not content.lstrip().startswith("/no_think"):
                    content = "/no_think\n" + content
                patched.append({**msg, "content": content})
                added_prefix = True
            else:
                patched.append(msg)
        if not added_prefix:
            patched.insert(0, {"role": "system", "content": "/no_think"})
        return patched

    @classmethod
    def _build_nv_payload(cls, model: str, messages: List[dict], temperature: float, max_tokens: int) -> dict:
        chat_template_kwargs = (
            {"thinking": True}
            if NV_ENABLE_THINKING
            else {"enable_thinking": False, "clear_thinking": True}
        )
        payload = {
            "model": model,
            "messages": cls._prepare_nv_messages(model, messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": 1.0,
            "chat_template_kwargs": chat_template_kwargs,
        }
        if model.startswith("moonshotai/kimi-k2.5") and not NV_ENABLE_THINKING:
            payload["thinking"] = {"type": "disabled"}
        return payload

    @staticmethod
    def _extract_json_array_text(text: str) -> str:
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            return text[start:end + 1]
        if text.startswith("{"):
            return "[" + text + "]"
        return text

    @staticmethod
    def _normalize_llm_entry(job: dict, entry: Optional[dict], valid_syms: Set[str]) -> dict:
        if not entry or not isinstance(entry, dict):
            return {
                "stocks": [],
                "sentiment": "neutral",
                "impact": "low",
                "breaking": False,
                "gold_silver": False,
                "india_market_impact": False,
                "market_relevant": False,
                "company_specific": True,
            }
        stocks = [s for s in entry.get("stocks", []) if s in valid_syms]
        sentiment = entry.get("sentiment", "neutral")
        if sentiment not in ("bullish", "bearish", "neutral"):
            sentiment = "neutral"
        impact = entry.get("impact", "low")
        if impact not in ("high", "medium", "low"):
            impact = "low"
        is_gs = bool(entry.get("gold_silver", False))
        is_india = bool(entry.get("india_market_impact", False))
        if "market_relevant" in entry:
            is_market_rel = bool(entry.get("market_relevant"))
        else:
            is_market_rel = bool(is_india or is_gs or entry.get("breaking", False))
        if "company_specific" in entry:
            is_comp = bool(entry.get("company_specific"))
        else:
            is_comp = bool(job.get("company_specific", False))
        is_brk = is_india
        if job.get("stock_event") or is_comp or job.get("company_specific"):
            is_brk = False
        is_market_rel = bool(is_market_rel or stocks or is_india or is_gs or is_brk)
        return {
            "stocks": stocks,
            "sentiment": sentiment,
            "impact": impact,
            "breaking": is_brk,
            "gold_silver": is_gs,
            "india_market_impact": is_india,
            "market_relevant": is_market_rel,
            "company_specific": is_comp,
        }

    async def _llm_loop(self):
        """Background LLM worker: drains headline stack with bounded mini-batches."""
        try:
            while self._running:
                with self._llm_lock:
                    backlog = len(self._llm_stack)
                dispatch_size = min(backlog, max(1, LLM_BATCH_SIZE * NV_MAX_PARALLEL)) if backlog >= LLM_BATCH_THRESHOLD else 1
                jobs = self._pop_llm_stack_batch(dispatch_size)
                if not jobs:
                    await asyncio.sleep(0.25)
                    continue
                await self._broadcast("llm_queue")
                model = NV_FAST_MODEL if len(jobs) > 1 or backlog > LLM_BATCH_THRESHOLD else NV_API_MODEL
                chunks = self._split_llm_jobs(jobs, NV_MAX_PARALLEL)
                try:
                    valid_syms = self._valid_equity_symbols()
                    chunk_results = await asyncio.gather(
                        *(asyncio.to_thread(self._llm_run_chunk, chunk, model) for chunk in chunks),
                        return_exceptions=True,
                    )
                    for chunk, entries in zip(chunks, chunk_results):
                        if isinstance(entries, Exception) or not isinstance(entries, list):
                            entries = [None] * len(chunk)
                        for job, entry in zip(chunk, entries):
                            cache_key = job["cache_key"]
                            result = self._normalize_llm_entry(job, entry, valid_syms)
                            self._llm_cache[cache_key] = result
                            while len(self._llm_cache) > LLM_CACHE_SIZE:
                                self._llm_cache.popitem(last=False)
                            self._apply_llm_result_to_news(cache_key, result)
                    self._recluster_current_news()
                    await self._broadcast("news")
                except Exception:
                    traceback.print_exc()
                finally:
                    for job in jobs:
                        self._mark_llm_done(job["cache_key"])
        except asyncio.CancelledError:
            return

    def process_llm_queue_sync(self, max_items: int = 24) -> int:
        """Drain some or all queued LLM jobs synchronously for prewarm-only flows."""
        processed = 0
        while processed < max_items:
            with self._llm_lock:
                backlog = len(self._llm_stack)
            if backlog <= 0:
                break
            dispatch_cap = min(backlog, max_items - processed)
            dispatch_size = min(dispatch_cap, max(1, LLM_BATCH_SIZE * NV_MAX_PARALLEL)) if backlog >= LLM_BATCH_THRESHOLD else 1
            jobs = self._pop_llm_stack_batch(dispatch_size)
            if not jobs:
                break
            model = NV_FAST_MODEL if len(jobs) > 1 or backlog > LLM_BATCH_THRESHOLD else NV_API_MODEL
            chunks = self._split_llm_jobs(jobs, NV_MAX_PARALLEL)
            try:
                valid_syms = self._valid_equity_symbols()
                with ThreadPoolExecutor(max_workers=max(1, min(len(chunks), NV_MAX_PARALLEL))) as pool:
                    future_map = {pool.submit(self._llm_run_chunk, chunk, model): chunk for chunk in chunks}
                    for future, chunk in future_map.items():
                        try:
                            entries = future.result()
                        except Exception:
                            entries = [None] * len(chunk)
                        for job, entry in zip(chunk, entries):
                            cache_key = job["cache_key"]
                            result = self._normalize_llm_entry(job, entry, valid_syms)
                            self._llm_cache[cache_key] = result
                            while len(self._llm_cache) > LLM_CACHE_SIZE:
                                self._llm_cache.popitem(last=False)
                            self._apply_llm_result_to_news(cache_key, result)
                            self._mark_llm_done(cache_key)
                            processed += 1
            except Exception:
                traceback.print_exc()
                for job in jobs:
                    self._mark_llm_done(job["cache_key"])
        if processed:
            self._recluster_current_news()
        return processed

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
                    await asyncio.to_thread(self.persist_dashboard_snapshot, True)
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

    async def _global_stream_loop(self):
        """Connect to Yahoo Finance WebSocket for true real-time global prices.

        Architecture:
        1. Seed previous_close values via one yfinance REST call.
        2. Open a persistent WebSocket to wss://streamer.finance.yahoo.com.
        3. On each tick, update _global_live; mark dirty.
        4. A separate broadcast coroutine flushes dirty state to UI clients
           at a capped rate (default 1 Hz) so the DOM isn't overwhelmed.
        5. On disconnect, reconnect with exponential backoff; fall back to
           REST polling if streaming is unavailable for >60s.
        """
        try:
            import base64 as _b64
            import json as _json
            from websockets.asyncio.client import connect as _ws_connect
            from yfinance.live import PricingData as _PricingData
            from google.protobuf.json_format import MessageToDict as _MessageToDict
        except ModuleNotFoundError:
            print("[Global WS] Streaming unavailable in current runtime — using REST polling only")
            try:
                while self._running:
                    try:
                        await asyncio.to_thread(self._fetch_global_futures)
                        self._last_global_update = datetime.now(IST).strftime("%H:%M:%S")
                        await asyncio.to_thread(self.persist_dashboard_snapshot, False)
                        await self._broadcast("global_tick")
                    except Exception:
                        traceback.print_exc()
                    await asyncio.sleep(max(10, self._gift_refresh_secs))
            except asyncio.CancelledError:
                return
            return

        yf_entries = [(lbl, sym, reg) for lbl, sym, reg in GLOBAL_FUTURES if sym]
        symbols = [sym for _, sym, _ in yf_entries]
        sym_to_label = {sym: lbl for lbl, sym, _ in yf_entries}
        sym_to_region = {sym: reg for _, sym, reg in yf_entries}

        # — Seed previous_close from REST (once, then periodically refresh) —
        async def _seed_prev_close():
            def _fetch():
                try:
                    ok = 0
                    batch = yf.Tickers(" ".join(symbols))
                    for sym in symbols:
                        pc = 0.0
                        try:
                            fi = batch.tickers[sym].fast_info
                            pc = float(fi.previous_close)
                        except Exception:
                            pass
                        if pc <= 0:
                            snap = self._fetch_yahoo_chart_snapshot(sym)
                            pc = self._to_float((snap or {}).get("prev_close"), 0.0)
                        if pc and pc > 0:
                            self._global_prev_close[sym] = pc
                            ok += 1
                    print(f"[Global WS] Seeded previous_close for {ok}/{len(symbols)} symbols")
                except Exception as e:
                    print(f"[Global WS] Seed error: {e}")
            await asyncio.to_thread(_fetch)

        # — Broadcast coroutine: flush dirty state at capped rate —
        async def _broadcast_loop():
            try:
                while self._running:
                    await asyncio.sleep(self._global_broadcast_interval)
                    if not self._global_dirty:
                        continue
                    self._global_dirty = False
                    self._rebuild_global_futures()
                    self._last_global_update = datetime.now(IST).strftime("%H:%M:%S")
                    await asyncio.to_thread(self.persist_dashboard_snapshot, False)
                    await self._broadcast("global_tick")
            except asyncio.CancelledError:
                return

        broadcast_task = asyncio.create_task(_broadcast_loop())
        backoff = 1
        last_rest_fallback = 0.0
        REST_FALLBACK_SECS = 15

        try:
            await _seed_prev_close()
            # Also do one REST fetch so the UI has data before first WS tick.
            await asyncio.to_thread(self._fetch_global_futures)
            self._last_global_update = datetime.now(IST).strftime("%H:%M:%S")
            await asyncio.to_thread(self.persist_dashboard_snapshot, True)
            await self._broadcast("global_tick")

            while self._running:
                try:
                    async with _ws_connect(
                        "wss://streamer.finance.yahoo.com/?version=2",
                        additional_headers={"User-Agent": random.choice(_USER_AGENTS)},
                        ping_interval=20,
                        ping_timeout=10,
                        close_timeout=5,
                    ) as ws:
                        self._global_stream_connected = True
                        backoff = 1
                        await ws.send(_json.dumps({"subscribe": symbols}))
                        print(f"[Global WS] Connected — streaming {len(symbols)} symbols")

                        resub_interval = 30
                        last_resub = time.time()
                        refresh_prev_interval = 900
                        last_prev_refresh = time.time()

                        async for raw in ws:
                            if not self._running:
                                break
                            try:
                                msg = _json.loads(raw)
                                b64 = msg.get("message", "")
                                if not b64:
                                    continue
                                decoded = _b64.b64decode(b64)
                                pd = _PricingData()
                                pd.ParseFromString(decoded)
                                d = _MessageToDict(pd, preserving_proto_field_name=True)
                                sym = d.get("id", "")
                                price = d.get("price")
                                if not sym or not price:
                                    continue
                                ws_prev = d.get("previous_close")
                                if ws_prev and ws_prev > 0:
                                    self._global_prev_close[sym] = ws_prev
                                if sym == "6J=F":
                                    fr = float(price)
                                    pr = float(ws_prev) if ws_prev else 0.0
                                    if not pr:
                                        pr = float(self._global_prev_close.get(sym, 0) or 0)
                                    if fr > 0 and fr < 0.35 and pr > 0 and pr < 0.35:
                                        dp = 1.0 / fr
                                        dprev = 1.0 / pr
                                        chg = dp - dprev
                                        chg_pct = (chg / dprev * 100.0) if dprev else 0.0
                                        fp = dp
                                        fc = float(chg)
                                        self._global_live[sym] = {
                                            "name": sym_to_label.get(sym, sym),
                                            "symbol": sym,
                                            "region": sym_to_region.get(sym, "OTHER"),
                                            "price": round(fp, 2 if fp >= 10 else 4),
                                            "change": round(fc, 2 if abs(fc) >= 1 else 4),
                                            "change_pct": round(float(chg_pct), 2),
                                        }
                                        self._global_dirty = True
                                        self._global_tick_count += 1
                                        continue
                                ws_chg = d.get("change")
                                ws_pct = d.get("change_percent")
                                prev = ws_prev or self._global_prev_close.get(sym)
                                prev_f = float(prev) if prev and prev > 0 else 0.0
                                if ws_chg is not None and ws_pct is not None:
                                    chg = ws_chg
                                    chg_pct = ws_pct
                                elif ws_pct is not None and prev_f > 0:
                                    chg_pct = ws_pct
                                    chg = prev_f * (float(ws_pct) / 100.0)
                                elif ws_chg is not None and prev_f > 0:
                                    chg = ws_chg
                                    chg_pct = (float(ws_chg) / prev_f) * 100.0
                                elif prev_f > 0:
                                    chg = float(price) - prev_f
                                    chg_pct = (chg / prev_f) * 100.0
                                else:
                                    chg = float(ws_chg or 0)
                                    chg_pct = float(ws_pct or 0)
                                fp = float(price)
                                fc = float(chg)
                                self._global_live[sym] = {
                                    "name": sym_to_label.get(sym, sym),
                                    "symbol": sym,
                                    "region": sym_to_region.get(sym, "OTHER"),
                                    "price": round(fp, 2 if fp >= 10 else 4),
                                    "change": round(fc, 2 if abs(fc) >= 1 else 4),
                                    "change_pct": round(float(chg_pct), 2),
                                }
                                self._global_dirty = True
                                self._global_tick_count += 1
                            except Exception:
                                pass

                            now = time.time()
                            if now - last_resub >= resub_interval:
                                await ws.send(_json.dumps({"subscribe": symbols}))
                                last_resub = now
                            if now - last_prev_refresh >= refresh_prev_interval:
                                asyncio.create_task(_seed_prev_close())
                                last_prev_refresh = now

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._global_stream_connected = False
                    print(f"[Global WS] Disconnected: {e} — reconnecting in {backoff}s")

                    # Fall back to REST polling while disconnected
                    now = time.time()
                    if now - last_rest_fallback >= REST_FALLBACK_SECS:
                        try:
                            await asyncio.to_thread(self._fetch_global_futures)
                            self._last_global_update = datetime.now(IST).strftime("%H:%M:%S")
                            await asyncio.to_thread(self.persist_dashboard_snapshot, False)
                            await self._broadcast("global_tick")
                        except Exception:
                            pass
                        last_rest_fallback = time.time()

                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)

        except asyncio.CancelledError:
            pass
        finally:
            broadcast_task.cancel()
            self._global_stream_connected = False

    def _rebuild_global_futures(self):
        """Merge streaming _global_live data with any REST-sourced entries into _global_futures."""
        merged: Dict[str, dict] = {}
        for item in self._global_futures:
            merged[item["symbol"]] = item
        for sym, live in self._global_live.items():
            merged[sym] = live
        if self._gift_nifty:
            merged["GIFTNIFTY"] = self._decorate_global_market_row({
                "name": "GIFT NIFTY",
                "symbol": "GIFTNIFTY",
                "region": "ASIAN MARKETS",
                "price": self._gift_nifty["price"],
                "change": self._gift_nifty["change"],
                "change_pct": self._gift_nifty["change_pct"],
            })
        results = [self._decorate_global_market_row(item) for item in merged.values()]
        results = [item for item in results if item]
        order = {r: i for i, (_, _, r) in enumerate(GLOBAL_FUTURES)}
        results.sort(key=lambda x: (order.get(x["region"], 99),
            next((i for i, (l, _, _) in enumerate(GLOBAL_FUTURES) if l == x["name"]), 99)))
        self._global_futures = results

    def _fetch_yahoo_chart_snapshot(self, symbol: str) -> Optional[dict]:
        """Fetch a compact quote snapshot from Yahoo's public chart endpoint."""
        try:
            url = YAHOO_CHART_URL.format(symbol=quote(symbol, safe=""))
            r = requests.get(
                url,
                params={
                    "range": "5d",
                    "interval": "1d",
                    "includePrePost": "false",
                    "events": "div,splits",
                },
                headers={"User-Agent": random.choice(_USER_AGENTS)},
                timeout=20,
            )
            if r.status_code != 200:
                print(f"[Global] {symbol} chart HTTP {r.status_code}")
                return None
            payload = r.json()
            result = ((payload.get("chart") or {}).get("result") or [None])[0]
            if not result:
                return None
            meta = result.get("meta") or {}
            indicators = result.get("indicators") or {}
            quotes = (indicators.get("quote") or [{}])[0] or {}
            closes = [self._to_float(v, 0.0) for v in (quotes.get("close") or []) if v is not None]

            price = self._to_float(meta.get("regularMarketPrice"), 0.0)
            if not price and closes:
                price = closes[-1]
            if not price:
                return None

            prev = self._to_float(meta.get("chartPreviousClose"), 0.0)
            if not prev:
                prev = self._to_float(meta.get("previousClose"), 0.0)
            if not prev and len(closes) >= 2:
                prev = closes[-2]
            if not prev and closes:
                prev = closes[-1]

            return {
                "price": price,
                "prev_close": prev,
            }
        except Exception as e:
            print(f"[Global] {symbol} snapshot error: {e}")
            return None

    def _get_watchlist_article_cache(self, cache_key: str) -> Optional[dict]:
        with self._watchlist_article_lock:
            cached = self._watchlist_article_cache.get(cache_key)
            if cached is not None:
                self._watchlist_article_cache.move_to_end(cache_key)
            return cached

    def _set_watchlist_article_cache(self, cache_key: str, result: dict):
        with self._watchlist_article_lock:
            self._watchlist_article_cache[cache_key] = result
            self._watchlist_article_cache.move_to_end(cache_key)
            while len(self._watchlist_article_cache) > WATCHLIST_ARTICLE_CACHE_SIZE:
                self._watchlist_article_cache.popitem(last=False)

    def _get_watchlist_article_body_cache(self, cache_key: str) -> Optional[str]:
        with self._watchlist_article_lock:
            cached = self._watchlist_article_body_cache.get(cache_key)
            if cached is not None:
                self._watchlist_article_body_cache.move_to_end(cache_key)
            return cached

    def _set_watchlist_article_body_cache(self, cache_key: str, body_text: str):
        with self._watchlist_article_lock:
            self._watchlist_article_body_cache[cache_key] = body_text
            self._watchlist_article_body_cache.move_to_end(cache_key)
            while len(self._watchlist_article_body_cache) > WATCHLIST_ARTICLE_CACHE_SIZE:
                self._watchlist_article_body_cache.popitem(last=False)

    @staticmethod
    def _canonicalize_news_url(raw_url: str) -> str:
        if not raw_url:
            return ""
        try:
            parsed = urlparse(raw_url)
        except Exception:
            return raw_url
        if not parsed.scheme or not parsed.netloc:
            return raw_url
        clean_query = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            lower = key.lower()
            if lower.startswith("utm_") or lower in {
                "gclid", "fbclid", "guccounter", "guce_referrer", "guce_referrer_sig",
            }:
                continue
            clean_query.append((key, value))
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.params,
                urlencode(clean_query, doseq=True),
                "",
            )
        )

    def _llm_call_watchlist_article(
        self,
        symbol: str,
        company_name: str,
        title: str,
        summary: str,
        body_text: str,
        model: str,
    ) -> Optional[dict]:
        if not NV_API_KEY:
            return None
        article_text = (body_text or summary or title).strip()
        if len(article_text) < 80:
            return None
        user_msg = (
            _WATCHLIST_LLM_PROMPT_PREFIX
            + f"Watched NSE symbol: {symbol}\n"
            + f"Company: {company_name or symbol}\n"
            + f"Headline: {title}\n"
            + f"Summary: {summary or 'n/a'}\n\n"
            + "Article text:\n"
            + article_text[:WATCHLIST_ARTICLE_BODY_MAX_CHARS]
        )
        try:
            r = self._nv_post_json(
                self._build_nv_payload(
                    model,
                    [
                        {"role": "system", "content": _WATCHLIST_LLM_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                    0.1,
                    900,
                ),
                timeout=WATCHLIST_LLM_TIMEOUT_SECS,
            )
            if not r or r.status_code != 200:
                return None
            text = self._extract_json_array_text(r.json()["choices"][0]["message"]["content"].strip())
            arr = json.loads(text)
            if isinstance(arr, list) and arr:
                return arr[0] if isinstance(arr[0], dict) else None
        except Exception:
            return None
        return None

    def _analyze_watchlist_candidate(
        self,
        symbol: str,
        company_name: str,
        title: str,
        summary: str,
        link: str,
        tags: dict,
    ) -> dict:
        resolved_link = link or ""
        if resolved_link and "news.google.com" in resolved_link:
            resolved_link = self._resolve_google_news_link(resolved_link) or resolved_link
        canonical_link = self._canonicalize_news_url(resolved_link or link)
        cache_key = f"{canonical_link or title[:120].lower()}|{symbol}"
        cached = self._get_watchlist_article_cache(cache_key)
        if cached is not None:
            return cached

        body_text = ""
        fetch_url = canonical_link or resolved_link or link
        if fetch_url.startswith("http"):
            body_text = self._get_watchlist_article_body_cache(fetch_url) or ""
            if not body_text:
                html = self._http_get_html(fetch_url, timeout=WATCHLIST_ARTICLE_TIMEOUT_SECS)
                if html:
                    body_text = self._extract_body_text(html)
                    if body_text:
                        self._set_watchlist_article_body_cache(fetch_url, body_text)

        if len(body_text) < 120:
            result = {
                "important": False,
                "stocks": [symbol],
                "sentiment": "neutral",
                "impact": "low",
                "gold_silver": bool(tags.get("gold_silver", False)),
                "india_market_impact": False,
                "market_relevant": False,
                "company_specific": True,
                "link": canonical_link or resolved_link or link,
                "used_full_article": False,
            }
            self._set_watchlist_article_cache(cache_key, result)
            return result

        model = NV_API_MODEL if len(body_text) >= 400 else NV_FAST_MODEL
        entry = self._llm_call_watchlist_article(symbol, company_name, title, summary, body_text, model)
        valid_syms = self._valid_equity_symbols()
        stocks = [symbol]
        if entry and isinstance(entry, dict):
            for extra in entry.get("stocks", []) or []:
                if extra in valid_syms and extra not in stocks:
                    stocks.append(extra)
            sentiment = entry.get("sentiment", "neutral")
            if sentiment not in ("bullish", "bearish", "neutral"):
                sentiment = "neutral"
            impact = entry.get("impact", "low")
            if impact not in ("high", "medium", "low"):
                impact = "low"
            important = bool(entry.get("important", False))
            market_relevant = bool(entry.get("market_relevant", False) or tags.get("market_relevant", False))
            company_specific = bool(entry.get("company_specific", True))
            gold_silver = bool(entry.get("gold_silver", False) or tags.get("gold_silver", False))
            india_market_impact = bool(entry.get("india_market_impact", False))
        else:
            important = False
            sentiment = "neutral"
            impact = "low"
            market_relevant = False
            company_specific = True
            gold_silver = bool(tags.get("gold_silver", False))
            india_market_impact = False

        result = {
            "important": important,
            "stocks": stocks,
            "sentiment": sentiment,
            "impact": impact,
            "gold_silver": gold_silver,
            "india_market_impact": india_market_impact,
            "market_relevant": market_relevant,
            "company_specific": company_specific,
            "link": canonical_link or resolved_link or link,
            "used_full_article": bool(body_text),
        }
        self._set_watchlist_article_cache(cache_key, result)
        return result

    def _search_stock_news(self, symbol: str) -> List[dict]:
        """Search Google News RSS for a specific stock symbol over the past 12 hours.

        Returns formatted news items ready to merge into self._news.
        """
        company_name = YF_COMPANY_NAMES.get(symbol, "")
        if not company_name:
            cat = next((item for item in self._load_equity_catalog() if item["symbol"] == symbol), None)
            if cat:
                company_name = cat.get("name", "")
        short_name = company_name.replace(" Limited", "").replace(" Ltd", "").strip() if company_name else ""

        queries = []
        if short_name:
            queries.append(f'"{short_name}" when:12h')
        queries.append(f"{symbol} NSE stock when:12h")

        now = datetime.now(IST)
        cutoff = now - timedelta(hours=12)
        results: List[dict] = []
        seen_titles: Set[str] = set()
        analyzed = 0

        for q in queries:
            url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"
            try:
                feed = self._load_feed(url)
                for entry in feed.entries[:15]:
                    if analyzed >= WATCHLIST_NEWS_ANALYZE_LIMIT or len(results) >= WATCHLIST_NEWS_MAX_ITEMS:
                        break
                    pub_dt = self._parse_pub_time(entry)
                    if pub_dt and pub_dt < cutoff:
                        continue
                    title = entry.get("title", "").strip()
                    if not title:
                        continue
                    title_key = title.lower()[:50]
                    if title_key in seen_titles:
                        continue
                    seen_titles.add(title_key)
                    summary = entry.get("summary", "").strip()[:300]
                    tags = self._classify_news(title, summary)
                    if not self._watchlist_candidate_mentions_target(symbol, short_name or company_name, title, summary):
                        if tags.get("stock_event") or len(tags.get("keyword_stocks") or []) >= 3:
                            continue
                    analyzed += 1
                    analysis = self._analyze_watchlist_candidate(symbol, company_name, title, summary, entry.get("link", ""), tags)
                    if not analysis.get("important"):
                        continue
                    wl_stocks = list(analysis.get("stocks") or [symbol])
                    for s in tags["keyword_stocks"]:
                        if s not in wl_stocks:
                            wl_stocks.append(s)
                    age_secs = int((now - pub_dt).total_seconds()) if pub_dt else 999999
                    display_src = self._display_news_source(url, "Google News", title, entry)
                    combined_text = title.lower() + (" " + summary.lower() if summary else "")
                    india_hint = _has_keyword(combined_text, _INDIA_IMPACT_HINT_KW)
                    india_news = _has_keyword(combined_text, _INDIA_NEWS_KW)
                    cache_key = title[:80].lower()
                    with self._llm_lock:
                        self._llm_cache[cache_key] = {
                            "stocks": wl_stocks,
                            "sentiment": analysis.get("sentiment", "neutral"),
                            "impact": analysis.get("impact", "low"),
                            "breaking": False,
                            "gold_silver": analysis.get("gold_silver", tags["gold_silver"]),
                            "india_market_impact": analysis.get("india_market_impact", False),
                            "market_relevant": analysis.get("market_relevant", tags.get("market_relevant", False)),
                            "company_specific": analysis.get("company_specific", True),
                            "watchlist_important": True,
                        }
                        while len(self._llm_cache) > LLM_CACHE_SIZE:
                            self._llm_cache.popitem(last=False)
                    results.append({
                        "title": title,
                        "link": analysis.get("link") or entry.get("link", ""),
                        "source": display_src,
                        "age_secs": age_secs,
                        "published_at_ts": pub_dt.timestamp() if pub_dt else None,
                        "time": self._relative_time(pub_dt) if pub_dt else "",
                        "is_fresh": age_secs < 900,
                        "global_news": False,
                        "india_news": india_news,
                        "breaking": False,
                        "breaking_hint": tags["breaking"],
                        "stock_event": tags["stock_event"],
                        "gold_silver": analysis.get("gold_silver", tags["gold_silver"]),
                        "india_market_impact": analysis.get("india_market_impact", india_hint),
                        "market_relevant": analysis.get("market_relevant", tags.get("market_relevant", False)),
                        "company_specific": analysis.get("company_specific", True),
                        "keyword_stocks": tags["keyword_stocks"],
                        "watchlist_stocks": wl_stocks,
                        "sentiment": analysis.get("sentiment", "neutral"),
                        "impact": analysis.get("impact", "low"),
                        "watchlist_injected_at": time.time(),
                        "watchlist_full_article": analysis.get("used_full_article", False),
                    })
            except Exception as e:
                print(f"[WL Search] {symbol} feed error: {e}")

        print(f"[WL Search] {symbol}: kept {len(results)} of {analyzed} analyzed candidate(s)")
        return results

    @staticmethod
    def _watchlist_candidate_mentions_target(
        symbol: str,
        company_name: str,
        title: str,
        summary: str,
    ) -> bool:
        combined = f"{title or ''} {summary or ''}".lower()
        symbol_token = (symbol or "").strip().lower()
        if symbol_token and symbol_token in combined:
            return True
        base_name = (company_name or "").strip().lower()
        if not base_name:
            return False
        cleaned = (
            base_name.replace(" limited", "")
            .replace(" ltd.", "")
            .replace(" ltd", "")
            .replace(" limited.", "")
            .strip()
        )
        if cleaned and cleaned in combined:
            return True
        parts = [part for part in re.split(r"[^a-z0-9&]+", cleaned) if len(part) >= 4]
        if not parts:
            return False
        leading_phrase = " ".join(parts[:2]).strip()
        if leading_phrase and leading_phrase in combined:
            return True
        return sum(1 for part in parts[:3] if part in combined) >= 2

    def _merge_watchlist_news_items(self, symbol: str, items: List[dict]) -> List[dict]:
        if not items:
            return []
        changed_items: List[dict] = []
        with self._news_lock:
            existing_by_title = {n["title"].lower()[:50]: n for n in self._news}
            new_items: List[dict] = []
            now_ts = time.time()
            for item in items:
                key = item["title"].lower()[:50]
                existing = existing_by_title.get(key)
                if existing:
                    existing.setdefault("watchlist_stocks", [])
                    existing.setdefault("keyword_stocks", [])
                    for sym in item.get("watchlist_stocks", []) or [symbol]:
                        if sym not in existing["watchlist_stocks"]:
                            existing["watchlist_stocks"].append(sym)
                    for sym in item.get("keyword_stocks", []) or []:
                        if sym not in existing["keyword_stocks"]:
                            existing["keyword_stocks"].append(sym)
                    existing["company_specific"] = bool(
                        existing.get("company_specific") or item.get("company_specific")
                    )
                    existing["market_relevant"] = bool(
                        existing.get("market_relevant") or item.get("market_relevant")
                    )
                    existing["sentiment"] = item.get("sentiment", existing.get("sentiment", "neutral"))
                    existing["impact"] = item.get("impact", existing.get("impact", "low"))
                    existing["gold_silver"] = bool(existing.get("gold_silver") or item.get("gold_silver"))
                    existing["india_market_impact"] = bool(
                        existing.get("india_market_impact") or item.get("india_market_impact")
                    )
                    if item.get("link"):
                        existing["link"] = item["link"]
                    existing["watchlist_injected_at"] = now_ts
                    changed_items.append(existing)
                    continue
                item["watchlist_injected_at"] = now_ts
                new_items.append(item)
            if new_items:
                self._news.extend(new_items)
                self._news.sort(key=lambda x: x.get("age_secs", 999999))
                changed_items.extend(new_items)
        return changed_items

    def _refresh_watchlist_news_sync(
        self,
        symbols: List[str],
        limit: Optional[int] = None,
        skip_symbols: Optional[Set[str]] = None,
    ) -> Tuple[List[dict], List[str]]:
        unique_symbols = self._normalize_watchlist_symbols(symbols)
        if not unique_symbols:
            return [], []
        now_ts = time.time()
        ranked = self._rank_watchlist_refresh_symbols(unique_symbols, skip_symbols)
        changed: List[dict] = []
        max_symbols = max(1, min(len(ranked), int(limit or WATCHLIST_PANEL_REFRESH_SYMBOL_CAP)))
        selected_symbols = ranked[:max_symbols]
        refreshed_symbols: List[str] = list(selected_symbols)
        for sym in selected_symbols:
            self._watchlist_news_refresh_at[sym] = now_ts
        if len(selected_symbols) == 1:
            sym = selected_symbols[0]
            try:
                items = self._search_stock_news(sym)
            except Exception:
                traceback.print_exc()
                items = []
            changed.extend(self._merge_watchlist_news_items(sym, items))
        else:
            workers = max(1, min(WATCHLIST_REFRESH_WORKERS, len(selected_symbols)))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(self._search_stock_news, sym): sym
                    for sym in selected_symbols
                }
                for future in as_completed(futures):
                    sym = futures[future]
                    try:
                        items = future.result()
                    except Exception:
                        traceback.print_exc()
                        continue
                    changed.extend(self._merge_watchlist_news_items(sym, items))
        if changed:
            self._push_llm_stack(changed)
        return changed, refreshed_symbols

    def _normalize_watchlist_symbols(self, symbols: List[str]) -> List[str]:
        unique_symbols = []
        seen = set()
        for raw in symbols:
            sym = (raw or "").strip().upper()
            if not sym or sym in seen or not self.is_known_equity(sym):
                continue
            seen.add(sym)
            unique_symbols.append(sym)
        return unique_symbols

    def _watchlist_direct_hit_symbols(self, symbols: List[str]) -> Set[str]:
        wl_set = {sym.upper() for sym in symbols if sym}
        if not wl_set:
            return set()
        recent_cutoff = time.time() - 12 * 60 * 60
        direct_hits: Set[str] = set()
        with self._news_lock:
            for item in self._news:
                age_secs = int(item.get("age_secs", 999999) or 999999)
                injected_at = float(item.get("watchlist_injected_at", 0) or 0)
                if age_secs > 12 * 60 * 60 and injected_at and injected_at < recent_cutoff:
                    continue
                for sym in item.get("watchlist_stocks", []) or []:
                    if sym in wl_set:
                        direct_hits.add(sym)
        return direct_hits

    def _rank_watchlist_refresh_symbols(
        self,
        symbols: List[str],
        skip_symbols: Optional[Set[str]] = None,
    ) -> List[str]:
        unique_symbols = self._normalize_watchlist_symbols(symbols)
        if skip_symbols:
            unique_symbols = [sym for sym in unique_symbols if sym not in skip_symbols]
        if not unique_symbols:
            return []
        direct_hits = self._watchlist_direct_hit_symbols(unique_symbols)
        watchlist_order = {sym: idx for idx, sym in enumerate(unique_symbols)}
        return sorted(
            unique_symbols,
            key=lambda sym: (
                1 if sym in direct_hits else 0,
                self._watchlist_news_refresh_at.get(sym, 0),
                watchlist_order.get(sym, 0),
            ),
        )

    @staticmethod
    def _watchlist_item_sort_key(
        item: dict,
        wl_set: Set[str],
    ) -> Tuple[int, int, int, int, int, int, int, int, int, float]:
        explicit_matches = [sym for sym in (item.get("watchlist_stocks") or []) if sym in wl_set]
        keyword_matches = [sym for sym in (item.get("keyword_stocks") or []) if sym in wl_set]
        keyword_stock_count = len(item.get("keyword_stocks") or [])
        keyword_only_stock_event = bool(not explicit_matches and item.get("stock_event"))
        keyword_only_sector_basket = bool(not explicit_matches and keyword_stock_count >= 4)
        age_secs = int(item.get("age_secs", 999999) or 999999)
        injected_at = float(item.get("watchlist_injected_at", 0) or 0)
        return (
            0 if explicit_matches else 1,
            0 if item.get("company_specific") else 1,
            0 if item.get("market_relevant") else 1,
            0 if item.get("india_market_impact") else 1,
            1 if keyword_only_stock_event else 0,
            1 if keyword_only_sector_basket else 0,
            0 if item.get("is_fresh") else 1,
            -(len(explicit_matches) or len(keyword_matches)),
            age_secs,
            -injected_at,
        )

    def _current_watchlist_news_items(self, symbols: List[str]) -> List[dict]:
        wl_set = {sym.upper() for sym in symbols if sym}
        if not wl_set:
            return []
        with self._news_lock:
            items = [
                dict(item)
                for item in self._news
                if any(sym in wl_set for sym in (item.get("watchlist_stocks") or item.get("keyword_stocks") or []))
            ]
        filtered: List[dict] = []
        for item in items:
            explicit = [sym for sym in (item.get("watchlist_stocks") or []) if sym in wl_set]
            if explicit:
                filtered.append(item)
                continue
            if item.get("market_relevant") or item.get("company_specific") or item.get("india_market_impact"):
                filtered.append(item)
        items = filtered
        items.sort(key=lambda item: self._watchlist_item_sort_key(item, wl_set))
        return items[:80]

    async def fetch_watchlist_stock_news(self, symbol: str):
        """Async entry point: search for stock news, merge into live feed, broadcast."""
        symbol = symbol.upper().strip()
        if not self.is_known_equity(symbol):
            return
        items = await asyncio.to_thread(self._search_stock_news, symbol)
        changed_items = self._merge_watchlist_news_items(symbol, items)
        if changed_items:
            self._push_llm_stack(changed_items)
            await asyncio.to_thread(self.persist_dashboard_snapshot, False)
            await self._broadcast("news")

    async def _backfill_watchlist_news(self):
        symbols = self._watchlist_store.list_symbols()
        if not symbols:
            return
        now_ts = time.time()
        stale_cutoff = now_ts - WATCHLIST_NEWS_BACKFILL_SECS
        with self._news_lock:
            fresh_symbols = set()
            for item in self._news:
                if item.get("watchlist_injected_at", 0) <= stale_cutoff:
                    continue
                for sym in item.get("watchlist_stocks", []) or []:
                    fresh_symbols.add(sym)
        ranked = sorted(
            symbols,
            key=lambda sym: (
                1 if sym in fresh_symbols else 0,
                self._watchlist_news_refresh_at.get(sym, 0),
            ),
        )
        for sym in ranked[:WATCHLIST_NEWS_BACKFILL_LIMIT]:
            last_refresh = self._watchlist_news_refresh_at.get(sym, 0)
            if last_refresh and now_ts - last_refresh < WATCHLIST_NEWS_BACKFILL_SECS:
                continue
            self._watchlist_news_refresh_at[sym] = now_ts
            try:
                await self.fetch_watchlist_stock_news(sym)
            except Exception:
                traceback.print_exc()

    async def _gift_nifty_loop(self):
        """Poll GIFT Nifty price on its own short interval (scrape source doesn't have a WS)."""
        try:
            while self._running:
                try:
                    await asyncio.to_thread(self._fetch_gift_nifty)
                    self._last_gift_fetch_ts = time.time()
                    self._global_dirty = True
                except Exception:
                    traceback.print_exc()
                await asyncio.sleep(self._gift_refresh_secs)
        except asyncio.CancelledError:
            return

    async def _news_loop(self):
        """Fetch news every 60 s for real-time coverage."""
        try:
            while self._running:
                t0 = time.time()
                try:
                    await asyncio.to_thread(self._refresh_news_if_stale, True)
                    elapsed = round(time.time() - t0, 1)
                    print(f"[News] {len(self._news)} headlines in {elapsed}s")
                    await self._broadcast("news")
                except Exception:
                    traceback.print_exc()
                await asyncio.sleep(NEWS_REFRESH_SECS)
        except asyncio.CancelledError:
            return

    async def _live_stories_loop(self):
        """Poll live pages + deep-read breaking articles. Discovery runs in background, never blocks polling."""
        try:
            interval = LIVE_STORY_POLL_SECS
            discover_secs = LIVE_STORY_DISCOVER_SECS
            last_discover = 0.0
            cycle = 0
            while self._running:
                cycle += 1
                try:
                    await asyncio.to_thread(self._poll_live_story_pages)
                except Exception:
                    traceback.print_exc()
                now = time.time()
                if now - last_discover >= discover_secs:
                    last_discover = now
                    try:
                        await asyncio.to_thread(self._discover_live_pages_from_rss)
                    except Exception:
                        traceback.print_exc()
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return

    def _env_live_story_urls(self) -> List[str]:
        raw = os.getenv("LIVE_STORY_URLS", "").strip()
        if not raw:
            return []
        return [u.strip() for u in raw.split(",") if u.strip()]

    def _env_live_url_set(self) -> Set[str]:
        return set(self._env_live_story_urls())

    def _active_live_story_urls(self) -> List[str]:
        """Explicit env URLs first, then auto-discovered live pages (capped)."""
        cap = max(4, min(24, int(os.getenv("LIVE_POLL_MAX_URLS", "12"))))
        env_u = self._env_live_story_urls()
        discovered = list(self._discovered_live_urls.keys())
        out: List[str] = []
        seen: Set[str] = set()
        for u in env_u + discovered:
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
        return out[:cap]

    def _resolve_google_news_link(self, url: str) -> Optional[str]:
        """Follow redirects (Google News → publisher). Uses HEAD for speed."""
        try:
            parsed = urlparse(url)
            if parsed.netloc.endswith("news.google.com") and "/articles/" in (parsed.path or ""):
                decoded = self._decode_google_news_article_url(url)
                if decoded:
                    return decoded
        except Exception:
            pass
        try:
            r = requests.head(
                url,
                timeout=8,
                headers={"User-Agent": random.choice(_USER_AGENTS)},
                allow_redirects=True,
            )
            final = r.url if r.url and len(r.url) > 12 else None
            if final:
                return final
            r2 = requests.get(url, timeout=10, headers={"User-Agent": random.choice(_USER_AGENTS)},
                              allow_redirects=True, stream=True)
            r2.close()
            return r2.url if r2.url and len(r2.url) > 12 else None
        except Exception:
            return None

    def _decode_google_news_article_url(self, url: str) -> Optional[str]:
        """Decode Google News RSS article wrappers into the publisher URL."""
        try:
            parsed = urlparse(url)
            article_id = (parsed.path or "").rstrip("/").split("/")[-1]
            if not article_id:
                return None
            page = requests.get(
                f"https://news.google.com/rss/articles/{article_id}",
                timeout=10,
                headers={"User-Agent": random.choice(_USER_AGENTS)},
                allow_redirects=True,
            )
            if page.status_code != 200:
                return None
            soup = BeautifulSoup(page.text, "html.parser")
            params_el = soup.select_one("c-wiz > div[data-n-a-sg][data-n-a-ts]")
            if not params_el:
                return None
            sig = params_el.get("data-n-a-sg")
            ts = params_el.get("data-n-a-ts")
            if not sig or not ts:
                return None
            rpc = [[
                "Fbv4je",
                (
                    '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,'
                    'null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
                    f'"{article_id}",{ts},"{sig}"]'
                ),
            ]]
            payload = "f.req=" + quote(json.dumps([rpc]))
            resp = requests.post(
                "https://news.google.com/_/DotsSplashUi/data/batchexecute",
                timeout=10,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                    "User-Agent": random.choice(_USER_AGENTS),
                },
                data=payload,
            )
            if resp.status_code != 200:
                return None
            parts = resp.text.split("\n\n", 1)
            if len(parts) != 2:
                return None
            rows = json.loads(parts[1])
            for row in rows:
                if not isinstance(row, list) or len(row) < 3 or row[1] != "Fbv4je":
                    continue
                decoded = json.loads(row[2])
                if (
                    isinstance(decoded, list)
                    and len(decoded) >= 2
                    and decoded[0] == "garturlres"
                    and isinstance(decoded[1], str)
                    and decoded[1].startswith("http")
                ):
                    return decoded[1]
        except Exception:
            return None
        return None

    def _discover_live_pages_from_rss(self):
        """Refresh candidate live URLs from market-event RSS searches only."""
        domains = _live_discovery_domains()
        feeds: List[str] = list(LIVE_DISCOVERY_FEEDS)
        extra = os.getenv("LIVE_DISCOVERY_FEEDS", "").strip()
        if extra:
            feeds.extend(u.strip() for u in extra.split(",") if u.strip().startswith("http"))
        added = 0
        for feed_url in feeds:
            try:
                r = requests.get(
                    feed_url,
                    timeout=22,
                    headers={"User-Agent": random.choice(_USER_AGENTS)},
                )
                if r.status_code != 200:
                    continue
                fp = feedparser.parse(r.content)
            except Exception:
                continue
            resolved_count = 0
            for entry in fp.entries[:20]:
                link = (entry.get("link") or "").strip()
                if not link:
                    continue
                title_lower = (entry.get("title") or "").lower()
                if not any(h in title_lower for h in ("live", "updates", "as it happened")):
                    continue
                if not (
                    _has_keyword(title_lower, _MARKET_MOVING_KW)
                    or _has_keyword(title_lower, _INDIA_IMPACT_HINT_KW)
                ):
                    continue
                canon = self._resolve_google_news_link(link)
                resolved_count += 1
                if resolved_count > 6:
                    break
                if not canon or not _is_probable_live_page(canon, domains):
                    continue
                if canon in self._discovered_live_urls:
                    self._discovered_live_urls.move_to_end(canon)
                else:
                    self._discovered_live_urls[canon] = True
                    added += 1
                while len(self._discovered_live_urls) > self._max_discovered_live_urls:
                    self._discovered_live_urls.popitem(last=False)
        if added:
            print(f"[Live] discovery: +{added} URL(s); tracking {len(self._discovered_live_urls)} live page(s)")

    def _http_get_html(self, url: str, timeout: int = 28) -> Optional[str]:
        headers = {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }
        ck = (os.getenv("LIVE_PAGE_EXTRA_COOKIE", "") or os.getenv("REUTERS_EXTRA_COOKIE", "")).strip()
        if ck:
            headers["Cookie"] = ck
        try:
            r = requests.get(url, timeout=timeout, headers=headers, allow_redirects=True)
            if r.status_code != 200:
                return None
            if len(r.text) < 4000:
                # Bot wall / interstitial — HTML too small to be a real article page
                return None
            return r.text
        except Exception:
            return None

    @staticmethod
    def _extract_live_candidates_from_json(obj, seen_titles: Set[str], base_url: str) -> List[dict]:
        """Pull likely live-post headlines from Reuters __NEXT_DATA__ (and similar) trees."""
        out: List[dict] = []

        def maybe_add(title: str, link: Optional[str], t_raw: Optional[str]):
            title = (title or "").strip()
            if not _live_title_ok(title) or title in seen_titles:
                return
            seen_titles.add(title)
            if link and link.startswith("/"):
                link = urljoin(base_url, link)
            elif not link or not link.startswith("http"):
                link = base_url
            out.append({"title": title, "link": link, "t_raw": t_raw})

        def walk(node):
            if isinstance(node, dict):
                title_val = None
                for k in ("headline", "title"):
                    v = node.get(k)
                    if isinstance(v, str) and len(v.strip()) > 12:
                        title_val = v.strip()
                        break
                link_val = None
                for k in ("url", "canonicalUrl", "canonical_url"):
                    v = node.get(k)
                    if isinstance(v, str) and (v.startswith("http") or v.startswith("/")):
                        link_val = v
                        break
                t_raw = None
                for k in ("datePublished", "publishedTime", "displayTime", "firstPublished", "timestamp"):
                    v = node.get(k)
                    if v is not None:
                        t_raw = str(v)
                        break
                if title_val and _live_title_ok(title_val):
                    maybe_add(title_val, link_val, t_raw)
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for it in node:
                    walk(it)

        walk(obj)
        return out

    @staticmethod
    def _extract_live_candidates_from_jsonld(
        soup: BeautifulSoup,
        seen_titles: Set[str],
        base_url: str,
    ) -> List[dict]:
        """Prefer explicit LiveBlogPosting schema updates over page-level summary headlines."""
        out: List[dict] = []

        def maybe_add(title: str, link: Optional[str], t_raw: Optional[str]):
            title = (title or "").strip()
            if not _live_title_ok(title) or title in seen_titles:
                return
            seen_titles.add(title)
            if link and link.startswith("/"):
                link = urljoin(base_url, link)
            elif not link or not link.startswith("http"):
                link = base_url
            out.append({"title": title, "link": link, "t_raw": t_raw})

        def extract_updates(node):
            updates = node.get("liveBlogUpdate") or node.get("blogPost") or node.get("hasPart") or []
            if isinstance(updates, dict):
                updates = [updates]
            if not isinstance(updates, list):
                return
            for update in updates:
                if not isinstance(update, dict):
                    continue
                title = update.get("headline") or update.get("name") or update.get("title")
                link = update.get("url")
                main_page = update.get("mainEntityOfPage")
                if isinstance(main_page, dict):
                    link = link or main_page.get("@id") or main_page.get("url")
                elif isinstance(main_page, str):
                    link = link or main_page
                t_raw = (
                    update.get("datePublished")
                    or update.get("dateModified")
                    or update.get("uploadDate")
                    or update.get("timestamp")
                )
                if isinstance(title, str):
                    maybe_add(title, link, str(t_raw) if t_raw is not None else None)

        def walk(node):
            if isinstance(node, dict):
                raw_type = node.get("@type")
                types: Set[str] = set()
                if isinstance(raw_type, str):
                    types.add(raw_type)
                elif isinstance(raw_type, list):
                    types.update(t for t in raw_type if isinstance(t, str))
                if "LiveBlogPosting" in types:
                    extract_updates(node)
                for value in node.values():
                    if isinstance(value, (dict, list)):
                        walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        for script in soup.select('script[type="application/ld+json"]'):
            raw = script.string or script.get_text()
            if not raw or ("LiveBlogPosting" not in raw and "liveBlogUpdate" not in raw):
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            walk(payload)

        return out

    @staticmethod
    def _extract_live_candidates_from_dom(
        soup: BeautifulSoup,
        seen_titles: Set[str],
        base_url: str,
    ) -> List[dict]:
        """Fallback for live pages that render update cards directly in the HTML."""
        out: List[dict] = []
        seen_nodes: Set[int] = set()
        selectors = (
            ".LiveBlogWrapper .entry",
            '[class*="LiveBlog"] .entry',
            '[data-testid*="LiveBlog"] .entry',
            '[itemprop="liveBlogUpdate"]',
            ".entry",
        )

        def maybe_add(title: str, link: Optional[str], t_raw: Optional[str]):
            title = (title or "").strip()
            if not _live_title_ok(title) or title in seen_titles:
                return
            seen_titles.add(title)
            if link and link.startswith("/"):
                link = urljoin(base_url, link)
            elif not link or not link.startswith("http"):
                link = base_url
            out.append({"title": title, "link": link, "t_raw": t_raw})

        for selector in selectors:
            for node in soup.select(selector):
                node_id = id(node)
                if node_id in seen_nodes:
                    continue
                seen_nodes.add(node_id)
                heading = node.select_one("h1, h2, h3, h4, [data-testid='Heading']")
                if not heading:
                    continue
                title = heading.get_text(" ", strip=True)
                if not _live_title_ok(title):
                    continue
                link = None
                anchor = heading.find("a", href=True) or node.find("a", href=True)
                if anchor:
                    link = anchor.get("href")
                time_el = node.find("time")
                t_raw = None
                if time_el:
                    t_raw = time_el.get("datetime") or time_el.get_text(" ", strip=True) or None
                maybe_add(title, link, t_raw)

        return out

    @staticmethod
    def _extract_body_text(html: str) -> str:
        """Extract clean readable text from article HTML (paragraphs, headings, list items)."""
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.select("script, style, nav, footer, header, noscript, iframe, svg, form"):
            tag.decompose()
        parts: List[str] = []
        for el in soup.select("article p, article li, article h2, article h3, "
                              "[data-testid] p, [data-testid] li, "
                              "main p, main li, main h2, main h3, "
                              ".article-body p, .article-body li"):
            t = el.get_text(" ", strip=True)
            if t and len(t) > 15:
                parts.append(t)
        if not parts:
            for el in soup.find_all("p"):
                t = el.get_text(" ", strip=True)
                if t and len(t) > 20:
                    parts.append(t)
        return "\n".join(parts[:200])

    def _llm_extract_developments(self, body_text: str, source_brand: str) -> List[str]:
        """Ask the LLM to read the full article body and extract individual developments as headlines."""
        if not NV_API_KEY or not body_text or len(body_text) < 80:
            return []
        truncated = body_text[:6000]
        system_prompt = (
            "You are a wire-service editor. Read the article text and extract up to 10 distinct, "
            "important NEW developments or facts as short news headlines (one sentence each, under 120 chars). "
            "Focus on: geopolitics, policy decisions, military actions, economic impacts, market reactions, "
            "diplomatic moves, deadlines, official statements. "
            "Skip: author bios, navigation text, ads, generic filler. "
            "Output ONLY a JSON array of strings. No markdown, no explanation."
        )
        user_msg = f"Source: {source_brand}\n\nArticle text:\n{truncated}"
        try:
            r = self._nv_post_json(
                self._build_nv_payload(
                    NV_FAST_MODEL,
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    0.15,
                    1024,
                ),
                timeout=30,
            )
            if not r or r.status_code != 200:
                return []
            text = r.json()["choices"][0]["message"]["content"].strip()
            text = self._extract_json_array_text(text)
            start = text.find("[")
            end = text.rfind("]")
            if start == -1 or end == -1:
                return []
            raw = text[start:end + 1]
            try:
                arr = json.loads(raw)
            except json.JSONDecodeError:
                arr = re.findall(r'"([^"]{20,})"', raw)
            if not isinstance(arr, list):
                return []
            return [s.strip() for s in arr if isinstance(s, str) and len(s.strip()) > 20][:10]
        except Exception:
            return []

    def _parse_live_page_items(self, html: str, page_url: str) -> List[dict]:
        soup = BeautifulSoup(html, "html.parser")
        seen_titles: Set[str] = set()

        items_from_structure = self._extract_live_candidates_from_jsonld(soup, seen_titles, page_url)
        if not items_from_structure:
            items_from_structure = self._extract_live_candidates_from_dom(soup, seen_titles, page_url)

        if items_from_structure:
            return items_from_structure[:40]

        items_from_structure = []
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        if m:
            try:
                data = json.loads(m.group(1))
                items_from_structure = self._extract_live_candidates_from_json(data, seen_titles, page_url)[:30]
            except json.JSONDecodeError:
                pass

        if not items_from_structure:
            for h in soup.select('article h2, article h3, [data-testid="Heading"]'):
                t = h.get_text(" ", strip=True)
                if _live_title_ok(t) and t not in seen_titles:
                    seen_titles.add(t)
                    items_from_structure.append({"title": t, "link": page_url, "t_raw": None})

        body_text = self._extract_body_text(html)
        brand = _live_brand_from_url(page_url)
        llm_headlines = self._llm_extract_developments(body_text, brand)

        seen_keys: Set[str] = {it["title"].lower()[:50] for it in items_from_structure}
        for headline in llm_headlines:
            k = headline.lower()[:50]
            if k not in seen_keys:
                seen_keys.add(k)
                items_from_structure.append({"title": headline, "link": page_url, "t_raw": None})

        return items_from_structure[:40]

    def _live_digest(self, title: str, link: Optional[str]) -> str:
        basis = f"{title}\n{link or ''}"
        return hashlib.sha256(basis.encode("utf-8", errors="ignore")).hexdigest()[:28]

    def _live_post_to_news_item(self, title: str, link: str, page_url: str) -> dict:
        now = datetime.now(IST)
        tags = self._classify_news(title, "")
        combined = title.lower()
        india_hint = _has_keyword(combined, _INDIA_IMPACT_HINT_KW)
        india_news = _has_keyword(combined, _INDIA_NEWS_KW)
        brk_pre = india_hint and not tags.get("stock_event", False) and not tags.get("company_specific", False)
        return {
            "title": title,
            "link": link or page_url,
            "source": f"{_live_brand_from_url(page_url)} LIVE",
            "live_story": True,
            "age_secs": 0,
            "published_at_ts": now.timestamp(),
            "time": "just now",
            "is_fresh": True,
            "global_news": True,
            "india_news": india_news,
            "breaking": False,
            "breaking_hint": bool(brk_pre or tags["breaking"]),
            "stock_event": tags["stock_event"],
            "gold_silver": tags["gold_silver"],
            "india_market_impact": india_hint,
            "market_relevant": tags.get("market_relevant", False),
            "company_specific": tags.get("company_specific", False),
            "keyword_stocks": tags["keyword_stocks"],
            "watchlist_stocks": [],
        }

    def _schedule_news_broadcast(self):
        loop = getattr(self, "_asyncio_loop", None)
        if not loop or not loop.is_running():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast("news"), loop)
        except Exception:
            pass

    def _merge_live_items_front(self, fresh: List[dict]) -> int:
        """Prepend new live-blog posts to the in-memory wire, deduped by headline."""
        if not fresh:
            return 0
        with self._news_lock:
            keys = {n["title"][:50].lower() for n in self._news}
            prefix: List[dict] = []
            for it in fresh:
                if not (self._is_tradable_news_item(it) or self._should_llm_classify_news_item(it)):
                    continue
                k = it["title"][:50].lower()
                if k in keys:
                    continue
                keys.add(k)
                prefix.append(it)
            if not prefix:
                return 0
            self._news = prefix + self._news
            self._news = self._news[:120]
        return len(prefix)

    def _deep_read_breaking_articles(self):
        """Proactively fetch the full text of top breaking news articles, extract developments."""
        with self._news_lock:
            candidates = [
                n for n in self._news
                if n.get("breaking")
                and n.get("link")
                and n["link"].startswith("http")
                and not n.get("live_story")
                and not n.get("_deep_read_done")
            ]
        max_reads = max(1, min(5, int(os.getenv("LIVE_DEEP_READ_MAX", "3"))))
        for item in candidates[:max_reads]:
            link = item["link"]
            sid = _live_story_id(link)
            html = self._http_get_html(link)
            item["_deep_read_done"] = True
            if not html:
                continue
            body_text = self._extract_body_text(html)
            if not body_text or len(body_text) < 100:
                continue
            brand = _live_brand_from_url(link)
            headlines = self._llm_extract_developments(body_text, brand)
            if not headlines:
                continue
            fresh: List[dict] = []
            for hl in headlines:
                d = self._live_digest(hl, link)
                with self._live_lock:
                    if d in self._live_seen:
                        continue
                    self._live_seen.add(d)
                    fresh.append(self._live_post_to_news_item(hl, link, link))
            if fresh:
                print(f"[Live] deep-read {sid}: +{len(fresh)} developments from article body")
                inserted = self._merge_live_items_front(fresh)
                if inserted:
                    self._push_llm_stack(fresh)
                    self._schedule_news_broadcast()

    def _poll_live_story_pages(self):
        urls = self._active_live_story_urls()
        any_new = 0

        self._deep_read_breaking_articles()

        if not urls:
            return
        for page_url in urls:
            sid = _live_story_id(page_url)
            html = self._http_get_html(page_url)
            if not html:
                if page_url not in self._env_live_url_set():
                    fc = self._live_fetch_failures.get(page_url, 0) + 1
                    self._live_fetch_failures[page_url] = fc
                    if fc >= self._live_fail_drop_after:
                        self._discovered_live_urls.pop(page_url, None)
                        self._live_fetch_failures.pop(page_url, None)
                        print(f"[Live] dropped discovery URL after repeated failures: {sid}")
                if self._live_log_state.get(sid) != "blocked":
                    print(f"[Live] {sid}: page fetch blocked/empty — set LIVE_PAGE_EXTRA_COOKIE if needed, "
                          f"or use LIVE_STORY_URLS / LIVE_DISCOVERY_FEEDS")
                    self._live_log_state[sid] = "blocked"
                continue
            self._live_fetch_failures.pop(page_url, None)
            self._live_log_state[sid] = "ok"
            items = self._parse_live_page_items(html, page_url)
            fresh_batch: List[dict] = []
            for it in items:
                d = self._live_digest(it["title"], it["link"])
                with self._live_lock:
                    if d in self._live_seen:
                        continue
                    self._live_seen.add(d)
                    fresh_batch.append(self._live_post_to_news_item(it["title"], it["link"], page_url))
            if fresh_batch:
                print(f"[Live] {sid}: extracted {len(fresh_batch)} new post(s)")
                inserted = self._merge_live_items_front(fresh_batch)
                if inserted:
                    any_new += inserted
                    self._push_llm_stack(fresh_batch)
        if any_new:
            self._schedule_news_broadcast()

    # ── NSE data (indices + stocks in 2 API calls) ─────────────────────

    @staticmethod
    def _to_float(value, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _yahoo_change_for_display(
        info: Optional[dict],
        price: float,
        prev_close: float,
    ) -> tuple[float, float]:
        """Use Yahoo quote-summary change fields when present; else price vs previous close."""
        inf: dict = info if isinstance(info, dict) else {}
        base_prev = DataEngine._to_float(
            inf.get("regularMarketPreviousClose") or inf.get("previousClose"),
            0.0,
        )
        if base_prev <= 0 and prev_close > 0:
            base_prev = prev_close

        rmc = inf.get("regularMarketChange")
        rmcp = inf.get("regularMarketChangePercent")
        has_c = rmc is not None
        has_p = rmcp is not None
        if has_c and has_p:
            return float(rmc), float(rmcp)
        if has_p and base_prev > 0:
            pct = float(rmcp)
            return base_prev * (pct / 100.0), pct
        if has_c and base_prev > 0:
            c = float(rmc)
            return c, (c / base_prev) * 100.0
        pc = prev_close if prev_close > 0 else base_prev
        if pc > 0:
            chg = price - pc
            return chg, (chg / pc) * 100.0
        return 0.0, 0.0

    @staticmethod
    def _invert_cme_jpy_future_to_usdjpy(px: float) -> float:
        """CME yen future on Yahoo is quoted as a small USD-per-JPY fraction; invert to USD/JPY."""
        try:
            p = float(px)
        except (TypeError, ValueError):
            return 0.0
        if p <= 0 or p >= 0.35:
            return 0.0
        return 1.0 / p

    def _twelve_stale_or_none(self, key: tuple) -> Optional[dict]:
        """Return last cached quote if any (used when rate-limited or offline)."""
        with self._twelve_lock:
            ent = self._twelve_quote_cache.get(key)
            if ent and isinstance(ent[1], dict):
                return dict(ent[1])
        return None

    def _twelve_data_quote(self, td_symbol: str, td_exchange: str) -> Optional[dict]:
        """Quote from Twelve Data `/quote` (indices, NSE equities, mapped globals).

        Respects Basic-plan limits via TWELVE_DATA_MAX_PER_MINUTE + response caching.
        """
        if not TWELVE_DATA_API_KEY:
            return None
        sym_k = (td_symbol or "").strip().upper()
        ex_k = (td_exchange or "").strip().upper()
        key = (sym_k, ex_k)
        now = time.time()
        with self._twelve_lock:
            ent = self._twelve_quote_cache.get(key)
            if ent and now - ent[0] < TWELVE_DATA_QUOTE_CACHE_SECS:
                return dict(ent[1]) if isinstance(ent[1], dict) else None
            while self._twelve_call_times and now - self._twelve_call_times[0] > 60.0:
                self._twelve_call_times.popleft()
            if len(self._twelve_call_times) >= TWELVE_DATA_MAX_PER_MINUTE:
                if now - self._twelve_log_throttle > 60.0:
                    self._twelve_log_throttle = now
                    print(
                        "[Twelve] Per-minute API budget exhausted "
                        f"({TWELVE_DATA_MAX_PER_MINUTE}/min); serving cache or skipping."
                    )
                if ent and isinstance(ent[1], dict):
                    return dict(ent[1])
                return None
            self._twelve_call_times.append(now)

        try:
            params: Dict[str, str] = {"symbol": sym_k, "apikey": TWELVE_DATA_API_KEY}
            if ex_k:
                params["exchange"] = ex_k
            r = requests.get(
                TWELVE_DATA_QUOTE_URL,
                params=params,
                headers={"User-Agent": random.choice(_USER_AGENTS)},
                timeout=12,
            )
            if r.status_code == 429:
                stale = self._twelve_stale_or_none(key)
                if stale:
                    return stale
                return None
            if r.status_code != 200:
                stale = self._twelve_stale_or_none(key)
                if stale:
                    return stale
                return None
            j = r.json()
            if j.get("status") == "error" or j.get("code"):
                stale = self._twelve_stale_or_none(key)
                if stale:
                    return stale
                return None
            price = self._to_float(j.get("close"), 0.0)
            if not price:
                price = self._to_float(j.get("open"), 0.0)
            prev = self._to_float(j.get("previous_close"), 0.0)
            if not price:
                stale = self._twelve_stale_or_none(key)
                if stale:
                    return stale
                return None
            chg = self._to_float(j.get("change"), 0.0)
            pct = self._to_float(j.get("percent_change"), 0.0)
            if not chg and prev > 0:
                chg = price - prev
            if not pct and prev > 0:
                pct = (chg / prev) * 100.0
            name = (j.get("name") or "").strip()
            payload = {
                "price": price,
                "prev": prev,
                "chg": chg,
                "pct": pct,
                "name": name,
                "open": self._to_float(j.get("open"), 0.0),
                "high": self._to_float(j.get("high"), 0.0),
                "low": self._to_float(j.get("low"), 0.0),
                "volume": int(self._to_float(j.get("volume"), 0)),
            }
            with self._twelve_lock:
                self._twelve_quote_cache[key] = (time.time(), payload)
                while len(self._twelve_quote_cache) > TWELVE_DATA_CACHE_MAX_KEYS:
                    self._twelve_quote_cache.popitem(last=False)
            return dict(payload)
        except Exception:
            stale = self._twelve_stale_or_none(key)
            if stale:
                return stale
            return None

    def _try_twelve_global_futures_row(
        self, label: str, yahoo_sym: str, region: str
    ) -> Optional[dict]:
        spec = _GLOBAL_TD_FALLBACK.get(yahoo_sym)
        if not spec:
            return None
        td_sym, td_ex = spec[0], spec[1]
        tdq = self._twelve_data_quote(td_sym, td_ex)
        if not tdq or not tdq.get("price"):
            return None
        p = tdq["price"]
        c = tdq["chg"]
        if tdq.get("prev", 0) > 0:
            self._global_prev_close[yahoo_sym] = tdq["prev"]
        return self._decorate_global_market_row({
            "name": label,
            "symbol": yahoo_sym,
            "region": region,
            "price": round(p, 2 if p >= 10 else 4),
            "change": round(c, 2 if abs(c) >= 1 else 4),
            "change_pct": round(float(tdq["pct"]), 2),
        })

    @staticmethod
    def _prefer_twelve_global_row(yahoo_sym: str) -> bool:
        return yahoo_sym in _GLOBAL_TD_PREFER_FIRST

    def _build_global_futures_row(
        self,
        label: str,
        yahoo_sym: str,
        region: str,
        price: float,
        prev: float,
        inf: dict,
    ) -> Optional[dict]:
        """One global row from Yahoo prices; seeds _global_prev_close for the stream."""
        inf = inf if isinstance(inf, dict) else {}
        if yahoo_sym == "6J=F":
            raw_p = self._to_float(price, 0.0)
            raw_prev = self._to_float(prev, 0.0)
            if raw_prev <= 0:
                raw_prev = self._to_float(
                    inf.get("regularMarketPreviousClose") or inf.get("previousClose"),
                    0.0,
                )
            display_p = self._invert_cme_jpy_future_to_usdjpy(raw_p)
            display_prev = self._invert_cme_jpy_future_to_usdjpy(raw_prev)
            if not display_p:
                return None
            if raw_prev > 0:
                self._global_prev_close[yahoo_sym] = raw_prev
            chg = display_p - display_prev if display_prev else 0.0
            chg_pct = (chg / display_prev * 100.0) if display_prev else 0.0
            fp, fc = display_p, chg
        else:
            if not price:
                return None
            if prev > 0:
                self._global_prev_close[yahoo_sym] = prev
            chg, chg_pct = self._yahoo_change_for_display(inf, price, prev)
            fp, fc = price, chg
        return self._decorate_global_market_row({
            "name": label,
            "symbol": yahoo_sym,
            "region": region,
            "price": round(fp, 2 if fp >= 10 else 4),
            "change": round(fc, 2 if abs(fc) >= 1 else 4),
            "change_pct": round(float(chg_pct), 2),
        })

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
            row = None
            try:
                info = yf.Ticker(yf_symbol).fast_info
                price = self._to_float(info.get("lastPrice"))
                prev = self._to_float(info.get("previousClose"))
                if price and prev:
                    change = price - prev
                    pct = (change / prev * 100) if prev else 0
                    row = {
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
                        "source": "yahoo",
                    }
            except Exception as e:
                print(f"[YF Index] {name}: {e}")
            if not row:
                snap = self._fetch_yahoo_chart_snapshot(yf_symbol)
                if snap:
                    price = self._to_float(snap.get("price"), 0.0)
                    prev = self._to_float(snap.get("prev_close"), 0.0)
                    if price:
                        change = price - prev if prev else 0.0
                        pct = (change / prev * 100) if prev else 0.0
                        row = {
                            "symbol": name,
                            "name": name,
                            "price": round(price, 2),
                            "change": round(change, 2),
                            "change_pct": round(pct, 2),
                            "open": round(price, 2),
                            "high": round(price, 2),
                            "low": round(price, 2),
                            "advances": None,
                            "declines": None,
                            "source": "yahoo_chart",
                        }
            if not row and TWELVE_DATA_API_KEY:
                td_spec = INDIA_YF_INDEX_TO_TWELVE.get(yf_symbol)
                if td_spec:
                    td_sym, td_ex = td_spec[0], td_spec[1]
                    tdq = self._twelve_data_quote(td_sym, td_ex)
                    if tdq and tdq.get("price"):
                        p = tdq["price"]
                        prev = tdq.get("prev") or 0.0
                        if not prev:
                            prev = p
                        chg = float(tdq["chg"])
                        pct = float(tdq["pct"])
                        if not chg and prev:
                            chg = p - prev
                        if not pct and prev:
                            pct = (chg / prev) * 100
                        o = tdq.get("open") or p
                        hi = tdq.get("high") or p
                        lo = tdq.get("low") or p
                        row = {
                            "symbol": name,
                            "name": name,
                            "price": round(p, 2),
                            "change": round(chg, 2),
                            "change_pct": round(pct, 2),
                            "open": round(o, 2),
                            "high": round(hi, 2),
                            "low": round(lo, 2),
                            "advances": None,
                            "declines": None,
                            "source": "twelve",
                        }
            if row:
                results.append(row)
        if results:
            self._indices = results

    def _fetch_yf_stocks(self):
        stocks: Dict[str, dict] = {}
        try:
            batch = yf.Tickers(" ".join(f"{s}.NS" for s in YF_STOCK_SYMBOLS))
        except Exception as e:
            print(f"[YF Stocks] tickers failed: {e}")
            return

        for sym in YF_STOCK_SYMBOLS:
            yf_symbol = f"{sym}.NS"
            try:
                t = batch.tickers[yf_symbol]
            except Exception:
                continue

            price = prev_close = open_price = high = low = 0.0
            volume = 0

            try:
                fi = t.fast_info
                price = self._to_float(getattr(fi, "last_price", None) or fi.get("lastPrice"), 0.0)
                prev_close = self._to_float(getattr(fi, "previous_close", None) or fi.get("previousClose"), 0.0)
                open_price = self._to_float(getattr(fi, "open", None) or fi.get("open"), price)
                high = self._to_float(getattr(fi, "day_high", None) or fi.get("dayHigh"), price)
                low = self._to_float(getattr(fi, "day_low", None) or fi.get("dayLow"), price)
                volume = int(self._to_float(getattr(fi, "last_volume", None) or fi.get("lastVolume"), 0))
            except Exception:
                pass

            inf: dict = {}
            if not price or not prev_close:
                try:
                    raw = t.info
                    if isinstance(raw, dict):
                        inf = raw
                except Exception:
                    pass

            if not price and inf:
                price = self._to_float(inf.get("regularMarketPrice") or inf.get("currentPrice"), 0.0)
            if not prev_close and inf:
                prev_close = self._to_float(inf.get("regularMarketPreviousClose") or inf.get("previousClose"), 0.0)
            if not open_price and inf:
                open_price = self._to_float(inf.get("regularMarketOpen") or inf.get("open"), price)
            if not high and inf:
                high = self._to_float(inf.get("regularMarketDayHigh") or inf.get("dayHigh"), price)
            if not low and inf:
                low = self._to_float(inf.get("regularMarketDayLow") or inf.get("dayLow"), price)
            if not volume and inf:
                volume = int(self._to_float(inf.get("regularMarketVolume") or inf.get("volume"), 0))

            if not price:
                snap = self._fetch_yahoo_chart_snapshot(yf_symbol)
                if snap:
                    price = self._to_float(snap.get("price"), 0.0)
                    prev_close = prev_close or self._to_float(snap.get("prev_close"), 0.0)

            if not price:
                continue
            if not prev_close:
                prev_close = price
            change = price - prev_close
            pct = (change / prev_close * 100) if prev_close else 0.0

            name = YF_COMPANY_NAMES.get(sym, sym)
            sector = SECTOR_MAP.get(sym, "Other")
            try:
                if inf:
                    name = inf.get("longName") or inf.get("shortName") or name
                    if sector == "Other":
                        sector = inf.get("sector") or inf.get("industry") or sector
            except Exception:
                pass

            if not high:
                high = price
            if not low:
                low = price
            if not open_price:
                open_price = price

            stocks[sym] = self._build_stock_payload(
                symbol=sym,
                name=name,
                sector=sector,
                price=price,
                change=change,
                change_pct=pct,
                open_price=open_price,
                high=high,
                low=low,
                volume=volume,
                prev_close=prev_close,
                year_high=high,
                year_low=low,
                source="yahoo",
            )
        if TWELVE_DATA_API_KEY and TWELVE_DATA_STOCK_FALLBACK_CAP:
            filled = 0
            for sym in YF_STOCK_SYMBOLS:
                if sym in stocks:
                    continue
                if filled >= TWELVE_DATA_STOCK_FALLBACK_CAP:
                    break
                tq = self._fetch_twelve_equity_quote(sym)
                if tq:
                    stocks[sym] = tq
                    filled += 1
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
                    "source": "nse",
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

    def _fetch_global_futures(self):
        """Fetch global markets via yfinance batch (used for initial seed + polling fallback)."""
        try:
            yf_entries = [(lbl, sym, reg) for lbl, sym, reg in GLOBAL_FUTURES if sym]
            symbols = [sym for _, sym, _ in yf_entries]
            tickers = yf.Tickers(" ".join(symbols))
            results = []
            for label, sym, region in yf_entries:
                row = None
                if self._prefer_twelve_global_row(sym):
                    row = self._try_twelve_global_futures_row(label, sym, region)
                try:
                    if not row:
                        price = 0.0
                        prev = 0.0
                        inf: dict = {}
                        try:
                            t = tickers.tickers[sym]
                            try:
                                fi = t.fast_info
                                price = self._to_float(fi.last_price, 0.0)
                                prev = self._to_float(fi.previous_close, 0.0)
                            except Exception:
                                pass
                            if not price or not prev:
                                try:
                                    raw = t.info
                                    if isinstance(raw, dict):
                                        inf = raw
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        if not price and inf:
                            price = self._to_float(
                                inf.get("regularMarketPrice") or inf.get("currentPrice"),
                                0.0,
                            )
                        if not prev and inf:
                            prev = self._to_float(
                                inf.get("regularMarketPreviousClose") or inf.get("previousClose"),
                                0.0,
                            )
                        if not price or not prev:
                            snap = self._fetch_yahoo_chart_snapshot(sym)
                            if snap:
                                if not price:
                                    price = self._to_float(snap.get("price"), 0.0)
                                if not prev:
                                    prev = self._to_float(snap.get("prev_close"), 0.0)
                        row = self._build_global_futures_row(label, sym, region, price, prev, inf)
                except Exception:
                    pass
                if row:
                    results.append(row)
                else:
                    trow = self._try_twelve_global_futures_row(label, sym, region)
                    if trow:
                        results.append(trow)
            if self._gift_nifty:
                gn = self._gift_nifty
                results.append(self._decorate_global_market_row({
                    "name": "GIFT NIFTY",
                    "symbol": "GIFTNIFTY",
                    "region": "ASIAN MARKETS",
                    "price": gn["price"],
                    "change": gn["change"],
                    "change_pct": gn["change_pct"],
                }))
            if results:
                order = {r: i for i, (_, _, r) in enumerate(GLOBAL_FUTURES)}
                results.sort(key=lambda x: (order.get(x["region"], 99),
                    next((i for i, (l, _, _) in enumerate(GLOBAL_FUTURES) if l == x["name"]), 99)))
                self._global_futures = results
        except Exception as e:
            print(f"[Global] {e}")

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
                    "source": "nse",
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
        is_breaking = _has_keyword(lower, _BREAKING_MARKET_KW)
        is_stock_event = _has_keyword(combined, _STOCK_EVENT_KW)
        if is_stock_event:
            is_breaking = False
        is_gold_silver = _has_keyword(combined, _GOLD_SILVER_KW)
        is_company_specific = bool(_TICKER_PAREN_RE.search(title)) or bool(_SHARES_MOVE_RE.search(combined)) or is_stock_event
        # Generic market relevance: used for GLOBAL tab filtering
        is_market_rel = _has_keyword(combined, _GLOBAL_MARKET_KW) and not _has_keyword(combined, _GLOBAL_EXCLUDE_KW)
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
            "keyword_stocks": matched,
        }

    @staticmethod
    def _news_combined_text(item: dict) -> str:
        return f"{item.get('title') or ''} {item.get('summary') or ''}".lower()

    @classmethod
    def _infer_news_sentiment(cls, item: dict) -> str:
        """Fast market read-through used only until the LLM supplies a label."""
        combined = cls._news_combined_text(item)
        if not combined:
            return "neutral"
        bullish_kw = {
            "stocks rally", "stocks rise", "stocks gain", "markets rally", "markets rise",
            "futures rise", "futures gain", "record high", "all-time high",
            "nifty gains", "sensex gains", "nifty rises", "sensex rises",
            "rate cut", "repo rate cut", "fed cuts", "yield falls", "bond yield falls",
            "inflation cools", "inflation eases", "cpi cools", "pmi rises",
            "rupee gains", "rupee rises", "inr gains", "dollar index falls",
            "oil prices fall", "oil prices edge lower", "crude oil falls", "brent falls",
            "fii buys", "fii inflow", "fpi inflow", "foreign inflow",
        }
        bearish_kw = {
            "stocks sink", "stocks fall", "markets fall", "markets slide",
            "futures fall", "futures slip", "sell-off", "selloff", "risk-off",
            "market crash", "flash crash", "plunge", "tumble", "bloodbath",
            "nifty falls", "sensex falls", "nifty slips", "sensex slips",
            "rate hike", "repo rate hike", "fed hikes", "yield rises", "bond yield rises",
            "inflation rises", "inflation accelerates", "hot inflation",
            "rupee falls", "rupee weakens", "inr weakens", "dollar index rises",
            "oil prices rise", "crude oil rises", "brent rises", "rising oil prices",
            "capital outflows", "foreign outflows", "fii sells", "fii outflow",
            "missile strike", "drone attack", "sanctions", "strait of hormuz",
        }
        score = 0
        for phrase in bullish_kw:
            if phrase in combined:
                score += 1
        for phrase in bearish_kw:
            if phrase in combined:
                score -= 1
        if _has_keyword(combined, {"surge", "soar", "jump", "rally"}) and not _has_keyword(combined, {"oil", "gold"}):
            score += 1
        if _has_keyword(combined, {"drop", "slump", "slide", "plummet"}):
            score -= 1
        if item.get("gold_silver") and _has_keyword(combined, {"war", "risk-off", "uncertainty", "sanction"}):
            score -= 1
        if item.get("india_market_impact") and _has_keyword(combined, {"oil"}) and _has_keyword(combined, {"lower", "fall", "drop"}):
            score += 1
        if item.get("india_market_impact") and _has_keyword(combined, {"oil"}) and _has_keyword(combined, {"rise", "higher", "surge"}):
            score -= 1
        if score > 0:
            return "bullish"
        if score < 0:
            return "bearish"
        return "neutral"

    @classmethod
    def _ensure_visible_news_sentiment(cls, item: dict) -> dict:
        sentiment = str(item.get("sentiment") or "").strip().lower()
        if sentiment in {"bullish", "bearish"}:
            item["sentiment"] = sentiment
            return item
        if sentiment == "neutral" and item.get("llm_classified"):
            item["sentiment"] = sentiment
            return item
        inferred = cls._infer_news_sentiment(item)
        item["sentiment"] = inferred if inferred != "neutral" else "neutral"
        if item["sentiment"] != "neutral":
            item["sentiment_source"] = "rule"
        return item

    @classmethod
    def _is_low_signal_news(cls, item: dict) -> bool:
        combined = cls._news_combined_text(item)
        if _has_keyword(combined, _GLOBAL_EXCLUDE_KW) or _has_keyword(combined, _LOW_SIGNAL_NEWS_KW):
            return True
        source = str((item or {}).get("source") or "").lower()
        if _has_keyword(source, _LOW_SIGNAL_SOURCE_KW) and not item.get("breaking_confirmed"):
            return True
        return False

    @classmethod
    def _has_market_moving_signal(cls, item: dict) -> bool:
        combined = cls._news_combined_text(item)
        if item.get("breaking") or item.get("breaking_hint") or item.get("breaking_confirmed"):
            return True
        if item.get("india_market_impact") or item.get("keyword_stocks"):
            return True
        if _has_keyword(combined, _MARKET_MOVING_KW):
            return True
        return bool(item.get("market_relevant") and _has_keyword(combined, _BREAKING_CLUSTER_EVENT_KW))

    @classmethod
    def _should_llm_classify_news_item(cls, item: dict) -> bool:
        if not isinstance(item, dict):
            return False
        if int(item.get("age_secs", 999999) or 999999) > NEWS_MAX_VISIBLE_AGE_SECS:
            return False
        if cls._is_low_signal_news(item):
            return False
        if item.get("company_specific") and not item.get("keyword_stocks"):
            return False
        return cls._has_market_moving_signal(item)

    @staticmethod
    def _is_tradable_news_item(item: dict) -> bool:
        """Return True only for news worth surfacing in a market terminal panel."""
        if not isinstance(item, dict):
            return False
        if int(item.get("age_secs", 999999) or 999999) > NEWS_MAX_VISIBLE_AGE_SECS:
            return False
        if DataEngine._is_low_signal_news(item):
            return False
        if item.get("watchlist_stocks") or item.get("watchlist_important"):
            return True
        if (item.get("company_specific") or item.get("stock_event")) and not item.get("keyword_stocks"):
            return False
        if item.get("gold_silver") and DataEngine._has_market_moving_signal(item):
            return True
        if item.get("india_market_impact") and DataEngine._has_market_moving_signal(item):
            return True
        if item.get("market_relevant") and DataEngine._has_market_moving_signal(item):
            return True
        if item.get("keyword_stocks"):
            return True
        if item.get("stock_event"):
            combined = f"{item.get('title') or ''} {item.get('source') or ''}"
            title_tags = DataEngine._classify_news(item.get("title") or "", "")
            if not title_tags.get("stock_event"):
                return False
            return not _has_keyword(combined, _GLOBAL_EXCLUDE_KW)
        if item.get("llm_classified"):
            return False
        return bool(item.get("breaking"))

    @staticmethod
    def _news_source_key(item: dict) -> str:
        raw = str((item or {}).get("source") or "").strip().lower()
        raw = re.sub(r"[^a-z0-9]+", " ", raw)
        raw = re.sub(r"\b(news|markets?|business|latest|top|rss|feed|live)\b", " ", raw)
        key = " ".join(raw.split())[:40]
        if key:
            return key
        link = str((item or {}).get("link") or "")
        try:
            host = urlparse(link).netloc.lower().replace("www.", "")
            return host.split(":")[0]
        except Exception:
            return ""

    @staticmethod
    def _same_story_tokens(left: Set[str], right: Set[str]) -> bool:
        if len(left) < 3 or len(right) < 3:
            return False
        shared = len(left & right)
        if shared < 3:
            return False
        union = len(left | right)
        jaccard = shared / max(union, 1)
        containment = shared / max(min(len(left), len(right)), 1)
        return jaccard >= 0.42 or (shared >= 4 and containment >= 0.62)

    @classmethod
    def _is_breaking_cluster_candidate(cls, item: dict) -> bool:
        if not cls._is_tradable_news_item(item):
            return False
        if item.get("stock_event") or item.get("company_specific") or item.get("watchlist_stocks"):
            return False
        if int(item.get("age_secs", 999999) or 999999) > BREAKING_PIN_TTL_SECS:
            return False
        title = str(item.get("title") or "").lower()
        if _has_keyword(title, _BREAKING_CLUSTER_NOISE_KW):
            return False
        if not (item.get("breaking_hint") or _has_keyword(title, _BREAKING_CLUSTER_EVENT_KW)):
            return False
        return bool(
            item.get("breaking_hint")
            or item.get("india_market_impact")
            or item.get("gold_silver")
        )

    @classmethod
    def _reset_breaking_flags(cls, item: dict):
        if not isinstance(item, dict):
            return
        if item.get("breaking"):
            item["breaking_hint"] = True
        item["breaking"] = False
        item["breaking_pinned"] = False
        item["breaking_confirmed"] = False
        item.pop("breaking_cluster_count", None)
        item.pop("breaking_sources", None)
        item.pop("breaking_reason", None)
        item.pop("breaking_expires_at", None)

    @classmethod
    def _apply_breaking_clusters(cls, items: List[dict], now_ts: Optional[float] = None) -> List[dict]:
        now_ts = now_ts or time.time()
        for item in items:
            cls._reset_breaking_flags(item)

        groups: List[dict] = []
        candidates: List[tuple[int, dict, Set[str], str, int]] = []
        for idx, item in enumerate(items):
            if not cls._is_breaking_cluster_candidate(item):
                continue
            source_key = cls._news_source_key(item)
            if not source_key:
                continue
            tokens = _breaking_cluster_tokens(str(item.get("title") or ""))
            if len(tokens) < 3:
                continue
            age = int(item.get("age_secs", 999999) or 999999)
            candidates.append((idx, item, tokens, source_key, age))

        candidates.sort(key=lambda row: row[4])
        for idx, item, tokens, source_key, age in candidates:
            target = None
            for group in groups:
                if age - group["youngest_age"] > BREAKING_CLUSTER_WINDOW_SECS:
                    continue
                if cls._same_story_tokens(tokens, group["tokens"]):
                    target = group
                    break
            if target is None:
                target = {
                    "indices": [],
                    "tokens": set(tokens),
                    "sources": {},
                    "youngest_age": age,
                    "oldest_age": age,
                }
                groups.append(target)
            target["indices"].append(idx)
            target["sources"].setdefault(source_key, str(item.get("source") or source_key)[:32])
            target["oldest_age"] = max(target["oldest_age"], age)
            target["tokens"] = set(target["tokens"]) | set(tokens)

        for group in groups:
            source_names = list(group["sources"].values())
            if len(source_names) < BREAKING_CLUSTER_MIN_SOURCES:
                continue
            if group["youngest_age"] > BREAKING_PIN_TTL_SECS:
                continue
            expires_at = now_ts + max(60, BREAKING_PIN_TTL_SECS - int(group["youngest_age"]))
            for idx in group["indices"]:
                item = items[idx]
                item["breaking"] = True
                item["breaking_pinned"] = True
                item["breaking_confirmed"] = True
                item["breaking_cluster_count"] = len(source_names)
                item["breaking_sources"] = source_names[:6]
                item["breaking_reason"] = (
                    f"{len(source_names)} sources within {BREAKING_CLUSTER_WINDOW_SECS // 60}m"
                )
                item["breaking_expires_at"] = expires_at
                item["breaking_hint"] = True
        return items

    @classmethod
    def _refresh_breaking_flags(cls, items: List[dict]) -> List[dict]:
        now_ts = time.time()
        for item in items:
            if item.get("breaking_confirmed") and float(item.get("breaking_expires_at") or 0) > now_ts:
                item["breaking"] = True
                item["breaking_pinned"] = True
            else:
                if item.get("breaking"):
                    item["breaking_hint"] = True
                item["breaking"] = False
                item["breaking_pinned"] = False
        return items

    @classmethod
    def _sort_news_with_breaking_pins(cls, items: List[dict]) -> List[dict]:
        for item in items:
            cls._refresh_news_item_age(item)
        cls._refresh_breaking_flags(items)
        pinned = sorted(
            [item for item in items if item.get("breaking") and item.get("breaking_pinned")],
            key=lambda item: int(item.get("age_secs", 999999) or 999999),
        )[:BREAKING_PIN_MAX_ITEMS]
        pinned_ids = {id(item) for item in pinned}
        return pinned + [item for item in items if id(item) not in pinned_ids]

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
    def _refresh_news_item_age(item: dict):
        """Keep cached/live news age honest so old items do not stay pinned as fresh."""
        if not isinstance(item, dict):
            return
        ts = item.get("published_at_ts")
        if not ts:
            return
        try:
            pub_ts = float(ts)
        except (TypeError, ValueError):
            return
        age_secs = max(0, int(time.time() - pub_ts))
        item["age_secs"] = age_secs
        item["time"] = DataEngine._relative_time(datetime.fromtimestamp(pub_ts, tz=IST))
        item["is_fresh"] = age_secs < 900

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

    def _llm_call_one(self, headline: str, model: str) -> Optional[dict]:
        """Classify a single headline via NVIDIA API. Returns parsed dict or None."""
        user_msg = _LLM_PROMPT_PREFIX + f'1. "{headline}"'
        try:
            r = self._nv_post_json(
                self._build_nv_payload(
                    model,
                    [
                        {"role": "system", "content": _LLM_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                    0.1,
                    512,
                ),
                timeout=30,
            )
            if not r or r.status_code != 200:
                return None
            text = self._extract_json_array_text(r.json()["choices"][0]["message"]["content"].strip())
            arr = json.loads(text)
            return arr[0] if arr else None
        except Exception:
            return None

    def _llm_call_many(self, headlines: List[str], model: str) -> List[Optional[dict]]:
        """Classify multiple headlines in one request. Returns results aligned with input order."""
        if not headlines:
            return []
        user_msg = _LLM_PROMPT_PREFIX + "\n".join(
            f'{idx + 1}. "{headline}"' for idx, headline in enumerate(headlines)
        )
        try:
            r = self._nv_post_json(
                self._build_nv_payload(
                    model,
                    [
                        {"role": "system", "content": _LLM_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                    0.1,
                    min(2048, max(640, 220 * len(headlines))),
                ),
                timeout=max(30, min(60, 12 + 5 * len(headlines))),
            )
            if not r or r.status_code != 200:
                return [None] * len(headlines)
            text = self._extract_json_array_text(r.json()["choices"][0]["message"]["content"].strip())
            arr = json.loads(text)
            out: List[Optional[dict]] = [None] * len(headlines)
            if isinstance(arr, dict):
                arr = [arr]
            if not isinstance(arr, list):
                return out
            for entry in arr:
                if not isinstance(entry, dict):
                    continue
                try:
                    pos = int(entry.get("idx", 0)) - 1
                except Exception:
                    continue
                if 0 <= pos < len(out):
                    out[pos] = entry
            return out
        except Exception:
            return [None] * len(headlines)

    def _llm_classify_all(self, items: List[dict]):
        """Classify headlines: apply cache, then LLM for each new one."""
        if not NV_API_KEY or not items:
            return

        valid_syms = self._valid_equity_symbols()
        uncached = []
        for item in items:
            cache_key = item["title"][:80].lower()
            if cache_key in self._llm_cache:
                self._llm_cache.move_to_end(cache_key)
                cached = self._llm_cache[cache_key]
                if cached["stocks"]:
                    item["watchlist_stocks"] = list(dict.fromkeys(cached["stocks"]))
                item["sentiment"] = cached["sentiment"]
                item["impact"] = cached["impact"]
                item["breaking_hint"] = bool(item.get("breaking_hint") or cached.get("breaking", False))
                item["gold_silver"] = cached.get("gold_silver", False) or item.get("gold_silver", False)
                item["india_market_impact"] = cached.get("india_market_impact", False)
                item["market_relevant"] = cached.get("market_relevant", item.get("market_relevant", False))
                item["company_specific"] = cached.get("company_specific", item.get("company_specific", False))
                item["llm_classified"] = True
                item["breaking"] = bool(
                    item.get("breaking_confirmed")
                    and float(item.get("breaking_expires_at") or 0) > time.time()
                )
                item["breaking_pinned"] = item["breaking"]
            else:
                uncached.append(item)

        if not uncached:
            return

        model = NV_FAST_MODEL if len(uncached) > 10 else NV_API_MODEL
        ok = 0
        chunks = self._split_llm_jobs(
            [{"title": item["title"], "company_specific": item.get("company_specific", False), "stock_event": item.get("stock_event", False)} for item in uncached],
            NV_MAX_PARALLEL,
        )
        chunk_items = []
        start = 0
        for chunk in chunks:
            chunk_items.append(uncached[start:start + len(chunk)])
            start += len(chunk)
        with ThreadPoolExecutor(max_workers=max(1, min(len(chunks), NV_MAX_PARALLEL))) as pool:
            future_map = {
                pool.submit(self._llm_run_chunk, chunk, model): (chunk, items_chunk)
                for chunk, items_chunk in zip(chunks, chunk_items)
            }
            for future, (chunk, items_chunk) in future_map.items():
                try:
                    entries = future.result()
                except Exception:
                    entries = [None] * len(chunk)
                for item, job, entry in zip(items_chunk, chunk, entries):
                    if not entry or not isinstance(entry, dict):
                        item["india_market_impact"] = False
                        item["breaking_hint"] = bool(item.get("breaking_hint"))
                        item["breaking"] = False
                        item["breaking_pinned"] = False
                        continue
                    result = self._normalize_llm_entry(job, entry, valid_syms)
                    cache_key = item["title"][:80].lower()
                    self._llm_cache[cache_key] = result
                    while len(self._llm_cache) > LLM_CACHE_SIZE:
                        self._llm_cache.popitem(last=False)
                    if result["stocks"]:
                        item["watchlist_stocks"] = list(dict.fromkeys(result["stocks"]))
                    item["sentiment"] = result["sentiment"]
                    item["impact"] = result["impact"]
                    item["breaking_hint"] = bool(item.get("breaking_hint") or result["breaking"])
                    item["gold_silver"] = result["gold_silver"] or item.get("gold_silver", False)
                    item["india_market_impact"] = result["india_market_impact"]
                    item["market_relevant"] = result["market_relevant"]
                    item["company_specific"] = result["company_specific"]
                    item["llm_classified"] = True
                    item["breaking"] = bool(
                        item.get("breaking_confirmed")
                        and float(item.get("breaking_expires_at") or 0) > time.time()
                    )
                    item["breaking_pinned"] = item["breaking"]
                    ok += 1

        print(f"[LLM] {ok}/{len(uncached)} new via {model.split('/')[-1]}, "
              f"{len(items)-len(uncached)} cached")

    def _load_feed(self, url: str):
        """Fetch and parse one RSS feed with an explicit network timeout."""
        headers = {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
        }
        r = requests.get(url, timeout=NEWS_FEED_TIMEOUT_SECS, headers=headers, allow_redirects=True)
        if r.status_code >= 400:
            raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
        return feedparser.parse(r.content)

    @staticmethod
    def _parse_alpha_vantage_time(value: str) -> Optional[datetime]:
        try:
            dt = datetime.strptime(str(value or ""), "%Y%m%dT%H%M%S")
            return pytz.utc.localize(dt).astimezone(IST)
        except Exception:
            return None

    def _fetch_alpha_vantage_news(self, raw: List[dict], now: datetime):
        """Optional low-lag market news source when ALPHA_VANTAGE_API_KEY is configured."""
        if not ALPHA_VANTAGE_NEWS_ENABLED:
            return
        time_from = (datetime.utcnow() - timedelta(hours=NEWS_LOOKBACK_HOURS)).strftime("%Y%m%dT%H%M")
        params = {
            "function": "NEWS_SENTIMENT",
            "topics": "financial_markets,economy_monetary,economy_macro,energy_transportation",
            "time_from": time_from,
            "sort": "LATEST",
            "limit": "50",
            "apikey": ALPHA_VANTAGE_API_KEY,
        }
        try:
            r = requests.get(
                "https://www.alphavantage.co/query",
                params=params,
                timeout=NEWS_FEED_TIMEOUT_SECS,
                headers={"User-Agent": random.choice(_USER_AGENTS)},
            )
            if r.status_code != 200:
                print(f"[News] Alpha Vantage error: HTTP {r.status_code}")
                return
            data = r.json()
        except Exception as e:
            print(f"[News] Alpha Vantage error: {e}")
            return
        for entry in (data.get("feed") or [])[:50]:
            title = str(entry.get("title") or "").strip()
            if not title:
                continue
            pub_dt = self._parse_alpha_vantage_time(entry.get("time_published", ""))
            if not pub_dt:
                continue
            age_secs = int((now - pub_dt).total_seconds())
            if age_secs < 0:
                age_secs = 0
            if age_secs > NEWS_MAX_VISIBLE_AGE_SECS:
                continue
            summary = str(entry.get("summary") or "").strip()[:300]
            tags = self._classify_news(title, summary)
            url = str(entry.get("url") or "").strip()
            source = str(entry.get("source") or "").strip()
            if not source and url:
                source = urlparse(url).netloc.replace("www.", "")[:20]
            combined_text = (title + " " + summary).lower()
            india_hint = _has_keyword(combined_text, _INDIA_IMPACT_HINT_KW)
            raw.append({
                "title": title,
                "summary": summary,
                "link": url,
                "source": (source or "Alpha Vantage")[:20],
                "age_secs": age_secs,
                "published_at_ts": pub_dt.timestamp(),
                "time": self._relative_time(pub_dt),
                "is_fresh": age_secs < 900,
                "global_news": True,
                "india_news": _has_keyword(combined_text, _INDIA_NEWS_KW),
                "breaking": False,
                "breaking_hint": bool(tags["breaking"] or india_hint),
                "stock_event": tags["stock_event"],
                "gold_silver": tags["gold_silver"],
                "india_market_impact": india_hint,
                "market_relevant": tags.get("market_relevant", False),
                "company_specific": tags.get("company_specific", False),
                "keyword_stocks": tags["keyword_stocks"],
                "watchlist_stocks": [],
                "sentiment": str(entry.get("overall_sentiment_label") or "").lower() or "neutral",
            })

    def _fetch_all_news(self):
        """Fetch RSS feeds + Tradient API, merge, sort by recency."""
        now = datetime.now(IST)
        cutoff = now - timedelta(hours=NEWS_LOOKBACK_HOURS)
        global_cutoff = now - timedelta(hours=NEWS_GLOBAL_LOOKBACK_HOURS)
        gold_cutoff = now - timedelta(hours=NEWS_GOLD_LOOKBACK_HOURS)
        raw: List[dict] = []

        with ThreadPoolExecutor(max_workers=NEWS_FEED_WORKERS) as pool:
            future_to_url = {pool.submit(self._load_feed, url): url for url in NEWS_FEEDS}
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    feed = future.result()
                except Exception as e:
                    print(f"[News] RSS error for {url}: {e}")
                    continue
                is_global_feed = url in _GLOBAL_FEEDS_SET
                is_gold_feed = url in _GOLD_FEEDS_SET
                is_india_feed = url in _INDIA_FEEDS_SET
                is_gdelt_feed = url in _GDELT_FEEDS_SET
                source = feed.feed.get("title", "")
                if " - " in source:
                    parts = source.split(" - ")
                    if parts[-1].strip().lower().startswith("google"):
                        source = "Google News"
                    else:
                        source = parts[0]
                source = source.strip()[:20]
                entry_cap = 12 if ("news.google.com" in url or is_gdelt_feed) else 15
                for entry in feed.entries[:entry_cap]:
                    pub_dt = self._parse_pub_time(entry)
                    if pub_dt:
                        if is_gold_feed and pub_dt < gold_cutoff:
                            continue
                        elif is_global_feed and pub_dt < global_cutoff:
                            continue
                        elif (not is_global_feed) and pub_dt < cutoff:
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
                    if age_secs < 0:
                        age_secs = 0
                    if age_secs > NEWS_MAX_VISIBLE_AGE_SECS:
                        continue
                    display_src = self._display_news_source(url, source, title, entry)
                    combined_text = title.lower() + (" " + summary.lower() if summary else "")
                    india_hint = _has_keyword(combined_text, _INDIA_IMPACT_HINT_KW)
                    india_news = is_india_feed or _has_keyword(combined_text, _INDIA_NEWS_KW)
                    brk_pre = india_hint and not tags.get("stock_event", False) and not tags.get("company_specific", False)
                    raw.append({
                        "title": title,
                        "summary": summary,
                        "link": entry.get("link", ""),
                        "source": display_src,
                        "age_secs": age_secs,
                        "published_at_ts": pub_dt.timestamp() if pub_dt else None,
                        "time": self._relative_time(pub_dt) if pub_dt else "",
                        "is_fresh": age_secs < 900,
                        "global_news": is_global_feed or is_gdelt_feed,
                        "india_news": india_news,
                        "breaking": brk_pre,
                        "breaking_hint": tags["breaking"],
                        "stock_event": tags["stock_event"],
                        "gold_silver": tags["gold_silver"],
                        "india_market_impact": india_hint,
                        "market_relevant": tags.get("market_relevant", False),
                        "company_specific": tags.get("company_specific", False),
                        "keyword_stocks": tags["keyword_stocks"],
                        "watchlist_stocks": [],
                    })

        self._fetch_alpha_vantage_news(raw, now)
        self._fetch_tradient_news(raw, now, cutoff)
        self._apply_breaking_clusters(raw)

        raw.sort(key=lambda x: x["age_secs"])

        seen_keys: Set[str] = set()
        seen_fuzzy: Set[str] = set()
        unique: List[dict] = []
        for item in raw:
            title_lower = item["title"].lower()
            words = set(re.findall(r"[a-z]{4,}", title_lower))
            exact_key = re.sub(r"\s+", " ", title_lower)[:80]
            if exact_key in seen_keys:
                continue
            fuzzy_key = " ".join(sorted(w for w in words if w not in _BREAKING_CLUSTER_STOPWORDS)[:10])
            if fuzzy_key and fuzzy_key in seen_fuzzy:
                continue
            seen_keys.add(exact_key)
            if fuzzy_key:
                seen_fuzzy.add(fuzzy_key)
            unique.append(item)
        with self._news_lock:
            prior_live = []
            prior_watchlist = []
            for n in self._news:
                self._refresh_news_item_age(n)
                age_secs = int(n.get("age_secs", 999999) or 999999)
                if n.get("live_story") and age_secs <= NEWS_MAX_VISIBLE_AGE_SECS:
                    prior_live.append(n)
                elif (
                    n.get("watchlist_stocks")
                    and age_secs <= NEWS_MAX_VISIBLE_AGE_SECS
                    and n.get("watchlist_injected_at", 0) > time.time() - 12 * 3600
                ):
                    prior_watchlist.append(n)
        if not unique and not prior_live and not prior_watchlist:
            self._last_news_refresh_ts = time.time()
            return
        # Reserve visible slots for market-relevant items, while keeping a bounded
        # pending tail so LLM classification can still rescue semantic headlines.
        display_ready = [u for u in unique if self._is_tradable_news_item(u)]
        pending_llm = [
            u for u in unique
            if not self._is_tradable_news_item(u) and self._should_llm_classify_news_item(u)
        ]
        display_ready.sort(key=lambda item: int(item.get("age_secs", 999999) or 999999))
        unique_trim = (display_ready + pending_llm[:NEWS_PENDING_LLM_TAIL])[:100]
        live_cap = min(len(prior_live), 30)
        capped_live = prior_live[:live_cap]
        combined: List[dict] = []
        seen_k: Set[str] = set()
        for item in capped_live + prior_watchlist[:40] + unique_trim:
            k = item["title"][:50].lower()
            if k in seen_k:
                continue
            seen_k.add(k)
            combined.append(item)
        combined = self._sort_news_with_breaking_pins(combined)
        with self._news_lock:
            self._news = combined[:130]
            self._last_news_refresh_ts = time.time()
        llm_candidates = [
            item for item in unique_trim
            if self._should_llm_classify_news_item(item)
        ][:NEWS_PENDING_LLM_TAIL]
        if llm_candidates:
            self._push_llm_stack(llm_candidates)

    def _refresh_news_if_stale(self, force: bool = False):
        now_ts = time.time()
        if not force and self._news and now_ts - self._last_news_refresh_ts < NEWS_STALE_SECS:
            return
        if not self._news_refresh_lock.acquire(blocking=False):
            return
        try:
            now_ts = time.time()
            if force or not self._news or now_ts - self._last_news_refresh_ts >= NEWS_STALE_SECS:
                self._fetch_all_news()
        finally:
            self._news_refresh_lock.release()

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
                trad_combined = (title + " " + body + " " + stock).lower()
                trad_india_hint = _has_keyword(trad_combined, _INDIA_IMPACT_HINT_KW)
                trad_brk = trad_india_hint and not tags.get("stock_event", False) and not tags.get("company_specific", False)
                raw.append({
                    "title": title,
                    "link": link,
                    "source": source,
                    "age_secs": age_secs,
                    "published_at_ts": pub_dt.timestamp() if pub_dt else None,
                    "time": self._relative_time(pub_dt) if pub_dt else "",
                    "is_fresh": age_secs < 900,
                    "global_news": False,
                    "india_news": True,
                    "breaking": trad_brk,
                    "breaking_hint": tags["breaking"],
                    "stock_event": tags["stock_event"],
                    "gold_silver": tags["gold_silver"],
                    "india_market_impact": trad_india_hint,
                    "market_relevant": tags.get("market_relevant", False),
                    "company_specific": tags.get("company_specific", False),
                    "keyword_stocks": tags["keyword_stocks"],
                    "watchlist_stocks": [],
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

    @staticmethod
    def watchlist_hash(symbols: List[str]) -> str:
        normalized = ",".join(sorted({(sym or "").strip().upper() for sym in symbols if sym}))
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12] if normalized else "empty"

    def build_overview_panel(self) -> dict:
        self._fetch_nse_data()
        self._fetch_gift_nifty()
        self._compute_movers()
        self._compute_sectors()
        self._last_update = datetime.now(IST).strftime("%H:%M:%S")
        return self._overview_payload()

    def build_global_panel(self) -> dict:
        if not self._gift_nifty:
            self._fetch_gift_nifty()
        self._fetch_global_futures()
        self._last_global_update = datetime.now(IST).strftime("%H:%M:%S")
        return {
            "global_futures": self._global_futures,
            "last_global_update": self._last_global_update,
            "global_streaming": bool(self._global_stream_connected and self.background_enabled),
        }

    def build_news_panel(self, tab: str, watchlist_symbols: Optional[List[str]] = None) -> dict:
        normalized_tab = (tab or "all").strip().lower()
        if normalized_tab in {"all", "breaking"} or not self._news:
            self._refresh_news_if_stale()
        if normalized_tab == "watchlist":
            symbols = self._normalize_watchlist_symbols(watchlist_symbols or [])
            current_items = self._current_watchlist_news_items(symbols)
            refresh_limit = WATCHLIST_PANEL_REFRESH_SYMBOL_CAP
            refreshed_symbols: Set[str] = set()
            if len(current_items) < WATCHLIST_PANEL_SPARSE_TARGET_ITEMS:
                refresh_limit = min(len(symbols), WATCHLIST_PANEL_SPARSE_REFRESH_SYMBOL_CAP)
            _, first_batch = self._refresh_watchlist_news_sync(symbols, limit=refresh_limit)
            refreshed_symbols.update(first_batch)
            current_items = self._current_watchlist_news_items(symbols)
            if (
                len(current_items) < WATCHLIST_PANEL_SPARSE_TARGET_ITEMS
                and len(refreshed_symbols) < len(symbols)
            ):
                extra_limit = min(
                    len(symbols) - len(refreshed_symbols),
                    WATCHLIST_PANEL_DEEP_REFRESH_SYMBOL_CAP,
                )
                if extra_limit > 0:
                    _, extra_batch = self._refresh_watchlist_news_sync(
                        symbols,
                        limit=extra_limit,
                        skip_symbols=refreshed_symbols,
                    )
                    refreshed_symbols.update(extra_batch)
                    current_items = self._current_watchlist_news_items(symbols)
            current_items = [self._ensure_visible_news_sentiment(item) for item in current_items]
            return {
                "items": current_items,
                "watchlist_hash": self.watchlist_hash(symbols),
                "news_llm_pending": self._llm_stack_pending_count(),
                "news_llm_enabled": bool(NV_API_KEY),
            }
        if normalized_tab in {"all", "breaking"} and self._llm_stack_pending_count() and not self.background_enabled:
            self.process_llm_queue_sync(REQUEST_LLM_SYNC_MAX_ITEMS)
        items = self._sort_news_with_breaking_pins([
            item for item in self._news if self._is_tradable_news_item(item)
        ])
        items = [self._ensure_visible_news_sentiment(item) for item in items]
        if normalized_tab == "breaking":
            items = [
                item
                for item in items
                if item.get("breaking")
                and not item.get("stock_event")
                and not item.get("company_specific")
            ]
        return {
            "items": items,
            "news_llm_pending": self._llm_stack_pending_count(),
            "news_llm_enabled": bool(NV_API_KEY),
        }

    def build_watchlist_quotes_panel(self, symbols: List[str]) -> dict:
        rows: List[dict] = []
        ordered_symbols: List[str] = []
        for raw in symbols:
            sym = (raw or "").strip().upper()
            if not sym or sym in ordered_symbols:
                continue
            ordered_symbols.append(sym)
            stock = self.get_stock(sym)
            if stock:
                rows.append(stock)
        return {"symbols": ordered_symbols, "rows": rows}

    def ensure_data_ready(
        self,
        force_quotes: bool = False,
        refresh_news: bool = False,
        refresh_globals: bool = False,
    ):
        """Sync fetch for serverless request lifecycles.

        If `force_quotes` is True, always refresh indices/stocks from source so % changes
        reflect the latest snapshot instead of an in-memory cache.
        """
        if not self._ensure_ready_lock.acquire(blocking=False):
            return
        try:
            if force_quotes or (not self._stocks or not self._indices):
                try:
                    self._fetch_nse_data()
                except Exception:
                    traceback.print_exc()
            if force_quotes or not self._gift_nifty:
                try:
                    self._fetch_gift_nifty()
                except Exception:
                    traceback.print_exc()
            should_fetch_globals = force_quotes or (
                not self._global_futures and (not self._running or not self._global_task)
            )
            if refresh_globals and (not self._global_task):
                should_fetch_globals = True
            if should_fetch_globals:
                try:
                    self._fetch_global_futures()
                except Exception:
                    traceback.print_exc()
            should_fetch_news = not self._news and (not self._running or not self._news_task)
            if refresh_news and (not self._news_task):
                should_fetch_news = True
            if should_fetch_news:
                try:
                    self._fetch_all_news()
                except Exception:
                    traceback.print_exc()
            self._compute_movers()
            self._compute_sectors()
            self._last_update = datetime.now(IST).strftime("%H:%M:%S")
        finally:
            self._ensure_ready_lock.release()

    def get_dashboard(self) -> dict:
        global_rows = [self._decorate_global_market_row(row) for row in self._global_futures]
        global_rows = [row for row in global_rows if row]
        return {
            **self._overview_payload(),
            "indices": self._indices,
            "stocks": list(self._stocks.values()),
            "movers": self._movers,
            "news": self._news,
            "sectors": self._sectors,
            "sector_map": SECTOR_MAP,
            "gift_nifty": self._gift_nifty,
            "global_futures": global_rows,
            "last_global_update": self._last_global_update,
            "global_streaming": self._global_stream_connected,
            "news_llm_pending": self._llm_stack_pending_count(),
            "news_llm_enabled": bool(NV_API_KEY),
        }

    def _cache_search_results(self, query: str, results: list):
        self._search_cache[query] = {
            "results": results,
            "expires": time.time() + self._search_cache_ttl,
        }
        self._search_cache.move_to_end(query)
        while len(self._search_cache) > self._search_cache_max:
            self._search_cache.popitem(last=False)

    def _search_nse_equities(self, query: str) -> list:
        q = (query or "").strip().upper()
        if not q:
            return []
        cached = self._search_cache.get(q)
        if cached and cached.get("expires", 0) > time.time():
            self._search_cache.move_to_end(q)
            return cached.get("results", [])

        catalog = self._load_equity_catalog()
        results = []
        for item in catalog:
            symbol = item["symbol"]
            name = item["name"]
            if q not in symbol and q not in name.upper():
                continue
            results.append({
                "symbol": symbol,
                "name": name,
                "sector": SECTOR_MAP.get(symbol, "Other"),
            })
            if len(results) >= 24:
                break
        self._cache_search_results(q, results)
        return results

    def _load_equity_catalog(self) -> List[dict]:
        now = time.time()
        if self._equity_catalog and now - self._equity_catalog_loaded_at < self._equity_catalog_ttl:
            return self._equity_catalog

        catalog = []
        for url in NSE_EQUITY_LIST_URLS:
            try:
                r = requests.get(url, headers={"User-Agent": random.choice(_USER_AGENTS)}, timeout=20)
                if r.status_code != 200 or not r.text.strip():
                    continue
                rows = list(csv.DictReader(io.StringIO(r.text)))
                for row in rows:
                    symbol = (row.get("SYMBOL") or "").strip().upper()
                    name = (row.get("NAME OF COMPANY") or "").strip()
                    series = (row.get(" SERIES") or row.get("SERIES") or "").strip().upper()
                    if not symbol or not name or (series and series not in {"EQ", "T0"}):
                        continue
                    catalog.append({"symbol": symbol, "name": name})
                if catalog:
                    break
            except Exception as e:
                print(f"[Search] Equity catalog fetch error: {e}")

        if catalog:
            self._equity_catalog = catalog
            self._equity_catalog_loaded_at = now
        return self._equity_catalog

    def _build_stock_payload(
        self,
        symbol: str,
        name: str,
        sector: str,
        price: float,
        change: float,
        change_pct: float,
        open_price: float,
        high: float,
        low: float,
        volume: int,
        prev_close: float,
        year_high: float,
        year_low: float,
        source: str = "",
    ) -> dict:
        return {
            "symbol": symbol,
            "name": name or symbol,
            "sector": sector or SECTOR_MAP.get(symbol, "Other"),
            "price": round(price, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "open": round(open_price, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "volume": int(volume),
            "prev_close": round(prev_close, 2),
            "year_high": round(year_high, 2),
            "year_low": round(year_low, 2),
            "source": source,
        }

    def _fetch_nse_equity_quote(self, symbol: str) -> Optional[dict]:
        data = self._nse.get(NSE_QUOTE_EQUITY_URL.format(symbol=quote(symbol)))
        if not data or not isinstance(data, dict):
            return None
        info = data.get("info") or {}
        price_info = data.get("priceInfo") or {}
        security_info = data.get("securityInfo") or {}
        name = info.get("companyName") or YF_COMPANY_NAMES.get(symbol, symbol)
        sector = info.get("industry") or security_info.get("industry") or SECTOR_MAP.get(symbol, "Other")
        price = self._to_float(price_info.get("lastPrice"))
        prev_close = self._to_float(price_info.get("previousClose"), price)
        if not price:
            return None
        change = self._to_float(price_info.get("change"), price - prev_close if prev_close else 0)
        pct = self._to_float(price_info.get("pChange"), (change / prev_close * 100) if prev_close else 0)
        open_price = self._to_float(price_info.get("open"), price)
        intraday = price_info.get("intraDayHighLow") or {}
        week = price_info.get("weekHighLow") or {}
        high = self._to_float(intraday.get("max"), price)
        low = self._to_float(intraday.get("min"), price)
        year_high = self._to_float(week.get("max"), high)
        year_low = self._to_float(week.get("min"), low)
        volume = self._to_float((data.get("securityWiseDP") or {}).get("quantityTraded"), 0)
        if not volume:
            volume = self._to_float((data.get("marketDeptOrderBook") or {}).get("tradeInfo", {}).get("totalTradedVolume"), 0)
        return self._build_stock_payload(
            symbol=symbol,
            name=name,
            sector=sector,
            price=price,
            change=change,
            change_pct=pct,
            open_price=open_price,
            high=high,
            low=low,
            volume=int(volume),
            prev_close=prev_close,
            year_high=year_high,
            year_low=year_low,
            source="nse",
        )

    def _fetch_twelve_equity_quote(self, symbol: str) -> Optional[dict]:
        """NSE equity quote via Twelve Data when NSE API and Yahoo fail or omit rows."""
        if not TWELVE_DATA_API_KEY:
            return None
        sym = symbol.upper().strip()
        tdq = self._twelve_data_quote(sym, "NSE")
        if not tdq or not tdq.get("price"):
            return None
        p = tdq["price"]
        prev = tdq.get("prev") or 0.0
        if not prev:
            prev = p
        chg = float(tdq["chg"])
        pct = float(tdq["pct"])
        if not chg and prev:
            chg = p - prev
        if not pct and prev:
            pct = (chg / prev) * 100
        name = (tdq.get("name") or "").strip() or YF_COMPANY_NAMES.get(sym, sym)
        sector = SECTOR_MAP.get(sym, "Other")
        catalog_match = next(
            (item for item in self._load_equity_catalog() if item["symbol"] == sym),
            None,
        )
        if catalog_match and name == sym:
            name = catalog_match["name"]
        open_p = tdq.get("open") or p
        high = tdq.get("high") or p
        low = tdq.get("low") or p
        vol = int(tdq.get("volume") or 0)
        return self._build_stock_payload(
            symbol=sym,
            name=name,
            sector=sector,
            price=p,
            change=chg,
            change_pct=pct,
            open_price=open_p,
            high=high,
            low=low,
            volume=vol,
            prev_close=prev,
            year_high=high,
            year_low=low,
            source="twelve",
        )

    def _fetch_yf_equity_quote(self, symbol: str) -> Optional[dict]:
        ticker = yf.Ticker(f"{symbol}.NS")
        price = prev_close = open_price = high = low = 0.0
        volume = 0
        inf: dict = {}
        try:
            fi = ticker.fast_info
            price = self._to_float(getattr(fi, "last_price", None) or fi.get("lastPrice"), 0.0)
            prev_close = self._to_float(getattr(fi, "previous_close", None) or fi.get("previousClose"), 0.0)
            open_price = self._to_float(getattr(fi, "open", None) or fi.get("open"), price)
            high = self._to_float(getattr(fi, "day_high", None) or fi.get("dayHigh"), price)
            low = self._to_float(getattr(fi, "day_low", None) or fi.get("dayLow"), price)
            volume = int(self._to_float(getattr(fi, "last_volume", None) or fi.get("lastVolume"), 0))
        except Exception:
            pass

        if not price or not prev_close:
            try:
                raw = ticker.info
                if isinstance(raw, dict):
                    inf = raw
            except Exception:
                pass
        if not price and inf:
            price = self._to_float(inf.get("regularMarketPrice") or inf.get("currentPrice"), 0.0)
        if not prev_close and inf:
            prev_close = self._to_float(inf.get("regularMarketPreviousClose") or inf.get("previousClose"), 0.0)
        if not open_price and inf:
            open_price = self._to_float(inf.get("regularMarketOpen") or inf.get("open"), price)
        if not high and inf:
            high = self._to_float(inf.get("regularMarketDayHigh") or inf.get("dayHigh"), price)
        if not low and inf:
            low = self._to_float(inf.get("regularMarketDayLow") or inf.get("dayLow"), price)
        if not volume and inf:
            volume = int(self._to_float(inf.get("regularMarketVolume") or inf.get("volume"), 0))

        if not price:
            snap = self._fetch_yahoo_chart_snapshot(f"{symbol}.NS")
            if snap:
                price = self._to_float(snap.get("price"), 0.0)
                prev_close = prev_close or self._to_float(snap.get("prev_close"), 0.0)

        # Worst-case fallback: daily close-to-close (less accurate intraday).
        if not price:
            try:
                hist = ticker.history(period="5d", interval="1d")
                closes = hist["Close"].dropna() if "Close" in hist else []
                if len(closes) == 0:
                    return None
                row = hist.loc[closes.index[-1]]
                price = self._to_float(row.get("Close"), 0.0)
                prev_close = self._to_float(closes.iloc[-2] if len(closes) > 1 else price, price)
                open_price = self._to_float(row.get("Open"), price)
                high = self._to_float(row.get("High"), price)
                low = self._to_float(row.get("Low"), price)
                volume = int(self._to_float(row.get("Volume"), 0))
            except Exception as e:
                print(f"[YF Quote] {symbol}: {e}")
                return None

        if not prev_close:
            prev_close = price
        change = price - prev_close
        pct = (change / prev_close * 100) if prev_close else 0.0
        name = YF_COMPANY_NAMES.get(symbol, symbol)
        sector = SECTOR_MAP.get(symbol, "")
        try:
            if isinstance(inf, dict):
                name = inf.get("longName") or inf.get("shortName") or name
                if not sector:
                    sector = inf.get("sector") or inf.get("industry") or ""
        except Exception:
            pass
        if not sector:
            catalog_match = next(
                (item for item in self._load_equity_catalog() if item["symbol"] == symbol),
                None,
            )
            if catalog_match and name == symbol:
                name = catalog_match["name"]
        if not sector:
            sector = "Other"
        return self._build_stock_payload(
            symbol=symbol,
            name=name,
            sector=sector,
            price=price,
            change=change,
            change_pct=pct,
            open_price=open_price or price,
            high=high or price,
            low=low or price,
            volume=int(volume or 0),
            prev_close=prev_close,
            year_high=high or price,
            year_low=low or price,
            source="yahoo",
        )

    def is_known_equity(self, symbol: str) -> bool:
        """Fast check: valid NSE-style ticker we allow on the watchlist.

        Uses the equity catalog when loaded; if the CSV is unavailable (e.g. blocked CDN),
        falls back to the same ticker shape as the API/search so refresh/sync still work.
        """
        sym = symbol.upper()
        if sym in self._stocks or sym in SECTOR_MAP or sym in YF_COMPANY_NAMES:
            return True
        catalog = self._load_equity_catalog()
        if any(item["symbol"] == sym for item in catalog):
            return True
        return bool(re.fullmatch(r"[A-Z0-9&.-]{1,20}", sym))

    def get_stock(self, symbol: str) -> Optional[dict]:
        sym = symbol.upper()
        stock = self._stocks.get(sym)
        if stock:
            return stock
        return (
            self._fetch_nse_equity_quote(sym)
            or self._fetch_yf_equity_quote(sym)
            or self._fetch_twelve_equity_quote(sym)
        )

    def search(self, query: str) -> list:
        q = query.upper().strip()
        if not q:
            return []
        local = [
            s for s in self._stocks.values()
            if q in s["symbol"] or q in s.get("name", "").upper()
        ]
        results = []
        seen = set()
        for item in local + self._search_nse_equities(q):
            sym = item.get("symbol")
            if not sym or sym in seen:
                continue
            seen.add(sym)
            results.append(item)
            if len(results) >= 12:
                break
        if not results and re.fullmatch(r"[A-Z0-9&.-]{1,20}", q):
            stock = self._fetch_yf_equity_quote(q) or self._fetch_twelve_equity_quote(q)
            if stock:
                results.append({
                    "symbol": stock["symbol"],
                    "name": stock["name"],
                    "sector": stock["sector"],
                    "price": stock["price"],
                    "change_pct": stock["change_pct"],
                })
        return results

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
        is_index = symbol in OC_INDEX_SYMBOLS
        oc_type = "Indices" if is_index else "Equity"
        contract = self._nse.get(NSE_OC_CONTRACT_INFO_URL.format(symbol=quote(symbol)))
        contract_expiries = contract.get("expiryDates", []) if isinstance(contract, dict) else []
        selected = expiry if expiry and expiry in contract_expiries else (contract_expiries[0] if contract_expiries else "")
        url = NSE_OC_V3_URL.format(type=quote(oc_type), symbol=quote(symbol))
        if selected:
            url += "&expiry=" + quote(selected, safe="")
        data = self._nse.get(url)
        if not data or "records" not in data:
            url_tpl = NSE_OC_INDEX_URL if is_index else NSE_OC_EQUITY_URL
            data = self._nse.get(url_tpl.format(symbol=quote(symbol)))
        if not data or "records" not in data:
            return {"error": "No data — market may be closed", "strikes": [], "expiries": []}

        records = data["records"]
        all_expiries = records.get("expiryDates", []) or contract_expiries
        spot = records.get("underlyingValue", 0)
        timestamp = records.get("timestamp", "")

        selected = selected if selected in all_expiries else (expiry if expiry and expiry in all_expiries else (all_expiries[0] if all_expiries else ""))

        total_ce_oi = 0
        total_pe_oi = 0
        max_oi = 1
        strikes = []

        for row in records.get("data", []):
            row_expiry = row.get("expiryDate") or row.get("expiryDates") or ""
            if selected and row_expiry and row_expiry != selected:
                ce_expiry = (row.get("CE") or {}).get("expiryDate", "")
                pe_expiry = (row.get("PE") or {}).get("expiryDate", "")
                if selected not in {ce_expiry, pe_expiry}:
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

            strikes.append({"strike": strike, "ce": ce, "pe": pe})

        pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else 0
        pain_map: Dict[float, float] = {}
        for candidate in [float(s["strike"]) for s in strikes if s.get("strike")]:
            total_pain = 0.0
            for row in strikes:
                strike = float(row.get("strike") or 0)
                ce_oi = float((row.get("ce") or {}).get("oi") or 0)
                pe_oi = float((row.get("pe") or {}).get("oi") or 0)
                total_pain += max(0.0, candidate - strike) * ce_oi
                total_pain += max(0.0, strike - candidate) * pe_oi
            pain_map[candidate] = total_pain
        max_pain = min(pain_map, key=pain_map.get) if pain_map else 0

        atm_strike = 0
        if spot and strikes:
            atm_strike = min(strikes, key=lambda s: abs(s["strike"] - spot))["strike"]
        strike_values = sorted({float(s["strike"]) for s in strikes if s.get("strike")})
        strike_step = 0
        if len(strike_values) > 1:
            diffs = [
                round(strike_values[i] - strike_values[i - 1], 4)
                for i in range(1, len(strike_values))
                if strike_values[i] > strike_values[i - 1]
            ]
            strike_step = min(diffs) if diffs else 0

        return {
            "symbol": symbol,
            "spot": spot,
            "lot_size": OC_INDEX_LOT_SIZES.get(symbol, 0),
            "strike_step": strike_step,
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
            payload = json.dumps({
                "type": "news",
                "news": self._news,
                "news_llm_pending": self._llm_stack_pending_count(),
                "news_llm_enabled": bool(NV_API_KEY),
            })
        elif msg_type == "llm_queue":
            payload = json.dumps({
                "type": "llm_queue",
                "news_llm_pending": self._llm_stack_pending_count(),
                "news_llm_enabled": bool(NV_API_KEY),
            })
        elif msg_type == "global_tick":
            payload = json.dumps({
                "type": "global_tick",
                "global_futures": self._global_futures,
                "gift_nifty": self._gift_nifty,
                "last_global_update": self._last_global_update,
                "global_streaming": self._global_stream_connected,
            })
        else:
            payload = json.dumps({"type": "update", "data": self.get_dashboard()})
        dead = set()
        # Iterate over a snapshot so connect/disconnect during a broadcast
        # does not raise "Set changed size during iteration".
        for ws in tuple(self._ws_clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead
