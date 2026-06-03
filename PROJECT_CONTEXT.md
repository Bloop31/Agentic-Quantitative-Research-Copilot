# Quant Copilot — Project Context

## Stack
- MongoDB Atlas (motor async) — stores close prices
- yfinance — US market data source
- Groq (llama-3.3-70b) — primary LLM, Ollama qwen2.5:14b fallback
- MCP — tool protocol between agent and data servers
- FastAPI + WebSocket — live, running on port 8000
- Redis — query result cache (TTL 1 hour)

## Current Status
- Phase 1 complete
- Phase 2 in progress

### Built & Tested
- `db/mongo.py` — MongoDB data access layer
- `mcp_servers/market_data.py` — yfinance fetch, cache, log returns
- `mcp_servers/quant.py` — quant analysis MCP tools (4 tools)
- `quant/analysis.py` — pure quant functions (dev 1)
- `agent.py` — Groq + Ollama fallback, tool-calling loop, WebSocket callback support
- `main.py` — FastAPI REST + WebSocket endpoints
- `redis_cache.py` — Redis query cache

### Atlas Cache
- AAPL: 250 docs
- MSFT: 250 docs

## File Structure
```
MCP Quant/
├── db/
│   └── mongo.py
├── mcp_servers/
│   ├── market_data.py
│   └── quant.py
├── quant/
│   ├── __init__.py
│   └── analysis.py
├── agent.py
├── main.py
├── redis_cache.py
├── requirements.txt
└── .env
```

## API Endpoints
- `GET  /health`     — health check
- `POST /query`      — REST query endpoint
- `WS   /ws/query`   — WebSocket streaming endpoint

## Phase 2 Remaining
- `mcp_servers/portfolio.py` — portfolio analysis MCP server
- `mcp_servers/screener.py`  — stock screener MCP server
- `quant/backtest.py`        — backtesting engine (dev 1)

## Dev Split
- Dev 2 (aiml/infra) — MongoDB, FastAPI, Redis, WebSocket, agent, MCP wiring
- Dev 1 (quant) — quant/analysis.py ✅, quant/backtest.py (in progress), factors.py, optimizer.py, anomaly.py