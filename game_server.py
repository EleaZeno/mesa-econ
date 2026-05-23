# -*- coding: utf-8 -*-
"""
经济沙盘 v5.3 — 游戏服务器 (Flask + 内嵌HTML)
Run: python game_server.py
Open: http://127.0.0.1:7861
"""
from __future__ import annotations

import sys, os, io, time, json, csv, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')

from flask import Flask, Response, request
from model import EconomyModel

# Try to import Land
try:
    from model import Land
except ImportError:
    Land = None

app = Flask(__name__, static_folder=None)

# ══════════════════════════════════════════════════════════════
# 全局状态
# ══════════════════════════════════════════════════════════════
_lock = threading.RLock()
_model: EconomyModel | None = None
_hist_entries: list[dict] = []
_running = False
_stop_event = threading.Event()
_speed_ms = 800
_play_thread: threading.Thread | None = None


def _init_model(**kw):
    global _model, _hist_entries
    defaults = dict(
        n_households=25, n_firms=12, n_banks=2, n_traders=20,
        tax_rate=0.15, capital_gains_tax=0.10,
        productivity=1.0, subsidy=10.0, gov_purchase=50.0,
        shock_prob=0.02, seed=42,
    )
    defaults.update((k, v) for k, v in kw.items() if v is not None)
    with _lock:
        _model = EconomyModel(**defaults)
        if hasattr(_model, "_refresh_cache"):
            _model._refresh_cache()
    _hist_entries.clear()


def _get_state() -> dict:
    with _lock:
        if _model is None:
            return {"cycle": 0}
        m = _model
        emp = sum(1 for h in m.households if h.employed)
        nh = len(m.households)
        nb = len(m.banks) if m.banks else 0
        im = getattr(m, "interbank_market", None)

        land_price, land_vacant, land_foreclosed = 0, 0, 0
        if Land is not None and Land._registry:
            lands = Land._registry
            land_price = round(sum(l.price for l in lands) / max(1, len(lands)), 1)
            land_vacant = sum(1 for l in lands if l.owner is None)
            land_foreclosed = sum(1 for l in lands if l.foreclosed)

        m0 = round(
            sum(getattr(h, 'cash', 0) for h in m.households) +
            sum(getattr(f, 'cash', 0) for f in m.firms) +
            sum(getattr(b, 'reserves', 0) for b in m.banks) +
            sum(getattr(t, 'cash', 0) for t in m.traders) +
            getattr(m.government, 'cash', 0) +
            getattr(getattr(m, '_market_pool', None), 'cash', 0), 2)

        return {
            "cycle": m.cycle,
            "gdp": round(m.gdp),
            "unemp": round(m.unemployment * 100, 1),
            "price": round(m.avg_price, 2),
            "stock": round(m.stock_price, 1),
            "bdr": round(getattr(m, "bank_bad_debt_rate", 0.0) * 100, 1),
            "loans": round(m.total_loans_outstanding),
            "govt_revenue": round(m.govt_revenue),
            "bankrupt": m.bankrupt_count,
            "gini": round(m.gini, 3),
            "emp_rate": round(emp / nh * 100 if nh else 0, 1),
            "health_score": round(getattr(m, "health_score", 50)),
            "nfirms": len(m.firms),
            "n_banks": nb,
            "mkt_rate": round(sum(b.loan_rate + b.lending_spread for b in m.banks) / max(1, nb) * 100, 2) if nb else 0,
            "avg_deposit_rate": round(sum(b.deposit_rate for b in m.banks) / max(1, nb) * 100, 2) if nb else 0,
            "ca_pop": getattr(m, "city_a_pop", 0),
            "cb_pop": getattr(m, "city_b_pop", 0),
            "ca_gdp": round(getattr(m, "city_a_gdp", 0)),
            "cb_gdp": round(getattr(m, "city_b_gdp", 0)),
            "ca_unemp": round(getattr(m, "city_a_unemp", 0) * 100, 1),
            "cb_unemp": round(getattr(m, "city_b_unemp", 0) * 100, 1),
            "shibor": round((im.shibor * 100) if im else 0, 2),
            "interbank_vol": 0,
            "fear_premium": round(getattr(im, "fear_premium", 0.0), 4),
            "land_price": land_price,
            "land_vacant": land_vacant,
            "land_foreclosed": land_foreclosed,
            "m0": m0,
            "shock": getattr(m, "current_shock", "") or "",
            "tax_rate": round(m.tax_rate * 100),
            "productivity": round(m.productivity, 2),
            "gov_purchase": round(m.gov_purchase),
            "subsidy": round(m.subsidy),
        }


def _play_loop():
    global _running
    while not _stop_event.is_set() and _running:
        with _lock:
            if _model is not None:
                try:
                    _model.step()
                except RuntimeError as e:
                    print(f"[SFC] {e}", file=sys.stderr)
                    _running = False
                    break
        _hist_entries.append(_get_state())
        if len(_hist_entries) > 500:
            _hist_entries.pop(0)
        _stop_event.wait(_speed_ms / 1000.0)


# ══════════════════════════════════════════════════════════════
# 路由
# ══════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return HTML_TEMPLATE


@app.route("/api/state")
def api_state():
    return _get_state()


@app.route("/api/step", methods=["POST"])
def api_step():
    global _hist_entries
    with _lock:
        if _model is not None:
            try:
                _model.step()
            except RuntimeError:
                pass
    state = _get_state()
    _hist_entries.append(state)
    if len(_hist_entries) > 500:
        _hist_entries.pop(0)
    return state


@app.route("/api/start", methods=["POST"])
def api_start():
    global _running, _speed_ms, _play_thread, _stop_event
    data = request.get_json(silent=True) or {}
    _speed_ms = data.get("speed_ms", _speed_ms)
    if not _running:
        _running = True
        _stop_event.clear()
        _play_thread = threading.Thread(target=_play_loop, daemon=True)
        _play_thread.start()
    return {"status": "running", "speed_ms": _speed_ms}


@app.route("/api/stop", methods=["POST"])
def api_stop():
    global _running
    _running = False
    _stop_event.set()
    return {"status": "stopped"}


@app.route("/api/params", methods=["POST"])
def api_params():
    data = request.get_json(silent=True) or {}
    with _lock:
        if _model is not None:
            for key, val in data.items():
                if key == "tax_rate":
                    _model.tax_rate = max(0.0, min(0.45, float(val)))
                elif key == "productivity":
                    _model.productivity = max(0.1, min(5.0, float(val)))
                elif key == "gov_purchase":
                    _model.gov_purchase = max(0, float(val))
                elif key == "subsidy":
                    _model.subsidy = max(0, float(val))
                elif key == "capital_gains_tax":
                    _model.capital_gains_tax = max(0.0, min(0.5, float(val)))
                elif key == "city_a_tax":
                    from model import City, CITY_PARAMS
                    CITY_PARAMS[City.CITY_A]["tax_rate"] = max(0.0, min(0.45, float(val)))
                elif key == "city_b_tax":
                    from model import City, CITY_PARAMS
                    CITY_PARAMS[City.CITY_B]["tax_rate"] = max(0.0, min(0.45, float(val)))
    return {"status": "ok", "updated": list(data.keys())}


@app.route("/api/shock", methods=["POST"])
def api_shock():
    from model import SHOCK_EFFECTS
    data = request.get_json(silent=True) or {}
    shock_type = data.get("type", "")
    with _lock:
        if _model is not None and shock_type in SHOCK_EFFECTS:
            effect = SHOCK_EFFECTS[shock_type]
            prod_delta = effect.get("productivity", None)
            if callable(prod_delta):
                _model.productivity = max(0.1, min(5.0, prod_delta(_model.productivity)))
            if effect.get("bank_run", False):
                _model.systemic_risk = min(1.0, _model.systemic_risk + 0.2)
                for b in _model.banks:
                    run_amount = b.reserves * 0.3
                    if run_amount > 0:
                        b.deposits -= run_amount
                        per_capita = run_amount / max(1, len(_model.households))
                        for h in _model.households:
                            _model.ledger.transfer(b, h, per_capita)
                if hasattr(_model, "interbank_market") and _model.interbank_market:
                    _model.interbank_market.fear_premium += 0.03
            if shock_type == "oil_crisis" and hasattr(_model, "row"):
                _model.row.apply_external_shock("oil_crisis")
            if shock_type == "tech_breakthrough" and hasattr(_model, "row"):
                _model.row.apply_external_shock("commodity_glut")
            _model.current_shock = effect["desc"]
    return _get_state()


@app.route("/api/reset", methods=["POST"])
def api_reset():
    global _hist_entries
    _init_model()
    _hist_entries.clear()
    return _get_state()


@app.route("/api/export")
def api_export():
    global _hist_entries
    if not _hist_entries:
        _hist_entries = [_get_state()]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(_hist_entries[0].keys())
    for row in _hist_entries:
        writer.writerow(row.values())
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=mesa-econ_export.csv"}
    )


# ══════════════════════════════════════════════════════════════
# HTML 模板 (内嵌)
# ══════════════════════════════════════════════════════════════

HTML_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>经济沙盘 v5.3 — 指挥官模式</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Share+Tech+Mono&family=Noto+Sans+SC:wght@300;400;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg-deep: #080d14;
  --bg-panel: #0d1525;
  --bg-card: #111b2e;
  --border: #1a2a44;
  --cyan: #00e5ff;
  --cyan-dim: rgba(0,229,255,0.15);
  --amber: #ff9100;
  --amber-dim: rgba(255,145,0,0.15);
  --red: #ff1744;
  --red-dim: rgba(255,23,68,0.15);
  --green: #00e676;
  --green-dim: rgba(0,230,118,0.15);
  --purple: #b388ff;
  --text: #c8d6e5;
  --text-dim: #5c6e8a;
  --font-mono: 'Share Tech Mono', monospace;
  --font-head: 'Orbitron', sans-serif;
  --font-body: 'Noto Sans SC', sans-serif;
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  background: var(--bg-deep);
  color: var(--text);
  font-family: var(--font-body);
  overflow: hidden;
  height: 100vh;
  display: flex;
  flex-direction: column;
}
body::after {
  content: '';
  position: fixed; top:0; left:0; right:0; bottom:0;
  background: repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.03) 2px, rgba(0,0,0,0.03) 4px);
  pointer-events: none; z-index: 999;
}

/* Header */
header {
  background: linear-gradient(180deg, rgba(0,229,255,0.08) 0%, rgba(0,0,0,0) 100%);
  border-bottom: 1px solid var(--border);
  padding: 8px 20px;
  display: flex;
  align-items: center;
  gap: 20px;
  min-height: 52px;
  z-index: 10;
}
header .brand {
  font-family: var(--font-head);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 2px;
  color: var(--cyan);
  text-shadow: 0 0 20px var(--cyan-dim);
  white-space: nowrap;
}
header .sep { width:1px; height:24px; background:var(--border); }
header .stat { display:flex; flex-direction:column; align-items:center; min-width:60px; }
header .stat .label { font-size:8px; color:var(--text-dim); text-transform:uppercase; letter-spacing:1px; font-family:var(--font-head); }
header .stat .value { font-family:var(--font-mono); font-size:16px; font-weight:700; transition:color 0.5s; }
header .stat .value.good { color:var(--green); }
header .stat .value.warn { color:var(--amber); }
header .stat .value.bad { color:var(--red); }
header .health { margin-left:auto; display:flex; align-items:center; gap:6px; }
header .health .pulse { width:12px; height:12px; border-radius:50%; animation:pulse 2s infinite; }
@keyframes pulse { 0%,100%{ box-shadow:0 0 4px currentColor; transform:scale(1); } 50%{ box-shadow:0 0 14px currentColor; transform:scale(1.2); } }
header .health .score { font-family:var(--font-head); font-size:22px; font-weight:900; }
header .shock-banner { background:var(--red-dim); border:1px solid var(--red); color:var(--red); padding:3px 10px; border-radius:4px; font-size:11px; font-family:var(--font-head); letter-spacing:1px; animation:shake 0.5s; display:none; }
header .shock-banner.active { display:block; }
@keyframes shake { 0%,100%{ transform:translateX(0); } 25%{ transform:translateX(-3px); } 75%{ transform:translateX(3px); } }

/* Main */
main { flex:1; display:flex; overflow:hidden; position:relative; }
.charts-area { flex:1; display:grid; grid-template-columns:repeat(3,1fr); grid-template-rows:repeat(3,1fr); gap:6px; padding:6px; overflow-y:auto; }
.event-timeline { height:28px; margin:0 6px 6px; background:var(--bg-card); border:1px solid var(--border); border-radius:6px; display:flex; align-items:center; padding:0 12px; gap:8px; overflow-x:auto; font-size:10px; }
.event-timeline .dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.event-timeline .dot.red { background:#ff1744; box-shadow:0 0 6px #ff1744; }
.event-timeline .dot.green { background:#00e676; box-shadow:0 0 6px #00e676; }
.event-timeline .dot.amber { background:#ff9100; box-shadow:0 0 6px #ff9100; }
.event-dot {
  width:10px; height:10px; border-radius:50%; flex-shrink:0; position:relative; cursor:pointer;
}
.event-dot:hover::after {
  content:attr(data-tip); position:absolute; bottom:120%; left:50%; transform:translateX(-50%);
  background:var(--bg-card); border:1px solid var(--border); color:var(--text); padding:2px 8px;
  border-radius:4px; font-size:10px; white-space:nowrap; z-index:100;
}
.event-dot.red { background:#ff1744; }
.event-dot.green { background:#00e676; }
.event-dot.amber { background:#ff9100; }
.event-dot.cyan { background:var(--cyan); }
.chart-card { background:var(--bg-card); border:1px solid var(--border); border-radius:4px; padding:6px; display:flex; flex-direction:column; position:relative; overflow:hidden; }
.chart-card:hover { border-color:rgba(0,229,255,0.3); }
.chart-card .card-title { font-family:var(--font-head); font-size:9px; letter-spacing:1px; color:var(--text-dim); text-transform:uppercase; margin-bottom:3px; }
.chart-card canvas { flex:1; min-height:0; }

/* Control Panel */
aside#controls { width:0; overflow:hidden; background:var(--bg-panel); border-left:1px solid var(--border); transition:width 0.3s ease; display:flex; flex-direction:column; }
aside#controls.open { width:260px; }
aside#controls .panel-inner { width:260px; padding:10px; overflow-y:auto; flex:1; }
aside#controls h3 { font-family:var(--font-head); font-size:10px; letter-spacing:2px; color:var(--cyan); margin:8px 0 5px; text-transform:uppercase; }
aside#controls .param-row { margin-bottom:8px; }
aside#controls .param-row label { font-size:10px; color:var(--text-dim); display:flex; justify-content:space-between; margin-bottom:2px; }
aside#controls .param-row label .val { font-family:var(--font-mono); color:var(--cyan); }
aside#controls input[type=range] { -webkit-appearance:none; width:100%; height:3px; background:var(--border); border-radius:2px; outline:none; }
aside#controls input[type=range]::-webkit-slider-thumb { -webkit-appearance:none; width:14px; height:14px; background:var(--cyan); border-radius:50%; cursor:pointer; box-shadow:0 0 6px var(--cyan); }
aside#controls .btn-row { display:flex; gap:4px; flex-wrap:wrap; }
aside#controls button { background:var(--bg-card); border:1px solid var(--border); color:var(--text); font-family:var(--font-body); font-size:10px; padding:5px 8px; border-radius:3px; cursor:pointer; transition:all 0.2s; }
aside#controls button:hover { border-color:var(--cyan); color:var(--cyan); }
aside#controls button.danger:hover { border-color:var(--red); color:var(--red); }

/* Footer */
footer { background:var(--bg-panel); border-top:1px solid var(--border); padding:5px 12px; display:flex; align-items:center; gap:6px; min-height:40px; z-index:10; }
footer button { background:var(--bg-card); border:1px solid var(--border); color:var(--text); font-family:var(--font-head); font-size:10px; letter-spacing:1px; padding:6px 12px; border-radius:3px; cursor:pointer; transition:all 0.2s; text-transform:uppercase; }
footer button:hover { border-color:var(--cyan); color:var(--cyan); box-shadow:0 0 10px rgba(0,229,255,0.2); }
footer button.play { background:rgba(0,230,118,0.15); border-color:var(--green); color:var(--green); }
footer button.play:hover { box-shadow:0 0 12px rgba(0,230,118,0.3); }
footer button.play.running { background:rgba(255,145,0,0.15); border-color:var(--amber); color:var(--amber); }
footer .speed-group { display:flex; align-items:center; gap:5px; margin-left:auto; }
footer .speed-group label { font-size:9px; font-family:var(--font-head); color:var(--text-dim); letter-spacing:1px; }
footer .speed-group select { background:var(--bg-card); border:1px solid var(--border); color:var(--cyan); font-family:var(--font-mono); font-size:11px; padding:3px 6px; border-radius:3px; cursor:pointer; }

/* Event Log */
#event-log { position:fixed; bottom:50px; right:12px; z-index:100; display:flex; flex-direction:column-reverse; gap:4px; max-width:320px; }
#event-log .toast { background:var(--bg-card); border:1px solid var(--border); border-left:3px solid var(--cyan); padding:8px 10px; border-radius:3px; font-size:11px; animation:slideIn 0.3s ease; }
#event-log .toast.shock { border-left-color:var(--red); }
#event-log .toast.shock strong { color:var(--red); }
#event-log .toast .toast-time { font-family:var(--font-mono); font-size:8px; color:var(--text-dim); margin-bottom:2px; }
@keyframes slideIn { from{ transform:translateX(100%); opacity:0; } to{ transform:translateX(0); opacity:1; } }

@media (max-width:900px) { .charts-area{ grid-template-columns:repeat(2,1fr); } }
@media (max-width:600px) { .charts-area{ grid-template-columns:1fr; } header{ gap:8px; } header .stat{ min-width:45px; } }
</style>
</head>
<body>

<header>
  <span class="brand">🏙 经济沙盘 v5.3</span>
  <span class="sep"></span>
  <div class="stat"><span class="label">轮次</span><span class="value" id="h-cycle">0</span></div>
  <span class="sep"></span>
  <div class="stat"><span class="label">GDP</span><span class="value" id="h-gdp">—</span></div>
  <div class="stat"><span class="label">失业</span><span class="value" id="h-unemp">—</span></div>
  <div class="stat"><span class="label">基尼</span><span class="value" id="h-gini">—</span></div>
  <div class="stat"><span class="label">企业</span><span class="value" id="h-firms">—</span></div>
  <div class="stat"><span class="label">利率</span><span class="value" id="h-rate">—</span></div>
  <span class="sep"></span>
  <div class="health">
    <div class="pulse" id="h-pulse" style="color:var(--cyan)"></div>
    <div class="score" id="h-score">—</div>
  </div>
  <div class="shock-banner" id="shock-banner">⚠ <span id="shock-text"></span></div>
</header>

<main>
  <div class="charts-area" id="charts-grid">
    <div class="chart-card"><span class="card-title">📈 GDP 趋势</span><canvas id="ch-gdp"></canvas></div>
    <div class="chart-card"><span class="card-title">📉 失业率 %</span><canvas id="ch-unemp"></canvas></div>
    <div class="chart-card"><span class="card-title">💹 物价 & 股价</span><canvas id="ch-price"></canvas></div>
    <div class="chart-card"><span class="card-title">⚖ 基尼系数</span><canvas id="ch-gini"></canvas></div>
    <div class="chart-card"><span class="card-title">🏠 房价 & M0</span><canvas id="ch-land"></canvas></div>
    <div class="chart-card"><span class="card-title">🏦 坏账率</span><canvas id="ch-bank"></canvas></div>
    <div class="chart-card"><span class="card-title">🏙️ 双城 GDP</span><canvas id="ch-city"></canvas></div>
  </div>
  <div class="event-timeline" id="event-timeline"><span style="color:var(--text-dim);flex-shrink:0;">📋 事件轴</span></div>
  <aside id="controls">
    <div class="panel-inner">
      <h3>💰 宏观政策</h3>
      <div class="param-row"><label>税率 <span class="val" id="pv-tax">15%</span></label><input type="range" id="p-tax" min="5" max="30" value="15" data-key="tax_rate" data-scale="0.01"></div>
      <div class="param-row"><label>生产率 <span class="val" id="pv-prod">1.0</span></label><input type="range" id="p-prod" min="50" max="200" value="100" data-key="productivity" data-scale="0.01"></div>
      <div class="param-row"><label>政府购买 <span class="val" id="pv-gov">50</span></label><input type="range" id="p-gov" min="0" max="500" value="50" data-key="gov_purchase" data-scale="1"></div>
      <div class="param-row"><label>失业补贴 <span class="val" id="pv-sub">10</span></label><input type="range" id="p-sub" min="0" max="100" value="10" data-key="subsidy" data-scale="1"></div>

      <h3>🏙 城市政策</h3>
      <div class="param-row"><label>城市A税率 <span class="val" id="pv-cat">12%</span></label><input type="range" id="p-cat" min="5" max="30" value="12" data-key="city_a_tax" data-scale="0.01"></div>
      <div class="param-row"><label>城市B税率 <span class="val" id="pv-cbt">18%</span></label><input type="range" id="p-cbt" min="5" max="30" value="18" data-key="city_b_tax" data-scale="0.01"></div>

      <h3>⚡ 外部冲击</h3>
      <div class="btn-row">
        <button onclick="injectShock('oil_crisis')">🛢 石油危机</button>
        <button onclick="injectShock('tech_breakthrough')">💡 技术突破</button>
        <button onclick="injectShock('demand_crash')">📉 需求骤降</button>
        <button onclick="injectShock('trade_war')">⚔ 贸易战</button>
        <button onclick="injectShock('bank_panic')">🏦 银行恐慌</button>
        <button onclick="injectShock('economic_recovery')">🌱 经济复苏</button>
      </div>

      <h3>🔧 操作</h3>
      <div class="btn-row">
        <button class="danger" onclick="resetModel()">🔄 重置沙盘</button>
      </div>
    </div>
  </aside>
</main>

<footer>
  <button class="play" id="btn-play" onclick="togglePlay()">▶ 开始</button>
  <button onclick="stepModel()">⏭ 单步</button>
  <button onclick="togglePanel()" id="btn-panel">📊 参数</button>
  <button onclick="exportCSV()">📥 导出</button>
  <div class="speed-group">
    <label>速度</label>
    <select id="speed-select" onchange="updateSpeed()">
      <option value="2000">慢速</option>
      <option value="800" selected>正常</option>
      <option value="300">快速</option>
      <option value="80">极速</option>
    </select>
  </div>
</footer>

<div id="event-log"></div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script>
const MAX_POINTS=100;
let running=false, playTimer=null, speedMs=800, history=[], charts={}, currentShock='', events=[], gdp_peak=0, last_land=0, last_bdr=0;

const chartConfigs = {
  gdp:   { el:'ch-gdp',   datasets:[{label:'GDP', data:[], borderColor:'#00e5ff', backgroundColor:'rgba(0,229,255,0.08)', fill:true, tension:0.3, pointRadius:0}] },
  unemp: { el:'ch-unemp', datasets:[{label:'失业率%', data:[], borderColor:'#ff9100', backgroundColor:'rgba(255,145,0,0.08)', fill:true, tension:0.3, pointRadius:0}] },
  price: { el:'ch-price', datasets:[{label:'物价', data:[], borderColor:'#b388ff', tension:0.3, pointRadius:0, yAxisID:'y'},{label:'股价', data:[], borderColor:'#00e676', tension:0.3, pointRadius:0, yAxisID:'y1', borderDash:[4,2]}] },
  gini:  { el:'ch-gini',  datasets:[{label:'基尼', data:[], borderColor:'#ff1744', backgroundColor:'rgba(255,23,68,0.08)', fill:true, tension:0.3, pointRadius:0}] },
  land:  { el:'ch-land',  datasets:[{label:'房价', data:[], borderColor:'#ff9100', tension:0.3, pointRadius:0, yAxisID:'y'},{label:'M0/100', data:[], borderColor:'#00e5ff', tension:0.3, pointRadius:0, yAxisID:'y1', borderDash:[4,2]}] },
  bank:  { el:'ch-bank',  datasets:[{label:'坏账率%', data:[], borderColor:'#ff1744', tension:0.3, pointRadius:0}] },
  city:  { el:'ch-city',  datasets:[{label:'A城GDP', data:[], borderColor:'#00e5ff', backgroundColor:'rgba(0,229,255,0.06)', fill:true, tension:0.3, pointRadius:0},{label:'B城GDP', data:[], borderColor:'#b388ff', backgroundColor:'rgba(179,136,255,0.06)', fill:true, tension:0.3, pointRadius:0}] }
};

function initCharts() {
  const commonOpts = { responsive:true, maintainAspectRatio:false, animation:{duration:300}, interaction:{intersect:false, mode:'index'}, plugins:{legend:{labels:{color:'#5c6e8a', font:{size:9, family:'"Share Tech Mono"'}, boxWidth:10, padding:6}}}, scales:{x:{display:true, ticks:{color:'#3a4a66', font:{size:8}, maxTicksLimit:5}, grid:{color:'rgba(26,42,68,0.4)'}}, y:{display:true, ticks:{color:'#3a4a66', font:{size:8}}, grid:{color:'rgba(26,42,68,0.4)'}}} };
  for (const [key, cfg] of Object.entries(chartConfigs)) {
    const ctx = document.getElementById(cfg.el).getContext('2d');
    let opts = JSON.parse(JSON.stringify(commonOpts));
    if (key === 'price' || key === 'land') { opts.scales.y1 = { display:true, position:'right', ticks:{color:'#3a4a66', font:{size:8}}, grid:{display:false} }; }
    charts[key] = new Chart(ctx, { type:'line', data:{labels:[], datasets:cfg.datasets}, options:opts });
  }
}

function updateCharts(s) {
  history.push(s); if (history.length > MAX_POINTS) history.shift();
  const labels = history.map(h => h.cycle);
  charts.gdp.data.labels = labels; charts.gdp.data.datasets[0].data = history.map(h => h.gdp); charts.gdp.update('none');
  charts.unemp.data.labels = labels; charts.unemp.data.datasets[0].data = history.map(h => h.unemp); charts.unemp.update('none');
  charts.price.data.labels = labels; charts.price.data.datasets[0].data = history.map(h => h.price); charts.price.data.datasets[1].data = history.map(h => h.stock); charts.price.update('none');
  charts.gini.data.labels = labels; charts.gini.data.datasets[0].data = history.map(h => h.gini); charts.gini.update('none');
  charts.land.data.labels = labels; charts.land.data.datasets[0].data = history.map(h => h.land_price); charts.land.data.datasets[1].data = history.map(h => (h.m0||0)/100); charts.land.update('none');
  charts.bank.data.labels = labels; charts.bank.data.datasets[0].data = history.map(h => h.bdr); charts.bank.update('none');
  charts.city.data.labels = labels; charts.city.data.datasets[0].data = history.map(h => h.ca_gdp||0); charts.city.data.datasets[1].data = history.map(h => h.cb_gdp||0); charts.city.update('none'); updateEventTimeline(s);
}

function updateHeader(s) {
  document.getElementById('h-cycle').textContent = s.cycle;
  document.getElementById('h-gdp').textContent = '¥' + (s.gdp||0).toLocaleString();
  document.getElementById('h-unemp').textContent = (s.unemp||0).toFixed(1) + '%';
  document.getElementById('h-gini').textContent = (s.gini||0).toFixed(3);
  document.getElementById('h-firms').textContent = s.nfirms;
  document.getElementById('h-rate').textContent = (s.mkt_rate||0).toFixed(1) + '%';
  const score = s.health_score || 50;
  const scoreEl = document.getElementById('h-score'); const pulseEl = document.getElementById('h-pulse');
  scoreEl.textContent = Math.round(score);
  if (score >= 70) { scoreEl.style.color = 'var(--green)'; pulseEl.style.color = 'var(--green)'; }
  else if (score >= 40) { scoreEl.style.color = 'var(--amber)'; pulseEl.style.color = 'var(--amber)'; }
  else { scoreEl.style.color = 'var(--red)'; pulseEl.style.color = 'var(--red)'; }
  const unempEl = document.getElementById('h-unemp');
  unempEl.className = 'value ' + (s.unemp <= 5 ? 'good' : s.unemp <= 15 ? 'warn' : 'bad');
  const banner = document.getElementById('shock-banner');
  if (s.shock && s.shock !== currentShock) { document.getElementById('shock-text').textContent = s.shock; banner.classList.add('active'); addToast(s.shock, 'shock'); currentShock = s.shock || ''; }
  else if (!s.shock) { banner.classList.remove('active'); currentShock = ''; }
}

function addToast(msg, type='') {
  const log = document.getElementById('event-log');
  const toast = document.createElement('div');
  toast.className = 'toast ' + type;
  const now = new Date();
  toast.innerHTML = '<div class="toast-time">' + now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0') + '</div>' + (type === 'shock' ? '<strong>⚡ </strong>' : '') + msg;
  log.appendChild(toast);
  setTimeout(() => { toast.style.opacity = '0'; toast.style.transition = 'opacity 0.5s'; setTimeout(() => toast.remove(), 500); }, 6000);
  while (log.children.length > 4) log.lastChild.remove();
}

function updateEventTimeline(s) {
  if (s.gdp > gdp_peak) gdp_peak = s.gdp;
  if (gdp_peak > 0 && s.gdp < gdp_peak * 0.8 && s.cycle > 10) {
    events.push({cycle:s.cycle, type:'amber', label:'衰退'}); gdp_peak = s.gdp; renderEvents();
  }
  if (s.unemp < 3 && history.length > 1 && history[history.length-2].unemp >= 3) {
    events.push({cycle:s.cycle, type:'green', label:'全员就业'}); renderEvents();
  }
  if (s.land_price > 200 && last_land <= 200 && last_land > 0) {
    events.push({cycle:s.cycle, type:'cyan', label:'房价过热'}); renderEvents();
  }
  if (s.bdr > 15 && last_bdr <= 15 && last_bdr > 0) {
    events.push({cycle:s.cycle, type:'red', label:'银行危机'}); renderEvents();
  }
  last_land = s.land_price || 0; last_bdr = s.bdr || 0;
}
function renderEvents() {
  const tl = document.getElementById('event-timeline');
  const last = events[events.length-1]; if (!last) return;
  const dot = document.createElement('div'); dot.className = 'event-dot ' + last.type;
  dot.dataset.tip = '#' + last.cycle + ' ' + last.label; tl.appendChild(dot);
  if (tl.children.length > 30) tl.removeChild(tl.children[1]);
}

async function fetchState() { try { const r = await fetch('/api/state'); const s = await r.json(); updateHeader(s); updateCharts(s); return s; } catch(e) {} }
async function stepModel() { const r = await fetch('/api/step', {method:'POST'}); const s = await r.json(); updateHeader(s); updateCharts(s); }

async function togglePlay() {
  const btn = document.getElementById('btn-play');
  if (running) {
    await fetch('/api/stop', {method:'POST'});
    running = false; if (playTimer) { clearInterval(playTimer); playTimer = null; }
    btn.textContent = '▶ 开始'; btn.classList.remove('running');
  } else {
    await fetch('/api/start', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({speed_ms:speedMs})});
    running = true; btn.textContent = '⏸ 暂停'; btn.classList.add('running'); startPolling();
  }
}
function startPolling() { if (playTimer) clearInterval(playTimer); playTimer = setInterval(async () => { if (!running) { clearInterval(playTimer); playTimer = null; return; } await fetchState(); }, speedMs + 50); }
function updateSpeed() { speedMs = parseInt(document.getElementById('speed-select').value); if (running) { fetch('/api/start', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({speed_ms:speedMs})}); startPolling(); } }

async function injectShock(type) { const r = await fetch('/api/shock', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({type})}); const s = await r.json(); updateHeader(s); updateCharts(s); }
async function resetModel() { if (!confirm('确定要重置沙盘吗？')) return; running = false; if (playTimer) { clearInterval(playTimer); playTimer = null; } document.getElementById('btn-play').textContent = '▶ 开始'; document.getElementById('btn-play').classList.remove('running'); history = []; currentShock = ''; for (const k of Object.keys(charts)) { charts[k].data.labels = []; charts[k].data.datasets.forEach(ds => ds.data = []); charts[k].update(); } await fetch('/api/reset', {method:'POST'}); await fetchState(); }
function togglePanel() { document.getElementById('controls').classList.toggle('open'); }
async function exportCSV() { const r = await fetch('/api/export'); const blob = await r.blob(); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = 'mesa-econ_' + new Date().toISOString().slice(0,10) + '.csv'; a.click(); URL.revokeObjectURL(url); }

document.querySelectorAll('#controls input[type=range]').forEach(slider => {
  slider.addEventListener('input', function() {
    const key = this.dataset.key; const scale = parseFloat(this.dataset.scale); const val = this.value * scale;
    const labelId = 'pv-' + this.id.replace('p-', ''); const labelEl = document.getElementById(labelId);
    if (labelEl) { if (key.includes('tax')) { labelEl.textContent = this.value + '%'; } else if (key === 'productivity') { labelEl.textContent = val.toFixed(2); } else { labelEl.textContent = Math.round(val); } }
  });
  slider.addEventListener('change', async function() {
    const key = this.dataset.key; const scale = parseFloat(this.dataset.scale); const val = this.value * scale;
    await fetch('/api/params', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({[key]: val})});
  });
});

initCharts(); fetchState();
document.addEventListener('keydown', e => { if (e.key === ' ') { e.preventDefault(); togglePlay(); } if (e.key === 'ArrowRight') { e.preventDefault(); stepModel(); } });
</script>
</body>
</html>'''


# ══════════════════════════════════════════════════════════════
# 启动
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    _init_model()
    print(f"\n🚀 经济沙盘 v5.3 — 指挥官模式")
    print(f"   打开浏览器访问: http://127.0.0.1:7861\n")
    app.run(host="127.0.0.1", port=7861, debug=False, threaded=True)