"""
market_data.py — Real-time market-data integration.

Supported data sources
----------------------
* CoinGecko public API  — cryptocurrency prices (BTC, ETH, SOL, …)
* Mock interest-rate    — configurable via MOCK_INTEREST_RATE env var
                          (replace with a live FRED / SOFR endpoint as needed)

All fetched prices are persisted to the database so they are available
offline and for historical trend queries.

Public interface
----------------
    fetch_crypto_prices(tickers)  → list[MarketPrice]
    fetch_interest_rate()         → float
    run_market_update()           → dict   (summary of what was updated)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

import requests

import config
import database
from models import MarketPrice

logger = logging.getLogger(__name__)

# HTTP session reused across calls (connection pooling).
_session = requests.Session()
_session.headers.update({
    "Accept": "application/json",
    "User-Agent": "ReflexiveLedger/1.0",
})


# ── CoinGecko helpers ─────────────────────────────────────────────────────────

def fetch_crypto_prices(
    tickers: Optional[list[str]] = None,
    retries: int = 3,
    backoff: float = 2.0,
) -> list[MarketPrice]:
    """
    Fetch current USD prices for one or more cryptocurrency tickers.

    Parameters
    ----------
    tickers  : list of internal ticker symbols, e.g. ["BTC", "ETH"].
               Defaults to all tickers in config.CRYPTO_ASSETS.
    retries  : number of HTTP retries on transient failure.
    backoff  : seconds to wait between retries (doubles each attempt).

    Returns
    -------
    List of MarketPrice objects.  Empty list on total failure.
    """
    if tickers is None:
        tickers = list(config.CRYPTO_ASSETS.keys())

    # Map tickers to CoinGecko IDs, ignoring unknown tickers.
    coin_ids: dict[str, str] = {}
    for t in tickers:
        cg_id = config.CRYPTO_ASSETS.get(t.upper())
        if cg_id:
            coin_ids[t.upper()] = cg_id
        else:
            logger.warning("Unknown ticker '%s' — skipped.", t)

    if not coin_ids:
        return []

    ids_param = ",".join(coin_ids.values())
    url = f"{config.COINGECKO_BASE_URL}/simple/price"
    params = {
        "ids": ids_param,
        "vs_currencies": "usd",
        "include_market_cap": "true",
        "include_24hr_vol": "true",
        "include_24hr_change": "true",
    }

    wait = backoff
    for attempt in range(1, retries + 1):
        try:
            response = _session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            prices: list[MarketPrice] = []
            now = datetime.utcnow()

            for ticker, cg_id in coin_ids.items():
                coin_data = data.get(cg_id, {})
                if not coin_data:
                    logger.warning("No data returned for %s (%s)", ticker, cg_id)
                    continue
                mp = MarketPrice(
                    ticker=ticker,
                    price_usd=float(coin_data.get("usd", 0)),
                    change_24h=float(coin_data.get("usd_24h_change", 0)),
                    market_cap=coin_data.get("usd_market_cap"),
                    volume_24h=coin_data.get("usd_24h_vol"),
                    last_updated=now,
                    source=url,
                )
                prices.append(mp)
                # Persist to DB immediately.
                database.save_market_price(mp)
                logger.info(
                    "Fetched %s = $%.2f (Δ24h %.2f%%)",
                    ticker, mp.price_usd, mp.change_24h,
                )
            return prices

        except requests.exceptions.Timeout:
            logger.warning("CoinGecko timeout (attempt %d/%d)", attempt, retries)
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response else "?"
            logger.warning("CoinGecko HTTP %s (attempt %d/%d)", status, attempt, retries)
            if status == 429:
                # Rate-limited: back off longer.
                wait = min(wait * 3, 120)
        except Exception as exc:
            logger.error("Unexpected error fetching prices: %s", exc)

        if attempt < retries:
            logger.info("Retrying in %.1f s …", wait)
            time.sleep(wait)
            wait *= 2

    logger.error("All retries exhausted for CoinGecko price fetch.")
    return []


def get_cached_price(ticker: str) -> Optional[MarketPrice]:
    """
    Return the most recently stored price for a ticker from the DB.

    Useful when a live fetch is not desired (offline mode, testing).
    """
    return database.get_latest_price(ticker.upper())


# ── Interest-rate helpers ─────────────────────────────────────────────────────

def fetch_interest_rate() -> float:
    """
    Return the current risk-free / reference interest rate.

    Currently returns a configurable mock value.  Replace the body of this
    function with a live API call (e.g. FRED API for US Fed Funds Rate) when
    a live feed is desired.

    Returns
    -------
    Annual interest rate as a decimal, e.g. 0.0525 for 5.25 %.
    """
    rate = config.MOCK_INTEREST_RATE
    logger.info("Interest rate (mock): %.4f (%.2f%%)", rate, rate * 100)
    return rate


# ── Orchestration ─────────────────────────────────────────────────────────────

def run_market_update(tickers: Optional[list[str]] = None) -> dict:
    """
    Fetch all configured market data, persist it, and return a summary dict.

    This is the single entry-point called by the scheduler and by the
    /market/update API endpoint.

    Returns
    -------
    {
        "prices": { "BTC": 68000.0, … },
        "interest_rate": 0.0525,
        "updated_at": "2025-…",
    }
    """
    logger.info("Running full market update …")
    prices = fetch_crypto_prices(tickers)
    rate   = fetch_interest_rate()

    price_map = {p.ticker: p.price_usd for p in prices}
    return {
        "prices":        price_map,
        "interest_rate": rate,
        "updated_at":    datetime.utcnow().isoformat(),
        "tickers_fetched": list(price_map.keys()),
    }


def get_all_latest_prices() -> dict[str, MarketPrice]:
    """
    Return a dict of ticker → latest cached price for all configured assets.

    Falls back gracefully to an empty dict if no prices have been fetched yet.
    """
    result: dict[str, MarketPrice] = {}
    for ticker in config.CRYPTO_ASSETS:
        mp = database.get_latest_price(ticker)
        if mp:
            result[ticker] = mp
    return result
