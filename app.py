import io, json, threading
from flask import Flask, request, Response, jsonify
from model import EconomyModel

app = Flask(__name__)
_lock = threading.Lock()
_md = None
_hist = []
_hl = threading.Lock()
_run = False
_stop = threading.Event()
_thr = None
_feedback = None  # 用于页面顶部提示


def init(**kw):
    global _md, _hist, _run, _thr
    d = dict(n_households=20, n_firms=10, n_traders=20,
             tax_rate=0.15, base_interest_rate=0.05, min_wage=7,
             productivity=1.0, subsidy=0.0, gov_purchase=0.0,
             capital_gains_tax=0.10, shock_prob=0.02)
    d.update((k, v) for k, v in kw.items() if v is not None)
    _run = False
    if _thr:
        _stop.set()
    with _lock:
        _md = EconomyModel(**d)
    _hist.clear()
    _rec()


def step():
    with _lock:
        if _md:
            _md.step()
    _rec()


def _rec(locked=False):
    _own_lock = False
    if not locked:
        _own_lock = _lock.acquire(timeout=1.0)
        if not _own_lock:
            return
    try:
        if _md is None:
            return
        m = _md
        emp = sum(1 for h in m.households if h.employed)
        nh = len(m.households)
        ent = {
            "cycle": m.cycle,
            "gdp": round(m.gdp),
            "unemp": round(m.unemployment * 100, 1),
            "price": round(getattr(m, "price_index", 100.0), 1),
            "stock": round(m.stock_price, 1),
            "vol": round(getattr(m, "stock_volatility", 0.0), 3),
            "bdr": round(getattr(m, "bank_bad_debt_rate", 0.0) * 100, 1),
            "loans": round(m.total_loans_outstanding),
            "rev": round(m.govt_revenue),
            "bankrupt": m.bankrupt_count,
            "gini": round(m.gini, 3),
            "emp": emp, "nh": nh,
            "rate": round(emp / nh * 100 if nh else 0, 1),
        }
    except Exception:
        ent = {"cycle": getattr(_md, "cycle", 0)}
    finally:
        if _own_lock:
            _lock.release()
    with _hl:
        _hist.append(ent)
        if len(_hist) > 500:
            del _hist[:-500]


def _svg(vals, color="#3b82f6", w=640, h=180):
    if not vals:
        vals = [0]
    n = len(vals)
    LP, RP, TP, BP = 48, 8, 8, 28
    W = w - LP - RP
    H = h - TP - BP
    mn = min(vals)
    mx = max(vals)
    rng = mx - mn if mx != mn else 1
    parts = []
    parts.append('<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="' + str(h) + '" viewBox="0 0 ' + str(w) + ' ' + str(h) + '">')
    for i in range(5):
        y = TP + H * i / 4
        v = mx - rng * i / 4
        parts.append('<line x1="' + str(LP) + '" y1="' + str(round(y, 1)) + '" x2="' + str(LP + W) + '" y2="' + str(round(y, 1)) + '" stroke="#e2e8f0"/>')
        parts.append('<text x="' + str(LP - 4) + '" y="' + str(round(y + 3, 1)) + '" text-anchor="end" font-size="10" fill="#94a3b8">' + str(round(v, 0)) + '</text>')
    pts = []
    for i, v in enumerate(vals):
        x = LP + W * i // max(1, n - 1)
        y = TP + H * (1 - (v - mn) / rng)
        pts.append(str(round(x)) + "," + str(round(y, 1)))
    parts.append('<polyline points="' + " ".join(pts) + '" fill="none" stroke="' + color + '" stroke-width="2" stroke-linejoin="round"/>')
    fp = ["M " + str(LP) + " " + str(TP + H)]
    for i, v in enumerate(vals):
        x = LP + W * i // max(1, n - 1)
        y = TP + H * (1 - (v - mn) / rng)
        fp.append("L " + str(round(x)) + " " + str(round(y, 1)))
    fp.append("L " + str(LP + W) + " " + str(TP + H) + " Z")
    parts.append('<path d="' + " ".join(fp) + '" fill="' + color + '" opacity="0.1"/>')
    parts.append('<line x1="' + str(LP) + '" y1="' + str(TP) + '" x2="' + str(LP + W) + '" y2="' + str(TP) + '" stroke="#cbd5e1"/>')
    parts.append('<line x1="' + str(LP) + '" y1="' + str(TP) + '" x2="' + str(LP) + '" y2="' + str(TP + H) + '" stroke="#cbd5e1"/>')
    parts.append('</svg>')
    return "".join(parts)


SCENARIOS = [
    ("ecocrisis", "经济危机(利率15%,税率25%)"),
    ("loose", "宽松政策(利率1%,税率5%)"),
    ("hightax", "高税高补贴(税率40%,工资15)"),
    ("freemarket", "自由市场(利率2%,税率5%)"),
    ("govstim", "政府刺激(购买150)"),
    ("fin_cris", "金融危机(利率20%,税率30%)"),
]

SCEN_MAP = {
    "ecocrisis": dict(base_interest_rate=0.15, tax_rate=0.25),
    "loose": dict(base_interest_rate=0.01, tax_rate=0.05, subsidy=15.0),
    "hightax": dict(tax_rate=0.40, subsidy=20.0, min_wage=15.0),
    "freemarket": dict(tax_rate=0.05, base_interest_rate=0.02),
    "govstim": dict(gov_purchase=150.0, tax_rate=0.12, subsidy=8.0),
    "fin_cris": dict(base_interest_rate=0.20, tax_rate=0.30, shock_prob=0.15),
}

SLIDERS = [
    ("n_households", "家庭数量", 5, 80, 5),
    ("n_firms", "企业数量", 3, 40, 1),
    ("tax_rate", "所得税率(%)", 0, 45, 1),
    ("base_interest_rate", "基准利率(%)", 0, 25, 0.5),
    ("min_wage", "最低工资", 0, 20, 0.5),
    ("productivity", "全要素生产率", 0.1, 3, 0.1),
    ("gov_purchase", "政府购买", 0, 200, 5),
    ("shock_prob", "冲击概率(%)", 0, 20, 1),
]

CHART_KEYS = [
    ("gdp", "GDP"), ("unemp", "失业率(%)"), ("gini", "基尼系数"),
    ("stock", "股价"), ("price", "物价指数"), ("vol", "波动率"),
    ("bdr", "坏账率(%)"), ("loans", "信贷总量"),
]

CHART_CLR = {
    "gdp": "#16a34a", "unemp": "#dc2626", "gini": "#9333ea",
    "stock": "#d97706", "price": "#64748b", "vol": "#f97316",
    "bdr": "#ec4899", "loans": "#0891b2",
}

CSS = """*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#f8fafc;color:#1e293b;padding:16px;font-size:14px;max-width:960px;margin:0 auto}
h1{font-size:20px;margin-bottom:12px}
h2{font-size:11px;color:#64748b;margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:14px;margin-bottom:10px}
.ctrl{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
.btn{border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:13px;font-family:inherit}
.bg{background:#16a34a;color:#fff}.bg:hover{background:#15803d}
.bb{background:#3b82f6;color:#fff}.bb:hover{background:#2563eb}
.bo{background:#d97706;color:#fff}.bo:hover{background:#b45309}
.br{background:#dc2626;color:#fff}.br:hover{background:#b91c1c}
.c2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
@media(max-width:600px){.c2{grid-template-columns:1fr}}
.sc{display:grid;grid-template-columns:repeat(auto-fill,minmax(115px,1fr));gap:8px}
.sc>div{background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:8px 10px}
.sl{font-size:10px;color:#94a3b8;text-transform:uppercase}
.sv{font-size:18px;font-weight:700;color:#0f172a;margin-top:2px}
.dw{color:#d97706}.dr{color:#dc2626}
.tabs{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px}
.tb{background:#e2e8f0;color:#475569;border:none;padding:5px 10px;border-radius:5px;cursor:pointer;font-size:12px;font-family:inherit;margin:2px}
.ta{background:#3b82f6;color:#fff;border:none;padding:5px 10px;border-radius:5px;cursor:pointer;font-size:12px;font-family:inherit;margin:2px}
select{width:100%;padding:6px;border-radius:6px;border:1px solid #cbd5e1;background:#fff;font-size:13px;font-family:inherit;margin-bottom:8px}
.srow{margin-bottom:6px}
.slbl{display:flex;justify-content:space-between;margin-bottom:2px;font-size:13px;color:#475569}
input[type=range]{width:100%;accent-color:#3b82f6;height:4px}
.alist{max-height:170px;overflow-y:auto;font-size:12px;line-height:1.7}
.cyc{color:#94a3b8;margin-left:auto}"""


def _page():
    global _feedback
    app_param = _feedback
    _feedback = None  # 消费掉，只显示一次
    with _lock:
        if _md is None:
            cyc, last = 0, {}
        else:
            cyc = _md.cycle
            try:
                emp = sum(1 for h in _md.households if h.employed)
                nh = len(_md.households)
                last = {
                    "gdp": round(_md.gdp),
                    "unemp": round(_md.unemployment * 100, 1),
                    "price": round(getattr(_md, "price_index", 100.0), 1),
                    "stock": round(_md.stock_price, 1),
                    "vol": round(getattr(_md, "stock_volatility", 0.0), 3),
                    "bdr": round(getattr(_md, "bank_bad_debt_rate", 0.0) * 100, 1),
                    "loans": round(_md.total_loans_outstanding),
                    "rev": round(_md.govt_revenue),
                    "bankrupt": _md.bankrupt_count,
                    "gini": round(_md.gini, 3),
                    "emp": emp, "nh": nh,
                    "rate": round(emp / nh * 100 if nh else 0, 1),
                }
            except Exception:
                last = {}
    with _hl:
        hist = list(_hist)

    ck = request.args.get("chart", "gdp")
    cv = [h.get(ck, 0) for h in hist]
    svg_html = _svg(cv, color=CHART_CLR.get(ck, "#3b82f6"))

    stats = [
        ("gdp", "GDP", "int", False),
        ("unemp", "失业率", "pct", last.get("unemp", 0) > 15),
        ("price", "物价", "dec1", False),
        ("stock", "股价", "dec1", False),
        ("gini", "基尼", "dec3", last.get("gini", 0) > 0.4),
        ("rate", "就业率", "pct", False),
        ("vol", "波动率", "dec3", last.get("vol", 0) > 0.3),
        ("bdr", "坏账率", "pct", last.get("bdr", 0) > 10),
        ("bankrupt", "破产", "int", last.get("bankrupt", 0) > 0),
        ("loans", "贷款", "int", False),
        ("rev", "政府收入", "int", False),
    ]

    atype = request.args.get("atype", "household")
    agents = ""
    with _lock:
        if _md:
            if atype == "household":
                tm = {"low": "低收入", "middle": "中收入", "high": "高收入"}
                for h in _md.households:
                    tier = tm.get(str(getattr(h, "income_tier", "")), "?")
                    st2 = "就业" if h.employed else "失业"
                    agents += ('<div style="font-family:monospace;font-size:11px;background:#f8fafc;'
                               'padding:2px 8px;margin-bottom:2px;border-radius:4px">H' +
                               str(h.unique_id) + " 现:" + str(int(h.cash)) +
                               " 富:" + str(int(h.wealth)) + " " + st2 +
                               "/" + str(int(h.salary)) + " 股:" +
                               str(getattr(h, "shares_owned", 0)) + " [" + tier + "]</div>")
            elif atype == "firm":
                for f in _md.firms:
                    agents += ('<div style="font-family:monospace;font-size:11px;background:#f8fafc;'
                               'padding:2px 8px;margin-bottom:2px;border-radius:4px">F' +
                               str(f.unique_id) + " 现:" + str(int(f.cash)) +
                               " 富:" + str(int(f.wealth)) + " 产:" +
                               str(round(f.production, 1)) + " 库:" +
                               str(round(f.inventory, 1)) + " 员:" + str(f.employees) + "</div>")
            elif atype == "trader":
                for t in _md.traders:
                    agents += ('<div style="font-family:monospace;font-size:11px;background:#f8fafc;'
                               'padding:2px 8px;margin-bottom:2px;border-radius:4px">T' +
                               str(t.unique_id) + " 现:" + str(int(t.cash)) +
                               " 富:" + str(int(t.wealth)) + " 股:" +
                               str(getattr(t, "shares", 0)) + "</div>")
            elif atype == "bank":
                for b in _md.banks:
                    agents += ('<div style="font-family:monospace;font-size:11px;background:#f8fafc;'
                               'padding:2px 8px;margin-bottom:2px;border-radius:4px">B' +
                               str(b.unique_id) + " 准:" +
                               str(int(getattr(b, "reserves", 0))) +
                               " 富:" + str(int(b.wealth)) + "</div>")

    p = []
    p.append("<!DOCTYPE html><html lang=zh><head><meta charset=utf-8>")
    p.append('<meta name=viewport content="width=device-width,initial-scale=1">')
    p.append("<title>经济沙盘 v3.3</title>")
    p.append("<style>" + CSS + "</style></head><body>")
    p.append('<h1>经济沙盘 <span style="color:#3b82f6">v3.3</span> <span class=cyc id=cycle>第 ' + str(cyc) + ' 轮</span></h1>')
    if app_param:
        p.append('<div style="background:#dcfce7;color:#166534;padding:8px 12px;border-radius:6px;margin-bottom:12px;font-size:13px">' + app_param + '</div>')
    p.append('<div class="card ctrl">')
    p.append('<form method=get style=display:inline><button type=submit name=action value=play class="btn bg">播放</button></form>')
    p.append('<form method=get style=display:inline><button type=submit name=action value=pause class="btn bo">暂停</button></form>')
    p.append('<form method=get style=display:inline><button type=submit name=action value=step class="btn bb">单步</button></form>')
    p.append('<form method=get style=display:inline><button type=submit name=action value=reset class="btn br">重置</button></form>')
    p.append('<span class=cyc style="margin-left:12px" id=cycle2>第 ' + str(cyc) + ' 轮</span>')
    p.append('</div>')
    p.append('<div class=card><h2>宏观指标</h2><div class=sc id=stats>')
    for k, lbl, typ, warn in stats:
        v = last.get(k, 0)
        cls = "dw" if warn else ""
        if typ == "int":
            fv = "{:,}".format(int(v))
        elif typ == "pct":
            fv = "{:.1f}%".format(v)
        elif typ == "dec1":
            fv = "{:.1f}".format(v)
        else:
            fv = "{:.3f}".format(v)
        p.append('<div><div class=sl>' + lbl + '</div><div class="sv ' + cls + '">' + fv + '</div></div>')
    p.append('</div></div>')

    # ── 经济健康分 + 手动冲击 ──────────────────────────
    p.append('<div class=card><h2>经济健康分</h2>')
    p.append('<div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">')
    p.append('<div id=hscore style="font-size:40px;font-weight:800;color:#3b82f6;line-height:1">--</div>')
    p.append('<div><div id=hlevel style="font-size:14px;font-weight:600;color:#64748b">--</div>')
    p.append('<div style="font-size:11px;color:#94a3b8;margin-top:2px" id=hbreakdown></div></div></div>')
    p.append('<div style="height:6px;background:#e2e8f0;border-radius:3px;overflow:hidden">')
    p.append('<div id=hbar style="height:100%;width:0%;background:#3b82f6;transition:width 0.4s,background 0.4s;border-radius:3px"></div></div>')
    p.append('</div>')

    # 手动冲击
    p.append('<div class=card><h2>触发冲击</h2>')
    p.append('<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px">')
    for sn, sl in [("oil_crisis","石油危机"),("tech_breakthrough","技术突破"),("demand_slowdown","需求骤降"),("trade_war","贸易战"),("banking_panic","银行恐慌"),("recovery","经济复苏")]:
        p.append('<button type=button class="btn bb" style="font-size:11px;padding:4px 8px" onclick="triggerShock(\'' + sn + '\')">' + sl + '</button>')
    p.append('</div>')
    p.append('<div id=shock-msg style="font-size:11px;color:#ef4444;min-height:16px"></div>')
    p.append('</div>')

    # ── 单体追踪 ──────────────────────────────────────
    p.append('<div class=card><h2>单体追踪</h2>')
    p.append('<div style="display:flex;gap:8px;margin-bottom:8px;align-items:center">')
    p.append('<select id=track-type style="flex:1;padding:4px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px">')
    p.append('<option value=household>家庭</option><option value=firm>企业</option>')
    p.append('<option value=trader>交易者</option><option value=bank>银行</option></select>')
    p.append('<input id=track-id placeholder="ID号" style="width:60px;padding:4px;border:1px solid #e2e8f0;border-radius:6px;font-size:12px">')
    p.append('<button type=button class="btn bb" style="font-size:12px" onclick="trackAgent()">追踪</button>')
    p.append('</div>')
    p.append('<div id=track-info style="font-family:monospace;font-size:11px;background:#f8fafc;padding:6px 8px;border-radius:6px;min-height:60px;color:#334155"></div>')
    p.append('<div id=track-chart style="margin-top:6px"></div>')
    p.append('</div>')

    p.append('<div class=card><h2>图表</h2><div class=tabs id=chart-tabs>')
    for fk, fl in CHART_KEYS:
        cls = "ta" if fk == ck else "tb"
        p.append('<button type=button id=ctab-' + fk + ' class=' + cls + ' onclick="switchChart(\'' + fk + '\')">' + fl + '</button>')
    p.append('</div><form method=get><input type=hidden name=chart value=' + ck + ' id=chart-key>')
    p.append('<div id=chart>' + svg_html + '</div></form></div>')
    p.append('<div class=c2>')
    p.append('<div class=card><h2>经济参数</h2><form method=get>')
    for k, lbl, mn, mx, st_v in SLIDERS:
        v = getattr(_md, k, None) if _md else None
        if k in ("tax_rate", "base_interest_rate", "shock_prob"):
            dv = round((v or 0) * 100, 1)
            unit = "%"
        else:
            dv = v if v is not None else (20 if k == "n_households" else 10 if k == "n_firms" else 0)
            unit = ""
        p.append('<div class=srow><div class=slbl><span>' + lbl + '</span><b id=sv-' + k + '>' + str(dv) + unit + '</b></div>')
        p.append('<input type=range id=sl-' + k + ' min=' + str(mn) + ' max=' + str(mx) + ' step=' + str(st_v) + ' value=' + str(dv) + ' oninput="applySlider(\'' + k + '\',this.value)"></div>')
    p.append('<button type=submit name=action value=apply_param class="btn bb" style="margin-top:8px;width:100%">应用参数（重置）</button>')
    p.append('</form></div>')
    p.append('<div class=card><h2>预设场景</h2><form method=get>')
    p.append('<select name=scen><option value="">-- 选择场景 --</option>')
    for sv, sl in SCENARIOS:
        p.append('<option value=' + sv + '>' + sl + '</option>')
    p.append('</select>')
    p.append('<button type=submit name=action value=apply_scen class="btn bb" style="width:100%;margin-bottom:12px">应用场景</button>')
    p.append('</form>')
    p.append('<h2>Agent 详情</h2><div class=tabs>')
    for av, al in [("household", "家庭"), ("firm", "企业"), ("trader", "交易者"), ("bank", "银行")]:
        cls = "ta" if av == atype else "tb"
        p.append('<form method=get style=display:inline><button type=submit name=atype value=' + av + ' class=' + cls + '>' + al + '</button></form>')
    p.append('</div>')
    p.append('<div class=alist>' + (agents or '<div style="color:#94a3b8">无</div>') + '</div>')
    p.append('</div></div>')
    p.append('<div class=card><form method=get><button type=submit name=action value=export_csv class="btn bb">下载 CSV</button></form></div>')
    p.append('<script>')
    p.append('var _running=' + ('true' if _run else 'false') + ';')
    p.append('var _chart="' + ck + '";')
    p.append('function switchChart(k){')
    p.append('  _chart=k;')
    p.append('  document.getElementById("chart-key").value=k;')
    p.append('  var tabs=document.getElementById("chart-tabs");')
    p.append('  if(tabs)tabs.querySelectorAll("button").forEach(function(b){b.className=b.id==="ctab-"+k?"ta":"tb"});')
    p.append('  fetch("/api/live?chart="+k)')
    p.append('    .then(function(r){return r.json()})')
    p.append('    .then(function(d){')
    p.append('      var ch=document.getElementById("chart");')
    p.append('      if(ch&&d.svg)ch.innerHTML=d.svg;')
    p.append('      _running=d.running;')
    p.append('      if(_running)setTimeout(poll,500);')
    p.append('    }).catch(function(){});')
    p.append('}')
    p.append('function applySlider(k,v){')
    p.append('  var sv=document.getElementById("sv-"+k);')
    p.append('  if(sv)sv.textContent=v+(k==="tax_rate"||k==="base_interest_rate"||k==="shock_prob"?"%":"");')
    p.append('  fetch("/api/param",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({[k]:parseFloat(v)})}).catch(function(){});')
    p.append('}')
    p.append('function poll(){')
    p.append('  if(!_running)return;')
    p.append('  fetch("/api/live?chart="+_chart)')
    p.append('    .then(function(r){return r.json()})')
    p.append('    .then(function(d){')
    p.append('      var e1=document.getElementById("cycle");')
    p.append('      var e2=document.getElementById("cycle2");')
    p.append('      if(e1)e1.textContent="\\u7B2C "+d.cycle+" \\u8F6E";')
    p.append('      if(e2)e2.textContent="\\u7B2C "+d.cycle+" \\u8F6E";')
    p.append('      var ch=document.getElementById("chart");')
    p.append('      if(ch&&d.svg)ch.innerHTML=d.svg;')
    p.append('      var st=document.getElementById("stats");')
    p.append('      if(st){')
    p.append('        var s=d.stats;')
    p.append('        var items=st.querySelectorAll(".sv");')
    p.append('        var keys=["gdp","unemp","gini","stock","price","vol","bdr","loans","rev","bankrupt","rate","emp"];')
    p.append('        var types=["int","pct","dec3","dec1","dec1","dec3","dec1","int","int","int","pct","int"];')
    p.append('        for(var i=0;i<items.length&&i<keys.length;i++){')
    p.append('          var v=s[keys[i]];')
    p.append('          if(v===undefined)continue;')
    p.append('          if(types[i]==="int")items[i].textContent=v.toLocaleString();')
    p.append('          else if(types[i]==="pct")items[i].textContent=v.toFixed(1)+"%";')
    p.append('          else if(types[i]==="dec1")items[i].textContent=v.toFixed(1);')
    p.append('          else items[i].textContent=v.toFixed(3);')
    p.append('        }')
    p.append('      }')
    p.append('      _running=d.running;')
    p.append('      if(_running)setTimeout(poll,500);')
    p.append('    })')
    p.append('    .catch(function(){if(_running)setTimeout(poll,2000)});')
    p.append('}')
    p.append('if(_running)setTimeout(poll,300);')
    p.append('// ── 经济健康分轮询 ──────────────────────────')
    p.append('function pollHealth(){')
    p.append('  fetch("/api/health")')
    p.append('    .then(function(r){return r.json()})')
    p.append('    .then(function(d){')
    p.append('      var s=document.getElementById("hscore"),l=document.getElementById("hlevel"),')
    p.append('          b=document.getElementById("hbar"),br=document.getElementById("hbreakdown");')
    p.append('      if(s&&d.score>0){s.textContent=d.score.toFixed(1);s.style.color=d.color;')
    p.append('        l.textContent=d.level;l.style.color=d.color;')
    p.append('        b.style.width=d.score+"%";b.style.background=d.color;')
    p.append('        br.textContent="GDP"+d.gdp+" 失业"+d.unemp+"% 基尼"+d.gini+" 波动"+d.vol+" 坏账"+d.bdr+"%";')
    p.append('      }')
    p.append('      if(d.shock)document.getElementById("shock-msg").textContent="⚡ 当前："+d.shock;')
    p.append('      if(_running)setTimeout(pollHealth,1000);')
    p.append('    }).catch(function(){});')
    p.append('}')
    p.append('// ── 手动触发冲击 ───────────────────────────')
    p.append('function triggerShock(name){')
    p.append('  fetch("/api/shock",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:name})})')
    p.append('    .then(function(r){return r.json()})')
    p.append('    .then(function(d){')
    p.append('      var el=document.getElementById("shock-msg");')
    p.append('      if(d.ok){el.textContent="⚡ "+d.shock;el.style.color="#ef4444";poll();pollHealth();}')
    p.append('      else{el.textContent="❌ "+d.error;el.style.color="#ef4444";}')
    p.append('    }).catch(function(){document.getElementById("shock-msg").textContent="❌ 网络错误";});')
    p.append('}')
    p.append('// ── 单体追踪 ──────────────────────────────')
    p.append('function trackAgent(){')
    p.append('  var type=document.getElementById("track-type").value,')
    p.append('      uid=parseInt(document.getElementById("track-id").value);')
    p.append('  if(!uid){document.getElementById("track-info").textContent="请输入 ID 号";return;}')
    p.append('  var routes={"household":"api/agent","firm":"api/agent","trader":"api/agent","bank":"api/agent"};')
    p.append('  fetch("/"+routes[type]+"/"+uid)')
    p.append('    .then(function(r){return r.json()})')
    p.append('    .then(function(d){')
    p.append('      var el=document.getElementById("track-info");')
    p.append('      if(d.error){el.textContent="❌ 未找到 ID="+uid;return;}')
    p.append('      var lines=[type[0].toUpperCase()+uid+" 详情","现金:"+d.cash+" 财富:"+d.wealth];')
    p.append('      if(d.type==="household")lines.push("工资:"+d.salary+" 债:"+d.loan+" 股:"+d.shares+" ["+d.tier+"]");')
    p.append('      else if(d.type==="firm")lines.push("产出:"+d.production+" 库存:"+d.inventory+" 员工:"+d.employees+" 单价:"+d.price);')
    p.append('      else if(d.type==="trader")lines.push("持有股份:"+d.shares);')
    p.append('      else if(d.type==="bank")lines.push("准备金:"+d.reserves+" 坏账:"+d.bad_debts);')
    p.append('      el.innerHTML=lines.join("<br>");')
    p.append('      el.style.border="1px solid #e2e8f0";')
    p.append('    }).catch(function(){document.getElementById("track-info").textContent="❌ 网络错误";});')
    p.append('}')
    p.append('// 启动时加载健康分')
    p.append('pollHealth();')
    p.append('</script>')
    p.append('</body></html>')
    return "\n".join(p)


@app.route("/api/live")
def api_live():
    with _lock:
        if _md is None:
            return jsonify({"cycle": 0})
        cyc = _md.cycle
        try:
            emp = sum(1 for h in _md.households if h.employed)
            nh = len(_md.households)
            last = {
                "gdp": round(_md.gdp),
                "unemp": round(_md.unemployment * 100, 1),
                "price": round(getattr(_md, "price_index", 100.0), 1),
                "stock": round(_md.stock_price, 1),
                "vol": round(getattr(_md, "stock_volatility", 0.0), 3),
                "bdr": round(getattr(_md, "bank_bad_debt_rate", 0.0) * 100, 1),
                "loans": round(_md.total_loans_outstanding),
                "rev": round(_md.govt_revenue),
                "bankrupt": _md.bankrupt_count,
                "gini": round(_md.gini, 3),
                "emp": emp, "nh": nh,
                "rate": round(emp / nh * 100 if nh else 0, 1),
            }
        except Exception:
            last = {}
    with _hl:
        h = list(_hist)
    ck = request.args.get("chart", "gdp")
    vals = [r.get(ck, 0) for r in h]
    svg_html = _svg(vals, CHART_CLR.get(ck, "#3b82f6"))
    return jsonify({"cycle": cyc, "stats": last, "svg": svg_html, "running": _run})


@app.route("/api/param", methods=["POST"])
def api_param():
    """滑块实时调参，不重置仿真"""
    data = request.get_json() or {}
    pmap = [
        ("tax_rate",             "pct"),
        ("base_interest_rate",   "pct"),
        ("min_wage",             "flat"),
        ("productivity",         "flat"),
        ("gov_purchase",         "flat"),
        ("shock_prob",           "pct"),
    ]
    updates = {}
    with _lock:
        if _md:
            for k, kind in pmap:
                if k in data:
                    try:
                        v = float(data[k])
                        if kind == "pct":
                            v /= 100
                        setattr(_md, k, v)
                        updates[k] = v
                    except (ValueError, TypeError):
                        pass
    return jsonify({"ok": True, "updated": updates})


@app.route("/api/shock", methods=["POST"])
def api_shock():
    """手动触发外生冲击"""
    data = request.get_json() or {}
    name = data.get("name", "")
    with _lock:
        if _md:
            desc = _md.trigger_shock(name)
            _rec(locked=True)
            return jsonify({"ok": True, "shock": desc})
    return jsonify({"ok": False, "error": "模型未初始化"})


@app.route("/api/agent/<int:uid>")
def api_agent(uid):
    """获取指定 Agent 的详细信息"""
    with _lock:
        if not _md:
            return jsonify({"error": "模型未初始化"})
        for h in _md.households:
            if h.unique_id == uid:
                return jsonify({
                    "id": uid, "type": "household",
                    "cash": round(h.cash), "wealth": round(h.wealth),
                    "employed": h.employed,
                    "salary": round(h.salary),
                    "loan": round(h.loan_principal),
                    "shares": getattr(h, "shares_owned", 0),
                    "tier": str(getattr(h, "income_tier", "")),
                })
        for f in _md.firms:
            if f.unique_id == uid:
                return jsonify({
                    "id": uid, "type": "firm",
                    "cash": round(f.cash), "wealth": round(f.wealth),
                    "production": round(f.production, 1),
                    "inventory": round(f.inventory, 1),
                    "employees": f.employees,
                    "price": round(getattr(f, "price", 0), 2),
                })
        for t in _md.traders:
            if t.unique_id == uid:
                return jsonify({
                    "id": uid, "type": "trader",
                    "cash": round(t.cash), "wealth": round(t.wealth),
                    "shares": getattr(t, "shares", 0),
                })
        for b in _md.banks:
            if b.unique_id == uid:
                return jsonify({
                    "id": uid, "type": "bank",
                    "cash": round(b.wealth), "wealth": round(b.wealth),
                    "reserves": round(getattr(b, "reserves", 0)),
                    "bad_debts": round(getattr(b, "bad_debts", 0)),
                })
    return jsonify({"error": "Agent 未找到"})


@app.route("/api/health")
def api_health():
    """经济健康分"""
    with _lock:
        if not _md:
            return jsonify({"score": 0, "level": "未初始化"})
        score = _md.health_score
        if score >= 80:
            level, color = "繁荣", "#16a34a"
        elif score >= 60:
            level, color = "稳健", "#3b82f6"
        elif score >= 40:
            level, color = "偏弱", "#f59e0b"
        else:
            level, color = "危机", "#ef4444"
        return jsonify({"score": score, "level": level, "color": color,
                        "gdp": round(_md.gdp),
                        "unemp": round(_md.unemployment * 100, 1),
                        "gini": round(_md.gini, 3),
                        "vol": round(getattr(_md, "stock_volatility", 0), 3),
                        "bdr": round(getattr(_md, "bank_bad_debt_rate", 0) * 100, 1),
                        "shock": _md.current_shock})


@app.route("/")
def index():
    global _run, _thr, _feedback
    act = request.args.get("action", "")
    if act == "step":
        step()
    elif act == "play":
        _run = True
        if not (_thr and _thr.is_alive()):
            _stop.clear()
            _thr = threading.Thread(target=_play_loop, daemon=True)
            _thr.start()
    elif act == "pause":
        _run = False
    elif act == "reset":
        init()
    elif act == "apply_param":
        params = {}
        for k in ("n_households", "n_firms", "min_wage", "productivity", "gov_purchase"):
            v = request.args.get(k)
            if v:
                try:
                    params[k] = float(v)
                except Exception:
                    pass
        for k in ("tax_rate", "base_interest_rate", "shock_prob"):
            v = request.args.get(k)
            if v:
                try:
                    params[k] = float(v) / 100
                except Exception:
                    pass
        if params:
            init(**params)
            _feedback = "参数已应用，仿真已重置"
    elif act == "apply_scen":
        sc = request.args.get("scen", "")
        if sc in SCEN_MAP:
            init(**SCEN_MAP[sc])
            _feedback = "场景已应用，仿真已重置"
    elif act == "export_csv":
        buf = io.StringIO()
        with _hl:
            h = list(_hist)
        if h:
            keys = list(h[0].keys())
            buf.write(",".join(keys) + "\n")
            for row in h:
                buf.write(",".join(str(row.get(k, "")) for k in keys) + "\n")
        return Response(buf.getvalue().encode("utf-8"), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=economy.csv"})
    return _page()


def _play_loop():
    global _run
    while not _stop.wait(0.5):
        if _run:
            step()


if __name__ == "__main__":
    init()
    print("经济沙盘 v3.3: http://127.0.0.1:8523")
    app.run(host="0.0.0.0", port=8523, debug=False, threaded=True)
