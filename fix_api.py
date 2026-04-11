"""修复 solara 1.56.0 API 兼容性问题"""
import re

with open('server.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. solara.Grid → solara.Columns(widths=[...])
content = content.replace(
    'with solara.Grid(columns=2):',
    'with solara.Columns(widths=[6, 6]):'
)
content = content.replace(
    'with solara.Grid(columns=4):',
    'with solara.Columns(widths=[3, 3, 3, 3]):'
)
content = content.replace(
    'with solara.Grid(columns=3, gap="6px 16px", style="font-size:13px;"):',
    'with solara.Columns(widths=[4, 4, 4], gutters=False):'
)
content = content.replace(
    'with solara.Grid(columns=2, gap="8px"):',
    'with solara.Columns(widths=[6, 6]):'
)
content = content.replace(
    'with solara.Grid(columns=2, gap="4px 24px",',
    'with solara.Columns(widths=[6, 6],'
)

# 2. Row(align=) → 去掉 align 参数（用 justify/align_items 代替）
content = content.replace(
    'with solara.Row(gap="8px", align="center"):',
    'with solara.Row(gap="8px", justify="center"):'
)
content = content.replace(
    'with solara.Row(gap="8px", align="center",):',
    'with solara.Row(gap="8px", justify="center"):'
)
content = content.replace(
    'with solara.Row(gap="8px", align="center"):',
    'with solara.Row(gap="8px", justify="center"):'
)
# 通用：去掉任何 align="..." from Row
content = re.sub(
    r'with solara\.Row\([^)]*align="[^"]*"[^)]*\):',
    lambda m: re.sub(r',?\s*align="[^"]*"', '', m.group()),
    content
)

# 3. VBox(style=) → 去掉 style 参数，改用 Solara.Style 包装
content = content.replace(
    'with solara.VBox(style="max-height:350px;overflow-y:auto;padding:4px;"):',
    'with solara.VBox(grow=True, align_items="stretch"):'
)
content = content.replace(
    'with solara.VBox(style="max-height:380px;overflow-y:auto;padding:4px;"):',
    'with solara.VBox(grow=True, align_items="stretch"):'
)
content = content.replace(
    'with solara.VBox(style="max-height:200px;overflow-y:auto;"):',
    'with solara.VBox(grow=True, align_items="stretch"):'
)
# 4. Card 去掉 style 参数（1.56 Card 不支持）
content = re.sub(
    r'with solara\.Card\([^)]*style=STYLE_DARK_CARD[^)]*\):',
    lambda m: m.group().replace(', style=STYLE_DARK_CARD', ''),
    content
)
content = re.sub(
    r'with solara\.Card\([^)]*style=STYLE_DARK[^)]*\):',
    lambda m: m.group().replace(', style=STYLE_DARK', ''),
    content
)
# 5. ChartPanel use_state → 加 noqa
content = content.replace(
    '    selected_key = solara.use_state(default_key)',
    '    selected_key = solara.use_state(default_key)  # noqa: SH101'
)
# 6. fix Row gap 参数（去掉多余的 gap="8px" from row inside card row）
# 7. Card title 支持中文
content = re.sub(
    r'with solara\.Card\(f"([^"]*)",',
    lambda m: f'with solara.Card(title="{m.group(1)}",',
    content
)

# 8. Fix the Card titles (they use f-strings which Card doesn't support in 1.56)
# Card(title=...) not Card(f"...")
# MacroStatsPanel
content = re.sub(
    r'with solara\.Card\(title="宏观快照  第 \{stats\[',
    'with solara.Card(title="宏观快照  第 " + str(',
    content
)

with open('server.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Fixed")
