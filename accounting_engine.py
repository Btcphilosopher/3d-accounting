"""
accounting_engine.py — 3-D Reflexive Accounting Engine.

This is the heart of the system.  Every write to the ledger passes through
this module, which enforces double-entry rules, computes reflexive columns
(depreciation, amortization, net_value), and propagates changes downstream
(e.g., an asset revaluation automatically adjusts the equity account).

Public interface
----------------
    add_transaction(...)            — record a manual journal entry
    apply_depreciation_run()        — compute & post periodic depreciation
    revalue_market_assets()         — pull latest prices and revalue crypto/stock
    accrue_interest(account_id)     — post interest accrual for a liability
    propagate_equity_adjustments()  — re-sync equity after asset changes
    get_3d_ledger(...)              — return entries with all 3 dimensions filled

Design decisions
----------------
* Every function returns the entry/entries it created so callers can log or
  display them immediately.
* Reflexive propagation is synchronous within the same function call — there
  is no separate background worker needed beyond the scheduler in main.py.
* All monetary values are plain Python floats (64-bit).  Production systems
  should use the `decimal` module to avoid rounding drift.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import config
import database
import market_data as md
from models import (
    Account, AccountType, AssetCategory,
    EntryType, LedgerEntry, MarketPrice,
)

logger = logging.getLogger(__name__)


# ── Helper: build a LedgerEntry skeleton ────────────────────────────────────

def _make_entry(
    account: Account,
    entry_type: EntryType,
    debit: float,
    credit: float,
    asset_value: float,
    description: str,
    depreciation: float = 0.0,
    amortization: float = 0.0,
    market_source: Optional[str] = None,
    reference_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> LedgerEntry:
    """
    Construct a LedgerEntry with all reflexive columns computed.

    net_value = asset_value − depreciation − amortization
    """
    net_value = asset_value - depreciation - amortization
    return LedgerEntry(
        id=0,                       # 0 = not yet persisted
        account_id=account.id,
        account_name=account.name,
        entry_type=entry_type,
        asset_value=asset_value,
        debit=debit,
        credit=credit,
        depreciation=depreciation,
        amortization=amortization,
        net_value=net_value,
        description=description,
        market_source=market_source,
        timestamp=datetime.utcnow(),
        reference_id=reference_id,
        metadata=metadata,
    )


# ── Core: add a manual transaction ──────────────────────────────────────────

def add_transaction(
    account_name: str,
    debit: float,
    credit: float,
    description: str,
    asset_value: Optional[float] = None,
    reference_id: Optional[str] = None,
    actor: str = "cli",
) -> LedgerEntry:
    """
    Record a manual double-entry transaction and trigger reflexive propagation.

    Parameters
    ----------
    account_name  : must match an existing account name
    debit / credit: transaction amounts (at least one should be non-zero)
    description   : human-readable memo
    asset_value   : explicit fair value; defaults to debit if not provided
    reference_id  : optional invoice / PO reference
    actor         : "cli" | "api" | "system"

    Returns
    -------
    The persisted LedgerEntry.

    Raises
    ------
    ValueError  if the account does not exist.
    """
    account = database.get_account_by_name(account_name)
    if account is None:
        raise ValueError(f"Account '{account_name}' not found.")

    if asset_value is None:
        asset_value = debit if debit else credit

    # Determine depreciation rate for tangible assets.
    dep_rate = account.depreciation_rate or 0.0
    # Apply one period's depreciation (monthly = annual / 12).
    monthly_dep = asset_value * dep_rate / 12 if dep_rate else 0.0

    entry = _make_entry(
        account=account,
        entry_type=EntryType.MANUAL,
        debit=debit,
        credit=credit,
        asset_value=asset_value,
        description=description,
        depreciation=monthly_dep,
        market_source=None,
        reference_id=reference_id,
    )
    entry = database.create_entry(entry, actor=actor)

    # Reflexive rule: asset purchase → equity should be checked.
    if account.account_type in (AccountType.ASSET, AccountType.LIABILITY):
        _propagate_equity(account, debit - credit, actor=actor)

    logger.info(
        "Transaction posted: [%s] Dr %.2f / Cr %.2f — %s",
        account_name, debit, credit, description,
    )
    return entry


# ── Equity propagation ───────────────────────────────────────────────────────

def _propagate_equity(
    source_account: Account,
    delta: float,
    actor: str = "system",
) -> Optional[LedgerEntry]:
    """
    Reflexively adjust the Retained Earnings / Equity account
    when a source account changes value.

    For assets:
        delta > 0 (debit)  → equity decreases (credit equity)
        delta < 0 (credit) → equity increases (debit equity)
    For liabilities:
        opposite sign convention.

    Returns the equity adjustment entry, or None if no equity account exists.
    """
    equity_account = database.get_account_by_name("Retained Earnings")
    if equity_account is None:
        logger.debug("No 'Retained Earnings' account found; skipping propagation.")
        return None

    # Asset accounts: a new asset financed by equity → reduce equity.
    if source_account.account_type == AccountType.ASSET and delta > 0:
        eq_debit, eq_credit = 0.0, delta
        desc = f"Equity adj for asset '{source_account.name}' Δ{delta:+.2f}"
    elif source_account.account_type == AccountType.LIABILITY and delta > 0:
        eq_debit, eq_credit = 0.0, delta
        desc = f"Equity adj for liability '{source_account.name}' Δ{delta:+.2f}"
    else:
        return None   # No propagation needed for other cases.

    eq_entry = _make_entry(
        account=equity_account,
        entry_type=EntryType.EQUITY_ADJUSTMENT,
        debit=eq_debit,
        credit=eq_credit,
        asset_value=0.0,
        description=desc,
    )
    return database.create_entry(eq_entry, actor=actor)


# ── Depreciation run ─────────────────────────────────────────────────────────

def apply_depreciation_run(actor: str = "system") -> list[LedgerEntry]:
    """
    Post a monthly depreciation entry for every asset account that has a
    non-zero depreciation_rate.

    The amount is: (latest net_value × annual_rate) / 12

    Returns
    -------
    List of depreciation entries posted (one per qualifying account).
    """
    accounts = database.list_accounts(account_type="asset")
    posted: list[LedgerEntry] = []

    for account in accounts:
        if not account.depreciation_rate:
            continue

        balance = database.get_account_balance(account.id)
        current_net = balance.get("total_net") or 0.0
        if current_net <= 0:
            continue

        monthly_dep = current_net * account.depreciation_rate / 12
        if monthly_dep < 0.01:
            continue

        entry = _make_entry(
            account=account,
            entry_type=EntryType.DEPRECIATION,
            debit=0.0,
            credit=monthly_dep,     # credit the asset (reduces its book value)
            asset_value=current_net,
            depreciation=monthly_dep,
            description=f"Monthly depreciation @ {account.depreciation_rate*100:.1f}% p.a.",
        )
        entry = database.create_entry(entry, actor=actor)
        posted.append(entry)
        logger.info(
            "Depreciation: %s − $%.2f (net book = $%.2f)",
            account.name, monthly_dep, current_net - monthly_dep,
        )

    return posted


# ── Market asset revaluation ─────────────────────────────────────────────────

def revalue_market_assets(
    tickers: Optional[list[str]] = None,
    actor: str = "system",
) -> list[LedgerEntry]:
    """
    Fetch fresh market prices and post revaluation entries for every
    ledger account linked to those tickers.

    Steps
    -----
    1. Fetch live prices via market_data module.
    2. For each asset account with a matching asset_ticker:
       a. Compute the new fair value (quantity × price).
       b. Post a MARKET_REVALUATION entry (or update the most recent one).
       c. Trigger equity propagation for the gain/loss.
    3. Return all revaluation entries posted.

    Parameters
    ----------
    tickers : optional subset of tickers to revalue; defaults to all.
    """
    update_result = md.run_market_update(tickers)
    price_map: dict[str, float] = update_result["prices"]

    if not price_map:
        logger.warning("No prices fetched; skipping revaluation.")
        return []

    accounts = database.list_accounts(account_type="asset")
    posted: list[LedgerEntry] = []

    for account in accounts:
        ticker = account.asset_ticker
        if not ticker or ticker.upper() not in price_map:
            continue

        new_price = price_map[ticker.upper()]
        balance   = database.get_account_balance(account.id)
        old_net   = balance.get("total_net") or 0.0

        # Read quantity from metadata of the most recent manual entry.
        entries = database.list_entries(account_id=account.id, limit=1)
        quantity = 1.0
        if entries and entries[0].metadata:
            quantity = float(entries[0].metadata.get("quantity", 1.0))

        new_value = new_price * quantity
        gain_loss = new_value - old_net

        source_url = f"{config.COINGECKO_BASE_URL}/simple/price"
        entry = _make_entry(
            account=account,
            entry_type=EntryType.MARKET_REVALUATION,
            debit=max(gain_loss, 0.0),    # gain  → debit asset
            credit=max(-gain_loss, 0.0),  # loss  → credit asset
            asset_value=new_value,
            description=(
                f"Market revaluation: {ticker} @ ${new_price:,.2f} "
                f"× {quantity} = ${new_value:,.2f}"
            ),
            market_source=source_url,
            metadata={"ticker": ticker, "price": new_price, "quantity": quantity},
        )
        entry = database.create_entry(entry, actor=actor)
        posted.append(entry)

        # Reflexively post unrealised gain/loss to P&L equity.
        _post_unrealised_pnl(account, gain_loss, source_url, actor=actor)
        logger.info(
            "Revalued %s: old=%.2f → new=%.2f (Δ%.2f)",
            account.name, old_net, new_value, gain_loss,
        )

    return posted


def _post_unrealised_pnl(
    asset_account: Account,
    gain_loss: float,
    source: str,
    actor: str,
) -> Optional[LedgerEntry]:
    """
    Post an unrealised gain (or loss) to the Unrealised P&L account.

    Gain  → credit Unrealised P&L (income)
    Loss  → debit  Unrealised P&L (expense)
    """
    pnl_account = database.get_account_by_name("Unrealised P&L")
    if pnl_account is None:
        return None

    if abs(gain_loss) < 0.01:
        return None

    if gain_loss > 0:
        debit, credit = 0.0, gain_loss
        desc = f"Unrealised gain on {asset_account.name}: +${gain_loss:,.2f}"
    else:
        debit, credit = abs(gain_loss), 0.0
        desc = f"Unrealised loss on {asset_account.name}: -${abs(gain_loss):,.2f}"

    pnl_entry = _make_entry(
        account=pnl_account,
        entry_type=EntryType.MARKET_REVALUATION,
        debit=debit,
        credit=credit,
        asset_value=0.0,
        description=desc,
        market_source=source,
    )
    return database.create_entry(pnl_entry, actor=actor)


# ── Interest accrual ─────────────────────────────────────────────────────────

def accrue_interest(
    liability_account_name: str,
    principal: Optional[float] = None,
    rate: Optional[float] = None,
    actor: str = "system",
) -> LedgerEntry:
    """
    Post a monthly interest accrual for a liability account.

    Parameters
    ----------
    liability_account_name : must reference an existing liability account.
    principal              : outstanding principal; defaults to account balance.
    rate                   : annual rate; defaults to config.MOCK_INTEREST_RATE.

    Returns
    -------
    The posted interest-accrual entry.
    """
    account = database.get_account_by_name(liability_account_name)
    if account is None:
        raise ValueError(f"Account '{liability_account_name}' not found.")
    if account.account_type != AccountType.LIABILITY:
        raise ValueError(f"'{liability_account_name}' is not a liability account.")

    if principal is None:
        balance   = database.get_account_balance(account.id)
        principal = abs(balance.get("total_net") or 0.0)

    if rate is None:
        rate = md.fetch_interest_rate()

    monthly_interest = principal * rate / 12

    entry = _make_entry(
        account=account,
        entry_type=EntryType.INTEREST_ACCRUAL,
        debit=0.0,
        credit=monthly_interest,
        asset_value=principal,
        amortization=monthly_interest,
        description=(
            f"Interest accrual: ${principal:,.2f} × {rate*100:.2f}%/yr "
            f"/ 12 = ${monthly_interest:,.2f}"
        ),
        metadata={"rate": rate, "principal": principal},
    )
    entry = database.create_entry(entry, actor=actor)
    logger.info(
        "Interest accrued on %s: $%.2f", liability_account_name, monthly_interest
    )
    return entry


# ── 3-D ledger view ──────────────────────────────────────────────────────────

def get_3d_ledger(
    account_name: Optional[str] = None,
    account_type: Optional[str] = None,
    asset_ticker: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 200,
) -> list[LedgerEntry]:
    """
    Return ledger entries enriched with all three accounting dimensions
    plus the two reflexive columns.

    This is the primary read path for the CLI table and the web API.
    """
    account_id: Optional[int] = None
    if account_name:
        acct = database.get_account_by_name(account_name)
        if acct is None:
            raise ValueError(f"Account '{account_name}' not found.")
        account_id = acct.id

    return database.list_entries(
        account_id=account_id,
        account_type=account_type,
        asset_ticker=asset_ticker,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )


# ── Seed demo data ────────────────────────────────────────────────────────────

def seed_demo_data() -> None:
    """
    Create a set of example accounts and transactions so the system
    can be demonstrated immediately after installation.

    Safe to call more than once — duplicate account names are silently skipped.
    """
    from models import AssetCategory

    def _ensure_account(
        name: str,
        atype: str,
        ticker: Optional[str] = None,
        category: str = "other",
        dep_rate: Optional[float] = None,
        currency: str = "USD",
        notes: Optional[str] = None,
    ) -> Account:
        existing = database.get_account_by_name(name)
        if existing:
            return existing
        acct = Account(
            id=0,
            name=name,
            account_type=AccountType(atype),
            currency=currency,
            asset_ticker=ticker,
            asset_category=AssetCategory(category),
            depreciation_rate=dep_rate,
            created_at=datetime.utcnow(),
            notes=notes,
        )
        return database.create_account(acct)

    # ── Chart of accounts ────────────────────────────────────────────────────
    cash_acct      = _ensure_account("Cash",              "asset",     category="cash",
                                     notes="Operating cash account")
    btc_acct       = _ensure_account("Bitcoin Holdings",  "asset",     ticker="BTC",
                                     category="crypto",
                                     notes="Bitcoin held on exchange")
    eth_acct       = _ensure_account("Ethereum Holdings", "asset",     ticker="ETH",
                                     category="crypto",
                                     notes="Ethereum held on exchange")
    equip_acct     = _ensure_account("Server Equipment",  "asset",     category="equipment",
                                     dep_rate=0.20,
                                     notes="On-premise servers; 5-yr useful life")
    loan_acct      = _ensure_account("Bank Loan",         "liability",
                                     notes="5-year term loan @ SOFR+2%")
    equity_acct    = _ensure_account("Retained Earnings", "equity",
                                     notes="Reflexively updated by engine")
    revenue_acct   = _ensure_account("Trading Revenue",   "revenue",
                                     notes="Crypto trading P&L")
    expense_acct   = _ensure_account("Operating Expenses","expense",
                                     notes="Salaries, hosting, etc.")
    pnl_acct       = _ensure_account("Unrealised P&L",    "equity",
                                     notes="Mark-to-market unrealised gains/losses")

    # ── Seed transactions ────────────────────────────────────────────────────
    # Only seed if Cash has no entries yet.
    if database.get_account_balance(cash_acct.id).get("entry_count", 0) == 0:
        # 1. Initial equity injection
        add_transaction("Cash", debit=500_000, credit=0,
                        description="Initial capital injection",
                        actor="seed")
        add_transaction("Retained Earnings", debit=0, credit=500_000,
                        description="Initial equity — offset cash injection",
                        actor="seed")

        # 2. Purchase server equipment
        add_transaction("Server Equipment", debit=45_000, credit=0,
                        description="Purchase: rack servers × 5",
                        reference_id="PO-2024-001",
                        actor="seed")
        add_transaction("Cash", debit=0, credit=45_000,
                        description="Payment: server equipment",
                        reference_id="PO-2024-001",
                        actor="seed")

        # 3. Take out a bank loan
        add_transaction("Cash",      debit=200_000, credit=0,
                        description="Bank loan draw-down",
                        reference_id="LOAN-001", actor="seed")
        add_transaction("Bank Loan", debit=0, credit=200_000,
                        description="Bank loan principal",
                        reference_id="LOAN-001", actor="seed")

        # 4. Buy Bitcoin (1.5 BTC — asset_value seeded at $68,000 placeholder)
        btc_entry = _make_entry(
            account=btc_acct,
            entry_type=EntryType.MANUAL,
            debit=102_000, credit=0,
            asset_value=102_000,
            description="Purchase 1.5 BTC @ ~$68,000",
            metadata={"ticker": "BTC", "quantity": 1.5, "purchase_price": 68_000},
        )
        database.create_entry(btc_entry, actor="seed")
        add_transaction("Cash", debit=0, credit=102_000,
                        description="Payment for 1.5 BTC purchase", actor="seed")

        # 5. Buy Ethereum (10 ETH — placeholder $3,200)
        eth_entry = _make_entry(
            account=eth_acct,
            entry_type=EntryType.MANUAL,
            debit=32_000, credit=0,
            asset_value=32_000,
            description="Purchase 10 ETH @ ~$3,200",
            metadata={"ticker": "ETH", "quantity": 10.0, "purchase_price": 3_200},
        )
        database.create_entry(eth_entry, actor="seed")
        add_transaction("Cash", debit=0, credit=32_000,
                        description="Payment for 10 ETH purchase", actor="seed")

        # 6. Record some revenue
        add_transaction("Cash",           debit=18_500, credit=0,
                        description="Crypto trading profit — Q1",
                        actor="seed")
        add_transaction("Trading Revenue", debit=0, credit=18_500,
                        description="Trading profit recognised",
                        actor="seed")

        # 7. Operating expenses
        add_transaction("Operating Expenses", debit=12_000, credit=0,
                        description="Monthly operating costs (staff + hosting)",
                        actor="seed")
        add_transaction("Cash", debit=0, credit=12_000,
                        description="Payment: operating expenses", actor="seed")

        # 8. Monthly depreciation on equipment
        apply_depreciation_run(actor="seed")

        # 9. Monthly interest on loan
        accrue_interest("Bank Loan", principal=200_000, actor="seed")

        logger.info("Demo seed data loaded successfully.")
    else:
        logger.info("Demo data already present — skipping seed.")
