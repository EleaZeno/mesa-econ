"""
Mesa Economic Sandbox - Web Server (Mesa 3.x)
Run: solara run server.py
Open: http://127.0.0.1:8521
"""

from __future__ import annotations

import io
import logging
from typing import Any

import solara
from mesa import Model
from mesa.visualization import SolaraViz
from mesa.visualization import make_plot_component

from model import EconomyModel, Household, Firm, Bank, Trader

# ─────────────────────────────────────────────
# 日志配置
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 全局配置（消除硬编码）
# ─────────────────────────────────────────────

# 可视化配置
PLAY_INTERVAL_MS = 500  # 500ms 更稳定，避免计算卡顿
APP_NAME = "Economic Sandbox"

# 样式常量
STYLE_CONTAINER = (
    "background:linear-gradient(135deg,#0f172a,#1e3a5f);"
    "color:#e2e8f0;border-radius:12px;padding:18px 20px;"
    "font-family:Segoe UI,sans-serif;box-shadow:0 4px 20px rgba(0,0,0,0.3);"
    "line-height:2;"
)
STYLE_HEADER = (
    "font-size:16px;font-weight:700;margin-bottom:10px;"
    "border-bottom:1px solid #334155;padding-bottom:8px;"
)
STYLE_GRID = (
    "display:grid;grid-template-columns:1fr 1fr;gap:6px 24px;font-size:13px;"
)

# 图表列表（新增/删除图表只需改这里）
CHART_NAMES = (
    "stock_price",
    "gdp",
    "unemployment",
    "price_index",
    "gini",
    "buy_orders",
    "loans",
)

# 模型参数默认值
_PARAM_DEFAULTS = {
    "n_households":  {"min": 5,  "max": 80,  "step": 5,   "default": 20, "label": "Consumers",           "fmt": None},
    "n_firms":       {"min": 3,  "max": 40,  "step": 1,   "default": 10, "label": "Firms",               "fmt": None},
    "n_traders":     {"min": 5,  "max": 80,  "step": 5,   "default": 20, "label": "Traders",             "fmt": None},
    "tax_rate":      {"min": 0.0,"max": 0.45,"step": 0.01,"default": 0.15,"label": "Income Tax Rate",    "fmt": "0%"},
    "base_interest_rate": {"min": 0.0,"max": 0.25,"step": 0.01,"default": 0.05,"label": "Base Interest Rate","fmt": "0%"},
    "min_wage":      {"min": 0.0,"max": 20.0, "step": 0.5, "default": 7.0,"label": "Minimum Wage",         "fmt": None},
    "productivity":  {"min": 0.1,"max": 3.0,  "step": 0.1, "default": 1.0,"label": "Productivity",         "fmt": None},
    "subsidy":       {"min": 0.0,"max": 20.0, "step": 0.5, "default": 0.0,"label": "Unemployment Subsidy", "fmt": None},
}


# ─────────────────────────────────────────────
# 工具函数（逻辑复用 + 边界处理）
# ─────────────────────────────────────────────

def get_agent_groups(model: Model) -> tuple[list[Firm], list[Household], list[Trader]]:
    """从模型中分类获取所有主体"""
    firms = [a for a in model.agents if isinstance(a, Firm)]
    households = [a for a in model.agents if isinstance(a, Household)]
    traders = [a for a in model.agents if isinstance(a, Trader)]
    return firms, households, traders


def build_macro_stats(model: Model) -> dict[str, Any]:
    """
    计算宏观统计数据，分离业务计算与可视化。
    边界处理：households 为空时避免除零，属性缺失时返回默认值。
    """
    firms, households, traders = get_agent_groups(model)
    n_hh = len(households)
    employed = sum(1 for h in households if h.employed)
    emp_rate = (employed / n_hh * 100) if n_hh > 0 else 0.0

    return {
        "cycle":         getattr(model, "cycle", 0),
        "n_firms":       len(firms),
        "employed":      employed,
        "n_households":  n_hh,
        "emp_rate":      f"{emp_rate:.0f}%",
        "n_unemployed":  n_hh - employed if n_hh > 0 else 0,
        "n_traders":     len(traders),
        "total_prod":    sum(getattr(f, "production", 0) for f in firms),
        "total_div":     getattr(model, "total_dividends", 0.0),
        "tax_rate":      f"{getattr(model, 'tax_rate', 0):.0%}",
        "interest_rate": f"{getattr(model, 'base_interest_rate', 0):.0%}",
        "price_index":   f"{getattr(model, 'price_index', 0):.2f}",
        "govt_rev":      f"{getattr(model, 'govt_revenue', 0):.1f}",
        "loans":         f"{getattr(model, 'total_loans_outstanding', 0):.1f}",
    }


def build_model_params() -> dict[str, dict]:
    """根据 _PARAM_DEFAULTS 构建 SolaraViz 参数字典"""
    params = {}
    for name, cfg in _PARAM_DEFAULTS.items():
        is_int = isinstance(cfg["step"], int) or cfg["step"] == int(cfg["step"])
        params[name] = {
            "type":  "SliderInt" if is_int else "SliderFloat",
            "value": cfg["default"],
            "min":   cfg["min"],
            "max":   cfg["max"],
            "step":  cfg["step"],
            "label": cfg["label"],
        }
        if cfg["fmt"]:
            params[name]["format"] = cfg["fmt"]
    return params


def create_charts(names: tuple[str, ...]) -> list:
    """
    批量创建图表组件，消除重复代码。
    创建失败时降级为占位组件，避免页面崩溃。
    """
    charts = []
    for name in names:
        try:
            charts.append(make_plot_component(name, backend="matplotlib"))
        except Exception as e:
            logger.error("Chart '%s' creation failed: %s", name, e)
            # 降级占位组件
            @solara.component
            def fallback(m: Model, n=name, err=str(e)):
                solara.Text(f"Chart '{n}' unavailable — {err[:40]}")
            charts.append(fallback)
    return charts


# ─────────────────────────────────────────────
# Solara 组件
# ─────────────────────────────────────────────

@solara.component
def MacroStats(model: Model):
    """实时宏观统计面板"""
    stats = build_macro_stats(model)

    html = (
        f"<div style='{STYLE_CONTAINER}'>"
        f"  <div style='{STYLE_HEADER}'>Macro Snapshot — Cycle {stats['cycle']}</div>"
        f"  <div style='{STYLE_GRID}'>"
        f"    <div>Firms: {stats['n_firms']}</div>"
        f"    <div>Employment: {stats['employed']}/{stats['n_households']} ({stats['emp_rate']})</div>"
        f"    <div>Unemployed: {stats['n_unemployed']}</div>"
        f"    <div>Traders: {stats['n_traders']}</div>"
        f"    <div>Output: {stats['total_prod']:.1f}</div>"
        f"    <div>Dividends: {stats['total_div']:.1f}</div>"
        f"    <div>Tax Rate: {stats['tax_rate']}</div>"
        f"    <div>Interest Rate: {stats['interest_rate']}</div>"
        f"    <div>Price Index: {stats['price_index']}</div>"
        f"    <div>Gov Revenue: {stats['govt_rev']}</div>"
        f"    <div>Loans: {stats['loans']}</div>"
        f"  </div>"
        f"</div>"
    )
    solara.HTML(tag="div", unsafe_innerHTML=html)


@solara.component
def PolicyPanel(model: Model):
    """政策干预面板：降息/加息、减税/加税、调整补贴"""
    
    def on_cut_rate():
        model.adjust_interest_rate(-0.005)  # 降息 50BP
        logger.info("Policy: Rate cut 50BP → %.1f%%", model.base_interest_rate * 100)
    
    def on_hike_rate():
        model.adjust_interest_rate(0.005)  # 加息 50BP
        logger.info("Policy: Rate hike 50BP → %.1f%%", model.base_interest_rate * 100)
    
    def on_cut_tax():
        model.adjust_tax_rate(-0.05)  # 减税 5%
        logger.info("Policy: Tax cut 5%% → %.1f%%", model.tax_rate * 100)
    
    def on_hike_tax():
        model.adjust_tax_rate(0.05)  # 加税 5%
        logger.info("Policy: Tax hike 5%% → %.1f%%", model.tax_rate * 100)
    
    def on_add_subsidy():
        model.adjust_subsidy(5.0)  # 增发补贴
        logger.info("Policy: Subsidy +5 → %.1f", model.subsidy)
    
    def on_cut_subsidy():
        model.adjust_subsidy(-5.0)  # 削减补贴
        logger.info("Policy: Subsidy -5 → %.1f", model.subsidy)
    
    with solara.Row(gap="8px"):
        solara.Button("Cut Rate 50BP", on_click=on_cut_rate, color="primary")
        solara.Button("Hike Rate 50BP", on_click=on_hike_rate, color="primary")
        solara.Button("Cut Tax 5%", on_click=on_cut_tax, color="success")
        solara.Button("Hike Tax 5%", on_click=on_hike_tax, color="error")
        solara.Button("+Subsidy 5", on_click=on_add_subsidy, color="warning")
        solara.Button("-Subsidy 5", on_click=on_cut_subsidy, color="warning")


@solara.component
def ExportPanel(model: Model):
    """数据导出面板"""
    
    def export_csv():
        """导出模型数据为 CSV"""
        try:
            df_model = model.datacollector.get_model_vars_dataframe()
            df_agents = model.datacollector.get_agent_vars_dataframe()
            
            # 合并到一个 StringIO
            buffer = io.StringIO()
            buffer.write("# Model-level metrics\n")
            df_model.to_csv(buffer)
            buffer.write("\n# Agent-level data\n")
            df_agents.to_csv(buffer)
            
            logger.info("Exported %d model rows, %d agent rows", len(df_model), len(df_agents))
            # Solara 的文件下载需要通过 solara.FileDownload 组件
            # 这里先只打印日志，实际下载需要额外处理
        except Exception as e:
            logger.error("Export failed: %s", e)
    
    with solara.Row():
        solara.Button("Export CSV", on_click=export_csv, color="primary", icon_name="mdi-download")
        solara.Text(f"Cycle: {getattr(model, 'cycle', 0)}")


# ─────────────────────────────────────────────
# 初始化
# ─────────────────────────────────────────────

logger.info("Building chart components...")
chart_components = create_charts(CHART_NAMES)

logger.info("Building model parameters...")
model_params = build_model_params()

logger.info("Initializing model...")
try:
    model = EconomyModel()
except Exception as e:
    logger.critical("Model initialization failed: %s", e)
    raise

logger.info("Starting SolaraViz on http://127.0.0.1:8521 ...")
page = SolaraViz(
    model,
    renderer=None,
    components=[
        MacroStats,
        PolicyPanel,
        ExportPanel,
        *chart_components,
    ],
    model_params=model_params,
    name=APP_NAME,
    play_interval=PLAY_INTERVAL_MS,
)

page
