# 3-D Reflexive Accounting System

A **real-time, market-linked, reflexive triple-entry ledger** built in Python 3.11.

---

## Architecture

```
reflexive_ledger/
├── main.py              Entry point (cli / web / scheduler / demo modes)
├── config.py            All environment-overridable settings
├── models.py            Pure-Python dataclasses (Account, LedgerEntry, …)
├── database.py          SQLite layer — schema, CRUD, audit log
├── market_data.py       CoinGecko API + interest-rate integration
├── accounting_engine.py 3-D reflexive engine (revaluation, depreciation, …)
├── cli.py               Rich-formatted CLI (argparse)
├── web_app.py           FastAPI REST API + self-contained HTML dashboard
├── requirements.txt
└── README.md
```

### The Three Dimensions

| Dimension | Column | Description |
|-----------|--------|-------------|
| 1 | **Asset Value** | Current fair / market value of the position |
| 2 | **Debit** | Traditional debit (increases assets / expenses) |
| 3 | **Credit** | Traditional credit (increases liabilities / equity / revenue) |
| Reflexive | **Depreciation** | Auto-computed: asset_value × rate / 12 per period |
| Reflexive | **Net Value** | asset_value − depreciation − amortization |

Changes in one column automatically propagate:
* Asset revaluation → Unrealised P&L account updated  
* New asset purchase → Retained Earnings equity adjusted  
* Market price drop → Net value reduced; loss posted to P&L  

---

## Quick Start

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Run the full demo (seed data + live market prices + report)

```bash
python main.py demo
```

This will:
1. Create `reflexive_ledger.db` with the full chart of accounts
2. Post example transactions (cash, BTC, ETH, equipment, loan)
3. **Fetch live BTC / ETH prices from CoinGecko**
4. Revalue crypto holdings based on live prices
5. Run monthly depreciation on server equipment
6. Accrue interest on the bank loan
7. Print the full 3-D ledger table and summary report

---

## CLI Reference

All CLI commands are accessed via:

```bash
python main.py cli <subcommand> [options]
```

### `seed` — Load demo data

```bash
python main.py cli seed
```

### `ledger` — View the 3-D ledger

```bash
# All entries (last 50)
python main.py cli ledger

# Filter by account
python main.py cli ledger --account "Bitcoin Holdings"

# Filter by account type
python main.py cli ledger --type asset

# Filter by ticker
python main.py cli ledger --ticker BTC

# Filter by date range
python main.py cli ledger --start 2024-01-01 --end 2024-12-31

# Show more rows
python main.py cli ledger --limit 200
```

### `add-account` — Create a new account

```bash
# Cash account
python main.py cli add-account "Petty Cash" asset --currency USD

# Crypto asset account
python main.py cli add-account "SOL Holdings" asset \
  --ticker SOL --category crypto

# Depreciable equipment
python main.py cli add-account "Office Furniture" asset \
  --category furniture --dep-rate 0.10 \
  --notes "10% annual straight-line depreciation"

# Liability
python main.py cli add-account "Credit Line" liability
```

### `add-tx` — Post a manual transaction

```bash
# Simple cash receipt
python main.py cli add-tx "Cash" \
  --debit 50000 --credit 0 \
  --description "Client payment received" \
  --ref "INV-2024-042"

# Record an expense
python main.py cli add-tx "Operating Expenses" \
  --debit 5000 --credit 0 \
  --description "Monthly SaaS subscriptions"

python main.py cli add-tx "Cash" \
  --debit 0 --credit 5000 \
  --description "Payment: SaaS subscriptions"
```

### `revalue` — Live market revaluation

```bash
# Revalue all crypto assets (fetches live prices)
python main.py cli revalue

# Revalue specific tickers only
python main.py cli revalue --tickers BTC,ETH
```

### `depreciate` — Monthly depreciation run

```bash
python main.py cli depreciate
```

### `report` — Summary balance sheet + P&L

```bash
python main.py cli report
```

### `prices` — Show latest cached prices

```bash
python main.py cli prices
```

### `audit` — View audit log

```bash
python main.py cli audit
python main.py cli audit --limit 100
```

---

## Web Interface

Start the FastAPI server with background market-update scheduler:

```bash
python main.py web
```

Then open **http://localhost:8000** in your browser.

The dashboard provides:
* **KPI cards** — Assets, Liabilities, Equity, Net Income, Crypto Exposure, Depreciation  
* **Live Prices panel** — Latest cached prices with 24h % change  
* **⚡ Revalue All Assets** button — triggers live CoinGecko fetch + revaluation  
* **3-D Ledger table** — filterable by account / type / ticker / limit  
* **Post Transaction form** — add any manual entry from the browser  
* **Auto-refresh** every 60 seconds  

### REST API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/ledger` | Query ledger (params: account_name, account_type, asset_ticker, start_date, end_date, limit) |
| GET | `/api/accounts` | List all accounts |
| POST | `/api/transactions` | Post manual transaction |
| GET | `/api/report` | Balance-sheet + P&L summary |
| POST | `/api/market/update` | Trigger market fetch + revaluation |
| GET | `/api/prices` | Latest cached prices |
| GET | `/api/prices/{ticker}/history` | Price history for one ticker |
| GET | `/api/audit` | Recent audit log |

#### Example: POST /api/transactions

```bash
curl -X POST http://localhost:8000/api/transactions \
  -H "Content-Type: application/json" \
  -d '{
    "account_name": "Cash",
    "debit": 10000,
    "credit": 0,
    "description": "Client retainer received",
    "reference_id": "INV-001"
  }'
```

#### Example: POST /api/market/update

```bash
curl -X POST http://localhost:8000/api/market/update \
  -H "Content-Type: application/json" \
  -d '{"tickers": ["BTC", "ETH"]}'
```

---

## Scheduler (standalone)

Run only the background market-update loop (useful if the web server is managed separately):

```bash
python main.py scheduler
```

Update interval defaults to 60 seconds; override with:

```bash
MARKET_UPDATE_INTERVAL=300 python main.py scheduler
```

---

## Configuration

All settings can be overridden with environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `LEDGER_DB` | `reflexive_ledger.db` | SQLite file path |
| `WEB_HOST` | `0.0.0.0` | Web server bind address |
| `WEB_PORT` | `8000` | Web server port |
| `MARKET_UPDATE_INTERVAL` | `60` | Seconds between scheduled updates |
| `MOCK_INTEREST_RATE` | `0.0525` | Annual interest rate used for accruals |

Example:

```bash
LEDGER_DB=/data/my_company.db WEB_PORT=9000 python main.py web
```

---

## Example Workflow

```bash
# 1. Seed demo chart of accounts + transactions
python main.py cli seed

# 2. View the initial ledger
python main.py cli ledger

# 3. Fetch live BTC/ETH prices and revalue holdings
python main.py cli revalue

# 4. Run monthly depreciation on equipment
python main.py cli depreciate

# 5. Check updated report
python main.py cli report

# 6. Add a new transaction manually
python main.py cli add-tx "Cash" --debit 25000 \
  --description "New client payment" --ref "INV-2025-007"

# 7. View audit trail
python main.py cli audit

# 8. Start the web dashboard
python main.py web
# → http://localhost:8000
```

---

## Database Schema

```
accounts          — Chart of accounts with depreciation metadata
ledger_entries    — 3-D reflexive ledger (asset_value, debit, credit,
                    depreciation, amortization, net_value)
market_prices     — Time-series price snapshots (CoinGecko)
audit_log         — Append-only event log (never updated/deleted)
```

All tables use WAL journal mode for safe concurrent reads.  
The `audit_log` table is strictly append-only by convention; no UPDATE or DELETE is ever issued against it.

---

## Extending the System

### Add a new market data source

Edit `market_data.py` → add a new function alongside `fetch_crypto_prices()`.  
Return `MarketPrice` objects and call `database.save_market_price()` to persist them.

### Add a live interest-rate feed

In `market_data.py`, replace the body of `fetch_interest_rate()` with a call to the  
[FRED API](https://fred.stlouisfed.org/docs/api/fred/) (free key required) or any  
OpenData source for SOFR / base rate.

### Add a new reflexive rule

In `accounting_engine.py`, extend `_propagate_equity()` or add a new propagation  
function.  Wire it into `add_transaction()` or `revalue_market_assets()`.

### Upgrade to PostgreSQL

Replace `database.py`'s `get_connection()` with `psycopg2` or `asyncpg`.  
The SQL is intentionally standard and compatible with PostgreSQL without changes.
