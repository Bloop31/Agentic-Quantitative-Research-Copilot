# Quant Copilot

An agentic quantitative research assistant. Ask natural language financial questions — the agent fetches real market data, runs quant analysis, and returns a structured research report.

## Demo

```
Query: "What is the max drawdown of MSFT over the last year?"

→ tool_call: get_max_drawdown
→ tool_done: get_max_drawdown
→ answer: "The maximum drawdown of MSFT over the last year is -35.07%..."
```

## Stack

| Layer | Tech |
|-------|------|
| LLM | Groq (llama-3.3-70b), Ollama fallback |
| Tools | MCP (market data + quant analysis) |
| Data | yfinance → MongoDB Atlas |
| API | FastAPI + WebSocket |
| Cache | Redis (1hr TTL) |

## Project Structure

```
MCP Quant/
├── db/
│   └── mongo.py              # MongoDB data access layer
├── mcp_servers/
│   ├── market_data.py        # yfinance fetch + cache + log returns
│   └── quant.py              # quant analysis tools (drawdown, VaR, correlation, portfolio)
├── quant/
│   └── analysis.py           # pure quant functions
├── agent.py                  # Groq + Ollama tool-calling loop
├── main.py                   # FastAPI REST + WebSocket endpoints
├── redis_cache.py            # Redis query cache
└── requirements.txt
```

## API

### REST
```
POST /query
{
    "query": "What is the Sharpe ratio of AAPL over the last year?"
}
```

### WebSocket
```
WS /ws/query
Send: { "query": "Compare AAPL and MSFT volatility" }

Receive:
  { "type": "tool_call", "tool": "get_price_data" }
  { "type": "tool_done", "tool": "get_price_data" }
  { "type": "answer",    "data": { ... } }
```

## Setup

```bash
# 1. Clone and install
pip install -r requirements.txt

# 2. Create .env
GROQ_API_KEY=your_key
MONGO_URI=your_atlas_uri
REDIS_URL=redis://localhost:6379

# 3. Start Redis
docker run -d -p 6379:6379 redis

# 4. Run
uvicorn main:app --reload
```

## Available Tools

| Tool | Description |
|------|-------------|
| `get_price_data` | Log returns for one or more tickers |
| `get_close_prices` | Raw close prices |
| `refresh_ticker` | Force re-fetch from yfinance |
| `get_max_drawdown` | Peak-to-trough loss analysis |
| `get_var_95` | Historical 95% Value-at-Risk |
| `get_correlation_matrix` | Pairwise correlation between tickers |
| `get_portfolio_summary` | Equal-weight portfolio Sharpe, return, volatility |

## Roadmap

- [x] Phase 1 — MongoDB, yfinance, agent, MCP tools
- [x] Phase 2 — FastAPI, WebSocket, Redis, portfolio + screener MCP servers
- [x] Phase 3 — Frontend, backtest engine, factor analysis, optimizer
