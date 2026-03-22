"""
cli.py — Command-line interface for the 3D Reflexive Accounting System.

Uses argparse (stdlib) for subcommand routing and the `rich` library for
beautifully formatted tables, panels, and progress indicators.

Subcommands
-----------
    ledger          View the 3-D ledger (filterable)
    add-account     Add a new account to the chart of accounts
    add-tx          Post a manual transaction
    revalue         Trigger market-price revaluation
    depreciate      Run the monthly depreciation pass
    report          Print the summary P&L / balance-sheet report
    prices          Show the latest cached market prices
    audit           View the last N audit log entries
    seed            Load demo seed data
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import Optional

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

import accounting_engine as engine
import database
import market_data as md
from models import LedgerEntry, ReportSummary

console = Console() if RICH_AVAILABLE else None


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt(value: float, color_zero: bool = False) -> str:
    """Format a float as currency string with thousands separator."""
    formatted = f"${value:>14,.2f}"
    if RICH_AVAILABLE and color_zero and value < 0:
        return f"[red]{formatted}[/red]"
    if RICH_AVAILABLE and color_zero and value > 0:
        return f"[green]{formatted}[/green]"
    return formatted


def _print(msg: str) -> None:
    if console:
        console.print(msg)
    else:
        print(msg)


def _error(msg: str) -> None:
    if console:
        console.print(f"[bold red]ERROR:[/bold red] {msg}")
    else:
        print(f"ERROR: {msg}", file=sys.stderr)


# ── Ledger table ──────────────────────────────────────────────────────────────

def cmd_ledger(args: argparse.Namespace) -> None:
    """
    Display the 3-D reflexive ledger as a rich table.

    Each row shows:
        Account | Type | Asset Value | Debit | Credit | Depreciation | Net Value | Description
    """
    start = datetime.fromisoformat(args.start) if args.start else None
    end   = datetime.fromisoformat(args.end)   if args.end   else None

    try:
        entries = engine.get_3d_ledger(
            account_name=args.account or None,
            account_type=args.type    or None,
            asset_ticker=args.ticker  or None,
            start_date=start,
            end_date=end,
            limit=args.limit,
        )
    except ValueError as exc:
        _error(str(exc))
        return

    if not entries:
        _print("[yellow]No entries found matching the given filters.[/yellow]")
        return

    if RICH_AVAILABLE:
        table = Table(
            title=f"3-D Reflexive Ledger  ({len(entries)} entries)",
            box=box.ROUNDED,
            show_lines=True,
            highlight=True,
        )
        table.add_column("ID",          style="dim",    width=5,  justify="right")
        table.add_column("Timestamp",   style="cyan",   width=19)
        table.add_column("Account",     style="bold",   width=22)
        table.add_column("Type",        style="magenta",width=18)
        # ── Three primary dimensions ──────────────────────────────────────
        table.add_column("Asset Value", style="yellow", width=14, justify="right")
        table.add_column("Debit",       style="green",  width=14, justify="right")
        table.add_column("Credit",      style="red",    width=14, justify="right")
        # ── Reflexive columns ─────────────────────────────────────────────
        table.add_column("Depreciation",style="blue",   width=14, justify="right")
        table.add_column("Net Value",   style="bold",   width=14, justify="right")
        table.add_column("Description", width=30)

        for e in entries:
            net_style = "bold green" if e.net_value >= 0 else "bold red"
            table.add_row(
                str(e.id),
                e.timestamp.strftime("%Y-%m-%d %H:%M"),
                e.account_name,
                e.entry_type.value,
                f"${e.asset_value:>12,.2f}",
                f"${e.debit:>12,.2f}",
                f"${e.credit:>12,.2f}",
                f"${e.depreciation:>12,.2f}",
                f"[{net_style}]${e.net_value:>12,.2f}[/{net_style}]",
                e.description[:30],
            )
        console.print(table)
    else:
        # Plain-text fallback
        hdr = (
            f"{'ID':>5} {'Timestamp':>19} {'Account':>22} {'Type':>18} "
            f"{'AssetValue':>14} {'Debit':>14} {'Credit':>14} "
            f"{'Depreciation':>14} {'NetValue':>14}"
        )
        print(hdr)
        print("-" * len(hdr))
        for e in entries:
            print(
                f"{e.id:>5} {e.timestamp.strftime('%Y-%m-%d %H:%M'):>19} "
                f"{e.account_name:>22} {e.entry_type.value:>18} "
                f"{e.asset_value:>14,.2f} {e.debit:>14,.2f} {e.credit:>14,.2f} "
                f"{e.depreciation:>14,.2f} {e.net_value:>14,.2f}"
            )


# ── Add account ───────────────────────────────────────────────────────────────

def cmd_add_account(args: argparse.Namespace) -> None:
    """Create a new account in the chart of accounts."""
    from models import Account, AccountType, AssetCategory
    try:
        acct = Account(
            id=0,
            name=args.name,
            account_type=AccountType(args.type),
            currency=args.currency,
            asset_ticker=args.ticker or None,
            asset_category=AssetCategory(args.category),
            depreciation_rate=args.dep_rate or None,
            created_at=datetime.utcnow(),
            notes=args.notes or None,
        )
        acct = database.create_account(acct)
        _print(
            f"[green]✓[/green] Account created: [bold]{acct.name}[/bold] "
            f"(id={acct.id}, type={acct.account_type.value})"
        )
    except Exception as exc:
        _error(str(exc))


# ── Add transaction ───────────────────────────────────────────────────────────

def cmd_add_tx(args: argparse.Namespace) -> None:
    """Post a manual debit/credit transaction."""
    try:
        entry = engine.add_transaction(
            account_name=args.account,
            debit=args.debit,
            credit=args.credit,
            description=args.description,
            asset_value=args.asset_value or None,
            reference_id=args.ref or None,
            actor="cli",
        )
        _print(
            f"[green]✓[/green] Entry #{entry.id} posted to "
            f"[bold]{entry.account_name}[/bold]: "
            f"Dr ${entry.debit:,.2f} / Cr ${entry.credit:,.2f} — "
            f"Net ${entry.net_value:,.2f}"
        )
    except ValueError as exc:
        _error(str(exc))


# ── Market revalue ────────────────────────────────────────────────────────────

def cmd_revalue(args: argparse.Namespace) -> None:
    """Fetch live prices and revalue all market-linked asset accounts."""
    _print("[cyan]Fetching market prices …[/cyan]")
    tickers = args.tickers.upper().split(",") if args.tickers else None
    entries = engine.revalue_market_assets(tickers=tickers, actor="cli")
    if entries:
        _print(f"[green]✓[/green] {len(entries)} asset(s) revalued.")
        for e in entries:
            _print(
                f"  • [bold]{e.account_name}[/bold]: "
                f"new value = ${e.asset_value:,.2f}  "
                f"(Δ ${e.debit - e.credit:+,.2f})"
            )
    else:
        _print("[yellow]No market-linked assets found or no prices returned.[/yellow]")


# ── Depreciation run ──────────────────────────────────────────────────────────

def cmd_depreciate(args: argparse.Namespace) -> None:
    """Run the monthly depreciation pass for all depreciable asset accounts."""
    entries = engine.apply_depreciation_run(actor="cli")
    if entries:
        _print(f"[green]✓[/green] Depreciation run: {len(entries)} entries posted.")
        for e in entries:
            _print(
                f"  • [bold]{e.account_name}[/bold]: "
                f"−${e.depreciation:,.2f}  (net = ${e.net_value:,.2f})"
            )
    else:
        _print("[yellow]No depreciable assets found or all fully depreciated.[/yellow]")


# ── Summary report ────────────────────────────────────────────────────────────

def cmd_report(args: argparse.Namespace) -> None:
    """Print a formatted balance-sheet + P&L summary report."""
    report = database.build_report_summary()

    if RICH_AVAILABLE:
        # Balance sheet panel
        bs = Table(box=box.SIMPLE_HEAVY, show_header=False, padding=(0, 1))
        bs.add_column("Label", style="bold", width=28)
        bs.add_column("Amount", justify="right", width=18)

        bs.add_row("[cyan]ASSETS[/cyan]",          "")
        bs.add_row("  Total Assets",                _fmt(report.total_assets))
        bs.add_row("  Crypto Exposure",             _fmt(report.crypto_exposure))
        bs.add_row("  Accumulated Depreciation",    _fmt(-report.total_depreciation, True))
        bs.add_row("",                              "")
        bs.add_row("[cyan]LIABILITIES[/cyan]",      "")
        bs.add_row("  Total Liabilities",           _fmt(report.total_liabilities))
        bs.add_row("",                              "")
        bs.add_row("[cyan]EQUITY[/cyan]",           "")
        bs.add_row("  Total Equity",                _fmt(report.total_equity))
        bs.add_row("  Working Capital",             _fmt(report.working_capital, True))
        bs.add_row("",                              "")
        bs.add_row("[cyan]P&L[/cyan]",              "")
        bs.add_row("  Total Revenue",               _fmt(report.total_revenue))
        bs.add_row("  Total Expenses",              _fmt(report.total_expenses))
        bs.add_row("  Total Amortization",          _fmt(-report.total_amortization, True))
        bs.add_row(
            "[bold]  Net Income[/bold]",
            _fmt(report.net_income, color_zero=True),
        )

        console.print(
            Panel(bs,
                  title=f"[bold]Summary Report — {report.generated_at.strftime('%Y-%m-%d %H:%M UTC')}[/bold]",
                  border_style="bright_blue",
                  expand=False)
        )
    else:
        print("=" * 40)
        print(f"Report: {report.generated_at.strftime('%Y-%m-%d %H:%M UTC')}")
        print("=" * 40)
        print(f"Total Assets:      {report.total_assets:>14,.2f}")
        print(f"Total Liabilities: {report.total_liabilities:>14,.2f}")
        print(f"Total Equity:      {report.total_equity:>14,.2f}")
        print(f"Total Revenue:     {report.total_revenue:>14,.2f}")
        print(f"Total Expenses:    {report.total_expenses:>14,.2f}")
        print(f"Net Income:        {report.net_income:>14,.2f}")
        print(f"Depreciation:      {report.total_depreciation:>14,.2f}")
        print(f"Crypto Exposure:   {report.crypto_exposure:>14,.2f}")


# ── Market prices ─────────────────────────────────────────────────────────────

def cmd_prices(args: argparse.Namespace) -> None:
    """Show the most recent cached price for each configured asset."""
    prices = md.get_all_latest_prices()
    if not prices:
        _print("[yellow]No prices in database yet.  Run 'revalue' first.[/yellow]")
        return

    if RICH_AVAILABLE:
        table = Table(title="Latest Market Prices", box=box.ROUNDED)
        table.add_column("Ticker",       style="bold yellow", width=8)
        table.add_column("Price (USD)",  justify="right",     width=16)
        table.add_column("24h Change",   justify="right",     width=12)
        table.add_column("Market Cap",   justify="right",     width=20)
        table.add_column("Last Updated", width=20)

        for ticker, mp in sorted(prices.items()):
            change_str = f"{mp.change_24h:+.2f}%"
            change_col = "green" if mp.change_24h >= 0 else "red"
            table.add_row(
                ticker,
                f"${mp.price_usd:>14,.2f}",
                f"[{change_col}]{change_str}[/{change_col}]",
                f"${mp.market_cap:>18,.0f}" if mp.market_cap else "N/A",
                mp.last_updated.strftime("%Y-%m-%d %H:%M"),
            )
        console.print(table)
    else:
        for ticker, mp in sorted(prices.items()):
            print(
                f"{ticker:>6}: ${mp.price_usd:>12,.2f}  "
                f"({mp.change_24h:+.2f}%)  "
                f"{mp.last_updated.strftime('%Y-%m-%d %H:%M')}"
            )


# ── Audit log ─────────────────────────────────────────────────────────────────

def cmd_audit(args: argparse.Namespace) -> None:
    """Display the most recent audit log entries."""
    logs = database.list_audit_log(limit=args.limit)
    if not logs:
        _print("[yellow]Audit log is empty.[/yellow]")
        return

    if RICH_AVAILABLE:
        table = Table(title=f"Audit Log (last {len(logs)})", box=box.SIMPLE)
        table.add_column("ID",          style="dim",   width=6,  justify="right")
        table.add_column("Timestamp",   style="cyan",  width=20)
        table.add_column("Event",       style="yellow",width=22)
        table.add_column("Entity",      width=14)
        table.add_column("Actor",       style="green", width=8)
        table.add_column("Description", width=50)

        for log in logs:
            table.add_row(
                str(log.id),
                log.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                log.event_type,
                f"{log.entity_type}#{log.entity_id}",
                log.actor,
                log.description[:50],
            )
        console.print(table)
    else:
        for log in logs:
            print(
                f"{log.timestamp.strftime('%Y-%m-%d %H:%M:%S')} "
                f"[{log.event_type}] {log.entity_type}#{log.entity_id} "
                f"by {log.actor}: {log.description}"
            )


# ── Seed ─────────────────────────────────────────────────────────────────────

def cmd_seed(args: argparse.Namespace) -> None:
    """Load demo accounts and transactions into the database."""
    _print("[cyan]Seeding demo data …[/cyan]")
    engine.seed_demo_data()
    _print("[green]✓[/green] Demo seed complete.  Run [bold]ledger[/bold] to view entries.")


# ── Parser construction ───────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ledger",
        description="3-D Reflexive Accounting System — CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ledger
    p_ledger = sub.add_parser("ledger", help="View the 3-D ledger")
    p_ledger.add_argument("--account", help="Filter by account name")
    p_ledger.add_argument("--type",    help="Filter by account type (asset/liability/…)")
    p_ledger.add_argument("--ticker",  help="Filter by asset ticker (BTC/ETH/…)")
    p_ledger.add_argument("--start",   help="Start date ISO (2024-01-01)")
    p_ledger.add_argument("--end",     help="End date ISO")
    p_ledger.add_argument("--limit",   type=int, default=50, help="Max rows to display")

    # add-account
    p_acct = sub.add_parser("add-account", help="Add a new account")
    p_acct.add_argument("name")
    p_acct.add_argument("type", choices=["asset","liability","equity","revenue","expense"])
    p_acct.add_argument("--ticker",    help="Asset ticker (e.g. BTC)")
    p_acct.add_argument("--category",  default="other",
                        choices=["equipment","vehicle","building","software",
                                 "furniture","crypto","stock","bond","cash","other"])
    p_acct.add_argument("--dep-rate",  type=float, dest="dep_rate",
                        help="Annual straight-line depreciation rate (0.0–1.0)")
    p_acct.add_argument("--currency",  default="USD")
    p_acct.add_argument("--notes")

    # add-tx
    p_tx = sub.add_parser("add-tx", help="Post a manual transaction")
    p_tx.add_argument("account",     help="Account name")
    p_tx.add_argument("--debit",     type=float, default=0.0)
    p_tx.add_argument("--credit",    type=float, default=0.0)
    p_tx.add_argument("--description", required=True)
    p_tx.add_argument("--asset-value", type=float, dest="asset_value")
    p_tx.add_argument("--ref",       help="Reference ID (invoice / PO)")

    # revalue
    p_rev = sub.add_parser("revalue", help="Run market-price revaluation")
    p_rev.add_argument("--tickers",  help="Comma-separated tickers, e.g. BTC,ETH")

    # depreciate
    sub.add_parser("depreciate", help="Run monthly depreciation pass")

    # report
    sub.add_parser("report", help="Print P&L and balance-sheet summary")

    # prices
    sub.add_parser("prices", help="Show latest cached market prices")

    # audit
    p_audit = sub.add_parser("audit", help="View audit log")
    p_audit.add_argument("--limit", type=int, default=50)

    # seed
    sub.add_parser("seed", help="Load demo seed data")

    return parser


# ── Entry point ───────────────────────────────────────────────────────────────

COMMAND_MAP = {
    "ledger":      cmd_ledger,
    "add-account": cmd_add_account,
    "add-tx":      cmd_add_tx,
    "revalue":     cmd_revalue,
    "depreciate":  cmd_depreciate,
    "report":      cmd_report,
    "prices":      cmd_prices,
    "audit":       cmd_audit,
    "seed":        cmd_seed,
}


def run_cli(argv: Optional[list[str]] = None) -> None:
    """
    Parse arguments and dispatch to the appropriate subcommand handler.

    The database must be initialised before calling this function.
    """
    parser = build_parser()
    args   = parser.parse_args(argv)
    handler = COMMAND_MAP.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
