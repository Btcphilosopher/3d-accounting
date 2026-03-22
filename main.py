"""
main.py — Entry point for the 3-D Reflexive Accounting System.

Usage
-----
    python main.py cli  <subcommand> [args]   # CLI mode
    python main.py web                         # Start FastAPI web server
    python main.py scheduler                   # Run market-update scheduler
    python main.py demo                        # Seed + run a full demo

All modes share the same SQLite database and accounting engine.
"""

from __future__ import annotations

import logging
import sys
import threading
import time

# ── Logging must be configured before any module imports engine code ──────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("main")

import config
import database
import accounting_engine as engine
import market_data as md
import cli


# ── Scheduler ────────────────────────────────────────────────────────────────

def _market_update_loop(stop_event: threading.Event) -> None:
    """
    Background thread: fetch fresh prices and revalue market assets
    every MARKET_UPDATE_INTERVAL seconds.
    """
    logger.info(
        "Market-update scheduler started (interval = %ds).",
        config.MARKET_UPDATE_INTERVAL,
    )
    while not stop_event.is_set():
        try:
            result = md.run_market_update()
            entries = engine.revalue_market_assets(actor="scheduler")
            logger.info(
                "Scheduled update: prices=%s, revaluations=%d",
                list(result.get("prices", {}).keys()),
                len(entries),
            )
        except Exception as exc:
            logger.error("Scheduler error: %s", exc)
        stop_event.wait(timeout=config.MARKET_UPDATE_INTERVAL)
    logger.info("Market-update scheduler stopped.")


def start_scheduler() -> threading.Event:
    """
    Launch the background market-update thread.

    Returns the stop_event so callers can shut the thread down gracefully.
    """
    stop_event = threading.Event()
    t = threading.Thread(
        target=_market_update_loop,
        args=(stop_event,),
        daemon=True,
        name="market-scheduler",
    )
    t.start()
    return stop_event


# ── Demo mode ─────────────────────────────────────────────────────────────────

def run_demo() -> None:
    """
    Seed demo data, perform a live market update, run depreciation,
    then print the ledger and summary report to the console.

    This gives a full end-to-end demonstration in one command.
    """
    try:
        from rich.console import Console
        console = Console()
        console.rule("[bold cyan]3-D Reflexive Accounting Demo[/bold cyan]")
    except ImportError:
        print("=" * 60)
        print("3-D Reflexive Accounting Demo")
        print("=" * 60)
        console = None

    # 1. Seed accounts and transactions
    logger.info("Step 1: Seeding demo data …")
    engine.seed_demo_data()

    # 2. Fetch live market prices
    logger.info("Step 2: Fetching live market prices …")
    update = md.run_market_update()
    if update["prices"]:
        for ticker, price in update["prices"].items():
            print(f"  {ticker}: ${price:,.2f}")
    else:
        print("  [market API unavailable — using seeded values]")

    # 3. Revalue crypto assets
    logger.info("Step 3: Revaluing market-linked assets …")
    rev_entries = engine.revalue_market_assets(actor="demo")
    print(f"  Revaluations posted: {len(rev_entries)}")

    # 4. Run depreciation
    logger.info("Step 4: Running depreciation pass …")
    dep_entries = engine.apply_depreciation_run(actor="demo")
    print(f"  Depreciation entries: {len(dep_entries)}")

    # 5. Accrue interest on loan
    logger.info("Step 5: Accruing interest on Bank Loan …")
    try:
        engine.accrue_interest("Bank Loan", principal=200_000, actor="demo")
    except ValueError as exc:
        logger.warning("Interest accrual skipped: %s", exc)

    # 6. Display the ledger
    print()
    cli.run_cli(["ledger", "--limit", "30"])

    # 7. Display the report
    print()
    cli.run_cli(["report"])

    # 8. Display prices
    print()
    cli.run_cli(["prices"])


# ── Application bootstrap ─────────────────────────────────────────────────────

def main() -> None:
    """
    Parse the top-level mode argument and delegate accordingly.

    Modes
    -----
    cli <args>    — CLI subcommand (see cli.py for full command list)
    web           — Start FastAPI server (blocking)
    scheduler     — Run background market-update loop (blocking, Ctrl-C to exit)
    demo          — Seed + live demo (non-interactive)
    """
    # Always initialise the database first.
    database.init_db()

    if len(sys.argv) < 2:
        print(__doc__)
        print("\nUsage: python main.py [cli|web|scheduler|demo] [args…]")
        sys.exit(0)

    mode = sys.argv[1].lower()

    # ── CLI mode ──────────────────────────────────────────────────────────────
    if mode == "cli":
        cli.run_cli(sys.argv[2:])

    # ── Web mode ──────────────────────────────────────────────────────────────
    elif mode == "web":
        # Start background market updates alongside the web server.
        stop_event = start_scheduler()
        try:
            from web_app import start_server
            logger.info(
                "Starting web server on http://%s:%d",
                config.WEB_HOST, config.WEB_PORT,
            )
            start_server(host=config.WEB_HOST, port=config.WEB_PORT)
        except ImportError as exc:
            logger.error("Web server dependencies missing: %s", exc)
            logger.error("Install with: pip install fastapi uvicorn")
        finally:
            stop_event.set()

    # ── Scheduler-only mode ───────────────────────────────────────────────────
    elif mode == "scheduler":
        stop_event = start_scheduler()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Scheduler interrupted by user.")
            stop_event.set()

    # ── Demo mode ─────────────────────────────────────────────────────────────
    elif mode == "demo":
        run_demo()

    else:
        print(f"Unknown mode: {mode!r}")
        print("Valid modes: cli, web, scheduler, demo")
        sys.exit(1)


if __name__ == "__main__":
    main()
