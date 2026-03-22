"""
database.py — SQLite persistence layer for the 3D Reflexive Accounting System.

Responsibilities
----------------
* Schema initialisation (idempotent — safe to call on every startup).
* CRUD helpers for accounts, ledger entries, market prices, and audit logs.
* Query helpers filtered by account, asset type, or date range.
* Strict use of parameterised queries to prevent SQL injection.
* Thread-safe via check_same_thread=False + a module-level lock.

All functions accept / return plain Python objects (dataclasses from models.py)
so callers never deal with raw sqlite3.Row tuples.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Generator, Optional

import config
from models import (
    Account, AccountType, AssetCategory,
    AuditLog, LedgerEntry, EntryType, MarketPrice,
    ReportSummary,
)

logger = logging.getLogger(__name__)

# One connection per process; guard concurrent writes with a lock.
_conn: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


# ── Connection management ─────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """
    Return (or lazily create) the module-level SQLite connection.

    Row factory is set so columns are accessible by name.
    WAL journal mode is used for better concurrency.
    """
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(
            config.DATABASE_PATH,
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


@contextmanager
def transaction() -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager that wraps a block in a serialised DB transaction.

    On success the transaction is committed; on any exception it is rolled
    back and the exception is re-raised.
    """
    conn = get_connection()
    with _lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


# ── Schema initialisation ─────────────────────────────────────────────────────

SCHEMA_SQL = """
-- ── Accounts ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS accounts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL UNIQUE,
    account_type      TEXT    NOT NULL,
    currency          TEXT    NOT NULL DEFAULT 'USD',
    asset_ticker      TEXT,
    asset_category    TEXT    NOT NULL DEFAULT 'other',
    depreciation_rate REAL,
    created_at        TEXT    NOT NULL,
    notes             TEXT
);

-- ── Ledger entries (the 3-D reflexive table) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS ledger_entries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id     INTEGER NOT NULL REFERENCES accounts(id),
    entry_type     TEXT    NOT NULL,
    asset_value    REAL    NOT NULL DEFAULT 0,
    debit          REAL    NOT NULL DEFAULT 0,
    credit         REAL    NOT NULL DEFAULT 0,
    depreciation   REAL    NOT NULL DEFAULT 0,
    amortization   REAL    NOT NULL DEFAULT 0,
    net_value      REAL    NOT NULL DEFAULT 0,
    description    TEXT    NOT NULL,
    market_source  TEXT,
    timestamp      TEXT    NOT NULL,
    reference_id   TEXT,
    metadata       TEXT    -- JSON blob
);

CREATE INDEX IF NOT EXISTS idx_entries_account  ON ledger_entries(account_id);
CREATE INDEX IF NOT EXISTS idx_entries_type     ON ledger_entries(entry_type);
CREATE INDEX IF NOT EXISTS idx_entries_ts       ON ledger_entries(timestamp);

-- ── Market price cache ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_prices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT    NOT NULL,
    price_usd    REAL    NOT NULL,
    change_24h   REAL    NOT NULL DEFAULT 0,
    market_cap   REAL,
    volume_24h   REAL,
    last_updated TEXT    NOT NULL,
    source       TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_prices_ticker ON market_prices(ticker);
CREATE INDEX IF NOT EXISTS idx_prices_ts     ON market_prices(last_updated);

-- ── Audit log (append-only) ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT    NOT NULL,
    entity_id   INTEGER NOT NULL,
    entity_type TEXT    NOT NULL,
    description TEXT    NOT NULL,
    actor       TEXT    NOT NULL DEFAULT 'system',
    source      TEXT,
    timestamp   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_entity  ON audit_log(entity_id, entity_type);
CREATE INDEX IF NOT EXISTS idx_audit_ts      ON audit_log(timestamp);
"""


def init_db() -> None:
    """
    Create all tables and indexes if they do not already exist.

    Safe to call multiple times (idempotent due to IF NOT EXISTS).
    """
    with transaction() as conn:
        conn.executescript(SCHEMA_SQL)
    logger.info("Database initialised at %s", config.DATABASE_PATH)


# ── Account helpers ───────────────────────────────────────────────────────────

def _row_to_account(row: sqlite3.Row) -> Account:
    """Map a sqlite3.Row to an Account dataclass."""
    return Account(
        id=row["id"],
        name=row["name"],
        account_type=AccountType(row["account_type"]),
        currency=row["currency"],
        asset_ticker=row["asset_ticker"],
        asset_category=AssetCategory(row["asset_category"]),
        depreciation_rate=row["depreciation_rate"],
        created_at=datetime.fromisoformat(row["created_at"]),
        notes=row["notes"],
    )


def create_account(account: Account) -> Account:
    """
    Insert a new account row and return the account with its generated id.

    Raises sqlite3.IntegrityError if the name already exists.
    """
    sql = """
        INSERT INTO accounts
            (name, account_type, currency, asset_ticker, asset_category,
             depreciation_rate, created_at, notes)
        VALUES (?,?,?,?,?,?,?,?)
    """
    with transaction() as conn:
        cur = conn.execute(sql, (
            account.name,
            account.account_type.value,
            account.currency,
            account.asset_ticker,
            account.asset_category.value,
            account.depreciation_rate,
            account.created_at.isoformat(),
            account.notes,
        ))
        account.id = cur.lastrowid
    _audit("account_created", account.id, "account",
           f"Account '{account.name}' created", "system")
    return account


def get_account(account_id: int) -> Optional[Account]:
    """Return an Account by primary key, or None if not found."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    return _row_to_account(row) if row else None


def get_account_by_name(name: str) -> Optional[Account]:
    """Return an Account by unique name, or None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM accounts WHERE name = ?", (name,)
    ).fetchone()
    return _row_to_account(row) if row else None


def list_accounts(account_type: Optional[str] = None) -> list[Account]:
    """
    Return all accounts, optionally filtered by account_type string.
    """
    conn = get_connection()
    if account_type:
        rows = conn.execute(
            "SELECT * FROM accounts WHERE account_type = ? ORDER BY name",
            (account_type,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM accounts ORDER BY account_type, name"
        ).fetchall()
    return [_row_to_account(r) for r in rows]


# ── Ledger entry helpers ──────────────────────────────────────────────────────

def _row_to_entry(row: sqlite3.Row, account_name: str = "") -> LedgerEntry:
    """Map a sqlite3.Row (from ledger_entries) to a LedgerEntry dataclass."""
    meta_raw = row["metadata"]
    metadata = json.loads(meta_raw) if meta_raw else None
    return LedgerEntry(
        id=row["id"],
        account_id=row["account_id"],
        account_name=account_name or str(row["account_id"]),
        entry_type=EntryType(row["entry_type"]),
        asset_value=row["asset_value"],
        debit=row["debit"],
        credit=row["credit"],
        depreciation=row["depreciation"],
        amortization=row["amortization"],
        net_value=row["net_value"],
        description=row["description"],
        market_source=row["market_source"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        reference_id=row["reference_id"],
        metadata=metadata,
    )


def _fetch_account_name(conn: sqlite3.Connection, account_id: int) -> str:
    row = conn.execute(
        "SELECT name FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    return row["name"] if row else str(account_id)


def create_entry(entry: LedgerEntry, actor: str = "system") -> LedgerEntry:
    """
    Persist a new ledger entry and write an audit log record.

    Returns the entry with its generated id.
    """
    sql = """
        INSERT INTO ledger_entries
            (account_id, entry_type, asset_value, debit, credit,
             depreciation, amortization, net_value, description,
             market_source, timestamp, reference_id, metadata)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    meta_json = json.dumps(entry.metadata) if entry.metadata else None
    with transaction() as conn:
        cur = conn.execute(sql, (
            entry.account_id,
            entry.entry_type.value,
            entry.asset_value,
            entry.debit,
            entry.credit,
            entry.depreciation,
            entry.amortization,
            entry.net_value,
            entry.description,
            entry.market_source,
            entry.timestamp.isoformat(),
            entry.reference_id,
            meta_json,
        ))
        entry.id = cur.lastrowid
    _audit("entry_created", entry.id, "ledger_entry",
           f"{entry.entry_type.value}: {entry.description}", actor,
           source=entry.market_source)
    return entry


def update_entry_revaluation(
    entry_id: int,
    new_asset_value: float,
    new_net_value: float,
    market_source: str,
    actor: str = "system",
) -> None:
    """
    Update asset_value and net_value on an existing entry (market revaluation).

    A new audit record is written for every update.
    """
    with transaction() as conn:
        conn.execute(
            """UPDATE ledger_entries
               SET asset_value = ?, net_value = ?, market_source = ?,
                   entry_type = ?
               WHERE id = ?""",
            (new_asset_value, new_net_value, market_source,
             EntryType.MARKET_REVALUATION.value, entry_id),
        )
    _audit("entry_revalued", entry_id, "ledger_entry",
           f"Revalued to {new_asset_value:.2f}", actor, source=market_source)


def get_entry(entry_id: int) -> Optional[LedgerEntry]:
    """Return a single ledger entry by primary key."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM ledger_entries WHERE id = ?", (entry_id,)
    ).fetchone()
    if not row:
        return None
    name = _fetch_account_name(conn, row["account_id"])
    return _row_to_entry(row, name)


def list_entries(
    account_id: Optional[int] = None,
    account_type: Optional[str] = None,
    asset_ticker: Optional[str] = None,
    entry_type: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = config.MAX_QUERY_ROWS,
) -> list[LedgerEntry]:
    """
    Flexible ledger query supporting all the filter dimensions.

    Returns entries ordered by timestamp descending (most recent first).
    """
    conn = get_connection()
    clauses: list[str] = []
    params: list = []

    if account_id is not None:
        clauses.append("le.account_id = ?")
        params.append(account_id)

    if account_type:
        clauses.append("a.account_type = ?")
        params.append(account_type)

    if asset_ticker:
        clauses.append("a.asset_ticker = ?")
        params.append(asset_ticker.upper())

    if entry_type:
        clauses.append("le.entry_type = ?")
        params.append(entry_type)

    if start_date:
        clauses.append("le.timestamp >= ?")
        params.append(start_date.isoformat())

    if end_date:
        clauses.append("le.timestamp <= ?")
        params.append(end_date.isoformat())

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT le.*, a.name AS account_name
        FROM   ledger_entries le
        JOIN   accounts a ON a.id = le.account_id
        {where}
        ORDER  BY le.timestamp DESC
        LIMIT  {int(limit)}
    """
    rows = conn.execute(sql, params).fetchall()
    results = []
    for row in rows:
        e = _row_to_entry(row, row["account_name"])
        results.append(e)
    return results


def get_account_balance(account_id: int) -> dict:
    """
    Return aggregated debit, credit, net_value, and depreciation for one account.
    """
    conn = get_connection()
    row = conn.execute(
        """
        SELECT
            SUM(debit)        AS total_debit,
            SUM(credit)       AS total_credit,
            SUM(net_value)    AS total_net,
            SUM(depreciation) AS total_depreciation,
            SUM(amortization) AS total_amortization,
            COUNT(*)          AS entry_count
        FROM ledger_entries
        WHERE account_id = ?
        """,
        (account_id,),
    ).fetchone()
    return dict(row) if row else {}


# ── Market price helpers ──────────────────────────────────────────────────────

def save_market_price(price: MarketPrice) -> MarketPrice:
    """Insert a new market-price snapshot row."""
    sql = """
        INSERT INTO market_prices
            (ticker, price_usd, change_24h, market_cap, volume_24h,
             last_updated, source)
        VALUES (?,?,?,?,?,?,?)
    """
    with transaction() as conn:
        cur = conn.execute(sql, (
            price.ticker.upper(),
            price.price_usd,
            price.change_24h,
            price.market_cap,
            price.volume_24h,
            price.last_updated.isoformat(),
            price.source,
        ))
        price_id = cur.lastrowid
    _audit("market_price_saved", price_id, "market_price",
           f"{price.ticker} @ {price.price_usd:.2f} USD", "system",
           source=price.source)
    return price


def get_latest_price(ticker: str) -> Optional[MarketPrice]:
    """Return the most recently stored price snapshot for a ticker."""
    conn = get_connection()
    row = conn.execute(
        """
        SELECT * FROM market_prices
        WHERE  ticker = ?
        ORDER  BY last_updated DESC
        LIMIT  1
        """,
        (ticker.upper(),),
    ).fetchone()
    if not row:
        return None
    return MarketPrice(
        ticker=row["ticker"],
        price_usd=row["price_usd"],
        change_24h=row["change_24h"],
        market_cap=row["market_cap"],
        volume_24h=row["volume_24h"],
        last_updated=datetime.fromisoformat(row["last_updated"]),
        source=row["source"],
    )


def get_price_history(ticker: str, limit: int = 100) -> list[MarketPrice]:
    """Return the last N price snapshots for a ticker, newest first."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM market_prices
        WHERE  ticker = ?
        ORDER  BY last_updated DESC
        LIMIT  ?
        """,
        (ticker.upper(), limit),
    ).fetchall()
    return [
        MarketPrice(
            ticker=r["ticker"],
            price_usd=r["price_usd"],
            change_24h=r["change_24h"],
            market_cap=r["market_cap"],
            volume_24h=r["volume_24h"],
            last_updated=datetime.fromisoformat(r["last_updated"]),
            source=r["source"],
        )
        for r in rows
    ]


# ── Report helper ─────────────────────────────────────────────────────────────

def build_report_summary() -> ReportSummary:
    """
    Aggregate ledger data into a ReportSummary in a single SQL pass.

    Joins accounts to pick up account_type, then groups by type to sum
    net_value (the reflexive computed column).
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            a.account_type,
            a.asset_ticker,
            SUM(le.net_value)    AS total_net,
            SUM(le.depreciation) AS total_dep,
            SUM(le.amortization) AS total_amort
        FROM   ledger_entries le
        JOIN   accounts a ON a.id = le.account_id
        GROUP  BY a.account_type, a.asset_ticker
        """
    ).fetchall()

    totals: dict[str, float] = {
        "asset": 0.0, "liability": 0.0, "equity": 0.0,
        "revenue": 0.0, "expense": 0.0,
    }
    total_dep   = 0.0
    total_amort = 0.0
    crypto_exp  = 0.0

    for row in rows:
        atype = row["account_type"]
        net   = row["total_net"]   or 0.0
        dep   = row["total_dep"]   or 0.0
        amort = row["total_amort"] or 0.0
        if atype in totals:
            totals[atype] += net
        total_dep   += dep
        total_amort += amort
        if row["asset_ticker"] and atype == "asset":
            crypto_exp += net   # crude proxy; refine per ticker if needed

    net_income = totals["revenue"] - totals["expense"]

    return ReportSummary(
        total_assets=totals["asset"],
        total_liabilities=totals["liability"],
        total_equity=totals["equity"],
        total_revenue=totals["revenue"],
        total_expenses=totals["expense"],
        net_income=net_income,
        total_depreciation=total_dep,
        total_amortization=total_amort,
        crypto_exposure=crypto_exp,
        generated_at=datetime.utcnow(),
    )


# ── Audit helpers (private) ───────────────────────────────────────────────────

def _audit(
    event_type: str,
    entity_id: int,
    entity_type: str,
    description: str,
    actor: str,
    source: Optional[str] = None,
) -> None:
    """
    Append an immutable row to the audit_log table.

    This function swallows its own exceptions so that a logging failure
    never rolls back a legitimate business transaction.
    """
    try:
        sql = """
            INSERT INTO audit_log
                (event_type, entity_id, entity_type, description,
                 actor, source, timestamp)
            VALUES (?,?,?,?,?,?,?)
        """
        conn = get_connection()
        with _lock:
            conn.execute(sql, (
                event_type, entity_id, entity_type, description,
                actor, source, datetime.utcnow().isoformat(),
            ))
            conn.commit()
    except Exception as exc:  # pragma: no cover
        logger.warning("Audit log write failed: %s", exc)


def list_audit_log(limit: int = 200) -> list[AuditLog]:
    """Return the most recent audit log entries (newest first)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    return [
        AuditLog(
            id=r["id"],
            event_type=r["event_type"],
            entity_id=r["entity_id"],
            entity_type=r["entity_type"],
            description=r["description"],
            actor=r["actor"],
            source=r["source"],
            timestamp=datetime.fromisoformat(r["timestamp"]),
        )
        for r in rows
    ]
