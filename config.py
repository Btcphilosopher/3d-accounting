"""
config.py — Central configuration for the 3D Reflexive Accounting System.

All environment-overridable constants live here. Import this module
anywhere settings are needed; never hard-code values in other modules.
"""

import os

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_PATH: str = os.getenv("LEDGER_DB", "reflexive_ledger.db")

# ── Web server ────────────────────────────────────────────────────────────────
WEB_HOST: str = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT: int = int(os.getenv("WEB_PORT", "8000"))

# ── Market-data polling ───────────────────────────────────────────────────────
# How often (in seconds) the background scheduler fetches fresh prices.
MARKET_UPDATE_INTERVAL: int = int(os.getenv("MARKET_UPDATE_INTERVAL", "60"))

# CoinGecko public API — no key required for basic endpoints.
COINGECKO_BASE_URL: str = "https://api.coingecko.com/api/v3"

# Map internal ticker symbols → CoinGecko coin IDs.
CRYPTO_ASSETS: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "ADA": "cardano",
    "BNB": "binancecoin",
}

# ── Depreciation ──────────────────────────────────────────────────────────────
# Annual straight-line depreciation rates per asset category.
DEFAULT_DEPRECIATION_RATES: dict[str, float] = {
    "equipment": 0.20,
    "vehicle":   0.25,
    "building":  0.04,
    "software":  0.33,
    "furniture": 0.10,
    "crypto":    0.00,   # crypto assets: not depreciated, but revalued
    "stock":     0.00,
    "bond":      0.00,
}

# ── Accounting categories ─────────────────────────────────────────────────────
ACCOUNT_TYPES: list[str] = [
    "asset",
    "liability",
    "equity",
    "revenue",
    "expense",
]

# ── Reflexive propagation rules ───────────────────────────────────────────────
# When an asset account is revalued, these downstream accounts are updated.
# Format: { source_account_type: [downstream_account_type, ...] }
REFLEXIVE_RULES: dict[str, list[str]] = {
    "asset":    ["equity"],          # asset revaluation → equity adjustment
    "expense":  ["equity"],          # expense accrual   → reduces equity
    "revenue":  ["equity"],          # revenue accrual   → increases equity
    "liability":["equity"],          # new liability     → reduces equity
}

# ── Interest rate (mock / override) ──────────────────────────────────────────
# Used for interest-accrual rules when no live feed is configured.
MOCK_INTEREST_RATE: float = float(os.getenv("MOCK_INTEREST_RATE", "0.0525"))  # 5.25 %

# ── Audit ─────────────────────────────────────────────────────────────────────
# Maximum rows returned by a single ledger query (safety cap).
MAX_QUERY_ROWS: int = 10_000
