"""更新 server.py：添加新参数控件"""
import re

with open('server.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 添加新 reactive 变量
old_vars = "    subsidy = solara.use_reactive(0.0)"
new_vars = """    subsidy = solara.use_reactive(0.0)
    gov_purchase = solara.use_reactive(0.0)
    capital_gains_tax = solara.use_reactive(0.10)
    shock_prob = solara.use_reactive(0.02)"""
content = content.replace(old_vars, new_vars)

# 2. 添加新 sliders（在 subsidy slider 后面）
old_slider_end = """                    solara.FloatSlider(
                        label="subsidy",
                        value=subsidy,
                        min=0,
                        max=50,
                        step=1,
                        on_value=lambda v: on_change_factory("subsidy", v),
                    )
                ]
            )
        ]
    )
"""
new_slider_end = """                    solara.FloatSlider(
                        label="subsidy",
                        value=subsidy,
                        min=0,
                        max=50,
                        step=1,
                        on_value=lambda v: on_change_factory("subsidy", v),
                    )
                    solara.FloatSlider(
                        label="gov_purchase",
                        value=gov_purchase,
                        min=0,
                        max=200,
                        step=5,
                        on_value=lambda v: on_change_factory("gov_purchase", v),
                    )
                    solara.FloatSlider(
                        label="capital_gains_tax",
                        value=capital_gains_tax,
                        min=0,
                        max=0.30,
                        step=0.01,
                        on_value=lambda v: on_change_factory("capital_gains_tax", v),
                    )
                    solara.FloatSlider(
                        label="shock_prob",
                        value=shock_prob,
                        min=0,
                        max=0.20,
                        step=0.01,
                        on_value=lambda v: on_change_factory("shock_prob", v),
                    )
                ]
            )
        ]
    )
"""
content = content.replace(old_slider_end, new_slider_end)

# 3. 添加新 stat 卡片
old_stats = """"govt_rev":      model.govt_revenue,"""
new_stats = """"govt_rev":      model.govt_revenue,
                "gov_purch":    model.gov_purchase,
                "cap_gains":    model.capital_gains_tax_revenue,
                "systemic":     round(model.systemic_risk, 3),
                "bankrupt":     model.bankrupt_count,
                "n_firms":      len(model.firms),"""
content = content.replace(old_stats, new_stats)

with open('server.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("server.py updated")
