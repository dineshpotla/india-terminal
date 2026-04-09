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
from datetime import datetime, timedelta
import threading
from typing import Dict, List, Optional, Set
from urllib.parse import quote, quote_plus, urljoin, urlparse

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
NSE_QUOTE_EQUITY_URL = NSE_BASE + "/api/quote-equity?symbol={symbol}"
NSE_EQUITY_LIST_URLS = (
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
)
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

# Global markets: prefer Yahoo "ROOT=F" futures where available. Those tickers track the
# **front contract**; Yahoo retargets them at rollover, so we do not maintain a local expiry calendar.
#
# Futures availability (Yahoo): CME/ICE roots (ES, NQ, CL, 6E, BTC=F, …) work. Probed chart/=F
# symbols for FTSE/DAX/CAC/Euro Stoxx/Hang Seng/KOSPI/Taiwan/etc. futures return 404 — contracts
# exist on exchanges but Yahoo does not list them as continuous =F. For those rows we keep Yahoo
# **cash indices** and optionally fall back to Twelve Data (TWELVE_DATA_API_KEY) if Yahoo returns
# no price. Twelve is also used for Indian index/ equity quotes when NSE or Yahoo omit data.
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

# Yahoo symbol -> Twelve Data `symbol`[, exchange] for REST fallback (spot index), same display scale.
_GLOBAL_TD_FALLBACK: Dict[str, tuple] = {
    "^FTSE": ("FTSE", ""),
    "^GDAXI": ("DAX", ""),
    "^FCHI": ("FCHI", ""),
    "^STOXX50E": ("STOXX50E", ""),
    "^HSI": ("HSI", ""),
    "000001.SS": ("000001", "SSE"),
    "^KS11": ("KS11", ""),
    "^TWII": ("TWII", ""),
    "^STI": ("STI", ""),
    "^SET.BK": ("SET", "SET"),
    "^JKSE": ("JKSE", ""),
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
    "https://feeds.bbci.co.uk/news/world/rss.xml",
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
    "https://news.google.com/rss/search?q=iran+ceasefire+OR+pakistan+iran+OR+%22ceasefire+talks%22+OR+%22peace+talks%22+iran+OR+%22middle+east+ceasefire%22+when:6h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=ceasefire+OR+truce+OR+negotiations+iran+when:6h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=S%26P+500+OR+Nasdaq+OR+Dow+Jones+when:6h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=global+selloff+OR+risk-off+OR+credit+spreads+OR+treasury+auction+when:6h&hl=en&gl=US&ceid=US:en",

    # Reuters (broad site coverage; avoids direct reuters.com feeds that datacenters often block)
    "https://news.google.com/rss/search?q=site:reuters.com+when:1h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:reuters.com+when:6h&hl=en&gl=US&ceid=US:en",
]
NEWS_FEEDS_GOLD_SILVER = [
    # ── Direct RSS (institutional + mining / silver industry) ─────────
    # World Gold Council (demand trends, industry research, policy)
    "https://www.gold.org/rss.xml",
    # Silver Institute (supply/demand, market commentary)
    "https://www.silverinstitute.org/feed/",
    # Broad mining wire (filter with gold/silver keywords + LLM downstream)
    "https://www.mining.com/feed/",

    # ── India retail / exchange context ───────────────────────────────
    "https://news.google.com/rss/search?q=gold+price+OR+%22gold+rate%22+OR+%22gold+today%22+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=silver+price+OR+%22silver+rate%22+OR+%22silver+today%22+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=%22MCX+gold%22+OR+%22MCX+silver%22+OR+%22COMEX+gold%22+OR+%22COMEX+silver%22+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=%22precious+metals%22+OR+%22gold+jewellery%22+OR+%22gold+import%22+india+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=site:economictimes.indiatimes.com+gold+OR+silver+OR+bullion+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=site:moneycontrol.com+gold+OR+silver+OR+MCX+OR+bullion+when:1d&hl=en-IN&gl=IN&ceid=IN:en",

    # ── Global price / market structure ────────────────────────────────
    "https://news.google.com/rss/search?q=gold+price+OR+silver+price+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=XAUUSD+OR+XAGUSD+OR+%22spot+gold%22+OR+%22spot+silver%22+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+futures+OR+silver+futures+OR+%22COMEX+gold%22+OR+%22COMEX+silver%22+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+ETF+OR+GLD+OR+IAU+OR+SLV+OR+%22gld+etf%22+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+rally+OR+gold+crash+OR+gold+forecast+OR+silver+rally+OR+silver+forecast+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=LBMA+OR+%22london+bullion%22+OR+%22fixes+gold%22+OR+%22gold+fix%22+when:2d&hl=en&gl=UK&ceid=GB:en",
    "https://news.google.com/rss/search?q=Shanghai+Gold+Exchange+OR+SGE+gold+when:2d&hl=en&gl=US&ceid=US:en",

    # ── Official sector: reserves, PBOC, IMF, sovereign buying ─────────
    "https://news.google.com/rss/search?q=PBOC+gold+OR+%22People%27s+Bank+of+China%22+gold+OR+%22China+gold+reserves%22+OR+%22Chinese+central+bank%22+gold+when:3d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=central+bank+gold+OR+%22gold+reserves%22+OR+%22official+gold%22+OR+%22sovereign+gold%22+when:2d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=IMF+gold+OR+%22IMF+gold+sale%22+OR+%22IMF+gold+sales%22+OR+World+Bank+gold+when:3d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:imf.org+gold+when:14d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:worldbank.org+gold+OR+silver+OR+bullion+when:14d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Poland+gold+OR+Turkey+gold+OR+India+gold+reserves+OR+Russia+gold+reserves+when:3d&hl=en&gl=US&ceid=US:en",

    # ── Silver fundamentals / industry (beyond spot price) ────────────
    "https://news.google.com/rss/search?q=%22silver+supply%22+OR+%22silver+deficit%22+OR+%22silver+demand%22+OR+photovoltaic+silver+when:3d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=%22World+Gold+Council%22+OR+%22gold+demand+trends%22+OR+wgc+gold+when:7d&hl=en&gl=US&ceid=US:en",

    # ── Tier-1 publishers (Google News site filters) ────────────────
    "https://news.google.com/rss/search?q=gold+OR+silver+site:reuters.com+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+OR+silver+site:bloomberg.com+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+OR+silver+site:cnbc.com+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+OR+silver+site:ft.com+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+OR+silver+site:marketwatch.com+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+OR+silver+site:barrons.com+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+OR+silver+site:investing.com+when:1d&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=gold+OR+silver+site:fxstreet.com+when:1d&hl=en&gl=US&ceid=US:en",
    # Kitco site search only — direct /news/*.rss URLs now serve HTML shells
    "https://news.google.com/rss/search?q=gold+OR+silver+site:kitco.com+when:1d&hl=en&gl=US&ceid=US:en",
]
NEWS_FEEDS = NEWS_FEEDS_INDIA + NEWS_FEEDS_GLOBAL + NEWS_FEEDS_GOLD_SILVER
_GLOBAL_FEEDS_SET = set(NEWS_FEEDS_GLOBAL + NEWS_FEEDS_GOLD_SILVER)
_GOLD_FEEDS_SET = set(NEWS_FEEDS_GOLD_SILVER)
_INDIA_FEEDS_SET = set(NEWS_FEEDS_INDIA)

TRADIENT_NEWS_URL = "https://api.tradient.org/v1/api/market/news"

# Live blog pages: polled on a short interval. URLs are never tied to one story — they come from
# LIVE_STORY_URLS (optional) plus automatic discovery (Google News RSS → publisher links that look
# like /live/ pages). Uses only URL shape + allowlisted domains, not topic keywords.
_LIVE_TITLE_BAD = (
    "subscribe", "newsletter", "cookie policy", "sign in", "follow us", "skip to",
    "privacy policy", "ad choices", "terms of use", "copyright",
)

# Topic-agnostic discovery: “live” page patterns on major wires / broadcasters.
LIVE_DISCOVERY_FEEDS = [
    "https://news.google.com/rss/search?q=site:reuters.com+(live+OR+%22live+updates%22)+when:6h&hl=en&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=site:bbc.co.uk+(news+live+OR+%2Flive%2F)+when:6h&hl=en&gl=UK&ceid=GB:en",
    "https://news.google.com/rss/search?q=site:theguardian.com+live+when:6h&hl=en&gl=UK&ceid=GB:en",
]

_LIVE_PATH_HINTS = (
    "/live/", "-live-", "live-updates", "liveblog", "live-blog", "as-it-happened",
)

_DEFAULT_LIVE_DISCOVERY_DOMAINS = (
    "reuters.com", "bbc.co.uk", "bbc.com", "theguardian.com",
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
        int(os.getenv("TWELVE_DATA_MAX_PER_MINUTE", "6" if _TWELVE_FREE else "55")),
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
NV_API_MODEL = os.getenv("NV_NEWS_MODEL", "moonshotai/kimi-k2.5")
NV_FAST_MODEL = "nvidia/llama-3.3-nemotron-super-49b-v1"
LLM_CACHE_SIZE = 500

_NIFTY50_SYMBOLS_STR = ", ".join(sorted(SECTOR_MAP.keys()))
_LLM_SYSTEM = (
    "You output ONLY a raw JSON array. No markdown, no explanation, no text before or after. "
    "Each element: "
    '{"idx":N,"stocks":[],"sentiment":"bullish"|"bearish"|"neutral",'
    '"impact":"high"|"medium"|"low","breaking":true|false,'
    '"gold_silver":true|false,"india_market_impact":true|false,'
    '"market_relevant":true|false,"company_specific":true|false}'
)
_LLM_PROMPT_PREFIX = (
    f"NIFTY 50 symbols: {_NIFTY50_SYMBOLS_STR}.\n"
    "Rules:\n"
    "- stocks: relevant NIFTY50 symbols, [] if none\n"
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

_INDIA_IMPACT_HINT_KW = {
    "india", "indian", "nifty", "sensex", "bse", "nse", "rupee", "inr",
    "rbi", "sebi ban", "sebi order", "sebi probe",
    "fii", "fpi", "dii", "msci india",
    "adani", "reliance", "tata", "infosys", "tcs", "hdfc",
    "oil price", "crude oil", "brent", "opec",
    "fed rate", "rate cut", "rate hike", "federal reserve",
    "tariff", "trade war", "sanction",
    "iran", "strait of hormuz", "war escalat",
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
        _gift_secs = int(os.getenv("GIFT_NIFTY_REFRESH_SECS", "15"))
        self._gift_refresh_secs = max(8, min(300, _gift_secs))
        _bcast_ms = int(os.getenv("GLOBAL_BROADCAST_MS", "1000"))
        self._global_broadcast_interval = max(200, min(10000, _bcast_ms)) / 1000.0
        # LLM classification stack (LIFO). New headlines push here; worker pops one at a time.
        self._llm_stack: List[dict] = []
        self._llm_pending: Set[str] = set()
        self._llm_lock = threading.Lock()
        self._search_cache: OrderedDict = OrderedDict()
        self._search_cache_ttl = 300
        self._search_cache_max = 64
        self._equity_catalog: List[dict] = []
        self._equity_catalog_loaded_at = 0.0
        self._equity_catalog_ttl = 12 * 60 * 60
        # Twelve Data rate limits (Basic plan: 8 credits/min); see TWELVE_DATA_* env vars.
        self._twelve_lock = threading.Lock()
        self._twelve_call_times: deque = deque()
        self._twelve_quote_cache: OrderedDict = OrderedDict()
        self._twelve_log_throttle = 0.0

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
        self._asyncio_loop = asyncio.get_running_loop()
        self._market_task = asyncio.create_task(self._market_loop())
        self._global_task = asyncio.create_task(self._global_stream_loop())
        self._gift_task = asyncio.create_task(self._gift_nifty_loop())
        self._news_task = asyncio.create_task(self._news_loop())
        self._llm_task = asyncio.create_task(self._llm_loop())
        self._live_task = asyncio.create_task(self._live_stories_loop())

    async def stop(self):
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

    def _mark_llm_done(self, cache_key: str):
        with self._llm_lock:
            self._llm_pending.discard(cache_key)

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
            # BREAKING is India-impact-only and never stock-event.
            brk = item.get("india_market_impact", False)
            if item.get("stock_event"):
                brk = False
            if item.get("company_specific"):
                brk = False
            item["breaking"] = brk

    async def _llm_loop(self):
        """Background LLM worker: pops headlines from stack and classifies one at a time."""
        try:
            while self._running:
                job = self._pop_llm_stack()
                if not job:
                    await asyncio.sleep(0.25)
                    continue
                await self._broadcast("llm_queue")
                cache_key = job["cache_key"]
                title = job["title"]
                try:
                    # Choose model based on current backlog (cold start => faster model).
                    with self._llm_lock:
                        backlog = len(self._llm_stack)
                    model = NV_FAST_MODEL if backlog > 10 else NV_API_MODEL

                    entry = await asyncio.to_thread(self._llm_call_one, title, model)
                    if not entry or not isinstance(entry, dict):
                        # Fail closed for breaking/India impact.
                        result = {
                            "stocks": [],
                            "sentiment": "neutral",
                            "impact": "low",
                            "breaking": False,
                            "gold_silver": False,
                            "india_market_impact": False,
                            "market_relevant": False,
                            "company_specific": True,
                        }
                    else:
                        valid_syms = set(SECTOR_MAP.keys())
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
                            # Backward compatibility with older cached/model outputs.
                            # If it can impact India or is precious-metals related, treat as market-relevant.
                            is_market_rel = bool(is_india or is_gs or entry.get("breaking", False))

                        if "company_specific" in entry:
                            is_comp = bool(entry.get("company_specific"))
                        else:
                            is_comp = bool(job.get("company_specific", False))
                        # BREAKING tab is India-impact-only (LLM-gated), and must not include company/stock-specific items.
                        is_brk = is_india
                        if job.get("stock_event") or is_comp or job.get("company_specific"):
                            is_brk = False
                        result = {
                            "stocks": stocks,
                            "sentiment": sentiment,
                            "impact": impact,
                            "breaking": is_brk,
                            "gold_silver": is_gs,
                            "india_market_impact": is_india,
                            "market_relevant": is_market_rel,
                            "company_specific": is_comp,
                        }

                    # Cache + apply to live news
                    self._llm_cache[cache_key] = result
                    while len(self._llm_cache) > LLM_CACHE_SIZE:
                        self._llm_cache.popitem(last=False)
                    self._apply_llm_result_to_news(cache_key, result)
                    await self._broadcast("news")
                except Exception:
                    traceback.print_exc()
                finally:
                    self._mark_llm_done(cache_key)
        except asyncio.CancelledError:
            return

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
            merged["GIFTNIFTY"] = {
                "name": "GIFT NIFTY",
                "symbol": "GIFTNIFTY",
                "region": "ASIAN MARKETS",
                "price": self._gift_nifty["price"],
                "change": self._gift_nifty["change"],
                "change_pct": self._gift_nifty["change_pct"],
            }
        results = list(merged.values())
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

        for q in queries:
            url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:15]:
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
                    wl_stocks = [symbol]
                    for s in tags["keyword_stocks"]:
                        if s not in wl_stocks:
                            wl_stocks.append(s)
                    age_secs = int((now - pub_dt).total_seconds()) if pub_dt else 999999
                    display_src = self._display_news_source(url, "Google News", title, entry)
                    combined_text = title.lower() + (" " + summary.lower() if summary else "")
                    india_hint = any(kw in combined_text for kw in _INDIA_IMPACT_HINT_KW)
                    india_news = any(kw in combined_text for kw in _INDIA_NEWS_KW)
                    results.append({
                        "title": title,
                        "link": entry.get("link", ""),
                        "source": display_src,
                        "age_secs": age_secs,
                        "time": self._relative_time(pub_dt) if pub_dt else "",
                        "is_fresh": age_secs < 900,
                        "global_news": False,
                        "india_news": india_news,
                        "breaking": False,
                        "breaking_hint": tags["breaking"],
                        "stock_event": tags["stock_event"],
                        "gold_silver": tags["gold_silver"],
                        "india_market_impact": india_hint,
                        "market_relevant": tags.get("market_relevant", False),
                        "company_specific": True,
                        "keyword_stocks": tags["keyword_stocks"],
                        "watchlist_stocks": wl_stocks,
                    })
            except Exception as e:
                print(f"[WL Search] {symbol} feed error: {e}")

        print(f"[WL Search] {symbol}: {len(results)} items from {len(queries)} queries")
        return results

    async def fetch_watchlist_stock_news(self, symbol: str):
        """Async entry point: search for stock news, merge into live feed, broadcast."""
        symbol = symbol.upper().strip()
        if not self.is_known_equity(symbol):
            return
        items = await asyncio.to_thread(self._search_stock_news, symbol)
        if not items:
            return
        with self._news_lock:
            existing_titles = {n["title"].lower()[:50] for n in self._news}
            new_items = [it for it in items if it["title"].lower()[:50] not in existing_titles]
            if new_items:
                self._news.extend(new_items)
                self._news.sort(key=lambda x: x.get("age_secs", 999999))
        if new_items:
            self._push_llm_stack(new_items)
            await self._broadcast("news")

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
                    await asyncio.to_thread(self._fetch_all_news)
                    elapsed = round(time.time() - t0, 1)
                    print(f"[News] {len(self._news)} headlines in {elapsed}s")
                    await self._broadcast("news")
                except Exception:
                    traceback.print_exc()
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            return

    async def _live_stories_loop(self):
        """Poll live pages + deep-read breaking articles. Discovery runs in background, never blocks polling."""
        try:
            interval = max(30, int(os.getenv("LIVE_STORY_POLL_SECS", "90")))
            discover_secs = max(120, int(os.getenv("LIVE_STORY_DISCOVER_SECS", "300")))
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

    def _discover_live_pages_from_rss(self):
        """Refresh candidate live URLs from generic RSS searches (any major breaking story, any topic)."""
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

    def _http_get_html(self, url: str) -> Optional[str]:
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
            r = requests.get(url, timeout=28, headers=headers, allow_redirects=True)
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
            r = requests.post(
                NV_API_URL,
                headers={"Authorization": f"Bearer {NV_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": NV_FAST_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    "temperature": 0.15,
                    "max_tokens": 1024,
                },
                timeout=30,
            )
            if r.status_code != 200:
                return []
            text = r.json()["choices"][0]["message"]["content"].strip()
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
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
        items_from_structure: List[dict] = []
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        if m:
            try:
                data = json.loads(m.group(1))
                items_from_structure = self._extract_live_candidates_from_json(data, set(), page_url)[:30]
            except json.JSONDecodeError:
                pass

        if not items_from_structure:
            soup = BeautifulSoup(html, "html.parser")
            seen: Set[str] = set()
            for h in soup.select('article h2, article h3, [data-testid="Heading"]'):
                t = h.get_text(" ", strip=True)
                if _live_title_ok(t) and t not in seen:
                    seen.add(t)
                    items_from_structure.append({"title": t, "link": page_url, "t_raw": None})

        body_text = self._extract_body_text(html)
        brand = _live_brand_from_url(page_url)
        llm_headlines = self._llm_extract_developments(body_text, brand)

        seen_titles: Set[str] = {it["title"].lower()[:50] for it in items_from_structure}
        for headline in llm_headlines:
            k = headline.lower()[:50]
            if k not in seen_titles:
                seen_titles.add(k)
                items_from_structure.append({"title": headline, "link": page_url, "t_raw": None})

        return items_from_structure[:40]

    def _live_digest(self, title: str, link: Optional[str]) -> str:
        basis = f"{title}\n{link or ''}"
        return hashlib.sha256(basis.encode("utf-8", errors="ignore")).hexdigest()[:28]

    def _live_post_to_news_item(self, title: str, link: str, page_url: str) -> dict:
        now = datetime.now(IST)
        tags = self._classify_news(title, "")
        combined = title.lower()
        india_hint = any(kw in combined for kw in _INDIA_IMPACT_HINT_KW)
        india_news = any(kw in combined for kw in _INDIA_NEWS_KW)
        brk_pre = india_hint and not tags.get("stock_event", False) and not tags.get("company_specific", False)
        return {
            "title": title,
            "link": link or page_url,
            "source": f"{_live_brand_from_url(page_url)} LIVE",
            "live_story": True,
            "age_secs": 0,
            "time": "just now",
            "is_fresh": True,
            "global_news": True,
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
        return {
            "name": label,
            "symbol": yahoo_sym,
            "region": region,
            "price": round(p, 2 if p >= 10 else 4),
            "change": round(c, 2 if abs(c) >= 1 else 4),
            "change_pct": round(float(tdq["pct"]), 2),
        }

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
        return {
            "name": label,
            "symbol": yahoo_sym,
            "region": region,
            "price": round(fp, 2 if fp >= 10 else 4),
            "change": round(fc, 2 if abs(fc) >= 1 else 4),
            "change_pct": round(float(chg_pct), 2),
        }

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
                try:
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
                results.append({
                    "name": "GIFT NIFTY",
                    "symbol": "GIFTNIFTY",
                    "region": "ASIAN MARKETS",
                    "price": gn["price"],
                    "change": gn["change"],
                    "change_pct": gn["change_pct"],
                })
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
            "keyword_stocks": matched,
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
                    item["watchlist_stocks"] = list(dict.fromkeys(cached["stocks"]))
                item["sentiment"] = cached["sentiment"]
                item["impact"] = cached["impact"]
                item["breaking"] = cached.get("breaking", item.get("breaking", False))
                item["gold_silver"] = cached.get("gold_silver", False) or item.get("gold_silver", False)
                item["india_market_impact"] = cached.get("india_market_impact", False)
                # BREAKING is India-impact-only.
                if not item["india_market_impact"]:
                    item["breaking"] = False
                if item.get("stock_event"):
                    item["breaking"] = False
            else:
                uncached.append(item)

        if not uncached:
            return

        model = NV_FAST_MODEL if len(uncached) > 10 else NV_API_MODEL
        ok = 0
        for item in uncached:
            entry = self._llm_call_one(item["title"], model)
            if not entry or not isinstance(entry, dict):
                # LLM must decide India impact for BREAKING; fail closed.
                item["india_market_impact"] = False
                item["breaking"] = False
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
            is_india_impact = bool(entry.get("india_market_impact", False))
            # BREAKING is strictly India-market-impacting only (LLM-gated).
            is_brk = is_brk and is_india_impact
            result = {
                "stocks": stocks,
                "sentiment": sentiment,
                "impact": impact,
                "breaking": is_brk,
                "gold_silver": is_gs,
                "india_market_impact": is_india_impact,
            }

            cache_key = item["title"][:80].lower()
            self._llm_cache[cache_key] = result
            while len(self._llm_cache) > LLM_CACHE_SIZE:
                self._llm_cache.popitem(last=False)

            if stocks:
                item["watchlist_stocks"] = list(dict.fromkeys(stocks))
            item["sentiment"] = sentiment
            item["impact"] = impact
            item["breaking"] = is_brk
            item["gold_silver"] = is_gs
            item["india_market_impact"] = is_india_impact
            ok += 1

        print(f"[LLM] {ok}/{len(uncached)} new via {model.split('/')[-1]}, "
              f"{len(items)-len(uncached)} cached")

    def _fetch_all_news(self):
        """Fetch RSS feeds + Tradient API, merge, sort by recency."""
        now = datetime.now(IST)
        cutoff = now - timedelta(hours=18)
        global_cutoff = now - timedelta(hours=6)
        gold_cutoff = now - timedelta(hours=24)
        raw: List[dict] = []

        for url in NEWS_FEEDS:
            try:
                feed = feedparser.parse(url)
                is_global_feed = url in _GLOBAL_FEEDS_SET
                is_gold_feed = url in _GOLD_FEEDS_SET
                is_india_feed = url in _INDIA_FEEDS_SET
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
                    display_src = self._display_news_source(url, source, title, entry)
                    combined_text = title.lower() + (" " + summary.lower() if summary else "")
                    india_hint = any(kw in combined_text for kw in _INDIA_IMPACT_HINT_KW)
                    india_news = is_india_feed or any(kw in combined_text for kw in _INDIA_NEWS_KW)
                    brk_pre = india_hint and not tags.get("stock_event", False) and not tags.get("company_specific", False)
                    raw.append({
                        "title": title,
                        "link": entry.get("link", ""),
                        "source": display_src,
                        "age_secs": age_secs,
                        "time": self._relative_time(pub_dt) if pub_dt else "",
                        "is_fresh": age_secs < 900,
                        "global_news": is_global_feed,
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
        with self._news_lock:
            prior_live = [
                n for n in self._news
                if n.get("live_story") and n.get("age_secs", 999999) < 12 * 3600
            ]
        if not unique and not prior_live:
            return
        # Reserve slots for gold/silver items so they don't get crowded out
        gold_items = [u for u in unique if u.get("gold_silver")]
        other_items = [u for u in unique if not u.get("gold_silver")]
        unique_trim = (gold_items[:20] + other_items)[:100]
        live_cap = min(len(prior_live), 30)
        capped_live = prior_live[:live_cap]
        combined: List[dict] = []
        seen_k: Set[str] = set()
        for item in capped_live + unique_trim:
            k = item["title"][:50].lower()
            if k in seen_k:
                continue
            seen_k.add(k)
            combined.append(item)
        with self._news_lock:
            self._news = combined[:130]
        if unique_trim:
            self._push_llm_stack(unique_trim)

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
                trad_india_hint = any(kw in trad_combined for kw in _INDIA_IMPACT_HINT_KW)
                trad_brk = trad_india_hint and not tags.get("stock_event", False) and not tags.get("company_specific", False)
                raw.append({
                    "title": title,
                    "link": link,
                    "source": source,
                    "age_secs": age_secs,
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

    def ensure_data_ready(self, force_quotes: bool = False):
        """Sync fetch for serverless request lifecycles.

        If `force_quotes` is True, always refresh indices/stocks from source so % changes
        reflect the latest snapshot instead of an in-memory cache.
        """
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
        if force_quotes or not self._global_futures:
            try:
                self._fetch_global_futures()
            except Exception:
                traceback.print_exc()
        if not self._news:
            try:
                self._fetch_all_news()
            except Exception:
                traceback.print_exc()
        self._compute_movers()
        self._compute_sectors()
        self._last_update = datetime.now(IST).strftime("%H:%M:%S")

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
            "sector_map": SECTOR_MAP,
            "gift_nifty": self._gift_nifty,
            "global_futures": self._global_futures,
            "last_global_update": self._last_global_update,
            "global_streaming": self._global_stream_connected,
            "market_status": self.market_status,
            "last_update": self._last_update,
            "time": datetime.now(IST).strftime("%H:%M:%S"),
            "breadth": {"advances": adv, "declines": dec},
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
