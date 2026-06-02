# Quant Copilot — Project Context

## Stack
- MongoDB Atlas (motor async) — stores close prices
- yfinance — US market data source
- Groq (llama-3.3-70b) — primary LLM, Ollama qwen2.5:14b fallback
- MCP — tool protocol between agent and data servers
- FastAPI + WebSocket — Phase 2 (not built yet)

## Current Status
- Phase 1 complete
- mongo.py, market_data.py, agent.py all built and tested
- Atlas has AAPL + MSFT data cached (250 docs each)

## File Structure
MCP Quant/
├── db/mongo.py
├── mcp_servers/market_data.py
├── agent.py
├── requirements.txt
└── .env

## Phase 2 Todo
- main.py (FastAPI)
- WebSocket streaming
- redis_cache.py
- portfolio.py MCP server
- screener.py MCP server

## Dev Split
- Dev 2 (me) — data pipeline, agent, infra
- Dev 1 (quant) — quant/analysis.py (max drawdown, VaR, correlation)