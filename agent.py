"""
agent.py
--------
Agentic Quant Research Copilot — core reasoning loop.

Flow:
    1. Receive natural language financial query
    2. Try Groq (llama-3.3-70b) for tool-calling
    3. If Groq rate-limited / unavailable → fallback to Ollama (qwen2.5:14b)
    4. LLM decides which MCP tools to call
    5. Tools execute (market_data MCP server)
    6. LLM receives results, reasons, calls more tools if needed
    7. Returns structured research report

Providers:
    Primary  → Groq      (llama-3.3-70b-versatile) — fast cloud inference
    Fallback → Ollama    (qwen2.5:14b)              — local, no rate limits
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from groq import AsyncGroq, RateLimitError, APIStatusError

from db.mongo import connect_db, close_db
from mcp_servers.market_data import (
    get_price_data,
    get_close_prices,
    refresh_ticker,
)

from mcp_servers.quant import (
    get_max_drawdown,
    get_var_95,
    get_correlation_matrix,
    get_portfolio_summary,
    run_backtest,
)

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config from .env
# ---------------------------------------------------------------------------
GROQ_API_KEY    = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL      = os.getenv("GROQ_MODEL",   "llama-3.3-70b-versatile")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL",  "qwen2.5:14b")
MONGO_URI       = os.getenv("MONGO_URI",     "mongodb://localhost:27017")

# Max tool-call iterations per query (prevents infinite loops)
MAX_ITERATIONS = 8


# ===========================================================================
# Tool definitions — what the LLM sees
# ===========================================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_price_data",
            "description": (
                "Get log returns for one or more US stock tickers. "
                "Use this for: Sharpe ratio, volatility, correlation, "
                "VaR, performance analysis, or any return-based calculation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tickers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of US ticker symbols e.g. ['AAPL', 'MSFT']",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format e.g. '2024-01-01'",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format e.g. '2024-12-31'",
                    },
                },
                "required": ["tickers", "start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_close_prices",
            "description": (
                "Get raw close prices for one or more US stock tickers. "
                "Use this for: current price, drawdown analysis, "
                "price charts, or when you need actual price levels."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tickers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of US ticker symbols e.g. ['AAPL']",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format",
                    },
                },
                "required": ["tickers", "start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refresh_ticker",
            "description": (
                "Force re-fetch a ticker from yfinance, deleting stale cached data. "
                "Use only when the user explicitly asks to refresh or update data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Single US ticker symbol e.g. 'AAPL'",
                    },
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_max_drawdown",
            "description": "Calculate maximum drawdown for one or more tickers. Use for peak-to-trough loss analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tickers":    {"type": "array", "items": {"type": "string"}},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date":   {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["tickers", "start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_var_95",
            "description": "Calculate historical 95% Value-at-Risk per ticker. Use for downside risk analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tickers":    {"type": "array", "items": {"type": "string"}},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date":   {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["tickers", "start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_correlation_matrix",
            "description": "Pairwise correlation between 2+ tickers. Use for diversification and portfolio construction analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tickers":    {"type": "array", "items": {"type": "string"}},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date":   {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["tickers", "start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_portfolio_summary",
            "description": "Equal-weight portfolio stats: annual return, volatility, Sharpe ratio. Use when asked about a portfolio of multiple tickers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tickers":    {"type": "array", "items": {"type": "string"}},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date":   {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["tickers", "start_date", "end_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_backtest",
            "description": (
                "Run a full equal-weight portfolio backtest. Returns cumulative return, "
                "annualized return, volatility, Sharpe ratio, max drawdown, and a daily "
                "equity curve. Use when the user asks about portfolio performance, "
                "backtesting, or historical simulation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tickers":        {"type": "array", "items": {"type": "string"}},
                    "start_date":     {"type": "string", "description": "YYYY-MM-DD"},
                    "end_date":       {"type": "string", "description": "YYYY-MM-DD"},
                    "risk_free_rate": {"type": "number", "description": "Annual risk-free rate, default 0.0"},
                },
                "required": ["tickers", "start_date", "end_date"],
            },
        },
    },
]


# ===========================================================================
# System prompt
# ===========================================================================

SYSTEM_PROMPT = """You are a Quant Research Copilot — an expert financial analyst and quantitative researcher.

You have access to tools that fetch real US market data. When given a financial query:

1. THINK about what data you need and what date range makes sense
2. CALL the appropriate tools to get that data
3. COMPUTE the relevant metrics from the data (Sharpe ratio, volatility, correlation, drawdown, VaR, etc.)
4. SYNTHESIZE a clear, structured research report

Key rules:
- Always use real data from tools — never make up numbers
- For Sharpe ratio: use annualised return / annualised volatility (√252 factor)
- For volatility: annualise by multiplying daily std by √252
- Log returns are already fetched — use them directly for calculations
- If no date range is specified, default to the last 1 year
- Format numbers clearly: percentages to 2 decimal places, ratios to 3 decimal places
- For Indian stocks use NSE suffix: RELIANCE.NS, TCS.NS, INFY.NS etc.
- Never mix Indian and US tickers in the same query
- Indian prices are in INR, US prices in USD — they cannot be compared directly

Today's date: {today}
"""


# ===========================================================================
# Tool executor — routes LLM tool calls to actual functions
# ===========================================================================

async def _execute_tool(tool_name: str, tool_args: dict) -> str:
    """
    Execute a tool call from the LLM and return result as a JSON string.

    Args:
        tool_name: name of the tool the LLM wants to call
        tool_args: arguments the LLM passed

    Returns:
        JSON string of the tool result
    """
    logger.info("[tool] Calling %s with args: %s", tool_name, tool_args)

    try:
        if tool_name == "get_price_data":
            result = await get_price_data(**tool_args)

        elif tool_name == "get_close_prices":
            result = await get_close_prices(**tool_args)

        elif tool_name == "refresh_ticker":
            result = await refresh_ticker(**tool_args)
        
        elif tool_name == "get_max_drawdown":
            result = await get_max_drawdown(**tool_args)
            
        elif tool_name == "get_var_95":
            result = await get_var_95(**tool_args)
            
        elif tool_name == "get_correlation_matrix":
            result = await get_correlation_matrix(**tool_args)
            
        elif tool_name == "get_portfolio_summary":
            result = await get_portfolio_summary(**tool_args)
            
        elif tool_name == "run_backtest":
            result = await run_backtest(**tool_args)

        else:
            result = {"status": "error", "message": f"Unknown tool: {tool_name}"}

    except Exception as e:
        logger.error("[tool] Error executing %s: %s", tool_name, e)
        result = {"status": "error", "message": str(e)}

    # Convert datetime keys to strings for JSON serialisation
    result_str = json.dumps(result, default=str)
    logger.info("[tool] %s → %d chars returned", tool_name, len(result_str))
    return result_str


# ===========================================================================
# Provider 1 — Groq
# ===========================================================================

async def _run_groq_loop(messages: list[dict],callback=None) -> str:
    """
    Run the full tool-calling loop using Groq.

    Raises:
        RateLimitError: when Groq quota is exceeded → triggers fallback
        APIStatusError: on other Groq API errors
    """
    client = AsyncGroq(api_key=GROQ_API_KEY, base_url="https://api.groq.com")
    iteration = 0

    logger.info("[groq] Starting tool loop (model=%s)", GROQ_MODEL)

    while iteration < MAX_ITERATIONS:
        iteration += 1
        logger.info("[groq] Iteration %d/%d", iteration, MAX_ITERATIONS)

        response = await client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.1,        # low temp for consistent quant outputs
            max_tokens=4096,
        )

        msg = response.choices[0].message

        # --- No tool calls → LLM has final answer ---
        if not msg.tool_calls:
            logger.info("[groq] Final answer received after %d iterations", iteration)
            return msg.content

        # --- Process tool calls ---
        # Add assistant message with tool calls to history
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id":       tc.id,
                    "type":     "function",
                    "function": {
                        "name":      tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ],
        })

        # Execute each tool call and add results to history
        for tc in msg.tool_calls:
            if callback:
                await callback({"type": "tool_call", "tool": tc.function.name})

            tool_result = await _execute_tool(
                tc.function.name,
                json.loads(tc.function.arguments),
            )

            if callback:
                await callback({"type": "tool_done", "tool": tc.function.name})

            messages.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      tool_result,
            })

    return "Maximum iterations reached. Partial analysis may be incomplete."


# ===========================================================================
# Provider 2 — Ollama (fallback)
# ===========================================================================

async def _run_ollama_loop(messages: list[dict],callback=None) -> str:
    """
    Run the full tool-calling loop using Ollama (qwen2.5:14b).
    Used as fallback when Groq is rate-limited.

    Ollama uses the OpenAI-compatible /v1/chat/completions endpoint.
    """
    url = f"{OLLAMA_BASE_URL}/v1/chat/completions"
    iteration = 0

    logger.info("[ollama] Starting tool loop (model=%s)", OLLAMA_MODEL)

    async with httpx.AsyncClient(timeout=120.0) as client:
        while iteration < MAX_ITERATIONS:
            iteration += 1
            logger.info("[ollama] Iteration %d/%d", iteration, MAX_ITERATIONS)

            payload = {
                "model":       OLLAMA_MODEL,
                "messages":    messages,
                "tools":       TOOLS,
                "tool_choice": "auto",
                "temperature": 0.1,
                "stream":      False,
            }

            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

            msg = data["choices"][0]["message"]

            # --- No tool calls → final answer ---
            if not msg.get("tool_calls"):
                logger.info("[ollama] Final answer after %d iterations", iteration)
                return msg.get("content", "No response generated.")

            # --- Add assistant message to history ---
            messages.append(msg)

            # --- Execute tool calls ---
            for tc in msg["tool_calls"]:
                fn   = tc["function"]
                args = json.loads(fn["arguments"]) if isinstance(fn["arguments"], str) else fn["arguments"]

                if callback:
                    await callback({"type": "tool_call", "tool": fn["name"]})

                tool_result = await _execute_tool(fn["name"], args)

                if callback:
                    await callback({"type": "tool_done", "tool": fn["name"]})

                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content":      tool_result,
                })

    return "Maximum iterations reached. Partial analysis may be incomplete."


# ===========================================================================
# Public interface — run_query
# ===========================================================================

async def run_query(query: str,callback=None) -> dict:
    """
    Main entry point. Accepts a natural language financial query,
    runs the tool-calling loop, returns a structured result.

    Args:
        query: natural language question e.g.
            "What is the Sharpe ratio of AAPL and MSFT over the last year?"

    Returns:
        dict:
            status:    "ok" or "error"
            answer:    the full research report (string)
            provider:  "groq" or "ollama" (which LLM was used)
            query:     original query
            timestamp: ISO timestamp
    """
    logger.info("[agent] Query received: %s", query)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    messages = [
        {
            "role":    "system",
            "content": SYSTEM_PROMPT.format(today=today),
        },
        {
            "role":    "user",
            "content": query,
        },
    ]

    provider = "groq"
    answer   = ""

    try:
        # --- Try Groq first ---
        answer   = await _run_groq_loop(messages,callback=callback)
        provider = "groq"
        logger.info("[agent] Completed via Groq")

    except RateLimitError as e:
        # --- Groq rate limit hit → fallback to Ollama ---
        logger.warning("[agent] Groq rate limit hit — falling back to Ollama. Error: %s", e)
        provider = "ollama"
        answer   = await _run_ollama_loop(messages,callback=callback)
        logger.info("[agent] Completed via Ollama (fallback)")

    except APIStatusError as e:
        # --- Other Groq API error → also fallback ---
        logger.warning("[agent] Groq API error (%s) — falling back to Ollama", e.status_code)
        provider = "ollama"
        answer   = await _run_ollama_loop(messages,callback=callback)

    except Exception as e:
        logger.error("[agent] Unexpected error: %s", e)
        return {
            "status":    "error",
            "answer":    f"Agent error: {str(e)}",
            "provider":  provider,
            "query":     query,
            "timestamp": today,
        }

    return {
        "status":    "ok",
        "answer":    answer,
        "provider":  provider,
        "query":     query,
        "timestamp": today,
    }


# ===========================================================================
# Local test runner
# ===========================================================================

async def _run_local_test():
    """
    Smoke test the full pipeline end-to-end.
    Run with: python -m agent
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )

    print("\n" + "="*60)
    print("  agent.py — Local Smoke Test")
    print("="*60)

    # Connect MongoDB
    print("\n[1] Connecting to MongoDB...")
    await connect_db(uri=MONGO_URI, db_name="quant_copilot")
    print("    ✅ Connected")

    # Test queries — from simple to complex
    queries = [
        "What is the annualised volatility of AAPL over the last 6 months?",
        "Compare the Sharpe ratio of AAPL and MSFT over the last year.",
        "Backtest an equal weight portfolio of AAPL and MSFT over the last year.",
    ]

    for i, query in enumerate(queries, start=2):
        print(f"\n[{i}] Query: {query}")
        print("-" * 50)

        result = await run_query(query)

        print(f"Provider : {result['provider'].upper()}")
        print(f"Status   : {result['status']}")
        print(f"\n{result['answer']}")

    # Disconnect
    print("\n" + "="*60)
    await close_db()
    print("✅ Done\n")


if __name__ == "__main__":
    asyncio.run(_run_local_test())