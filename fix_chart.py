"""Fix ChartPanel: replace solara.Tabs/Tab with Select + single figure"""
with open('server.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find ChartPanel boundaries (lines 542-583)
chart_start = None
for i, l in enumerate(lines):
    if 'def ChartPanel()' in l:
        chart_start = i
        break

if chart_start is None:
    print('ChartPanel not found')
    exit(1)

# Find where it ends (next @solara.component)
chart_end = None
for i in range(chart_start + 1, len(lines)):
    if '@solara.component' in lines[i]:
        chart_end = i
        break

if chart_end is None:
    chart_end = len(lines)

print(f'Replacing ChartPanel lines {chart_start+1}-{chart_end}')

new_chart = [
    '@solara.component\n',
    'def ChartPanel():\n',
    '    """图表面板: Select 切换 + matplotlib 渲染经济指标"""\n',
    '    m = model_ref.value\n',
    '    if m is None:\n',
    '        solara.Text("模型加载中...", style=STYLE_DIM)\n',
    '        return\n',
    '\n',
    '    chart_data = get_chart_data(m)\n',
    '    COLOR_MAP = {\n',
    '        "stock_price": "#f59e0b",\n',
    '        "gdp": "#22c55e",\n',
    '        "unemployment": "#ef4444",\n',
    '        "avg_price": "#94a3b8",\n',
    '        "gini": "#a855f7",\n',
    '        "buy_orders": "#3b82f6",\n',
    '        "loans": "#06b6d4",\n',
    '        "stock_vol": "#f97316",\n',
    '        "bad_debt_rate": "#ec4899",\n',
    '        "systemic_risk": "#dc2626",\n',
    '        "default_count": "#b91c1c",\n',
    '        "gov_revenue": "#84cc16",\n',
    '    }\n',
    '\n',
    '    # 默认选第一个有数据的指标\n',
    '    available = [(k, l) for k, l in CHART_CONFIG if chart_data.get(k, [])]\n',
    '    if not available:\n',
    '        solara.Card("经济指标图表", margin=0, style=STYLE_DARK_CARD)\n',
    '        solara.Text("暂无图表数据", style=STYLE_DIM)\n',
    '        return\n',
    '\n',
    '    default_key = available[0][0]\n',
    '    selected_key = solara.use_state(default_key)\n',
    '    selected_label = next((l for k, l in CHART_CONFIG if k == selected_key), "")\n',
    '    vals = chart_data.get(selected_key, [])\n',
    '    color = COLOR_MAP.get(selected_key, "#3b82f6")\n',
    '\n',
    '    # 标签映射\n',
    '    label_map = dict(CHART_CONFIG)\n',
    '\n',
    '    with solara.Card("经济指标图表", margin=0, style=STYLE_DARK_CARD):\n',
    '        with solara.Row(gap="8px", align="center"):\n',
    '            solara.Text("选择指标:", style="font-size:13px;")\n',
    '            solara.Select(\n',
    '                label="指标",\n',
    '                value=selected_key,\n',
    '                values=[k for k, _ in available],\n',
    '                format=lambda v: label_map.get(v, v),\n',
    '            )\n',
    '        if vals:\n',
    '            solara.FigureMatplotlib(\n',
    '                lambda: render_matplotlib_figure(vals, selected_label, color)\n',
    '            )\n',
    '        else:\n',
    '            solara.Text("暂无该指标数据", style=STYLE_DIM)\n',
    '\n',
]

lines[chart_start:chart_end] = new_chart

with open('server.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print(f'Done: {len(lines)} lines')
