"""
mcp_servers/market_data.py
--------------------------
MCP server that exposes market data tools to the agent.

Responsibility:
    - Fetch US close prices from yfinance
    - Apply the 3 transforms: Close only → ffill → validate
    - Cache raw closes in MongoDB (via mongo.py)
    - Compute log returns on the fly before returning to agent

Tools exposed (MCP):
    - get_price_data(tickers, start, end)   → log returns DataFrame as JSON
    - get_close_prices(tickers, start, end) → raw close prices as JSON
    - refresh_ticker(ticker)                → force re-fetch from yfinance

Data contract (what agent receives):
    DataFrame:
        index   = datetime (UTC)
        columns = tickers  (e.g. ["AAPL", "MSFT"])
        values  = log returns (float)
        no NaNs
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from db.mongo import (
    connect_db,
    close_db,
    save_price_data,
    load_price_data,
    ticker_exists,
    delete_price_data,
)

logger = logging.getLogger(__name__)


# ===========================================================================
# Stage 1 — Raw fetch + clean pipeline
# ===========================================================================

def _fetch_from_yfinance(
    tickers: list[str],
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """
    Fetch close prices from yfinance and apply the 3 transforms.

    Transform pipeline:
        1. df["Close"]    → close prices only
        2. df.ffill()     → forward fill missing (holidays, gaps)
        3. validate       → no NaNs must remain after ffill

    Args:
        tickers: list of uppercase ticker strings e.g. ["AAPL", "MSFT"]
        start:   range start (inclusive)
        end:     range end   (inclusive)

    Returns:
        pd.DataFrame
            index   = DatetimeIndex (UTC)
            columns = tickers
            values  = close prices (float)
            NO NaNs

    Raises:
        ValueError: if yfinance returns empty data or NaNs remain after ffill
    """
    logger.info(
        "[yfinance] Fetching %s from %s to %s",
        tickers, start.date(), end.date()
    )

    raw = yf.download(
        tickers=tickers,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        auto_adjust=True,       # adjust for splits + dividends
        progress=False,         # suppress yfinance progress bar
        threads=True,           # parallel fetch for multiple tickers
    )

    if raw.empty:
        raise ValueError(
            f"yfinance returned empty data for {tickers} "
            f"({start.date()} → {end.date()}). "
            "Check tickers are valid US symbols."
        )

    # --- Step 1: Close prices only ---
    if isinstance(raw.columns, pd.MultiIndex):
        # Multiple tickers → MultiIndex columns → grab "Close" level
        df = raw["Close"]
    else:
        # Single ticker → flat columns
        df = raw[["Close"]].rename(columns={"Close": tickers[0]})

    # Ensure columns are uppercase
    df.columns = [str(c).upper() for c in df.columns]

    # --- Step 2: Forward fill missing data ---
    df = df.ffill()

    # Also backfill the very first row if it starts NaN
    # (ffill can't fill leading NaNs)
    df = df.bfill()

    # --- Step 3: Validate — no NaNs should remain ---
    if df.isnull().values.any():
        nan_cols = df.columns[df.isnull().any()].tolist()
        raise ValueError(
            f"NaNs remain after ffill+bfill for columns: {nan_cols}. "
            "These tickers may have insufficient history for this date range."
        )

    # Ensure DatetimeIndex is UTC-aware
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "date"

    logger.info(
        "[yfinance] Clean data: %d rows x %d tickers",
        len(df), len(df.columns)
    )
    return df


def _compute_log_returns(closes: pd.DataFrame) -> pd.DataFrame:
    """
    Compute log returns from close prices.

    Formula: log(P_t / P_{t-1})

    Drops the first row (NaN after shift) so the agent
    always receives a clean, NaN-free DataFrame.

    Args:
        closes: DataFrame of close prices (index=datetime, cols=tickers)

    Returns:
        DataFrame of log returns — same shape minus first row
    """
    returns = np.log(closes / closes.shift(1))
    returns = returns.dropna()

    logger.debug(
        "[log_returns] %d rows computed from %d close rows",
        len(returns), len(closes)
    )
    return returns


# ===========================================================================
# Stage 2 — Cache layer (mongo check → yfinance fallback)
# ===========================================================================

async def _get_closes_with_cache(
    ticker: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """
    Cache-aware close price loader for a SINGLE ticker.

    Logic:
        1. Check mongo — does a full cache exist for this range?
        2. YES → load from mongo (fast)
        3. NO  → fetch from yfinance → save to mongo → return

    Args:
        ticker: single ticker string e.g. "AAPL"
        start:  range start
        end:    range end

    Returns:
        pd.DataFrame
            index   = DatetimeIndex (UTC)
            columns = [ticker]
            values  = close prices
    """
    ticker = ticker.upper().strip()

    # --- Cache check ---
    cached = await ticker_exists(ticker, start, end)

    if cached:
        logger.info("[cache] HIT — loading %s from MongoDB", ticker)
        df = await load_price_data(ticker, start, end)

        if df is not None and not df.empty:
            return df
        # If mongo returned None despite ticker_exists=True, fall through
        logger.warning("[cache] mongo returned None for %s despite hit — re-fetching", ticker)

    # --- Cache miss → fetch from yfinance ---
    logger.info("[cache] MISS — fetching %s from yfinance", ticker)
    df = _fetch_from_yfinance([ticker], start, end)

    # Save to mongo for next time
    await save_price_data(ticker, df)

    return df[[ticker]] if ticker in df.columns else df


async def _get_multi_ticker_closes(
    tickers: list[str],
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """
    Load closes for MULTIPLE tickers.

    Strategy:
        - Check cache for each ticker individually
        - Fetch all cache-miss tickers in ONE yfinance call (efficient)
        - Merge cached + freshly fetched into single DataFrame
        - Align indexes (some tickers may have different trading day coverage)

    Args:
        tickers: list of ticker strings
        start:   range start
        end:     range end

    Returns:
        pd.DataFrame
            index   = DatetimeIndex (UTC, sorted, inner join of all tickers)
            columns = tickers
            values  = close prices
    """
    tickers = [t.upper().strip() for t in tickers]

    cached_dfs = []
    to_fetch = []

    # --- Split into hits and misses ---
    for ticker in tickers:
        is_cached = await ticker_exists(ticker, start, end)
        if is_cached:
            df = await load_price_data(ticker, start, end)
            if df is not None and not df.empty:
                cached_dfs.append(df)
                logger.info("[cache] HIT — %s", ticker)
                continue
        to_fetch.append(ticker)
        logger.info("[cache] MISS — %s queued for yfinance", ticker)

    # --- Batch fetch all misses in one yfinance call ---
    if to_fetch:
        fresh_df = _fetch_from_yfinance(to_fetch, start, end)

        # Save each ticker to mongo individually
        for ticker in to_fetch:
            if ticker in fresh_df.columns:
                await save_price_data(ticker, fresh_df[[ticker]])

        # Split into per-ticker dfs for merging
        for ticker in to_fetch:
            if ticker in fresh_df.columns:
                cached_dfs.append(fresh_df[[ticker]])

    if not cached_dfs:
        raise ValueError(f"No data returned for any of: {tickers}")

    # --- Merge all ticker dfs on their date index ---
    merged = pd.concat(cached_dfs, axis=1, join="inner")
    merged = merged.sort_index()

    # Reorder columns to match requested order
    available = [t for t in tickers if t in merged.columns]
    merged = merged[available]

    logger.info(
        "[multi_ticker] Final DataFrame: %d rows x %d tickers",
        len(merged), len(merged.columns)
    )
    return merged


# ===========================================================================
# Stage 3 — MCP Tools
# ===========================================================================

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("market_data")


@mcp.tool()
async def get_price_data(
    tickers: list[str],
    start_date: str,
    end_date: str,
) -> dict:
    """
    Get log returns for one or more US tickers.

    This is the PRIMARY tool the agent calls for quant analysis.
    Returns log returns (not raw prices) — ready for Sharpe, VaR, etc.

    Args:
        tickers:    List of US ticker symbols  e.g. ["AAPL", "MSFT", "GOOGL"]
        start_date: Start date string          e.g. "2023-01-01"
        end_date:   End date string            e.g. "2024-01-01"

    Returns:
        dict with keys:
            "returns"     : log returns as dict {ticker: {date: value}}
            "tickers"     : list of tickers returned
            "start"       : actual start date
            "end"         : actual end date
            "rows"        : number of trading days
            "status"      : "ok" or "error"
            "message"     : error description if status == "error"
    """
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end   = datetime.strptime(end_date,   "%Y-%m-%d").replace(tzinfo=timezone.utc)

        closes  = await _get_multi_ticker_closes(tickers, start, end)
        returns = _compute_log_returns(closes)

        # Build summary stats per ticker — avoids sending raw rows to LLM
        stats = {}
        for ticker in returns.columns:
            s = returns[ticker]
            stats[ticker] = {
                "mean_daily_return"  : round(float(s.mean()), 6),
                "std_daily_return"   : round(float(s.std()),  6),
                "annualised_return"  : round(float(s.mean() * 252), 6),
                "annualised_vol"     : round(float(s.std() * (252 ** 0.5)), 6),
                "sharpe_ratio"       : round(float((s.mean() * 252) / (s.std() * (252 ** 0.5))), 4),
                "min_return"         : round(float(s.min()), 6),
                "max_return"         : round(float(s.max()), 6),
                "total_trading_days" : int(len(s)),
            }

        return {
            "status"         : "ok",
            "tickers"        : list(returns.columns),
            "start"          : str(returns.index.min().date()),
            "end"            : str(returns.index.max().date()),
            "rows"           : len(returns),
            "returns_summary": stats,
            "note"           : "Pre-computed stats from log returns. Use these directly.",
        }

    except Exception as e:
        logger.error("[get_price_data] Error: %s", e)
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def get_close_prices(
    tickers: list[str],
    start_date: str,
    end_date: str,
) -> dict:
    """
    Get raw close prices for one or more US tickers.

    Use this when the agent needs actual price levels
    (e.g. current price, price chart, drawdown analysis).

    Args:
        tickers:    List of US ticker symbols
        start_date: Start date string  e.g. "2023-01-01"
        end_date:   End date string    e.g. "2024-01-01"

    Returns:
        dict with keys:
            "closes"  : close prices as dict {ticker: {date: value}}
            "tickers" : list of tickers
            "start"   : actual start date
            "end"     : actual end date
            "rows"    : number of trading days
            "status"  : "ok" or "error"
            "message" : error description if status == "error"
    """
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end   = datetime.strptime(end_date,   "%Y-%m-%d").replace(tzinfo=timezone.utc)

        closes = await _get_multi_ticker_closes(tickers, start, end)

        return {
            "status"  : "ok",
            "tickers" : list(closes.columns),
            "start"   : str(closes.index.min().date()),
            "end"     : str(closes.index.max().date()),
            "rows"    : len(closes),
            "closes"  : closes.reset_index().assign(date=closes.reset_index()["date"].astype(str)).set_index("date").to_dict(),
        }

    except Exception as e:
        logger.error("[get_close_prices] Error: %s", e)
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def refresh_ticker(ticker: str) -> dict:
    """
    Force delete + re-fetch a ticker from yfinance.

    Use when cached data is stale or corrupted.
    Re-fetches the last 2 years of data by default.

    Args:
        ticker: Single US ticker symbol  e.g. "AAPL"

    Returns:
        dict with status and rows saved
    """
    try:
        ticker = ticker.upper().strip()

        deleted = await delete_price_data(ticker)
        logger.info("[refresh] Deleted %d stale docs for %s", deleted, ticker)

        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=730)   # 2 years

        df = _fetch_from_yfinance([ticker], start, end)
        await save_price_data(ticker, df)

        return {
            "status"  : "ok",
            "ticker"  : ticker,
            "deleted" : deleted,
            "rows_saved" : len(df),
            "message" : f"{ticker} refreshed with {len(df)} days of data.",
        }

    except Exception as e:
        logger.error("[refresh_ticker] Error: %s", e)
        return {"status": "error", "message": str(e)}


# ===========================================================================
# Stage 4 — Local test runner (run this file directly to verify)
# ===========================================================================

async def _run_local_test():
    """
    Quick smoke test — run with:
        python -m mcp_servers.market_data

    Tests the full pipeline:
        yfinance → ffill → validate → save mongo → load mongo → log returns
    """
    import os
    from dotenv import load_dotenv

    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s"
    )

    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")

    print("\n" + "="*60)
    print("  market_data.py — Local Smoke Test")
    print("="*60)

    # --- Connect MongoDB ---
    print("\n[1] Connecting to MongoDB...")
    await connect_db(uri=mongo_uri, db_name="quant_copilot_test")
    print("    ✅ Connected")

    # --- Test single ticker ---
    print("\n[2] Fetching AAPL (2024-01-01 → 2024-06-01)...")
    result = await get_price_data(["AAPL"], "2024-01-01", "2024-06-01")

    if result["status"] == "ok":
        print(f"    ✅ Rows returned : {result['rows']}")
        print(f"    ✅ Date range    : {result['start']} → {result['end']}")
        print(f"    ✅ Tickers       : {result['tickers']}")
    else:
        print(f"    ❌ Error: {result['message']}")

    # --- Test cache hit ---
    print("\n[3] Fetching AAPL again (should be cache HIT)...")
    result2 = await get_price_data(["AAPL"], "2024-01-01", "2024-06-01")
    if result2["status"] == "ok":
        print(f"    ✅ Cache hit — {result2['rows']} rows from MongoDB")

    # --- Test multi ticker ---
    print("\n[4] Fetching AAPL + MSFT + GOOGL...")
    result3 = await get_price_data(
        ["AAPL", "MSFT", "GOOGL"],
        "2024-01-01", "2024-06-01"
    )
    if result3["status"] == "ok":
        print(f"    ✅ Tickers : {result3['tickers']}")
        print(f"    ✅ Rows    : {result3['rows']}")
    else:
        print(f"    ❌ Error: {result3['message']}")

    # --- Test close prices ---
    print("\n[5] Testing get_close_prices...")
    result4 = await get_close_prices(["AAPL"], "2024-01-01", "2024-03-01")
    if result4["status"] == "ok":
        print(f"    ✅ Close prices: {result4['rows']} rows")

    # --- Disconnect ---
    print("\n[6] Closing MongoDB connection...")
    await close_db()
    print("    ✅ Done\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(_run_local_test())