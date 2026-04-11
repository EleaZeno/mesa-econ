"""重写 server.py (v3.1) — 基于完整重构"""
import re

with open('server.py', 'r', encoding='utf-8') as f:
    content = f.read()

# ══ 1. 替换 get_agent_groups ══════════════════════════════════
old = '''def get_agent_groups(model: EconomyModel):
    firms = [a for a in model.agents if isinstance(a, Firm)]
    households = [a for a in model.agents if isinstance(a, Household)]
    traders = [a for a in model.agents if isinstance(a, Trader)]
    banks = [a for a in model.agents if isinstance(a, Bank)]
    return firms, households, traders, banks'''
new = '''def get_agent_groups(model: EconomyModel):
    """直接用 model 分类列表，O(1) 避免 O(n²) 重复 isinstance 筛选"""
    return model.firms, model.households, model.traders, model.banks'''
content = content.replace(old, new, 1)

# ══ 2. 替换 build_macro_stats ════════════════════════════════
old = '''def build_macro_stats(model: EconomyModel) -> dict[str, Any]:
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
new = '''def build_macro_stats(model: EconomyModel) -> dict[str, Any]:
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
        "unemployment":      model.unemployment,
        "gini":              model.gini,
        "shock":             getattr(model, "current_shock", ""),
    }'''
content = content.replace(old, new, 1)

# ══ 3. 替换经济周期阈值 ════════════════════════════════════════
old = '''def get_cycle_stage(model: EconomyModel) -> tuple[str, str]:
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
new = '''CYCLE_CONFIG = [
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
content = content.replace(old, new, 1)

# ══ 4. 失业率转换修正 ══════════════════════════════════════════
old = '''            # 转换 unemployment: 存储的是百分比数字 15.0，不是小数
            if key == "unemployment":
                vals = [v / 100 for v in vals] if vals else []'''
new = '''            # unemployment: 模型存小数(0.15)则不变；若存百分比(15.0)则 /100
            if key == "unemployment":
                vals = [v / 100 if v > 1 else v for v in vals] if vals else []'''
content = content.replace(old, new, 1)

# ══ 5. 重写 ControlBar（stop_event 替代裸 threading）═════════
old_bar = '''@solara.component
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

new_bar = '''# 全局停止事件（单线程，安全替代裸 threading）
import threading
_play_stop_event: threading.Event | None = None

def _run_play_loop(stop_evt: threading.Event):
    """播放循环：stop_evt.set() 即优雅停止"""
    import time
    while not stop_evt.wait(0.5):
        m = model_ref.value
        if m is not None:
            m.step()


@solara.component
def ControlBar(initial_params: dict):
    """控制栏：stop_event 替代裸 threading（优雅停止，无线程泄漏）"""
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
            # 停止
            if _play_stop_event:
                _play_stop_event.set()
            playing.set(False)
        else:
            # 启动新循环
            _play_stop_event = threading.Event()
            t = threading.Thread(target=_run_play_loop, args=(_play_stop_event,), daemon=True)
            t.start()
            playing.set(True)

    solara.use_effect(lambda: on_init(), [])

    with solara.Row(gap="8px", align="center"):'''
content = content.replace(old_bar, new_bar, 1)

# ══ 6. 重写 ParamSliders ══════════════════════════════════════
old_ps = '''@solara.component
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

    with solara.Card("经济参数（实时生效）", margin=0):
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

new_ps = '''# 全局滑块引用字典（用于场景同步）
_slider_refs: dict[str, solara.Reactive] = {}

def _apply_immediately(key: str, value: Any):
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
    # 首次初始化响应式字典
    if not _slider_refs:
        for key, cfg in PARAM_CONFIG.items():
            _slider_refs[key] = solara.use_reactive(cfg["default"])

    with solara.Card("经济参数（实时生效）", margin=0, style="background:#1e293b;color:#e2e8f0;"):
        with solara.Grid(columns=2):
            for key, cfg in PARAM_CONFIG.items():
                ref = _slider_refs[key]
                # 闭包陷阱修复：k=key 默认参数捕获
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
content = content.replace(old_ps, new_ps, 1)

# ══ 7. 重写 ScenarioPanel（边界校验 + 滑块同步）═════════════
old_sp = '''@solara.component
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

new_sp = '''@solara.component
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
                    # 边界校验：超限值自动 clamp 并告警
                    valid_val = max(cfg["min"], min(value, cfg["max"]))
                    if valid_val != value:
                        logger.warning(
                            "场景'%s'的%s=%.3f超出[%.2f,%.2f]，修正为%.3f",
                            selected.value, key, value, cfg["min"], cfg["max"], valid_val,
                        )
                    setattr(m, key, valid_val)
                    _sync_slider(key, valid_val)
                    logger.info("场景 '%s': %s = %.3f", selected.value, key, valid_val)
        solara.Button("应用场景", on_click=apply_scenario, color="primary")'''
content = content.replace(old_sp, new_sp, 1)

# ══ 8. 重写 PolicyPanel（边界从 PARAM_CONFIG 读取）══════════
old_pp = '''@solara.component
def PolicyPanel():
    def adjust(model_attr: str, delta: float, label: str):
        m = model_ref.value
        if m is None:
            return
        old = getattr(m, model_attr)
        new_val = max(0.0, min(getattr(m, model_attr) + delta, 0.5))
        setattr(m, model_attr, new_val)
        logger.info("政策 %s: %.4f → %.4f", label, old, new_val)

    with solara.Card("利率/税率调整", margin=0):'''

new_pp = '''@solara.component
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
content = content.replace(old_pp, new_pp, 1)

# ══ 9. 重写 AgentDetailPanel ══════════════════════════════════
old_adp = '''@solara.component
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

new_adp = '''@solara.component
def AgentDetailPanel():
    selected_type = solara.use_reactive("家庭")
    agent_types = ["家庭", "企业", "交易者", "银行"]
    type_map = {"家庭": Household, "企业": Firm, "交易者": Trader, "银行": Bank}

    with solara.Card("Agent详情", margin=0, style="background:#1e293b;color:#e2e8f0;"):
        solara.Select(
            label="选择类型",
            value=selected_type,
            values=agent_types,
        )
        m = model_ref.value
        if m is None:
            solara.Text("模型加载中...", style="color:#94a3b8;")
            return

        # 直接用 model 分类列表（O(1)，不再 isinstance 筛选）
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
                    tier = getattr(a, "income_tier", "?")
                    solara.Text(
                        f"H#{uid} 现:{cash:>6.0f} 富:{wealth:>7.0f} "
                        f"{employed} 薪:{salary:>5.1f} 股:{shares} [{tier}]",
                        style="font-size:11px;margin:1px 0;",
                    )
                elif isinstance(a, Firm):
                    prod = getattr(a, "production", 0.0)
                    inv = getattr(a, "inventory", 0.0)
                    emp = getattr(a, "employees", 0)
                    ind = getattr(a, "industry", None)
                    lc = getattr(a, "lifecycle", None)
                    ind_str = ind.value[:3] if ind else "?"
                    lc_str = lc.value[:3] if lc else "?"
                    solara.Text(
                        f"F#{uid} 现:{cash:>6.0f} 富:{wealth:>7.0f} "
                        f"产:{prod:>5.1f} 库:{inv:>4.1f} 员:{emp} {ind_str}/{lc_str}",
                        style="font-size:11px;margin:1px 0;",
                    )
                elif isinstance(a, Trader):
                    shares_t = getattr(a, "shares", 0)
                    strat = getattr(a, "strategy", None)
                    strat_str = strat.value if strat else "?"
                    solara.Text(
                        f"T#{uid} 现:{cash:>6.0f} 富:{wealth:>7.0f} 股:{shares_t} {strat_str}",
                        style="font-size:11px;margin:1px 0;",
                    )
                elif isinstance(a, Bank):
                    reserves = getattr(a, "reserves", 0.0)
                    btype = getattr(a, "bank_type", "?")
                    solara.Text(
                        f"B#{uid} 准:{reserves:>7.0f} 富:{wealth:>7.0f} [{btype}]",
                        style="font-size:11px;margin:1px 0;",
                    )'''
content = content.replace(old_adp, new_adp, 1)

# ══ 10. 重写 MacroStatsPanel ══════════════════════════════════
old_mp = '''@solara.component
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

new_mp = '''@solara.component
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

    with solara.Card(f"宏观快照  第 {stats['cycle']} 轮", margin=0, style="background:#1e293b;color:#e2e8f0;"):
        with solara.Row(align="center"):
            solara.Text(f"周期: {stage}", style=f"color:{stage_color};font-weight:bold;margin-left:8px;")
            if shock:
                solara.Text(f"[{shock}]", style="color:#fbbf24;margin-left:8px;font-size:12px;")

        with solara.Grid(columns=3, gap="6px 16px", style="font-size:13px;"):
            solara.Text(f"GDP: {stats['gdp']:>7.0f}")
            solara.Text(f"物价: {stats['price_index']:>6.1f}")
            solara.Text(f"股价: {stats['stock_price']:>6.1f}")
            solara.Text(f"企业: {stats['n_firms']}")
            solara.Text(f"员工: {stats['employed']}/{stats['n_households']} ({stats['emp_rate']:.0f}%)")
            solara.Text(f"失业: {stats['unemployed']}")
            solara.Text(f"基尼: {stats['gini']:.3f}")
            vol_flag = "!" if vol > 0.3 else ""
            solara.Text(f"波动: {vol:.3f}{vol_flag}")
            bdr_flag = "!" if bdr > 0.1 else ""
            solara.Text(f"坏账: {bdr:.1%}{bdr_flag}")

        with solara.Grid(columns=2, gap="4px 24px",
                         style="margin-top:6px;padding-top:6px;border-top:1px solid #334155;font-size:12px;color:#94a3b8;"):
            solara.Text(f"政府收入: {stats['govt_rev']:>7.1f}")
            solara.Text(f"政府购买: {stats['gov_purch']:>6.0f}")
            solara.Text(f"总贷款: {stats['loans']:>7.0f}")
            solara.Text(f"资本利得税: {stats['cap_gains']:>5.1f}")
            solara.Text(f"系统风险: {stats['systemic']:.3f}")
            solara.Text(f"破产: {stats['bankrupt']}家")
            solara.Text(f"违约企业: {stats['default_count']}家")
            solara.Text(f"交易者: {stats['n_traders']}")'''
content = content.replace(old_mp, new_mp, 1)

# ══ 11. 替换 _get_current_params ═════════════════════════════
old_gcp = '''def _get_current_params():
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
new_gcp = '''def _get_current_params():
    """从全局滑块字典读取当前参数（用于重置）"""
    return {key: ref.value for key, ref in _slider_refs.items()}'''
content = content.replace(old_gcp, new_gcp, 1)

# ══ 12. 删除 play_task_ref（已用 stop_event 替代）════════════
content = content.replace(
    'play_task_ref: solara.Reactive[Any] = solara.reactive(None)\n',
    'play_task_ref: solara.Reactive[Any] = solara.reactive(None)  # deprecated, kept for compat\n'
)

with open('server.py', 'w', encoding='utf-8') as f:
    f.write(content)

print(f"rewrite_server done: {len(content)} chars")
