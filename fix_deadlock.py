with open('C:/Users/Kanyun/.qclaw/workspace/mesa-econ/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Find api_state function boundaries
api_state_marker = '@app.route("/api/state")'
step_marker = '@app.route("/api/step")'
api_state_start = content.find(api_state_marker)
step_start = content.find(step_marker)

print(f'api_state at: {api_state_start}, step at: {step_start}')
print('Current function:')
print(content[api_state_start:step_start][:300])

new_func = '''@app.route("/api/state")
def api_state():
    """不持有锁时收集数据快照，避免死锁"""
    last = None
    cycle = 0
    with _app_lock:
        if _model is None:
            return {"cycle": 0, "last": {}, "history": []}
        m = _model
        firms = m.firms
        households = m.households
        vol = getattr(m, 'stock_volatility', 0.0)
        bdr = getattr(m, 'bank_bad_debt_rate', 0.0)
        employed = sum(1 for h in households if h.employed)
        n_hh = len(households)
        last = {
            "cycle": m.cycle,
            "gdp": round(m.gdp, 0),
            "unemployment": round(m.unemployment * 100, 1),
            "price_index": round(getattr(m, 'price_index', 100), 1),
            "stock_price": round(m.stock_price, 1),
            "chart_vol": round(vol, 3),
            "chart_bdr": round(bdr * 100, 1),
            "chart_systemic": round(getattr(m, 'systemic_risk', 0), 3),
            "loans": round(m.total_loans_outstanding, 0),
            "govt_rev": round(m.govt_revenue, 0),
            "bankrupt": m.bankrupt_count,
            "default_count": m.default_count,
            "n_firms": len(firms),
            "employed": employed,
            "n_households": n_hh,
            "emp_rate": round(employed / n_hh * 100 if n_hh > 0 else 0, 1),
            "unemployed": n_hh - employed,
            "gini": round(m.gini, 3),
        }
        cycle = m.cycle
    with _history_lock:
        hist = list(_history)
    return {"cycle": cycle, "last": last, "history": hist}

'''

# Use flask jsonify wrapper
new_func = new_func.replace('return {"cycle"', 'from flask import jsonify; return jsonify({"cycle"')
# Actually just use plain dict - Flask will jsonify

# Find and replace the function
old_func = content[api_state_start:step_start]
if 'with _app_lock' in old_func and '_record()' in old_func:
    # The old function had _record() inside the lock - that was the bug
    print('Found _record() inside lock - removing deadlock')
    content = content[:api_state_start] + new_func + content[step_start:]
    with open('C:/Users/Kanyun/.qclaw/workspace/mesa-econ/app.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Fixed!')
else:
    print('No deadlock pattern found, checking...')
    print('Old func:')
    print(repr(old_func[:600]))
