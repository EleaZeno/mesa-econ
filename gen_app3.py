"""Generator script - creates a clean app.py using string concatenation"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

# The v3.3 app.py source
SOURCE = '''"""
Mesa 经济沙盘 v3.3 - 纯服务端渲染（零 JS，零 XHR）
"""
import io, threading
from flask import Flask, request

from model import EconomyModel

app = Flask(__name__)
_lock = threading.Lock()
_md = None
_hist = []
_hl = threading.Lock()
_running = False
_stop = threading.Event()
_thr = None

def init(**kw):
    global _md, _hist, _running, _thr
    d = dict(n_households=20, n_firms=10, n_traders=20,
             tax_rate=0.15, base_interest_rate=0.05, min_wage=7,
             productivity=1.0, subsidy=0.0, gov_purchase=0.0,
             capital_gains_tax=0.10, shock_prob=0.02)
    d.update((k, v) for k, v in kw.items() if v is not None)
    global _running, _thr
    _running = False
    if _thr:
        _stop.set()
    with _lock:
        _md = EconomyModel(**d)
    _hist.clear()
    _rec()

def step():
    global _md
    with _lock:
        if _md:
            _md.step()
    _rec()

def _rec():
    global _hist
    with _lock:
        if _md is None:
            return
        m = _md
        try:
            emp = sum(1 for h in m.households if h.employed)
            nh = len(m.households)
            vol = getattr(m, 'stock_volatility', 0.0)
            bdr = getattr(m, 'bank_bad_debt_rate', 0.0) * 100
            ent = {
                'cycle': m.cycle,
                'gdp': round(m.gdp),
                'unemp': round(m.unemployment * 100, 1),
                'price': round(getattr(m, 'price_index', 100.0), 1),
                'stock': round(m.stock_price, 1),
                'vol': round(vol, 3),
                'bdr': round(bdr, 1),
                'loans': round(m.total_loans_outstanding),
                'rev': round(m.govt_revenue),
                'bankrupt': m.bankrupt_count,
                'gini': round(m.gini, 3),
                'emp': emp, 'nh': nh,
                'rate': round(emp / nh * 100 if nh else 0, 1),
            }
        except Exception:
            ent = {'cycle': getattr(_md, 'cycle', 0)}
    with _hl:
        _hist.append(ent)
        if len(_hist) > 500:
            del _hist[:-500]

def _svg(vals, color='#3b82f6', w=640, h=180):
    if not vals:
        vals = [0]
    n = len(vals)
    LP, RP, TP, BP = 48, 8, 8, 28
    W = w - LP - RP
    H = h - TP - BP
    mn = min(vals)
    mx = max(vals)
    rng = mx - mn if mx != mn else 1
    g = ''
    for i in range(5):
        y = TP + H * i / 4
        v = mx - rng * i / 4
        g += '<line x1="{x}" y1="{y:.1f}" x2="{x2}" y2="{y:.1f}" stroke="#e2e8f0"/>'.format(x=LP, x2=LP+W, y=y, y2=y)
        g += '<text x="{x}" y="{y:.1f}" text-anchor="end" font-size="10" fill="#94a3b8">{v:.0f}</text>'.format(x=LP-4, y=y+3, v=v)
    pts = ' '.join('{x:.0f},{y:.0f}'.format(x=LP+W*i//max(1,n-1), y=TP+H*(1-(v-mn)/rng)) for i, v in enumerate(vals))
    fp = ' '.join('L {x:.0f} {y:.0f}'.format(x=LP+W*i//max(1,n-1), y=TP+H*(1-(v-mn)/rng)) for i, v in enumerate(vals))
    fill = 'M {x} {yh} '.format(x=LP, yh=TP+H) + ' '.join('L {x:.0f} {y:.0f}'.format(x=LP+W*i//max(1,n-1), y=TP+H*(1-(v-mn)/rng)) for i, v in enumerate(vals)) + ' L {x2} {yh} Z'.format(x2=LP+W, yh=TP+H)
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="{}" viewBox="0 0 {} {}">'.format(h, w, h)
    svg += g
    svg += '<polyline points="{}" fill="none" stroke="{}" stroke-width="2" stroke-linejoin="round"/>'.format(pts, color)
    svg += '<path d="{}" fill="{}" opacity="0.1"/>'.format(fill, color)
    svg += '<line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#cbd5e1"/><line x1="{}" y1="{}" x2="{}" y2="{}" stroke="#cbd5e1"/>'.format(LP, TP, LP+W, TP, LP, TP, LP, TP+H)
    svg += '</svg>'
    return svg

SCENARIOS = [
    ('ecocrisis', '经济危机(利率15%,税率25%)'),
    ('loose', '宽松政策(利率1%,税率5%)'),
    ('hightax', '高税高补贴(税率40%,工资15)'),
    ('freemarket', '自由市场(利率2%,税率5%)'),
    ('govstim', '政府刺激(购买150)'),
    ('fin_cris', '金融危机(利率20%,税率30%)'),
]
SCEN_MAP = {
    'ecocrisis': dict(base_interest_rate=0.15, tax_rate=0.25),
    'loose': dict(base_interest_rate=0.01, tax_rate=0.05, subsidy=15.0),
    'hightax': dict(tax_rate=0.40, subsidy=20.0, min_wage=15.0),
    'freemarket': dict(tax_rate=0.05, base_interest_rate=0.02, subsidy=0.0, min_wage=0.0),
    'govstim': dict(gov_purchase=150.0, tax_rate=0.12, subsidy=8.0),
    'fin_cris': dict(base_interest_rate=0.20, tax_rate=0.30, shock_prob=0.15),
}
SLIDERS = [
    ('n_households', '家庭数量', 5, 80, 5, 'int'),
    ('n_firms', '企业数量', 3, 40, 1, 'int'),
    ('tax_rate', '所得税率(%)', 0, 45, 1, 'pct'),
    ('base_interest_rate', '基准利率(%)', 0, 25, 0.5, 'pct'),
    ('min_wage', '最低工资', 0, 20, 0.5, 'dec1'),
    ('productivity', '全要素生产率', 0.1, 3, 0.1, 'dec1'),
    ('gov_purchase', '政府购买', 0, 200, 5, 'int'),
    ('shock_prob', '冲击概率(%)', 0, 20, 1, 'pct'),
]
CHART_KEYS = [('gdp','GDP'),('unemp','失业率(%)'),('gini','基尼系数'),
              ('stock','股价'),('price','物价指数'),('vol','波动率'),
              ('bdr','坏账率(%)'),('loans','信贷总量')]
CHART_CLR = {'gdp':'#16a34a','unemp':'#dc2626','gini':'#9333ea',
             'stock':'#d97706','price':'#64748b','vol':'#f97316',
             'bdr':'#ec4899','loans':'#0891b2'}

def _f(v, t):
    if t == 'int': return '{:,}'.format(int(v))
    if t == 'pct': return '{:.1f}%'.format(v)
    if t == 'dec1': return '{:.1f}'.format(v)
    if t == 'dec3': return '{:.3f}'.format(v)
    return str(v)

def _build_page():
    with _lock:
        if _md is None:
            cyc, last = 0, {}
        else:
            cyc = _md.cycle
            try:
                emp = sum(1 for h in _md.households if h.employed)
                nh = len(_md.households)
                vol = getattr(_md, 'stock_volatility', 0.0)
                bdr = getattr(_md, 'bank_bad_debt_rate', 0.0) * 100
                last = {'gdp':round(_md.gdp),'unemp':round(_md.unemployment*100,1),
                        'price':round(getattr(_md,'price_index',100.0),1),
                        'stock':round(_md.stock_price,1),'vol':round(vol,3),
                        'bdr':round(bdr,1),'loans':round(_md.total_loans_outstanding),
                        'rev':round(_md.govt_revenue),'bankrupt':_md.bankrupt_count,
                        'gini':round(_md.gini,3),'emp':emp,'nh':nh,
                        'rate':round(emp/nh*100 if nh else 0,1)}
            except Exception:
                last = {}
    with _hl:
        hist = list(_hist)

    ck = request.args.get('chart', 'gdp')
    cv = [h.get(ck, 0) for h in hist]
    svg_html = _svg(cv, color=CHART_CLR.get(ck,'#3b82f6'))

    # Stats
    st = [
        ('gdp','GDP','int',False),
        ('unemp','失业率','pct',last.get('unemp',0)>15),
        ('price','物价','dec1',False),
        ('stock','股价','dec1',False),
        ('gini','基尼','dec3',last.get('gini',0)>0.4),
        ('rate','就业率','pct',False),
        ('vol','波动率','dec3',last.get('vol',0)>0.3),
        ('bdr','坏账率','pct',last.get('bdr',0)>10),
        ('bankrupt','破产','int',last.get('bankrupt',0)>0),
        ('loans','贷款','int',False),
        ('rev','政府收入','int',False),
    ]

    # Agents
    atype = request.args.get('atype', 'household')
    agents = ''
    with _lock:
        if _md:
            if atype == 'household':
                tm = {'low':'低收入','middle':'中收入','high':'高收入'}
                for h in _md.households:
                    tier = tm.get(str(getattr(h,'income_tier','')),'?')
                    st2 = '就业' if h.employed else '失业'
                    agents += '<div style="font-family:monospace;font-size:11px;background:#f8fafc;padding:2px 8px;margin-bottom:2px;border-radius:4px">H'
                    agents += '#' + str(h.unique_id) + ' 现:' + str(int(h.cash)) + ' 富:' + str(int(h.wealth))
                    agents += ' ' + st2 + '/' + str(int(h.salary)) + ' 股:' + str(getattr(h,'shares_owned',0)) + ' [' + tier + ']</div>'
            elif atype == 'firm':
                for f in _md.firms:
                    agents += '<div style="font-family:monospace;font-size:11px;background:#f8fafc;padding:2px 8px;margin-bottom:2px;border-radius:4px">F'
                    agents += '#' + str(f.unique_id) + ' 现:' + str(int(f.cash)) + ' 富:' + str(int(f.wealth))
                    agents += ' 产:' + str(round(f.production,1)) + ' 库:' + str(round(f.inventory,1)) + ' 员:' + str(f.employees) + '</div>'
            elif atype == 'trader':
                for t in _md.traders:
                    agents += '<div style="font-family:monospace;font-size:11px;background:#f8fafc;padding:2px 8px;margin-bottom:2px;border-radius:4px">T'
                    agents += '#' + str(t.unique_id) + ' 现:' + str(int(t.cash)) + ' 富:' + str(int(t.wealth)) + ' 股:' + str(getattr(t,'shares',0)) + '</div>'
            elif atype == 'bank':
                for b in _md.banks:
                    agents += '<div style="font-family:monospace;font-size:11px;background:#f8fafc;padding:2px 8px;margin-bottom:2px;border-radius:4px">B'
                    agents += '#' + str(b.unique_id) + ' 准:' + str(int(getattr(b,'reserves',0))) + ' 富:' + str(int(b.wealth)) + '</div>'

    html = [
        '<!DOCTYPE html><html lang="zh"><head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>经济沙盘 v3.3</title>',
        '<style>',
        '*{box-sizing:border-box;margin:0;padding:0}',
        'body{font-family:system-ui,sans-serif;background:#f8fafc;color:#1e293b;padding:16px;font-size:14px;max-width:960px;margin:0 auto}',
        'h1{font-size:20px;margin-bottom:12px}',
        'h2{font-size:11px;color:#64748b;margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em}',
        '.card{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:14px;margin-bottom:10px}',
        '.ctrl{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}',
        '.btn{border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:13px;font-family:inherit}',
        '.bg{background:#16a34a;color:#fff}.bg:hover{background:#15803d}',
        '.bb{background:#3b82f6;color:#fff}.bb:hover{background:#2563eb}',
        '.bo{background:#d97706;color:#fff}.bo:hover{background:#b45309}',
        '.br{background:#dc2626;color:#fff}.br:hover{background:#b91c1c}',
        '.c2{display:grid;grid-template-columns:1fr 1fr;gap:10px}',
        '@media(max-width:600px){.c2{grid-template-columns:1fr}}',
        '.sc{display:grid;grid-template-columns:repeat(auto-fill,minmax(115px,1fr));gap:8px}',
        '.sc>div{background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:8px 10px}',
        '.sl{font-size:10px;color:#94a3b8;text-transform:uppercase}',
        '.sv{font-size:18px;font-weight:700;color:#0f172a;margin-top:2px}',
        '.dw{color:#d97706}.dr{color:#dc2626}.dg{color:#16a34a}',
        '.tabs{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px}',
        '.tb{background:#e2e8f0;color:#475569;border:none;padding:5px 10px;border-radius:5px;cursor:pointer;font-size:12px;font-family:inherit;margin:2px}',
        '.ta{background:#3b82f6;color:#fff;border:none;padding:5px 10px;border-radius:5px;cursor:pointer;font-size:12px;font-family:inherit;margin:2px}',
        'select{width:100%;padding:6px;border-radius:6px;border:1px solid #cbd5e1;background:#fff;font-size:13px;font-family:inherit;margin-bottom:8px}',
        '.srow{margin-bottom:6px}',
        '.slbl{display:flex;justify-content:space-between;margin-bottom:2px;font-size:13px;color:#475569}',
        'input[type=range]{width:100%;accent-color:#3b82f6;height:4px}',
        '.alist{max-height:170px;overflow-y:auto;font-size:12px;line-height:1.7}',
        '.cyc{color:#94a3b8;margin-left:auto}',
        '</style></head><body>',
        '<h1>经济沙盘 <span style="color:#3b82f6">v3.3</span> <span class="cyc">第 ' + str(cyc) + ' 轮</span></h1>',
        '<div class="card ctrl">',
        '<form method="get" style="display:inline"><button type="submit" name="action" value="play" class="btn bg">播放</button></form>',
        '<form method="get" style="display:inline"><button type="submit" name="action" value="pause" class="btn bo">暂停</button></form>',
        '<form method="get" style="display:inline"><button type="submit" name="action" value="step" class="btn bb">单步</button></form>',
        '<form method="get" style="display:inline"><button type="submit" name="action" value="reset" class="btn br">重置</button></form>',
        '<span class="cyc" style="margin-left:12px">第 ' + str(cyc) + ' 轮</span>',
        '</div>',
        '<div class="card"><h2>宏观指标</h2><div class="sc">',
    ]
    for k, l, t, w in st:
        v = last.get(k, 0)
        cls = 'dw' if w else ''
        html.append('<div><div class="sl">' + l + '</div><div class="sv ' + cls + '">' + _f(v, t) + '</div></div>')
    html.append('</div></div>')

    html.append('<div class="card"><h2>图表</h2><div class="tabs">')
    for fk, fl in CHART_KEYS:
        cls = 'ta' if fk == ck else 'tb'
        html.append('<form method="get" style="display:inline"><button type="submit" name="chart" value="' + fk + '" class="' + cls + '">' + fl + '</button></form>')
    html.append('</div>' + svg_html + '</div>')

    html.append('<div class="c2">')

    # Left: sliders
    html.append('<div class="card"><h2>经济参数</h2><form method="get">')
    for k, l, mn, mx, st_v, typ in SLIDERS:
        v = getattr(_md, k, None) if _md else None
        if k in ('tax_rate', 'base_interest_rate', 'shock_prob'):
            dv = round((v or 0) * 100, 1)
            unit = '%'
        else:
            dv = v if v is not None else (20 if k == 'n_households' else 10 if k == 'n_firms' else 0)
            unit = ''
        html.append('<div class="srow"><div class="slbl"><span>' + l + '</span><b>' + str(dv) + unit + '</b></div>')
        html.append('<input type="range" name="' + k + '" min="' + str(mn) + '" max="' + str(mx) + '" step="' + str(st_v) + '" value="' + str(dv) + '"></div>')
    html.append('<button type="submit" name="action" value="apply_param" class="btn bb" style="margin-top:8px;width:100%">应用参数</button>')
    html.append('</form></div>')

    # Right: scenarios + agents
    html.append('<div class="card"><h2>预设场景</h2><form method="get">')
    html.append('<select name="scen">')
    html.append('<option value="">-- 选择场景 --</option>')
    for sv, sl in SCENARIOS:
        html.append('<option value="' + sv + '">' + sl + '</option>')
    html.append('</select>')
    html.append('<button type="submit" name="action" value="apply_scen" class="btn bb" style="width:100%;margin-bottom:12px">应用场景</button>')
    html.append('</form>')
    html.append('<h2>Agent 详情</h2><div class="tabs">')
    for av, al in [('household','家庭'),('firm','企业'),('trader','交易者'),('bank','银行')]:
        cls = 'ta' if av == atype else 'tb'
        html.append('<form method="get" style="display:inline"><button type="submit" name="atype" value="' + av + '" class="' + cls + '">' + al + '</button></form>')
    html.append('</div>')
    html.append('<div class="alist">' + (agents or '<div style="color:#94a3b8">无</div>') + '</div>')
    html.append('</div></div>')

    html.append('<div class="card"><form method="get"><button type="submit" name="action" value="export_csv" class="btn bb">下载 CSV</button></form></div>')
    html.append('</body></html>')
    return '\n'.join(html)

@app.route("/")
def index():
    global _running, _thr
    action = request.args.get('action', '')
    if action == 'step':
        step()
    elif action == 'play':
        _running = True
        if not (_thr and _thr.is_alive()):
            _stop.clear()
            _thr = threading.Thread(target=_play_loop, daemon=True)
            _thr.start()
    elif action == 'pause':
        _running = False
    elif action == 'reset':
        init()
    elif action == 'apply_param':
        params = {}
        for k in ('n_households','n_firms','min_wage','productivity','gov_purchase'):
            v = request.args.get(k)
            if v:
                try: params[k] = float(v)
                except: pass
        for k in ('tax_rate','base_interest_rate','shock_prob'):
            v = request.args.get(k)
            if v:
                try: params[k] = float(v) / 100
                except: pass
        if params:
            init(**params)
    elif action == 'apply_scen':
        scen = request.args.get('scen','')
        if scen in SCEN_MAP:
            init(**SCEN_MAP[scen])
    elif action == 'export_csv':
        buf = io.StringIO()
        with _hl:
            h = list(_hist)
        if h:
            keys = list(h[0].keys())
            buf.write(','.join(keys) + '\n')
            for row in h:
                buf.write(','.join(str(row.get(k,'')) for k in keys) + '\n')
        from flask import Response
        return Response(buf.getvalue().encode('utf-8'), mimetype='text/csv',
                       headers={'Content-Disposition':'attachment; filename=economy.csv'})
    return _build_page()

def _play_loop():
    global _running
    while not _stop.wait(0.5):
        if _running:
            step()

if __name__ == '__main__':
    init()
    print('经济沙盘 v3.3: http://127.0.0.1:8523')
    app.run(host='0.0.0.0', port=8523, debug=False, threaded=True)
'''

with open('C:/Users/Kanyun/.qclaw/workspace/mesa-econ/app.py', 'w', encoding='utf-8') as f:
    f.write(SOURCE)

print('app.py written:', len(SOURCE), 'chars')

# Compile check
import py_compile
try:
    py_compile.compile('C:/Users/Kanyun/.qclaw/workspace/mesa-econ/app.py', doraise=True)
    print('SYNTAX OK')
except py_compile.PyCompileError as e:
    print('SYNTAX ERROR:', e)
