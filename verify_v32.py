import urllib.request, re, json
r = urllib.request.urlopen('http://127.0.0.1:8523/', timeout=8)
html = r.read().decode('utf-8', errors='replace')
svg_tag = '<svg'
poly = 'polyline'
has_svg = svg_tag in html
has_poly = poly in html
print(f'Page: {r.status} {len(html)} bytes')
print(f'Has inline SVG: {has_svg}')
print(f'Has polyline: {has_poly}')

# Check JS
m = re.search(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
if m:
    open('C:/Users/Kanyun/.qclaw/workspace/mesa-econ/live_check.js', 'w', encoding='utf-8').write(m.group(1))
    has_refresh = 'function refresh' in m.group(1)
    has_updateChart = 'function updateChart' in m.group(1)
    has_showChart = 'function showChart' in m.group(1)
    has_HIST = 'window._HIST' in m.group(1)
    print(f'JS: refresh={has_refresh} updateChart={has_updateChart} showChart={has_showChart} _HIST={has_HIST}')

# Check no "loading..." placeholder
has_loading = html.find('loading') >= 0
print(f'Still has loading placeholder: {has_loading}')
