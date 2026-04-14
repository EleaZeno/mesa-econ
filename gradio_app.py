"""
经济沙盘 v5.2 — Gradio 版 (四方向全部完成)
方向一: 拉式信贷/资产负债表衰退 | 方向二: 国债市场/收益率曲线/CRT
方向三: B2B供应链/牛鞭效应 | 方向四: 空间经济学/地租
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

from model import EconomyModel, PlayerHousehold

# ── 全局仿真状态 ─────────────────────────────────────────────────
_lock = threading.RLock()  # RLock: 可重入，防止 _rec/_snapshot 在 _lock 内递归死锁
_md: Optional[EconomyModel] = None
_hist: list = []
_hl = threading.Lock()
_run = False
_stop = threading.Event()
_thr: Optional[threading.Thread] = None

# ── 玩家决策缓存（跨 API 传递）─────────────────────────────────
_player_options_cache: dict = {}  # 供 /api/player_options 读取
_player_decision_ready: dict = {}  # 供 model.step() 末尾读取

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
        productivity=1.0, subsidy=10.0, gov_purchase=50.0,
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
            f"## 经济沙盘 v5.2 &nbsp;&nbsp;"
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
        stats_md = "## 经济沙盘 v5.2\n\n*仿真未开始，点击「单步」或「开始」*"

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


# ── 玩家化身回调 ─────────────────────────────────────────────────
def cb_player_status():
    """返回玩家 HH 的实时状态面板"""
    with _lock:
        if _md is None:
            return "**玩家状态：** 仿真未启动"
        ph = next(
            (h for h in _md.households if isinstance(h, PlayerHousehold)), None
        )
        if ph is None:
            return "**玩家状态：** 未找到玩家化身"
        pending = getattr(_md, "_pending_player", {})
        waiting = "🟡 **等待决策**" if pending.get("_waiting") else "🟢 AI 自主"
        return (
            f"**玩家 HH #{ph.unique_id}**  {waiting}\n\n"
            f"💰 现金：**{ph.cash:.1f}** &nbsp;|&nbsp; "
            f"工资：**{ph.salary:.1f}**/轮 &nbsp;|&nbsp; "
            f"商品：**{ph.goods}**\n\n"
            f"📊 财富：**{ph.wealth:.1f}** &nbsp;|&nbsp; "
            f"贷款：**{ph.loan_principal:.1f}** &nbsp;|&nbsp; "
            f"信用分：**{ph.credit_score:.0f}**\n\n"
            f"📈 持股：**{ph.shares_owned}** 股 × {_md.stock_price:.1f} = "
            f"**{ph.shares_owned * _md.stock_price:.1f}** &nbsp;|&nbsp; "
            f"🏙️ 城市：**{ph.city.value if hasattr(ph.city, 'value') else ph.city}**\n\n"
            f"当前轮次：**{_md.cycle}** &nbsp;|&nbsp; "
            f"健康分：**{_md.health_score:.0f}**"
        )


def cb_player_options():
    """返回商品选项列表（供下拉框用）"""
    with _lock:
        if _md is None:
            return [], []
        pending = getattr(_md, "_pending_player", {})
        if not pending:
            return [], []
        goods = pending.get("goods_options", [])
        firms = pending.get("job_options", [])
        # label = "企业N [城市] ¥价格"
        goods_choices = [
            f"企业{f['firm_id']} [{f['city']}] ¥{f['price']:.0f}"
            for f in goods
        ] if goods else ["-- 暂无商品 --"]
        job_choices = [
            f"企业{f['firm_id']} [{f['industry']}] 工资¥{f['wage_offer']:.0f}"
            for f in firms
        ] if firms else ["-- 暂无职位 --"]
        return goods_choices, job_choices


def cb_submit_decision(action_type: str, firm_idx: int, qty: int,
                        shares: int, firm_job_idx: int):
    """玩家提交决策 → 写入 model._pending_player['decision']"""
    with _lock:
        if _md is None:
            return "❌ 模型未启动"
        pending = getattr(_md, "_pending_player", {})
        if not pending.get("_waiting"):
            return "❌ 当前帧无需玩家决策"
        decision = {"action": action_type}
        if action_type == "consume" and firm_idx >= 0:
            goods = pending.get("goods_options", [])
            if firm_idx < len(goods):
                decision["firm_id"] = goods[firm_idx]["firm_id"]
                decision["qty"] = max(1, qty)
        elif action_type == "buy_stock" and shares > 0:
            decision["shares"] = shares
        elif action_type == "sell_stock" and shares > 0:
            decision["shares"] = shares
        elif action_type == "accept_job" and firm_job_idx >= 0:
            jobs = pending.get("job_options", [])
            if firm_job_idx < len(jobs):
                decision["firm_id"] = jobs[firm_job_idx]["firm_id"]
        # 写入模型
        _md._pending_player["decision"] = decision
        return f"✅ 决策已提交 [{action_type}]，将在下一帧结算"


# ── 玩家企业回调 ─────────────────────────────────────────────────
from model import PlayerFirm

def cb_firm_status():
    """返回玩家企业的实时状态面板"""
    with _lock:
        if _md is None:
            return "**玩家企业：** 仿真未启动"
        pf = next((f for f in _md.firms if isinstance(f, PlayerFirm)), None)
        if pf is None:
            return "**玩家企业：** 未找到玩家企业"
        pending = getattr(_md, "_pending_firm", {})
        waiting = "🟡 **等待决策**" if pending.get("_waiting") else "🟢 AI 自主"
        return (
            f"**玩家企业 #{pf.unique_id}** [{pf.industry.value}] {waiting}\n\n"
            f"💰 现金：**{pf.cash:.1f}** &nbsp;|&nbsp; "
            f"库存：**{pf.inventory:.1f}** &nbsp;|&nbsp; "
            f"员工：**{pf.employees}**\n\n"
            f"💵 时薪：**{pf.wage_offer:.1f}** &nbsp;|&nbsp; "
            f"📦 商品价格：**{pf.price:.1f}** &nbsp;|&nbsp; "
            f"📈 分红：**{pf.dividend_per_share:.2f}**/股\n\n"
            f"🏦 贷款：**{pf.loan_principal:.1f}** &nbsp;|&nbsp; "
            f"📍 城市：**{pf.city.value if hasattr(pf.city, 'value') else pf.city}**\n\n"
            f"🔔 待处理：**{pending.get('available_workers', [])[:3]}**"
        )


def cb_firm_options():
    """返回玩家企业的选项（招聘/开店）"""
    with _lock:
        if _md is None:
            return [], [], [], [], "¥0"
        pending = getattr(_md, "_pending_firm", {})
        if not pending:
            return [], [], [], [], "¥0"
        workers = pending.get("available_workers", [])
        unemployed = pending.get("unemployed_workers", [])
        wage = pending.get("wage_offer", 0)
        return (
            [f"ID:{w['hh_id']} [技能{w['skill']}]" for w in workers],
            [f"ID:{w['hh_id']} [技能{w['skill']}]" for w in unemployed],
            list(range(0, 11)),  # 0-10 positions
            list(range(5, 50, 5)),  # price 5-50
            list(range(0, 21)),  # dividend 0-2.0
            f"¥{wage:.0f}"
        )


def cb_firm_decision(action_type: str, hh_idx: int, positions: int, price: float, dividend: float):
    """玩家企业提交决策"""
    with _lock:
        if _md is None:
            return "❌ 模型未启动"
        decision = {"action": action_type}
        if action_type == "hire" and hh_idx >= 0:
            pending = getattr(_md, "_pending_firm", {})
            workers = pending.get("available_workers", [])
            if hh_idx < len(workers):
                decision["hh_id"] = workers[hh_idx]["hh_id"]
        elif action_type == "set_wage":
            decision["wage"] = max(1.0, float(price or 8))
        elif action_type == "set_price":
            decision["price"] = max(1.0, float(price or 10))
        elif action_type == "set_dividend":
            decision["dividend"] = max(0.0, float(dividend or 0) / 10.0)
        elif action_type == "open_position":
            decision["positions"] = max(0, int(positions or 2))
        elif action_type == "fire":
            decision["count"] = max(1, int(positions or 1))
        _md._pending_firm["decision"] = decision
        return f"✅ 企业决策已提交 [{action_type}]"





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
    with gr.Blocks(title="经济沙盘 v5.2 — 玩家模式") as demo:

        # ── 顶部状态栏 ──────────────────────────────────────────
        stats_md = gr.Markdown(
            "## 经济沙盘 v5.2\n\n*初始化中...*",
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
                sl_sub = gr.Slider(0, 100, value=10, step=5, label="补贴")
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

                # ── 玩家化身面板 ────────────────────────────────────
                gr.Markdown("### 👤 玩家化身")
                player_status_md = gr.Markdown("*启动后显示玩家状态*")

                # 消费
                with gr.Row():
                    sl_qty = gr.Number(value=1, min=1, max=10, step=1, label="购买数量", scale=1)
                    sel_goods = gr.Dropdown(label="选择商品", choices=[], scale=2, interactive=True)
                with gr.Row():
                    btn_consume = gr.Button("🛒 消费", variant="secondary", scale=1)
                    btn_skip = gr.Button("⏭ 跳过本轮", scale=1)

                # 股票
                with gr.Row():
                    sl_shares = gr.Number(value=1, min=1, max=100, step=1, label="股数", scale=1)
                with gr.Row():
                    btn_buy_stock = gr.Button("📈 买入股票", scale=1)
                    btn_sell_stock = gr.Button("📉 卖出股票", scale=1)

                # 跳槽
                sel_job = gr.Dropdown(label="接受工作 offer", choices=[], interactive=True)
                btn_accept_job = gr.Button("💼 跳槽", variant="primary", scale=1)

                player_feedback = gr.Textbox(label="操作反馈", interactive=False, lines=2)

                # ── 玩家企业面板 ────────────────────────────────────
                gr.Markdown("### 🏭 玩家企业")
                firm_status_md = gr.Markdown("*启动后显示企业状态*")

                with gr.Row():
                    sl_price = gr.Number(value=10, min=1, max=100, step=1, label="商品定价", scale=1)
                    sl_wage = gr.Number(value=8, min=1, max=50, step=0.5, label="工资标准", scale=1)
                with gr.Row():
                    btn_set_price = gr.Button("💰 调价", scale=1)
                    btn_set_wage = gr.Button("💵 调薪", scale=1)

                with gr.Row():
                    sl_positions = gr.Number(value=2, min=0, max=20, step=1, label="招聘名额", scale=1)
                    sl_fire = gr.Number(value=0, min=0, max=10, step=1, label="裁员人数", scale=1)
                with gr.Row():
                    btn_hire = gr.Button("➕ 招人", scale=1)
                    btn_fire = gr.Button("➖ 裁员", scale=1)
                    btn_open_pos = gr.Button("📋 开职位", scale=1)

                with gr.Row():
                    sl_dividend = gr.Number(value=0, min=0, max=5, step=0.1, label="每股分红", scale=1)
                btn_dividend = gr.Button("📊 设分红", scale=1)

                firm_feedback = gr.Textbox(label="企业反馈", interactive=False, lines=2)

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

        # 定时轮询（更新状态 + 选项）
        timer.tick(fn=cb_poll, outputs=outputs)
        # 玩家面板轮询（定时刷新状态和选项）
        timer.tick(fn=cb_player_status, outputs=[player_status_md])
        timer.tick(fn=cb_player_options, outputs=[sel_goods, sel_job])
        timer.tick(fn=cb_firm_status, outputs=[firm_status_md])

        # 控制按钮
        btn_step.click(fn=cb_step, outputs=outputs)
        btn_toggle.click(fn=cb_toggle, outputs=[btn_toggle] + outputs)
        btn_reset.click(fn=cb_reset, outputs=[btn_toggle] + outputs)
        btn_apply.click(fn=cb_apply, inputs=all_sliders, outputs=outputs)

        # 玩家居民操作
        btn_consume.click(
            fn=lambda idx, qty, **kw: cb_submit_decision("consume", int(idx or 0), int(qty or 1), 0, 0),
            inputs=[sel_goods, sl_qty],
            outputs=[player_feedback],
        )
        btn_skip.click(
            fn=lambda **kw: cb_submit_decision("skip", -1, 1, 0, 0),
            inputs=[],
            outputs=[player_feedback],
        )
        btn_buy_stock.click(
            fn=lambda s, **kw: cb_submit_decision("buy_stock", -1, 1, int(s or 1), 0),
            inputs=[sl_shares],
            outputs=[player_feedback],
        )
        btn_sell_stock.click(
            fn=lambda s, **kw: cb_submit_decision("sell_stock", -1, 1, int(s or 1), 0),
            inputs=[sl_shares],
            outputs=[player_feedback],
        )
        btn_accept_job.click(
            fn=lambda idx, **kw: cb_submit_decision("accept_job", -1, 1, 0, int(idx or 0)),
            inputs=[sel_job],
            outputs=[player_feedback],
        )

        # 玩家企业操作
        btn_set_price.click(
            fn=lambda p, **kw: cb_firm_decision("set_price", -1, 0, float(p or 10), 0),
            inputs=[sl_price],
            outputs=[firm_feedback],
        )
        btn_set_wage.click(
            fn=lambda w, **kw: cb_firm_decision("set_wage", -1, 0, float(w or 8), 0),
            inputs=[sl_wage],
            outputs=[firm_feedback],
        )
        btn_hire.click(
            fn=lambda **kw: cb_firm_decision("hire", 0, 0, 0, 0),
            inputs=[],
            outputs=[firm_feedback],
        )
        btn_fire.click(
            fn=lambda n, **kw: cb_firm_decision("fire", -1, int(n or 1), 0, 0),
            inputs=[sl_fire],
            outputs=[firm_feedback],
        )
        btn_open_pos.click(
            fn=lambda p, **kw: cb_firm_decision("open_position", -1, int(p or 2), 0, 0),
            inputs=[sl_positions],
            outputs=[firm_feedback],
        )
        btn_dividend.click(
            fn=lambda d, **kw: cb_firm_decision("set_dividend", -1, 0, 0, float(d or 0) * 10),
            inputs=[sl_dividend],
            outputs=[firm_feedback],
        )

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
