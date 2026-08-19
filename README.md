# Agentic Quantitative Research Copilot

A production-grade AI agent for quantitative financial research. Type a natural
language query — the agent fetches real market data, runs quant analysis, and
streams a structured research report back in real time.

Built with an agent-first architecture: the LLM decides which tools to call,
executes them against live market data, and synthesises the results. Not a
chatbot wrapper — a reasoning system with real financial computation.


---



```
CALLING TOOL   get_correlation_matrix(['AAPL','MSFT','NVDA'], '2024-07-27', '2026-07-27')
TOOL DONE      AAPL–MSFT 0.37 | AAPL–NVDA 0.37 | MSFT–NVDA 0.59
CALLING TOOL   get_portfolio_summary(['AAPL','MSFT','NVDA'], ...)
TOOL DONE      Equal-weight portfolio Sharpe: 0.78
CALLING TOOL   get_max_drawdown(['AAPL','MSFT','NVDA'], ...)
TOOL DONE      AAPL -33.36% | MSFT -33.91% | NVDA -36.88%

REPORT
AAPL and MSFT show weak positive correlation (0.37), as do AAPL and NVDA (0.37).
MSFT and NVDA are moderately correlated (0.59) — limited diversification benefit
within the pair.
Equal-weight portfolio Sharpe: 0.78 — moderate risk-adjusted performance.
Worst peak-to-trough loss over the window: NVDA -36.88%, MSFT -33.91%,
AAPL -33.36%.
```



## Architecture

```
Browser (Next.js)
    │
    ├── WebSocket /ws/query ──► FastAPI
    │                               │
    │                           Redis cache (1hr TTL)
    │                               │ MISS
    │                           Groq LLM (llama-3.3-70b)
    │                               │ tool_call
    │                           MCP Servers
    │                           ├── market_data.py → yfinance → MongoDB Atlas
    │                           └── quant.py → analysis + backtest
    │                               │
    └── Streams events back ◄───────┘
        {type: tool_call | tool_done | answer}
```

**Why MCP instead of direct function calls?**
MCP (Model Context Protocol) lets the LLM discover and call tools through a
standard interface. The agent decides at runtime which tools to call and in
what order — it's not hardcoded. Add a new tool and the agent uses it
automatically.

---

## Features

- **Natural language queries** — no structured input required
- **Real-time streaming** — watch the agent call tools live via WebSocket
- **Smart caching** — Redis (query-level) + MongoDB (price-level), cache hits
  return in milliseconds
- **Groq → Ollama fallback** — hits Groq first, falls back to local
  qwen2.5:14b on rate limit
- **Payload compression** — equity curves and raw time series are stripped
  before re-entering the LLM context (228 chars vs 13k+)
- **US + Indian markets** — append `.NS`/`.BO` for NSE/BSE tickers
- **Backtest engine** — equal-weight portfolio with equity curve, Sharpe,
  drawdown, VaR
- **Rate limiting** — 10 req/min on /query, 5 req/min on /backtest
- **CORS locked** — origins from `.env`, not wildcard

---

## Stack

| Layer | Technology |
|---|---|
| LLM (primary) | Groq `llama-3.3-70b-versatile` |
| LLM (fallback) | Ollama `qwen2.5:14b` (local) |
| Agent protocol | MCP via FastMCP |
| Market data | yfinance (US + Indian) |
| Database | MongoDB Atlas (motor async) |
| Cache | Redis (Docker) |
| API | FastAPI + WebSocket + slowapi |
| Frontend | Next.js |
| Charts | Plotly.js |

---

## Project Structure

```
├── db/
│   └── mongo.py              # Data access layer — connect, save, load, cache check
│
├── mcp_servers/
│   ├── market_data.py        # yfinance fetch, ffill, log returns, 3 MCP tools
│   └── quant.py              # 5 MCP tools: drawdown, VaR, correlation, portfolio, backtest
│
├── quant/
│   ├── analysis.py           # max_drawdown, var_95, correlation_matrix, portfolio_summary
│   └── backtest.py           # Backtester class — equity curve, Sharpe, drawdown, VaR
│
├── agent.py                  # Groq tool loop + Ollama fallback, MAX_ITERATIONS=8
├── main.py                   # FastAPI: /query, /backtest, /ws/query, rate limiting, CORS
├── redis_cache.py            # Async Redis connect/get/set
│
├── assets/                   # README demo GIFs and screenshots
│
└── frontend/
    ├── pages/
    │   ├── index.jsx         # Landing page — starfield background, live demo, equity preview
    │   └── chat.jsx          # Terminal UI — query input, live feed, backtest panel, chart
    ├── components/
    │   ├── QueryInput.jsx    # Text input + submit
    │   ├── LiveFeed.jsx      # Streaming WebSocket events with colour-coded types
    │   ├── EquityChart.jsx   # Plotly equity curve (dynamic import, no SSR)
    │   ├── BacktestPanel.jsx # DEMO/CUSTOM toggle, ticker autocomplete, date pickers
    │   ├── GridBackground.jsx # Animated amber grid + star canvas
    │   └── LiveFeedDemo.jsx  # Auto-playing agent demo in landing hero
    └── hooks/
        └── useQuantCopilot.js # WebSocket streaming + /backtest fetch, all backend comms
```

---

## MCP Tools

| Tool | Description |
|---|---|
| `get_price_data` | Log returns + pre-computed stats (Sharpe, vol, return) |
| `get_close_prices` | Raw close prices for drawdown / price level queries |
| `refresh_ticker` | Force delete + re-fetch from yfinance |
| `get_max_drawdown` | Peak-to-trough loss per ticker |
| `get_var_95` | Historical 95% Value-at-Risk per ticker |
| `get_correlation_matrix` | Pairwise correlations across tickers |
| `get_portfolio_summary` | Equal-weight portfolio Sharpe, return, volatility |
| `run_backtest` | Full backtest metrics (equity curve stripped from LLM context) |

---

## Setup

### Prerequisites
- Python 3.11+
- Node.js 18+
- Docker (for Redis)
- Ollama with `qwen2.5:14b` pulled (optional — for local fallback)
- MongoDB Atlas account (free M0 tier)
- Groq API key (free at console.groq.com)

### Backend

```bash
# 1. Clone
git clone https://github.com/aryan-kochhar/Agentic-Quantitative-Research-Copilot
cd Agentic-Quantitative-Research-Copilot

# 2. Create and activate venv
python -m venv venv
venv\Scripts\activate      # Windows
source venv/bin/activate   # Mac/Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create .env
GROQ_API_KEY=gsk_your_key_here
MONGO_URI=mongodb+srv://user:password@cluster.mongodb.net/
REDIS_URL=redis://localhost:6379
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:14b
GROQ_MODEL=llama-3.3-70b-versatile
ALLOWED_ORIGINS=http://localhost:3000

# 5. Start Redis
docker run -d -p 6379:6379 --name redis redis:alpine

# 6. Start Ollama (optional)
ollama serve

# 7. Run backend
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local   # set NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev
```

Open `http://localhost:3000`

---

## API Reference

```
POST /query
Body: { "query": string (max 500 chars) }
Rate: 10/min per IP
Cache: Redis 1hr TTL

POST /backtest
Body: { "tickers": string[], "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD" }
Rate: 5/min per IP
Returns: metrics + full equity curve

WS /ws/query
Send:    { "query": string }
Receive: { "type": "start" | "tool_call" | "tool_done" | "cache_hit" | "answer" | "error", ... }

GET /health
Returns: { "status": "ok" }
```

---

## Indian Market Support

Append `.NS` (NSE) or `.BO` (BSE) to ticker symbols:

```
RELIANCE.NS   TCS.NS   INFY.NS   HDFCBANK.NS   ICICIBANK.NS
```

Mixed portfolios (US + Indian) are blocked — currency mismatch (USD vs INR)
makes portfolio math meaningless across markets.

---

## Key Design Decisions

**Why store close prices, not returns?**
Returns depend on the date range. Storing closes lets you compute returns for
any window without re-fetching. MongoDB holds one document per ticker per day.

**Why summary stats instead of raw time series to the LLM?**
250 rows × 2 tickers = ~25,000 chars. Groq's context limit throws a 413.
Pre-computed stats (Sharpe, vol, return, min, max) = ~500 chars. Same
information the LLM needs, 98% smaller payload.

**Why strip the equity curve before the LLM sees it?**
245 daily equity values = 13,000+ chars of wasted context tokens. The LLM
needs the 5 metrics (Sharpe, return, vol, drawdown, VaR) — not the curve.
The curve goes directly to the frontend via the `/backtest` endpoint.

**Why Groq → Ollama fallback?**
Groq is fast but has rate limits on the free tier. Ollama runs locally with
no limits — same tool-calling interface, zero API cost. Fallback is automatic
on `RateLimitError` or `APIStatusError`.
