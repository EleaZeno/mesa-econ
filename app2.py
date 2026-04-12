"""
Mesa 经济沙盘 v3.3 - 最简服务端渲染（零 JS，零 XHR）
"""
import io, threading
from flask import Flask, request, redirect

from model import EconomyModel

app = Flask(__name__)
_lock = threading.Lock()
_md = None
_hist = []
_hist_lock = threading.Lock()
_running = False
_stop = threading.Event()
_thr = None


def init(**kw):
    global _md, _hist, _running, _thr
    defaults = dict(n_households=20, n_firms=10, n_traders=20,
                    tax_rate=0.15, base_interest_rate=0.05, min_wage=7,
                    productivity=1.0, subsidy=0.0, gov_purchase=0.0,
                    capital_gains_tax=0.10, shock_prob=0.02)
    defaults.update((k, v) for k, v in kw.items() if v is not None)
    _running = False
    if _thr:
        _stop.set()
    with _lock:
        _md = EconomyModel(**defaults)
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
                'emp': emp,
                'nh': nh,
                'rate': round(emp / nh * 100 if nh else 0, 1),
            }
        except Exception:
            ent = {'cycle': getattr(_md, 'cycle', 0)}
    with _hist_lock:
        _hist.append(ent)
        if len(_hist) > 500:
            del _hist[:-500]


def _svg(vals, color='#3b82f6', w=640, h=180):
    if not vals:
        vals = [0]
    n = len(vals)
    LPAD, RPAD, TPAD, BPAD = 48, 8, 8, 28
    W = w - LPAD - RPAD
    H = h - TPAD - BPAD
    mn = min(vals)
    mx = max(vals)
    rng = mx - mn if mx != mn else 1

    lines = []
    for i in range(5):
        y = TPAD + H * i / 4
        v = mx - rng * i / 4
        lines.append('<line x1="{x}" y1="{y:.1f}" x2="{x2}" y2="{y:.1f}" stroke="#e2e8f0"/>'.format(
            x=LPAD, x2=LPAD + W, y=y, y2=y))
        lines.append('<text x="{x}" y="{y:.1f}" text-anchor="end" font-size="10" fill="#94a3b8">{v:.0f}</text>'.format(
            x=LPAD - 4, y=y + 3, v=v))

    pts = ' '.join('{x:.0f},{y:.0f}'.format(
        x=LPAD + W * i / max(1, n - 1),
        y=TPAD + H * (1 - (v - mn) / rng))
        for i, v in enumerate(vals))

    fill = ('M {x} {yh} ' + ' '.join(
        'L {x:.0f} {y:.0f}'.format(
            x=LPAD + W * i / max(1, n - 1),
            y=TPAD + H * (1 - (v - mn) / rng))
        for i, v in enumerate(vals)) + ' L {x2} {yh} Z').format(x=LPAD, x2=LPAD + W, yh=TPAD + H)

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'width="100%" height="{h}" viewBox="0 0 {w} {h}">'
        '{lines}'
        '<polyline points="{pts}" fill="none" stroke="{clr}" stroke-width="2" stroke-linejoin="round"/>'
        '<path d="{fill}" fill="{clr}" opacity="0.1"/>'
        '<line x1="{x}" y1="{y}" x2="{x2}" y2="{y}" stroke="#cbd5e1"/>'
        '<line x1="{x}" y1="{y}" x2="{x}" y2="{y2}" stroke="#cbd5e1"/>'
        '</svg>'
    ).format(
        h=h, w=w, x=LPAD, x2=LPAD + W, y=TPAD, y2=TPAD + H,
        lines=''.join(lines), pts=pts, fill=fill, clr=color)
    return svg


SCENARIOS = [
    ('ecocrisis', '经济危机(利率15%,税率25%)'),
    ('loose', '宽松政策(利率1%,税率5%)'),
    ('hightax', '高税高补贴(税率40%,工资15)'),
    ('freemarket', '自由市场(利率2%,税率5%)'),
    ('govstim', '政府刺激(购买150)'),
    ('fin危机', '金融危机(利率20%,税率30%)'),
]

SCEN_MAP = {
    'ecocrisis': dict(base_interest_rate=0.15, tax_rate=0.25),
    'loose': dict(base_interest_rate=0.01, tax_rate=0.05, subsidy=15.0),
    'hightax': dict(tax_rate=0.40, subsidy=20.0, min_wage=15.0),
    'freemarket': dict(tax_rate=0.05, base_interest_rate=0.02, subsidy=0.0, min_wage=0.0),
    'govstim': dict(gov_purchase=150.0, tax_rate=0.12, subsidy=8.0),
    'fin危机': dict(base_interest_rate=0.20, tax_rate=0.30, shock_prob=0.15),
}

SLIDERS = [
    ('n_households', '家庭数量', 5, 80, 5),
    ('n_firms', '企业数量', 3, 40, 1),
    ('tax_rate', '所得税率(%)', 0, 45, 1),
    ('base_interest_rate', '基准利率(%)', 0, 25, 0.5),
    ('min_wage', '最低工资', 0, 20, 0.5),
    ('productivity', '全要素生产率', 0.1, 3, 0.1),
    ('gov_purchase', '政府购买', 0, 200, 5),
    ('shock_prob', '冲击概率(%)', 0, 20, 1),
]


def _fmt(val, typ='int'):
    if typ == 'int':
        return '{:,}'.format(int(val))
    elif typ == 'pct':
        return '{:.1f}%'.format(val)
    elif typ == 'dec3':
        return '{:.3f}'.format(val)
    elif typ == 'dec1':
        return '{:.1f}'.format(val)
    return str(val)


def _build_page():
    with _lock:
        if _md is None:
            cycle, last = 0, {}
        else:
            cycle = _md.cycle
            try:
                emp = sum(1 for h in _md.households if h.employed)
                nh = len(_md.households)
                vol = getattr(_md, 'stock_volatility', 0.0)
                bdr = getattr(_md, 'bank_bad_debt_rate', 0.0) * 100
                last = {
                    'gdp': round(_md.gdp),
                    'unemp': round(_md.unemployment * 100, 1),
                    'price': round(getattr(_md, 'price_index', 100.0), 1),
                    'stock': round(_md.stock_price, 1),
                    'vol': round(vol, 3),
                    'bdr': round(bdr, 1),
                    'loans': round(_md.total_loans_outstanding),
                    'rev': round(_md.govt_revenue),
                    'bankrupt': _md.bankrupt_count,
                    'gini': round(_md.gini, 3),
                    'emp': emp,
                    'nh': nh,
                    'rate': round(emp / nh * 100 if nh else 0, 1),
                }
            except Exception:
                last = {}
    with _hist_lock:
        hist = list(_hist)

    # Chart data
    chart_key = request.args.get('chart', 'gdp')
    chart_vals = [h.get(chart_key, 0) for h in hist]
    chart_labels = {
        'gdp': ('GDP', '#16a34a'),
        'unemp': ('失业率(%)', '#dc2626'),
        'gini': ('基尼系数', '#9333ea'),
        'stock': ('股价', '#d97706'),
        'price': ('物价指数', '#64748b'),
        'vol': ('波动率', '#f97316'),
        'bdr': ('坏账率(%)', '#ec4899'),
        'loans': ('信贷总量', '#0891b2'),
    }
    clbl, ccolor = chart_labels.get(chart_key, ('GDP', '#16a34a'))
    svg_html = _svg(chart_vals, color=ccolor)

    # Stats cards
    stats_defs = [
        ('gdp', 'GDP', 'int', False),
        ('unemp', '失业率', 'pct', last.get('unemp', 0) > 15),
        ('price', '物价', 'dec1', False),
        ('stock', '股价', 'dec1', False),
        ('gini', '基尼', 'dec3', last.get('gini', 0) > 0.4),
        ('rate', '就业率', 'pct', False),
        ('vol', '波动率', 'dec3', last.get('vol', 0) > 0.3),
        ('bdr', '坏账率', 'pct', last.get('bdr', 0) > 10),
        ('bankrupt', '破产', 'int', last.get('bankrupt', 0) > 0),
        ('loans', '贷款', 'int', False),
        ('rev', '政府收入', 'int', False),
    ]

    # Agent list
    atype = request.args.get('atype', 'household')
    agents_html = ''
    with _lock:
        if _md:
            if atype == 'household':
                tmap = {'low': '低收入', 'middle': '中收入', 'high': '高收入'}
                for h in _md.households:
                    tier = tmap.get(str(getattr(h, 'income_tier', '')), '?')
                    status = '就业' if h.employed else '失业'
                    agents_html += (
                        '<div style="font-family:monospace;font-size:11px;'
                        'background:#f8fafc;padding:2px 8px;margin-bottom:2px;'
                        'border-radius:4px">'
                        'H#{} 现:{} 富:{} {}/{} 薪:{} 股:{} [{}]</div>'
                    ).format(
                        h.unique_id, int(h.cash), int(h.wealth),
                        status, int(h.salary), getattr(h, 'shares_owned', 0), tier)
            elif atype == 'firm':
                for f in _md.firms:
                    agents_html += (
                        '<div style="font-family:monospace;font-size:11px;'
                        'background:#f8fafc;padding:2px 8px;margin-bottom:2px;'
                        'border-radius:4px">'
                        'F#{} 现:{} 富:{} 产:{} 库:{} 员:{}</div>'
                    ).format(
                        f.unique_id, int(f.cash), int(f.wealth),
                        round(f.production, 1), round(f.inventory, 1), f.employees)
            elif atype == 'trader':
                for t in _md.traders:
                    agents_html += (
                        '<div style="font-family:monospace;font-size:11px;'
                        'background:#f8fafc;padding:2px 8px;margin-bottom:2px;'
                        'border-radius:4px">'
                        'T#{} 现:{} 富:{} 股:{}</div>'
                    ).format(t.unique_id, int(t.cash), int(t.wealth), getattr(t, 'shares', 0))
            elif atype == 'bank':
                for b in _md.banks:
                    agents_html += (
                        '<div style="font-family:monospace;font-size:11px;'
                        'background:#f8fafc;padding:2px 8px;margin-bottom:2px;'
                        'border-radius:4px">'
                        'B#{} 准:{} 富:{}</div>'
                    ).format(b.unique_id, int(getattr(b, 'reserves', 0)), int(b.wealth))

    html = (
        '<!DOCTYPE html>\n'
        '<html lang="zh">\n'
        '<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<title>经济沙盘 v3.3</title>\n'
        '<style>\n'
        '*{box-sizing:border-box;margin:0;padding:0}\n'
        'body{font-family:system-ui,sans-serif;background:#f8fafc;'
        'color:#1e293b;padding:16px;font-size:14px;max-width:960px;margin:0 auto}\n'
        'h1{font-size:20px;margin-bottom:12px}\n'
        'h2{font-size:11px;color:#64748b;margin-bottom:8px;'
        'text-transform:uppercase;letter-spacing:.05em}\n'
        '.card{background:#fff;border:1px solid #e2e8f0;'
        'border-radius:8px;padding:14px;margin-bottom:10px}\n'
        '.ctrl{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}\n'
        '.btn{border:none;padding:8px 16px;border-radius:6px;cursor:pointer;'
        'font-size:13px;font-family:inherit}\n'
        '.bg{background:#16a34a;color:#fff}.bg:hover{background:#15803d}\n'
        '.bb{background:#3b82f6;color:#fff}.bb:hover{background:#2563eb}\n'
        '.bo{background:#d97706;color:#fff}.bo:hover{background:#b45309}\n'
        '.br{background:#dc2626;color:#fff}.br:hover{background:#b91c1c}\n'
        '.c2{display:grid;grid-template-columns:1fr 1fr;gap:10px}\n'
        '@media(max-width:600px){.c2{grid-template-columns:1fr}}\n'
        '.sc{display:grid;grid-template-columns:repeat(auto-fill,minmax(115px,1fr));gap:8px}\n'
        '.sc>div{background:#f8fafc;border:1px solid #e2e8f0;'
        'border-radius:6px;padding:8px 10px}\n'
        '.sl{font-size:10px;color:#94a3b8;text-transform:uppercase}\n'
        '.sv{font-size:18px;font-weight:700;color:#0f172a;margin-top:2px}\n'
        '.dw{color:#d97706}.dr{color:#dc2626}.dg{color:#16a34a}\n'
        '.tabs{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px}\n'
        '.tb{background:#e2e8f0;color:#475569;border:none;padding:5px 10px;'
        'border-radius:5px;cursor:pointer;font-size:12px;font-family:inherit;margin:2px}\n'
        '.ta{background:#3b82f6;color:#fff;border:none;padding:5px 10px;'
        'border-radius:5px;cursor:pointer;font-size:12px;font-family:inherit;margin:2px}\n'
        'select{width:100%;padding:6px;border-radius:6px;'
        'border:1px solid #cbd5e1;background:#fff;font-size:13px;'
        'font-family:inherit;margin-bottom:8px}\n'
        '.srow{margin-bottom:6px}\n'
        '.slbl{display:flex;justify-content:space-between;'
        'margin-bottom:2px;font-size:13px;color:#475569}\n'
        'input[type=range]{width:100%;accent-color:#3b82f6;height:4px}\n'
        '.alist{max-height:170px;overflow-y:auto;font-size:12px;'
        'line-height:1.7}\n'
        '.aitem{font-family:monospace;background:#f8fafc;'
        'border-radius:4px;padding:2px 8px;margin-bottom:2px}\n'
        '.cyc{color:#94a3b8;margin-left:auto}\n'
        '</style>\n'
        '</head>\n'
        '<body>\n'
        '<h1>经济沙盘 <span style="color:#3b82f6">v3.3</span>'
        '<span class="cyc">第 {} 轮</span></h1>\n'
    ).format(cycle)

    # Control bar
    html += '<div class="card ctrl">\n'
    html += '<form method="get" style="display:inline">'
    html += '<button type="submit" name="action" value="play" class="btn bg">播放</button></form>\n'
    html += '<form method="get" style="display:inline">'
    html += '<button type="submit" name="action" value="pause" class="btn bo">暂停</button></form>\n'
    html += '<form method="get" style="display:inline">'
    html += '<button type="submit" name="action" value="step" class="btn bb">单步</button></form>\n'
    html += '<form method="get" style="display:inline">'
    html += '<button type="submit" name="action" value="reset" class="btn br">重置</button></form>\n'
    html += '<span class="cyc">第 {} 轮</span>\n'.format(cycle)
    html += '</div>\n'

    # Stats
    html += '<div class="card">\n<h2>宏观指标</h2>\n<div class="sc">\n'
    for key, lbl, typ, warn in stats_defs:
        val = last.get(key, 0)
        cls = 'dw' if warn else ''
        html += '<div><div class="sl">{}</div><div class="sv {}">{}</div></div>\n'.format(
            lbl, cls, _fmt(val, typ))
    html += '</div>\n</div>\n'

    # Chart
    html += '<div class="card">\n<h2>图表</h2>\n<div class="tabs">\n'
    for key, (lbl, _) in chart_labels.items():
        cls = 'ta' if key == chart_key else 'tb'
        html += '<form method="get" style="display:inline">"
        html += '<button type="submit" name="chart" value="{}" class="{}">{}</button>\n'.format(
            key, cls, lbl)
        html += '</form>\n'
    html += '</div>\n{}\n</div>\n'.format(svg_html)

    # Two column
    html += '<div class="c2">\n'

    # Left: sliders
    html += '<div class="card">\n<h2>经济参数</h2>\n<form method="get">\n'
    for key, lbl, mn, mx, st in SLIDERS:
        v = getattr(_md, key, None) if _md else None
        # Convert display units
        if key == 'tax_rate' or key == 'base_interest_rate' or key == 'shock_prob':
            v = round((v or 0) * 100, 1)
        else:
            v = v or (20 if key == 'n_households' else 10 if key == 'n_firms' else 0)
        html += '<div class="srow">\n'
        html += '<div class="slbl"><span>{}</span><b>{}{}</b></div>\n'.format(
            lbl, v, '%' if key in ('tax_rate', 'base_interest_rate', 'shock_prob') else '')
        html += '<input type="range" name="{}" min="{}" max="{}" step="{}" value="{}">\n'.format(
            key, mn, mx, st, v)
        html += '</div>\n'
    html += '<button type="submit" name="action" value="apply_param" '
    html += 'class="btn bb" style="margin-top:8px;width:100%">应用参数</button>\n'
    html += '</form>\n</div>\n'

    # Right: scenarios + agents
    html += '<div class="card">\n<h2>预设场景</h2>\n<form method="get">\n<select name="scen">\n'
    html += '<option value="">-- 选择场景 --</option>\n'
    for val, lbl in SCENARIOS:
        html += '<option value="{}">{}</option>\n'.format(val, lbl)
    html += '</select>\n'
    html += '<button type="submit" name="action" value="apply_scen" '
    html += 'class="btn bb" style="width:100%;margin-bottom:12px">应用场景</button>\n'
    html += '</form>\n'

    html += '<h2>Agent 详情</h2>\n<div class="tabs">\n'
    for val, lbl in [('household', '家庭'), ('firm', '企业'),
                       ('trader', '交易者'), ('bank', '银行')]:
        cls = 'ta' if val == atype else 'tb'
        html += '<form method="get" style="display:inline">'
        html += '<button type="submit" name="atype" value="{}" class="{}">{}</button>\n'.format(
            val, cls, lbl)
        html += '</form>\n'
    html += '</div>\n'
    html += '<div class="alist">\n'
    html += agents_html or '<div style="color:#94a3b8">无</div>\n'
    html += '</div>\n</div>\n'

    html += '</div>\n'  # end c2

    # Export
    html += '<div class="card">\n'
    html += '<form method="get">\n'
    html += '<button type="submit" name="action" value="export_csv" class="btn bb">下载 CSV</button>\n'
    html += '</form>\n</div>\n'

    html += '</body>\n</html>\n'
    return html


@app.route("/")
def index():
    action = request.args.get('action', '')

    if action == 'step':
        step()
    elif action == 'play':
        global _running, _thr
        _running = True
        if not (_thr and _thr.is_alive()):
            _stop.clear()
            _thr = threading.Thread(target=_play_loop, daemon=True)
            _thr.start()
    elif action == 'pause':
        global _running
        _running = False
    elif action == 'reset':
        init()
    elif action == 'apply_param':
        params = {}
        for k in ['n_households', 'n_firms', 'min_wage', 'productivity',
                   'gov_purchase']:
            v = request.args.get(k)
            if v:
                try:
                    params[k] = float(v)
                except Exception:
                    pass
        for k in ['tax_rate', 'base_interest_rate', 'shock_prob']:
            v = request.args.get(k)
            if v:
                try:
                    params[k] = float(v) / 100
                except Exception:
                    pass
        if params:
            init(**params)
    elif action == 'apply_scen':
        scen = request.args.get('scen', '')
        if scen in SCEN_MAP:
            init(**SCEN_MAP[scen])
    elif action == 'export_csv':
        buf = io.StringIO()
        with _hist_lock:
            h = list(_hist)
        if h:
            keys = list(h[0].keys())
            buf.write(','.join(keys) + '\n')
            for row in h:
                buf.write(','.join(str(row.get(k, '')) for k in keys) + '\n')
        from flask import Response
        return Response(
            buf.getvalue().encode('utf-8'),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=economy.csv'}
        )

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
