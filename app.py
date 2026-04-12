"""
Mesa 经济沙盘 v3.2 - Flask + 服务器端 SVG（零 JS 图表依赖）
"""
import io
import math
import threading
from flask import Flask, jsonify, request, Response

from model import EconomyModel

# ─────────────────────────────────────────
_model_lock = threading.Lock()
_model = None
_history = []
_history_lock = threading.Lock()
_running = False
_play_stop = threading.Event()
_play_thread = None


def init_model(**kwargs):
    global _model, _history, _running, _play_thread
    defaults = dict(
        n_households=20, n_firms=10, n_traders=20,
        tax_rate=0.15, base_interest_rate=0.05, min_wage=7.0,
        productivity=1.0, subsidy=0.0, gov_purchase=0.0,
        capital_gains_tax=0.10, shock_prob=0.02,
    )
    defaults.update({k: v for k, v in kwargs.items() if v is not None})
    _running = False
    if _play_thread:
        _play_stop.set()
    with _model_lock:
        _model = EconomyModel(**defaults)
    _history = []
    _record()


def step_model():
    global _model
    with _model_lock:
        if _model:
            _model.step()
    _record()


def _record():
    with _model_lock:
        if _model is None:
            return
        m = _model
        try:
            employed = sum(1 for h in m.households if h.employed)
            n_hh = len(m.households)
            vol = getattr(m, 'stock_volatility', 0.0)
            bdr = getattr(m, 'bank_bad_debt_rate', 0.0)
            entry = {
                "cycle": m.cycle,
                "gdp": round(m.gdp, 0),
                "unemployment": round(m.unemployment * 100, 1),
                "price_index": round(getattr(m, 'price_index', 100.0), 1),
                "stock_price": round(m.stock_price, 1),
                "chart_vol": round(vol, 3),
                "chart_bdr": round(bdr * 100, 1),
                "chart_systemic": round(getattr(m, 'systemic_risk', 0.0), 3),
                "loans": round(m.total_loans_outstanding, 0),
                "govt_rev": round(m.govt_revenue, 0),
                "bankrupt": m.bankrupt_count,
                "n_firms": len(m.firms),
                "employed": employed,
                "n_households": n_hh,
                "emp_rate": round(employed / n_hh * 100 if n_hh > 0 else 0.0, 1),
                "unemployed": n_hh - employed,
                "gini": round(m.gini, 3),
                "shock": getattr(m, 'current_shock', '') or '',
            }
        except Exception:
            entry = {"cycle": getattr(m, 'cycle', 0)}
    with _history_lock:
        _history.append(entry)
        if len(_history) > 500:
            del _history[:-500]


def _play_loop():
    global _running
    while not _play_stop.wait(0.5):
        if _running:
            step_model()


# ─────────────────────────────────────────
# SVG 图表生成
# ─────────────────────────────────────────

def make_svg(values, color="#3b82f6", width=680, height=200):
    if not values:
        values = [0]
    n = len(values)
    PADL, PADR, PADT, PADB = 50, 10, 8, 30
    w = width - PADL - PADR
    h = height - PADT - PADB
    mn = min(values)
    mx = max(values)
    rng = mx - mn if mx != mn else 1

    # 网格线
    grid = ""
    for i in range(5):
        y = PADT + h * i / 4
        v = mx - rng * i / 4
        grid += '<line x1="{}" y1="{:.1f}" x2="{}" y2="{:.1f}" stroke="#e2e8f0" stroke-width="1"/>'.format(
            PADL, y, PADL + w, y)
        grid += '<text x="{}" y="{:.1f}" text-anchor="end" font-size="10" fill="#94a3b8">{:.1f}</text>'.format(
            PADL - 4, y + 3, v)

    # 折线点
    pts = ""
    for i, v in enumerate(values):
        x = PADL + w * i / max(1, n - 1)
        y = PADT + h * (1 - (v - mn) / rng)
        pts += "{:.1f},{:.1f} ".format(x, y)

    # 填充区域
    fill = "M {} {} ".format(PADL, PADT + h) + " ".join(
        "L {:.1f} {:.1f}".format(PADL + w * i / max(1, n - 1),
                                  PADT + h * (1 - (v - mn) / rng))
        for i, v in enumerate(values)) + " L {} {}".format(PADL + w, PADT + h) + " Z"

    # X 轴标签
    xlabels = ""
    step = max(1, n // 6)
    for i in range(0, n, step):
        x = PADL + w * i / max(1, n - 1)
        xlabels += '<text x="{:.1f}" y="{:.1f}" text-anchor="middle" font-size="10" fill="#94a3b8">{}</text>'.format(
            x, PADT + h + 16, i)

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="{}" viewBox="0 0 {} {}">'.format(
            height, width, height) +
        grid + xlabels +
        '<polyline points="{}" fill="none" stroke="{}" stroke-width="2" stroke-linejoin="round"/>'.format(
            pts.strip(), color) +
        '<path d="{}" fill="{}" opacity="0.1"/>'.format(fill, color) +
        '<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#cbd5e1" stroke-width="1"/>'.format(
            PADL, PADT, PADL, PADT + h) +
        '<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#cbd5e1" stroke-width="1"/>'.format(
            PADL, PADT + h, PADL + w, PADT + h) +
        '</svg>'
    )
    return svg


# ─────────────────────────────────────────
# 页面构建（纯字符串，无模板引擎）
# ─────────────────────────────────────────

CHART_DEFS = [
    ("gdp", "GDP", "#16a34a"),
    ("unemployment", "失业率(%)", "#dc2626"),
    ("gini", "基尼系数", "#9333ea"),
    ("stock_price", "股价", "#d97706"),
    ("price_index", "物价指数", "#64748b"),
    ("chart_vol", "波动率", "#f97316"),
    ("chart_bdr", "坏账率(%)", "#ec4899"),
    ("loans", "信贷总量", "#0891b2"),
    ("chart_systemic", "系统风险", "#dc2626"),
]

SLIDER_DEFS = [
    ("n_households", "家庭数量", 5, 80, 5, 20),
    ("n_firms", "企业数量", 3, 40, 1, 10),
    ("tax_rate", "所得税率(%)", 0, 45, 1, 15),
    ("base_interest_rate", "基准利率(%)", 0, 25, 0.5, 5),
    ("min_wage", "最低工资", 0, 20, 0.5, 7),
    ("productivity", "全要素生产率", 0.1, 3, 0.1, 1.0),
    ("gov_purchase", "政府购买", 0, 200, 5, 0),
    ("shock_prob", "冲击概率(%)", 0, 20, 1, 2),
]

SCENARIOS = {
    "经济危机": {"base_interest_rate": 15, "tax_rate": 25},
    "宽松政策": {"base_interest_rate": 1, "tax_rate": 5, "subsidy": 15},
    "高税高补贴": {"tax_rate": 40, "subsidy": 20, "min_wage": 15},
    "自由市场": {"tax_rate": 5, "base_interest_rate": 2, "subsidy": 0, "min_wage": 0},
    "政府刺激": {"gov_purchase": 150, "tax_rate": 12, "subsidy": 8},
    "金融危机": {"base_interest_rate": 20, "tax_rate": 30, "shock_prob": 15},
}


def build_page():
    # 生成 slider 行
    sliders_h = ""
    for key, label, mn, mx, st, dflt in SLIDER_DEFS:
        sliders_h += (
            '<div class="srow">'
            '<div class="slbl"><span>{}</span><b id="v-{}">{}</b></div>'
            '<input type="range" id="s-{}" min="{}" max="{}" step="{}" value="{}" '
            'oninput="sl(this,\'{}\')">'
            '</div>'
        ).format(label, key, dflt, key, mn, mx, st, dflt, key)

    # 生成 scenario 选项
    scen_h = '<option value="">-- 选择场景 --</option>'
    for nm in SCENARIOS:
        scen_h += '<option value="{}">{}</option>'.format(nm, nm)

    # 生成 chart tab 按钮
    tabs_h = ""
    cfg_js = "var CFG=["
    first = True
    for field, label, color in CHART_DEFS:
        cls = "active" if field == "gdp" else ""
        tabs_h += '<button class="tabbtn {}" onclick="showChart(\'{}\')">{}</button>'.format(
            cls, field, label)
        sep = "" if first else ","
        cfg_js += '{}["{}","{}","{}"]'.format(sep, field, label, color)
        first = False
    cfg_js += "];"

    # 生成 slider id 数组
    slider_ids = "var SID=[" + ",".join("'{}'".format(s[0]) for s in SLIDER_DEFS) + "];"

    # 生成 scenario json
    scen_js = "var SCEN={"
    first = True
    for nm, params in SCENARIOS.items():
        sep = "" if first else ","
        kv = ",".join("'{}':{}".format(k, v) for k, v in params.items())
        scen_js += "{}'{}':{{{}}}".format(sep, nm, kv)
        first = False
    scen_js += "};"

    html = _PAGE_HTML
    html = html.replace("__SLIDERS__", sliders_h)
    html = html.replace("__SCENARIOS__", scen_h)
    html = html.replace("__TAB_BUTTONS__", tabs_h)
    html = html.replace("/* __CFG_JS__ */", cfg_js)
    html = html.replace("/* __SID_JS__ */", slider_ids)
    html = html.replace("/* __SCEN_JS__ */", scen_js)
    return html


_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>经济沙盘 v3.2</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:#f8fafc;color:#1e293b;padding:16px;font-size:14px;max-width:960px;margin:0 auto}
h1{font-size:20px;margin-bottom:12px;color:#0f172a}
h2{font-size:12px;color:#64748b;margin-bottom:8px;font-weight:500;text-transform:uppercase;letter-spacing:.05em}
h3{font-size:12px;color:#64748b;margin:10px 0 6px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:14px;margin-bottom:10px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
.btn{border:none;padding:7px 16px;border-radius:6px;cursor:pointer;font-size:13px;font-family:inherit}
.btn-pri{background:#3b82f6;color:#fff}.btn-pri:hover{background:#2563eb}
.btn-grn{background:#16a34a;color:#fff}.btn-grn:hover{background:#15803d}
.btn-org{background:#d97706;color:#fff}.btn-org:hover{background:#b45309}
.btn-red{background:#dc2626;color:#fff}.btn-red:hover{background:#b91c1c}
.btn-sm{padding:5px 10px;font-size:12px}
.srow{margin-bottom:6px}
.slbl{display:flex;justify-content:space-between;margin-bottom:2px;font-size:13px;color:#475569}
.slbl b{color:#0f172a;font-weight:600}
input[type=range]{width:100%;accent-color:#3b82f6;height:4px}
select{width:100%;padding:6px;border-radius:6px;border:1px solid #cbd5e1;background:#fff;font-size:13px;margin-bottom:8px;font-family:inherit}
.sgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(115px,1fr));gap:8px}
.scard{background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:8px 10px}
.slbl2{font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:.04em}
.sval{font-size:18px;font-weight:700;color:#0f172a;margin-top:2px}
.sval.warn{color:#d97706}
.sval.danger{color:#dc2626}
.sval.good{color:#16a34a}
.tabs{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px}
.tabbtn{background:#e2e8f0;color:#475569;border:none;padding:5px 11px;border-radius:5px;cursor:pointer;font-size:12px;font-family:inherit}
.tabbtn.active{background:#3b82f6;color:#fff}
.alist{max-height:170px;overflow-y:auto;font-size:12px;color:#475569;line-height:1.7;font-family:monospace}
.aitem{background:#f8fafc;border-radius:4px;padding:2px 8px;margin-bottom:2px}
.c2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
@media(max-width:600px){.c2{grid-template-columns:1fr}}
</style>
</head>
<body>
<h1>经济沙盘 <span style="color:#3b82f6">v3.2</span></h1>

<div class="row">
  <button class="btn btn-grn" id="btn-play" onclick="togglePlay()">播放</button>
  <button class="btn btn-sm" onclick="step()">单步</button>
  <button class="btn btn-red btn-sm" onclick="reset()">重置</button>
  <span id="cycle" style="margin-left:auto;color:#94a3b8">第 0 轮</span>
</div>

<div class="card">
  <h2>宏观指标</h2>
  <div class="sgrid" id="stats"></div>
</div>

<div class="card">
  <h2>图表</h2>
  <div class="tabs" id="tabs">__TAB_BUTTONS__</div>
  <div id="chart">正在加载图表...</div>
</div>

<div class="c2">
  <div class="card">
    <h2>经济参数</h2>
__SLIDERS__
  </div>
  <div class="card">
    <h2>预设场景</h2>
    <select id="scen">__SCENARIOS__</select>
    <button class="btn btn-pri btn-sm" onclick="applyScen()" style="width:100%;margin-bottom:12px">应用场景</button>
    <h2>Agent 详情</h2>
    <select id="atype" onchange="loadAgents()">
      <option value="household">家庭</option><option value="firm">企业</option><option value="trader">交易者</option><option value="bank">银行</option>
    </select>
    <div class="alist" id="alist"></div>
  </div>
</div>

<div class="card">
  <button class="btn btn-sm" onclick="exportCSV()">下载 CSV</button>
</div>

<script>
var GFIELD='gdp';
var GTIMER=null;
var GPLAY=false;

(function(){
  /* __CFG_JS__ */
  /* __SID_JS__ */
  /* __SCEN_JS__ */
  refresh();
  setInterval(refresh, 2000);
  loadAgents();
  showChart('gdp');
})();

function refresh(){
  var x=new XMLHttpRequest();
  x.open('GET','/api/state',true);
  x.onreadystatechange=function(){
    if(x.readyState===4&&x.status===200){
      try{
        var d=JSON.parse(x.responseText);
        document.getElementById('cycle').textContent='第 '+(d.cycle||0)+' 轮';
        updateStats(d.last);
        updateChart(d.history);
      }catch(e){console.error(e);}
    }
  };
  x.send();
}

function updateStats(s){
  if(!s)return;
  var cards=[
    ['gdp','GDP',fk(s.gdp),''],
    ['price_index','物价',(s.price_index||100).toFixed(1),''],
    ['stock_price','股价',(s.stock_price||0).toFixed(1),''],
    ['unemployment','失业率',(s.unemployment||0)+'%',parseFloat(s.unemployment)>15?'danger':''],
    ['gini','基尼',(s.gini||0).toFixed(3),parseFloat(s.gini)>0.4?'warn':''],
    ['emp_rate','就业率',(s.emp_rate||0)+'%',''],
    ['chart_vol','波动率',(s.chart_vol||0).toFixed(3),(s.chart_vol||0)>0.3?'danger':'warn'],
    ['chart_bdr','坏账率',(s.chart_bdr||0).toFixed(1)+'%',(s.chart_bdr||0)>10?'danger':'warn'],
    ['chart_systemic','风险',(s.chart_systemic||0).toFixed(3),(s.chart_systemic||0)>0.2?'danger':'warn'],
    ['bankrupt','破产',s.bankrupt||0,s.bankrupt>0?'warn':''],
    ['loans','贷款',fk(s.loans),''],
    ['govt_rev','政府收入',fk(s.govt_rev),''],
  ];
  document.getElementById('stats').innerHTML=cards.map(function(c){
    return '<div class=scard><div class=slbl2>'+c[0]+'</div><div class="sval '+c[3]+'">'+c[2]+'</div></div>';
  }).join('');
}

function fk(n){
  n=n||0;
  if(n>=1e6)return(n/1e6).toFixed(1)+'M';
  if(n>=1e3)return(n/1e3).toFixed(0)+'K';
  return n.toFixed(0);
}

function updateChart(h){
  var data=(h||[]).map(function(x){return x[GFIELD]||0;});
  if(!data.length)data=[0];
  var color='#3b82f6';
  for(var i=0;i<CFG.length;i++){
    if(CFG[i][0]===GFIELD){color=CFG[i][2];break;}
  }
  var x=new XMLHttpRequest();
  x.open('POST','/api/svg',true);
  x.setRequestHeader('Content-Type','application/json');
  x.onreadystatechange=function(){
    if(x.readyState===4&&x.status===200){
      document.getElementById('chart').innerHTML=x.responseText;
    }
  };
  x.send(JSON.stringify({field:GFIELD,values:data,color:color}));
}

function showChart(f){
  GFIELD=f;
  var tabs=document.getElementById('tabs').getElementsByTagName('button');
  for(var i=0;i<tabs.length;i++){
    tabs[i].className='tabbtn'+(tabs[i].onclick.toString().indexOf(f)>0?' active':'');
  }
  // 触发更新
  var h=document.getElementById('chart').innerHTML;
  if(h&&h.indexOf('svg')>=0)updateChart(window._HIST||[]);
  // 直接请求新svg
  var data=(window._HIST||[]).map(function(x){return x[GFIELD]||0;});
  if(!data.length)data=[0];
  var color='#3b82f6';
  for(var i=0;i<CFG.length;i++){
    if(CFG[i][0]===GFIELD){color=CFG[i][2];break;}
  }
  var x=new XMLHttpRequest();
  x.open('POST','/api/svg',true);
  x.setRequestHeader('Content-Type','application/json');
  x.onreadystatechange=function(){
    if(x.readyState===4&&x.status===200){
      document.getElementById('chart').innerHTML=x.responseText;
    }
  };
  x.send(JSON.stringify({field:GFIELD,values:data,color:color}));
}

function step(){
  var x=new XMLHttpRequest();
  x.open('POST','/api/step',true);
  x.onreadystatechange=function(){if(x.readyState===4)refresh();};
  x.send();
}

function togglePlay(){
  var btn=document.getElementById('btn-play');
  if(GPLAY){
    GPLAY=false;clearInterval(GTIMER);
    btn.textContent='播放';btn.className='btn btn-grn';
    var x=new XMLHttpRequest();x.open('POST','/api/pause',true);x.send();
  }else{
    GPLAY=true;
    btn.textContent='暂停';btn.className='btn btn-org';
    var x=new XMLHttpRequest();x.open('POST','/api/play',true);x.send();
    if(GTIMER)clearInterval(GTIMER);
    GTIMER=setInterval(refresh,500);
  }
}

function reset(){
  if(GPLAY){GPLAY=false;clearInterval(GTIMER);}
  document.getElementById('btn-play').textContent='播放';
  document.getElementById('btn-play').className='btn btn-grn';
  var params={};
  for(var i=0;i<SID.length;i++){
    var el=document.getElementById('s-'+SID[i]);
    if(el)params[SID[i]]=parseFloat(el.value);
  }
  var x=new XMLHttpRequest();
  x.open('POST','/api/reset',true);
  x.setRequestHeader('Content-Type','application/json');
  x.onreadystatechange=function(){if(x.readyState===4){refresh();}};
  x.send(JSON.stringify(params));
}

function sl(el,key){
  var disp=document.getElementById('v-'+key);
  if(disp)disp.textContent=el.value;
  var x=new XMLHttpRequest();
  x.open('POST','/api/param',true);
  x.setRequestHeader('Content-Type','application/json');
  x.send(JSON.stringify({key:parseFloat(el.value)}));
}

function applyScen(){
  var nm=document.getElementById('scen').value;
  var p=SCEN[nm];
  if(!p)return;
  for(var k in p){
    var el=document.getElementById('s-'+k);
    if(el){el.value=p[k];var disp=document.getElementById('v-'+k);if(disp)disp.textContent=p[k];}
    var x=new XMLHttpRequest();
    x.open('POST','/api/param',true);
    x.setRequestHeader('Content-Type','application/json');
    x.send(JSON.stringify({key:parseFloat(p[k])}));
  }
}

function loadAgents(){
  var t=encodeURIComponent(document.getElementById('atype').value);
  var x=new XMLHttpRequest();
  x.open('GET','/api/agents/'+t,true);
  x.onreadystatechange=function(){
    if(x.readyState===4&&x.status===200){
      var a=JSON.parse(x.responseText)||[];
      document.getElementById('alist').innerHTML=a.map(function(s){return'<div class=aitem>'+s+'</div>';}).join('')||'<div style="color:#94a3b8">无</div>';
    }
  };
  x.send();
}

function exportCSV(){
  var x=new XMLHttpRequest();
  x.open('GET','/api/state',true);
  x.onreadystatechange=function(){
    if(x.readyState===4&&x.status===200){
      var d=JSON.parse(x.responseText)||{};
      var h=d.history||[];
      if(!h.length)return;
      var keys=Object.keys(h[0]);
      var csv='cycle,'+keys.join(',')+'\n';
      h.forEach(function(r){
        csv+=(r.cycle||'')+','+keys.map(function(k){return r[k]||0;}).join(',')+'\n';
      });
      var a=document.createElement('a');
      a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));
      a.download='economy.csv';a.click();
    }
  };
  x.send();
}
</script>
</body>
</html>
"""


# ─────────────────────────────────────────
# Flask 路由
# ─────────────────────────────────────────

app = Flask(__name__)


@app.route("/")
def index():
    if _model is None:
        init_model()
    return Response(build_page(), mimetype="text/html; charset=utf-8")


@app.route("/api/state")
def api_state():
    with _model_lock:
        if _model is None:
            return jsonify({"cycle": 0, "last": {}, "history": []})
        m = _model
        try:
            employed = sum(1 for h in m.households if h.employed)
            n_hh = len(m.households)
            vol = getattr(m, 'stock_volatility', 0.0)
            bdr = getattr(m, 'bank_bad_debt_rate', 0.0)
            last = {
                "cycle": m.cycle,
                "gdp": round(m.gdp, 0),
                "unemployment": round(m.unemployment * 100, 1),
                "price_index": round(getattr(m, 'price_index', 100.0), 1),
                "stock_price": round(m.stock_price, 1),
                "chart_vol": round(vol, 3),
                "chart_bdr": round(bdr * 100, 1),
                "chart_systemic": round(getattr(m, 'systemic_risk', 0.0), 3),
                "loans": round(m.total_loans_outstanding, 0),
                "govt_rev": round(m.govt_revenue, 0),
                "bankrupt": m.bankrupt_count,
                "n_firms": len(m.firms),
                "employed": employed,
                "n_households": n_hh,
                "emp_rate": round(employed / n_hh * 100 if n_hh > 0 else 0.0, 1),
                "unemployed": n_hh - employed,
                "gini": round(m.gini, 3),
            }
        except Exception:
            last = {"cycle": m.cycle}
    with _history_lock:
        hist = list(_history)
    return jsonify({"cycle": last.get("cycle", 0), "last": last, "history": hist})


@app.route("/api/step", methods=["POST"])
def api_step():
    step_model()
    return jsonify({"ok": True})


@app.route("/api/play", methods=["POST"])
def api_play():
    global _running, _play_thread
    _running = True
    if not (_play_thread and _play_thread.is_alive()):
        _play_stop.clear()
        _play_thread = threading.Thread(target=_play_loop, daemon=True)
        _play_thread.start()
    return jsonify({"ok": True})


@app.route("/api/pause", methods=["POST"])
def api_pause():
    global _running
    _running = False
    return jsonify({"ok": True})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    params = request.get_json() or {}
    init_model(**params)
    return jsonify({"ok": True})


@app.route("/api/param", methods=["POST"])
def api_param():
    data = request.get_json(force=True, silent=True) or {}
    with _model_lock:
        if _model:
            for k, v in data.items():
                if hasattr(_model, k):
                    try:
                        setattr(_model, k, float(v))
                    except Exception:
                        pass
    return jsonify({"ok": True})


@app.route("/api/svg", methods=["POST"])
def api_svg():
    data = request.get_json(force=True, silent=True) or {}
    field = str(data.get("field", "gdp"))
    values = data.get("values", [])
    color = str(data.get("color", "#3b82f6"))
    svg = make_svg(values, color=color)
    return Response(svg, mimetype="image/svg+xml")


@app.route("/api/agents/<path:agent_type>")
def api_agents(agent_type):
    with _model_lock:
        if _model is None:
            return jsonify([])
        agents = []
        if agent_type in ("household", "家庭"):
            tier_map = {"low": "低", "middle": "中", "high": "高"}
            for h in _model.households:
                tier = getattr(h, 'income_tier', None)
                tier_key = str(tier.value) if hasattr(tier, 'value') else str(tier) if tier else ""
                ts = tier_map.get(tier_key, "?")
                agents.append(
                    "H#{} 现:{} 富:{} {}/{} 薪:{} 股:{} [{}]".format(
                        h.unique_id, round(h.cash, 0), round(h.wealth, 0),
                        "就业" if h.employed else "失业",
                        h.unique_id,  # duplicated but fine
                        round(h.salary, 1), h.shares_owned, ts
                    )
                )
        elif agent_type in ("firm", "企业"):
            for f in _model.firms:
                agents.append(
                    "F#{} 现:{} 富:{} 产:{} 库:{} 员:{}".format(
                        f.unique_id, round(f.cash, 0), round(f.wealth, 0),
                        round(f.production, 1), round(f.inventory, 1), f.employees
                    )
                )
        elif agent_type in ("trader", "交易者"):
            for t in _model.traders:
                agents.append(
                    "T#{} 现:{} 富:{} 股:{}".format(
                        t.unique_id, round(t.cash, 0), round(t.wealth, 0), t.shares
                    )
                )
        elif agent_type in ("bank", "银行"):
            for b in _model.banks:
                agents.append(
                    "B#{} 准:{} 富:{}".format(
                        b.unique_id, round(b.reserves, 0), round(b.wealth, 0)
                    )
                )
    return jsonify(agents)


if __name__ == "__main__":
    init_model()
    print("经济沙盘 v3.2: http://127.0.0.1:8523")
    app.run(host="0.0.0.0", port=8523, debug=False, threaded=True)
