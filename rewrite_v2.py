"""直接用行号替换函数体"""
with open('server.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

def make_reactive_block():
    return [
        '# 全局滑块引用字典（用于场景同步）\n',
        '_slider_refs: dict[str, solara.Reactive] = {}\n',
        '\n',
        'def _apply_immediately(key: str, value: Any):\n',
        '    m = model_ref.value\n',
        '    if m is not None and hasattr(m, key):\n',
        '        setattr(m, key, value)\n',
        '        logger.info("实时 %s = %s", key, value)\n',
        '\n',
        'def _sync_slider(key: str, value: Any):\n',
        '    if key in _slider_refs:\n',
        '        _slider_refs[key].set(value)\n',
        '    _apply_immediately(key, value)\n',
        '\n',
    ]

def make_controlbar_block():
    return [
        '# 全局停止事件（单线程替代裸 threading）\n',
        'import threading\n',
        '_play_stop_event: threading.Event | None = None\n',
        '\n',
        'def _run_play_loop(stop_evt: threading.Event):\n',
        '    import time\n',
        '    while not stop_evt.wait(0.5):\n',
        '        m = model_ref.value\n',
        '        if m is not None:\n',
        '            m.step()\n',
        '\n',
        '\n',
        '@solara.component\n',
        'def ControlBar(initial_params: dict):\n',
        '    """控制栏：stop_event 替代裸 threading（优雅停止，无线程泄漏）"""\n',
        '    playing = solara.use_reactive(False)\n',
        '    loading = solara.use_reactive(False)\n',
        '\n',
        '    def on_init():\n',
        '        loading.set(True)\n',
        '        reset_model(initial_params)\n',
        '        loading.set(False)\n',
        '\n',
        '    def on_reset():\n',
        '        global _play_stop_event\n',
        '        if _play_stop_event:\n',
        '            _play_stop_event.set()\n',
        '        loading.set(True)\n',
        '        playing.set(False)\n',
        '        params = _get_current_params()\n',
        '        reset_model(params)\n',
        '        loading.set(False)\n',
        '\n',
        '    def on_step():\n',
        '        m = model_ref.value\n',
        '        if m is not None:\n',
        '            m.step()\n',
        '\n',
        '    def on_play():\n',
        '        global _play_stop_event\n',
        '        if playing.value:\n',
        '            if _play_stop_event:\n',
        '                _play_stop_event.set()\n',
        '            playing.set(False)\n',
        '        else:\n',
        '            _play_stop_event = threading.Event()\n',
        '            t = threading.Thread(target=_run_play_loop, args=(_play_stop_event,), daemon=True)\n',
        '            t.start()\n',
        '            playing.set(True)\n',
        '\n',
        '    solara.use_effect(lambda: on_init(), [])\n',
        '\n',
        '    with solara.Row(gap="8px", align="center"):\n',
    ]

# ── 1. 全局函数前插：_slider_refs + _sync_slider ─────────────────
# 找 get_agent_groups 的位置（之前插入）
insert_idx = None
for i, l in enumerate(lines):
    if 'def get_agent_groups(' in l:
        insert_idx = i
        break

if insert_idx:
    # 在 get_agent_groups 前插入全局滑块函数
    new_block = make_reactive_block()
    lines[insert_idx:insert_idx] = new_block
    shift = len(new_block)
else:
    shift = 0
    insert_idx = 0

# 重新计算各函数位置（行号都偏移了）
def find_line(pattern, start=0):
    for i in range(start, len(lines)):
        if pattern in lines[i]:
            return i
    return -1

# 找 _run_play_loop 插入位置（在 reset_model 之后）
reset_idx = find_line('def reset_model(')
play_fn_idx = find_line('def _run_play_loop')
if play_fn_idx == -1:
    play_fn_idx = reset_idx + 5  # 约在 reset_model 之后
    # 插入 _run_play_loop 和 stop_event
    block = [
        '# 全局停止事件\n',
        'import threading\n',
        '_play_stop_event: threading.Event | None = None\n',
        '\n',
        'def _run_play_loop(stop_evt: threading.Event):\n',
        '    import time\n',
        '    while not stop_evt.wait(0.5):\n',
        '        m = model_ref.value\n',
        '        if m is not None:\n',
        '            m.step()\n',
        '\n',
    ]
    lines[play_fn_idx:play_fn_idx] = block
    shift2 = len(block)
    # 重新找所有位置
    reset_idx = find_line('def reset_model(')
    ctrlbar_start = find_line('def ControlBar(')
    ctrlbar_end = find_line('@solara.component', ctrlbar_start + 1)
    ps_start = find_line('def ParamSliders(')
    ps_end = find_line('@solara.component', ps_start + 1)
    mp_start = find_line('def MacroStatsPanel(')
    pp_start = find_line('def PolicyPanel(')
    sp_start = find_line('def ScenarioPanel(')
    adp_start = find_line('def AgentDetailPanel(')
    adp_end = find_line('@solara.component', adp_start + 1)
    pg_start = find_line('def Page(')
    gcp_idx = find_line('def _get_current_params(')
    gag_idx = find_line('def get_agent_groups(')
    bms_idx = find_line('def build_macro_stats(')
    gcs_idx = find_line('def get_cycle_stage(')
    gch_idx = find_line('def get_chart_data(')

    # ── 2. 替换 get_agent_groups ─────────────────────────────────
    old = []
    for i in range(gag_idx, gag_idx + 6):
        if i < len(lines):
            old.append(lines[i])
    new = [
        'def get_agent_groups(model: EconomyModel):\n',
        '    """直接用 model 分类列表，O(1) 避免 O(n²) 重复 isinstance 筛选"""\n',
        '    return model.firms, model.households, model.traders, model.banks\n',
        '\n',
    ]
    lines[gag_idx:gag_idx+len(old)] = new

    # 重新定位（因为内容变了）
    gag_idx = find_line('def get_agent_groups(')
    bms_idx = find_line('def build_macro_stats(')
    gcs_idx = find_line('def get_cycle_stage(')
    gch_idx = find_line('def get_chart_data(')

    # ── 3. 替换 build_macro_stats ───────────────────────────────
    # 找结束行
    bms_end = find_line('def ', bms_idx + 1)
    if bms_end == -1: bms_end = gag_idx  # fallback
    old = lines[bms_idx:bms_end]
    new = [
        'def build_macro_stats(model: EconomyModel) -> dict[str, Any]:\n',
        '    firms, households, traders, banks = get_agent_groups(model)\n',
        '    n_hh = len(households)\n',
        '    employed = sum(1 for h in households if h.employed)\n',
        '    emp_rate = (employed / n_hh * 100) if n_hh > 0 else 0.0\n',
        '    return {\n',
        '        "cycle":             model.cycle,\n',
        '        "n_firms":           len(firms),\n',
        '        "employed":          employed,\n',
        '        "n_households":      n_hh,\n',
        '        "emp_rate":          emp_rate,\n',
        '        "unemployed":        n_hh - employed if n_hh > 0 else 0,\n',
        '        "n_traders":         len(traders),\n',
        '        "total_prod":        sum(getattr(f, "production", 0) for f in firms),\n',
        '        "dividends":         model.total_dividends,\n',
        '        "tax_rate":          model.tax_rate,\n',
        '        "interest_rate":     model.base_interest_rate,\n',
        '        "price_index":       getattr(model, "price_index", model.avg_price),\n',
        '        "govt_rev":          model.govt_revenue,\n',
        '        "gov_purch":         model.gov_purchase,\n',
        '        "cap_gains":         model.capital_gains_tax_revenue,\n',
        '        "systemic":          round(model.systemic_risk, 4),\n',
        '        "bankrupt":          model.bankrupt_count,\n',
        '        "loans":             model.total_loans_outstanding,\n',
        '        "volatility":        model.stock_volatility,\n',
        '        "default_count":     model.default_count,\n',
        '        "bad_debt_rate":     model.bank_bad_debt_rate,\n',
        '        "stock_price":       model.stock_price,\n',
        '        "gdp":               model.gdp,\n',
        '        "unemployment":      model.unemployment,\n',
        '        "gini":              model.gini,\n',
        '        "shock":             getattr(model, "current_shock", ""),\n',
        '    }\n',
        '\n',
    ]
    lines[bms_idx:bms_end] = new

    # 重新定位
    gcs_idx = find_line('def get_cycle_stage(')
    gch_idx = find_line('def get_chart_data(')

    # ── 4. 替换经济周期函数 ─────────────────────────────────────
    gcs_end = find_line('def ', gcs_idx + 1)
    if gcs_end == -1: gcs_end = bms_idx
    lines[gcs_idx:gcs_end] = [
        'CYCLE_CONFIG = [\n',
        '    {"gdp_thr": 2000, "u_thr": 0.10, "label": "繁荣", "color": "#22c55e"},\n',
        '    {"gdp_thr": 1500, "u_thr": 0.15, "label": "复苏", "color": "#84cc16"},\n',
        '    {"gdp_thr": 1000, "u_thr": 0.25, "label": "平稳", "color": "#eab308"},\n',
        '    {"gdp_thr": 0,    "u_thr": 0.25, "label": "衰退", "color": "#f97316"},\n',
        '    {"gdp_thr": 0,    "u_thr": 1.00, "label": "萧条", "color": "#ef4444"},\n',
        ']\n',
        '\n',
        'def get_cycle_stage(model: EconomyModel) -> tuple[str, str]:\n',
        '    for cfg in CYCLE_CONFIG:\n',
        '        if model.gdp >= cfg["gdp_thr"] and model.unemployment <= cfg["u_thr"]:\n',
        '            return cfg["label"], cfg["color"]\n',
        '    return CYCLE_CONFIG[-1]["label"], CYCLE_CONFIG[-1]["color"]\n',
        '\n',
    ]

    # 重新定位
    gch_idx = find_line('def get_chart_data(')

    # ── 5. 修正失业率转换 ───────────────────────────────────────
    gch_end = find_line('def ', gch_idx + 1)
    if gch_end == -1: gch_end = gch_idx + 20
    chart_block = ''.join(lines[gch_idx:gch_end])
    # 只替换失业率那行
    chart_block = chart_block.replace(
        '[v / 100 for v in vals] if vals else []',
        '[v / 100 if v > 1 else v for v in vals] if vals else []'
    )
    chart_block = chart_block.replace(
        '# 转换 unemployment: 存储的是百分比数字 15.0，不是小数',
        '# unemployment: 模型存小数(0.15)则不变；若存百分比(15.0)则 /100'
    )
    new_chart = chart_block.split('\n')
    lines[gch_idx:gch_end] = [l + '\n' for l in new_chart]

    # 重新定位
    ctrlbar_start = find_line('def ControlBar(')
    ps_start = find_line('def ParamSliders(')
    adp_start = find_line('def AgentDetailPanel(')
    adp_end = find_line('@solara.component', adp_start + 1)
    pg_start = find_line('def Page(')
    gcp_idx = find_line('def _get_current_params(')

    # ── 6. 替换 ControlBar ─────────────────────────────────────
    ctrlbar_end = find_line('@solara.component', ctrlbar_start + 1)
    if ctrlbar_end == -1: ctrlbar_end = ps_start
    lines[ctrlbar_start:ctrlbar_end] = [
        '@solara.component\n',
        'def ControlBar(initial_params: dict):\n',
        '    """控制栏：stop_event 替代裸 threading（优雅停止，无线程泄漏）"""\n',
        '    playing = solara.use_reactive(False)\n',
        '    loading = solara.use_reactive(False)\n',
        '\n',
        '    def on_init():\n',
        '        loading.set(True)\n',
        '        reset_model(initial_params)\n',
        '        loading.set(False)\n',
        '\n',
        '    def on_reset():\n',
        '        global _play_stop_event\n',
        '        if _play_stop_event:\n',
        '            _play_stop_event.set()\n',
        '        loading.set(True)\n',
        '        playing.set(False)\n',
        '        params = _get_current_params()\n',
        '        reset_model(params)\n',
        '        loading.set(False)\n',
        '\n',
        '    def on_step():\n',
        '        m = model_ref.value\n',
        '        if m is not None:\n',
        '            m.step()\n',
        '\n',
        '    def on_play():\n',
        '        global _play_stop_event\n',
        '        if playing.value:\n',
        '            if _play_stop_event:\n',
        '                _play_stop_event.set()\n',
        '            playing.set(False)\n',
        '        else:\n',
        '            _play_stop_event = threading.Event()\n',
        '            t = threading.Thread(target=_run_play_loop, args=(_play_stop_event,), daemon=True)\n',
        '            t.start()\n',
        '            playing.set(True)\n',
        '\n',
        '    solara.use_effect(lambda: on_init(), [])\n',
        '\n',
        '    with solara.Row(gap="8px", align="center"):\n',
    ]

    # 重新定位
    ps_start = find_line('def ParamSliders(')
    ps_end = find_line('@solara.component', ps_start + 1)
    adp_start = find_line('def AgentDetailPanel(')
    adp_end = find_line('@solara.component', adp_start + 1)
    pg_start = find_line('def Page(')
    gcp_idx = find_line('def _get_current_params(')

    # ── 7. 替换 ParamSliders ───────────────────────────────────
    lines[ps_start:ps_end] = [
        '@solara.component\n',
        'def ParamSliders():\n',
        '    """参数面板：遍历 PARAM_CONFIG 自动生成滑块，实时生效"""\n',
        '    if not _slider_refs:\n',
        '        for key, cfg in PARAM_CONFIG.items():\n',
        '            _slider_refs[key] = solara.use_reactive(cfg["default"])\n',
        '\n',
        '    with solara.Card("经济参数（实时生效）", margin=0, style="background:#1e293b;color:#e2e8f0;"):\n',
        '        with solara.Grid(columns=2):\n',
        '            for key, cfg in PARAM_CONFIG.items():\n',
        '                ref = _slider_refs[key]\n',
        '                def make_handler(k):\n',
        '                    def handler(value):\n',
        '                        ref.set(value)\n',
        '                        _apply_immediately(k, value)\n',
        '                    return handler\n',
        '                solara.FloatSlider(\n',
        '                    label=cfg["label"],\n',
        '                    value=ref,\n',
        '                    min=cfg["min"],\n',
        '                    max=cfg["max"],\n',
        '                    step=cfg["step"],\n',
        '                    format=cfg.get("fmt", "%.2f"),\n',
        '                    on_value=make_handler(key),\n',
        '                )\n',
    ]

    # 重新定位
    adp_start = find_line('def AgentDetailPanel(')
    adp_end = find_line('@solara.component', adp_start + 1)
    pg_start = find_line('def Page(')
    gcp_idx = find_line('def _get_current_params(')
    sp_start = find_line('def ScenarioPanel(')
    pp_start = find_line('def PolicyPanel(')
    mp_start = find_line('def MacroStatsPanel(')

    # ── 8. 替换 AgentDetailPanel ────────────────────────────────
    lines[adp_start:adp_end] = [
        '@solara.component\n',
        'def AgentDetailPanel():\n',
        '    selected_type = solara.use_reactive("家庭")\n',
        '    agent_types = ["家庭", "企业", "交易者", "银行"]\n',
        '    type_map = {"家庭": Household, "企业": Firm, "交易者": Trader, "银行": Bank}\n',
        '\n',
        '    with solara.Card("Agent详情", margin=0, style="background:#1e293b;color:#e2e8f0;"):\n',
        '        solara.Select(\n',
        '            label="选择类型",\n',
        '            value=selected_type,\n',
        '            values=agent_types,\n',
        '        )\n',
        '        m = model_ref.value\n',
        '        if m is None:\n',
        '            solara.Text("模型加载中...", style="color:#94a3b8;")\n',
        '            return\n',
        '\n',
        '        all_agents = {\n',
        '            "家庭": m.households,\n',
        '            "企业": m.firms,\n',
        '            "交易者": m.traders,\n',
        '            "银行": m.banks,\n',
        '        }.get(selected_type.value, [])\n',
        '\n',
        '        with solara.VBox(style="max-height:350px;overflow-y:auto;padding:4px;"):\n',
        '            if not all_agents:\n',
        '                solara.Text("无该类型Agent", style="color:#94a3b8;")\n',
        '            for a in all_agents:\n',
        '                uid = a.unique_id\n',
        '                cash = getattr(a, "cash", 0.0)\n',
        '                wealth = getattr(a, "wealth", 0.0)\n',
        '                if isinstance(a, Household):\n',
        '                    employed = "就业" if getattr(a, "employed", False) else "失业"\n',
        '                    salary = getattr(a, "salary", 0.0)\n',
        '                    shares = getattr(a, "shares_owned", 0)\n',
        '                    tier = getattr(a, "income_tier", "?")\n',
        '                    solara.Text(\n',
        '                        f"H#{uid} 现:{cash:>6.0f} 富:{wealth:>7.0f} "\n',
        '                        f"{employed} 薪:{salary:>5.1f} 股:{shares} [{tier}]",\n',
        '                        style="font-size:11px;margin:1px 0;",\n',
        '                    )\n',
        '                elif isinstance(a, Firm):\n',
        '                    prod = getattr(a, "production", 0.0)\n',
        '                    inv = getattr(a, "inventory", 0.0)\n',
        '                    emp = getattr(a, "employees", 0)\n',
        '                    ind = getattr(a, "industry", None)\n',
        '                    lc = getattr(a, "lifecycle", None)\n',
        '                    ind_str = ind.value[:3] if ind else "?"\n',
        '                    lc_str = lc.value[:3] if lc else "?"\n',
        '                    solara.Text(\n',
        '                        f"F#{uid} 现:{cash:>6.0f} 富:{wealth:>7.0f} "\n',
        '                        f"产:{prod:>5.1f} 库:{inv:>4.1f} 员:{emp} {ind_str}/{lc_str}",\n',
        '                        style="font-size:11px;margin:1px 0;",\n',
        '                    )\n',
        '                elif isinstance(a, Trader):\n',
        '                    shares_t = getattr(a, "shares", 0)\n',
        '                    strat = getattr(a, "strategy", None)\n',
        '                    strat_str = strat.value if strat else "?"\n',
        '                    solara.Text(\n',
        '                        f"T#{uid} 现:{cash:>6.0f} 富:{wealth:>7.0f} 股:{shares_t} {strat_str}",\n',
        '                        style="font-size:11px;margin:1px 0;",\n',
        '                    )\n',
        '                elif isinstance(a, Bank):\n',
        '                    reserves = getattr(a, "reserves", 0.0)\n',
        '                    btype = getattr(a, "bank_type", "?")\n',
        '                    solara.Text(\n',
        '                        f"B#{uid} 准:{reserves:>7.0f} 富:{wealth:>7.0f} [{btype}]",\n',
        '                        style="font-size:11px;margin:1px 0;",\n',
        '                    )\n',
    ]

    # 重新定位
    pg_start = find_line('def Page(')
    gcp_idx = find_line('def _get_current_params(')
    sp_start = find_line('def ScenarioPanel(')
    pp_start = find_line('def PolicyPanel(')
    mp_start = find_line('def MacroStatsPanel(')

    # ── 9. 替换 ScenarioPanel ───────────────────────────────────
    sp_end = find_line('@solara.component', sp_start + 1)
    if sp_end == -1: sp_end = pp_start
    lines[sp_start:sp_end] = [
        '@solara.component\n',
        'def ScenarioPanel():\n',
        '    selected = solara.use_reactive("默认")\n',
        '    with solara.Card("预设场景", margin=0, style="background:#1e293b;color:#e2e8f0;"):\n',
        '        solara.Select(\n',
        '            label="选择场景",\n',
        '            value=selected,\n',
        '            values=list(SCENARIOS.keys()),\n',
        '        )\n',
        '        def apply_scenario():\n',
        '            scenario = SCENARIOS.get(selected.value, {})\n',
        '            m = model_ref.value\n',
        '            if m is None:\n',
        '                return\n',
        '            for key, value in scenario.items():\n',
        '                if hasattr(m, key) and key in PARAM_CONFIG:\n',
        '                    cfg = PARAM_CONFIG[key]\n',
        '                    valid_val = max(cfg["min"], min(value, cfg["max"]))\n',
        '                    if valid_val != value:\n',
        '                        logger.warning(\n',
        '                            "场景%s的%s=%.3f超出[%.2f,%.2f]，修正为%.3f",\n',
        '                            selected.value, key, value, cfg["min"], cfg["max"], valid_val,\n',
        '                        )\n',
        '                    setattr(m, key, valid_val)\n',
        '                    _sync_slider(key, valid_val)\n',
        '                    logger.info("场景 %s: %s = %.3f", selected.value, key, valid_val)\n',
        '        solara.Button("应用场景", on_click=apply_scenario, color="primary")\n',
    ]

    # 重新定位
    pp_start = find_line('def PolicyPanel(')
    pp_end = find_line('@solara.component', pp_start + 1)
    mp_start = find_line('def MacroStatsPanel(')
    mp_end = find_line('@solara.component', mp_start + 1)
    gcp_idx = find_line('def _get_current_params(')

    # ── 10. 替换 PolicyPanel ─────────────────────────────────────
    lines[pp_start:pp_end] = [
        '@solara.component\n',
        'def PolicyPanel():\n',
        '    def adjust(model_attr: str, delta: float, label: str):\n',
        '        m = model_ref.value\n',
        '        if m is None or model_attr not in PARAM_CONFIG:\n',
        '            return\n',
        '        cfg = PARAM_CONFIG[model_attr]\n',
        '        old = getattr(m, model_attr)\n',
        '        new_val = max(cfg["min"], min(old + delta, cfg["max"]))\n',
        '        setattr(m, model_attr, new_val)\n',
        '        _sync_slider(model_attr, new_val)\n',
        '        logger.info("政策 %s: %.4f -> %.4f", label, old, new_val)\n',
        '\n',
        '    with solara.Card("利率/税率调整", margin=0, style="background:#1e293b;color:#e2e8f0;"):\n',
    ]

    # 重新定位
    mp_start = find_line('def MacroStatsPanel(')
    mp_end = find_line('@solara.component', mp_start + 1)
    gcp_idx = find_line('def _get_current_params(')

    # ── 11. 替换 MacroStatsPanel ────────────────────────────────
    lines[mp_start:mp_end] = [
        '@solara.component\n',
        'def MacroStatsPanel():\n',
        '    m = model_ref.value\n',
        '    if m is None:\n',
        '        solara.Text("模型加载中...", style="color:#94a3b8;")\n',
        '        return\n',
        '    stats = build_macro_stats(m)\n',
        '    stage, stage_color = get_cycle_stage(m)\n',
        '    vol = stats["volatility"]\n',
        '    bdr = stats["bad_debt_rate"]\n',
        '    shock = stats.get("shock", "") or ""\n',
        '\n',
        '    with solara.Card(f"宏观快照  第 {stats[\'cycle\']} 轮", margin=0, style="background:#1e293b;color:#e2e8f0;"):\n',
        '        with solara.Row(align="center"):\n',
        '            solara.Text(f"周期: {stage}", style=f"color:{stage_color};font-weight:bold;margin-left:8px;")\n',
        '            if shock:\n',
        '                solara.Text(f"[{shock}]", style="color:#fbbf24;margin-left:8px;font-size:12px;")\n',
        '\n',
        '        with solara.Grid(columns=3, gap="6px 16px", style="font-size:13px;"):\n',
        '            solara.Text(f"GDP: {stats[\'gdp\']:>7.0f}")\n',
        '            solara.Text(f"物价: {stats[\'price_index\']:>6.1f}")\n',
        '            solara.Text(f"股价: {stats[\'stock_price\']:>6.1f}")\n',
        '            solara.Text(f"企业: {stats[\'n_firms\']}")\n',
        '            solara.Text(f"员工: {stats[\'employed\']}/{stats[\'n_households\']} ({stats[\'emp_rate\']:.0f}%)")\n',
        '            solara.Text(f"失业: {stats[\'unemployed\']}")\n',
        '            solara.Text(f"基尼: {stats[\'gini\']:.3f}")\n',
        '            vol_flag = "!" if vol > 0.3 else ""\n',
        '            solara.Text(f"波动: {vol:.3f}{vol_flag}")\n',
        '            bdr_flag = "!" if bdr > 0.1 else ""\n',
        '            solara.Text(f"坏账: {bdr:.1%}{bdr_flag}")\n',
        '\n',
        '        with solara.Grid(columns=2, gap="4px 24px",\n',
        '                         style="margin-top:6px;padding-top:6px;border-top:1px solid #334155;font-size:12px;color:#94a3b8;"):\n',
        '            solara.Text(f"政府收入: {stats[\'govt_rev\']:>7.1f}")\n',
        '            solara.Text(f"政府购买: {stats[\'gov_purch\']:>6.0f}")\n',
        '            solara.Text(f"总贷款: {stats[\'loans\']:>7.0f}")\n',
        '            solara.Text(f"资本利得税: {stats[\'cap_gains\']:>5.1f}")\n',
        '            solara.Text(f"系统风险: {stats[\'systemic\']:.3f}")\n',
        '            solara.Text(f"破产: {stats[\'bankrupt\']}家")\n',
        '            solara.Text(f"违约企业: {stats[\'default_count\']}家")\n',
        '            solara.Text(f"交易者: {stats[\'n_traders\']}")\n',
    ]

    # 重新定位
    gcp_idx = find_line('def _get_current_params(')

    # ── 12. 替换 _get_current_params ────────────────────────────
    gcp_end = find_line('def ', gcp_idx + 1)
    if gcp_end == -1: gcp_end = gcp_idx + 15
    lines[gcp_idx:gcp_end] = [
        'def _get_current_params():\n',
        '    """从全局滑块字典读取当前参数（用于重置）"""\n',
        '    return {key: ref.value for key, ref in _slider_refs.items()}\n',
        '\n',
    ]

# 写回文件
with open('server.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print(f'Done: {len(lines)} lines')
