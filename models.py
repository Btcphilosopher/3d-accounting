"""
models.py — Pure-Python dataclasses that represent every domain object.

No database logic lives here; these are plain value objects used throughout
the system.  Pydantic is intentionally avoided to keep dependencies minimal —
FastAPI will serialise these via asdict() where needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Optional


# ── Enumerations ──────────────────────────────────────────────────────────────

class AccountType(str, Enum):
    """Chart-of-accounts classification (normal-balance convention)."""
    ASSET     = "asset"
    LIABILITY = "liability"
    EQUITY    = "equity"
    REVENUE   = "revenue"
    EXPENSE   = "expense"


class EntryType(str, Enum):
    """How a ledger entry was created."""
    MANUAL            = "manual"
    MARKET_REVALUATION = "market_revaluation"
    DEPRECIATION      = "depreciation"
    AMORTIZATION      = "amortization"
    INTEREST_ACCRUAL  = "interest_accrual"
    EQUITY_ADJUSTMENT = "equity_adjustment"


class AssetCategory(str, Enum):
    """Physical / financial classification used for depreciation lookup."""
    EQUIPMENT  = "equipment"
    VEHICLE    = "vehicle"
    BUILDING   = "building"
    SOFTWARE   = "software"
    FURNITURE  = "furniture"
    CRYPTO     = "crypto"
    STOCK      = "stock"
    BOND       = "bond"
    CASH       = "cash"
    OTHER      = "other"


# ── Domain objects ────────────────────────────────────────────────────────────

@dataclass
class Account:
    """
    Represents a ledger account (a node in the chart of accounts).

    Fields
    ------
    id                 : surrogate primary key (0 = not yet persisted)
    name               : human-readable account name
    account_type       : AccountType enum
    currency           : ISO 4217 code, e.g. "USD" or "BTC"
    asset_ticker       : CoinGecko / Yahoo symbol for market-linked assets
    asset_category     : used to look up depreciation rates
    depreciation_rate  : annual straight-line rate; None = not applicable
    created_at         : creation timestamp
    notes              : free-text notes
    """
    id:                 int
    name:               str
    account_type:       AccountType
    currency:           str                = "USD"
    asset_ticker:       Optional[str]      = None
    asset_category:     AssetCategory      = AssetCategory.OTHER
    depreciation_rate:  Optional[float]    = None
    created_at:         datetime           = field(default_factory=datetime.utcnow)
    notes:              Optional[str]      = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["account_type"]    = self.account_type.value
        d["asset_category"]  = self.asset_category.value
        d["created_at"]      = self.created_at.isoformat()
        return d


@dataclass
class LedgerEntry:
    """
    One row in the 3-D reflexive ledger.

    The three primary "dimensions":
        asset_value   — current fair / book value of the asset at entry time
        debit         — amount debited (increases assets / expenses)
        credit        — amount credited (increases liabilities / equity / revenue)

    Reflexive computed columns (auto-populated by the accounting engine):
        depreciation  — cumulative depreciation applied to this entry
        amortization  — cumulative amortization applied
        net_value     — asset_value − depreciation − amortization

    Audit columns:
        market_source — URL / name of market-data provider used (if any)
        metadata      — JSON bag for extra key-value pairs
    """
    id:            int
    account_id:    int
    account_name:  str
    entry_type:    EntryType
    asset_value:   float
    debit:         float
    credit:        float
    depreciation:  float
    amortization:  float
    net_value:     float
    description:   str
    market_source: Optional[str]
    timestamp:     datetime
    reference_id:  Optional[str]   = None
    metadata:      Optional[dict]  = None

    def balance(self) -> float:
        """Signed balance contribution: positive = net debit."""
        return self.debit - self.credit

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["entry_type"] = self.entry_type.value
        d["timestamp"]  = self.timestamp.isoformat()
        return d


@dataclass
class MarketPrice:
    """Snapshot of an asset's market price from a data provider."""
    ticker:       str
    price_usd:    float
    change_24h:   float          # percentage change in last 24 h
    market_cap:   Optional[float]
    volume_24h:   Optional[float]
    last_updated: datetime
    source:       str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["last_updated"] = self.last_updated.isoformat()
        return d


@dataclass
class AuditLog:
    """
    Immutable record of every write operation.

    The audit table is append-only; rows are never updated or deleted.
    """
    id:          int
    event_type:  str          # e.g. "entry_created", "market_update"
    entity_id:   int          # ID of the affected row
    entity_type: str          # "ledger_entry" | "account" | "market_price"
    description: str
    actor:       str          # "system" | "cli" | "api"
    source:      Optional[str]  # market-data URL or None
    timestamp:   datetime

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


@dataclass
class ReportSummary:
    """Aggregate financial snapshot for a point-in-time report."""
    total_assets:       float
    total_liabilities:  float
    total_equity:       float
    total_revenue:      float
    total_expenses:     float
    net_income:         float
    total_depreciation: float
    total_amortization: float
    crypto_exposure:    float     # market value of all crypto assets
    generated_at:       datetime

    @property
    def working_capital(self) -> float:
        """Simplified: assets minus liabilities."""
        return self.total_assets - self.total_liabilities

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["generated_at"]    = self.generated_at.isoformat()
        d["working_capital"] = self.working_capital
        return d
