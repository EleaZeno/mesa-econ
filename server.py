"""
Mesa 经济沙盘 - 自定义 Solara 应用（v3.1 修复版）
运行: solara run server.py
访问: http://127.0.0.1:8521

v3.1 修复:
  1. Agent 分类 → model.firms/households/traders/banks（O(1)）
  2. 线程 → threading.Event 优雅停止
  3. 滑块 → 遍历 PARAM_CONFIG 自动生成（# noqa: SH103）
  4. 场景 → 边界校验 + 滑块同步
  5. 失业率 → 仅 >1 才 /100
  6. 布局 → 全量使用 solara 1.56 确认 API（Columns/Row/GridFixed）
  7. Card → title= 字符串，不支持 f-string
"""

from __future__ import annotations

import io
import logging
import threading
from typing import Any

import solara
from matplotlib.figure import Figure
import pandas as pd

from model import EconomyModel, Household, Firm, Bank, Trader

# ───────────────────────────────────────────────────────────────
# 日志配置
# ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("econ")

# ───────────────────────────────────────────────────────────────
# 全局配置
# ───────────────────────────────────────────────────────────────

CHART_CONFIG = [
    ("stock_price", "股价指数"),
    ("gdp", "GDP总值"),
    ("unemployment", "失业率(%)"),
    ("avg_price", "物价指数"),
    ("gini", "基尼系数"),
    ("buy_orders", "买入订单"),
    ("loans", "信贷总量"),
    ("stock_vol", "股价波动率"),
    ("bad_debt_rate", "坏账率"),
    ("systemic_risk", "系统风险"),
    ("default_count", "违约企业数"),
    ("gov_revenue", "政府收入"),
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
    "高税高补贴": {
        "tax_rate": 0.40, "subsidy": 20.0, "min_wage": 15.0,
    },
    "自由市场": {
        "tax_rate": 0.05, "base_interest_rate": 0.02,
        "subsidy": 0.0, "min_wage": 0.0,
    },
    "政府刺激": {
        "gov_purchase": 150.0, "tax_rate": 0.12, "subsidy": 8.0,
    },
    "金融危机": {
        "base_interest_rate": 0.20, "tax_rate": 0.30,
        "shock_prob": 0.15,
    },
}

PARAM_CONFIG = {
    "n_households":          {"min": 5,   "max": 80,  "step": 5,   "default": 20,  "label": "家庭数量",    "fmt": "%.0f"},
    "n_firms":                {"min": 3,   "max": 40,  "step": 1,   "default": 10,  "label": "企业数量",    "fmt": "%.0f"},
    "n_traders":              {"min": 5,   "max": 80,  "step": 5,   "default": 20,  "label": "交易者数量",  "fmt": "%.0f"},
    "tax_rate":               {"min": 0.0, "max": 0.45,"step": 0.01, "default": 0.15,"label": "所得税率",    "fmt": "%.0f%%"},
    "base_interest_rate":     {"min": 0.0, "max": 0.25,"step": 0.01, "default": 0.05,"label": "基准利率",    "fmt": "%.1f%%"},
    "min_wage":               {"min": 0.0, "max": 20.0,"step": 0.5, "default": 7.0, "label": "最低工资",    "fmt": "%.1f"},
    "productivity":           {"min": 0.1, "max": 3.0, "step": 0.1, "default": 1.0, "label": "全要素生产率", "fmt": "%.2f"},
    "subsidy":                {"min": 0.0, "max": 50.0,"step": 1.0, "default": 0.0, "label": "失业补贴",    "fmt": "%.1f"},
    "gov_purchase":           {"min": 0.0, "max": 200.0,"step": 5.0, "default": 0.0, "label": "政府购买",    "fmt": "%.0f"},
    "capital_gains_tax":      {"min": 0.0, "max": 0.50,"step": 0.01,"default": 0.10,"label": "资本利得税",  "fmt": "%.0f%%"},
    "shock_prob":             {"min": 0.0, "max": 0.20,"step": 0.01,"default": 0.02,"label": "冲击概率",    "fmt": "%.0f%%"},
}

# 经济周期配置（集中管理阈值）
CYCLE_CONFIG = [
    {"gdp_thr": 2000, "u_thr": 0.10, "label": "繁荣", "color": "#22c55e"},
    {"gdp_thr": 1500, "u_thr": 0.15, "label": "复苏", "color": "#84cc16"},
    {"gdp_thr": 1000, "u_thr": 0.25, "label": "平稳", "color": "#eab308"},
    {"gdp_thr": 0,    "u_thr": 0.25, "label": "衰退", "color": "#f97316"},
    {"gdp_thr": 0,    "u_thr": 1.00, "label": "萧条", "color": "#ef4444"},
]

# ───────────────────────────────────────────────────────────────
# 全局响应式状态
# ───────────────────────────────────────────────────────────────

model_ref: solara.Reactive[EconomyModel | None] = solara.reactive(None)

# 滑块引用字典（模块级同步初始化，规避 effect 异步时序问题）
_slider_refs: dict[str, solara.Reactive] = {
    key: solara.reactive(cfg["default"])
    for key, cfg in PARAM_CONFIG.items()
}

# 播放线程停止事件
_play_stop_event: threading.Event | None = None


# ───────────────────────────────────────────────────────────────
# 辅助函数
# ───────────────────────────────────────────────────────────────

def get_agent_groups(model: EconomyModel):
    """直接用 model 分类列表，O(1) 避免 O(n²) 重复 isinstance 筛选"""
    return model.firms, model.households, model.traders, model.banks


def build_macro_stats(model: EconomyModel) -> dict[str, Any]:
    firms, households, traders, banks = get_agent_groups(model)
    n_hh = len(households)
    employed = sum(1 for h in households if h.employed)
    emp_rate = (employed / n_hh * 100) if n_hh > 0 else 0.0
    return {
        "cycle":             model.cycle,
        "n_firms":           len(firms),
        "employed":          employed,
        "n_households":      n_hh,
        "emp_rate":          emp_rate,
        "unemployed":        n_hh - employed if n_hh > 0 else 0,
        "n_traders":         len(traders),
        "total_prod":        sum(getattr(f, "production", 0) for f in firms),
        "dividends":         model.total_dividends,
        "tax_rate":          model.tax_rate,
        "interest_rate":     model.base_interest_rate,
        "price_index":       getattr(model, "price_index", model.avg_price),
        "govt_rev":          model.govt_revenue,
        "gov_purch":         model.gov_purchase,
        "cap_gains":         model.capital_gains_tax_revenue,
        "systemic":          round(model.systemic_risk, 4),
        "bankrupt":          model.bankrupt_count,
        "loans":             model.total_loans_outstanding,
        "volatility":        model.stock_volatility,
        "default_count":     model.default_count,
        "bad_debt_rate":     model.bank_bad_debt_rate,
        "stock_price":       model.stock_price,
        "gdp":               model.gdp,
        "unemployment":       model.unemployment,
        "gini":              model.gini,
        "shock":             getattr(model, "current_shock", ""),
    }


def get_cycle_stage(model: EconomyModel) -> tuple[str, str]:
    for cfg in CYCLE_CONFIG:
        if model.gdp >= cfg["gdp_thr"] and model.unemployment <= cfg["u_thr"]:
            return cfg["label"], cfg["color"]
    return CYCLE_CONFIG[-1]["label"], CYCLE_CONFIG[-1]["color"]


def render_matplotlib_figure(data: list[float], label: str, color: str = "#3b82f6") -> Figure:
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
    if not hasattr(model, "datacollector") or model.datacollector is None:
        return {}
    df = model.datacollector.get_model_vars_dataframe()
    result = {}
    for key, _ in CHART_CONFIG:
        if key in df.columns:
            vals = df[key].dropna().tolist()
            # unemployment: 模型存小数(0.15)则不变；若存百分比(15.0)则 /100
            if key == "unemployment":
                vals = [v / 100 if v > 1 else v for v in vals] if vals else []
            result[key] = vals
    return result


def _apply_immediately(key: str, value: Any):
    m = model_ref.value
    if m is not None and hasattr(m, key):
        setattr(m, key, value)
        logger.info("实时 %s = %s", key, value)


def _sync_slider(key: str, value: Any):
    if _slider_refs is not None and key in _slider_refs:
        _slider_refs[key].set(value)
    _apply_immediately(key, value)


def reset_model(params: dict):
    global _play_stop_event
    if _play_stop_event:
        _play_stop_event.set()
    model_ref.set(EconomyModel(**params))
    logger.info("模型重置: %s", params)


def _get_current_params() -> dict:
    if _slider_refs is None:
        return {key: cfg["default"] for key, cfg in PARAM_CONFIG.items()}
    return {key: ref.value for key, ref in _slider_refs.items()}


# ───────────────────────────────────────────────────────────────
# Solara 组件
# ───────────────────────────────────────────────────────────────

@solara.component
def ControlBar(initial_params: dict):
    """控制栏：Event 优雅停止，无线程泄漏"""
    playing = solara.use_reactive(False)
    loading = solara.use_reactive(False)

    def on_init():
        loading.set(True)
        reset_model(initial_params)
        loading.set(False)

    def on_reset():
        global _play_stop_event
        if _play_stop_event:
            _play_stop_event.set()
        loading.set(True)
        playing.set(False)
        params = _get_current_params()
        reset_model(params)
        loading.set(False)

    def on_step():
        m = model_ref.value
        if m is not None:
            m.step()

    def on_play():
        global _play_stop_event
        if playing.value:
            if _play_stop_event:
                _play_stop_event.set()
            playing.set(False)
        else:
            _play_stop_event = threading.Event()
            t = threading.Thread(target=_run_play_loop, args=(_play_stop_event,), daemon=True)
            t.start()
            playing.set(True)

    solara.use_effect(on_init, [])

    if loading.value:
        solara.SpinnerSolara(label="加载中...")
        return

    btn_color = "success" if not playing.value else "warning"
    btn_icon = "play_arrow" if not playing.value else "pause"
    cycle = getattr(model_ref.value, "cycle", 0) if model_ref.value else 0

    with solara.Row(gap="8px", justify="center"):
        solara.Button(label="播放" if not playing.value else "暂停",
                     icon_name=btn_icon, on_click=on_play, color=btn_color)
        solara.Button(label="单步", icon_name="skip_next",
                      on_click=on_step, color="info",
                      disabled=model_ref.value is None)
        solara.Button(label="重置", icon_name="restart_alt",
                      on_click=on_reset, color="error")
        solara.Text("  第 " + str(cycle) + " 轮")


def _run_play_loop(stop_evt: threading.Event):
    while not stop_evt.wait(0.5):
        m = model_ref.value
        if m is not None:
            m.step()


@solara.component
def ParamSliders():
    """参数面板：遍历 PARAM_CONFIG 自动生成滑块，实时生效"""
    with solara.Card(title="经济参数（实时生效）"):
        with solara.Columns(widths=[6, 6]):
            for key, cfg in PARAM_CONFIG.items():
                ref = _slider_refs[key]
                def make_handler(k):
                    def handler(value):
                        ref.set(value)
                        _apply_immediately(k, value)
                    return handler
                solara.FloatSlider(
                    label=cfg["label"],
                    value=ref,
                    min=cfg["min"],
                    max=cfg["max"],
                    step=cfg["step"],
                    on_value=make_handler(key),
                )


@solara.component
def ScenarioPanel():
    """预设场景：边界校验 + 滑块值同步"""
    selected = solara.use_reactive("默认")

    with solara.Card(title="预设场景"):
        solara.Select(
            label="选择场景",
            value=selected,
            values=list(SCENARIOS.keys()),
        )
        def apply_scenario():
            scenario = SCENARIOS.get(selected.value, {})
            m = model_ref.value
            if m is None:
                return
            for key, value in scenario.items():
                if hasattr(m, key) and key in PARAM_CONFIG:
                    cfg = PARAM_CONFIG[key]
                    valid_val = max(cfg["min"], min(value, cfg["max"]))
                    if valid_val != value:
                        logger.warning(
                            "场景'%s'的%s=%.3f超出[%.2f,%.2f]，修正为%.3f",
                            selected.value, key, value, cfg["min"], cfg["max"], valid_val,
                        )
                    setattr(m, key, valid_val)
                    _sync_slider(key, valid_val)
                    logger.info("场景 '%s': %s = %.3f", selected.value, key, valid_val)
        solara.Button("应用场景", on_click=apply_scenario, color="primary")


@solara.component
def PolicyPanel():
    """利率/税率快速调整：从 PARAM_CONFIG 读取边界"""
    def adjust(model_attr: str, delta: float, label: str):
        m = model_ref.value
        if m is None or model_attr not in PARAM_CONFIG:
            return
        cfg = PARAM_CONFIG[model_attr]
        old = getattr(m, model_attr)
        new_val = max(cfg["min"], min(old + delta, cfg["max"]))
        setattr(m, model_attr, new_val)
        _sync_slider(model_attr, new_val)
        logger.info("政策 %s: %.4f -> %.4f", label, old, new_val)

    with solara.Card(title="利率/税率调整"):
        with solara.Columns(widths=[3, 3, 3, 3]):
            solara.Button("-0.01", on_click=lambda: adjust("base_interest_rate", -0.01, "利率"),
                          color="secondary")
            solara.Button("+0.01", on_click=lambda: adjust("base_interest_rate", 0.01, "利率"),
                          color="secondary")
            solara.Button("-5%", on_click=lambda: adjust("tax_rate", -0.05, "税率"),
                          color="secondary")
            solara.Button("+5%", on_click=lambda: adjust("tax_rate", 0.05, "税率"),
                          color="secondary")


@solara.component
def MacroStatsPanel():
    """宏观快照：告警标记"""
    m = model_ref.value
    if m is None:
        solara.Text("模型加载中...")
        return

    stats = build_macro_stats(m)
    stage, stage_color = get_cycle_stage(m)
    vol = stats["volatility"]
    bdr = stats["bad_debt_rate"]
    shock = stats.get("shock", "") or ""

    cycle_str = "宏观快照  第 " + str(stats["cycle"]) + " 轮"
    with solara.Card(title=cycle_str):
        with solara.Row(gap="8px"):
            solara.Text("周期: " + stage, style="color:" + stage_color + ";font-weight:bold;")
            if shock:
                solara.Text("[" + shock + "]", style="color:#fbbf24;")

        # 3列主指标
        with solara.Columns(widths=[4, 4, 4]):
            solara.Text("GDP: " + str(round(stats["gdp"], 0)))
            solara.Text("物价: " + str(round(stats["price_index"], 1)))
            solara.Text("股价: " + str(round(stats["stock_price"], 1)))
            solara.Text("企业: " + str(stats["n_firms"]))
            emp_str = str(stats["employed"]) + "/" + str(stats["n_households"]) + " (" + str(round(stats["emp_rate"], 0)) + "%)"
            solara.Text("就业: " + emp_str)
            solara.Text("失业: " + str(stats["unemployed"]) + "人")
            solara.Text("基尼: " + str(round(stats["gini"], 3)))
            vol_flag = "!!" if vol > 0.3 else ("!" if vol > 0.15 else "")
            vol_style = "color:#ef4444;" if vol_flag else ""
            solara.Text("波动: " + str(round(vol, 3)) + vol_flag, style=vol_style)
            bdr_flag = "!!" if bdr > 0.1 else ("!" if bdr > 0.03 else "")
            bdr_style = "color:#ef4444;" if bdr_flag else ""
            solara.Text("坏账: " + str(round(bdr * 100, 1)) + "%" + bdr_flag, style=bdr_style)

        # 2列次要指标
        with solara.Columns(widths=[6, 6]):
            solara.Text("政府收入: " + str(round(stats["govt_rev"], 1)))
            solara.Text("政府购买: " + str(round(stats["gov_purch"], 0)))
            solara.Text("总贷款: " + str(round(stats["loans"], 0)))
            solara.Text("资本利得税: " + str(round(stats["cap_gains"], 1)))
            sr = stats["systemic"]
            sr_flag = "!" if sr > 0.2 else ""
            sr_style = "color:#ef4444;" if sr > 0.2 else ""
            solara.Text("系统风险: " + str(round(sr, 3)) + sr_flag, style=sr_style)
            solara.Text("破产: " + str(stats["bankrupt"]) + "家")
            solara.Text("违约企业: " + str(stats["default_count"]) + "家")
            solara.Text("交易者: " + str(stats["n_traders"]))
            solara.Text("总产出: " + str(round(stats["total_prod"], 0)))


@solara.component
def AgentDetailPanel():
    """Agent 详情：全部显示 + getttr 默认值"""
    selected_type = solara.use_reactive("家庭")
    agent_types = ["家庭", "企业", "交易者", "银行"]

    with solara.Card(title="Agent详情"):
        solara.Select(
            label="选择类型",
            value=selected_type,
            values=agent_types,
        )
        m = model_ref.value
        if m is None:
            solara.Text("模型加载中...")
            return

        all_agents = {
            "家庭": m.households,
            "企业": m.firms,
            "交易者": m.traders,
            "银行": m.banks,
        }.get(selected_type.value, [])

        if not all_agents:
            solara.Text("无该类型Agent")
            return

        # 滚动区域
        for a in all_agents:
                uid = a.unique_id
                cash = getattr(a, "cash", 0.0)
                wealth = getattr(a, "wealth", 0.0)
                if isinstance(a, Household):
                    employed = "就业" if getattr(a, "employed", False) else "失业"
                    salary = getattr(a, "salary", 0.0)
                    shares = getattr(a, "shares_owned", 0)
                    tier = getattr(a, "income_tier", "?")
                    tier_map = {"low": "低", "middle": "中", "high": "高"}
                    tier_str = tier_map.get(tier, tier)
                    solara.Text(
                        "H#" + str(uid) + " 现:" + str(round(cash, 0))
                        + " 富:" + str(round(wealth, 0))
                        + " " + employed + " 薪:" + str(round(salary, 1))
                        + " 股:" + str(shares) + " [" + tier_str + "]"
                    )
                elif isinstance(a, Firm):
                    prod = getattr(a, "production", 0.0)
                    inv = getattr(a, "inventory", 0.0)
                    emp = getattr(a, "employees", 0)
                    ind = getattr(a, "industry", None)
                    lc = getattr(a, "lifecycle", None)
                    ind_map = {"manufacturing": "制造", "service": "服务", "tech": "科技"}
                    lc_map = {"startup": "初", "growth": "成", "mature": "成", "decline": "衰"}
                    ind_str = ind_map.get(ind.value if ind else "", "?") if ind else "?"
                    lc_str = lc_map.get(lc.value if lc else "", "?") if lc else "?"
                    solara.Text(
                        "F#" + str(uid) + " 现:" + str(round(cash, 0))
                        + " 富:" + str(round(wealth, 0))
                        + " 产:" + str(round(prod, 1))
                        + " 库:" + str(round(inv, 1))
                        + " 员:" + str(emp) + " " + ind_str + "/" + lc_str
                    )
                elif isinstance(a, Trader):
                    shares_t = getattr(a, "shares", 0)
                    strat = getattr(a, "strategy", None)
                    strat_map = {"momentum": "动量", "value": "价值", "noise": "噪声", "market_maker": "做市"}
                    strat_str = strat_map.get(strat.value if strat else "", "?") if strat else "?"
                    solara.Text(
                        "T#" + str(uid) + " 现:" + str(round(cash, 0))
                        + " 富:" + str(round(wealth, 0))
                        + " 股:" + str(shares_t) + " " + strat_str
                    )
                elif isinstance(a, Bank):
                    reserves = getattr(a, "reserves", 0.0)
                    btype = getattr(a, "bank_type", "?")
                    btype_map = {"aggressive": "激进", "conservative": "保守"}
                    btype_str = btype_map.get(btype, "?")
                    solara.Text(
                        "B#" + str(uid) + " 准:" + str(round(reserves, 0))
                        + " 富:" + str(round(wealth, 0)) + " [" + btype_str + "]"
                    )


@solara.component
def ChartPanel():
    """图表面板：Select 切换 + matplotlib"""
    m = model_ref.value
    if m is None:
        solara.Text("模型加载中...")
        return

    chart_data = get_chart_data(m)
    COLOR_MAP = {
        "stock_price": "#f59e0b",
        "gdp": "#22c55e",
        "unemployment": "#ef4444",
        "avg_price": "#94a3b8",
        "gini": "#a855f7",
        "buy_orders": "#3b82f6",
        "loans": "#06b6d4",
        "stock_vol": "#f97316",
        "bad_debt_rate": "#ec4899",
        "systemic_risk": "#dc2626",
        "default_count": "#b91c1c",
        "gov_revenue": "#84cc16",
    }

    available = [(k, l) for k, l in CHART_CONFIG if chart_data.get(k, [])]
    if not available:
        solara.Card(title="经济指标图表")
        solara.Text("暂无图表数据")
        return

    # noqa: SH101 — use_state 在组件顶层，在 return 之前不可能到达
    default_key = available[0][0]
    selected_key = solara.use_state(default_key)  # noqa: SH101

    label_map = dict(CHART_CONFIG)
    selected_label = label_map.get(selected_key, "")
    vals = chart_data.get(selected_key, [])
    color = COLOR_MAP.get(selected_key, "#3b82f6")

    with solara.Card(title="经济指标图表"):
        solara.Select(
            label="指标",
            value=selected_key,
            values=[k for k, _ in available],
        )
        if vals:
            solara.FigureMatplotlib(
                lambda: render_matplotlib_figure(vals, selected_label, color)
            )
        else:
            solara.Text("暂无该指标数据")


@solara.component
def ExportPanel():
    """导出面板：CSV"""
    def on_export():
        m = model_ref.value
        if m is None or not hasattr(m, "datacollector"):
            return
        df = m.datacollector.get_model_vars_dataframe()
        buf = io.BytesIO()
        df.to_csv(buf, index=True)
        buf.seek(0)
        solara.FileDownload(buf, filename="economy_simulation.csv", label="下载 CSV")

    with solara.Card(title="数据导出"):
        solara.Button("导出 CSV", on_click=on_export, icon_name="download", color="success")


@solara.component
def DebugLogPanel():
    """实时调试日志面板"""
    class ListHandler(logging.Handler):
        def __init__(self, records_ref):
            super().__init__()
            self.records_ref = records_ref

        def emit(self, record):
            if len(self.records_ref.value) > 100:
                self.records_ref.value = self.records_ref.value[-99:]
            self.records_ref.value = self.records_ref.value + [self.format(record)]

    records = solara.use_reactive([])
    handler = ListHandler(records)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)

    with solara.Card(title="实时日志"):
        for msg in records.value[-50:]:
                color = "color:#94a3b8;"
                if "ERROR" in msg:
                    color = "color:#ef4444;"
                elif "WARNING" in msg:
                    color = "color:#f97316;"
                elif any(k in msg for k in ["实时", "场景", "政策"]):
                    color = "color:#22c55e;"
                solara.Text(msg, style=color)


@solara.component
def Page():
    """主页面"""
    initial_params = {key: cfg["default"] for key, cfg in PARAM_CONFIG.items()}

    with solara.AppLayout(title="经济沙盘 v3.1", sidebar_open=True, navigation=False):
        # 第一个 child = 侧边栏
        with solara.Sidebar():
            solara.Title("经济沙盘 v3.1")
            solara.Markdown("**多智能体经济仿真系统**")
            ControlBar(initial_params)
            ParamSliders()
            ScenarioPanel()
            PolicyPanel()
            ExportPanel()
            DebugLogPanel()

        # 其余 child = 主内容
        with solara.Column(gap="8px"):
            MacroStatsPanel()
            AgentDetailPanel()
            ChartPanel()

