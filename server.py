"""
Mesa 经济沙盘 - 自定义 Solara 应用（完整控制版）
运行: solara run server.py
访问: http://127.0.0.1:8521

完全响应式：参数改动实时生效，不中断模拟。
"""

from __future__ import annotations

import io
import logging
from typing import Any

import solara
from matplotlib.figure import Figure
import pandas as pd

from model import EconomyModel, Household, Firm, Bank, Trader

# ─────────────────────────────────────────────
# 日志配置
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("econ")

# ─────────────────────────────────────────────
# 全局配置
# ─────────────────────────────────────────────

APP_NAME = "经济沙盘"
STYLE_CONTAINER = (
    "background:linear-gradient(135deg,#0f172a,#1e3a5f);"
    "color:#e2e8f0;border-radius:12px;padding:18px 20px;"
    "font-family:Segoe UI,sans-serif;box-shadow:0 4px 20px rgba(0,0,0,0.3);"
)
STYLE_HEADER = (
    "font-size:16px;font-weight:700;margin-bottom:10px;"
    "border-bottom:1px solid #334155;padding-bottom:8px;"
)
STYLE_GRID = "display:grid;grid-template-columns:1fr 1fr;gap:6px 24px;font-size:13px;"

CHART_CONFIG = [
    ("stock_price", "股价指数"),
    ("gdp", "GDP产出"),
    ("unemployment", "失业率(%)"),
    ("price_index", "物价指数"),
    ("gini", "基尼系数"),
    ("buy_orders", "买入订单"),
    ("loans", "贷款余额"),
    ("stock_volatility", "股价波动率"),
    ("bad_debt_rate", "银行坏账率"),
    ("default_count", "违约企业数"),
]

SCENARIOS = {
    "默认": {},
    "经济危机": {
        "n_households": 30, "n_firms": 5,
        "base_interest_rate": 0.15, "tax_rate": 0.25,
    },
    "宽松政策": {
        "base_interest_rate": 0.01, "tax_rate": 0.05, "subsidy": 15.0,
    },
    "高税收高福利": {
        "tax_rate": 0.40, "subsidy": 20.0, "min_wage": 15.0,
    },
    "自由市场": {
        "tax_rate": 0.05, "base_interest_rate": 0.02,
        "subsidy": 0.0, "min_wage": 0.0,
    },
}

PARAM_CONFIG = {
    "n_households":      {"min": 5,  "max": 80,  "step": 5,   "default": 20,  "label": "家庭数量",    "fmt": None},
    "n_firms":           {"min": 3,  "max": 40,  "step": 1,   "default": 10,  "label": "企业数量",    "fmt": None},
    "n_traders":         {"min": 5,  "max": 80,  "step": 5,   "default": 20,  "label": "交易者数量",  "fmt": None},
    "tax_rate":          {"min": 0.0,"max": 0.45,"step": 0.01, "default": 0.15,"label": "所得税率",   "fmt": "0%"},
    "base_interest_rate":{"min": 0.0,"max": 0.25,"step": 0.01, "default": 0.05,"label": "基准利率",   "fmt": "0%"},
    "min_wage":          {"min": 0.0,"max": 20.0,"step": 0.5, "default": 7.0, "label": "最低工资",   "fmt": None},
    "productivity":      {"min": 0.1,"max": 3.0, "step": 0.1, "default": 1.0, "label": "生产率",     "fmt": None},
    "subsidy":           {"min": 0.0,"max": 20.0,"step": 0.5, "default": 0.0,  "label": "失业补贴",   "fmt": None},
}

# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def get_agent_groups(model: EconomyModel):
    firms = [a for a in model.agents if isinstance(a, Firm)]
    households = [a for a in model.agents if isinstance(a, Household)]
    traders = [a for a in model.agents if isinstance(a, Trader)]
    banks = [a for a in model.agents if isinstance(a, Bank)]
    return firms, households, traders, banks


def build_macro_stats(model: EconomyModel) -> dict[str, Any]:
    firms, households, traders, banks = get_agent_groups(model)
    n_hh = len(households)
    employed = sum(1 for h in households if h.employed)
    emp_rate = (employed / n_hh * 100) if n_hh > 0 else 0.0
    return {
        "cycle":         model.cycle,
        "n_firms":       len(firms),
        "employed":      employed,
        "n_households":  n_hh,
        "emp_rate":      emp_rate,
        "unemployed":    n_hh - employed if n_hh > 0 else 0,
        "n_traders":     len(traders),
        "total_prod":    sum(getattr(f, "production", 0) for f in firms),
        "dividends":     model.total_dividends,
        "tax_rate":      model.tax_rate,
        "interest_rate": model.base_interest_rate,
        "price_index":   model.price_index,
        "govt_rev":      model.govt_revenue,
        "loans":         model.total_loans_outstanding,
        "volatility":    model.stock_volatility,
        "default_count": model.default_count,
        "bad_debt_rate": model.bank_bad_debt_rate,
        "stock_price":   model.stock_price,
        "gdp":           model.gdp,
        "unemployment":  model.unemployment,
        "gini":          model.gini,
    }


def get_cycle_stage(model: EconomyModel) -> tuple[str, str]:
    if model.gdp > 2000 and model.unemployment < 0.1:
        return "繁荣", "#22c55e"
    elif model.gdp > 1500 and model.unemployment < 0.15:
        return "复苏", "#84cc16"
    elif model.gdp > 1000:
        return "平稳", "#eab308"
    elif model.unemployment > 0.25:
        return "萧条", "#ef4444"
    else:
        return "衰退", "#f97316"


def render_matplotlib_figure(data: list[float], label: str, color: str = "#3b82f6") -> Figure:
    """用 matplotlib 渲染折线图，返回 Figure 对象"""
    fig = Figure(facecolor="#0f172a", dpi=80)
    ax = fig.add_subplot(facecolor="#0f172a")
    ax.plot(data, color=color, linewidth=1.5)
    ax.set_title(label, color="#e2e8f0", fontsize=10)
    ax.tick_params(colors="#94a3b8", labelsize=7)
    ax.xaxis.label.set_color("#94a3b8")
    for spine in ax.spines.values():
        spine.set_edgecolor("#334155")
    fig.tight_layout(pad=0.5)
    return fig


def get_chart_data(model: EconomyModel) -> dict[str, list]:
    """从 datacollector 提取所有指标的时序数据"""
    if not hasattr(model, "datacollector") or model.datacollector is None:
        return {}
    df = model.datacollector.get_model_vars_dataframe()
    result = {}
    for key, _ in CHART_CONFIG:
        if key in df.columns:
            vals = df[key].dropna().tolist()
            # 转换 unemployment: 它存的是百分比数字 15.0，不是小数
            if key == "unemployment":
                vals = [v / 100 for v in vals] if vals else []
            result[key] = vals
    return result


# ─────────────────────────────────────────────
# 全局响应式状态（模型引用 + 参数）
# ─────────────────────────────────────────────

model_ref: solara.Reactive[EconomyModel | None] = solara.reactive(None)

# 步进循环控制
running_ref: solara.Reactive[bool] = solara.reactive(False)
play_task_ref: solara.Reactive[Any] = solara.reactive(None)


def reset_model(params: dict):
    """完全重置模型（用于初始化或重建）"""
    model_ref.set(EconomyModel(**params))
    logger.info("模型已重置，参数: %s", params)


# ─────────────────────────────────────────────
# Solara 组件
# ─────────────────────────────────────────────

@solara.component
def ControlBar(initial_params: dict):
    """顶部控制栏：初始化、重置、播放/暂停"""
    playing = solara.use_reactive(False)

    def on_init():
        reset_model(initial_params)

    def on_reset():
        params = _get_current_params()
        reset_model(params)
        playing.set(False)

    def on_step():
        m = model_ref.value
        if m is not None:
            m.step()

    def on_play():
        if playing.value:
            playing.set(False)
        else:
            playing.set(True)

    # 播放循环
    def play_loop():
        while playing.value:
            m = model_ref.value
            if m is not None:
                m.step()
            import time
            time.sleep(0.5)
        playing.set(False)

    import threading
    if playing.value and (play_task_ref.value is None or not play_task_ref.value.is_alive()):
        t = threading.Thread(target=play_loop, daemon=True)
        t.start()
        play_task_ref.set(t)

    solara.use_effect(lambda: on_init(), [])
    solara.use_effect(
        lambda: playing.set(running_ref.value),
        [running_ref.value],
    )

    with solara.Row(gap="8px", align="center"):
        solara.Button("▶ 播放", on_click=on_play, color="success",
                      disabled=model_ref.value is None)
        solara.Button("⏸ 暂停", on_click=lambda: playing.set(False),
                      disabled=not playing.value)
        solara.Button("步进", on_click=on_step,
                      disabled=model_ref.value is None)
        solara.Button("重置", on_click=on_reset, color="error")
        solara.Text(f"周期: {model_ref.value.cycle if model_ref.value else 0}",
                    style="color:#e2e8f0;margin-left:8px;font-size:14px;")


@solara.component
def ParamSliders():
    """参数滑块：实时调整经济参数"""
    n_households = solara.use_reactive(20)
    n_firms = solara.use_reactive(10)
    n_traders = solara.use_reactive(20)
    tax_rate = solara.use_reactive(0.15)
    base_interest_rate = solara.use_reactive(0.05)
    min_wage = solara.use_reactive(7.0)
    productivity = solara.use_reactive(1.0)
    subsidy = solara.use_reactive(0.0)

    def apply_immediately(key: str, value: Any):
        """立即应用到当前运行的模型（不重置模拟）"""
        m = model_ref.value
        if m is not None and hasattr(m, key):
            setattr(m, key, value)
            logger.info("实时调整 %s = %s", key, value)

    def on_change_factory(var_name: str, slider_ref: solara.Reactive):
        def handler(value):
            slider_ref.set(value)
            apply_immediately(var_name, value)
        return handler

    with solara.Card("⚙️ 经济参数（实时生效）", margin=0):
        with solara.Grid(columns=2):
            for key, cfg in PARAM_CONFIG.items():
                if key == "n_households":
                    solara.FloatSlider(
                        label=cfg["label"], value=n_households,
                        min=cfg["min"], max=cfg["max"], step=int(cfg["step"]),
                        format=cfg.get("fmt"),
                        on_value=lambda v, k=key: on_change_factory(k, n_households)(v),
                    )
                elif key == "n_firms":
                    solara.FloatSlider(
                        label=cfg["label"], value=n_firms,
                        min=cfg["min"], max=cfg["max"], step=int(cfg["step"]),
                        on_value=lambda v, k=key: on_change_factory(k, n_firms)(v),
                    )
                elif key == "n_traders":
                    solara.FloatSlider(
                        label=cfg["label"], value=n_traders,
                        min=cfg["min"], max=cfg["max"], step=int(cfg["step"]),
                        on_value=lambda v, k=key: on_change_factory(k, n_traders)(v),
                    )
                elif key == "tax_rate":
                    solara.FloatSlider(
                        label=cfg["label"], value=tax_rate,
                        min=cfg["min"], max=cfg["max"], step=cfg["step"],
                        format="0%", on_value=lambda v: (tax_rate.set(v), apply_immediately("tax_rate", v)),
                    )
                elif key == "base_interest_rate":
                    solara.FloatSlider(
                        label=cfg["label"], value=base_interest_rate,
                        min=cfg["min"], max=cfg["max"], step=cfg["step"],
                        format="0%", on_value=lambda v: (base_interest_rate.set(v), apply_immediately("base_interest_rate", v)),
                    )
                elif key == "min_wage":
                    solara.FloatSlider(
                        label=cfg["label"], value=min_wage,
                        min=cfg["min"], max=cfg["max"], step=cfg["step"],
                        on_value=lambda v: (min_wage.set(v), apply_immediately("min_wage", v)),
                    )
                elif key == "productivity":
                    solara.FloatSlider(
                        label=cfg["label"], value=productivity,
                        min=cfg["min"], max=cfg["max"], step=cfg["step"],
                        on_value=lambda v: (productivity.set(v), apply_immediately("productivity", v)),
                    )
                elif key == "subsidy":
                    solara.FloatSlider(
                        label=cfg["label"], value=subsidy,
                        min=cfg["min"], max=cfg["max"], step=cfg["step"],
                        on_value=lambda v: (subsidy.set(v), apply_immediately("subsidy", v)),
                    )


@solara.component
def MacroStatsPanel():
    """宏观快照面板"""
    m = model_ref.value
    if m is None:
        solara.Text("模型加载中...")
        return

    stats = build_macro_stats(m)
    stage, stage_color = get_cycle_stage(m)

    vol = stats["volatility"]
    vol_color = "#ef4444" if vol > 0.3 else "#e2e8f0"
    bdr = stats["bad_debt_rate"]
    bdr_color = "#ef4444" if bdr > 0.1 else "#e2e8f0"

    html = (
        f"<div style='{STYLE_CONTAINER}'>"
        f"  <div style='{STYLE_HEADER}'>📊 宏观快照 — 第 {stats['cycle']} 轮 "
        f"    <span style='background:{stage_color};padding:2px 10px;border-radius:6px;margin-left:12px;font-size:13px;'>{stage}</span>"
        f"  </div>"
        f"  <div style='{STYLE_GRID}'>"
        f"    <div>🏭 <b>企业</b>: {stats['n_firms']}</div>"
        f"    <div>👥 <b>就业</b>: {stats['employed']}/{stats['n_households']} ({stats['emp_rate']:.1f}%)</div>"
        f"    <div>📉 <b>失业</b>: {stats['unemployed']}人</div>"
        f"    <div>📈 <b>交易者</b>: {stats['n_traders']}</div>"
        f"    <div>📦 <b>产出</b>: {stats['total_prod']:.1f}</div>"
        f"    <div>💰 <b>股息</b>: {stats['dividends']:.1f}</div>"
        f"    <div>📋 <b>税率</b>: {stats['tax_rate']:.0%}</div>"
        f"    <div>💵 <b>利率</b>: {stats['interest_rate']:.0%}</div>"
        f"    <div>🏷️ <b>物价</b>: {stats['price_index']:.2f}</div>"
        f"    <div>🏛️ <b>财政</b>: {stats['govt_rev']:.1f}</div>"
        f"    <div>💳 <b>贷款</b>: {stats['loans']:.1f}</div>"
        f"  </div>"
        f"  <div style='border-top:1px solid #334155;margin-top:8px;padding-top:8px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:4px 16px;font-size:13px;'>"
        f"    <div>📊 波动率: <span style='color:{vol_color}'>{vol:.3f}</span></div>"
        f"    <div>🚨 违约: {stats['default_count']}家</div>"
        f"    <div>🏦 坏账率: <span style='color:{bdr_color}'>{bdr:.1%}</span></div>"
        f"  </div>"
        f"</div>"
    )
    solara.HTML(tag="div", unsafe_innerHTML=html)


@solara.component
def PolicyPanel():
    """政策干预面板（直接调模型方法）"""
    def adjust(model_attr: str, delta: float, label: str):
        m = model_ref.value
        if m is None:
            return
        old = getattr(m, model_attr)
        new_val = max(0.0, min(getattr(m, model_attr) + delta, 0.5))
        setattr(m, model_attr, new_val)
        logger.info("政策 %s: %.4f → %.4f", label, old, new_val)

    with solara.Card("🎛️ 政策工具（实时生效）", margin=0):
        with solara.Row(gap="6px"):
            solara.Button("降息50BP", on_click=lambda: adjust("base_interest_rate", -0.005, "降息"),
                          color="primary", style="font-size:12px;")
            solara.Button("加息50BP", on_click=lambda: adjust("base_interest_rate", 0.005, "加息"),
                          color="primary", style="font-size:12px;")
        with solara.Row(gap="6px"):
            solara.Button("减税5%", on_click=lambda: adjust("tax_rate", -0.05, "减税"),
                          color="success", style="font-size:12px;")
            solara.Button("加税5%", on_click=lambda: adjust("tax_rate", 0.05, "加税"),
                          color="error", style="font-size:12px;")
        with solara.Row(gap="6px"):
            solara.Button("补贴+5", on_click=lambda: adjust("subsidy", 5.0, "补贴+"),
                          color="warning", style="font-size:12px;")
            solara.Button("削减-5", on_click=lambda: adjust("subsidy", -5.0, "补贴-"),
                          color="warning", style="font-size:12px;")
        with solara.Row(gap="6px"):
            solara.Button("生产率+10%", on_click=lambda: adjust("productivity", 0.1, "生产率+"),
                          style="font-size:12px;")
            solara.Button("生产率-10%", on_click=lambda: adjust("productivity", -0.1, "生产率-"),
                          style="font-size:12px;")
        
        m = model_ref.value
        if m:
            solara.Text(
                f"当前: 税率{m.tax_rate:.0%} 利率{m.base_interest_rate:.0%} 补贴{m.subsidy:.1f} 生产率{m.productivity:.1f}",
                style="color:#94a3b8;font-size:11px;margin-top:8px;"
            )


@solara.component
def ScenarioPanel():
    """场景预设面板"""
    selected = solara.use_reactive("默认")

    def apply_scenario():
        scenario = SCENARIOS.get(selected.value, {})
        m = model_ref.value
        if m is None:
            return
        for key, value in scenario.items():
            if hasattr(m, key):
                setattr(m, key, value)
                logger.info("场景 '%s': %s = %s", selected.value, key, value)

    with solara.Card("🎬 场景预设", margin=0):
        solara.Select(
            label="选择场景",
            value=selected,
            values=list(SCENARIOS.keys()),
        )
        solara.Button("应用场景", on_click=apply_scenario, color="primary")


@solara.component
def AgentDetailPanel():
    """Agent 详情面板"""
    selected_type = solara.use_reactive("家庭")
    agent_types = ["家庭", "企业", "交易者", "银行"]
    type_map = {"家庭": Household, "企业": Firm, "交易者": Trader, "银行": Bank}

    agents = []
    m = model_ref.value
    if m is not None:
        agents = [a for a in m.agents if isinstance(a, type_map.get(selected_type.value, Household))]

    with solara.Card(f"👤 Agent详情 ({len(agents)}个)", margin=0):
        solara.Select(
            label="选择类型",
            value=selected_type,
            values=agent_types,
        )

        if agents:
            items = []
            for a in agents[:5]:
                uid = a.unique_id
                cash = getattr(a, "cash", 0)
                wealth = getattr(a, "wealth", 0)
                if isinstance(a, Household):
                    employed = "✅就业" if getattr(a, "employed", False) else "❌失业"
                    salary = getattr(a, "salary", 0)
                    shares = getattr(a, "shares_owned", 0)
                    items.append(f"#{uid} 现金:{cash:.0f} 财富:{wealth:.0f} {employed} 工资:{salary:.1f} 持股:{shares}")
                elif isinstance(a, Firm):
                    prod = getattr(a, "production", 0)
                    inv = getattr(a, "inventory", 0)
                    emp = getattr(a, "employees", 0)
                    dp = getattr(a, "default_probability", 0)
                    items.append(f"#{uid} 产出:{prod:.1f} 库存:{inv:.1f} 员工:{emp} 违约风险:{dp:.0%}")
                elif isinstance(a, Trader):
                    shares = getattr(a, "shares", 0)
                    mom = getattr(a, "momentum", 0)
                    items.append(f"#{uid} 现金:{cash:.0f} 持股:{shares} 动量:{mom:.3f}")
                elif isinstance(a, Bank):
                    reserves = getattr(a, "reserves", 0)
                    bad = getattr(a, "bad_debts", 0)
                    items.append(f"#{uid} 储备:{reserves:.0f} 坏账:{bad:.1f}")
            
            for item in items:
                solara.Text(item, style="font-size:12px;color:#94a3b8;font-family:Consolas,monospace;")


@solara.component
def ChartPanel():
    """图表面板：使用 matplotlib 渲染"""
    m = model_ref.value
    if m is None:
        solara.Text("模型加载中...")
        return

    chart_data = get_chart_data(m)
    colors = ["#3b82f6", "#22c55e", "#f97316", "#a855f7", "#ef4444",
              "#06b6d4", "#84cc16", "#f59e0b", "#ec4899", "#6366f1"]

    with solara.Grid(columns=3):
        for i, (key, label) in enumerate(CHART_CONFIG):
            data = chart_data.get(key, [])
            if not data:
                solara.FigureMatplotlib(
                    render_matplotlib_figure([0], label, colors[i % len(colors)])
                )
            else:
                solara.FigureMatplotlib(
                    render_matplotlib_figure(data, label, colors[i % len(colors)])
                )


@solara.component
def ExportPanel():
    """数据导出面板"""
    csv_bytes = solara.use_reactive(b"")
    filename = solara.use_reactive("econ_data.csv")

    def on_export():
        m = model_ref.value
        if m is None or not hasattr(m, "datacollector"):
            return
        df = m.datacollector.get_model_vars_dataframe()
        buf = io.StringIO()
        df.to_csv(buf)
        csv_bytes.set(buf.getvalue().encode("utf-8"))
        filename.set(f"econ_cycle{m.cycle}.csv")
        logger.info("导出 %d 字节", len(csv_bytes.value))

    with solara.Card("📥 数据导出", margin=0):
        solara.Button("导出CSV", on_click=on_export, color="primary", icon_name="mdi-download")
        if csv_bytes.value:
            solara.FileDownload(
                data=csv_bytes.value,
                filename=filename.value,
                label="📥 点击下载 CSV",
            )


@solara.component
def DebugLogPanel():
    """实时调试日志"""
    m = model_ref.value
    if m is None:
        return

    firms = [a for a in m.agents if isinstance(a, Firm)][:3]
    households = [a for a in m.agents if isinstance(a, Household)][:3]
    banks = [a for a in m.agents if isinstance(a, Bank)]

    vol = m.stock_volatility
    defaults = m.default_count
    bdr = m.bank_bad_debt_rate

    lines = []
    for f in firms:
        prod = getattr(f, "production", 0)
        inv = getattr(f, "inventory", 0)
        loan = getattr(f, "loan_principal", 0)
        dp = getattr(f, "default_probability", 0)
        lines.append(f"企业{f.unique_id}: 产出{prod:.1f} 库存{inv:.1f} 负债{loan:.1f} 违约{dp:.0%}")

    for h in households:
        emp = "就业" if getattr(h, "employed", False) else "失业"
        cash = getattr(h, "cash", 0)
        shares = getattr(h, "shares_owned", 0)
        lines.append(f"家庭{h.unique_id}: {emp} 现金{cash:.0f} 持股{shares}")

    for b in banks:
        reserves = getattr(b, "reserves", 0)
        bad = getattr(b, "bad_debts", 0)
        lines.append(f"银行{b.unique_id}: 储备{reserves:.0f} 坏账{bad:.1f}")

    alerts = []
    if vol > 0.3:
        alerts.append(f"⚠️ 股价波动: {vol:.3f}")
    if defaults > 0:
        alerts.append(f"🚨 企业违约: {defaults}家")
    if bdr > 0.1:
        alerts.append(f"🏦 坏账率: {bdr:.1%}")

    alert_color = "#ef4444" if alerts else "#4ade80"
    alert_text = " | ".join(alerts) if alerts else "✅ 系统正常"

    html = (
        "<div style='background:#0f172a;border-radius:8px;padding:12px;"
        "font-family:Consolas,monospace;font-size:11px;max-height:280px;overflow-y:auto;'>"
        f"<div style='margin-bottom:6px;color:#94a3b8;'>🐛 Agent 采样 (每轮刷新)</div>"
    )
    for line in lines:
        html += f"<div style='margin:2px 0;color:#e2e8f0;'>{line}</div>"
    html += (
        f"<div style='border-top:1px solid #334155;margin-top:8px;padding-top:8px;"
        f"color:{alert_color};font-weight:bold;'>{alert_text}</div>"
        "</div>"
    )
    solara.HTML(tag="div", unsafe_innerHTML=html)


# ─────────────────────────────────────────────
# 主页面布局
# ─────────────────────────────────────────────

@solara.component
def Page():
    """主页面"""
    # 初始化参数
    initial_params = {
        "n_households": 20,
        "n_firms": 10,
        "n_traders": 20,
        "tax_rate": 0.15,
        "base_interest_rate": 0.05,
        "min_wage": 7.0,
        "productivity": 1.0,
        "subsidy": 0.0,
    }

    # 顶部标题 + 控制栏
    solara.Markdown("# 🏛️ Mesa 经济沙盘")
    solara.Markdown("**消费者 × 企业 × 银行 × 交易者 — 四大市场实时仿真**")
    ControlBar(initial_params)

    # 参数 + 政策
    with solara.Grid(columns=2):
        ParamSliders()
        PolicyPanel()

    # 场景 + Agent详情 + 导出
    with solara.Grid(columns=3):
        ScenarioPanel()
        AgentDetailPanel()
        ExportPanel()

    # 宏观快照
    MacroStatsPanel()

    # 调试日志
    DebugLogPanel()

    # 图表
    solara.Markdown("### 📈 经济指标时序图")
    ChartPanel()


# ─────────────────────────────────────────────
# 启动
# ─────────────────────────────────────────────

logger.info("启动 Mesa 经济沙盘 Solara 应用...")
page = Page()
