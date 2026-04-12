"""
生成一个完全自包含的测试页面——SVG 直接写在 HTML 里，不需要任何 AJAX。
如果这个页面的图表能显示，说明 SVG 渲染没问题，问题在 AJAX。
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'C:/Users/Kanyun/.qclaw/workspace/mesa-econ')

from app import make_svg, init_model, step_model, _record, _history, _history_lock, _model_lock

init_model()
# Run 10 steps to get data
for _ in range(10):
    step_model()

with _history_lock:
    hist = list(_history)

gdp_vals = [h['gdp'] for h in hist]
unemp_vals = [h['unemployment'] for h in hist]
gini_vals = [h['gini'] for h in hist]

svg_gdp = make_svg(gdp_vals, color="#16a34a")
svg_unemp = make_svg(unemp_vals, color="#dc2626")
svg_gini = make_svg(gini_vals, color="#9333ea")

stats_cards = ""
if hist:
    last = hist[-1]
    cards = [
        ("GDP", f"${last.get('gdp',0):.0f}", ""),
        ("失业率", f"{last.get('unemployment',0):.1f}%", "danger" if last.get('unemployment',0) > 15 else ""),
        ("基尼", f"{last.get('gini',0):.3f}", "warn" if last.get('gini',0) > 0.4 else ""),
        ("股价", f"{last.get('stock_price',0):.1f}", ""),
        ("物价", f"{last.get('price_index',100):.1f}", ""),
        ("就业率", f"{last.get('emp_rate',0):.1f}%", ""),
        ("贷款", f"${last.get('loans',0):.0f}", ""),
        ("政府收入", f"${last.get('govt_rev',0):.0f}", ""),
        ("破产", f"{last.get('bankrupt',0)}", "warn" if last.get('bankrupt',0) > 0 else ""),
        ("波动率", f"{last.get('chart_vol',0):.3f}", "danger" if last.get('chart_vol',0) > 0.3 else "warn"),
    ]
    for label, value, cls in cards:
        stats_cards += f'<div class="scard"><div class="slabel">{label}</div><div class="sval {cls}">{value}</div></div>\n'

# Also show the actual data table
table_rows = ""
for h in hist[-5:]:
    table_rows += f"<tr><td>{h['cycle']}</td><td>{h['gdp']:.0f}</td><td>{h['unemployment']:.1f}%</td><td>{h['gini']:.3f}</td><td>{h['stock_price']:.1f}</td></tr>\n"

TEST_HTML = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>SVG 测试页（零AJAX）</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:#f8fafc;color:#1e293b;padding:20px}}
h1{{margin-bottom:16px}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:14px;margin-bottom:12px}}
.sgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:8px}}
.scard{{background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:8px 10px}}
.slabel{{font-size:10px;color:#94a3b8;text-transform:uppercase}}
.sval{{font-size:18px;font-weight:700;color:#0f172a;margin-top:2px}}
.sval.warn{{color:#d97706}}
.sval.danger{{color:#dc2626}}
h2{{font-size:12px;color:#64748b;margin-bottom:8px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}}
th,td{{border:1px solid #e2e8f0;padding:6px 10px;text-align:right}}
th{{background:#f1f5f9;text-align:center}}
.badge{{display:inline-block;background:#22c55e;color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;margin-left:12px}}
</style>
</head>
<body>
<h1>SVG 图表测试 <span class="badge">零 AJAX</span></h1>

<div class="card">
  <h2>第 {hist[-1]['cycle'] if hist else 0} 轮宏观指标</h2>
  <div class="sgrid">
  {stats_cards}
  </div>
</div>

<div class="card">
  <h2>GDP 趋势</h2>
  {svg_gdp}
</div>

<div class="card">
  <h2>失业率趋势</h2>
  {svg_unemp}
</div>

<div class="card">
  <h2>基尼系数趋势</h2>
  {svg_gini}
</div>

<div class="card">
  <h2>最近 5 轮数据</h2>
  <table>
    <tr><th>轮次</th><th>GDP</th><th>失业率</th><th>基尼</th><th>股价</th></tr>
    {table_rows}
  </table>
</div>

<div style="margin-top:20px;padding:12px;background:#f1f5f9;border-radius:6px;font-size:13px;color:#64748b">
  此页面所有图表均为服务器端 SVG，无任何 JavaScript 和 AJAX 调用。<br>
  如果你看到上面的 SVG 折线图 → 说明 SVG 渲染正常，主页面的问题在 JavaScript/AJAX 层。
</div>
</body>
</html>
"""

with open('C:/Users/Kanyun/.qclaw/workspace/mesa-econ/static/test.html', 'w', encoding='utf-8') as f:
    f.write(TEST_HTML)

print(f"Test page written: {len(TEST_HTML)} bytes")
print(f"History: {len(hist)} entries, GDP range: {min(gdp_vals):.0f} - {max(gdp_vals):.0f}")
