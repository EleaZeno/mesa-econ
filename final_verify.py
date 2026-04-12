import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'C:/Users/Kanyun/.qclaw/workspace/mesa-econ')
import importlib
import app
importlib.reload(app)

html = app.build_page()
idx = html.find("csv='")
if idx >= 0:
    chunk = html[idx:idx+80]
    print('After fix:')
    for i, c in enumerate(chunk):
        if ord(c) < 32:
            print(f'  char {i}: ord={ord(c)} (control)')
        else:
            print(f'  char {i}: ord={ord(c)} char={c}')

# Extract JS and test with node
import re
m = re.search(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
if m:
    js = m.group(1)
    open('C:/Users/Kanyun/.qclaw/workspace/mesa-econ/verify_js.js', 'w', encoding='utf-8').write(js)
    print(f'\nJS extracted ({len(js)} chars) for node check')
