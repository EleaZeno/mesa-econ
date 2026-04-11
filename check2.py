"""Check key replacements"""
import re

with open('server.py', 'r', encoding='utf-8') as f:
    content = f.read()

patterns = {
    '_slider_refs': re.compile(r'_slider_refs'),
    'PARAM_CONFIG_items': re.compile(r'for key, cfg in PARAM_CONFIG\.items'),
    '_sync_slider': re.compile(r'_sync_slider'),
    'CYCLE_CONFIG': re.compile(r'CYCLE_CONFIG'),
    '_play_stop_event': re.compile(r'_play_stop_event'),
    'avg_price': re.compile(r'avg_price'),
    'unemp_gt1': re.compile(r'> 1 else'),
    'cfg_max': re.compile(r'cfg\["max"\]'),
    'max_height': re.compile(r'max-height'),
    'all_agents': re.compile(r'all_agents\s*='),
    'background': re.compile(r'background:#1e293b'),
    'param_gov': re.compile(r'gov_purchase'),
    'param_capgains': re.compile(r'capital_gains_tax'),
    'param_shock': re.compile(r'"shock_prob"'),
}

for name, rx in patterns.items():
    found = bool(rx.search(content))
    print(f"{'OK' if found else 'MISS':5s} {name}")
