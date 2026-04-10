"""
Mesa Economic Sandbox - Web Server (Mesa 3.x)
Run: solara run server.py
Open: http://127.0.0.1:8521
"""

import solara
from mesa import Model
from mesa.visualization import SolaraViz
from mesa.visualization import make_plot_component
from model import EconomyModel, Household, Firm, Bank, Trader


# ─────────────────────────────────────────────
# Custom Solara Component: Macro Stats Panel
# ─────────────────────────────────────────────

@solara.component
def MacroStats(model: Model):
    """Real-time macro statistics panel"""
    firms = [a for a in model.agents if isinstance(a, Firm)]
    households = [a for a in model.agents if isinstance(a, Household)]
    traders = [a for a in model.agents if isinstance(a, Trader)]
    employed = sum(1 for h in households if h.employed)
    total_prod = sum(f.production for f in firms)
    total_div = model.total_dividends
    n_unemployed = len(households) - employed

    solara.HTML(
        tag="div",
        unsafe_innerHTML=(
            f"<div style='background:linear-gradient(135deg,#0f172a,#1e3a5f);"
            f"color:#e2e8f0;border-radius:12px;padding:18px 20px;"
            f"font-family:Segoe UI,sans-serif;box-shadow:0 4px 20px rgba(0,0,0,0.3);line-height:2;'>"
            f"<div style='font-size:16px;font-weight:700;margin-bottom:10px;"
            f"border-bottom:1px solid #334155;padding-bottom:8px;'>"
            f"Macro Snapshot - Cycle {model.cycle}</div>"
            f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:6px 24px;font-size:13px;'>"
            f"<div>Firms: {len(firms)}</div>"
            f"<div>Employment: {employed}/{len(households)} ({employed/len(households)*100:.0f}%)</div>"
            f"<div>Unemployed: {n_unemployed}</div>"
            f"<div>Traders: {len(traders)}</div>"
            f"<div>Output: {total_prod:.1f}</div>"
            f"<div>Dividends: {total_div:.1f}</div>"
            f"<div>Tax Rate: {model.tax_rate:.0%}</div>"
            f"<div>Interest Rate: {model.base_interest_rate:.0%}</div>"
            f"<div>Price Index: {model.price_index:.2f}</div>"
            f"<div>Gov Revenue: {model.govt_revenue:.1f}</div>"
            f"<div>Loans: {model.total_loans_outstanding:.1f}</div>"
            f"</div></div>"
        ),
    )


# ─────────────────────────────────────────────
# Chart Components (matplotlib backend for stability)
# ─────────────────────────────────────────────

chart_stock = make_plot_component("stock_price", backend="matplotlib")
chart_gdp = make_plot_component("gdp", backend="matplotlib")
chart_unemp = make_plot_component("unemployment", backend="matplotlib")
chart_price = make_plot_component("price_index", backend="matplotlib")
chart_gini = make_plot_component("gini", backend="matplotlib")
chart_orders = make_plot_component("buy_orders", backend="matplotlib")
chart_loans = make_plot_component("loans", backend="matplotlib")


# ─────────────────────────────────────────────
# Model Parameters (Sliders)
# ─────────────────────────────────────────────

model_params = {
    "n_households": {
        "type": "SliderInt",
        "value": 20,
        "min": 5,
        "max": 80,
        "step": 5,
        "label": "Consumers",
    },
    "n_firms": {
        "type": "SliderInt",
        "value": 10,
        "min": 3,
        "max": 40,
        "step": 1,
        "label": "Firms",
    },
    "n_traders": {
        "type": "SliderInt",
        "value": 20,
        "min": 5,
        "max": 80,
        "step": 5,
        "label": "Traders",
    },
    "tax_rate": {
        "type": "SliderFloat",
        "value": 0.15,
        "min": 0.0,
        "max": 0.45,
        "step": 0.01,
        "label": "Income Tax Rate",
        "format": "0%",
    },
    "base_interest_rate": {
        "type": "SliderFloat",
        "value": 0.05,
        "min": 0.0,
        "max": 0.25,
        "step": 0.01,
        "label": "Base Interest Rate",
        "format": "0%",
    },
    "min_wage": {
        "type": "SliderFloat",
        "value": 7.0,
        "min": 0.0,
        "max": 20.0,
        "step": 0.5,
        "label": "Minimum Wage",
    },
    "productivity": {
        "type": "SliderFloat",
        "value": 1.0,
        "min": 0.1,
        "max": 3.0,
        "step": 0.1,
        "label": "Productivity",
    },
    "subsidy": {
        "type": "SliderFloat",
        "value": 0.0,
        "min": 0.0,
        "max": 20.0,
        "step": 0.5,
        "label": "Unemployment Subsidy",
    },
}


# ─────────────────────────────────────────────
# Launch SolaraViz
# ─────────────────────────────────────────────

model = EconomyModel()

page = SolaraViz(
    model,
    renderer=None,
    components=[
        MacroStats,
        chart_stock,
        chart_gdp,
        chart_unemp,
        chart_price,
        chart_gini,
        chart_orders,
        chart_loans,
    ],
    model_params=model_params,
    name="Economic Sandbox",
    play_interval=200,
)

page
