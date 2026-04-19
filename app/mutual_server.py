"""Dedicated FastAPI server for the mutual-fund site."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from .dashboard_store import DashboardStore
from .mutual_fund_store import MutualFundWatchlistStore
from .mutual_funds import MutualFundsService

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=False)

STATIC = Path(__file__).parent / "static"
_MF_SCHEME_CODE_RE = re.compile(r"^[0-9]{1,20}$")

snapshot_store = DashboardStore()
mutual_fund_store = MutualFundWatchlistStore()
mutual_funds = MutualFundsService(snapshot_store, mutual_fund_store)


def _normalize_scheme_code(scheme_code: str) -> str:
    code = (scheme_code or "").strip()
    if not code or not _MF_SCHEME_CODE_RE.fullmatch(code):
        raise HTTPException(status_code=400, detail="Invalid scheme code")
    return code


def _normalize_scheme_codes(raw_value: str) -> list[str]:
    items = []
    seen = set()
    for raw in (raw_value or "").split(","):
        code = (raw or "").strip()
        if not code:
            continue
        normalized = _normalize_scheme_code(code)
        if normalized in seen:
            continue
        seen.add(normalized)
        items.append(normalized)
    return items


app = FastAPI(title="India Market Mutual Funds")
app.add_middleware(GZipMiddleware, minimum_size=800)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC / "mutual.html")


@app.head("/")
async def index_head():
    return JSONResponse({})


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


@app.head("/health")
async def health_head():
    return JSONResponse({})


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


@app.get("/api/mf/performance")
async def mf_performance(scheme_codes: str = Query(""), range_key: str = Query("max", alias="range")):
    codes = _normalize_scheme_codes(scheme_codes)
    if not codes:
        raise HTTPException(status_code=400, detail="No mutual funds selected")
    try:
        data = await asyncio.to_thread(mutual_funds.compare_many, codes, range_key)
    except Exception as exc:
        print(f"[MutualFunds] performance failed: {exc}")
        raise HTTPException(status_code=400, detail=str(exc))
    return JSONResponse(data)
