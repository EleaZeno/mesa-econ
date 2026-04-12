with open('C:/Users/Kanyun/.qclaw/workspace/mesa-econ/app2.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
issues = []
for i, l in enumerate(lines, 1):
    stripped = l.rstrip()
    # Find lines with mismatched quote patterns: starts with html += '...' and has embedded "
    if "html +=" in stripped and stripped.count("'") % 2 != 0:
        issues.append((i, stripped[:100]))
for i, txt in issues:
    print(f'L{i}: {txt}')
