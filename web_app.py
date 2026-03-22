"""
web_app.py — FastAPI REST + HTML dashboard for the 3-D Reflexive Ledger.

Endpoints
---------
    GET  /                  — Embedded HTML dashboard (no external files needed)
    GET  /api/ledger        — JSON ledger entries (filterable)
    GET  /api/accounts      — JSON chart of accounts
    POST /api/transactions  — Post a manual transaction
    GET  /api/report        — JSON summary report
    POST /api/market/update — Trigger market-price revaluation
    GET  /api/prices        — Latest cached market prices
    GET  /api/prices/{ticker}/history — Price history for one ticker
    GET  /api/audit         — Recent audit log entries

Run with:
    python main.py web
  or directly:
    uvicorn web_app:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import HTMLResponse, JSONResponse
    from pydantic import BaseModel
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False

import accounting_engine as engine
import database
import market_data as md
import config

logger = logging.getLogger(__name__)

# ── FastAPI application ───────────────────────────────────────────────────────

if FASTAPI_AVAILABLE:
    app = FastAPI(
        title="3-D Reflexive Accounting System",
        version="1.0.0",
        description="Real-time, market-linked, reflexive double-entry ledger.",
    )

    # ── Pydantic request schemas ──────────────────────────────────────────────

    class TransactionRequest(BaseModel):
        account_name:  str
        debit:         float = 0.0
        credit:        float = 0.0
        description:   str
        asset_value:   Optional[float] = None
        reference_id:  Optional[str]  = None

    class MarketUpdateRequest(BaseModel):
        tickers: Optional[list[str]] = None

    # ── HTML dashboard (self-contained, no external JS CDN required) ──────────

    DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>3-D Reflexive Ledger</title>
<style>
  :root {
    --bg: #0f172a; --panel: #1e293b; --border: #334155;
    --text: #e2e8f0; --muted: #94a3b8; --accent: #38bdf8;
    --green: #4ade80; --red: #f87171; --yellow: #fbbf24;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif; font-size: 14px; }
  header { background: var(--panel); border-bottom: 1px solid var(--border); padding: 12px 24px; display: flex; align-items: center; gap: 16px; }
  header h1 { color: var(--accent); font-size: 1.2rem; }
  header .subtitle { color: var(--muted); font-size: 0.85rem; }
  .badge { background: #0c4a6e; color: var(--accent); padding: 2px 8px; border-radius: 12px; font-size: 0.75rem; }
  main { padding: 24px; display: grid; gap: 24px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
  .card .label { color: var(--muted); font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }
  .card .value { font-size: 1.4rem; font-weight: 700; }
  .card .value.pos { color: var(--green); }
  .card .value.neg { color: var(--red); }
  .card .value.neu { color: var(--yellow); }
  .section { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 20px; }
  .section h2 { font-size: 1rem; margin-bottom: 16px; color: var(--accent); }
  .controls { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 14px; align-items: center; }
  .controls input, .controls select {
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    padding: 6px 10px; border-radius: 6px; font-size: 13px;
  }
  button {
    background: var(--accent); color: #0f172a; border: none; padding: 6px 14px;
    border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 13px;
  }
  button:hover { opacity: 0.85; }
  button.danger { background: var(--red); color: #fff; }
  button.warn   { background: var(--yellow); color: #0f172a; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 8px 10px; color: var(--muted); border-bottom: 1px solid var(--border); font-weight: 600; white-space: nowrap; }
  td { padding: 7px 10px; border-bottom: 1px solid #1e293b; vertical-align: top; }
  tr:hover td { background: #1a2744; }
  .pos { color: var(--green); }
  .neg { color: var(--red); }
  .badge-type { background: #1e3a5f; color: var(--accent); padding: 1px 7px; border-radius: 10px; font-size: 0.72rem; }
  .status { padding: 6px 12px; background: #1e293b; border-left: 3px solid var(--accent); border-radius: 4px; margin-bottom: 12px; font-size: 12px; color: var(--muted); }
  .prices-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px,1fr)); gap: 12px; margin-top: 8px; }
  .price-card { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
  .price-card .ticker { font-weight: 700; color: var(--yellow); font-size: 1rem; }
  .price-card .price { font-size: 1.1rem; margin: 4px 0; }
  .price-card .change { font-size: 0.8rem; }
  #status-bar { position: fixed; bottom: 0; left: 0; right: 0; background: var(--panel); border-top: 1px solid var(--border); padding: 6px 24px; font-size: 12px; color: var(--muted); }
</style>
</head>
<body>
<header>
  <h1>🔷 3-D Reflexive Ledger</h1>
  <span class="subtitle">Real-time · Market-Linked · Double-Entry</span>
  <span class="badge" id="last-update">Loading…</span>
</header>
<main>
  <!-- KPI cards -->
  <div class="cards" id="kpi-cards">
    <div class="card"><div class="label">Total Assets</div><div class="value neu" id="kpi-assets">—</div></div>
    <div class="card"><div class="label">Total Liabilities</div><div class="value neg" id="kpi-liabilities">—</div></div>
    <div class="card"><div class="label">Total Equity</div><div class="value pos" id="kpi-equity">—</div></div>
    <div class="card"><div class="label">Net Income</div><div class="value" id="kpi-income">—</div></div>
    <div class="card"><div class="label">Crypto Exposure</div><div class="value neu" id="kpi-crypto">—</div></div>
    <div class="card"><div class="label">Depreciation</div><div class="value neg" id="kpi-dep">—</div></div>
  </div>

  <!-- Market prices -->
  <div class="section">
    <h2>Live Market Prices</h2>
    <div style="display:flex;gap:8px;margin-bottom:12px;">
      <button onclick="fetchPrices()">↻ Refresh Cache</button>
      <button class="warn" onclick="triggerRevalue()">⚡ Revalue All Assets</button>
    </div>
    <div class="prices-grid" id="prices-grid">Loading…</div>
  </div>

  <!-- Ledger -->
  <div class="section">
    <h2>3-D Reflexive Ledger</h2>
    <div class="controls">
      <input id="filter-account" placeholder="Account name…" style="width:180px">
      <select id="filter-type">
        <option value="">All types</option>
        <option value="asset">Asset</option>
        <option value="liability">Liability</option>
        <option value="equity">Equity</option>
        <option value="revenue">Revenue</option>
        <option value="expense">Expense</option>
      </select>
      <input id="filter-ticker" placeholder="Ticker (BTC…)" style="width:110px">
      <input id="filter-limit" type="number" value="50" style="width:70px" min="1" max="500">
      <button onclick="fetchLedger()">🔍 Query</button>
    </div>
    <div id="ledger-status" class="status" style="display:none"></div>
    <div style="overflow-x:auto;">
      <table id="ledger-table">
        <thead>
          <tr>
            <th>ID</th><th>Timestamp</th><th>Account</th><th>Type</th>
            <th>Asset Value ▲</th><th>Debit ▲</th><th>Credit ▲</th>
            <th>Depreciation ▼</th><th>Net Value ▼</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody id="ledger-body"><tr><td colspan="10" style="color:var(--muted)">Loading…</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- Post transaction form -->
  <div class="section">
    <h2>Post Manual Transaction</h2>
    <div class="controls">
      <input id="tx-account" placeholder="Account name" style="width:200px">
      <input id="tx-debit"   placeholder="Debit"  type="number" step="0.01" style="width:120px">
      <input id="tx-credit"  placeholder="Credit" type="number" step="0.01" style="width:120px">
      <input id="tx-desc"    placeholder="Description" style="width:260px">
      <input id="tx-ref"     placeholder="Ref ID (optional)" style="width:140px">
      <button onclick="postTransaction()">✚ Post</button>
    </div>
    <div id="tx-status" style="margin-top:8px;font-size:13px;color:var(--muted)"></div>
  </div>
</main>
<div id="status-bar">Ready.</div>

<script>
const fmt = v => '$' + (parseFloat(v)||0).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});

async function apiFetch(path, opts={}) {
  const r = await fetch('/api' + path, opts);
  if (!r.ok) { const t = await r.text(); throw new Error(t); }
  return r.json();
}

function setStatus(msg) {
  document.getElementById('status-bar').textContent = msg;
}

async function fetchReport() {
  try {
    const d = await apiFetch('/report');
    document.getElementById('kpi-assets').textContent      = fmt(d.total_assets);
    document.getElementById('kpi-liabilities').textContent = fmt(d.total_liabilities);
    document.getElementById('kpi-equity').textContent      = fmt(d.total_equity);
    const inc = document.getElementById('kpi-income');
    inc.textContent = fmt(d.net_income);
    inc.className = 'value ' + (d.net_income >= 0 ? 'pos' : 'neg');
    document.getElementById('kpi-crypto').textContent = fmt(d.crypto_exposure);
    document.getElementById('kpi-dep').textContent    = fmt(d.total_depreciation);
    document.getElementById('last-update').textContent = 'Updated: ' + new Date().toLocaleTimeString();
  } catch(e) { setStatus('Report error: ' + e.message); }
}

async function fetchPrices() {
  try {
    const prices = await apiFetch('/prices');
    const grid = document.getElementById('prices-grid');
    if (!Object.keys(prices).length) { grid.innerHTML = '<span style="color:var(--muted)">No prices cached yet. Click Revalue.</span>'; return; }
    grid.innerHTML = Object.entries(prices).map(([t,p]) => {
      const chg = parseFloat(p.change_24h||0);
      return `<div class="price-card">
        <div class="ticker">${t}</div>
        <div class="price">${fmt(p.price_usd)}</div>
        <div class="change ${chg>=0?'pos':'neg'}">${chg>=0?'▲':'▼'} ${Math.abs(chg).toFixed(2)}% 24h</div>
        <div style="color:var(--muted);font-size:11px;margin-top:4px">${new Date(p.last_updated).toLocaleString()}</div>
      </div>`;
    }).join('');
  } catch(e) { setStatus('Prices error: ' + e.message); }
}

async function triggerRevalue() {
  setStatus('Triggering market revaluation…');
  try {
    const r = await apiFetch('/market/update', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({})});
    setStatus('Revaluation complete. Updated: ' + (r.tickers_fetched||[]).join(', '));
    await fetchReport();
    await fetchPrices();
    await fetchLedger();
  } catch(e) { setStatus('Revalue error: ' + e.message); }
}

async function fetchLedger() {
  const params = new URLSearchParams();
  const acct = document.getElementById('filter-account').value.trim();
  const type = document.getElementById('filter-type').value;
  const ticker = document.getElementById('filter-ticker').value.trim();
  const limit = document.getElementById('filter-limit').value;
  if (acct)   params.set('account_name', acct);
  if (type)   params.set('account_type', type);
  if (ticker) params.set('asset_ticker', ticker.toUpperCase());
  if (limit)  params.set('limit', limit);

  try {
    const entries = await apiFetch('/ledger?' + params.toString());
    const tbody = document.getElementById('ledger-body');
    if (!entries.length) {
      tbody.innerHTML = '<tr><td colspan="10" style="color:var(--muted)">No entries found.</td></tr>';
      return;
    }
    tbody.innerHTML = entries.map(e => {
      const net = parseFloat(e.net_value||0);
      return `<tr>
        <td>${e.id}</td>
        <td>${new Date(e.timestamp).toLocaleString()}</td>
        <td><b>${e.account_name}</b></td>
        <td><span class="badge-type">${e.entry_type}</span></td>
        <td class="neu">${fmt(e.asset_value)}</td>
        <td class="pos">${fmt(e.debit)}</td>
        <td class="neg">${fmt(e.credit)}</td>
        <td class="neg">${fmt(e.depreciation)}</td>
        <td class="${net>=0?'pos':'neg'}">${fmt(net)}</td>
        <td style="color:var(--muted)">${(e.description||'').substring(0,40)}</td>
      </tr>`;
    }).join('');
    document.getElementById('ledger-status').style.display = 'block';
    document.getElementById('ledger-status').textContent = `${entries.length} entries`;
  } catch(e) {
    document.getElementById('ledger-status').style.display = 'block';
    document.getElementById('ledger-status').textContent = 'Error: ' + e.message;
  }
}

async function postTransaction() {
  const payload = {
    account_name: document.getElementById('tx-account').value.trim(),
    debit:  parseFloat(document.getElementById('tx-debit').value)||0,
    credit: parseFloat(document.getElementById('tx-credit').value)||0,
    description: document.getElementById('tx-desc').value.trim(),
    reference_id: document.getElementById('tx-ref').value.trim()||null,
  };
  if (!payload.account_name || !payload.description) {
    document.getElementById('tx-status').textContent = '⚠ Account name and description are required.';
    return;
  }
  try {
    const r = await apiFetch('/transactions', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload),
    });
    document.getElementById('tx-status').textContent =
      `✓ Entry #${r.id} posted — Net: ${fmt(r.net_value)}`;
    await fetchReport();
    await fetchLedger();
  } catch(e) {
    document.getElementById('tx-status').textContent = '✗ Error: ' + e.message;
  }
}

// Auto-refresh every 60 s
async function init() {
  await fetchReport();
  await fetchPrices();
  await fetchLedger();
}
init();
setInterval(async () => { await fetchReport(); await fetchPrices(); }, 60000);
</script>
</body>
</html>"""

    # ── Routes ────────────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse, tags=["UI"])
    async def dashboard() -> str:
        """Serve the self-contained HTML dashboard."""
        return DASHBOARD_HTML

    @app.get("/api/ledger", tags=["Ledger"])
    async def api_ledger(
        account_name: Optional[str]  = Query(None),
        account_type: Optional[str]  = Query(None),
        asset_ticker: Optional[str]  = Query(None),
        entry_type:   Optional[str]  = Query(None),
        start_date:   Optional[str]  = Query(None),
        end_date:     Optional[str]  = Query(None),
        limit:        int            = Query(100, ge=1, le=10000),
    ):
        """Return ledger entries as JSON, with optional filters."""
        try:
            sd = datetime.fromisoformat(start_date) if start_date else None
            ed = datetime.fromisoformat(end_date)   if end_date   else None
            entries = engine.get_3d_ledger(
                account_name=account_name,
                account_type=account_type,
                asset_ticker=asset_ticker,
                start_date=sd,
                end_date=ed,
                limit=limit,
            )
            return [e.to_dict() for e in entries]
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            logger.exception("Ledger query failed")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/accounts", tags=["Accounts"])
    async def api_accounts(account_type: Optional[str] = Query(None)):
        """Return all accounts, optionally filtered by type."""
        return [a.to_dict() for a in database.list_accounts(account_type)]

    @app.post("/api/transactions", tags=["Ledger"], status_code=201)
    async def api_add_transaction(req: TransactionRequest):
        """Post a new manual transaction."""
        try:
            entry = engine.add_transaction(
                account_name=req.account_name,
                debit=req.debit,
                credit=req.credit,
                description=req.description,
                asset_value=req.asset_value,
                reference_id=req.reference_id,
                actor="api",
            )
            return entry.to_dict()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            logger.exception("Transaction post failed")
            raise HTTPException(status_code=500, detail=str(exc))

    @app.get("/api/report", tags=["Reports"])
    async def api_report():
        """Return the current balance-sheet / P&L summary."""
        return database.build_report_summary().to_dict()

    @app.post("/api/market/update", tags=["Market"])
    async def api_market_update(req: MarketUpdateRequest):
        """Fetch fresh prices, persist them, and revalue market-linked assets."""
        result  = md.run_market_update(req.tickers)
        entries = engine.revalue_market_assets(tickers=req.tickers, actor="api")
        return {
            **result,
            "revaluation_entries": len(entries),
        }

    @app.get("/api/prices", tags=["Market"])
    async def api_prices():
        """Return the latest cached price for every configured ticker."""
        prices = md.get_all_latest_prices()
        return {ticker: mp.to_dict() for ticker, mp in prices.items()}

    @app.get("/api/prices/{ticker}/history", tags=["Market"])
    async def api_price_history(ticker: str, limit: int = Query(100, ge=1, le=1000)):
        """Return price history for a single ticker."""
        history = database.get_price_history(ticker.upper(), limit=limit)
        return [mp.to_dict() for mp in history]

    @app.get("/api/audit", tags=["Audit"])
    async def api_audit(limit: int = Query(100, ge=1, le=1000)):
        """Return the most recent audit log entries."""
        return [log.to_dict() for log in database.list_audit_log(limit=limit)]


def start_server(host: str = config.WEB_HOST, port: int = config.WEB_PORT) -> None:
    """
    Start the FastAPI server with uvicorn.

    Call this from main.py; not invoked when the module is imported.
    """
    try:
        import uvicorn
    except ImportError:
        print("uvicorn not installed.  Run: pip install uvicorn")
        return

    if not FASTAPI_AVAILABLE:
        print("fastapi not installed.  Run: pip install fastapi")
        return

    uvicorn.run(
        "web_app:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
