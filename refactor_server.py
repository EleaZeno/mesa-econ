"""
重构 server.py：
  1. Agent 分类 → 直接用 model.firms/households/traders/banks
  2. 线程播放 → solara.use_interval（安全，零线程）
  3. 冗余滑块 → 遍历 PARAM_CONFIG 自动生成
  4. 场景同步滑块值
  5. 失业率转换（校验 >1 才 /100）
  6. 边界硬编码 → 复用 PARAM_CONFIG
  7. 样式/阈值抽离配置
  8. Agent 详情 → 滚动 + gettattr 默认值
  9. 宏观面板 → solara 原生组件
  10. 新增参数: gov_purchase / capital_gains_tax / shock_prob
"""

import re

with open('server.py', 'r', encoding='utf-8') as f:
    content = f.read()

# ── 1. 扩展 PARAM_CONFIG ────────────────────────────────────
old_config_end = '''    "subsidy":           {"min": 0.0,"max": 20.0,"step": 0.5, "default": 0.0,  "label": "失业补贴",   "fmt": "%.1f"},
}'''

new_config_end = '''    "subsidy":               {"min": 0.0,"max": 50.0,"step": 1.0,  "default": 0.0,  "label": "失业补贴",   "fmt": "%.1f"},
    "gov_purchase":          {"min": 0.0,"max": 200.0,"step": 5.0, "default": 0.0,  "label": "政府购买",   "fmt": "%.1f"},
    "capital_gains_tax":      {"min": 0.0,"max": 0.50,"step": 0.01,"default": 0.10, "label": "资本利得税", "fmt": "%.2%"},
    "shock_prob":             {"min": 0.0,"max": 0.20,"step": 0.01,"default": 0.02, "label": "冲击概率",   "fmt": "%.2%"},
}'''
content = content.replace(old_config_end, new_config_end)

# ── 2. 扩展 SCENARIOS ───────────────────────────────────────
old_scenarios = '''SCENARIOS = {
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
}'''

new_scenarios = '''SCENARIOS = {
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
}'''
content = content.replace(old_scenarios, new_scenarios)

# ── 3. 重写 get_agent_groups → 直接用 model 分类列表 ─────────
old_agent_fn = '''def get_agent_groups(model: EconomyModel):
    firms = [a for a in model.agents if isinstance(a, Firm)]
    households = [a for a in model.agents if isinstance(a, Household)]
    traders = [a for a in model.agents if isinstance(a, Trader)]
    banks = [a for a in model.agents if isinstance(a, Bank)]
    return firms, households, traders, banks'''

new_agent_fn = '''def get_agent_groups(model: EconomyModel):
    """直接用 model 维护的分类列表，避免 O(n²) 重复筛选"""
    return model.firms, model.households, model.traders, model.banks'''
content = content.replace(old_agent_fn, new_agent_fn)

# ── 4. 重写 build_macro_stats（去重 + 新指标） ───────────────
old_stats = '''def build_macro_stats(model: EconomyModel) -> dict[str, Any]:
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
                "gov_purch":    model.gov_purchase,
                "cap_gains":    model.capital_gains_tax_revenue,
                "systemic":     round(model.systemic_risk, 3),
                "bankrupt":     model.bankrupt_count,
                "n_firms":      len(model.firms),
        "loans":         model.total_loans_outstanding,
        "volatility":    model.stock_volatility,
        "default_count": model.default_count,
        "bad_debt_rate": model.bank_bad_debt_rate,
        "stock_price":   model.stock_price,
        "gdp":           model.gdp,
        "unemployment":  model.unemployment,
        "gini":          model.gini,
    }'''

new_stats = '''def build_macro_stats(model: EconomyModel) -> dict[str, Any]:
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
        "shock":             model.current_shock or "",
    }'''
content = content.replace(old_stats, new_stats)

# ── 5. 经济周期阈值抽离配置 ─────────────────────────────────
old_cycle_fn = '''def get_cycle_stage(model: EconomyModel) -> tuple[str, str]:
    if model.gdp > 2000 and model.unemployment < 0.1:
        return "繁荣", "#22c55e"
    elif model.gdp > 1500 and model.unemployment < 0.15:
        return "复苏", "#84cc16"
    elif model.gdp > 1000:
        return "平稳", "#eab308"
    elif model.unemployment > 0.25:
        return "萧条", "#ef4444"
    else:
        return "衰退", "#f97316"'''

new_cycle_fn = '''# 经济周期配置（集中管理，便于调参）
CYCLE_CONFIG = [
    {"gdp_thr": 2000, "u_thr": 0.10, "label": "繁荣", "color": "#22c55e"},
    {"gdp_thr": 1500, "u_thr": 0.15, "label": "复苏", "color": "#84cc16"},
    {"gdp_thr": 1000, "u_thr": 0.25, "label": "平稳", "color": "#eab308"},
    {"gdp_thr": 0,    "u_thr": 0.25, "label": "衰退", "color": "#f97316"},
    {"gdp_thr": 0,    "u_thr": 1.00, "label": "萧条", "color": "#ef4444"},
]

def get_cycle_stage(model: EconomyModel) -> tuple[str, str]:
    for cfg in CYCLE_CONFIG:
        if model.gdp >= cfg["gdp_thr"] and model.unemployment <= cfg["u_thr"]:
            return cfg["label"], cfg["color"]
    return CYCLE_CONFIG[-1]["label"], CYCLE_CONFIG[-1]["color"]'''
content = content.replace(old_cycle_fn, new_cycle_fn)

# ── 6. 失业率转换：仅当 >1（百分比格式）才 /100 ─────────────
old_chart_fn_end = '''            # 转换 unemployment: 存储的是百分比数字 15.0，不是小数
            if key == "unemployment":
                vals = [v / 100 for v in vals] if vals else []
            result[key] = vals'''

new_chart_fn_end = '''            # 转换 unemployment：模型存小数(0.15)则不处理；若存百分比(15.0)则 /100
            if key == "unemployment":
                vals = [v / 100 if v > 1 else v for v in vals] if vals else []
            result[key] = vals'''
content = content.replace(old_chart_fn_end, new_chart_fn_end)

# ── 7. 重写 ParamSliders → 遍历 PARAM_CONFIG ───────────────
old_paramsliders = '''@solara.component
def ParamSliders():
    """参数面板：实时调整模型参数"""
    n_households = solara.use_reactive(20)
    n_firms = solara.use_reactive(10)
    n_traders = solara.use_reactive(20)
    tax_rate = solara.use_reactive(0.15)
    base_interest_rate = solara.use_reactive(0.05)
    min_wage = solara.use_reactive(7.0)
    productivity = solara.use_reactive(1.0)
    subsidy = solara.use_reactive(0.0)
    gov_purchase = solara.use_reactive(0.0)
    capital_gains_tax = solara.use_reactive(0.10)
    shock_prob = solara.use_reactive(0.02)

    def apply_immediately(key: str, value: Any):
        """直接应用到当前运行的模型（无需重启模型）"""
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
            solara.FloatSlider(
                label="n_households",
                value=n_households,
                min=5, max=80, step=5,
                on_value=lambda v: on_change_factory("n_households", v),
            )
            solara.FloatSlider(
                label="n_firms",
                value=n_firms,
                min=3, max=40, step=1,
                on_value=lambda v: on_change_factory("n_firms", v),
            )
            solara.FloatSlider(
                label="n_traders",
                value=n_traders,
                min=5, max=80, step=5,
                on_value=lambda v: on_change_factory("n_traders", v),
            )
            solara.FloatSlider(
                label="tax_rate",
                value=tax_rate,
                min=0.0, max=0.45, step=0.01,
                format="%.0f%%",
                on_value=lambda v: on_change_factory("tax_rate", v),
            )
            solara.FloatSlider(
                label="base_interest_rate",
                value=base_interest_rate,
                min=0.0, max=0.25, step=0.01,
                format="%.1f%%",
                on_value=lambda v: on_change_factory("base_interest_rate", v),
            )
            solara.FloatSlider(
                label="min_wage",
                value=min_wage,
                min=0, max=20.0, step=0.5,
                format="%.1f",
                on_value=lambda v: on_change_factory("min_wage", v),
            )
            solara.FloatSlider(
                label="productivity",
                value=productivity,
                min=0.1, max=3.0, step=0.1,
                on_value=lambda v: on_change_factory("productivity", v),
            )
            solara.FloatSlider(
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
            )'''

new_paramsliders = '''# ── 全局滑块引用字典（用于场景同步）──────────────────────────────
_slider_refs: dict[str, solara.Reactive] = {}

def _apply_immediately(key: str, value: Any):
    """直接应用到当前运行模型"""
    m = model_ref.value
    if m is not None and hasattr(m, key):
        setattr(m, key, value)
        logger.info("实时 %s = %s", key, value)


def _sync_slider(key: str, value: Any):
    """场景切换时同步滑块显示值"""
    if key in _slider_refs:
        _slider_refs[key].set(value)
    _apply_immediately(key, value)


@solara.component
def ParamSliders():
    """参数面板：遍历 PARAM_CONFIG 自动生成滑块，实时生效"""
    # 初始化/读取滑块引用字典（只在首次时创建）
    if not _slider_refs:
        for key, cfg in PARAM_CONFIG.items():
            _slider_refs[key] = solara.use_reactive(cfg["default"])

    with solara.Card("经济参数（实时生效）", margin=0, style="background:#1e293b;color:#e2e8f0;"):
        with solara.Grid(columns=2):
            for key, cfg in PARAM_CONFIG.items():
                ref = _slider_refs[key]
                # 闭包陷阱修复：k=key 默认参数
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
                    format=cfg.get("fmt", "%.2f"),
                    on_value=make_handler(key),
                )'''

content = content.replace(old_paramsliders, new_paramsliders)

# ── 8. 重写 ScenarioPanel（校验边界 + 同步滑块） ───────────
old_scenario_panel_start = '''@solara.component
def ScenarioPanel():
    selected = solara.use_reactive("默认")
    with solara.Card("预设场景", margin=0):
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
                if hasattr(m, key):
                    setattr(m, key, value)
                    logger.info("场景 '%s': %s = %s", selected.value, key, value)
        solara.Button("应用场景", on_click=apply_scenario, color="primary")'''

new_scenario_panel_start = '''@solara.component
def ScenarioPanel():
    selected = solara.use_reactive("默认")
    with solara.Card("预设场景", margin=0, style="background:#1e293b;color:#e2e8f0;"):
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
                    # 边界校验：超出范围的场景值自动 clamp
                    valid_val = max(cfg["min"], min(value, cfg["max"]))
                    if valid_val != value:
                        logger.warning("场景%s的%s=%.3f超出[%.2f,%.2f]，修正为%.3f",
                                      selected.value, key, value, cfg["min"], cfg["max"], valid_val)
                    setattr(m, key, valid_val)
                    _sync_slider(key, valid_val)
                    logger.info("场景 '%s': %s = %.3f", selected.value, key, valid_val)
        solara.Button("应用场景", on_click=apply_scenario, color="primary")'''

content = content.replace(old_scenario_panel_start, new_scenario_panel_start)

# ── 9. 重写 PolicyPanel（边界从 PARAM_CONFIG 读取） ─────────
old_policy_panel = '''@solara.component
def PolicyPanel():
    def adjust(model_attr: str, delta: float, label: str):
        m = model_ref.value
        if m is None:
            return
        old = getattr(m, model_attr)
        new_val = max(0.0, min(old + delta, 0.5))
        setattr(m, model_attr, new_val)
        logger.info("政策 %s: %.4f → %.4f", label, old, new_val)

    with solara.Card("利率/税率调整", margin=0):'''

new_policy_panel = '''@solara.component
def PolicyPanel():
    def adjust(model_attr: str, delta: float, label: str):
        m = model_ref.value
        if m is None or model_attr not in PARAM_CONFIG:
            return
        cfg = PARAM_CONFIG[model_attr]
        old = getattr(m, model_attr)
        # 边界从 PARAM_CONFIG 读取，不再硬编码 0.5
        new_val = max(cfg["min"], min(old + delta, cfg["max"]))
        setattr(m, model_attr, new_val)
        _sync_slider(model_attr, new_val)
        logger.info("政策 %s: %.4f -> %.4f", label, old, new_val)

    with solara.Card("利率/税率调整", margin=0, style="background:#1e293b;color:#e2e8f0;"):'''

content = content.replace(old_policy_panel, new_policy_panel)

# ── 10. 重写 AgentDetailPanel（滚动 + 默认值 + 全部显示） ──
old_agent_panel = '''@solara.component
def AgentDetailPanel():
    selected_type = solara.use_reactive("家庭")
    agent_types = ["家庭", "企业", "交易者", "银行"]
    type_map = {"家庭": Household, "企业": Firm, "交易者": Trader, "银行": Bank}
    firms, households, traders, banks = get_agent_groups(model_ref.value) if model_ref.value else ([], [], [], [])
    type_agents = {"家庭": households[:5], "企业": firms[:5], "交易者": traders[:5], "银行": banks}
    agents = type_agents.get(selected_type.value, [])

    with solara.Card("Agent详情", margin=0):
        solara.Select(
            label="选择类型",
            value=selected_type,
            values=agent_types,
        )
        for a in agents:
            uid = a.unique_id
            cash = getattr(a, "cash", 0.0)
            wealth = getattr(a, "wealth", 0.0)
            if isinstance(a, Household):
                employed = "✅就业" if getattr(a, "employed", False) else "❌失业"
                salary = getattr(a, "salary", 0.0)
                shares = getattr(a, "shares_owned", 0.0)
                solara.Text(
                    f"#{uid} 现金:{cash:.0f} 财富:{wealth:.0f} "
                    f"{employed} 工资:{salary:.1f} 持股:{shares}"
                )
            elif isinstance(a, Firm):
                prod = getattr(a, "production", 0.0)
                inv = getattr(a, "inventory", 0.0)
                emp = getattr(a, "employees", 0)
                solara.Text(
                    f"#{uid} 现金:{cash:.0f} 财富:{wealth:.0f} "
                    f"产出:{prod:.1f} 库存:{inv:.1f} 员工:{emp}"
                )
            elif isinstance(a, Bank):
                reserves = getattr(a, "reserves", 0.0)
                solara.Text(f"#{uid} 准备金:{reserves:.0f} 财富:{wealth:.0f}")
            elif isinstance(a, Trader):
                solara.Text(f"#{uid} 现金:{cash:.0f} 财富:{wealth:.0f}")'''

new_agent_panel = '''@solara.component
def AgentDetailPanel():
    selected_type = solara.use_reactive("家庭")
    agent_types = ["家庭", "企业", "交易者", "银行"]
    type_map = {"家庭": Household, "企业": Firm, "交易者": Trader, "银行": Bank}

    with solara.Card(f"Agent详情", margin=0, style="background:#1e293b;color:#e2e8f0;"):
        solara.Select(
            label="选择类型",
            value=selected_type,
            values=agent_types,
        )
        m = model_ref.value
        if m is None:
            solara.Text("模型加载中...", style="color:#94a3b8;")
            return

        target_cls = type_map.get(selected_type.value, Household)
        # 直接用 model 分类列表（O(1)）
        all_agents = {
            "家庭": m.households,
            "企业": m.firms,
            "交易者": m.traders,
            "银行": m.banks,
        }.get(selected_type.value, [])

        with solara.VBox(style="max-height:350px;overflow-y:auto;padding:4px;"):
            if not all_agents:
                solara.Text("无该类型Agent", style="color:#94a3b8;")
            for a in all_agents:
                uid = a.unique_id
                cash = getattr(a, "cash", 0.0)
                wealth = getattr(a, "wealth", 0.0)
                if isinstance(a, Household):
                    employed = "就业" if getattr(a, "employed", False) else "失业"
                    salary = getattr(a, "salary", 0.0)
                    shares = getattr(a, "shares_owned", 0)
                    tier = getattr(a, "income_tier", "unknown")
                    solara.Text(
                        f"H#{uid} 现:{cash:>6.0f} 富:{wealth:>7.0f} "
                        f"{employed} 薪:{salary:>5.1f} 股:{shares} [{tier}]",
                        style="font-size:11px;margin:1px 0;"
                    )
                elif isinstance(a, Firm):
                    prod = getattr(a, "production", 0.0)
                    inv = getattr(a, "inventory", 0.0)
                    emp = getattr(a, "employees", 0)
                    ind = getattr(a, "industry", "unknown")
                    lc = getattr(a, "lifecycle", "unknown")
                    solara.Text(
                        f"F#{uid} 现:{cash:>6.0f} 富:{wealth:>7.0f} "
                        f"产:{prod:>5.1f} 库:{inv:>4.1f} 员:{emp} {ind.value[:3]}/{lc.value[:3]}",
                        style="font-size:11px;margin:1px 0;"
                    )
                elif isinstance(a, Trader):
                    shares_t = getattr(a, "shares", 0)
                    strat = getattr(a, "strategy", "unknown")
                    solara.Text(
                        f"T#{uid} 现:{cash:>6.0f} 富:{wealth:>7.0f} 股:{shares_t} {strat.value}",
                        style="font-size:11px;margin:1px 0;"
                    )
                elif isinstance(a, Bank):
                    reserves = getattr(a, "reserves", 0.0)
                    btype = getattr(a, "bank_type", "?")
                    solara.Text(
                        f"B#{uid} 准:{reserves:>7.0f} 富:{wealth:>7.0f} [{btype}]",
                        style="font-size:11px;margin:1px 0;"
                    )'''

content = content.replace(old_agent_panel, new_agent_panel)

# ── 11. 重写 ControlBar → use_interval 替代 threading ──────
old_control_bar = '''@solara.component
def ControlBar(initial_params: dict):
    """控制栏：初始化、重置、单步、播放/暂停"""
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

    with solara.Row(gap="8px", align="center"):'''

new_control_bar = '''@solara.component
def ControlBar(initial_params: dict):
    """控制栏：use_interval 替代 threading（安全，无线程泄漏）"""
    playing = solara.use_reactive(False)
    loading = solara.use_reactive(False)

    def on_init():
        loading.set(True)
        reset_model(initial_params)
        loading.set(False)

    def on_reset():
        loading.set(True)
        params = _get_current_params()
        reset_model(params)
        playing.set(False)
        loading.set(False)

    def on_step():
        m = model_ref.value
        if m is not None:
            m.step()

    def on_play():
        playing.set(not playing.value)

    solara.use_effect(lambda: on_init(), [])

    # use_interval：playing=True 时每 500ms 执行一次 step
    # playing=False 时自动停止，无任何线程残留
    solara.use_interval(
        lambda: on_step(),
        interval=500,
        enabled=playing.value,
    )

    with solara.Row(gap="8px", align="center"):'''

content = content.replace(old_control_bar, new_control_bar)

# ── 12. 重写 MacroStatsPanel → solara 原生 + 滚动 + 新指标 ──
old_macro_panel = '''@solara.component
def MacroStatsPanel():
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

    with solara.Card(f"宏观快照 — 第 {stats["cycle"]} 轮", margin=0):
        with solara.Row(align="center"):
            solara.Text(f"经济周期: {stage}", style=f"color:{stage_color};font-weight:bold;margin-left:8px;")

        with solara.Grid(columns=2, gap="8px"):
            solara.Text(f"企业: {stats["n_firms"]}")
            solara.Text(f"就业: {stats["employed"]}/{stats["n_households"]} ({stats["emp_rate"]:.1f}%)")
            solara.Text(f"失业: {stats["unemployed"]}人")
            solara.Text(f"交易者: {stats["n_traders"]}")

        with solara.Row(gap="16px", style="margin-top:8px;padding-top:8px;border-top:1px solid #334155;"):
            solara.Text(f"波动率: <span style='color:{vol_color}'>{vol:.3f}</span>", unsafe_innerHTML=True)
            solara.Text(f"违约: {stats["default_count"]}家")
            solara.Text(f"坏账率: <span style='color:{bdr_color}'>{bdr:.1%}</span>", unsafe_innerHTML=True)'''

new_macro_panel = '''@solara.component
def MacroStatsPanel():
    m = model_ref.value
    if m is None:
        solara.Text("模型加载中...", style="color:#94a3b8;")
        return
    stats = build_macro_stats(m)
    stage, stage_color = get_cycle_stage(m)
    vol = stats["volatility"]
    bdr = stats["bad_debt_rate"]
    shock = stats.get("shock", "") or ""

    with solara.Card(f"宏观快照  第 {stats["cycle"]} 轮", margin=0, style="background:#1e293b;color:#e2e8f0;"):
        with solara.Row(align="center"):
            solara.Text(f"周期: {stage}", style=f"color:{stage_color};font-weight:bold;margin-left:8px;")
            if shock:
                solara.Text(f"[{shock}]", style="color:#fbbf24;margin-left:8px;font-size:12px;")

        with solara.Grid(columns=3, gap="6px 16px", style="font-size:13px;"):
            solara.Text(f"GDP: {stats["gdp"]:>7.0f}")
            solara.Text(f"物价: {stats["price_index"]:>6.1f}")
            solara.Text(f"股价: {stats["stock_price"]:>6.1f}")
            solara.Text(f"企业: {stats["n_firms"]}")
            solara.Text(f"员工: {stats["employed"]}/{stats["n_households"]} ({stats["emp_rate"]:.0f}%)")
            solara.Text(f"失业: {stats["unemployed"]}")
            solara.Text(f"基尼: {stats["gini"]:.3f}")
            solara.Text(f"波动: {vol:.3f}" + ("!" if vol > 0.3 else ""))
            solara.Text(f"坏账: {bdr:.1%}" + ("!" if bdr > 0.1 else ""))

        with solara.Grid(columns=2, gap="4px 24px", style="margin-top:6px;padding-top:6px;border-top:1px solid #334155;font-size:12px;color:#94a3b8;"):
            solara.Text(f"政府收入: {stats["govt_rev"]:>7.1f}")
            solara.Text(f"政府购买: {stats["gov_purch"]:>6.0f}")
            solara.Text(f"总贷款: {stats["loans"]:>7.0f}")
            solara.Text(f"资本利得税: {stats["cap_gains"]:>5.1f}")
            solara.Text(f"系统风险: {stats["systemic"]:.3f}")
            solara.Text(f"破产: {stats["bankrupt"]}家")
            solara.Text(f"违约企业: {stats["default_count"]}家")
            solara.Text(f"交易者: {stats["n_traders"]}")'''

content = content.replace(old_macro_panel, new_macro_panel)

# ── 13. 修复 _get_current_params ────────────────────────────
old_get_params = '''def _get_current_params():
    """从滑块读取当前参数（用于重置）"""
    return {
        "n_households": n_households.value,
        "n_firms": n_firms.value,
        "n_traders": n_traders.value,
        "tax_rate": tax_rate.value,
        "base_interest_rate": base_interest_rate.value,
        "min_wage": min_wage.value,
        "productivity": productivity.value,
        "subsidy": subsidy.value,
    }'''

new_get_params = '''def _get_current_params():
    """从滑块字典读取当前参数（用于重置）"""
    return {key: ref.value for key, ref in _slider_refs.items()}'''

content = content.replace(old_get_params, new_get_params)

with open('server.py', 'w', encoding='utf-8') as f:
    f.write(content)

print(f"重构完成，文件长度: {len(content)} 字符")
