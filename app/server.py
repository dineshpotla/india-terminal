"""FastAPI server — serves the terminal UI and exposes market data APIs."""

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.gzip import GZipMiddleware

from .data_engine import DataEngine, SECTOR_MAP, YF_COMPANY_NAMES
from .watchlist_store import WatchlistStore

STATIC = Path(__file__).parent / "static"
_WATCHLIST_SYMBOL_RE = re.compile(r"^[A-Z0-9&.-]{1,20}$")

engine = DataEngine()
watchlist_store = WatchlistStore()


class WatchlistPayload(BaseModel):
    symbols: list[str] = Field(default_factory=list)


def _normalize_symbol(symbol: str) -> str:
    sym = (symbol or "").strip().upper()
    if not sym or not _WATCHLIST_SYMBOL_RE.fullmatch(sym):
        raise HTTPException(status_code=400, detail="Invalid symbol")
    return sym


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


def _refresh_watchlist_symbol(symbol: str):
    fetcher = getattr(engine, "fetch_watchlist_stock_news", None)
    if not callable(fetcher):
        return
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            asyncio.create_task(fetcher(symbol))
    except RuntimeError:
        pass


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


@app.get("/api/dashboard")
async def dashboard():
    try:
        # Render can cold-start into slow upstream providers. Return the best
        # cached snapshot we have instead of hanging the whole dashboard route.
        await asyncio.wait_for(asyncio.to_thread(engine.ensure_data_ready), timeout=8)
    except asyncio.TimeoutError:
        print("[Dashboard] ensure_data_ready timed out; serving cached snapshot")
    except Exception as exc:
        print(f"[Dashboard] ensure_data_ready failed: {exc}")
    return JSONResponse(engine.get_dashboard())


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
    symbols = _normalize_symbols(payload.symbols)
    try:
        await asyncio.to_thread(watchlist_store.merge_symbols, symbols)
    except Exception as exc:
        print(f"[Watchlist] merge_symbols failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    for sym in symbols:
        _refresh_watchlist_symbol(sym)
    return JSONResponse(_watchlist_response())


@app.put("/api/watchlist/{symbol}")
async def add_watchlist_symbol(symbol: str):
    sym = _normalize_symbol(symbol)
    if not _known_symbol(sym):
        raise HTTPException(status_code=404, detail="Unknown symbol")
    try:
        await asyncio.to_thread(watchlist_store.add_symbol, sym)
    except Exception as exc:
        print(f"[Watchlist] add_symbol failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    _refresh_watchlist_symbol(sym)
    return JSONResponse(_watchlist_response())


@app.delete("/api/watchlist/{symbol}")
async def delete_watchlist_symbol(symbol: str):
    sym = _normalize_symbol(symbol)
    try:
        await asyncio.to_thread(watchlist_store.remove_symbol, sym)
    except Exception as exc:
        print(f"[Watchlist] remove_symbol failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    return JSONResponse(_watchlist_response())


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
        # send current snapshot immediately
        import json
        await ws.send_text(json.dumps({"type": "update", "data": engine.get_dashboard()}))
        while True:
            # keep connection alive; client can send commands here
            msg = await ws.receive_text()
            if msg.startswith("stock:"):
                sym = msg.split(":")[1].strip().upper()
                detail = engine.get_stock(sym)
                if detail:
                    await ws.send_text(json.dumps({"type": "stock", "data": detail}))
            elif msg.startswith("watchlist:"):
                sym = msg.split(":")[1].strip().upper()
                _refresh_watchlist_symbol(sym)
    except WebSocketDisconnect:
        pass
    finally:
        engine.unregister(ws)
