import sys
sys.stdout.reconfigure(encoding='utf-8')

# Read app.py, extract _PAGE_HTML, then build page
# WITHOUT importing app (which triggers model init)

with open('C:/Users/Kanyun/.qclaw/workspace/mesa-econ/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Extract _PAGE_HTML
start = content.find('_PAGE_HTML = """')
end = content.find('"""', start + 15)
page_html = content[start + len('_PAGE_HTML = """'):end]

# Check the csv lines
idx = page_html.find('csv=')
chunk = page_html[idx:idx+60]

print('Python string content:')
has_real_newline = False
for i, c in enumerate(chunk):
    if ord(c) == 10:
        print(f'  [{i}] REAL NEWLINE (0x0A)')
        has_real_newline = True
    elif ord(c) == 92:
        next_c = chunk[i+1] if i+1 < len(chunk) else ''
        print(f'  [{i}] BACKSLASH (0x5C) next="{next_c}"')
    else:
        print(f'  [{i}] {c}')

if not has_real_newline:
    print('\nVERIFIED: No real newline inside JS strings!')
else:
    print('\nBROKEN: Real newline found inside JS string!')
