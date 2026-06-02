"""
db/mongo.py
-----------
Single data access layer for MongoDB.
All reads/writes to MongoDB flow through here.
No other module touches MongoDB directly.

Collections:
    price_history — one document per (ticker, date)
        { ticker, date (datetime), close (float), fetched_at (datetime) }

Indexes (created at startup):
    { ticker: 1, date: 1 }  →  unique compound index
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, UpdateOne

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state  (set by connect_db, cleared by close_db)
# ---------------------------------------------------------------------------
_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None

COLLECTION = "price_history"


# ===========================================================================
# Stage 1 — Connection lifecycle
# ===========================================================================

async def connect_db(uri: str = "mongodb://localhost:27017", db_name: str = "quant_copilot") -> None:
    """
    Open the Motor async client and ensure indexes exist.
    Call once at application startup.

    Args:
        uri:     MongoDB connection string  (default: local dev)
        db_name: Database name              (default: quant_copilot)
    """
    global _client, _db

    logger.info("Connecting to MongoDB at %s / %s", uri, db_name)
    _client = AsyncIOMotorClient(uri)
    _db = _client[db_name]

    await _ensure_indexes()
    logger.info("MongoDB connection established.")


async def close_db() -> None:
    """
    Gracefully close the Motor client.
    Call once at application shutdown.
    """
    global _client, _db

    if _client is not None:
        _client.close()
        _client = None
        _db = None
        logger.info("MongoDB connection closed.")


def get_db() -> AsyncIOMotorDatabase:
    """
    Return the active database handle.
    Raises RuntimeError if connect_db() has not been called.
    Used internally by all other functions in this module.
    """
    if _db is None:
        raise RuntimeError(
            "MongoDB is not connected. Call `await connect_db()` at startup."
        )
    return _db


async def _ensure_indexes() -> None:
    """
    Create the compound unique index on (ticker, date) if it does not exist.
    Called automatically by connect_db — never call directly.
    """
    db = get_db()
    collection = db[COLLECTION]
    await collection.create_index(
        [("ticker", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="ticker_date_unique",
    )
    logger.info("Index ensured on price_history (ticker, date).")


# ===========================================================================
# Stage 2 — Validation helpers
# ===========================================================================

def _validate_dataframe(df: pd.DataFrame, caller: str) -> None:
    """
    Guard against dirty data entering MongoDB.

    Checks:
        - df is a DataFrame
        - df is not empty
        - df has no NaN values in close prices
        - df index is DatetimeIndex
        - all column values are numeric
    
    Raises:
        TypeError:  wrong type passed
        ValueError: empty df, NaNs present, wrong index type
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"[{caller}] Expected pd.DataFrame, got {type(df)}")

    if df.empty:
        raise ValueError(f"[{caller}] DataFrame is empty — nothing to save.")

    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError(f"[{caller}] DataFrame index must be DatetimeIndex.")

    if df.isnull().values.any():
        nan_count = df.isnull().sum().sum()
        raise ValueError(
            f"[{caller}] DataFrame contains {nan_count} NaN(s). "
            "Apply .ffill() before saving."
        )

    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            raise ValueError(
                f"[{caller}] Column '{col}' is not numeric "
                f"(dtype={df[col].dtype}). Only close prices are stored."
            )


# ===========================================================================
# Stage 3 — Write operations
# ===========================================================================

async def save_price_data(ticker: str, df: pd.DataFrame) -> int:
    """
    Persist a single ticker's close price DataFrame to MongoDB.

    Converts each row → one document: { ticker, date, close, fetched_at }
    Uses upsert so re-fetching a date range never creates duplicates.

    Args:
        ticker: e.g. "AAPL"
        df:     Single-column or Series-compatible DataFrame.
                index  = DatetimeIndex
                values = close prices (float)
                NO NaNs (will raise)

    Returns:
        Number of documents upserted.

    Raises:
        TypeError / ValueError from _validate_dataframe
    """
    ticker = ticker.upper().strip()

    # If df is multi-column (e.g. caller passed full Close df), isolate ticker
    if isinstance(df.columns, pd.Index) and ticker in df.columns:
        df = df[[ticker]]

    _validate_dataframe(df, caller="save_price_data")

    db = get_db()
    collection = db[COLLECTION]
    now = datetime.now(timezone.utc)

    operations = []
    # df may be single or multi column — iterate rows
    close_series = df.iloc[:, 0] if df.shape[1] == 1 else df[ticker]

    for date, close_val in close_series.items():
        # Normalise date → UTC midnight datetime (MongoDB needs datetime)
        if isinstance(date, pd.Timestamp):
            doc_date = date.to_pydatetime().replace(
                hour=0, minute=0, second=0, microsecond=0,
                tzinfo=timezone.utc
            )
        else:
            doc_date = datetime(date.year, date.month, date.day, tzinfo=timezone.utc)

        operations.append(
            UpdateOne(
                filter={"ticker": ticker, "date": doc_date},
                update={
                    "$set": {
                        "ticker": ticker,
                        "date": doc_date,
                        "close": float(close_val),
                        "fetched_at": now,
                    }
                },
                upsert=True,
            )
        )

    if not operations:
        logger.warning("[save_price_data] No operations to execute for %s.", ticker)
        return 0

    result = await collection.bulk_write(operations, ordered=False)
    upserted = result.upserted_count + result.modified_count
    logger.info(
        "[save_price_data] %s → %d docs upserted / %d modified.",
        ticker, result.upserted_count, result.modified_count,
    )
    return upserted


# ===========================================================================
# Stage 4 — Read operations
# ===========================================================================

async def ticker_exists(
    ticker: str,
    start: datetime,
    end: datetime,
) -> bool:
    """
    Cache-hit check — does MongoDB already have this ticker for the full range?

    Counts distinct dates in [start, end] and compares to expected trading days.
    Uses a 10% tolerance for market holidays / weekends.

    Args:
        ticker: e.g. "AAPL"
        start:  range start (inclusive)
        end:    range end   (inclusive)

    Returns:
        True  → use the cache, skip yfinance
        False → fetch from yfinance
    """
    ticker = ticker.upper().strip()
    db = get_db()
    collection = db[COLLECTION]

    count = await collection.count_documents({
        "ticker": ticker,
        "date": {
            "$gte": start.replace(tzinfo=timezone.utc),
            "$lte": end.replace(tzinfo=timezone.utc),
        },
    })

    if count == 0:
        return False

    # Expected trading days ≈ 252/year  →  ~70% of calendar days
    calendar_days = (end - start).days
    expected_min = int(calendar_days * 0.60)   # conservative floor

    exists = count >= expected_min
    logger.debug(
        "[ticker_exists] %s: %d docs found, %d expected minimum → %s",
        ticker, count, expected_min, exists,
    )
    return exists


async def load_price_data(
    ticker: str,
    start: datetime,
    end: datetime,
) -> Optional[pd.DataFrame]:
    """
    Retrieve close prices for a ticker over a date range.

    Returns:
        pd.DataFrame with:
            index   = DatetimeIndex (UTC, sorted ascending)
            columns = [ticker]
            values  = close prices (float)
        OR None if no documents found.

    Args:
        ticker: e.g. "AAPL"
        start:  range start (inclusive)
        end:    range end   (inclusive)
    """
    ticker = ticker.upper().strip()
    db = get_db()
    collection = db[COLLECTION]

    cursor = collection.find(
        filter={
            "ticker": ticker,
            "date": {
                "$gte": start.replace(tzinfo=timezone.utc),
                "$lte": end.replace(tzinfo=timezone.utc),
            },
        },
        projection={"_id": 0, "date": 1, "close": 1},
        sort=[("date", ASCENDING)],
    )

    docs = await cursor.to_list(length=None)

    if not docs:
        logger.info("[load_price_data] No data found for %s (%s → %s).", ticker, start.date(), end.date())
        return None

    df = pd.DataFrame(docs)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    df = df.set_index("date").rename(columns={"close": ticker})
    df.index.name = "date"

    logger.info(
        "[load_price_data] %s → %d rows loaded (%s to %s).",
        ticker, len(df), df.index.min().date(), df.index.max().date(),
    )
    return df


async def delete_price_data(ticker: str) -> int:
    """
    Delete ALL price history for a ticker.
    Use for stale data cleanup or re-fetch forcing.

    Args:
        ticker: e.g. "AAPL"

    Returns:
        Number of documents deleted.
    """
    ticker = ticker.upper().strip()
    db = get_db()
    collection = db[COLLECTION]

    result = await collection.delete_many({"ticker": ticker})
    logger.info("[delete_price_data] %s → %d docs deleted.", ticker, result.deleted_count)
    return result.deleted_count