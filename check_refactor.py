"""检查重构结果"""
import re

with open('server.py', 'r', encoding='utf-8') as f:
    content = f.read()

checks = [
    ('_slider_refs', '滑块字典'),
    ('for key, cfg in PARAM_CONFIG.items', '遍历生成滑块'),
    ('_sync_slider', '场景同步滑块'),
    ('CYCLE_CONFIG', '经济周期配置'),
    ('_play_stop_event', '优雅停止事件'),
    ('avg_price', 'avg_price兼容'),
    ('> 1 else v', '失业率转换修正'),
    ('cfg\\["max"\\]', 'PolicyPanel边界'),
    ('max-height:350px', 'Agent滚动容器'),
    ('_slider_refs.items', '_get_current_params'),
    ('background:#1e293b', '卡片深色背景'),
    ('all_agents = {', '直接用model分类列表'),
    ('param_config_end_marker', 'PARAM_CONFIG扩展'),
]

for pat, desc in checks:
    found = bool(re.search(pat, content))
    status = 'OK' if found else 'MISS'
    print(f'{status:5s} {desc}')
