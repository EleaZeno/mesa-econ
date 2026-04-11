"""v3.0 验证脚本"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from model import EconomyModel
import numpy as np

# ── 1. 政府购买GDP效应 ───────────────────────────────────────
gdp_with, gdp_without = [], []
for _ in range(10):
    m1 = EconomyModel(gov_purchase=200.0, subsidy=5.0)
    m2 = EconomyModel(gov_purchase=0.0, subsidy=5.0)
    for _ in range(8):
        m1.step(); m2.step()
    gdp_with.append(m1.gdp)
    gdp_without.append(m2.gdp)

print("=== 1. 政府购买GDP效应 ===")
print(f"有购买均值: {np.mean(gdp_with):.0f}")
print(f"无购买均值: {np.mean(gdp_without):.0f}")
diff = np.mean(gdp_with) - np.mean(gdp_without)
print(f"差异: {diff:+.0f}")
print("PASS" if diff > 20 else f"WARN diff={diff:.0f}")

# ── 2. 差异化定价 ───────────────────────────────────────────
m = EconomyModel()
for _ in range(5): m.step()
prices = [f.price for f in m.firms]
spread = max(prices) - min(prices)
print(f"\n=== 2. 差异化定价 ===")
print(f"价差: {spread:.2f} (需>0.5)")
print("PASS" if spread > 0.5 else "FAIL")

# ── 3. 外部冲击 ─────────────────────────────────────────────
shocks = set()
m3 = EconomyModel(shock_prob=0.5)
for _ in range(15):
    m3.step()
    if m3.current_shock:
        shocks.add(m3.current_shock)
print(f"\n=== 3. 外部冲击 ===")
print(f"触发种类: {len(shocks)} (需>=2)")
print("PASS" if len(shocks) >= 2 else "FAIL")

# ── 4. 银行差异化利率 ───────────────────────────────────────
m4 = EconomyModel()
for _ in range(3): m4.step()
bank_rates = {}
for b in m4.banks:
    rate = m4.base_interest_rate + b.lending_spread
    bank_rates[b.bank_type] = f"{rate:.2%}"
print(f"\n=== 4. 银行差异化利率 ===")
print(f"激进/保守利率: {bank_rates}")
print("PASS" if bank_rates.get('aggressive','') != bank_rates.get('conservative','') else "FAIL")

# ── 5. 居民异质性 ───────────────────────────────────────────
tiers = {}
for h in m4.households:
    tiers[h.income_tier] = tiers.get(h.income_tier, 0) + 1
print(f"\n=== 5. 居民异质性 ===")
print(f"收入分层: {tiers}")
mpcs = {"low":0.8, "middle":0.5, "high":0.2}
for t, n in tiers.items():
    avg_mpc = np.mean([h.mpc for h in m4.households if h.income_tier == t])
    print(f"  {t}: n={n}, avg_mpc={avg_mpc:.2f} (期望{mpcs[t]})")
print("PASS" if len(tiers) == 3 else "FAIL")

# ── 6. 技能分布（高收入高技能） ─────────────────────────────
high_hh = [h for h in m4.households if h.income_tier == "high"]
low_hh = [h for h in m4.households if h.income_tier == "low"]
avg_skill_high = np.mean([h.skill_level for h in high_hh]) if high_hh else 0
avg_skill_low = np.mean([h.skill_level for h in low_hh]) if low_hh else 0
print(f"\n=== 6. 技能-收入相关性 ===")
print(f"高收入平均技能: {avg_skill_high:.2f} (期望>1.0)")
print(f"低收入平均技能: {avg_skill_low:.2f} (期望<1.0)")
print("PASS" if avg_skill_high > avg_skill_low else "WARN")

# ── 7. Gordon锚 + 供需混合定价 ──────────────────────────────
cycles = 12
annual_div = m4.total_dividends / cycles * 12
div_per_share = annual_div / max(1, len(m4.firms) * 50)
gordon = div_per_share / (m4.base_interest_rate + 0.02)
print(f"\n=== 7. Gordon锚 ===")
print(f"Gordon内在价值: {gordon:.1f}")
print(f"实际股价: {m4.stock_price:.2f}")
print(f"偏差: {abs(gordon - m4.stock_price) / m4.stock_price * 100:.0f}%")
print("PASS" if 20 < m4.stock_price < 500 else "WARN")

print("\n=== 全部验证完成 ===")
