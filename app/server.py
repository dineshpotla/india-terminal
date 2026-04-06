"""FastAPI server — serves the terminal UI and exposes market data APIs."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .data_engine import DataEngine

STATIC = Path(__file__).parent / "static"

engine = DataEngine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await engine.start()
    yield
    await engine.stop()


app = FastAPI(title="India Market Terminal", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ── Pages ───────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


# ── REST API ────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


@app.get("/api/dashboard")
async def dashboard():
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
    except WebSocketDisconnect:
        pass
    finally:
        engine.unregister(ws)
