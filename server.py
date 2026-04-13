"""
Solara Economic Sandbox v3.6
Replaces Flask with Solara 1.x reactive UI.
Run: python run_solara.py
Or: python -c "import server; server._init(); server.serve_solara()"
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import solara
from solara import (
    Button, Column, Div, HBox, Markdown, Row, SliderFloat, Text,
    HTML, Title, ColumnsResponsive, AppBar, AppBarTitle, Card, CardActions,
)

from model import EconomyModel, City

# ── Global simulation state ──────────────────────────────────────
_lock = threading.Lock()
_md: Optional[EconomyModel] = None
_hist: list = []
_hl = threading.Lock()
_run = False
_stop = threading.Event()
_thr: Optional[threading.Thread] = None


def _init(**kw):
    global _md, _run, _thr
    d = dict(
        n_households=25, n_firms=12, n_traders=20,
        tax_rate=0.15, base_interest_rate=0.05, min_wage=7.0,
        productivity=1.0, subsidy=0.0, gov_purchase=0.0,
        capital_gains_tax=0.10, shock_prob=0.02,
    )
    d.update((k, v) for k, v in kw.items() if v is not None)
    _run = False
    if _thr:
        _stop.set()
    with _lock:
        _md = EconomyModel(**d)
        if hasattr(_md, "_refresh_cache"):
            _md._refresh_cache()
    _hist.clear()
    _rec()


def _rec():
    with _lock:
        if _md is None:
            return
        m = _md
        try:
            emp = sum(1 for h in m.households if h.employed)
            nh = len(m.households)
            ent = {
                "cycle": m.cycle,
                "gdp": round(m.gdp),
                "unemp": round(m.unemployment * 100, 1),
                "price": round(m.avg_price, 2),
                "stock": round(m.stock_price, 1),
                "vol": round(getattr(m, "stock_volatility", 0.0), 3),
                "bdr": round(getattr(m, "bank_bad_debt_rate", 0.0) * 100, 1),
                "loans": round(m.total_loans_outstanding),
                "rev": round(m.govt_revenue),
                "bankrupt": m.bankrupt_count,
                "gini": round(m.gini, 3),
                "emp": emp, "nh": nh,
                "rate": round(emp / nh * 100 if nh else 0, 1),
            }
        except Exception:
            ent = {"cycle": getattr(_md, "cycle", 0)}
    _hl.acquire()
    try:
        _hist.append(ent)
        if len(_hist) > 500:
            del _hist[:-500]
    finally:
        _hl.release()


def _play_loop():
    while not _stop.wait(0.5):
        with _lock:
            if _run and _md:
                _md.step()
                _rec()


# ── Chart helpers ───────────────────────────────────────────────
def _mpl_to_svg(fig):
    buf = fig.savefig(format="svg", bbox_inches="tight", dpi=80, transparent=False)
    plt.close(fig)
    return buf.decode()


def _make_chart(cycles, values, title, color, h=1.1):
    if not cycles or not values:
        return ""
    n = min(len(cycles), 80)
    c, v = cycles[-n:], values[-n:]
    fig, ax = plt.subplots(figsize=(4, h))
    ax.plot(c, v, color=color, linewidth=1.5, alpha=0.85)
    ax.set_title(title, fontsize=8, color="#334155")
    ax.tick_params(labelsize=7, colors="#64748b")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_facecolor("#f8fafc")
    fig.patch.set_facecolor("white")
    plt.tight_layout(pad=0.4)
    return _mpl_to_svg(fig)


# ── Solara components ────────────────────────────────────────────

@solara.component
def StatusLine(s, score_color):
    """Top status bar."""
    with Row(gap=6, align="center", wrap=True):
        Markdown(
            f"**经济沙盘 v3.6**"
            f"&nbsp;&nbsp;第 **<b>{s['cycle']}</b> 轮**"
            f"&nbsp;&nbsp;GDP=**<b>{s['gdp']}</b>**"
            f"&nbsp;&nbsp;基尼=**<b>{s['gini']}</b>**"
            f"&nbsp;&nbsp;股价=**<b>{s['stock']}</b>**"
            f"&nbsp;&nbsp;A城=**<b>{s['ca_pop']}</b>人**"
            f"&nbsp;&nbsp;B城=**<b>{s['cb_pop']}</b>人**"
            f"&nbsp;&nbsp;健康分=**<b style='color:{score_color};font-size:22px'>{s['score']:.0f}</b>**"
        )


@solara.component
def ParameterPanel(params, set_params):
    """Left sidebar: sliders + buttons."""
    p = params

    def setv(key, value):
        set_params({**p, key: value})

    Markdown("### 经济参数")

    def pct(label, key, v, min_v, max_v, step_v=1):
        SliderFloat(
            label=label, value=float(v) * 100,
            on_value=lambda x: setv(key, x / 100),
            min=float(min_v) * 100, max=float(max_v) * 100,
            step=float(step_v) * 100,
        )
        Text(f"{float(v) * 100:.1f}%", style="font-size:11px;color:#64748b")

    def flat(label, key, v, min_v, max_v, step_v=1):
        SliderFloat(
            label=label, value=float(v),
            on_value=lambda x: setv(key, x),
            min=float(min_v), max=float(max_v), step=float(step_v),
        )
        Text(f"{float(v):.1f}", style="font-size:11px;color:#64748b")

    pct("税率", "tax_rate", p.get("tax_rate", 0.15), 5, 30)
    pct("基准利率", "base_interest_rate", p.get("base_interest_rate", 0.05), 1, 15)
    flat("最低工资", "min_wage", p.get("min_wage", 7.0), 5, 15, 0.5)
    flat("生产率", "productivity", p.get("productivity", 1.0), 0.5, 2.0, 0.1)
    flat("政府购买", "gov_purchase", p.get("gov_purchase", 0.0), 0, 500, 10)
    flat("补贴", "subsidy", p.get("subsidy", 0.0), 0, 100, 5)
    pct("资本利得税率", "capital_gains_tax", p.get("capital_gains_tax", 0.10), 0, 50)
    pct("城市A税率", "city_a_tax", p.get("city_a_tax", 0.12), 5, 30)
    pct("城市B税率", "city_b_tax", p.get("city_b_tax", 0.18), 5, 30)


@solara.component
def ActionButtons(playing, on_toggle, on_step, on_reset, on_apply):
    """Control buttons."""
    with Row(gap=4, justify="center"):
        Button(
            "暂停" if playing else "开始",
            on_click=on_toggle,
            icon_name="mdi-pause" if playing else "mdi-play",
            style="background:#3b82f6;color:white;flex:1",
        )
        Button("单步", on_click=on_step, icon_name="mdi-skip-next", style="flex:1")
        Button("应用", on_click=on_apply, icon_name="mdi-check", style="flex:1")
        Button("重置", on_click=on_reset, icon_name="mdi-refresh", style="flex:1")


@solara.component
def ChartCard(title, cycles, values, color):
    """Single matplotlib chart card."""
    svg = _make_chart(cycles, values, title, color)
    with Card(style="border:1px solid #e2e8f0;border-radius:8px;padding:8px;margin-bottom:8px"):
        Markdown(f"**{title}**")
        HTML(tag="div", unsafe_html=svg)


@solara.component
def ChartsPanel(hist_data):
    """9-chart grid."""
    cycles = [e["cycle"] for e in hist_data]
    gdps = [e["gdp"] for e in hist_data]
    unemps = [e["unemp"] for e in hist_data]
    ginis = [e["gini"] for e in hist_data]
    stocks = [e["stock"] for e in hist_data]
    prices = [e["price"] for e in hist_data]
    rates = [e["rate"] for e in hist_data]
    bdrs = [e["bdr"] for e in hist_data]

    # 3-column responsive grid
    with ColumnsResponsive(columns={"xs": 1, "sm": 2, "md": 3}, gap="8px"):
        ChartCard("GDP", cycles, gdps, "#3b82f6")
        ChartCard("失业率(%)", cycles, unemps, "#f59e0b")
        ChartCard("Gini系数", cycles, ginis, "#8b5cf6")
        ChartCard("股价", cycles, stocks, "#10b981")
        ChartCard("物价指数", cycles, prices, "#6366f1")
        ChartCard("就业率(%)", cycles, rates, "#22c55e")
        ChartCard("银行坏账率(%)", cycles, bdrs, "#ef4444")


@solara.component
def CityPanel(hist_data):
    """City comparison."""
    cycles = [e["cycle"] for e in hist_data]
    gdp_vals = [e["gdp"] for e in hist_data]
    with Card(style="border:1px solid #e2e8f0;border-radius:8px;padding:8px"):
        Markdown("### 双城对比")
        HTML(tag="div", unsafe_html=_make_chart(cycles, gdp_vals, "GDP(A城蓝/B城绿)", "#6366f1", h=1.5))


# ── Root component ───────────────────────────────────────────────
@solara.component
def Page():
    Title("经济沙盘 v3.6")

    # Reactive state
    params, set_params = solara.use_state({
        "tax_rate": 0.15, "base_interest_rate": 0.05, "min_wage": 7.0,
        "productivity": 1.0, "gov_purchase": 0.0, "subsidy": 0.0,
        "capital_gains_tax": 0.10, "city_a_tax": 0.12, "city_b_tax": 0.18,
    })
    hist_data, set_hist_data = solara.use_state([])
    playing, set_playing = solara.use_state(False)
    fb_msg, set_fb = solara.use_state("")
    global _run

    # Snapshot model state
    s = {
        "cycle": 0, "gdp": 0, "gini": 0, "stock": 0, "score": 50,
        "ca_pop": 0, "cb_pop": 0,
    }
    with _lock:
        if _md:
            s["cycle"] = _md.cycle
            s["gdp"] = round(_md.gdp)
            s["gini"] = round(_md.gini, 3)
            s["stock"] = round(_md.stock_price, 1)
            s["score"] = getattr(_md, "health_score", 50)
            s["ca_pop"] = getattr(_md, "city_a_pop", 0)
            s["cb_pop"] = getattr(_md, "city_b_pop", 0)

    score_color = "#16a34a" if s["score"] >= 80 else "#f59e0b" if s["score"] >= 40 else "#ef4444"

    # Snapshot history
    with _hl:
        hist_snapshot = list(_hist)
    set_hist_data(hist_snapshot)

    # Callbacks
    def do_toggle():
        global _run, _thr, _stop
        _run = not _run
        if _run:
            _stop.clear()
            _thr = threading.Thread(target=_play_loop, daemon=True)
            _thr.start()
        set_playing(_run)

    def do_step():
        with _lock:
            if _md:
                _md.step()
                _rec()

    def do_reset():
        _init()
        set_fb("仿真已重置")
        time.sleep(3)
        set_fb("")

    def do_apply():
        with _lock:
            if _md:
                _md.tax_rate = params.get("tax_rate", 0.15)
                _md.base_interest_rate = params.get("base_interest_rate", 0.05)
                _md.min_wage = params.get("min_wage", 7)
                _md.productivity = params.get("productivity", 1.0)
                _md.gov_purchase = params.get("gov_purchase", 0.0)
                _md.subsidy_rate = params.get("subsidy", 0.0)
                _md.capital_gains_tax = params.get("capital_gains_tax", 0.10)
                _md.city_a_tax = params.get("city_a_tax", 0.12)
                _md.city_b_tax = params.get("city_b_tax", 0.18)
                if hasattr(_md, "_refresh_cache"):
                    _md._refresh_cache()
        set_fb("参数已应用")

    # Page layout
    with Column(style="max-width:1400px;margin:0 auto;padding:12px;gap:12px"):
        # Header
        StatusLine(s, score_color)

        # Toast
        if fb_msg:
            Markdown(
                f"<div style='background:#dbeafe;color:#1e40af;padding:8px 16px;"
                f"border-radius:6px;font-size:13px'>{fb_msg}</div>"
            )

        # Two-column layout: sidebar + content
        with ColumnsResponsive(columns={"xs": 1, "lg": "300px 1fr"}, gap="16px"):
            # Left sidebar
            with Column(gap="8px"):
                Markdown("### 控制面板")
                with Card(style="padding:12px"):
                    ParameterPanel(params, set_params)
                    Div(style="height:8px")
                    ActionButtons(playing, do_toggle, do_step, do_reset, do_apply)
                Markdown("""
                **说明**：拖动滑块调参，**应用**后实时生效。
                **开始**运行仿真，**暂停**冻结。**单步**手动推进一轮，**重置**清空历史。
                """)

            # Right: charts
            with Column(gap="12px"):
                Markdown("### 宏观经济指标")
                ChartsPanel(hist_snapshot)
                CityPanel(hist_snapshot)


# ── Entry point ─────────────────────────────────────────────────
def serve_solara(port=8522, host="0.0.0.0"):
    """Start Solara server."""
    _init()
    from solara.server.app import AppScript
    app = AppScript("server:MainPage")
    app.init()
    asgi_app = app.app
    import uvicorn
    uvicorn.run(asgi_app, host=host, port=port, access_log=False)


if __name__ == "__main__":
    serve_solara()
