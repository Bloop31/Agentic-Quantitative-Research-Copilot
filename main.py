"""
main.py
-------
FastAPI entry point for the Quant Copilot API.

Endpoints:
    GET  /health       → health check
    POST /query        → run a natural language financial query (cached, rate-limited)
    POST /backtest     → run a backtest and return full equity curve for the frontend chart
    WS   /ws/query     → WebSocket endpoint — streams live tool call progress to frontend
"""

import logging
import os
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from db.mongo import connect_db, close_db
from agent import run_query
from redis_cache import connect_redis, close_redis, get_cached, set_cached
from mcp_servers.market_data import _get_multi_ticker_closes
from quant.backtest import Backtester, BacktestError

load_dotenv()
logger = logging.getLogger(__name__)

MONGO_URI    = os.getenv("MONGO_URI",    "mongodb://localhost:27017")
REDIS_URL    = os.getenv("REDIS_URL",    "redis://localhost:6379")
# Comma-separated list of allowed frontend origins e.g. "http://localhost:3000,https://yourapp.com"
# Defaults to localhost only — change in .env before deploying
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
# Keyed by client IP address.
# Prevents a single user from hammering Groq and burning the API quota.
limiter = Limiter(key_func=get_remote_address)


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------
# FastAPI runs this ONCE on start and ONCE on stop.
# All connections are opened here so every request handler can use them.

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )
    logger.info("Starting Quant Copilot API...")
    await connect_db(uri=MONGO_URI, db_name="quant_copilot")
    await connect_redis(url=REDIS_URL)

    yield  # app runs here

    logger.info("Shutting down...")
    await close_db()
    await close_redis()


# ---------------------------------------------------------------------------
# App instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Quant Copilot",
    description="AI-powered quantitative research API",
    version="1.0.0",
    lifespan=lifespan,
)

# Attach rate limiter error handler so 429s return clean JSON, not a crash
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# Only the origins in ALLOWED_ORIGINS can call this API from a browser.
# Never use allow_origins=["*"] in production — that lets any site make
# credentialed requests to your API.

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],   # only what we actually use
    allow_headers=["Content-Type"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str

    # Validate at the Pydantic layer before the request even reaches the handler.
    # Strips whitespace, enforces max length so no one sends a 100k token prompt.
    @field_validator("query")
    @classmethod
    def query_must_be_valid(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Query cannot be empty.")
        if len(v) > 500:
            raise ValueError("Query must be 500 characters or fewer.")
        return v


class BacktestRequest(BaseModel):
    tickers: list[str]
    start_date: str     # "YYYY-MM-DD"
    end_date: str       # "YYYY-MM-DD"
    risk_free_rate: float = 0.0

    @field_validator("tickers")
    @classmethod
    def tickers_must_be_valid(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("At least one ticker is required.")
        if len(v) > 10:
            raise ValueError("Maximum 10 tickers per request.")
        # Uppercase and strip each ticker
        return [t.upper().strip() for t in v]


class QueryResponse(BaseModel):
    status: str       # "ok" or "error"
    answer: str       # the research report text
    provider: str     # "groq" or "ollama"
    query: str        # echoed back so the frontend can match request to response
    timestamp: str    # UTC date


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Quick liveness check. Frontend polls this to confirm the API is up."""
    return {"status": "ok", "message": "Quant Copilot is running."}


@app.post("/query", response_model=QueryResponse)
@limiter.limit("10/minute")
async def query_endpoint(request: Request, body: QueryRequest):
    """
    Natural language query endpoint.
    Runs the Groq agent tool loop and returns a structured research report.

    Rate limited to 10 requests per minute per IP.
    Results are cached in Redis for 1 hour — identical queries skip the agent entirely.
    """
    logger.info("[API] /query → %s", body.query)

    cached = await get_cached(body.query)
    if cached:
        return QueryResponse(**cached)

    result = await run_query(body.query)

    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["answer"])

    await set_cached(body.query, result)
    return QueryResponse(**result)


@app.post("/backtest")
@limiter.limit("5/minute")
async def backtest_endpoint(request: Request, body: BacktestRequest):
    logger.info("[API] /backtest → %s %s→%s", body.tickers, body.start_date, body.end_date)

    # ── Market detection — block mixed portfolios ──────────────────────────
    tickers    = body.tickers
    has_indian = any(t.endswith(".NS") or t.endswith(".BO") for t in tickers)
    has_us     = any(not t.endswith(".NS") and not t.endswith(".BO") for t in tickers)

    if has_indian and has_us:
        raise HTTPException(
            status_code=400,
            detail="Mixed portfolios not supported. Use either US tickers or Indian (.NS/.BO) tickers, not both."
        )
    # ───────────────────────────────────────────────────────────────────────

    try:
        start = datetime.strptime(body.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end   = datetime.strptime(body.end_date,   "%Y-%m-%d").replace(tzinfo=timezone.utc)

        closes = await _get_multi_ticker_closes(body.tickers, start, end)

        bt     = Backtester()
        result = bt.run(closes, risk_free_rate=body.risk_free_rate)

        return {
            "status"      : "ok",
            "tickers"     : body.tickers,
            "start"       : body.start_date,
            "end"         : body.end_date,
            "metrics"     : result["metrics"],
            "equity_curve": result["equity_curve"],
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("[backtest] Error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


import httpx
from redis_cache import get_cached_raw, set_cached_raw
@app.get("/search")
async def search_tickers(q: str, market: str = "US"):
    q = q.strip()
    if not q:
        return {"quotes": []}
    cache_key = f"search:{market}:{q.lower()}"

    cached = await get_cached_raw(cache_key)
    if cached is not None:
        return cached

    region = "IN" if market == "IN" else "US"
    url = f"https://query1.finance.yahoo.com/v1/finance/search?q={q}&region={region}&lang=en-US&quotesCount=10"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.5, connect=1.5)) as client:
            res = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            res.raise_for_status()
            data = res.json()
            await set_cached_raw(cache_key, data, ttl=300)
            return data
    except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.HTTPStatusError) as e:
        logger.warning("[search] Yahoo Finance failed for q=%s: %s", q, e)
        return {"quotes": []}
    except Exception as e:
        logger.error("[search] Unexpected error: %s", e)
        return {"quotes": []}


@app.websocket("/ws/query")
async def websocket_query(websocket: WebSocket):
    """
    WebSocket endpoint — streams live progress as the agent works.

    Connect:  ws://localhost:8000/ws/query
    Send:     { "query": "What is the Sharpe ratio of AAPL?" }

    Receive (in order):
        { "type": "start",      "message": "Processing query..." }
        { "type": "tool_call",  "tool": "get_price_data" }
        { "type": "tool_done",  "tool": "get_price_data" }
        { "type": "answer",     "data": { full QueryResponse } }
        { "type": "cache_hit",  "message": "Returning cached result." }
        { "type": "error",      "message": "..." }
    """
    await websocket.accept()
    logger.info("[WS] Client connected")

    try:
        raw   = await websocket.receive_text()
        data  = json.loads(raw)
        query = data.get("query", "").strip()

        # Validate at the WS layer too — Pydantic doesn't cover WebSocket payloads
        if not query:
            await websocket.send_json({"type": "error", "message": "Query cannot be empty."})
            return
        if len(query) > 500:
            await websocket.send_json({"type": "error", "message": "Query must be 500 characters or fewer."})
            return

        await websocket.send_json({"type": "start", "message": "Processing query..."})

        cached = await get_cached(query)
        if cached:
            await websocket.send_json({"type": "cache_hit", "message": "Returning cached result."})
            await websocket.send_json({"type": "answer", "data": cached})
            return

        async def progress(event: dict):
            await websocket.send_json(event)

        result = await run_query(query, callback=progress)

        if result["status"] == "error":
            await websocket.send_json({"type": "error", "message": result["answer"]})
            return

        await set_cached(query, result)
        await websocket.send_json({"type": "answer", "data": result})

    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected")
    except Exception as e:
        logger.error("[WS] Error: %s", e)
        await websocket.send_json({"type": "error", "message": str(e)})