"""
Mesa 经济沙盘 - Flask + Chart.js 版（无 WebSocket 依赖）
运行: python app.py
访问: http://127.0.0.1:8523
"""

import io
import json
import threading
import time
from flask import Flask, jsonify, render_template_string, Response, send_file, request

from model import EconomyModel

# ───────────────────────────────────────────────────────────────
# 全局状态
# ───────────────────────────────────────────────────────────────

_model_lock = threading.Lock()
_model: EconomyModel | None = None
_history: list[dict] = []
_history_lock = threading.Lock()
_running = False
_play_stop = threading.Event()
_play_thread: threading.Thread | None = None

# ───────────────────────────────────────────────────────────────
# 模型操作
# ───────────────────────────────────────────────────────────────

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
    """在 _model_lock 外部调用（自身不加锁）"""
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
                "gov_purch": round(m.gov_purchase, 0),
                "cap_gains": round(getattr(m, 'capital_gains_tax_revenue', 0.0), 0),
                "bankrupt": m.bankrupt_count,
                "default_count": m.default_count,
                "n_firms": len(m.firms),
                "employed": employed,
                "n_households": n_hh,
                "emp_rate": round(employed / n_hh * 100 if n_hh > 0 else 0.0, 1),
                "unemployed": n_hh - employed,
                "n_traders": len(m.traders),
                "gini": round(m.gini, 3),
                "shock": getattr(m, 'current_shock', '') or '',
            }
        except Exception:
            entry = {"cycle": getattr(m, 'cycle', 0)}
    with _history_lock:
        _history.append(entry)
        if len(_history) > 500:
            _history[:] = _history[-500:]


def _play_loop():
    global _running
    while not _play_stop.wait(0.5):
        if _running:
            step_model()


# ───────────────────────────────────────────────────────────────
# Flask 路由
# ───────────────────────────────────────────────────────────────

app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>经济沙盘 v3.1</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:#ffffff;color:#1e293b;min-height:100vh;padding:12px}
h1{font-size:18px;color:#1e293b;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid #e2e8f0}
.panel{background:#f8fafc;border-radius:8px;padding:14px;margin-bottom:10px;border:1px solid #e2e8f0}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.btn{background:#3b82f6;color:#fff;border:none;padding:7px 16px;border-radius:5px;cursor:pointer;font-size:13px;font-family:inherit}
.btn:hover{background:#2563eb}
.btn-success{background:#22c55e}.btn-success:hover{background:#16a34a}
.btn-warning{background:#f59e0b}.btn-warning:hover{background:#d97706}
.btn-danger{background:#ef4444}.btn-danger:hover{background:#dc2626}
.btn-sm{padding:4px 10px;font-size:12px}
.stat{background:#f1f5f9;border-radius:6px;padding:8px 12px;min-width:100px;border:1px solid #e2e8f0;flex:1}
.stat-label{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.05em}
.stat-value{font-size:18px;font-weight:700;color:#1e293b;margin-top:2px}
.stat-value.warn{color:#f59e0b}
.stat-value.danger{color:#ef4444}
.stat-value.good{color:#22c55e}
.grid{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}
input[type=range]{width:100%;accent-color:#3b82f6}
.slider-row{margin-bottom:6px}
.slider-label{font-size:12px;color:#94a3b8;display:flex;justify-content:space-between;margin-bottom:2px}
canvas{max-width:100%}
.tab-bar{display:flex;gap:4px;margin-bottom:8px;flex-wrap:wrap}
.tab{background:#e2e8f0;color:#64748b;border:none;padding:5px 12px;border-radius:4px;cursor:pointer;font-size:12px;font-family:inherit}
.tab.active{background:#3b82f6;color:#fff}
.agent-list{max-height:200px;overflow-y:auto;font-size:12px;color:#94a3b8;line-height:1.6;font-family:monospace}
.agent-item{background:#f1f5f9;border-radius:4px;padding:4px 8px;margin-bottom:2px}
select{width:100%;padding:6px;border-radius:4px;background:#f1f5f9;color:#1e293b;border:1px solid #cbd5e1;margin-bottom:8px;font-family:inherit;font-size:13px}
h2{font-size:13px;color:#94a3b8;margin:12px 0 8px}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:10px}
@media(max-width:700px){.two-col{grid-template-columns:1fr}}
</style>
</head>
<body>
<h1>经济沙盘 v3.1</h1>

<div class="row panel" style="margin-bottom:12px">
  <button class="btn btn-success" id="btn-play" onclick="togglePlay()">▶ 播放</button>
  <button class="btn btn-sm" onclick="step()">单步</button>
  <button class="btn btn-danger btn-sm" onclick="doReset()">重置</button>
  <span id="cycle" style="margin-left:auto;color:#64748b;font-size:14px">第 0 轮</span>
</div>

<div class="grid panel" id="macro-grid"></div>

<div class="panel">
  <div class="tab-bar" id="chart-tabs"></div>
  <canvas id="chart" height="220"></canvas>
</div>

<div class="two-col">
  <div class="panel">
    <h2>经济参数</h2>
    <div class="slider-row"><div class="slider-label">家庭数量 <span id="v-n_households">20</span></div><input type="range" id="s-n_households" min="5" max="80" step="5" value="20" oninput="sl(this)"></div>
    <div class="slider-row"><div class="slider-label">企业数量 <span id="v-n_firms">10</span></div><input type="range" id="s-n_firms" min="3" max="40" step="1" value="10" oninput="sl(this)"></div>
    <div class="slider-row"><div class="slider-label">所得税率 <span id="v-tax_rate">15</span>%</div><input type="range" id="s-tax_rate" min="0" max="45" step="1" value="15" oninput="sl(this)"></div>
    <div class="slider-row"><div class="slider-label">基准利率 <span id="v-base_interest_rate">5.0</span>%</div><input type="range" id="s-base_interest_rate" min="0" max="25" step="0.5" value="5" oninput="sl(this)"></div>
    <div class="slider-row"><div class="slider-label">最低工资 <span id="v-min_wage">7.0</span></div><input type="range" id="s-min_wage" min="0" max="20" step="0.5" value="7" oninput="sl(this)"></div>
    <div class="slider-row"><div class="slider-label">全要素生产率 <span id="v-productivity">1.0</span></div><input type="range" id="s-productivity" min="0.1" max="3" step="0.1" value="1" oninput="sl(this)"></div>
    <div class="slider-row"><div class="slider-label">政府购买 <span id="v-gov_purchase">0</span></div><input type="range" id="s-gov_purchase" min="0" max="200" step="5" value="0" oninput="sl(this)"></div>
    <div class="slider-row"><div class="slider-label">冲击概率 <span id="v-shock_prob">2</span>%</div><input type="range" id="s-shock_prob" min="0" max="20" step="1" value="2" oninput="sl(this)"></div>
  </div>
  <div class="panel">
    <h2>预设场景</h2>
    <select id="scenario">
      <option value="默认">默认参数</option>
      <option value="经济危机">经济危机</option>
      <option value="宽松政策">宽松政策</option>
      <option value="高税高补贴">高税高补贴</option>
      <option value="自由市场">自由市场</option>
      <option value="政府刺激">政府刺激</option>
      <option value="金融危机">金融危机</option>
    </select>
    <button class="btn btn-sm" onclick="applyScenario()" style="width:100%;margin-bottom:12px">应用场景</button>
    <h2>Agent 详情</h2>
    <select id="agent-type" onchange="loadAgents()">
      <option value="家庭">家庭</option>
      <option value="企业">企业</option>
      <option value="交易者">交易者</option>
      <option value="银行">银行</option>
    </select>
    <div id="agent-list" class="agent-list"></div>
  </div>
</div>

<div class="panel">
  <button class="btn btn-sm" onclick="exportCSV()">下载 CSV</button>
</div>

<script src="/static/chart.umd.min.js"></script>
<script>
let chart=null, playing=false, hist=[], field='gdp', label='GDP', color='#22c55e';
const F=['gdp','unemployment','gini','stock_price','price_index','chart_vol','chart_bdr','loans','chart_systemic'];
const L=['GDP','失业率%','基尼系数','股价','物价指数','波动率','坏账率%','信贷总量','系统风险'];
const C=['#22c55e','#ef4444','#a855f7','#f59e0b','#94a3b8','#f97316','#ec4899','#06b6d4','#dc2626'];

function buildTabs(){
  document.getElementById('chart-tabs').innerHTML=F.map((f,i)=>
    '<button class="tab'+(f===field?' active':'')+'" onclick="setChart(\''+f+'\',\''+L[i]+'\',\''+C[i]+'\')">'+L[i]+'</button>'
  ).join('');
}
function setChart(f,l,c){field=f;label=l;color=c;buildTabs();updateChart();}

async function fetchState(){
  try{
    let r=await fetch('/api/state');
    let d=await r.json();
    hist=d.history||[];
    document.getElementById('cycle').textContent='第 '+(d.cycle||0)+' 轮';
    let s=d.last||{};
    document.getElementById('macro-grid').innerHTML=[
      ['gdp',s.gdp,'$'+Math.round(s.gdp||0).toLocaleString(),''],
      ['price_index','物价',(s.price_index||100).toFixed(1),''],
      ['stock_price','股价',(s.stock_price||0).toFixed(1),''],
      ['unemployment','失业',(s.unemployment||0)+'%',parseFloat(s.unemployment)>15?'danger':''],
      ['gini','基尼',(s.gini||0).toFixed(3),parseFloat(s.gini)>0.4?'warn':''],
      ['emp_rate','就业',(s.emp_rate||0)+'%',''],
      ['chart_vol','波动',(s.chart_vol||0).toFixed(3),(s.chart_vol||0)>0.3?'danger':'warn'],
      ['chart_bdr','坏账',(s.chart_bdr||0).toFixed(1)+'%',(s.chart_bdr||0)>10?'danger':'warn'],
      ['chart_systemic','风险',(s.chart_systemic||0).toFixed(3),(s.chart_systemic||0)>0.2?'danger':'warn'],
      ['bankrupt','破产',s.bankrupt||0,s.bankrupt>0?'warn':''],
      ['loans','贷款',Math.round(s.loans||0).toLocaleString(),''],
      ['govt_rev','政府收入',Math.round(s.govt_rev||0).toLocaleString(),''],
    ].map(([k,v,c,g])=>'<div class=stat><div class=stat-label>'+k+'</div><div class="stat-value '+g+'">'+c+'</div></div>').join('');
    updateChart();
  }catch(e){console.error(e);}
}

function updateChart(){
  let data=hist.map(h=>h[field]||0);
  if(!chart){
    chart=new Chart(document.getElementById('chart'),{
      type:'line',
      data:{labels:[],datasets:[{label,data:[],borderColor:color,backgroundColor:color+'22',tension:.3,fill:true,pointRadius:2}]},
      options:{responsive:true,maintainAspectRatio:false,
        plugins:{legend:{display:false}},
        scales:{
          x:{grid:{color:'#e2e8f0'},ticks:{color:'#94a3b8',maxTicksLimit:12}},
          y:{grid:{color:'#e2e8f0'},ticks:{color:'#94a3b8'}}
        }
      }
    });
  }
  chart.data.labels=hist.map(h=>h.cycle);
  chart.data.datasets[0].label=label;
  chart.data.datasets[0].borderColor=color;
  chart.data.datasets[0].backgroundColor=color+'22';
  chart.data.datasets[0].data=data;
  chart.update('none');
}

async function step(){
  await fetch('/api/step',{method:'POST'});
  await fetchState();
}
let playTimer=null;
async function togglePlay(){
  if(playing){
    playing=false;clearInterval(playTimer);
    document.getElementById('btn-play').textContent='▶ 播放';
    document.getElementById('btn-play').className='btn btn-success';
    await fetch('/api/pause',{method:'POST'});
  }else{
    playing=true;
    document.getElementById('btn-play').textContent='⏸ 暂停';
    document.getElementById('btn-play').className='btn btn-warning';
    await fetch('/api/play',{method:'POST'});
    playTimer=setInterval(fetchState,500);
  }
}
async function doReset(){
  if(playing){playing=false;clearInterval(playTimer);}
  document.getElementById('btn-play').textContent='▶ 播放';
  document.getElementById('btn-play').className='btn btn-success';
  let p={};
  document.querySelectorAll('input[type=range]').forEach(s=>p[s.id.slice(2)]=parseFloat(s.value));
  await fetch('/api/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
  if(chart){chart.destroy();chart=null;}
  hist=[];
  await fetchState();
}
function sl(el){
  let k=el.id.slice(2),d=document.getElementById('v-'+k);
  if(d)d.textContent=el.value;
  fetch('/api/param',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({[k]:parseFloat(el.value)})});
}
const SCENARIOS={
  '经济危机':{base_interest_rate:15,tax_rate:25},
  '宽松政策':{base_interest_rate:1,tax_rate:5,subsidy:15},
  '高税高补贴':{tax_rate:40,subsidy:20,min_wage:15},
  '自由市场':{tax_rate:5,base_interest_rate:2,subsidy:0,min_wage:0},
  '政府刺激':{gov_purchase:150,tax_rate:12,subsidy:8},
  '金融危机':{base_interest_rate:20,tax_rate:30,shock_prob:15},
};
async function applyScenario(){
  let name=document.getElementById('scenario').value;
  let p=SCENARIOS[name]||{};
  for(let [k,v] of Object.entries(p)){
    let s=document.getElementById('s-'+k);
    if(s){s.value=v;let d=document.getElementById('v-'+k);if(d)d.textContent=v;}
    await fetch('/api/param',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({[k]:parseFloat(v)})});
  }
}
async function loadAgents(){
  let r=await fetch('/api/agents/'+encodeURIComponent(document.getElementById('agent-type').value));
  let a=await r.json();
  document.getElementById('agent-list').innerHTML=(a||[]).map(s=>'<div class=agent-item>'+s+'</div>').join('')||'<div style=color:#64748b>无</div>';
}
function exportCSV(){
  if(!hist.length)return;
  let keys=Object.keys(hist[0]);
  let csv='cycle,'+keys.join(',')+'\n';
  hist.forEach(h=>csv+=h.cycle+','+keys.map(k=>h[k]||0).join(',')+'\n');
  let a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));
  a.download='economy_simulation.csv';a.click();
}
buildTabs();
fetchState();
setInterval(fetchState,2000);
loadAgents();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    if _model is None:
        init_model()
    return render_template_string(HTML)


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
                "default_count": m.default_count,
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
    data = request.get_json() or {}
    with _model_lock:
        if _model:
            for k, v in data.items():
                if hasattr(_model, k):
                    try:
                        setattr(_model, k, float(v))
                    except Exception:
                        pass
    return jsonify({"ok": True})


@app.route("/api/agents/<path:agent_type>")
def api_agents(agent_type):
    with _model_lock:
        if _model is None:
            return jsonify([])
        agents = []
        if agent_type == "家庭":
            for h in _model.households:
                tier = getattr(h, 'income_tier', None)
                tier_map = {"low": "低", "middle": "中", "high": "高"}
                ts = tier_map.get(tier.value if tier else "", "?") if tier else "?"
                agents.append(
                    f"H#{h.unique_id} 现:{round(h.cash,0)} 富:{round(h.wealth,0)} "
                    f"{'就业' if h.employed else '失业'} 薪:{round(h.salary,1)} 股:{h.shares_owned} [{ts}]"
                )
        elif agent_type == "企业":
            for f in _model.firms:
                ind = getattr(f, 'industry', None)
                ind_map = {"manufacturing": "制造", "service": "服务", "tech": "科技"}
                ind_str = ind_map.get(ind.value if ind else "", "?") if ind else "?"
                agents.append(
                    f"F#{f.unique_id} 现:{round(f.cash,0)} 富:{round(f.wealth,0)} "
                    f"产:{round(f.production,1)} 库:{round(f.inventory,1)} 员:{f.employees} [{ind_str}]"
                )
        elif agent_type == "交易者":
            for t in _model.traders:
                agents.append(
                    f"T#{t.unique_id} 现:{round(t.cash,0)} 富:{round(t.wealth,0)} 股:{t.shares}"
                )
        elif agent_type == "银行":
            for b in _model.banks:
                agents.append(
                    f"B#{b.unique_id} 准:{round(b.reserves,0)} 富:{round(b.wealth,0)}"
                )
    return jsonify(agents)


if __name__ == "__main__":
    init_model()
    print("经济沙盘 Flask 版: http://127.0.0.1:8523")
    app.run(host="0.0.0.0", port=8523, debug=False, threaded=True)
