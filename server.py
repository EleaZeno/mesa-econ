"""
Mesa 经济沙盘 - 可视化服务器 (Mesa 3.x)
运行: solara run server.py
访问: http://127.0.0.1:8521
"""

from __future__ import annotations

import base64
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
# 全局配置
# ─────────────────────────────────────────────

PLAY_INTERVAL_MS = 500
APP_NAME = "经济沙盘"

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

# 图表配置（中文名映射）
CHART_CONFIG = {
    "stock_price": "股价指数",
    "gdp": "GDP产出",
    "unemployment": "失业率(%)",
    "price_index": "物价指数",
    "gini": "基尼系数",
    "buy_orders": "买入订单",
    "loans": "贷款余额",
    "stock_volatility": "股价波动率",
    "bad_debt_rate": "银行坏账率",
    "default_count": "违约企业数",
}

# 场景预设
SCENARIOS = {
    "默认": {},
    "经济危机": {
        "n_households": 30,
        "n_firms": 5,
        "base_interest_rate": 0.15,
        "tax_rate": 0.25,
    },
    "宽松政策": {
        "base_interest_rate": 0.01,
        "tax_rate": 0.05,
        "subsidy": 15.0,
    },
    "高税收高福利": {
        "tax_rate": 0.40,
        "subsidy": 20.0,
        "min_wage": 15.0,
    },
    "自由市场": {
        "tax_rate": 0.05,
        "base_interest_rate": 0.02,
        "subsidy": 0.0,
        "min_wage": 0.0,
    },
}

# 模型参数
_PARAM_DEFAULTS = {
    "n_households":  {"min": 5,  "max": 80,  "step": 5,   "default": 20, "label": "家庭数量"},
    "n_firms":       {"min": 3,  "max": 40,  "step": 1,   "default": 10, "label": "企业数量"},
    "n_traders":     {"min": 5,  "max": 80,  "step": 5,   "default": 20, "label": "交易者数量"},
    "tax_rate":      {"min": 0.0,"max": 0.45,"step": 0.01,"default": 0.15,"label": "所得税率", "fmt": "0%"},
    "base_interest_rate": {"min": 0.0,"max": 0.25,"step": 0.01,"default": 0.05,"label": "基准利率", "fmt": "0%"},
    "min_wage":      {"min": 0.0,"max": 20.0, "step": 0.5, "default": 7.0,"label": "最低工资"},
    "productivity":  {"min": 0.1,"max": 3.0,  "step": 0.1, "default": 1.0,"label": "生产率"},
    "subsidy":       {"min": 0.0,"max": 20.0, "step": 0.5, "default": 0.0,"label": "失业补贴"},
}


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def get_agent_groups(model: Model) -> tuple[list[Firm], list[Household], list[Trader]]:
    """从模型中分类获取所有主体"""
    firms = [a for a in model.agents if isinstance(a, Firm)]
    households = [a for a in model.agents if isinstance(a, Household)]
    traders = [a for a in model.agents if isinstance(a, Trader)]
    return firms, households, traders


def build_macro_stats(model: Model) -> dict[str, Any]:
    """计算宏观统计数据"""
    firms, households, traders = get_agent_groups(model)
    n_hh = len(households)
    employed = sum(1 for h in households if h.employed)
    emp_rate = (employed / n_hh * 100) if n_hh > 0 else 0.0

    return {
        "周期": getattr(model, "cycle", 0),
        "企业数": len(firms),
        "就业人数": employed,
        "家庭数": n_hh,
        "就业率": f"{emp_rate:.1f}%",
        "失业人数": n_hh - employed if n_hh > 0 else 0,
        "交易者": len(traders),
        "总产出": f"{sum(getattr(f, 'production', 0) for f in firms):.1f}",
        "股息": f"{getattr(model, 'total_dividends', 0.0):.1f}",
        "税率": f"{getattr(model, 'tax_rate', 0):.0%}",
        "利率": f"{getattr(model, 'base_interest_rate', 0):.0%}",
        "物价": f"{getattr(model, 'price_index', 0):.2f}",
        "财政收入": f"{getattr(model, 'govt_revenue', 0):.1f}",
        "贷款余额": f"{getattr(model, 'total_loans_outstanding', 0):.1f}",
        # 金融风险
        "波动率": f"{getattr(model, 'stock_volatility', 0):.3f}",
        "违约数": getattr(model, "default_count", 0),
        "坏账率": f"{getattr(model, 'bank_bad_debt_rate', 0):.1%}",
    }


def build_model_params() -> dict[str, dict]:
    """构建参数字典"""
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
        if cfg.get("fmt"):
            params[name]["format"] = cfg["fmt"]
    return params


def create_charts(config: dict[str, str]) -> list:
    """创建图表组件"""
    charts = []
    for metric_key, metric_name in config.items():
        try:
            charts.append(make_plot_component(metric_key, backend="matplotlib"))
        except Exception as e:
            logger.error("图表创建失败 '%s': %s", metric_key, e)
    return charts


def get_cycle_stage(model: Model) -> tuple[str, str]:
    """识别经济周期阶段"""
    gdp = getattr(model, "gdp", 0)
    unemp = getattr(model, "unemployment", 0)
    
    if gdp > 2000 and unemp < 0.1:
        return "繁荣", "#22c55e"
    elif gdp > 1500 and unemp < 0.15:
        return "复苏", "#84cc16"
    elif gdp > 1000:
        return "平稳", "#eab308"
    elif unemp > 0.25:
        return "萧条", "#ef4444"
    else:
        return "衰退", "#f97316"


# ─────────────────────────────────────────────
# Solara 组件
# ─────────────────────────────────────────────

@solara.component
def MacroStats(model: Model):
    """宏观统计面板"""
    stats = build_macro_stats(model)
    stage, stage_color = get_cycle_stage(model)

    html = (
        f"<div style='{STYLE_CONTAINER}'>"
        f"  <div style='{STYLE_HEADER}'>📊 宏观快照 — 第 {stats['周期']} 轮 "
        f"    <span style='background:{stage_color};padding:2px 8px;border-radius:4px;margin-left:10px;'>{stage}</span>"
        f"  </div>"
        f"  <div style='{STYLE_GRID}'>"
        f"    <div>🏭 企业: {stats['企业数']}</div>"
        f"    <div>👥 就业: {stats['就业人数']}/{stats['家庭数']} ({stats['就业率']})</div>"
        f"    <div>📉 失业: {stats['失业人数']}</div>"
        f"    <div>📈 交易者: {stats['交易者']}</div>"
        f"    <div>📦 产出: {stats['总产出']}</div>"
        f"    <div>💰 股息: {stats['股息']}</div>"
        f"    <div>📋 税率: {stats['税率']}</div>"
        f"    <div>💵 利率: {stats['利率']}</div>"
        f"    <div>🏷️ 物价: {stats['物价']}</div>"
        f"    <div>🏛️ 财政: {stats['财政收入']}</div>"
        f"    <div>💳 贷款: {stats['贷款余额']}</div>"
        f"  </div>"
        f"  <div style='border-top:1px solid #334155;margin-top:8px;padding-top:8px;display:grid;grid-template-columns:1fr 1fr;gap:4px 24px;font-size:13px;'>"
        f"    <div>📊 波动率: {stats['波动率']}</div>"
        f"    <div>⚠️ 违约数: {stats['违约数']}</div>"
        f"    <div>🏦 坏账率: {stats['坏账率']}</div>"
        f"  </div>"
        f"</div>"
    )
    solara.HTML(tag="div", unsafe_innerHTML=html)


@solara.component
def PolicyPanel(model: Model):
    """政策干预面板"""
    
    def on_cut_rate():
        model.adjust_interest_rate(-0.005)
        logger.info("政策: 降息50BP → %.1f%%", model.base_interest_rate * 100)
    
    def on_hike_rate():
        model.adjust_interest_rate(0.005)
        logger.info("政策: 加息50BP → %.1f%%", model.base_interest_rate * 100)
    
    def on_cut_tax():
        model.adjust_tax_rate(-0.05)
        logger.info("政策: 减税5%% → %.1f%%", model.tax_rate * 100)
    
    def on_hike_tax():
        model.adjust_tax_rate(0.05)
        logger.info("政策: 加税5%% → %.1f%%", model.tax_rate * 100)
    
    def on_add_subsidy():
        model.adjust_subsidy(5.0)
        logger.info("政策: 增发补贴+5 → %.1f", model.subsidy)
    
    def on_cut_subsidy():
        model.adjust_subsidy(-5.0)
        logger.info("政策: 削减补贴-5 → %.1f", model.subsidy)
    
    with solara.Card("🎛️ 政策工具", margin=0):
        with solara.Row(gap="8px"):
            solara.Button("降息50BP", on_click=on_cut_rate, color="primary")
            solara.Button("加息50BP", on_click=on_hike_rate, color="primary")
        with solara.Row(gap="8px"):
            solara.Button("减税5%", on_click=on_cut_tax, color="success")
            solara.Button("加税5%", on_click=on_hike_tax, color="error")
        with solara.Row(gap="8px"):
            solara.Button("增发补贴+5", on_click=on_add_subsidy, color="warning")
            solara.Button("削减补贴-5", on_click=on_cut_subsidy, color="warning")


@solara.component
def ScenarioPanel(model: Model):
    """场景预设面板"""
    selected = solara.use_reactive("默认")
    
    def apply_scenario():
        scenario = SCENARIOS.get(selected.value, {})
        for key, value in scenario.items():
            if hasattr(model, key):
                setattr(model, key, value)
        logger.info("应用场景: %s → %s", selected.value, scenario)
    
    with solara.Card("🎬 场景预设", margin=0):
        solara.Select(
            label="选择场景",
            value=selected,
            values=list(SCENARIOS.keys()),
        )
        solara.Button("应用场景", on_click=apply_scenario, color="primary")


@solara.component
def ExportPanel(model: Model):
    """数据导出面板"""
    
    def get_csv_bytes():
        """生成CSV二进制数据"""
        df_model = model.datacollector.get_model_vars_dataframe()
        df_agents = model.datacollector.get_agent_vars_dataframe()
        
        buffer = io.StringIO()
        buffer.write("# 模型级指标\n")
        df_model.to_csv(buffer)
        buffer.write("\n# Agent级数据\n")
        df_agents.to_csv(buffer)
        
        return buffer.getvalue().encode("utf-8")
    
    csv_bytes, set_csv_bytes = solara.use_state(lambda: b"")
    filename = f"econ_cycle{getattr(model, 'cycle', 0)}.csv"
    
    def on_export():
        set_csv_bytes(get_csv_bytes())
        logger.info("导出成功，%d 字节", len(csv_bytes))
    
    with solara.Card("📥 数据导出", margin=0):
        with solara.Row():
            solara.Button("导出CSV", on_click=on_export, color="primary", icon_name="mdi-download")
            solara.Text(f"周期: {getattr(model, 'cycle', 0)}")
        
        if csv_bytes:
            solara.FileDownload(
                data=csv_bytes,
                filename=filename,
                label="📥 点击下载 CSV",
            )


@solara.component
def DebugLogPanel(model: Model):
    """实时调试日志面板"""
    log_lines = solara.use_reactive(list[str])
    max_lines = 50
    
    def add_log(msg: str):
        lines = log_lines.value
        lines.append(msg)
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        log_lines.set(lines)
    
    # 采样部分 Agent 行为
    firms = [a for a in model.agents if isinstance(a, Firm)][:2]
    for f in firms:
        prod = getattr(f, "production", 0)
        inv = getattr(f, "inventory", 0)
        loan = getattr(f, "loan_principal", 0)
        dp = getattr(f, "default_probability", 0)
        if prod > 0 or loan > 0:
            add_log(f"企业{f.unique_id}: 产出{prod:.1f} 库存{inv:.1f} 负债{loan:.1f} 违约风险{dp:.1%}")
    
    households = [a for a in model.agents if isinstance(a, Household)][:2]
    for h in households:
        employed = "就业" if getattr(h, "employed", False) else "失业"
        cash = getattr(h, "cash", 0)
        add_log(f"家庭{h.unique_id}: {employed} 现金{cash:.1f}")
    
    banks = [a for a in model.agents if isinstance(a, Bank)]
    for b in banks:
        reserves = getattr(b, "reserves", 0)
        bad = getattr(b, "bad_debts", 0)
        add_log(f"银行{b.unique_id}: 储备{reserves:.1f} 坏账{bad:.1f}")
    
    # 风险告警
    vol = getattr(model, "stock_volatility", 0)
    defaults = getattr(model, "default_count", 0)
    bdr = getattr(model, "bank_bad_debt_rate", 0)
    
    alert_style = "color:#ef4444;font-weight:bold;" if defaults > 0 else "color:#e2e8f0;"
    
    html = (
        f"<div style='background:#0f172a;border-radius:8px;padding:12px;font-family:Consolas,monospace;font-size:12px;max-height:300px;overflow-y:auto;'>"
        f"<div style='margin-bottom:8px;color:#94a3b8;'>🐛 调试日志 (实时采样)</div>"
    )
    for line in log_lines.value:
        html += f"<div style='margin:2px 0;'>{line}</div>"
    
    # 风险告警区
    html += f"<div style='border-top:1px solid #334155;margin-top:8px;padding-top:8px;{alert_style}'>"
    if vol > 0.3:
        html += f"⚠️ 股价波动剧烈: {vol:.3f}<br>"
    if defaults > 0:
        html += f"🚨 企业违约: {defaults}家<br>"
    if bdr > 0.1:
        html += f"🏦 银行坏账率警告: {bdr:.1%}<br>"
    if defaults == 0 and vol <= 0.3 and bdr <= 0.1:
        html += "✅ 系统运行正常"
    html += "</div></div>"
    
    solara.HTML(tag="div", unsafe_innerHTML=html)


@solara.component
def AgentDetailPanel(model: Model):
    """Agent详情面板"""
    selected_type = solara.use_reactive("家庭")
    agent_types = ["家庭", "企业", "交易者", "银行"]
    
    type_map = {"家庭": Household, "企业": Firm, "交易者": Trader, "银行": Bank}
    
    agents = [a for a in model.agents if isinstance(a, type_map.get(selected_type.value, Household))]
    
    with solara.Card(f"👤 Agent详情 ({selected_type.value}: {len(agents)}个)", margin=0):
        solara.Select(
            label="选择类型",
            value=selected_type,
            values=agent_types,
        )
        
        if agents:
            # 显示前5个agent的关键属性
            for i, agent in enumerate(agents[:5]):
                props = {
                    "cash": getattr(agent, "cash", "N/A"),
                    "wealth": getattr(agent, "wealth", "N/A"),
                    "employed": getattr(agent, "employed", "N/A"),
                }
                solara.Text(f"#{i+1} 现金:{props['cash']} 财富:{props['wealth']} 就业:{props['employed']}")


@solara.component
def PerformancePanel(model: Model):
    """性能监控面板"""
    import time
    import psutil
    import os
    
    process = psutil.Process(os.getpid())
    mem_mb = process.memory_info().rss / 1024 / 1024
    n_agents = len(list(model.agents))
    
    with solara.Card("⚡ 性能监控", margin=0):
        with solara.Row():
            solara.Text(f"Agent数: {n_agents}")
            solara.Text(f"内存: {mem_mb:.1f}MB")
        
        if n_agents > 80:
            solara.Warning("Agent数量较多，模拟可能变慢")


# ─────────────────────────────────────────────
# 初始化
# ─────────────────────────────────────────────

logger.info("创建图表组件...")
chart_components = create_charts(CHART_CONFIG)

logger.info("构建模型参数...")
model_params = build_model_params()

logger.info("初始化模型...")
try:
    model = EconomyModel()
except Exception as e:
    logger.critical("模型初始化失败: %s", e)
    raise

logger.info("启动 SolaraViz: http://127.0.0.1:8521 ...")
page = SolaraViz(
    model,
    renderer=None,
    components=[
        MacroStats,
        PolicyPanel,
        ScenarioPanel,
        ExportPanel,
        AgentDetailPanel,
        PerformancePanel,
        *chart_components,
    ],
    model_params=model_params,
    name=APP_NAME,
    play_interval=PLAY_INTERVAL_MS,
)

page
