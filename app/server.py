"""FastAPI server — serves the terminal UI and cache-first market APIs."""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.gzip import GZipMiddleware

from .data_engine import DataEngine, IST, SECTOR_MAP
from .mutual_fund_store import MutualFundWatchlistStore
from .mutual_funds import MutualFundsService
from .panel_cache import PanelCacheManager
from .watchlist_store import WatchlistStore

STATIC = Path(__file__).parent / "static"
_WATCHLIST_SYMBOL_RE = re.compile(r"^[A-Z0-9&.-]{1,20}$")
_MF_SCHEME_CODE_RE = re.compile(r"^[0-9]{1,20}$")

PANEL_OVERVIEW_KEY = "panel:overview"
PANEL_GLOBAL_KEY = "panel:global"
PANEL_NEWS_ALL_KEY = "panel:news:all"
PANEL_NEWS_BREAKING_KEY = "panel:news:breaking"
PANEL_WATCHLIST_QUOTES_KEY = "panel:watchlist:quotes"

OVERVIEW_TTL = 60.0
OVERVIEW_STALE_TTL = 15 * 60.0
GLOBAL_TTL = 60.0
GLOBAL_STALE_TTL = 15 * 60.0
NEWS_TTL = 60.0
NEWS_STALE_TTL = 30 * 60.0
WATCHLIST_QUOTES_TTL = 60.0
WATCHLIST_QUOTES_STALE_TTL = 10 * 60.0
WATCHLIST_NEWS_TTL = 120.0
WATCHLIST_NEWS_STALE_TTL = 12 * 60 * 60.0

engine = DataEngine()
watchlist_store = WatchlistStore()
mutual_fund_store = MutualFundWatchlistStore()
panel_cache = PanelCacheManager(engine._dashboard_store)
mutual_funds = MutualFundsService(engine._dashboard_store, mutual_fund_store)


class WatchlistPayload(BaseModel):
    symbols: list[str] = Field(default_factory=list)


def _now_time_str() -> str:
    return datetime.now(IST).strftime("%H:%M:%S")


def _normalize_symbol(symbol: str) -> str:
    sym = (symbol or "").strip().upper()
    if not sym or not _WATCHLIST_SYMBOL_RE.fullmatch(sym):
        raise HTTPException(status_code=400, detail="Invalid symbol")
    return sym


def _normalize_scheme_code(scheme_code: str) -> str:
    code = (scheme_code or "").strip()
    if not code or not _MF_SCHEME_CODE_RE.fullmatch(code):
        raise HTTPException(status_code=400, detail="Invalid scheme code")
    return code


def _known_symbol(symbol: str) -> bool:
    return engine.is_known_equity(symbol)


def _normalize_symbols(symbols: list[str]) -> list[str]:
    seen = set()
    valid = []
    for raw in symbols:
        try:
            sym = _normalize_symbol(raw)
        except HTTPException:
            continue
        if sym in seen or not _known_symbol(sym):
            continue
        seen.add(sym)
        valid.append(sym)
    return valid


def _watchlist_response() -> dict:
    return {
        "symbols": watchlist_store.list_symbols(),
        "storage": watchlist_store.storage_mode,
        "durable": watchlist_store.durable,
        "initialized": watchlist_store.initialized,
    }


def _watchlist_news_key(symbols: list[str]) -> str:
    return f"panel:news:watchlist:{engine.watchlist_hash(symbols)}"


def _empty_overview_payload() -> dict:
    return {
        "indices": [],
        "movers": {"gainers": [], "losers": []},
        "sectors": [],
        "gift_nifty": None,
        "market_status": engine.market_status,
        "last_update": None,
        "time": _now_time_str(),
        "breadth": {"advances": 0, "declines": 0},
    }


def _empty_global_payload() -> dict:
    return {
        "global_futures": [],
        "last_global_update": None,
        "global_streaming": False,
    }


def _empty_news_payload() -> dict:
    meta = engine.get_dashboard()
    return {
        "items": [],
        "news_llm_pending": meta.get("news_llm_pending", 0),
        "news_llm_enabled": meta.get("news_llm_enabled", False),
    }


def _empty_watchlist_quotes_payload() -> dict:
    return {"symbols": watchlist_store.list_symbols(), "rows": []}


async def _invalidate_watchlist_panels(before_symbols: list[str], after_symbols: list[str]):
    keys = {
        PANEL_WATCHLIST_QUOTES_KEY,
        _watchlist_news_key(before_symbols),
        _watchlist_news_key(after_symbols),
    }
    for key in keys:
        await panel_cache.delete(key)


async def _bootstrap_panel_as_ofs() -> dict:
    symbols = watchlist_store.list_symbols()
    entries = await asyncio.gather(
        panel_cache.peek(PANEL_OVERVIEW_KEY),
        panel_cache.peek(PANEL_NEWS_ALL_KEY),
        panel_cache.peek(PANEL_NEWS_BREAKING_KEY),
        panel_cache.peek(PANEL_GLOBAL_KEY),
        panel_cache.peek(PANEL_WATCHLIST_QUOTES_KEY),
        panel_cache.peek(_watchlist_news_key(symbols)),
    )
    return {
        "overview": entries[0]["as_of"] if entries[0] else None,
        "news_all": entries[1]["as_of"] if entries[1] else None,
        "news_breaking": entries[2]["as_of"] if entries[2] else None,
        "global": entries[3]["as_of"] if entries[3] else None,
        "watchlist_quotes": entries[4]["as_of"] if entries[4] else None,
        "watchlist_news": entries[5]["as_of"] if entries[5] else None,
    }


def _compose_cached_dashboard(
    overview_entry: dict | None,
    news_entry: dict | None,
    global_entry: dict | None,
) -> dict:
    data = engine.get_dashboard()
    if overview_entry:
        data.update(overview_entry.get("payload") or {})
    if news_entry:
        data["news"] = (news_entry.get("payload") or {}).get("items", [])
        data["news_llm_pending"] = (news_entry.get("payload") or {}).get(
            "news_llm_pending",
            data.get("news_llm_pending", 0),
        )
        data["news_llm_enabled"] = (news_entry.get("payload") or {}).get(
            "news_llm_enabled",
            data.get("news_llm_enabled", False),
        )
    if global_entry:
        payload = global_entry.get("payload") or {}
        cached_rows = payload.get("global_futures", [])
        data["global_futures"] = [
            row
            for row in (engine._decorate_global_market_row(item) for item in cached_rows)
            if row
        ] if cached_rows else data.get("global_futures", [])
        data["last_global_update"] = payload.get("last_global_update")
        data["global_streaming"] = payload.get("global_streaming", False)
    return data


@asynccontextmanager
async def lifespan(app: FastAPI):
    await engine.start()
    yield
    await engine.stop()


app = FastAPI(title="India Market Terminal", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=800)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ── Pages ───────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.head("/")
async def index_head():
    return JSONResponse({})


# ── REST API ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


@app.head("/health")
async def health_head():
    return JSONResponse({})


@app.get("/api/bootstrap")
async def bootstrap():
    cached = await _bootstrap_panel_as_ofs()
    meta = engine.get_dashboard()
    return JSONResponse(
        {
            "market_status": meta.get("market_status", engine.market_status),
            "last_update": meta.get("last_update"),
            "time": _now_time_str(),
            "news_llm_enabled": meta.get("news_llm_enabled", False),
            "news_llm_pending": meta.get("news_llm_pending", 0),
            "panels": cached,
        }
    )


@app.get("/api/panel/overview")
async def panel_overview():
    data = await panel_cache.get_or_refresh(
        PANEL_OVERVIEW_KEY,
        OVERVIEW_TTL,
        OVERVIEW_STALE_TTL,
        engine.build_overview_panel,
        fallback_payload=_empty_overview_payload,
    )
    return JSONResponse(data)


@app.get("/api/panel/global")
async def panel_global():
    data = await panel_cache.get_or_refresh(
        PANEL_GLOBAL_KEY,
        GLOBAL_TTL,
        GLOBAL_STALE_TTL,
        engine.build_global_panel,
        fallback_payload=_empty_global_payload,
        wait_on_miss=False,
    )
    return JSONResponse(data)


@app.get("/api/panel/news")
async def panel_news(tab: str = Query("all")):
    normalized = (tab or "all").strip().lower()
    if normalized not in {"all", "breaking", "watchlist"}:
        raise HTTPException(status_code=400, detail="Unsupported news tab")
    if normalized == "watchlist":
        symbols = watchlist_store.list_symbols()
        data = await panel_cache.get_or_refresh(
            _watchlist_news_key(symbols),
            WATCHLIST_NEWS_TTL,
            WATCHLIST_NEWS_STALE_TTL,
            lambda: engine.build_news_panel("watchlist", symbols),
            fallback_payload=lambda: {
                **_empty_news_payload(),
                "watchlist_hash": engine.watchlist_hash(symbols),
            },
            is_empty=lambda payload: not (payload.get("items") or []),
        )
        return JSONResponse(data)
    key = PANEL_NEWS_ALL_KEY if normalized == "all" else PANEL_NEWS_BREAKING_KEY
    data = await panel_cache.get_or_refresh(
        key,
        NEWS_TTL,
        NEWS_STALE_TTL,
        lambda: engine.build_news_panel(normalized),
        fallback_payload=_empty_news_payload,
    )
    return JSONResponse(data)


@app.get("/api/watchlist/quotes")
async def watchlist_quotes():
    symbols = watchlist_store.list_symbols()
    data = await panel_cache.get_or_refresh(
        PANEL_WATCHLIST_QUOTES_KEY,
        WATCHLIST_QUOTES_TTL,
        WATCHLIST_QUOTES_STALE_TTL,
        lambda: engine.build_watchlist_quotes_panel(symbols),
        fallback_payload=_empty_watchlist_quotes_payload,
    )
    return JSONResponse(data)


@app.get("/api/dashboard")
async def dashboard():
    overview_entry, news_entry, global_entry = await asyncio.gather(
        panel_cache.peek(PANEL_OVERVIEW_KEY),
        panel_cache.peek(PANEL_NEWS_ALL_KEY),
        panel_cache.peek(PANEL_GLOBAL_KEY),
    )
    return JSONResponse(_compose_cached_dashboard(overview_entry, news_entry, global_entry))


@app.get("/api/prewarm")
async def prewarm():
    cached = await _bootstrap_panel_as_ofs()
    return JSONResponse(
        {
            "status": "ok",
            "background_enabled": engine.background_enabled,
            "cached_panels": cached,
        }
    )


@app.get("/api/stock/{symbol}")
async def stock_detail(symbol: str):
    data = engine.get_stock(symbol)
    if not data:
        return JSONResponse({"error": "Stock not found"}, status_code=404)
    return JSONResponse(data)


@app.get("/api/chart/{symbol}")
async def chart(symbol: str, period: str = "1d", interval: str = "5m"):
    data = await engine.get_chart(symbol, period, interval)
    return JSONResponse(data)


@app.get("/api/search")
async def search(q: str = Query("")):
    return JSONResponse(engine.search(q))


@app.get("/api/watchlist")
async def get_watchlist():
    return JSONResponse(_watchlist_response())


@app.post("/api/watchlist/sync")
async def sync_watchlist(payload: WatchlistPayload):
    before_symbols = watchlist_store.list_symbols()
    symbols = _normalize_symbols(payload.symbols)
    try:
        await asyncio.to_thread(watchlist_store.merge_symbols, symbols)
    except Exception as exc:
        print(f"[Watchlist] merge_symbols failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    after_symbols = watchlist_store.list_symbols()
    await _invalidate_watchlist_panels(before_symbols, after_symbols)
    return JSONResponse(_watchlist_response())


@app.put("/api/watchlist/{symbol}")
async def add_watchlist_symbol(symbol: str):
    before_symbols = watchlist_store.list_symbols()
    sym = _normalize_symbol(symbol)
    if not _known_symbol(sym):
        raise HTTPException(status_code=404, detail="Unknown symbol")
    try:
        await asyncio.to_thread(watchlist_store.add_symbol, sym)
    except Exception as exc:
        print(f"[Watchlist] add_symbol failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    after_symbols = watchlist_store.list_symbols()
    await _invalidate_watchlist_panels(before_symbols, after_symbols)
    return JSONResponse(_watchlist_response())


@app.delete("/api/watchlist/{symbol}")
async def delete_watchlist_symbol(symbol: str):
    before_symbols = watchlist_store.list_symbols()
    sym = _normalize_symbol(symbol)
    try:
        await asyncio.to_thread(watchlist_store.remove_symbol, sym)
    except Exception as exc:
        print(f"[Watchlist] remove_symbol failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    after_symbols = watchlist_store.list_symbols()
    await _invalidate_watchlist_panels(before_symbols, after_symbols)
    return JSONResponse(_watchlist_response())


@app.get("/api/mf/search")
async def mf_search(q: str = Query("")):
    return JSONResponse(await asyncio.to_thread(mutual_funds.search, q))


@app.get("/api/mf/watchlist")
async def mf_watchlist():
    return JSONResponse(await asyncio.to_thread(mutual_funds.list_watchlist))


@app.put("/api/mf/watchlist/{scheme_code}")
async def add_mf_watchlist_item(scheme_code: str):
    code = _normalize_scheme_code(scheme_code)
    try:
        data = await asyncio.to_thread(mutual_funds.add, code)
    except Exception as exc:
        print(f"[MutualFunds] add failed: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))
    return JSONResponse(data)


@app.delete("/api/mf/watchlist/{scheme_code}")
async def delete_mf_watchlist_item(scheme_code: str):
    code = _normalize_scheme_code(scheme_code)
    try:
        data = await asyncio.to_thread(mutual_funds.remove, code)
    except Exception as exc:
        print(f"[MutualFunds] delete failed: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))
    return JSONResponse(data)


@app.get("/api/mf/compare/{scheme_code}")
async def mf_compare(scheme_code: str, benchmark: str = Query(""), range_key: str = Query("max", alias="range")):
    code = _normalize_scheme_code(scheme_code)
    try:
        data = await asyncio.to_thread(mutual_funds.compare, code, benchmark or None, range_key)
    except Exception as exc:
        print(f"[MutualFunds] compare failed: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))
    return JSONResponse(data)


@app.get("/api/options/{symbol}")
async def options(symbol: str, expiry: str = Query("")):
    data = await asyncio.to_thread(engine.get_option_chain, symbol, expiry or None)
    return JSONResponse(data)


# ── WebSocket ───────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    engine.register(ws)
    try:
        import json

        await ws.send_text(json.dumps({"type": "update", "data": engine.get_dashboard()}))
        while True:
            msg = await ws.receive_text()
            if msg.startswith("stock:"):
                sym = msg.split(":")[1].strip().upper()
                detail = engine.get_stock(sym)
                if detail:
                    await ws.send_text(json.dumps({"type": "stock", "data": detail}))
    except WebSocketDisconnect:
        pass
    finally:
        engine.unregister(ws)
