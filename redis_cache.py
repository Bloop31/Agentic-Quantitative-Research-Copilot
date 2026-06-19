"""
redis_cache.py
--------------
Simple Redis cache layer for agent query results.

Logic:
    - Key   = hash of the query string (lowercase + stripped)
    - Value = full JSON response from run_query()
    - TTL   = 1 hour (queries stay fresh for 60 mins, then expire)

Why:
    Same query twice → skip Groq entirely → instant response.

Usage:
    from redis_cache import get_cached, set_cached, connect_redis, close_redis
"""

import hashlib
import json
import logging
import os

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_redis: Redis | None = None

CACHE_TTL = 60 * 60  # 1 hour in seconds


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def connect_redis(url: str = "redis://localhost:6379") -> None:
    """Open Redis connection. Call at startup in main.py."""
    global _redis
    _redis = Redis.from_url(url, decode_responses=True)
    await _redis.ping()
    logger.info("Redis connected at %s", url)


async def close_redis() -> None:
    """Close Redis connection. Call at shutdown in main.py."""
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None
        logger.info("Redis connection closed.")


def _get_client() -> Redis:
    if _redis is None:
        raise RuntimeError("Redis not connected. Call await connect_redis() at startup.")
    return _redis


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------

def _make_key(query: str) -> str:
    """
    Hash the query into a short cache key.
    "What is AAPL volatility?" and "what is aapl volatility?" → same key.
    """
    normalised = query.strip().lower()
    return "query:" + hashlib.md5(normalised.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

async def get_cached(query: str) -> dict | None:
    """
    Look up a query in Redis.

    Returns:
        Cached result dict if found, None if cache miss.
    """
    client = _get_client()
    key    = _make_key(query)

    raw = await client.get(key)

    if raw is None:
        logger.info("[cache] MISS — %s", query[:60])
        return None

    logger.info("[cache] HIT  — %s", query[:60])
    return json.loads(raw)


async def set_cached(query: str, result: dict) -> None:
    """
    Store a query result in Redis with a 1-hour TTL.

    Args:
        query:  the original query string
        result: the full dict returned by run_query()
    """
    client = _get_client()
    key    = _make_key(query)

    await client.setex(key, CACHE_TTL, json.dumps(result))
    logger.info("[cache] SET  — %s (TTL=%ds)", query[:60], CACHE_TTL)


async def invalidate(query: str) -> None:
    """Delete a specific query from cache. Useful for testing."""
    client = _get_client()
    key    = _make_key(query)
    await client.delete(key)
    logger.info("[cache] DEL  — %s", query[:60])


# ---------------------------------------------------------------------------
# Generic cache interface (for non-query caching, e.g. /search)
# ---------------------------------------------------------------------------

async def get_cached_raw(key: str) -> dict | None:
    """
    Look up an arbitrary key in Redis (no hashing/prefixing applied).

    Args:
        key: full cache key, e.g. "search:US:appl"

    Returns:
        Cached value dict if found, None if cache miss.
    """
    client = _get_client()
    raw = await client.get(key)

    if raw is None:
        logger.info("[cache] MISS — %s", key)
        return None

    logger.info("[cache] HIT  — %s", key)
    return json.loads(raw)


async def set_cached_raw(key: str, value: dict, ttl: int = 300) -> None:
    """
    Store a value under an arbitrary key with a custom TTL.

    Args:
        key:   full cache key, e.g. "search:US:appl"
        value: JSON-serialisable dict
        ttl:   seconds until expiry (default 5 min)
    """
    client = _get_client()
    await client.setex(key, ttl, json.dumps(value))
    logger.info("[cache] SET  — %s (TTL=%ds)", key, ttl)