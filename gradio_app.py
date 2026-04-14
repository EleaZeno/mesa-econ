"""
经济沙盘 v4.6 — Gradio 版 (SFC 审计 + Layer 2/3 稳定器)
Layer 2: 扩招逻辑修复（不依赖 production 初始值）
Layer 3: 默认失业保险(5) + 政府购买(50)，防止需求塌缩
Run: python gradio_app.py
"""

from __future__ import annotations

import faulthandler
faulthandler.enable(open('_segfault.log', 'w', encoding='utf-8'), all_threads=True)

import threading
import time
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np

# 中文字体配置（Windows: SimHei / Microsoft YaHei）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

import gradio as gr

from model import EconomyModel

# ── 全局仿真状态 ─────────────────────────────────────────────────
_lock = threading.RLock()  # RLock: 可重入，防止 _rec/_snapshot 在 _lock 内递归死锁
_md: Optional[EconomyModel] = None
_hist: list = []
_hl = threading.Lock()
_run = False
_stop = threading.Event()
_thr: Optional[threading.Thread] = None


# ── 初始化 / 记录 / 播放循环 ─────────────────────────────────────
def _init(**kw):
    global _md, _run, _thr
    d = dict(
        n_households=25, n_firms=12, n_traders=20,
        tax_rate=0.15,
        productivity=1.0, subsidy=5.0, gov_purchase=50.0,
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
    with _hl:
        _hist.clear()
    _rec()


def _rec():
    """快照当前模型状态，追加到历史。"""
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
                "bdr": round(getattr(m, "bank_bad_debt_rate", 0.0) * 100, 1),
                "loans": round(m.total_loans_outstanding),
                "rev": round(m.govt_revenue),
                "bankrupt": m.bankrupt_count,
                "gini": round(m.gini, 3),
                "rate": round(emp / nh * 100 if nh else 0, 1),
                "score": round(getattr(m, "health_score", 50)),
                "nfirms": len(m.firms),
                "mkt_rate": round(
                    sum(b.loan_rate + b.lending_spread for b in m.banks)
                    / max(1, len(m.banks)) * 100, 2
                ) if m.banks else 0,
                "avg_deposit_rate": round(
                    sum(b.deposit_rate for b in m.banks)
                    / max(1, len(m.banks)) * 100, 2
                ) if m.banks else 0,
                "ca_pop": getattr(m, "city_a_pop", 0),
                "cb_pop": getattr(m, "city_b_pop", 0),
                "ca_gdp": round(getattr(m, "city_a_gdp", 0)),
                "cb_gdp": round(getattr(m, "city_b_gdp", 0)),
                "ca_unemp": round(getattr(m, "city_a_unemp", 0) * 100, 1),
                "cb_unemp": round(getattr(m, "city_b_unemp", 0) * 100, 1),
            }
        except Exception:
            ent = {"cycle": getattr(_md, "cycle", 0)}
    with _hl:
        _hist.append(ent)
        if len(_hist) > 500:
            del _hist[:-500]


def _play_loop():
    while not _stop.wait(0.5):
        with _lock:
            if _run and _md:
                _md.step()
                _rec()


# ── matplotlib 图表生成 ──────────────────────────────────────────
def _make_fig(hist_data: list) -> plt.Figure:
    """生成 6 格宏观指标图表（直接返回 Figure 给 gr.Plot）。"""
    fig = plt.figure(figsize=(12, 7), facecolor="#f8fafc")
    gs = gridspec.GridSpec(2, 3, figure=fig, hspace=0.45, wspace=0.35)

    charts = [
        ("GDP", "gdp", "#3b82f6"),
        ("失业率 (%)", "unemp", "#f59e0b"),
        ("Gini 系数", "gini", "#8b5cf6"),
        ("企业数量", "nfirms", "#10b981"),
        ("市场利率 (%)", "mkt_rate", "#f97316"),
        ("银行坏账率 (%)", "bdr", "#ef4444"),
    ]

    cycles = [e["cycle"] for e in hist_data]

    for idx, (title, key, color) in enumerate(charts):
        ax = fig.add_subplot(gs[idx // 3, idx % 3])
        vals = [e.get(key, 0) for e in hist_data]
        if cycles and vals:
            ax.plot(cycles, vals, color=color, linewidth=1.8, alpha=0.9)
            ax.fill_between(cycles, vals, alpha=0.08, color=color)
            # 最新值标注
            ax.annotate(
                f"{vals[-1]:.1f}",
                xy=(cycles[-1], vals[-1]),
                fontsize=8, color=color, fontweight="bold",
                xytext=(3, 3), textcoords="offset points",
            )
        ax.set_title(title, fontsize=9, color="#334155", fontweight="600")
        ax.tick_params(labelsize=7, colors="#64748b")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.set_facecolor("#ffffff")
        ax.grid(axis="y", alpha=0.3, linewidth=0.5)

    return fig


def _make_city_fig(hist_data: list) -> plt.Figure:
    """双城 GDP + 失业率对比图。"""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 3), facecolor="#f8fafc")
    cycles = [e["cycle"] for e in hist_data]

    for ax, key_a, key_b, title, ca_color, cb_color in [
        (ax1, "ca_gdp", "cb_gdp", "双城 GDP", "#3b82f6", "#22c55e"),
        (ax2, "ca_unemp", "cb_unemp", "双城失业率 (%)", "#60a5fa", "#4ade80"),
    ]:
        va = [e.get(key_a, 0) for e in hist_data]
        vb = [e.get(key_b, 0) for e in hist_data]
        if cycles:
            ax.plot(cycles, va, color=ca_color, linewidth=1.8, label="城市 A")
            ax.plot(cycles, vb, color=cb_color, linewidth=1.8, label="城市 B", linestyle="--")
            ax.legend(fontsize=7)
        ax.set_title(title, fontsize=9, color="#334155", fontweight="600")
        ax.tick_params(labelsize=7)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.set_facecolor("#ffffff")
        ax.grid(axis="y", alpha=0.3, linewidth=0.5)

    fig.tight_layout(pad=0.8)
    return fig


# ── 状态快照（供 Gradio 回调读取）────────────────────────────────
def _snapshot():
    """返回 (stats_md, macro_fig, city_fig)。"""
    with _hl:
        hist = list(_hist)

    # 状态文字
    if hist:
        last = hist[-1]
        score = last.get("score", 50)
        score_color = "#16a34a" if score >= 80 else "#f59e0b" if score >= 40 else "#ef4444"
        stats_md = (
            f"## 经济沙盘 v4.6 &nbsp;&nbsp;"
            f"<span style='color:{score_color};font-size:28px;font-weight:800'>{score}</span>"
            f"<span style='color:#94a3b8;font-size:12px'> 健康分</span>\n\n"
            f"**第 {last['cycle']} 轮** &nbsp;|&nbsp; "
            f"GDP = **{last['gdp']:,}** &nbsp;|&nbsp; "
            f"基尼 = **{last['gini']:.3f}** &nbsp;|&nbsp; "
            f"企业 = **{last['nfirms']}** &nbsp;|&nbsp; "
            f"失业率 = **{last['unemp']:.1f}%** &nbsp;|&nbsp; "
            f"市场利率 ≈ **{last['mkt_rate']:.1f}%** *(涌现)*\n\n"
            f"🏙️ A城 {last['ca_pop']}人 GDP={last['ca_gdp']:,} 失业{last['ca_unemp']:.1f}% &nbsp;&nbsp;"
            f"🌆 B城 {last['cb_pop']}人 GDP={last['cb_gdp']:,} 失业{last['cb_unemp']:.1f}%"
        )
    else:
        stats_md = "## 经济沙盘 v4.6\n\n*仿真未开始，点击「单步」或「开始」*"

    macro_fig = _make_fig(hist)
    city_fig = _make_city_fig(hist)
    return stats_md, macro_fig, city_fig


# ── Gradio 回调函数 ──────────────────────────────────────────────
def cb_step():
    import traceback as _tb
    try:
        with _lock:
            if _md:
                _md.step()
                _rec()
        return _snapshot()
    except Exception as exc:
        with open('_crash_cb_step.log', 'a', encoding='utf-8') as _f:
            _f.write(f'=== cb_step crash ===\n{_tb.format_exc()}\n')
        raise


def cb_toggle():
    global _run, _thr, _stop
    _run = not _run
    if _run:
        _stop.clear()
        _thr = threading.Thread(target=_play_loop, daemon=True)
        _thr.start()
        btn_label = "⏸ 暂停"
    else:
        btn_label = "▶ 开始"
    return (btn_label,) + _snapshot()


def cb_reset():
    global _run
    _run = False
    _init()
    return ("▶ 开始",) + _snapshot()


def cb_apply(tax, prod, gov, sub, cg, cat, cbt):
    with _lock:
        if _md:
            _md.tax_rate = tax / 100
            _md.productivity = prod
            _md.gov_purchase = gov
            _md.subsidy = sub
            _md.capital_gains_tax = cg / 100
            _md.city_a_tax = cat / 100
            _md.city_b_tax = cbt / 100
            if hasattr(_md, "_refresh_cache"):
                _md._refresh_cache()
    return _snapshot()


def cb_shock(shock_name):
    with _lock:
        if _md and hasattr(_md, "trigger_shock"):
            _md.trigger_shock(shock_name)
            _rec()
    return _snapshot()


def cb_poll():
    """定时器轮询：每 0.5s 刷新 UI。"""
    import traceback as _tb
    try:
        return _snapshot()
    except Exception as exc:
        with open('_crash_cb_poll.log', 'a', encoding='utf-8') as _f:
            _f.write(f'=== cb_poll crash ===\n{_tb.format_exc()}\n')
        raise


# ── Gradio Blocks UI ─────────────────────────────────────────────
def build_ui() -> gr.Blocks:
    with gr.Blocks(title="经济沙盘 v4.0") as demo:

        # ── 顶部状态栏 ──────────────────────────────────────────
        stats_md = gr.Markdown(
            "## 经济沙盘 v4.0\n\n*初始化中...*",
            elem_classes=["stat-header"],
        )

        # ── 主体：左侧参数 + 右侧图表 ───────────────────────────
        with gr.Row():

            # ── 左侧：参数面板 ──────────────────────────────────
            with gr.Column(scale=1, min_width=280):
                gr.Markdown("### 经济参数")

                sl_tax = gr.Slider(5, 30, value=15, step=1, label="税率 (%)")
                sl_prod = gr.Slider(0.5, 2.0, value=1.0, step=0.1, label="生产率")
                sl_gov = gr.Slider(0, 500, value=50, step=10, label="政府购买")
                sl_sub = gr.Slider(0, 100, value=5, step=5, label="补贴")
                sl_cg = gr.Slider(0, 50, value=10, step=5, label="资本利得税率 (%)")

                gr.Markdown("#### 城市政策")
                sl_cat = gr.Slider(5, 30, value=12, step=1, label="城市 A 税率 (%)")
                sl_cbt = gr.Slider(5, 30, value=18, step=1, label="城市 B 税率 (%)")

                all_sliders = [sl_tax, sl_prod, sl_gov, sl_sub, sl_cg, sl_cat, sl_cbt]

                gr.Markdown("### 控制")
                with gr.Row():
                    btn_toggle = gr.Button("▶ 开始", variant="primary", scale=2)
                    btn_step = gr.Button("⏭ 单步", scale=1)
                with gr.Row():
                    btn_apply = gr.Button("✅ 应用参数", variant="secondary", scale=1)
                    btn_reset = gr.Button("🔄 重置", variant="stop", scale=1)

                gr.Markdown("### 手动冲击")
                with gr.Row():
                    btn_oil = gr.Button("🛢 石油危机", size="sm")
                    btn_tech = gr.Button("💡 技术突破", size="sm")
                with gr.Row():
                    btn_demand = gr.Button("📉 需求骤降", size="sm")
                    btn_trade = gr.Button("⚔ 贸易战", size="sm")
                with gr.Row():
                    btn_bank = gr.Button("🏦 银行恐慌", size="sm")
                    btn_recovery = gr.Button("🌱 经济复苏", size="sm")

            # ── 右侧：图表面板 ──────────────────────────────────
            with gr.Column(scale=3):
                gr.Markdown("### 宏观经济指标")
                macro_plot = gr.Plot(label="", show_label=False)

                gr.Markdown("### 双城对比")
                city_plot = gr.Plot(label="", show_label=False)

        # ── 定时器（每 0.5s 轮询刷新）──────────────────────────
        timer = gr.Timer(value=0.5)

        # ── 事件绑定 ─────────────────────────────────────────────
        outputs = [stats_md, macro_plot, city_plot]

        # 定时轮询
        timer.tick(fn=cb_poll, outputs=outputs)

        # 控制按钮
        btn_step.click(fn=cb_step, outputs=outputs)
        btn_toggle.click(fn=cb_toggle, outputs=[btn_toggle] + outputs)
        btn_reset.click(fn=cb_reset, outputs=[btn_toggle] + outputs)
        btn_apply.click(fn=cb_apply, inputs=all_sliders, outputs=outputs)

        # 冲击按钮
        for btn, shock in [
            (btn_oil, "oil_crisis"),
            (btn_tech, "tech_breakthrough"),
            (btn_demand, "demand_slowdown"),
            (btn_trade, "trade_war"),
            (btn_bank, "banking_panic"),
            (btn_recovery, "recovery"),
        ]:
            btn.click(fn=lambda s=shock: cb_shock(s), outputs=outputs)

    return demo


# ── 入口 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    os.environ["NO_PROXY"] = "localhost,127.0.0.1"
    os.environ.pop("HTTP_PROXY", None)
    os.environ.pop("HTTPS_PROXY", None)

    _init()
    demo = build_ui()
    demo.queue()
    demo.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=True,
        show_error=True,
        theme=gr.themes.Soft(
            primary_hue="blue",
            secondary_hue="slate",
            neutral_hue="slate",
        ),
    )
