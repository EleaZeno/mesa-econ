with open('C:/Users/Kanyun/.qclaw/workspace/mesa-econ/app.py', 'r', encoding='utf-8') as f:
    c = f.read()

# Fix all the round(getattr(...), N) issues - missing closing paren
# These should be round(getattr(...), N) but are written as round(getattr(...), N)
# The issue is missing closing ) before the comma

# Pattern: round(getattr(..., ..., ...), N)  <- correct
# Actual:  round(getattr(..., ..., ...), N)  <- missing ) before the comma

# Replace bad patterns with correct ones
replacements = [
    # _rec() function
    ('"price": round(getattr(m, "price_index", 100.0), 1),', '"price": round(getattr(m, "price_index", 100.0), 1),'),
    ('"vol": round(getattr(m, "stock_volatility", 0.0), 3),', '"vol": round(getattr(m, "stock_volatility", 0.0), 3),'),
    ('"bdr": round(getattr(m, "bank_bad_debt_rate", 0.0) * 100, 1),', '"bdr": round(getattr(m, "bank_bad_debt_rate", 0.0) * 100, 1),'),
    # _page() function
    ('"price": round(getattr(_md, "price_index", 100.0), 1),', '"price": round(getattr(_md, "price_index", 100.0), 1),'),
    ('"vol": round(getattr(_md, "stock_volatility", 0.0), 3),', '"vol": round(getattr(_md, "stock_volatility", 0.0), 3),'),
    ('"bdr": round(getattr(_md, "bank_bad_debt_rate", 0.0) * 100, 1),', '"bdr": round(getattr(_md, "bank_bad_debt_rate", 0.0) * 100, 1),'),
]

count = 0
for old, new in replacements:
    if old in c:
        c = c.replace(old, new)
        print('Fixed:', repr(old[:60]))
        count += 1
    else:
        print('Not found:', repr(old[:60]))

with open('C:/Users/Kanyun/.qclaw/workspace/mesa-econ/app.py', 'w', encoding='utf-8') as f:
    f.write(c)
print('Total fixed:', count)

# Compile check
import py_compile
try:
    py_compile.compile('C:/Users/Kanyun/.qclaw/workspace/mesa-econ/app.py', doraise=True)
    print('SYNTAX OK')
except py_compile.PyCompileError as e:
    print('SYNTAX ERROR:', e)
