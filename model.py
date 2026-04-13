from __future__ import annotations
"""
Mesa 经济沙盘 - 核心模型 v3.5
基于 FRB/US · NAWM · ABCE 框架设计思路

v3.0 核心升级：
  ┌─────────────────────────────────────────────────────────────┐
  │ 一、智能体异质性                                            │
  │   Household: 三层收入 / 风险偏好 / 技能等级 / 差异化MPC     │
  │   Firm:     三行业 × 三生命周期阶段                        │
  │   Bank:     差异化利率（风险偏好型 vs 保守型）              │
  │   Trader:   四策略（动量 / 价值 / 噪声 / 做市商）           │
  ├─────────────────────────────────────────────────────────────┤
  │ 二、市场精细化                                              │
  │   商品市场: 企业独立定价 + 伯特兰竞争 + 价格粘性            │
  │   劳动力:   技能匹配 + 失业率议价 + 摩擦性失业             │
  │   信贷:     信用评分 + 抵押品 + 违约传导链                  │
  │   股市:     戈登增长模型基本面锚 + 供需短期扰动            │
  ├─────────────────────────────────────────────────────────────┤
  │ 三、政策传导                                                │
  │   政府购买（GDP拉动）+ 资本利得税 + 量化宽松（QE）         │
  │   利率→融资成本→招聘→失业率→消费 传导链                   │
  ├─────────────────────────────────────────────────────────────┤
  │ 四、外部冲击                                                │
  │   外生冲击：石油危机 / 技术突破 / 需求骤降 / 贸易战       │
  │   内生螺旋：金融加速器效应（抵押品→保证金→抛售）         │
  └─────────────────────────────────────────────────────────────┘

Mesa 3.x 最佳实践：
  - 分阶段调度（Shock → Bank → Firm → Household → Trader → Model宏观）
  - Agent 分类缓存（避免 O(n²) 重复筛选）
  - 参数全部可配置（零魔法数字）
"""


import logging
from enum import Enum
from typing import Optional

import numpy as np
from mesa import Agent, Model
from mesa import DataCollector

logger = logging.getLogger("econ")


# ══════════════════════════════════════════════════════════════
# 全局默认值（唯一配置源）
# ══════════════════════════════════════════════════════════════

class City(Enum):
    """城市标签（双城竞争）"""
    CITY_A = "city_a"   # 城市 A（工业导向）
    CITY_B = "city_b"   # 城市 B（科技导向）


# ── 城市级参数（Phase 3：差异化政策）────────────────────────────
CITY_PARAMS = {
    City.CITY_A: {
        "corporate_tax_rate": 0.12,    # 企业税率（低税吸引企业）
        "min_wage": 7.5,               # 最低工资
        "subsidy_rate": 0.10,          # 补贴率
        "infrastructure": 0.8,         # 基建水平（影响生产效率）
    },
    City.CITY_B: {
        "corporate_tax_rate": 0.18,    # 企业税率（高税高福利）
        "min_wage": 6.8,               # 最低工资（劳动力便宜）
        "subsidy_rate": 0.05,          # 补贴率
        "infrastructure": 1.0,         # 基建水平（科技发达）
    },
}


class Industry(Enum):
    """企业所属行业"""
    MANUFACTURING = "manufacturing"   # 制造业：资本密集，边际成本高
    SERVICE = "service"               # 服务业：轻资产，人力密集
    TECH = "tech"                    # 科技：研发投入大，生产率波动高


class FirmLifecycle(Enum):
    """企业生命周期阶段"""
    STARTUP = "startup"      # 初创：高风险，负现金流，强融资需求
    GROWTH = "growth"       # 成长期：盈利扩张，高招聘需求
    MATURE = "mature"       # 成熟期：稳定分红，低风险
    DECLINE = "decline"     # 衰退：产能过剩，裁员/破产风险


class TraderStrategy(Enum):
    """交易员策略"""
    MOMENTUM = "momentum"           # 动量：追涨杀跌
    VALUE = "value"                 # 价值：基于内在价值低买高卖
    NOISE = "noise"                 # 噪声：随机交易（散户行为）
    MARKET_MAKER = "market_maker"   # 做市商：双向挂单，赚价差


# ─── 居民异质性参数 ───────────────────────────────────────────

# 三层收入分位：低(0-33%) / 中(33-67%) / 高(67-100%)
INCOME_TIER_PARAMS = {
    "low": dict(
        mpc=0.80,        # 凯恩斯：低收入MPC≈0.8，赚100花80
        risk_aversion=0.9,   # 高风险厌恶，偏好储蓄
        stock_buy_prob=0.05,
        stock_sell_prob=0.02,
        initial_cash_range=(30, 100),
        skill_weights={0: 0.7, 1: 0.25, 2: 0.05},  # 低收入：70%低技能
    ),
    "middle": dict(
        mpc=0.50,        # 中等收入MPC≈0.5
        risk_aversion=0.5,
        stock_buy_prob=0.10,
        stock_sell_prob=0.06,
        initial_cash_range=(100, 250),
        skill_weights={0: 0.3, 1: 0.50, 2: 0.20},
    ),
    "high": dict(
        mpc=0.20,        # 高收入MPC≈0.2，赚100花20
        risk_aversion=0.2,    # 低风险厌恶，愿意投资
        stock_buy_prob=0.25,
        stock_sell_prob=0.15,
        initial_cash_range=(250, 600),
        skill_weights={0: 0.05, 1: 0.35, 2: 0.60},  # 高收入：60%高技能
    ),
}

# ─── 企业异质性参数 ───────────────────────────────────────────

INDUSTRY_PARAMS = {
    Industry.MANUFACTURING: dict(
        capital_intensity=2.0,     # 资本密集度（影响边际成本）
        price_flexibility=0.3,    # 价格调整速度（价格粘性：低→慢）
        wage_premium=1.0,         # 工资溢价（相对基准）
        productivity_noise=2.5,    # 生产率波动
        div_ratio=0.03,           # 分红比例（低：留存利润扩产）
        layoff_prob=0.05,         # 裁员概率（经济差时）
    ),
    Industry.SERVICE: dict(
        capital_intensity=0.5,     # 轻资产
        price_flexibility=0.5,     # 价格较灵活
        wage_premium=0.9,
        productivity_noise=1.5,
        div_ratio=0.06,
        layoff_prob=0.03,
    ),
    Industry.TECH: dict(
        capital_intensity=0.3,    # 低资本，高研发
        price_flexibility=0.7,    # 高灵活性
        wage_premium=1.5,         # 科技人才溢价
        productivity_noise=4.0,   # 高波动（技术突破/失败）
        div_ratio=0.02,           # 科技股少分红（高增长留存）
        layoff_prob=0.08,         # 快速裁员调整
    ),
}

# 企业生命周期权重
LIFECYCLE_WEIGHTS = {
    FirmLifecycle.STARTUP: 0.15,
    FirmLifecycle.GROWTH: 0.30,
    FirmLifecycle.MATURE: 0.40,
    FirmLifecycle.DECLINE: 0.15,
}

# ─── 银行异质性参数 ───────────────────────────────────────────

BANK_PARAMS = {
    "aggressive": dict(   # 风险偏好型银行
        risk_appetite=0.8,
        lending_spread=0.05,   # 高利差：基准+5%
        default_tolerance=0.7, # 高容忍坏账
        loan_amount=30.0,       # 大额放贷
        initial_reserves=1200.0,
    ),
    "conservative": dict(  # 保守型银行
        risk_appetite=0.3,
        lending_spread=0.01,   # 低利差：基准+1%
        default_tolerance=0.3,
        loan_amount=15.0,
        initial_reserves=800.0,
    ),
}

# ─── 核心模型参数 ───────────────────────────────────────────

DEFAULTS = dict(
    n_households=25,
    n_firms=12,
    n_banks=2,
    n_traders=20,
    # ── 政策参数 ──────────────────────────────────────
    tax_rate=0.15,             # 所得税率
    capital_gains_tax=0.10,     # 资本利得税率
    base_interest_rate=0.05,   # 基准利率
    min_wage=7.0,               # 最低工资
    productivity=1.0,           # 全要素生产率（TFP）
    subsidy=0.0,                # 失业补贴
    gov_purchase=0.0,          # 政府购买（新增）
    qe_amount=0.0,              # 量化宽松规模（新增）
    # ── 劳动力市场 ────────────────────────────────────
    job_search_cost=1.0,        # 求职现金消耗（摩擦成本）
    wage_bargain_strength=0.2, # 工资议价强度
    skill_wage_premium_high=0.5,  # 高技能工资溢价（+50%）
    skill_wage_premium_mid=0.2,   # 中技能溢价（+20%）
    # ── 信贷市场 ──────────────────────────────────────
    credit_score_min=300,
    credit_score_max=850,
    collateral_ratio=0.7,      # 抵押品折价率
    default_loss_rate=0.6,     # 违约损失率（银行实际损失比例）
    # ── 金融市场 ─────────────────────────────────────
    gordon_growth=0.02,         # 永续增长率（股价锚）
    price_stickiness=0.3,      # 价格粘性：30%企业每轮调价
    vol_window=10,             # 波动率滚动窗口
    # ── 外部冲击 ──────────────────────────────────────
    shock_prob=0.02,            # 每轮外生冲击概率
    # ── 宏观锚点 ──────────────────────────────────────
    gdp_target=1800.0,
    price_adjust_speed=0.08,
    stock_adjust_speed=0.025,
    # ── 风险参数 ──────────────────────────────────────
    default_threshold=0.5,
    bankruptcy_cycles=3,        # 连续N轮负现金流→破产
)


# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════

def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _draw_income_tier(rng: np.random.Generator) -> str:
    """帕累托加权抽取收入层（高收入抽取概率低，符合真实分布）"""
    r = rng.random()
    if r < 0.33:
        return "low"
    elif r < 0.67:
        return "middle"
    else:
        return "high"


def _draw_industry(rng: np.random.Generator) -> Industry:
    r = rng.random()
    if r < 0.40:
        return Industry.MANUFACTURING
    elif r < 0.75:
        return Industry.SERVICE
    else:
        return Industry.TECH


def _draw_lifecycle(rng: np.random.Generator) -> FirmLifecycle:
    r = rng.random()
    cum = 0.0
    for stage, w in LIFECYCLE_WEIGHTS.items():
        cum += w
        if r < cum:
            return stage
    return FirmLifecycle.MATURE


def _draw_trader_strategy(rng: np.random.Generator) -> TraderStrategy:
    r = rng.random()
    if r < 0.35:
        return TraderStrategy.MOMENTUM
    elif r < 0.55:
        return TraderStrategy.VALUE
    elif r < 0.80:
        return TraderStrategy.NOISE
    else:
        return TraderStrategy.MARKET_MAKER


# ══════════════════════════════════════════════════════════════
# 外部冲击事件
# ══════════════════════════════════════════════════════════════

class Shock(Enum):
    """外生冲击类型"""
    OIL_CRISIS = "oil_crisis"
    TECH_BREAKTHROUGH = "tech_breakthrough"
    DEMAND_SLOWDOWN = "demand_slowdown"
    TRADE_WAR = "trade_war"
    BANKING_PANIC = "banking_panic"
    RECOVERY = "recovery"


SHOCK_EFFECTS = {
    Shock.OIL_CRISIS: {
        "desc": "石油危机：生产成本暴涨",
        "productivity": lambda p: p * 0.70,
        "stock_sentiment": -0.15,   # 股市情绪负面
        "consumption_delta": -0.10, # 消费意愿下降
    },
    Shock.TECH_BREAKTHROUGH: {
        "desc": "技术突破：TFP大幅提升",
        "productivity": lambda p: p * 1.40,
        "stock_sentiment": 0.20,
        "consumption_delta": 0.05,
    },
    Shock.DEMAND_SLOWDOWN: {
        "desc": "需求骤降（如疫情）",
        "productivity": lambda p: p * 0.85,
        "stock_sentiment": -0.20,
        "consumption_delta": -0.30,
    },
    Shock.TRADE_WAR: {
        "desc": "贸易战：出口中断",
        "productivity": lambda p: p * 0.90,
        "stock_sentiment": -0.10,
        "consumption_delta": 0.02,  # 国内替代消费微增
    },
    Shock.BANKING_PANIC: {
        "desc": "银行恐慌：储户挤兑",
        "stock_sentiment": -0.25,
        "consumption_delta": -0.05,
        "bank_run": True,          # 触发银行挤兑
    },
    Shock.RECOVERY: {
        "desc": "经济复苏：需求回暖",
        "productivity": lambda p: p * 1.15,
        "stock_sentiment": 0.15,
        "consumption_delta": 0.15,
    },
}


# ══════════════════════════════════════════════════════════════
# 代理人
# ══════════════════════════════════════════════════════════════

class Household(Agent):
    """
    消费者 v3.0 - 异质性版本

    核心异质性：
      income_tier  → 决定 MPC、风险偏好、初始财富、技能分布
      skill_level  → 决定就业匹配、高薪岗位机会
      risk_aversion → 决定股票持有比例、借贷意愿
      credit_score  → 决定银行贷款批准概率

    行为顺序：
      earn_wage() → pay_taxes() → repay_loan() → deposit()
      → consume() → invest() → search_job()
    """

    def __init__(self, model: EconomyModel):
        super().__init__(model)

        # ── 城市归属（50/50 随机分配）───────────────
        self.city = self.random.choice(list(City))

        # ── 异质性属性 ───────────────────────────────
        self.income_tier = _draw_income_tier(self.random)
        p = INCOME_TIER_PARAMS[self.income_tier]
        self.mpc = p["mpc"]                          # 边际消费倾向
        self.risk_aversion = p["risk_aversion"]      # 风险厌恶系数
        self.consume_prob = min(0.95, p["mpc"])      # 消费概率≈MPC
        self.stock_buy_prob = p["stock_buy_prob"]
        self.stock_sell_prob = p["stock_sell_prob"]

        # 技能等级：0=低技能 / 1=中技能 / 2=高技能
        skill = self.random.choices(
            [0, 1, 2], weights=list(p["skill_weights"].values()), k=1
        )[0]
        self.skill_level = skill  # 0=低 / 1=中 / 2=高

        # 初始现金（帕累托分布偏向低现金）
        lo, hi = p["initial_cash_range"]
        self.cash = self.random.uniform(lo, hi)

        # ── 状态变量 ───────────────────────────────
        self.goods: int = 0
        self.salary: float = 0.0
        self.employed: bool = False
        self.employer: Optional["Firm"] = None
        self.loan_principal: float = 0.0
        self.shares_owned: int = 0
        self.cost_basis: float = 0.0   # 持股成本（移动平均买入价），用于计算资本利得
        self.wealth: float = self.cash
        # 信用评分：初始基于技能水平（高技能→高信用）
        self.credit_score: float = 500 + self.skill_level * 100
        # 历史收入（用于信用评估）
        self.income_history: list[float] = []

    # ── 子行为 ─────────────────────────────────────────────

    def earn_wage(self) -> None:
        """领取工资（含个税）"""
        if not self.employed or self.salary <= 0:
            return
        wage = self.salary
        self.cash += wage
        self.income_history.append(wage)
        if len(self.income_history) > 12:
            self.income_history.pop(0)

    def pay_taxes(self) -> None:
        """缴纳个人所得税"""
        if self.salary <= 0:
            return
        tax = self.salary * self.model.tax_rate
        self.cash -= tax
        self.model.govt_revenue += tax

    def repay_loan(self) -> None:
        """定期偿还贷款（含利息）"""
        if self.loan_principal <= 0:
            return
        rate = self.model.base_interest_rate
        interest = self.loan_principal * rate
        repayment = min(self.model.min_wage * 0.1, self.loan_principal + interest)
        if self.cash >= repayment:
            self.cash -= repayment
            self.loan_principal = max(0.0, self.loan_principal - max(0.0, repayment - interest))

    def deposit(self) -> None:
        """存款：MPC越高→存款比例越低（高收入存更多）"""
        if self.cash <= 5:
            return
        # 高MPC（低收入）几乎不存款；低MPC（高收入）存款更多
        deposit_rate = (1 - self.mpc) * 0.3
        deposit = self.cash * deposit_rate
        if deposit > 1 and self.model.banks:
            self.cash -= deposit
            bank = self.random.choice(self.model.banks)
            bank.reserves += deposit
            bank.deposits += deposit

    def consume(self) -> None:
        """
        差异化消费：按MPC决定是否消费
        高MPC（低收入）：几乎必定消费（生存型）
        低MPC（高收入）：消费概率低（储蓄/投资型）
        消费时同步从有库存的企业扣减库存，闭环商品市场。
        """
        if self.goods <= 0:
            return
        consume_prob = min(0.98, self.mpc + self.random.uniform(-0.05, 0.05))
        if self.random.random() < consume_prob:
            self.goods -= 1
            # 随机选择一家有库存的企业，扣其库存、加其营收，闭环商品市场
            firms_with_stock = self.model._cache.get('firms_with_stock', [])
            if firms_with_stock:
                f = self.random.choice(firms_with_stock)
                f.inventory -= 1
                tax = f.price * self.model.tax_rate
                after_tax = f.price * (1 - self.model.tax_rate)
                f.cash += after_tax
                self.model.govt_revenue += tax  # 税收归集，修复泄漏

    def invest(self) -> None:
        """股票投资：风险厌恶决定是否参与股市"""
        price = self.model.stock_price
        # 风险厌恶高→几乎不参与
        if self.random.random() > (1 - self.risk_aversion) * 0.5 + 0.1:
            return
        # 买入（移动平均成本基准）
        if self.cash >= price * 2 and self.random.random() < self.stock_buy_prob:
            shares_bought = 2
            cost = price * shares_bought
            self.cash -= cost
            # 更新移动平均成本
            total_cost = self.cost_basis * self.shares_owned + cost
            self.shares_owned += shares_bought
            self.cost_basis = total_cost / self.shares_owned if self.shares_owned > 0 else 0.0
            self.model.buy_orders += shares_bought
        # 卖出（资本利得税）
        elif self.shares_owned > 1 and self.random.random() < self.stock_sell_prob:
            shares_sold = 1
            proceeds = price * shares_sold
            cost = self.cost_basis * shares_sold
            capital_gain = max(0.0, proceeds - cost)
            tax = capital_gain * self.model.capital_gains_tax
            self.cash += proceeds - tax
            self.model.govt_revenue += tax
            self.model.capital_gains_tax_revenue += tax
            self.shares_owned -= shares_sold
            # 成本基准不变（平均成本法）
            self.model.sell_orders += shares_sold

    def search_job(self) -> None:
        """找工作（摩擦性失业：消耗现金）"""
        if self.employed:
            return
        # 求职现金消耗（摩擦成本）
        if self.cash > DEFAULTS["job_search_cost"]:
            self.cash -= DEFAULTS["job_search_cost"]

        if self.model.firms and self.random.random() < 0.35:
            # 按技能等级匹配岗位
            candidates = [
                f for f in self.model._cache.get('firms_with_jobs', [])
                if f.wage_offer >= self.model.min_wage
            ]
            if candidates:
                firm = self.random.choice(candidates)
                firm.open_positions -= 1
                self.employed = True
                self.employer = firm
                # 工资议价：失业率越高，议价能力越弱
                ur = self.model.unemployment
                wpremium = DEFAULTS["skill_wage_premium_mid"] if self.skill_level == 1 \
                    else DEFAULTS["skill_wage_premium_high"] if self.skill_level == 2 else 0.0
                wage = firm.wage_offer * (1 - ur * DEFAULTS["wage_bargain_strength"]) * (1 + wpremium)
                self.salary = max(self.model.min_wage, wage)

    def update_wealth(self) -> None:
        """财富 = 现金 - 负债 + 股票市值"""
        stock_value = self.shares_owned * self.model.stock_price
        self.wealth = self.cash - self.loan_principal + stock_value

    def _consider_migration(self) -> None:
        """跨城迁移决策（Phase 2 + 3）"""
        # 每轮有 3% 概率考虑迁移
        if self.random.random() > 0.03:
            return
        # 迁移成本：至少需要 50 现金
        if self.cash < 50:
            return
        my_city = self.city
        other_city = City.CITY_B if my_city == City.CITY_A else City.CITY_A
        # 失业率差
        my_unemp = self.model.city_a_unemp if my_city == City.CITY_A else self.model.city_b_unemp
        other_unemp = self.model.city_b_unemp if my_city == City.CITY_A else self.model.city_a_unemp
        unemp_diff = my_unemp - other_unemp
        # 最低工资差（Phase 3）
        my_min_wage = self.model.city_a_min_wage if my_city == City.CITY_A else self.model.city_b_min_wage
        other_min_wage = self.model.city_b_min_wage if my_city == City.CITY_A else self.model.city_a_min_wage
        wage_diff = other_min_wage - my_min_wage
        # 综合迁移评分
        migrate_score = unemp_diff * 3 + wage_diff * 0.5
        if migrate_score > 0.15 and self.random.random() < 0.5:
            self.city = other_city
            self.cash -= 50  # 迁移成本
            self.employed = False  # 摩擦性失业
            self.employer = None

    def update_credit_score(self) -> None:
        """
        信用评分更新：
          - 收入稳定性（标准差越小越好）
          - 当前负债比
          - 历史记录长度
        """
        if len(self.income_history) < 2:
            return
        income_mean = np.mean(self.income_history)
        income_std = np.std(self.income_history)
        coeff_var = _safe_div(income_std, income_mean + 1e-6, 1.0)
        stability = _clamp(1.0 - coeff_var, 0.0, 1.0)
        debt_ratio = _safe_div(self.loan_principal, self.wealth + 1e-6, 0.0)
        debt_penalty = _clamp(1.0 - debt_ratio * 0.5, 0.5, 1.0)
        # 信用评分 = 基准500 + 稳定性权重(±200) × 稳定性 + 负债调整
        delta = (stability - 0.5) * 200 * debt_penalty
        self.credit_score = _clamp(self.credit_score + delta * 0.1, DEFAULTS["credit_score_min"], DEFAULTS["credit_score_max"])

    # ── 主循环 ─────────────────────────────────────────────

    def step(self) -> None:
        self.earn_wage()
        self.pay_taxes()
        self.repay_loan()
        self.deposit()
        self.consume()
        self.invest()
        self.search_job()
        self.update_credit_score()
        self.update_wealth()
        self._consider_migration()


class Firm(Agent):
    """
    企业 v3.0 - 行业异质性 + 生命周期

    核心异质性：
      industry        → 差异化生产函数、定价策略、裁员率
      lifecycle       → 决定分红率、招聘强度、破产风险

    每个企业有独立定价权（price_stickiness 控制调价频率）
    """

    def __init__(self, model: EconomyModel):
        super().__init__(model)

        # ── 城市归属（50/50 随机分配）───────────────
        self.city = self.random.choice(list(City))

        # ── 异质性 ─────────────────────────────────
        self.industry = _draw_industry(self.random)
        self.lifecycle = _draw_lifecycle(self.random)
        ip = INDUSTRY_PARAMS[self.industry]

        # 初始现金受生命周期影响
        cash_lo, cash_hi = {
            FirmLifecycle.STARTUP: (50, 200),
            FirmLifecycle.GROWTH: (200, 500),
            FirmLifecycle.MATURE: (400, 900),
            FirmLifecycle.DECLINE: (100, 400),
        }[self.lifecycle]
        self.cash = self.random.uniform(cash_lo, cash_hi)

        self.employees: int = 0
        self.wage_offer: float = max(model.min_wage, 8.0) * ip["wage_premium"]
        self.open_positions: int = self.random.randint(0, 4)
        self.production: float = 0.0
        self.inventory: float = 0.0
        self.loan_principal: float = 0.0
        self.wealth: float = self.cash
        self.dividend_per_share: float = 0.0
        self.default_probability: float = 0.0
        self.price: float = model.avg_price if model.avg_price > 1 else 10.0

        # 生命周期专属参数
        self.negative_cash_cycles: int = 0  # 连续负现金流轮次
        self.rnd_investment: float = 0.0    # 研发投入（科技业）
        self.capital_stock: float = 200.0   # 固定资产（制造业）

        # 价格粘性：上次调价距今轮次
        self.price_change_cooldown: int = 0

        # 行业参数缓存
        self._ind = ip

    # ── 子行为 ─────────────────────────────────────────────

    def hire(self) -> None:
        """招聘（受生命周期驱动：初创/衰退企业风格不同）"""
        if self.open_positions <= 0:
            return
        unemployed = self.model.unemployed_households
        # 高技能岗位优先匹配高技能工人
        skill_required = 1 if self.industry == Industry.TECH else 0
        candidates = [h for h in unemployed if h.skill_level >= skill_required]
        if not candidates:
            candidates = unemployed[:]

        n_hire = min(self.open_positions, len(candidates))
        for _ in range(n_hire):
            if candidates:
                h = self.random.choice(candidates)
                h.employed = True
                h.employer = self
                h.salary = self.wage_offer
                self.employees += 1
                self.open_positions -= 1
                candidates.remove(h)

    def produce(self) -> None:
        """
        差异化生产函数：

        制造业：capital_intensity 高，依赖固定资产
          production = sqrt(capital) × TFP × (employees^0.6)

        服务业：轻资产，人力驱动
          production = employees × wage_base × TFP

        科技：R&D 驱动，高波动
          production = employees × TFP × (1 + rnd_investment/cash)
        """
        T = self.model.productivity
        noise_std = self._ind["productivity_noise"]

        if self.industry == Industry.MANUFACTURING:
            self.production = (
                np.sqrt(max(1.0, self.capital_stock))
                * T
                * (max(1, self.employees) ** 0.6)
                + self.random.gauss(0, noise_std)
            )
            # 资本折旧
            self.capital_stock *= 0.98

        elif self.industry == Industry.SERVICE:
            self.production = (
                max(1, self.employees)
                * self.model.min_wage
                * T
                + self.random.gauss(0, noise_std)
            )

        elif self.industry == Industry.TECH:
            # 科技业：研发投入转化为生产力（随机成功/失败）
            rnd_success = self.random.gauss(1.0, 0.5)
            rnd_success = max(0.1, rnd_success)
            self.production = (
                max(1, self.employees)
                * T
                * (1 + self.rnd_investment / max(1, self.cash) * rnd_success)
                + self.random.gauss(0, noise_std)
            )
            # 研发投入（利润的固定比例）
            self.rnd_investment = max(0.0, self.cash * 0.05 * rnd_success)

        self.production = max(0.0, self.production)
        self.inventory += self.production

    def price_goods(self) -> None:
        """
        差异化定价 + 价格粘性：

        1. 计算目标价格（基于边际成本 + 目标毛利率）
        2. 价格粘性：仅 price_stickiness 比例的企业会调价
        3. 伯特兰竞争：参考竞争对手平均价格
        """
        if self.price_change_cooldown > 0:
            self.price_change_cooldown -= 1
            return

        # 价格粘性：仅部分企业每轮调价
        if self.random.random() > DEFAULTS["price_stickiness"]:
            return

        competitors = [f for f in self.model._cache.get('firms_by_industry', {}).get(self.industry, []) if f is not self]
        avg_competitor_price = np.mean([f.price for f in competitors]) if competitors else self.model.avg_price

        # 目标价格：库存多→降价去库存；库存少→涨价
        cost_per_unit = self._ind["capital_intensity"] * self.model.min_wage * 0.5
        if self.inventory > 15:
            # 去库存：价格下浮最多20%
            self.price = max(cost_per_unit, avg_competitor_price * 0.90)
        elif self.inventory < 3:
            # 供不应求：价格上浮最多15%
            self.price = avg_competitor_price * 1.10
        else:
            # 正常：向竞争对手均价靠拢
            self.price = 0.5 * self.price + 0.5 * avg_competitor_price

        self.price = max(1.0, self.price)
        # 调价后进入冷却期（模拟菜单成本）
        self.price_change_cooldown = self.random.randint(1, 3)

    def sell_goods(self) -> None:
        """向居民销售商品（按价格排序：低价优先被购买）+ 城际贸易追踪（Phase 4）"""
        if self.inventory <= 0:
            return
        # 价格敏感型消费：优先买便宜的
        firms_sorted = sorted(self.model.firms, key=lambda f: f.price)
        buyers = self.random.sample(
            self.model.households, min(len(self.model.households), 6)
        )
        for h in buyers:
            if h.cash >= self.price and h.goods < 10 and self.inventory > 0:
                h.cash -= self.price
                h.goods += 1
                tax = self.price * self.model.tax_rate
                after_tax = self.price * (1 - self.model.tax_rate)
                self.cash += after_tax
                self.model.govt_revenue += tax  # 税收归集，修复泄漏
                self.inventory -= 1
                # 城际贸易：企业与消费者不在同一城市
                if h.city != self.city:
                    if self.city == City.CITY_A:
                        self.model.city_a_exports += after_tax
                        self.model.city_b_imports += after_tax
                    else:
                        self.model.city_b_exports += after_tax
                        self.model.city_a_imports += after_tax

    def pay_dividend(self) -> None:
        """生命周期决定分红率：成熟期高分红，初创期不分"""
        if self.cash <= 50:
            return
        div_ratio = self._ind["div_ratio"]
        if self.lifecycle == FirmLifecycle.STARTUP:
            div_ratio *= 0.0   # 初创：不分红，留存扩产
        elif self.lifecycle == FirmLifecycle.DECLINE:
            div_ratio *= 1.5   # 衰退：变现资产

        profit = self.cash * div_ratio
        self.cash -= profit
        self.dividend_per_share = _safe_div(profit, 50)
        self.model.total_dividends += profit

    def update_wage(self) -> None:
        """行业 + 生命周期决定工资调整策略"""
        ur = self.model.unemployment
        # 失业率高→压低工资（劳动市场宽松）；失业率低→提高工资（抢人）
        if ur > 0.15:
            self.wage_offer = _clamp(self.wage_offer * 0.97, self.model.min_wage, 50.0)
        elif self.inventory > 20 and self.lifecycle in (FirmLifecycle.GROWTH, FirmLifecycle.MATURE):
            self.wage_offer = _clamp(self.wage_offer * 1.04, self.model.min_wage, 50.0)

    def adjust_workforce(self) -> None:
        """生命周期 + 经济状态决定裁员/扩产"""
        if self.employees == 0:
            return
        # 衰退期或库存严重过剩时裁员
        layoff_prob = self._ind["layoff_prob"]
        if self.lifecycle == FirmLifecycle.DECLINE:
            layoff_prob *= 2.0
        if self.inventory < 2:
            layoff_prob *= 3.0

        if self.random.random() < layoff_prob:
            n_layoff = min(self.employees, self.random.randint(1, 3))
            # 随机裁一名员工
            employed = self.model._cache.get('employees_of', {}).get(id(self), [])
            if employed:
                h = self.random.choice(employed)
                h.employed = False
                h.employer = None
                h.salary = 0.0
                self.employees -= n_layoff

    def apply_for_loan(self) -> None:
        """申请贷款（有信用审核）"""
        if self.loan_principal >= DEFAULTS.get("loan_cap", 500.0):
            return
        wage_bill = self.employees * self.wage_offer
        if self.cash >= wage_bill * 0.5:
            return

        # 信用评分决定是否批准
        cs = getattr(self, "credit_score", 600)
        if cs < 400:
            return  # 信用太差，拒绝

        loan = min(DEFAULTS.get("bank_loan_amount", 20.0), DEFAULTS.get("loan_cap", 500.0) - self.loan_principal)
        if loan <= 0:
            return

        self.cash += loan
        self.loan_principal += loan
        self.model.total_loans_outstanding += loan
        if self.model.banks:
            bank = self.random.choice(self.model.banks)
            bank.total_loans += loan
            bank.reserves -= loan

    def repay_loan(self) -> None:
        """偿还贷款 + 违约判定"""
        if self.loan_principal <= 0:
            return
        rate = self.model.base_interest_rate
        interest = self.loan_principal * rate * 0.1
        repayment = min(interest + 5, self.cash)

        if self.cash >= repayment:
            self.cash -= repayment
            self.loan_principal -= max(0.0, repayment - interest)
        elif self.random.random() < self.default_probability:
            self._trigger_default()

    def _trigger_default(self) -> None:
        """违约触发：同步通知银行，增加全系统风险"""
        logger.warning(
            "企业%d违约！行业=%s，周期=%d，现金=%.1f，负债=%.1f",
            self.unique_id, self.industry.value, self.negative_cash_cycles,
            self.cash, self.loan_principal,
        )
        if self.model.banks:
            bank = self.random.choice(self.model.banks)
            # 违约损失率（银行实际承受）
            actual_loss = self.loan_principal * DEFAULTS["default_loss_rate"]
            bank.total_loans -= self.loan_principal
            bank.bad_debts += actual_loss
        self.model.total_loans_outstanding -= self.loan_principal
        self.model.default_count += 1
        self.model.systemic_risk = min(1.0, self.model.systemic_risk + 0.05)
        self.loan_principal = 0.0

    def check_bankruptcy(self) -> bool:
        """
        破产判定：连续N轮现金流为负
        触发：模型从 agents 列表移除
        """
        if self.cash < 0:
            self.negative_cash_cycles += 1
        else:
            self.negative_cash_cycles = 0

        if self.negative_cash_cycles >= DEFAULTS["bankruptcy_cycles"]:
            logger.warning(
                "企业%d破产！行业=%s，生命周期=%s",
                self.unique_id, self.industry.value, self.lifecycle.value,
            )
            # 通知银行：企业消失，贷款清零
            if self.loan_principal > 0:
                self.model.total_loans_outstanding -= self.loan_principal
                if self.model.banks:
                    bank = self.random.choice(self.model.banks)
                    bank.total_loans -= self.loan_principal
                    bank.bad_debts += self.loan_principal * 0.8  # 破产损失率80%

            # 解雇员工
            for h in self.model.households:
                if h.employer is self:
                    h.employed = False
                    h.employer = None
                    h.salary = 0.0

            self.model.firms.remove(self)
            self.model.agents.remove(self)
            self.model.bankrupt_count += 1
            return True
        return False

    def update_default_probability(self) -> None:
        """违约概率 = 1 - 现金/负债（更敏感）"""
        total_debt = self.loan_principal + 1e-6
        self.default_probability = _clamp(1.0 - self.cash / total_debt, 0.0, 1.0)

    def update_credit_score(self) -> None:
        """企业信用评分：基于利润率 + 负债率"""
        profit_ratio = _safe_div(self.production, self.employees * self.wage_offer + 1.0, 1.0)
        debt_ratio = _safe_div(self.loan_principal, self.wealth + 1.0, 0.0)
        # 信用 = 基准600 + 盈利调整 - 负债惩罚
        delta = (profit_ratio - 1.0) * 50 - debt_ratio * 100
        self.credit_score = _clamp(
            getattr(self, "credit_score", 600) + delta * 0.1,
            DEFAULTS["credit_score_min"],
            DEFAULTS["credit_score_max"],
        )

    def update_wealth(self) -> None:
        """企业财富 = 现金 - 负债 + 库存价值 + 固定资产"""
        inventory_value = self.inventory * self.price
        self.wealth = self.cash - self.loan_principal + inventory_value + self.capital_stock * 0.5

    # ── 主循环 ─────────────────────────────────────────────

    def step(self) -> None:
        if self.check_bankruptcy():
            return  # 已破产，不再执行
        self.hire()
        self.produce()
        self.price_goods()
        self.sell_goods()
        self.pay_dividend()
        self.update_wage()
        self.adjust_workforce()
        self.update_default_probability()
        self.update_credit_score()
        self.apply_for_loan()
        self.repay_loan()
        self.update_wealth()
        self._consider_migration()

    def _consider_migration(self) -> None:
        """企业跨城迁移（Phase 2 + 3）"""
        # 每轮 2% 概率考虑迁移
        if self.random.random() > 0.02:
            return
        # 迁移成本：需要至少 200 现金
        if self.cash < 200:
            return
        my_city = self.city
        other_city = City.CITY_B if my_city == City.CITY_A else City.CITY_A
        # 税率差（Phase 3）
        my_tax = self.model.city_a_tax if my_city == City.CITY_A else self.model.city_b_tax
        other_tax = self.model.city_b_tax if my_city == City.CITY_A else self.model.city_a_tax
        tax_diff = my_tax - other_tax  # 正值：当前税率高，想迁走
        # 失业率差（劳动力成本）
        my_unemp = self.model.city_a_unemp if my_city == City.CITY_A else self.model.city_b_unemp
        other_unemp = self.model.city_b_unemp if my_city == City.CITY_A else self.model.city_a_unemp
        labor_diff = other_unemp - my_unemp  # 正值：另一城市劳动力更便宜
        # 综合迁移概率
        migrate_score = tax_diff * 5 + labor_diff * 2
        if migrate_score > 0.05 and self.random.random() < 0.3:
            self.city = other_city
            self.cash -= 200  # 迁移成本
            # 20% 员工离职
            if self.employees > 0:
                n_quit = max(1, int(self.employees * 0.2))
                employed = self.model._cache.get('employees_of', {}).get(id(self), [])
                for h in self.random.sample(employed, min(n_quit, len(employed))):
                    h.employed = False
                    h.employer = None
                    self.employees -= 1


class Bank(Agent):
    """
    银行 v3.0 - 差异化利率 + 巴塞尔合规

    核心异质性：
      risk_appetite    → 高则放贷激进、利率高；低则保守、利率低
      lending_spread   → 在基准利率上的加点
      default_tolerance → 坏账容忍度
    """

    def __init__(self, model: EconomyModel):
        super().__init__(model)

        # ── 城市归属（50/50 随机分配）───────────────
        self.city = self.random.choice(list(City))

        # ── 随机选择银行类型 ─────────────────────────
        bank_type = "aggressive" if self.random.random() < 0.5 else "conservative"
        bp = BANK_PARAMS[bank_type]
        self.bank_type = bank_type
        self.risk_appetite = bp["risk_appetite"]
        self.lending_spread = bp["lending_spread"]
        self.default_tolerance = bp["default_tolerance"]
        self.loan_amount = bp["loan_amount"]
        self.reserves: float = bp["initial_reserves"]
        self.deposits: float = 0.0
        self.total_loans: float = 0.0
        self.bad_debts: float = 0.0
        self.wealth: float = self.reserves
        # 资本金（用于巴塞尔协议）
        self.capital: float = bp["initial_reserves"] * 0.1
        self._loans: dict[int, float] = {}  # 记录本银行的贷款明细（borrower_id → loan_amount），修复越界问题

    def _effective_rate(self, borrower: Agent) -> float:
        """
        差异化利率 = 基准利率 + 信用利差 + 银行风险偏好
        信用评分低 → 利率高（风险溢价）
        银行保守型 → 利差高
        """
        base = self.model.base_interest_rate
        credit_score = getattr(borrower, "credit_score", 600)
        # 信用评分映射到利差：[850分→+0%，300分→+5%]
        score_penalty = (DEFAULTS["credit_score_max"] - credit_score) / (DEFAULTS["credit_score_max"] - DEFAULTS["credit_score_min"]) * 0.05
        return base + self.lending_spread + score_penalty

    def pay_deposit_interest(self) -> None:
        """支付存款利息（从储备中扣除）"""
        rate = self.model.base_interest_rate * 0.5  # 存款利率通常低于基准
        interest = self.deposits * rate
        self.reserves -= interest

    def lend(self) -> None:
        """
        差异化放贷：
          - 按信用评分筛选
          - 按风险偏好决定规模
          - 资本金充足率合规检查（巴塞尔协议）
        """
        if self.reserves <= 50:
            return

        # 资本金充足率检查（风险加权资产 = 贷款额 × 1.0）
        capital_ratio = self.capital / max(1.0, self.total_loans)
        if capital_ratio < 0.08:
            # 低于8%：强制收缩（巴塞尔III）
            shrink = self.loan_amount * 0.3
            self.reserves += shrink
            self.total_loans -= shrink
            return

        borrowers = list(self.model.households) + list(self.model.firms)
        max_loans = min(3, len(borrowers))

        for _ in range(max_loans):
            if self.reserves <= self.loan_amount:
                break
            b = self.random.choice(borrowers)
            existing = getattr(b, "loan_principal", 0.0)
            cap = DEFAULTS.get("household_loan_cap", 200.0) if isinstance(b, Household) \
                else DEFAULTS.get("loan_cap", 500.0)
            if existing >= cap:
                continue

            # 信用审核
            cs = getattr(b, "credit_score", 600)
            if cs < 400:
                continue  # 拒贷

            amount = self.loan_amount * (0.7 + self.risk_appetite * 0.6)
            amount = min(amount, cap - existing)
            if amount <= 0:
                continue

            b.cash += amount
            b.loan_principal = existing + amount
            if not hasattr(b, 'creditor_bank') or not b.creditor_bank:
                b.creditor_bank = set()
            b.creditor_bank.add(id(self))  # 记录债主银行
            self._loans[id(b)] = amount
            self.reserves -= amount
            self.total_loans += amount
            self.model.total_loans_outstanding += amount

    def update_bad_debts(self) -> None:
        """
        坏账 = Σ(本银行债务人违约概率 × 本银行对其贷款额 × 损失率)
        修复：只统计自己发放的贷款，不越界统计其他银行的贷款
        """
        total = 0.0
        for borrower_id, loan_principal in list(self._loans.items()):
            # 从模型中查找对应债务人
            borrower = next(
                (a for a in list(self.model.households) + list(self.model.firms)
                 if id(a) == borrower_id), None
            )
            if borrower is None:
                # 债务人已消失（破产），全额计坏账
                total += loan_principal * DEFAULTS["default_loss_rate"]
            else:
                prob = getattr(borrower, "default_probability", 0.0)
                total += prob * loan_principal * DEFAULTS["default_loss_rate"]
        self.bad_debts = total

        # 更新资本金（利润留存）
        self.capital = max(self.capital * 0.99, self.reserves * 0.1)

    def update_wealth(self) -> None:
        """银行财富 = 准备金 + 有效贷款 - 坏账"""
        effective_loans = max(0.0, self.total_loans - self.bad_debts)
        self.wealth = self.reserves + effective_loans

    def step(self) -> None:
        self.pay_deposit_interest()
        self.lend()
        self.update_bad_debts()
        self.update_wealth()


class Trader(Agent):
    """
    交易员 v3.0 - 四种策略

    动量（Momentum）：追涨杀跌
    价值（Value）：基于戈登模型内在价值
    噪声（Noise）：随机交易（散户行为）
    做市商（Market Maker）：双向挂单赚价差
    """

    def __init__(self, model: EconomyModel):
        super().__init__(model)

        # ── 城市归属（50/50 随机分配）───────────────
        self.city = self.random.choice(list(City))

        self.strategy = _draw_trader_strategy(self.random)
        self.cash: float = self.random.uniform(300, 1000)
        self.shares: int = self.random.randint(0, 20)
        self.cost_basis: float = float(model.stock_price)  # 移动平均成本基准
        self.momentum: float = 0.0
        # 价值投资者：持有对内在价值的估计
        self.intrinsic_value_estimate: float = model.stock_price
        # 做市商：买卖价差
        self.bid_ask_spread: float = 0.02
        self.wealth: float = self.cash + self.shares * model.stock_price
        self.realized_gains: float = 0.0   # 已实现收益（用于正确计算财富）

    def _update_momentum(self, price: float, prev: float) -> None:
        if prev <= 0:
            return
        ret = (price - prev) / prev
        self.momentum = 0.7 * self.momentum + 0.3 * ret

    def _gordon_value(self, dividend: float, rate: float) -> float:
        """戈登增长模型：P = D / (r - g)"""
        g = DEFAULTS["gordon_growth"]
        return _safe_div(dividend, rate - g, 100.0)

    # ── 四种交易策略 ─────────────────────────────────────────

    def _trade_momentum(self, price: float) -> None:
        m = self.model
        self._update_momentum(price, m.prev_stock_price)

        buy_prob = _clamp(0.3 + self.momentum * 2.5, 0.0, 1.0)
        sell_prob = _clamp(0.3 - self.momentum * 2.5, 0.0, 1.0)

        if self.cash >= price * 2 and self.random.random() < buy_prob:
            cost = price * 2
            self.cash -= cost
            # 移动平均成本基准
            old_cost = self.cost_basis * self.shares
            self.shares += 2
            self.cost_basis = (old_cost + cost) / self.shares
            m.buy_orders += 2

        # 止损（全卖）
        prev = m.prev_stock_price
        if prev > 0 and (prev - price) / prev > 0.05 and self.shares > 0:
            self._sell(self.shares, price)
        elif self.shares > 0 and self.random.random() < sell_prob:
            self._sell(1, price)

    def _trade_value(self, price: float) -> None:
        """价值投资：内在价值低估则买，高估则卖"""
        m = self.model
        # 戈登模型估计内在价值
        avg_div_per_share = m.total_dividends / max(1, len(m.firms) * 50)
        intrinsic = self._gordon_value(avg_div_per_share, m.base_interest_rate)
        # 平滑估计
        self.intrinsic_value_estimate = 0.7 * self.intrinsic_value_estimate + 0.3 * intrinsic

        # 折价20%以上 → 买入；溢价20%以上 → 卖出
        if price < self.intrinsic_value_estimate * 0.80 and self.cash >= price:
            self.cash -= price
            self.shares += 1
            # 移动平均成本基准
            total_cost = self.cost_basis * (self.shares - 1) + price
            self.cost_basis = total_cost / self.shares
            m.buy_orders += 1
        elif price > self.intrinsic_value_estimate * 1.20 and self.shares > 0:
            self._sell(1, price)

    def _trade_noise(self, price: float) -> None:
        """噪声交易：随机买卖（模拟散户非理性行为）"""
        m = self.model
        if self.cash >= price and self.random.random() < 0.2:
            cost = price
            self.cash -= cost
            total_cost = self.cost_basis * self.shares + cost
            self.shares += 1
            self.cost_basis = total_cost / self.shares
            m.buy_orders += 1
        if self.shares > 0 and self.random.random() < 0.18:
            self._sell(1, price)

    def _trade_market_maker(self, price: float) -> None:
        """做市商：双向挂单，赚取买卖价差"""
        m = self.model
        spread = self.bid_ask_spread
        bid = price * (1 - spread)
        ask = price * (1 + spread)

        # 市价单：假设买卖均按当前价格成交
        # 买入
        if self.cash >= ask and self.random.random() < 0.4:
            self.cash -= ask
            self.shares += 1
            total_cost = self.cost_basis * (self.shares - 1) + ask
            self.cost_basis = total_cost / self.shares
            m.buy_orders += 1
        # 卖出
        if self.shares > 0 and self.random.random() < 0.4:
            self._sell(1, bid)

    def trade(self) -> None:
        """根据策略类型执行交易"""
        price = self.model.stock_price
        if self.strategy == TraderStrategy.MOMENTUM:
            self._trade_momentum(price)
        elif self.strategy == TraderStrategy.VALUE:
            self._trade_value(price)
        elif self.strategy == TraderStrategy.NOISE:
            self._trade_noise(price)
        elif self.strategy == TraderStrategy.MARKET_MAKER:
            self._trade_market_maker(price)

    def _sell(self, n: int, price: float) -> None:
        """卖出 n 股，含资本利得税，修复税收泄漏"""
        if n <= 0 or self.shares < n:
            return
        proceeds = price * n
        cost = self.cost_basis * n
        gain = max(0.0, proceeds - cost)
        tax = gain * self.model.capital_gains_tax
        self.cash += proceeds - tax
        self.model.govt_revenue += tax
        self.model.capital_gains_tax_revenue += tax
        # 已实现收益累计（不含税）
        self.realized_gains += gain - tax
        self.shares -= n
        self.model.sell_orders += n

    def update_wealth(self) -> None:
        self.wealth = self.cash + self.shares * self.model.stock_price

    def step(self) -> None:
        self.trade()
        self.update_wealth()


# ══════════════════════════════════════════════════════════════
# 宏观指标
# ══════════════════════════════════════════════════════════════

def compute_gini(model: EconomyModel) -> float:
    """矢量化 Gini 系数（Phase 1 NumPy 优化）"""
    wealths = np.array([getattr(a, "wealth", 0.0) for a in model.households], dtype=float)
    if wealths.size == 0:
        return 0.0
    n = len(wealths)
    S = np.sum(wealths)
    if S == 0 or n < 2:
        return 0.0
    wealths = np.sort(wealths)
    i = np.arange(1, n + 1)
    G = (2.0 * np.sum(i * wealths) / (n * S)) - (n + 1) / n
    return float(np.clip(G, 0.0, 1.0))

"""
Mesa 经济沙盘 - 核心模型 v3.5
基于 FRB/US · NAWM · ABCE 框架设计思路

v3.0 核心升级：
  ┌─────────────────────────────────────────────────────────────┐
  │ 一、智能体异质性                                            │
  │   Household: 三层收入 / 风险偏好 / 技能等级 / 差异化MPC     │
  │   Firm:     三行业 × 三生命周期阶段                        │
  │   Bank:     差异化利率（风险偏好型 vs 保守型）              │
  │   Trader:   四策略（动量 / 价值 / 噪声 / 做市商）           │
  ├─────────────────────────────────────────────────────────────┤
  │ 二、市场精细化                                              │
  │   商品市场: 企业独立定价 + 伯特兰竞争 + 价格粘性            │
  │   劳动力:   技能匹配 + 失业率议价 + 摩擦性失业             │
  │   信贷:     信用评分 + 抵押品 + 违约传导链                  │
  │   股市:     戈登增长模型基本面锚 + 供需短期扰动            │
  ├─────────────────────────────────────────────────────────────┤
  │ 三、政策传导                                                │
  │   政府购买（GDP拉动）+ 资本利得税 + 量化宽松（QE）         │
  │   利率→融资成本→招聘→失业率→消费 传导链                   │
  ├─────────────────────────────────────────────────────────────┤
  │ 四、外部冲击                                                │
  │   外生冲击：石油危机 / 技术突破 / 需求骤降 / 贸易战       │
  │   内生螺旋：金融加速器效应（抵押品→保证金→抛售）         │
  └─────────────────────────────────────────────────────────────┘

Mesa 3.x 最佳实践：
  - 分阶段调度（Shock → Bank → Firm → Household → Trader → Model宏观）
  - Agent 分类缓存（避免 O(n²) 重复筛选）
  - 参数全部可配置（零魔法数字）
"""


import logging
from enum import Enum
from typing import Optional

import numpy as np
from mesa import Agent, Model
from mesa import DataCollector

logger = logging.getLogger("econ")


# ══════════════════════════════════════════════════════════════
# 全局默认值（唯一配置源）
# ══════════════════════════════════════════════════════════════

class City(Enum):
    """城市标签（双城竞争）"""
    CITY_A = "city_a"   # 城市 A（工业导向）
    CITY_B = "city_b"   # 城市 B（科技导向）


# ── 城市级参数（Phase 3：差异化政策）────────────────────────────
CITY_PARAMS = {
    City.CITY_A: {
        "corporate_tax_rate": 0.12,    # 企业税率（低税吸引企业）
        "min_wage": 7.5,               # 最低工资
        "subsidy_rate": 0.10,          # 补贴率
        "infrastructure": 0.8,         # 基建水平（影响生产效率）
    },
    City.CITY_B: {
        "corporate_tax_rate": 0.18,    # 企业税率（高税高福利）
        "min_wage": 6.8,               # 最低工资（劳动力便宜）
        "subsidy_rate": 0.05,          # 补贴率
        "infrastructure": 1.0,         # 基建水平（科技发达）
    },
}


class Industry(Enum):
    """企业所属行业"""
    MANUFACTURING = "manufacturing"   # 制造业：资本密集，边际成本高
    SERVICE = "service"               # 服务业：轻资产，人力密集
    TECH = "tech"                    # 科技：研发投入大，生产率波动高


class FirmLifecycle(Enum):
    """企业生命周期阶段"""
    STARTUP = "startup"      # 初创：高风险，负现金流，强融资需求
    GROWTH = "growth"       # 成长期：盈利扩张，高招聘需求
    MATURE = "mature"       # 成熟期：稳定分红，低风险
    DECLINE = "decline"     # 衰退：产能过剩，裁员/破产风险


class TraderStrategy(Enum):
    """交易员策略"""
    MOMENTUM = "momentum"           # 动量：追涨杀跌
    VALUE = "value"                 # 价值：基于内在价值低买高卖
    NOISE = "noise"                 # 噪声：随机交易（散户行为）
    MARKET_MAKER = "market_maker"   # 做市商：双向挂单，赚价差


# ─── 居民异质性参数 ───────────────────────────────────────────

# 三层收入分位：低(0-33%) / 中(33-67%) / 高(67-100%)
INCOME_TIER_PARAMS = {
    "low": dict(
        mpc=0.80,        # 凯恩斯：低收入MPC≈0.8，赚100花80
        risk_aversion=0.9,   # 高风险厌恶，偏好储蓄
        stock_buy_prob=0.05,
        stock_sell_prob=0.02,
        initial_cash_range=(30, 100),
        skill_weights={0: 0.7, 1: 0.25, 2: 0.05},  # 低收入：70%低技能
    ),
    "middle": dict(
        mpc=0.50,        # 中等收入MPC≈0.5
        risk_aversion=0.5,
        stock_buy_prob=0.10,
        stock_sell_prob=0.06,
        initial_cash_range=(100, 250),
        skill_weights={0: 0.3, 1: 0.50, 2: 0.20},
    ),
    "high": dict(
        mpc=0.20,        # 高收入MPC≈0.2，赚100花20
        risk_aversion=0.2,    # 低风险厌恶，愿意投资
        stock_buy_prob=0.25,
        stock_sell_prob=0.15,
        initial_cash_range=(250, 600),
        skill_weights={0: 0.05, 1: 0.35, 2: 0.60},  # 高收入：60%高技能
    ),
}

# ─── 企业异质性参数 ───────────────────────────────────────────

INDUSTRY_PARAMS = {
    Industry.MANUFACTURING: dict(
        capital_intensity=2.0,     # 资本密集度（影响边际成本）
        price_flexibility=0.3,    # 价格调整速度（价格粘性：低→慢）
        wage_premium=1.0,         # 工资溢价（相对基准）
        productivity_noise=2.5,    # 生产率波动
        div_ratio=0.03,           # 分红比例（低：留存利润扩产）
        layoff_prob=0.05,         # 裁员概率（经济差时）
    ),
    Industry.SERVICE: dict(
        capital_intensity=0.5,     # 轻资产
        price_flexibility=0.5,     # 价格较灵活
        wage_premium=0.9,
        productivity_noise=1.5,
        div_ratio=0.06,
        layoff_prob=0.03,
    ),
    Industry.TECH: dict(
        capital_intensity=0.3,    # 低资本，高研发
        price_flexibility=0.7,    # 高灵活性
        wage_premium=1.5,         # 科技人才溢价
        productivity_noise=4.0,   # 高波动（技术突破/失败）
        div_ratio=0.02,           # 科技股少分红（高增长留存）
        layoff_prob=0.08,         # 快速裁员调整
    ),
}

# 企业生命周期权重
LIFECYCLE_WEIGHTS = {
    FirmLifecycle.STARTUP: 0.15,
    FirmLifecycle.GROWTH: 0.30,
    FirmLifecycle.MATURE: 0.40,
    FirmLifecycle.DECLINE: 0.15,
}

# ─── 银行异质性参数 ───────────────────────────────────────────

BANK_PARAMS = {
    "aggressive": dict(   # 风险偏好型银行
        risk_appetite=0.8,
        lending_spread=0.05,   # 高利差：基准+5%
        default_tolerance=0.7, # 高容忍坏账
        loan_amount=30.0,       # 大额放贷
        initial_reserves=1200.0,
    ),
    "conservative": dict(  # 保守型银行
        risk_appetite=0.3,
        lending_spread=0.01,   # 低利差：基准+1%
        default_tolerance=0.3,
        loan_amount=15.0,
        initial_reserves=800.0,
    ),
}

# ─── 核心模型参数 ───────────────────────────────────────────

DEFAULTS = dict(
    n_households=25,
    n_firms=12,
    n_banks=2,
    n_traders=20,
    # ── 政策参数 ──────────────────────────────────────
    tax_rate=0.15,             # 所得税率
    capital_gains_tax=0.10,     # 资本利得税率
    base_interest_rate=0.05,   # 基准利率
    min_wage=7.0,               # 最低工资
    productivity=1.0,           # 全要素生产率（TFP）
    subsidy=0.0,                # 失业补贴
    gov_purchase=0.0,          # 政府购买（新增）
    qe_amount=0.0,              # 量化宽松规模（新增）
    # ── 劳动力市场 ────────────────────────────────────
    job_search_cost=1.0,        # 求职现金消耗（摩擦成本）
    wage_bargain_strength=0.2, # 工资议价强度
    skill_wage_premium_high=0.5,  # 高技能工资溢价（+50%）
    skill_wage_premium_mid=0.2,   # 中技能溢价（+20%）
    # ── 信贷市场 ──────────────────────────────────────
    credit_score_min=300,
    credit_score_max=850,
    collateral_ratio=0.7,      # 抵押品折价率
    default_loss_rate=0.6,     # 违约损失率（银行实际损失比例）
    # ── 金融市场 ─────────────────────────────────────
    gordon_growth=0.02,         # 永续增长率（股价锚）
    price_stickiness=0.3,      # 价格粘性：30%企业每轮调价
    vol_window=10,             # 波动率滚动窗口
    # ── 外部冲击 ──────────────────────────────────────
    shock_prob=0.02,            # 每轮外生冲击概率
    # ── 宏观锚点 ──────────────────────────────────────
    gdp_target=1800.0,
    price_adjust_speed=0.08,
    stock_adjust_speed=0.025,
    # ── 风险参数 ──────────────────────────────────────
    default_threshold=0.5,
    bankruptcy_cycles=3,        # 连续N轮负现金流→破产
)


# ══════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════

def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _draw_income_tier(rng: np.random.Generator) -> str:
    """帕累托加权抽取收入层（高收入抽取概率低，符合真实分布）"""
    r = rng.random()
    if r < 0.33:
        return "low"
    elif r < 0.67:
        return "middle"
    else:
        return "high"


def _draw_industry(rng: np.random.Generator) -> Industry:
    r = rng.random()
    if r < 0.40:
        return Industry.MANUFACTURING
    elif r < 0.75:
        return Industry.SERVICE
    else:
        return Industry.TECH


def _draw_lifecycle(rng: np.random.Generator) -> FirmLifecycle:
    r = rng.random()
    cum = 0.0
    for stage, w in LIFECYCLE_WEIGHTS.items():
        cum += w
        if r < cum:
            return stage
    return FirmLifecycle.MATURE


def _draw_trader_strategy(rng: np.random.Generator) -> TraderStrategy:
    r = rng.random()
    if r < 0.35:
        return TraderStrategy.MOMENTUM
    elif r < 0.55:
        return TraderStrategy.VALUE
    elif r < 0.80:
        return TraderStrategy.NOISE
    else:
        return TraderStrategy.MARKET_MAKER


# ══════════════════════════════════════════════════════════════
# 外部冲击事件
# ══════════════════════════════════════════════════════════════

class Shock(Enum):
    """外生冲击类型"""
    OIL_CRISIS = "oil_crisis"
    TECH_BREAKTHROUGH = "tech_breakthrough"
    DEMAND_SLOWDOWN = "demand_slowdown"
    TRADE_WAR = "trade_war"
    BANKING_PANIC = "banking_panic"
    RECOVERY = "recovery"


SHOCK_EFFECTS = {
    Shock.OIL_CRISIS: {
        "desc": "石油危机：生产成本暴涨",
        "productivity": lambda p: p * 0.70,
        "stock_sentiment": -0.15,   # 股市情绪负面
        "consumption_delta": -0.10, # 消费意愿下降
    },
    Shock.TECH_BREAKTHROUGH: {
        "desc": "技术突破：TFP大幅提升",
        "productivity": lambda p: p * 1.40,
        "stock_sentiment": 0.20,
        "consumption_delta": 0.05,
    },
    Shock.DEMAND_SLOWDOWN: {
        "desc": "需求骤降（如疫情）",
        "productivity": lambda p: p * 0.85,
        "stock_sentiment": -0.20,
        "consumption_delta": -0.30,
    },
    Shock.TRADE_WAR: {
        "desc": "贸易战：出口中断",
        "productivity": lambda p: p * 0.90,
        "stock_sentiment": -0.10,
        "consumption_delta": 0.02,  # 国内替代消费微增
    },
    Shock.BANKING_PANIC: {
        "desc": "银行恐慌：储户挤兑",
        "stock_sentiment": -0.25,
        "consumption_delta": -0.05,
        "bank_run": True,          # 触发银行挤兑
    },
    Shock.RECOVERY: {
        "desc": "经济复苏：需求回暖",
        "productivity": lambda p: p * 1.15,
        "stock_sentiment": 0.15,
        "consumption_delta": 0.15,
    },
}


# ══════════════════════════════════════════════════════════════
# 代理人
# ══════════════════════════════════════════════════════════════

class Household(Agent):
    """
    消费者 v3.0 - 异质性版本

    核心异质性：
      income_tier  → 决定 MPC、风险偏好、初始财富、技能分布
      skill_level  → 决定就业匹配、高薪岗位机会
      risk_aversion → 决定股票持有比例、借贷意愿
      credit_score  → 决定银行贷款批准概率

    行为顺序：
      earn_wage() → pay_taxes() → repay_loan() → deposit()
      → consume() → invest() → search_job()
    """

    def __init__(self, model: EconomyModel):
        super().__init__(model)

        # ── 城市归属（50/50 随机分配）───────────────
        self.city = self.random.choice(list(City))

        # ── 异质性属性 ───────────────────────────────
        self.income_tier = _draw_income_tier(self.random)
        p = INCOME_TIER_PARAMS[self.income_tier]
        self.mpc = p["mpc"]                          # 边际消费倾向
        self.risk_aversion = p["risk_aversion"]      # 风险厌恶系数
        self.consume_prob = min(0.95, p["mpc"])      # 消费概率≈MPC
        self.stock_buy_prob = p["stock_buy_prob"]
        self.stock_sell_prob = p["stock_sell_prob"]

        # 技能等级：0=低技能 / 1=中技能 / 2=高技能
        skill = self.random.choices(
            [0, 1, 2], weights=list(p["skill_weights"].values()), k=1
        )[0]
        self.skill_level = skill  # 0=低 / 1=中 / 2=高

        # 初始现金（帕累托分布偏向低现金）
        lo, hi = p["initial_cash_range"]
        self.cash = self.random.uniform(lo, hi)

        # ── 状态变量 ───────────────────────────────
        self.goods: int = 0
        self.salary: float = 0.0
        self.employed: bool = False
        self.employer: Optional["Firm"] = None
        self.loan_principal: float = 0.0
        self.shares_owned: int = 0
        self.cost_basis: float = 0.0   # 持股成本（移动平均买入价），用于计算资本利得
        self.wealth: float = self.cash
        # 信用评分：初始基于技能水平（高技能→高信用）
        self.credit_score: float = 500 + self.skill_level * 100
        # 历史收入（用于信用评估）
        self.income_history: list[float] = []

    # ── 子行为 ─────────────────────────────────────────────

    def earn_wage(self) -> None:
        """领取工资（含个税）"""
        if not self.employed or self.salary <= 0:
            return
        wage = self.salary
        self.cash += wage
        self.income_history.append(wage)
        if len(self.income_history) > 12:
            self.income_history.pop(0)

    def pay_taxes(self) -> None:
        """缴纳个人所得税"""
        if self.salary <= 0:
            return
        tax = self.salary * self.model.tax_rate
        self.cash -= tax
        self.model.govt_revenue += tax

    def repay_loan(self) -> None:
        """定期偿还贷款（含利息）"""
        if self.loan_principal <= 0:
            return
        rate = self.model.base_interest_rate
        interest = self.loan_principal * rate
        repayment = min(self.model.min_wage * 0.1, self.loan_principal + interest)
        if self.cash >= repayment:
            self.cash -= repayment
            self.loan_principal = max(0.0, self.loan_principal - max(0.0, repayment - interest))

    def deposit(self) -> None:
        """存款：MPC越高→存款比例越低（高收入存更多）"""
        if self.cash <= 5:
            return
        # 高MPC（低收入）几乎不存款；低MPC（高收入）存款更多
        deposit_rate = (1 - self.mpc) * 0.3
        deposit = self.cash * deposit_rate
        if deposit > 1 and self.model.banks:
            self.cash -= deposit
            bank = self.random.choice(self.model.banks)
            bank.reserves += deposit
            bank.deposits += deposit

    def consume(self) -> None:
        """
        差异化消费：按MPC决定是否消费
        高MPC（低收入）：几乎必定消费（生存型）
        低MPC（高收入）：消费概率低（储蓄/投资型）
        消费时同步从有库存的企业扣减库存，闭环商品市场。
        """
        if self.goods <= 0:
            return
        consume_prob = min(0.98, self.mpc + self.random.uniform(-0.05, 0.05))
        if self.random.random() < consume_prob:
            self.goods -= 1
            # 随机选择一家有库存的企业，扣其库存、加其营收，闭环商品市场
            firms_with_stock = self.model._cache.get('firms_with_stock', [])
            if firms_with_stock:
                f = self.random.choice(firms_with_stock)
                f.inventory -= 1
                tax = f.price * self.model.tax_rate
                after_tax = f.price * (1 - self.model.tax_rate)
                f.cash += after_tax
                self.model.govt_revenue += tax  # 税收归集，修复泄漏

    def invest(self) -> None:
        """股票投资：风险厌恶决定是否参与股市"""
        price = self.model.stock_price
        # 风险厌恶高→几乎不参与
        if self.random.random() > (1 - self.risk_aversion) * 0.5 + 0.1:
            return
        # 买入（移动平均成本基准）
        if self.cash >= price * 2 and self.random.random() < self.stock_buy_prob:
            shares_bought = 2
            cost = price * shares_bought
            self.cash -= cost
            # 更新移动平均成本
            total_cost = self.cost_basis * self.shares_owned + cost
            self.shares_owned += shares_bought
            self.cost_basis = total_cost / self.shares_owned if self.shares_owned > 0 else 0.0
            self.model.buy_orders += shares_bought
        # 卖出（资本利得税）
        elif self.shares_owned > 1 and self.random.random() < self.stock_sell_prob:
            shares_sold = 1
            proceeds = price * shares_sold
            cost = self.cost_basis * shares_sold
            capital_gain = max(0.0, proceeds - cost)
            tax = capital_gain * self.model.capital_gains_tax
            self.cash += proceeds - tax
            self.model.govt_revenue += tax
            self.model.capital_gains_tax_revenue += tax
            self.shares_owned -= shares_sold
            # 成本基准不变（平均成本法）
            self.model.sell_orders += shares_sold

    def search_job(self) -> None:
        """找工作（摩擦性失业：消耗现金）"""
        if self.employed:
            return
        # 求职现金消耗（摩擦成本）
        if self.cash > DEFAULTS["job_search_cost"]:
            self.cash -= DEFAULTS["job_search_cost"]

        if self.model.firms and self.random.random() < 0.35:
            # 按技能等级匹配岗位
            candidates = [
                f for f in self.model._cache.get('firms_with_jobs', [])
                if f.wage_offer >= self.model.min_wage
            ]
            if candidates:
                firm = self.random.choice(candidates)
                firm.open_positions -= 1
                self.employed = True
                self.employer = firm
                # 工资议价：失业率越高，议价能力越弱
                ur = self.model.unemployment
                wpremium = DEFAULTS["skill_wage_premium_mid"] if self.skill_level == 1 \
                    else DEFAULTS["skill_wage_premium_high"] if self.skill_level == 2 else 0.0
                wage = firm.wage_offer * (1 - ur * DEFAULTS["wage_bargain_strength"]) * (1 + wpremium)
                self.salary = max(self.model.min_wage, wage)

    def update_wealth(self) -> None:
        """财富 = 现金 - 负债 + 股票市值"""
        stock_value = self.shares_owned * self.model.stock_price
        self.wealth = self.cash - self.loan_principal + stock_value

    def _consider_migration(self) -> None:
        """跨城迁移决策（Phase 2 + 3）"""
        # 每轮有 3% 概率考虑迁移
        if self.random.random() > 0.03:
            return
        # 迁移成本：至少需要 50 现金
        if self.cash < 50:
            return
        my_city = self.city
        other_city = City.CITY_B if my_city == City.CITY_A else City.CITY_A
        # 失业率差
        my_unemp = self.model.city_a_unemp if my_city == City.CITY_A else self.model.city_b_unemp
        other_unemp = self.model.city_b_unemp if my_city == City.CITY_A else self.model.city_a_unemp
        unemp_diff = my_unemp - other_unemp
        # 最低工资差（Phase 3）
        my_min_wage = self.model.city_a_min_wage if my_city == City.CITY_A else self.model.city_b_min_wage
        other_min_wage = self.model.city_b_min_wage if my_city == City.CITY_A else self.model.city_a_min_wage
        wage_diff = other_min_wage - my_min_wage
        # 综合迁移评分
        migrate_score = unemp_diff * 3 + wage_diff * 0.5
        if migrate_score > 0.15 and self.random.random() < 0.5:
            self.city = other_city
            self.cash -= 50  # 迁移成本
            self.employed = False  # 摩擦性失业
            self.employer = None

    def update_credit_score(self) -> None:
        """
        信用评分更新：
          - 收入稳定性（标准差越小越好）
          - 当前负债比
          - 历史记录长度
        """
        if len(self.income_history) < 2:
            return
        income_mean = np.mean(self.income_history)
        income_std = np.std(self.income_history)
        coeff_var = _safe_div(income_std, income_mean + 1e-6, 1.0)
        stability = _clamp(1.0 - coeff_var, 0.0, 1.0)
        debt_ratio = _safe_div(self.loan_principal, self.wealth + 1e-6, 0.0)
        debt_penalty = _clamp(1.0 - debt_ratio * 0.5, 0.5, 1.0)
        # 信用评分 = 基准500 + 稳定性权重(±200) × 稳定性 + 负债调整
        delta = (stability - 0.5) * 200 * debt_penalty
        self.credit_score = _clamp(self.credit_score + delta * 0.1, DEFAULTS["credit_score_min"], DEFAULTS["credit_score_max"])

    # ── 主循环 ─────────────────────────────────────────────

    def step(self) -> None:
        self.earn_wage()
        self.pay_taxes()
        self.repay_loan()
        self.deposit()
        self.consume()
        self.invest()
        self.search_job()
        self.update_credit_score()
        self.update_wealth()
        self._consider_migration()


class Firm(Agent):
    """
    企业 v3.0 - 行业异质性 + 生命周期

    核心异质性：
      industry        → 差异化生产函数、定价策略、裁员率
      lifecycle       → 决定分红率、招聘强度、破产风险

    每个企业有独立定价权（price_stickiness 控制调价频率）
    """

    def __init__(self, model: EconomyModel):
        super().__init__(model)

        # ── 城市归属（50/50 随机分配）───────────────
        self.city = self.random.choice(list(City))

        # ── 异质性 ─────────────────────────────────
        self.industry = _draw_industry(self.random)
        self.lifecycle = _draw_lifecycle(self.random)
        ip = INDUSTRY_PARAMS[self.industry]

        # 初始现金受生命周期影响
        cash_lo, cash_hi = {
            FirmLifecycle.STARTUP: (50, 200),
            FirmLifecycle.GROWTH: (200, 500),
            FirmLifecycle.MATURE: (400, 900),
            FirmLifecycle.DECLINE: (100, 400),
        }[self.lifecycle]
        self.cash = self.random.uniform(cash_lo, cash_hi)

        self.employees: int = 0
        self.wage_offer: float = max(model.min_wage, 8.0) * ip["wage_premium"]
        self.open_positions: int = self.random.randint(0, 4)
        self.production: float = 0.0
        self.inventory: float = 0.0
        self.loan_principal: float = 0.0
        self.wealth: float = self.cash
        self.dividend_per_share: float = 0.0
        self.default_probability: float = 0.0
        self.price: float = model.avg_price if model.avg_price > 1 else 10.0

        # 生命周期专属参数
        self.negative_cash_cycles: int = 0  # 连续负现金流轮次
        self.rnd_investment: float = 0.0    # 研发投入（科技业）
        self.capital_stock: float = 200.0   # 固定资产（制造业）

        # 价格粘性：上次调价距今轮次
        self.price_change_cooldown: int = 0

        # 行业参数缓存
        self._ind = ip

    # ── 子行为 ─────────────────────────────────────────────

    def hire(self) -> None:
        """招聘（受生命周期驱动：初创/衰退企业风格不同）"""
        if self.open_positions <= 0:
            return
        unemployed = self.model.unemployed_households
        # 高技能岗位优先匹配高技能工人
        skill_required = 1 if self.industry == Industry.TECH else 0
        candidates = [h for h in unemployed if h.skill_level >= skill_required]
        if not candidates:
            candidates = unemployed[:]

        n_hire = min(self.open_positions, len(candidates))
        for _ in range(n_hire):
            if candidates:
                h = self.random.choice(candidates)
                h.employed = True
                h.employer = self
                h.salary = self.wage_offer
                self.employees += 1
                self.open_positions -= 1
                candidates.remove(h)

    def produce(self) -> None:
        """
        差异化生产函数：

        制造业：capital_intensity 高，依赖固定资产
          production = sqrt(capital) × TFP × (employees^0.6)

        服务业：轻资产，人力驱动
          production = employees × wage_base × TFP

        科技：R&D 驱动，高波动
          production = employees × TFP × (1 + rnd_investment/cash)
        """
        T = self.model.productivity
        noise_std = self._ind["productivity_noise"]

        if self.industry == Industry.MANUFACTURING:
            self.production = (
                np.sqrt(max(1.0, self.capital_stock))
                * T
                * (max(1, self.employees) ** 0.6)
                + self.random.gauss(0, noise_std)
            )
            # 资本折旧
            self.capital_stock *= 0.98

        elif self.industry == Industry.SERVICE:
            self.production = (
                max(1, self.employees)
                * self.model.min_wage
                * T
                + self.random.gauss(0, noise_std)
            )

        elif self.industry == Industry.TECH:
            # 科技业：研发投入转化为生产力（随机成功/失败）
            rnd_success = self.random.gauss(1.0, 0.5)
            rnd_success = max(0.1, rnd_success)
            self.production = (
                max(1, self.employees)
                * T
                * (1 + self.rnd_investment / max(1, self.cash) * rnd_success)
                + self.random.gauss(0, noise_std)
            )
            # 研发投入（利润的固定比例）
            self.rnd_investment = max(0.0, self.cash * 0.05 * rnd_success)

        self.production = max(0.0, self.production)
        self.inventory += self.production

    def price_goods(self) -> None:
        """
        差异化定价 + 价格粘性：

        1. 计算目标价格（基于边际成本 + 目标毛利率）
        2. 价格粘性：仅 price_stickiness 比例的企业会调价
        3. 伯特兰竞争：参考竞争对手平均价格
        """
        if self.price_change_cooldown > 0:
            self.price_change_cooldown -= 1
            return

        # 价格粘性：仅部分企业每轮调价
        if self.random.random() > DEFAULTS["price_stickiness"]:
            return

        competitors = [f for f in self.model._cache.get('firms_by_industry', {}).get(self.industry, []) if f is not self]
        avg_competitor_price = np.mean([f.price for f in competitors]) if competitors else self.model.avg_price

        # 目标价格：库存多→降价去库存；库存少→涨价
        cost_per_unit = self._ind["capital_intensity"] * self.model.min_wage * 0.5
        if self.inventory > 15:
            # 去库存：价格下浮最多20%
            self.price = max(cost_per_unit, avg_competitor_price * 0.90)
        elif self.inventory < 3:
            # 供不应求：价格上浮最多15%
            self.price = avg_competitor_price * 1.10
        else:
            # 正常：向竞争对手均价靠拢
            self.price = 0.5 * self.price + 0.5 * avg_competitor_price

        self.price = max(1.0, self.price)
        # 调价后进入冷却期（模拟菜单成本）
        self.price_change_cooldown = self.random.randint(1, 3)

    def sell_goods(self) -> None:
        """向居民销售商品（按价格排序：低价优先被购买）+ 城际贸易追踪（Phase 4）"""
        if self.inventory <= 0:
            return
        # 价格敏感型消费：优先买便宜的
        firms_sorted = sorted(self.model.firms, key=lambda f: f.price)
        buyers = self.random.sample(
            self.model.households, min(len(self.model.households), 6)
        )
        for h in buyers:
            if h.cash >= self.price and h.goods < 10 and self.inventory > 0:
                h.cash -= self.price
                h.goods += 1
                tax = self.price * self.model.tax_rate
                after_tax = self.price * (1 - self.model.tax_rate)
                self.cash += after_tax
                self.model.govt_revenue += tax  # 税收归集，修复泄漏
                self.inventory -= 1
                # 城际贸易：企业与消费者不在同一城市
                if h.city != self.city:
                    if self.city == City.CITY_A:
                        self.model.city_a_exports += after_tax
                        self.model.city_b_imports += after_tax
                    else:
                        self.model.city_b_exports += after_tax
                        self.model.city_a_imports += after_tax

    def pay_dividend(self) -> None:
        """生命周期决定分红率：成熟期高分红，初创期不分"""
        if self.cash <= 50:
            return
        div_ratio = self._ind["div_ratio"]
        if self.lifecycle == FirmLifecycle.STARTUP:
            div_ratio *= 0.0   # 初创：不分红，留存扩产
        elif self.lifecycle == FirmLifecycle.DECLINE:
            div_ratio *= 1.5   # 衰退：变现资产

        profit = self.cash * div_ratio
        self.cash -= profit
        self.dividend_per_share = _safe_div(profit, 50)
        self.model.total_dividends += profit

    def update_wage(self) -> None:
        """行业 + 生命周期决定工资调整策略"""
        ur = self.model.unemployment
        # 失业率高→压低工资（劳动市场宽松）；失业率低→提高工资（抢人）
        if ur > 0.15:
            self.wage_offer = _clamp(self.wage_offer * 0.97, self.model.min_wage, 50.0)
        elif self.inventory > 20 and self.lifecycle in (FirmLifecycle.GROWTH, FirmLifecycle.MATURE):
            self.wage_offer = _clamp(self.wage_offer * 1.04, self.model.min_wage, 50.0)

    def adjust_workforce(self) -> None:
        """生命周期 + 经济状态决定裁员/扩产"""
        if self.employees == 0:
            return
        # 衰退期或库存严重过剩时裁员
        layoff_prob = self._ind["layoff_prob"]
        if self.lifecycle == FirmLifecycle.DECLINE:
            layoff_prob *= 2.0
        if self.inventory < 2:
            layoff_prob *= 3.0

        if self.random.random() < layoff_prob:
            n_layoff = min(self.employees, self.random.randint(1, 3))
            # 随机裁一名员工
            employed = self.model._cache.get('employees_of', {}).get(id(self), [])
            if employed:
                h = self.random.choice(employed)
                h.employed = False
                h.employer = None
                h.salary = 0.0
                self.employees -= n_layoff

    def apply_for_loan(self) -> None:
        """申请贷款（有信用审核）"""
        if self.loan_principal >= DEFAULTS.get("loan_cap", 500.0):
            return
        wage_bill = self.employees * self.wage_offer
        if self.cash >= wage_bill * 0.5:
            return

        # 信用评分决定是否批准
        cs = getattr(self, "credit_score", 600)
        if cs < 400:
            return  # 信用太差，拒绝

        loan = min(DEFAULTS.get("bank_loan_amount", 20.0), DEFAULTS.get("loan_cap", 500.0) - self.loan_principal)
        if loan <= 0:
            return

        self.cash += loan
        self.loan_principal += loan
        self.model.total_loans_outstanding += loan
        if self.model.banks:
            bank = self.random.choice(self.model.banks)
            bank.total_loans += loan
            bank.reserves -= loan

    def repay_loan(self) -> None:
        """
        偿还贷款 + 违约判定（修复负现金漏洞）
        
        Bug 修复：原代码 repayment = min(interest + 5, self.cash)
        当 self.cash 为负时，repayment 变为负数，导致 cash -= 负数 = 现金增加。
        """
        if self.loan_principal <= 0:
            return
        
        # 修复：现金为负时不能还款
        if self.cash <= 0:
            if self.random.random() < self.default_probability:
                self._trigger_default()
            return
        
        rate = self.model.base_interest_rate
        interest = self.loan_principal * rate * 0.1
        
        # 应还 = 利息 + 部分本金
        target_repayment = interest + 5
        # 实际还款 = min(目标, 现金)，确保非负
        repayment = min(target_repayment, self.cash)
        repayment = max(0, repayment)
        
        if repayment <= 0:
            return
        
        if self.cash >= repayment:
            self.cash -= repayment
            self.loan_principal -= max(0.0, repayment - interest)
        elif self.random.random() < self.default_probability:
            self._trigger_default()

    def _trigger_default(self) -> None:
        """违约触发：同步通知银行，增加全系统风险"""
        logger.warning(
            "企业%d违约！行业=%s，周期=%d，现金=%.1f，负债=%.1f",
            self.unique_id, self.industry.value, self.negative_cash_cycles,
            self.cash, self.loan_principal,
        )
        if self.model.banks:
            bank = self.random.choice(self.model.banks)
            # 违约损失率（银行实际承受）
            actual_loss = self.loan_principal * DEFAULTS["default_loss_rate"]
            bank.total_loans -= self.loan_principal
            bank.bad_debts += actual_loss
        self.model.total_loans_outstanding -= self.loan_principal
        self.model.default_count += 1
        self.model.systemic_risk = min(1.0, self.model.systemic_risk + 0.05)
        self.loan_principal = 0.0

    def check_bankruptcy(self) -> bool:
        """
        破产判定：连续N轮现金流为负
        触发：模型从 agents 列表移除
        """
        if self.cash < 0:
            self.negative_cash_cycles += 1
        else:
            self.negative_cash_cycles = 0

        if self.negative_cash_cycles >= DEFAULTS["bankruptcy_cycles"]:
            logger.warning(
                "企业%d破产！行业=%s，生命周期=%s",
                self.unique_id, self.industry.value, self.lifecycle.value,
            )
            # 通知银行：企业消失，贷款清零
            if self.loan_principal > 0:
                self.model.total_loans_outstanding -= self.loan_principal
                if self.model.banks:
                    bank = self.random.choice(self.model.banks)
                    bank.total_loans -= self.loan_principal
                    bank.bad_debts += self.loan_principal * 0.8  # 破产损失率80%

            # 解雇员工
            for h in self.model.households:
                if h.employer is self:
                    h.employed = False
                    h.employer = None
                    h.salary = 0.0

            self.model.firms.remove(self)
            self.model.agents.remove(self)
            self.model.bankrupt_count += 1
            return True
        return False

    def update_default_probability(self) -> None:
        """违约概率 = 1 - 现金/负债（更敏感）"""
        total_debt = self.loan_principal + 1e-6
        self.default_probability = _clamp(1.0 - self.cash / total_debt, 0.0, 1.0)

    def update_credit_score(self) -> None:
        """企业信用评分：基于利润率 + 负债率"""
        profit_ratio = _safe_div(self.production, self.employees * self.wage_offer + 1.0, 1.0)
        debt_ratio = _safe_div(self.loan_principal, self.wealth + 1.0, 0.0)
        # 信用 = 基准600 + 盈利调整 - 负债惩罚
        delta = (profit_ratio - 1.0) * 50 - debt_ratio * 100
        self.credit_score = _clamp(
            getattr(self, "credit_score", 600) + delta * 0.1,
            DEFAULTS["credit_score_min"],
            DEFAULTS["credit_score_max"],
        )

    def update_wealth(self) -> None:
        """企业财富 = 现金 - 负债 + 库存价值 + 固定资产"""
        inventory_value = self.inventory * self.price
        self.wealth = self.cash - self.loan_principal + inventory_value + self.capital_stock * 0.5

    # ── 主循环 ─────────────────────────────────────────────

    def step(self) -> None:
        if self.check_bankruptcy():
            return  # 已破产，不再执行
        self.hire()
        self.produce()
        self.price_goods()
        self.sell_goods()
        self.pay_dividend()
        self.update_wage()
        self.adjust_workforce()
        self.update_default_probability()
        self.update_credit_score()
        self.apply_for_loan()
        self.repay_loan()
        self.update_wealth()
        self._consider_migration()

    def _consider_migration(self) -> None:
        """企业跨城迁移（Phase 2 + 3）"""
        # 每轮 2% 概率考虑迁移
        if self.random.random() > 0.02:
            return
        # 迁移成本：需要至少 200 现金
        if self.cash < 200:
            return
        my_city = self.city
        other_city = City.CITY_B if my_city == City.CITY_A else City.CITY_A
        # 税率差（Phase 3）
        my_tax = self.model.city_a_tax if my_city == City.CITY_A else self.model.city_b_tax
        other_tax = self.model.city_b_tax if my_city == City.CITY_A else self.model.city_a_tax
        tax_diff = my_tax - other_tax  # 正值：当前税率高，想迁走
        # 失业率差（劳动力成本）
        my_unemp = self.model.city_a_unemp if my_city == City.CITY_A else self.model.city_b_unemp
        other_unemp = self.model.city_b_unemp if my_city == City.CITY_A else self.model.city_a_unemp
        labor_diff = other_unemp - my_unemp  # 正值：另一城市劳动力更便宜
        # 综合迁移概率
        migrate_score = tax_diff * 5 + labor_diff * 2
        if migrate_score > 0.05 and self.random.random() < 0.3:
            self.city = other_city
            self.cash -= 200  # 迁移成本
            # 20% 员工离职
            if self.employees > 0:
                n_quit = max(1, int(self.employees * 0.2))
                employed = self.model._cache.get('employees_of', {}).get(id(self), [])
                for h in self.random.sample(employed, min(n_quit, len(employed))):
                    h.employed = False
                    h.employer = None
                    self.employees -= 1


class Bank(Agent):
    """
    银行 v3.0 - 差异化利率 + 巴塞尔合规

    核心异质性：
      risk_appetite    → 高则放贷激进、利率高；低则保守、利率低
      lending_spread   → 在基准利率上的加点
      default_tolerance → 坏账容忍度
    """

    def __init__(self, model: EconomyModel):
        super().__init__(model)

        # ── 城市归属（50/50 随机分配）───────────────
        self.city = self.random.choice(list(City))

        # ── 随机选择银行类型 ─────────────────────────
        bank_type = "aggressive" if self.random.random() < 0.5 else "conservative"
        bp = BANK_PARAMS[bank_type]
        self.bank_type = bank_type
        self.risk_appetite = bp["risk_appetite"]
        self.lending_spread = bp["lending_spread"]
        self.default_tolerance = bp["default_tolerance"]
        self.loan_amount = bp["loan_amount"]
        self.reserves: float = bp["initial_reserves"]
        self.deposits: float = 0.0
        self.total_loans: float = 0.0
        self.bad_debts: float = 0.0
        self.wealth: float = self.reserves
        # 资本金（用于巴塞尔协议）
        self.capital: float = bp["initial_reserves"] * 0.1
        self._loans: dict[int, float] = {}  # 记录本银行的贷款明细（borrower_id → loan_amount），修复越界问题

    def _effective_rate(self, borrower: Agent) -> float:
        """
        差异化利率 = 基准利率 + 信用利差 + 银行风险偏好
        信用评分低 → 利率高（风险溢价）
        银行保守型 → 利差高
        """
        base = self.model.base_interest_rate
        credit_score = getattr(borrower, "credit_score", 600)
        # 信用评分映射到利差：[850分→+0%，300分→+5%]
        score_penalty = (DEFAULTS["credit_score_max"] - credit_score) / (DEFAULTS["credit_score_max"] - DEFAULTS["credit_score_min"]) * 0.05
        return base + self.lending_spread + score_penalty

    def pay_deposit_interest(self) -> None:
        """
        支付存款利息（复式簿记）：
          - 银行资产端：准备金减少
          - 银行负债端：存款不变（利息已支付）
          - 储户资产端：现金增加（利息收入）
          
        资金守恒：利息从银行准备金流向储户现金，系统内现金总量不变。
        """
        if self.deposits <= 0:
            return
            
        rate = self.model.base_interest_rate * 0.5  # 存款利率通常低于基准
        total_interest = self.deposits * rate
        
        # 确保银行有足够的准备金支付利息
        total_interest = min(total_interest, self.reserves * 0.1)  # 最多支付准备金的10%
        if total_interest <= 0:
            return
        
        # 按存款比例分配给储户
        depositors = list(self.model.households) + list(self.model.firms)
        total_deposits = sum(getattr(d, 'cash', 0) for d in depositors)  # 简化：现金=存款
        
        if total_deposits <= 0:
            return
        
        for d in depositors:
            deposit = getattr(d, 'cash', 0)
            if deposit > 0:
                share = (deposit / total_deposits) * total_interest
                # 复式簿记：
                # 1. 银行准备金减少
                self.reserves -= share
                # 2. 储户现金增加（利息收入）
                d.cash += share
                # 资金守恒：银行准备金↓ = 储户现金↑ ✓

    def lend(self) -> None:
        """
        复式簿记放贷：
          - 银行资产端：+贷款债权（对借款人）
          - 银行负债端：-准备金（现金减少）
          - 借款人资产端：+现金
          - 借款人负债端：+贷款债务
          
        资金守恒：系统内现金总量不变，只是从银行准备金转移到借款人手中。
        """
        if self.reserves <= 50:
            return

        # 资本金充足率检查（风险加权资产 = 贷款额 × 1.0）
        capital_ratio = self.capital / max(1.0, self.total_loans)
        if capital_ratio < 0.08:
            # 低于8%：强制收缩（巴塞尔III）
            # 收回部分贷款（从有现金的借款人那里）
            for borrower_id, loan_amount in list(self._loans.items()):
                if loan_amount <= 0:
                    continue
                # 找到借款人
                borrower = None
                for h in self.model.households:
                    if id(h) == borrower_id:
                        borrower = h
                        break
                if not borrower:
                    for f in self.model.firms:
                        if id(f) == borrower_id:
                            borrower = f
                            break
                if borrower and borrower.cash >= loan_amount * 0.3:
                    # 提前收回30%
                    repay = loan_amount * 0.3
                    borrower.cash -= repay
                    borrower.loan_principal -= repay
                    self._loans[borrower_id] -= repay
                    self.reserves += repay
                    self.total_loans -= repay
                    self.model.total_loans_outstanding -= repay
            return

        borrowers = list(self.model.households) + list(self.model.firms)
        max_loans = min(3, len(borrowers))

        for _ in range(max_loans):
            if self.reserves <= self.loan_amount:
                break
            b = self.random.choice(borrowers)
            existing = getattr(b, "loan_principal", 0.0)
            cap = DEFAULTS.get("household_loan_cap", 200.0) if isinstance(b, Household) \
                else DEFAULTS.get("loan_cap", 500.0)
            if existing >= cap:
                continue

            # 信用审核
            cs = getattr(b, "credit_score", 600)
            if cs < 400:
                continue  # 拒贷

            amount = self.loan_amount * (0.7 + self.risk_appetite * 0.6)
            amount = min(amount, cap - existing)
            if amount <= 0:
                continue

            # ═══ 复式簿记：贷款创造存款 ═══
            # 1. 银行资产端：增加贷款债权（对借款人的债权）
            self._loans[id(b)] = self._loans.get(id(b), 0) + amount
            self.total_loans += amount
            self.model.total_loans_outstanding += amount
            
            # 2. 银行负债端：减少准备金（现金流出）
            self.reserves -= amount
            
            # 3. 借款人资产端：增加现金
            b.cash += amount
            
            # 4. 借款人负债端：增加贷款债务
            b.loan_principal = existing + amount
            if not hasattr(b, 'creditor_bank') or not b.creditor_bank:
                b.creditor_bank = set()
            b.creditor_bank.add(id(self))  # 记录债主银行
            
            # 资金守恒验证：系统内现金总量不变
            # 银行准备金减少 = 借款人现金增加 ✓

    def update_bad_debts(self) -> None:
        """
        坏账 = Σ(本银行债务人违约概率 × 本银行对其贷款额 × 损失率)
        修复：只统计自己发放的贷款，不越界统计其他银行的贷款
        """
        total = 0.0
        for borrower_id, loan_principal in list(self._loans.items()):
            # 从模型中查找对应债务人
            borrower = next(
                (a for a in list(self.model.households) + list(self.model.firms)
                 if id(a) == borrower_id), None
            )
            if borrower is None:
                # 债务人已消失（破产），全额计坏账
                total += loan_principal * DEFAULTS["default_loss_rate"]
            else:
                prob = getattr(borrower, "default_probability", 0.0)
                total += prob * loan_principal * DEFAULTS["default_loss_rate"]
        self.bad_debts = total

        # 更新资本金（利润留存）
        self.capital = max(self.capital * 0.99, self.reserves * 0.1)

    def update_wealth(self) -> None:
        """银行财富 = 准备金 + 有效贷款 - 坏账"""
        effective_loans = max(0.0, self.total_loans - self.bad_debts)
        self.wealth = self.reserves + effective_loans

    def step(self) -> None:
        self.pay_deposit_interest()
        self.lend()
        self.update_bad_debts()
        self.update_wealth()


class Trader(Agent):
    """
    交易员 v3.0 - 四种策略

    动量（Momentum）：追涨杀跌
    价值（Value）：基于戈登模型内在价值
    噪声（Noise）：随机交易（散户行为）
    做市商（Market Maker）：双向挂单赚价差
    """

    def __init__(self, model: EconomyModel):
        super().__init__(model)

        # ── 城市归属（50/50 随机分配）───────────────
        self.city = self.random.choice(list(City))

        self.strategy = _draw_trader_strategy(self.random)
        self.cash: float = self.random.uniform(300, 1000)
        self.shares: int = self.random.randint(0, 20)
        self.cost_basis: float = float(model.stock_price)  # 移动平均成本基准
        self.momentum: float = 0.0
        # 价值投资者：持有对内在价值的估计
        self.intrinsic_value_estimate: float = model.stock_price
        # 做市商：买卖价差
        self.bid_ask_spread: float = 0.02
        self.wealth: float = self.cash + self.shares * model.stock_price
        self.realized_gains: float = 0.0   # 已实现收益（用于正确计算财富）

    def _update_momentum(self, price: float, prev: float) -> None:
        if prev <= 0:
            return
        ret = (price - prev) / prev
        self.momentum = 0.7 * self.momentum + 0.3 * ret

    def _gordon_value(self, dividend: float, rate: float) -> float:
        """戈登增长模型：P = D / (r - g)"""
        g = DEFAULTS["gordon_growth"]
        return _safe_div(dividend, rate - g, 100.0)

    # ── 四种交易策略 ─────────────────────────────────────────

    def _trade_momentum(self, price: float) -> None:
        m = self.model
        self._update_momentum(price, m.prev_stock_price)

        buy_prob = _clamp(0.3 + self.momentum * 2.5, 0.0, 1.0)
        sell_prob = _clamp(0.3 - self.momentum * 2.5, 0.0, 1.0)

        if self.cash >= price * 2 and self.random.random() < buy_prob:
            cost = price * 2
            self.cash -= cost
            # 移动平均成本基准
            old_cost = self.cost_basis * self.shares
            self.shares += 2
            self.cost_basis = (old_cost + cost) / self.shares
            m.buy_orders += 2

        # 止损（全卖）
        prev = m.prev_stock_price
        if prev > 0 and (prev - price) / prev > 0.05 and self.shares > 0:
            self._sell(self.shares, price)
        elif self.shares > 0 and self.random.random() < sell_prob:
            self._sell(1, price)

    def _trade_value(self, price: float) -> None:
        """价值投资：内在价值低估则买，高估则卖"""
        m = self.model
        # 戈登模型估计内在价值
        avg_div_per_share = m.total_dividends / max(1, len(m.firms) * 50)
        intrinsic = self._gordon_value(avg_div_per_share, m.base_interest_rate)
        # 平滑估计
        self.intrinsic_value_estimate = 0.7 * self.intrinsic_value_estimate + 0.3 * intrinsic

        # 折价20%以上 → 买入；溢价20%以上 → 卖出
        if price < self.intrinsic_value_estimate * 0.80 and self.cash >= price:
            self.cash -= price
            self.shares += 1
            # 移动平均成本基准
            total_cost = self.cost_basis * (self.shares - 1) + price
            self.cost_basis = total_cost / self.shares
            m.buy_orders += 1
        elif price > self.intrinsic_value_estimate * 1.20 and self.shares > 0:
            self._sell(1, price)

    def _trade_noise(self, price: float) -> None:
        """噪声交易：随机买卖（模拟散户非理性行为）"""
        m = self.model
        if self.cash >= price and self.random.random() < 0.2:
            cost = price
            self.cash -= cost
            total_cost = self.cost_basis * self.shares + cost
            self.shares += 1
            self.cost_basis = total_cost / self.shares
            m.buy_orders += 1
        if self.shares > 0 and self.random.random() < 0.18:
            self._sell(1, price)

    def _trade_market_maker(self, price: float) -> None:
        """做市商：双向挂单，赚取买卖价差"""
        m = self.model
        spread = self.bid_ask_spread
        bid = price * (1 - spread)
        ask = price * (1 + spread)

        # 市价单：假设买卖均按当前价格成交
        # 买入
        if self.cash >= ask and self.random.random() < 0.4:
            self.cash -= ask
            self.shares += 1
            total_cost = self.cost_basis * (self.shares - 1) + ask
            self.cost_basis = total_cost / self.shares
            m.buy_orders += 1
        # 卖出
        if self.shares > 0 and self.random.random() < 0.4:
            self._sell(1, bid)

    def trade(self) -> None:
        """根据策略类型执行交易"""
        price = self.model.stock_price
        if self.strategy == TraderStrategy.MOMENTUM:
            self._trade_momentum(price)
        elif self.strategy == TraderStrategy.VALUE:
            self._trade_value(price)
        elif self.strategy == TraderStrategy.NOISE:
            self._trade_noise(price)
        elif self.strategy == TraderStrategy.MARKET_MAKER:
            self._trade_market_maker(price)

    def _sell(self, n: int, price: float) -> None:
        """卖出 n 股，含资本利得税，修复税收泄漏"""
        if n <= 0 or self.shares < n:
            return
        proceeds = price * n
        cost = self.cost_basis * n
        gain = max(0.0, proceeds - cost)
        tax = gain * self.model.capital_gains_tax
        self.cash += proceeds - tax
        self.model.govt_revenue += tax
        self.model.capital_gains_tax_revenue += tax
        # 已实现收益累计（不含税）
        self.realized_gains += gain - tax
        self.shares -= n
        self.model.sell_orders += n

    def update_wealth(self) -> None:
        self.wealth = self.cash + self.shares * self.model.stock_price

    def step(self) -> None:
        self.trade()
        self.update_wealth()


# ══════════════════════════════════════════════════════════════
# 宏观指标
# ══════════════════════════════════════════════════════════════

def compute_gdp(model: EconomyModel) -> float:
    """
    支出法 GDP（C + I + G）：
      C = 居民消费（购买企业商品总支出）
      I = 企业投资（生产 - 已售 + 研发 + 资本形成）
      G = 政府购买 + 失业补贴
    """
    households = model.households
    firms = model.firms

    # 消费 C
    consumption = sum(h.goods * model.avg_price for h in households)

    # 投资 I = 库存净变动（已生产未出售的部分）+ R&D + 资本折旧
    # 注：不用 f.production * f.price（与 consumption 的 h.goods * price 重复）
    investment = sum(
        f.inventory * f.price + f.rnd_investment + f.capital_stock * 0.02
        for f in firms
    )

    # 政府支出 G（购买 + 补贴）→ 均计入 GDP
    gov_spending = model.gov_purchase + model.subsidy * len(model.unemployed_households)

    return consumption + investment + gov_spending


def compute_unemployment(model: EconomyModel) -> float:
    """矢量化失业率（Phase 1 NumPy 优化）"""
    if not model.households:
        return 0.0
    employed = np.array([h.employed for h in model.households], dtype=bool)
    return float(np.sum(~employed)) / len(model.households)


# ══════════════════════════════════════════════════════════════
# 经济模型
# ══════════════════════════════════════════════════════════════

class EconomyModel(Model):
    """
    主模型 v3.0

    执行顺序：
      0. 外部冲击（随机触发）
      1. Bank  → 2. Firm  → 3. Household  → 4. Trader
      5. 政府活动（G / 补贴）
      6. 宏观清算（股市 + 物价 + GDP）

    政策传导链（利率↑为例）：
      利率↑ → Bank.repay_loan()成本↑ → Firm.cash↓ → apply_for_loan()需求↑
      → 招聘↓ → Household失业↑ → 消费↓ → GDP↓ → 价格↓

    金融加速器：
      股价↓ → Firm抵押品价值↓ → 银行要求追加保证金
      → 抛售资产 → 股价进一步↓（螺旋下行）
    """

    def __init__(self, **kwargs):
        super().__init__()

        # ── 参数注入 + 校验 ─────────────────────────────────
        for key, default_val in DEFAULTS.items():
            setattr(self, key, _clamp(kwargs.get(key, default_val), 0.0, 1e9))

        self.tax_rate = _clamp(kwargs.get("tax_rate", DEFAULTS["tax_rate"]), 0.0, 0.45)
        self.base_interest_rate = _clamp(kwargs.get("base_interest_rate", DEFAULTS["base_interest_rate"]), 0.0, 0.25)
        self.min_wage = max(0.0, kwargs.get("min_wage", DEFAULTS["min_wage"]))
        self.productivity = max(0.01, kwargs.get("productivity", DEFAULTS["productivity"]))
        self.subsidy = max(0.0, kwargs.get("subsidy", DEFAULTS["subsidy"]))
        self.gov_purchase = max(0.0, kwargs.get("gov_purchase", DEFAULTS["gov_purchase"]))
        self.qe_amount = max(0.0, kwargs.get("qe_amount", DEFAULTS["qe_amount"]))

        # ── Agent 分类缓存 ─────────────────────────────────
        self.households: list[Household] = []
        self.firms: list[Firm] = []
        self.banks: list[Bank] = []
        self.traders: list[Trader] = []

        # ── 市场状态 ───────────────────────────────────────
        self.stock_price: float = 100.0
        self.prev_stock_price: float = 100.0
        self.avg_price: float = 10.0
        self.prev_avg_price: float = 10.0
        self.buy_orders: int = 0
        self.sell_orders: int = 0

        # ── 政府财政 ───────────────────────────────────────
        self.govt_revenue: float = 0.0
        self.govt_expenditure: float = 0.0

        # ── 信贷市场 ───────────────────────────────────────
        self.total_loans_outstanding: float = 0.0
        self.total_dividends: float = 0.0        # 单轮分红（每轮重置）
        self.all_dividends: float = 0.0          # 全量累计（永不清零，用于 Gordon 模型）

        # ── 宏观指标 ───────────────────────────────────────
        self.gdp: float = 0.0
        self.unemployment: float = 0.0
        self.gini: float = 0.0

        # ── 金融风险指标 ───────────────────────────────────
        self.stock_volatility: float = 0.0
        self.stock_returns: list[float] = []
        self.default_count: int = 0
        self.bank_bad_debt_rate: float = 0.0
        self.systemic_risk: float = 0.0    # 系统性风险（0~1）
        self.bankrupt_count: int = 0       # 累计破产企业数
        self.current_shock: str = ""        # 当前生效的冲击名称

        # ── 资本利得税收入 ─────────────────────────────────
        self.capital_gains_tax_revenue: float = 0.0

        # ── 城市级指标（双城竞争面板）──────────────────────
        self.city_a_pop: int = 0
        self.city_a_firms: int = 0
        self.city_a_unemp: float = 0.0
        self.city_a_gdp: float = 0.0
        self.city_b_pop: int = 0
        self.city_b_firms: int = 0
        self.city_b_unemp: float = 0.0
        self.city_b_gdp: float = 0.0

        # ── 城市级参数（Phase 3：差异化政策）──────────────────
        self.city_a_tax = CITY_PARAMS[City.CITY_A]["corporate_tax_rate"]
        self.city_a_min_wage = CITY_PARAMS[City.CITY_A]["min_wage"]
        self.city_a_subsidy = CITY_PARAMS[City.CITY_A]["subsidy_rate"]
        self.city_a_infra = CITY_PARAMS[City.CITY_A]["infrastructure"]
        self.city_b_tax = CITY_PARAMS[City.CITY_B]["corporate_tax_rate"]
        self.city_b_min_wage = CITY_PARAMS[City.CITY_B]["min_wage"]
        self.city_b_subsidy = CITY_PARAMS[City.CITY_B]["subsidy_rate"]
        self.city_b_infra = CITY_PARAMS[City.CITY_B]["infrastructure"]

        # ── 城际贸易统计（Phase 4）──────────────────────────
        self.city_a_exports: float = 0.0  # 城市 A 出口额
        self.city_b_exports: float = 0.0  # 城市 B 出口额
        self.city_a_imports: float = 0.0  # 城市 A 进口额
        self.city_b_imports: float = 0.0  # 城市 B 进口额

        # ── 周期计数器 ─────────────────────────────────────
        self.cycle: int = 0

        # ── 创建 Agent ─────────────────────────────────────
        n_hh = max(1, int(kwargs.get("n_households", DEFAULTS["n_households"])))
        n_firm = max(1, int(kwargs.get("n_firms", DEFAULTS["n_firms"])))
        n_bank = max(1, int(kwargs.get("n_banks", DEFAULTS["n_banks"])))
        n_trader = max(1, int(kwargs.get("n_traders", DEFAULTS["n_traders"])))

        for _ in range(n_hh):
            h = Household(self)
            self.agents.add(h)
            self.households.append(h)

        for _ in range(n_firm):
            f = Firm(self)
            self.agents.add(f)
            self.firms.append(f)

        for _ in range(n_bank):
            b = Bank(self)
            self.agents.add(b)
            self.banks.append(b)

        for _ in range(n_trader):
            t = Trader(self)
            self.agents.add(t)
            self.traders.append(t)

        # ── 数据收集器 ─────────────────────────────────────
        self.datacollector = DataCollector(
            model_reporters={
                "stock_price":    "stock_price",
                "price_index":    lambda m: round(m.avg_price, 2),
                "gdp":            "gdp",
                "unemployment":   lambda m: round(m.unemployment * 100, 1),
                "gini":           lambda m: round(m.gini, 4),
                "buy_orders":     "buy_orders",
                "sell_orders":    "sell_orders",
                "loans":          lambda m: round(m.total_loans_outstanding, 1),
                "stock_vol":      lambda m: round(m.stock_volatility, 4),
                "default_count":  "default_count",
                "bad_debt_rate":  lambda m: round(m.bank_bad_debt_rate, 4),
                "systemic_risk":  lambda m: round(m.systemic_risk, 4),
                "bankrupt_count": "bankrupt_count",
                "gov_revenue":    lambda m: round(m.govt_revenue, 1),
                "gov_expenditure": lambda m: round(m.govt_expenditure, 1),
                "cap_gains_tax":  lambda m: round(m.capital_gains_tax_revenue, 1),
                "n_firms":        lambda m: len(m.firms),
                "n_households":   lambda m: len(m.households),
            },
            agent_reporters={
                "cash":       lambda a: getattr(a, "cash", 0.0),
                "wealth":     lambda a: getattr(a, "wealth", 0.0),
                "agent_type": lambda a: type(a).__name__,
            },
        )

        logger.info(
            "模型 v3.0 初始化：%d households, %d firms, %d banks, %d traders",
            n_hh, n_firm, n_bank, n_trader,
        )

        # ── 运行时缓存（Phase 0A 优化）──────────────────────────
        self._cache: dict = {}
        self._refresh_cache()  # 初始化缓存

    def _refresh_cache(self) -> None:
        """在每个 step 末尾刷新查找缓存，将 O(n²) 遍历降为 O(1) 查找"""
        self._cache = {
            "firms_with_stock": [f for f in self.firms if f.inventory > 0],
            "firms_with_jobs": [f for f in self.firms if f.open_positions > 0],
            "firms_by_industry": {ind: [f for f in self.firms if f.industry == ind] for ind in Industry},
            "employed_hh": [h for h in self.households if h.employed],
            # 企业→员工列表映射（用于 Firm 批量裁员）
            "employees_of": {id(f): [h for h in self.households if h.employer is f] for f in self.firms},
        }

    # ── 属性代理 ───────────────────────────────────────────

    @property
    def unemployed_households(self) -> list[Household]:
        return [h for h in self.households if not h.employed]

    # ── 主循环 ─────────────────────────────────────────────

    def step(self) -> None:
        self._reset_counters()

        # 0. 外部冲击
        self._apply_shock()

        # 1. 银行
        for bank in self.banks:
            bank.step()

        # 2. 企业
        for firm in self.firms[:]:   # [:] 因为 step 内可能移除破产企业
            firm.step()

        # 3. 居民
        for hh in self.households:
            hh.step()

        # 4. 交易者
        for trader in self.traders:
            trader.step()

        # 5. 政府活动
        self._gov_activity()

        # 6. 宏观清算
        self._clear_markets()
        self._compute_macro()
        self._collect_data()

        # 7. 刷新运行时缓存（Phase 0A）
        self._refresh_cache()

        self.cycle += 1

    # ── 辅助方法 ──────────────────────────────────────────

    def _reset_counters(self) -> None:
        self.buy_orders = 0
        self.sell_orders = 0
        self.all_dividends += self.total_dividends  # 累计前先加上旧值
        self.total_dividends = 0.0        # 单轮重置
        self.govt_revenue = 0.0
        self.default_count = 0
        self.capital_gains_tax_revenue = 0.0

    def trigger_shock(self, shock_name: str) -> str:
        """手动触发指定冲击，返回冲击描述"""
        try:
            shock_type = Shock(shock_name)
        except ValueError:
            shock_type = self.random.choice(list(SHOCK_EFFECTS.keys()))

        effect = SHOCK_EFFECTS[shock_type]
        self.current_shock = effect["desc"]

        prod_delta = effect.get("productivity", None)
        if callable(prod_delta):
            self.productivity = _clamp(prod_delta(self.productivity), 0.1, 5.0)

        if effect.get("bank_run", False):
            self.systemic_risk = min(1.0, self.systemic_risk + 0.2)
            for h in self.households:
                if h.cash > 0:
                    withdraw = h.cash * 0.3
                    h.cash -= withdraw
                    for b in self.banks:
                        b.reserves -= withdraw

        sentiment = effect.get("stock_sentiment", 0.0)
        self.systemic_risk = min(1.0, self.systemic_risk + abs(sentiment) * 0.1)
        return effect["desc"]

    @property
    def health_score(self) -> float:
        """经济健康分（0-100）"""
        # GDP（偏离目标）：25分
        gdp_score = _clamp(self.gdp / DEFAULTS["gdp_target"], 0, 1) * 25

        # 失业率：25分（4%以下满分，30%以上零分）
        unemp_score = _clamp(1 - (self.unemployment - 0.04) / 0.26, 0, 1) * 25

        # 基尼系数：20分（0.25以下满分，0.7以上零分）
        gini_score = _clamp(1 - (self.gini - 0.25) / 0.45, 0, 1) * 20

        # 金融稳定（坏账+波动率）：15分
        bdr = getattr(self, "bank_bad_debt_rate", 0.0)
        vol = getattr(self, "stock_volatility", 0.0)
        fin_score = _clamp(1 - (bdr / 0.2 + vol / 0.4) / 2, 0, 1) * 15

        # 股市稳定性：15分
        vol_score = _clamp(1 - vol / 0.5, 0, 1) * 15

        return round(gdp_score + unemp_score + gini_score + fin_score + vol_score, 1)

    def _apply_shock(self) -> None:
        """随机外部冲击（按概率触发）"""
        if self.random.random() < DEFAULTS["shock_prob"]:
            self.current_shock = ""
            return

        shock_type = self.random.choice(list(SHOCK_EFFECTS.keys()))
        effect = SHOCK_EFFECTS[shock_type]
        logger.warning("⚡ 外部冲击触发：%s", effect["desc"])
        self.current_shock = effect["desc"]

        # TFP 变化
        prod_delta = effect.get("productivity", None)
        if callable(prod_delta):
            self.productivity = _clamp(prod_delta(self.productivity), 0.1, 5.0)

        # 银行恐慌：强制提取存款
        if effect.get("bank_run", False):
            self.systemic_risk = min(1.0, self.systemic_risk + 0.2)
            for h in self.households:
                if h.cash > 0:
                    withdraw = h.cash * 0.3
                    h.cash -= withdraw
                    for b in self.banks:
                        b.reserves -= withdraw

        # 系统性风险累计
        sentiment = effect.get("stock_sentiment", 0.0)
        self.systemic_risk = min(1.0, self.systemic_risk + abs(sentiment) * 0.1)

    def _gov_activity(self) -> None:
        """政府活动：购买商品（G→GDP）、发放补贴"""
        # 政府购买（新增：向企业采购，拉动总需求）
        firms = self.firms
        if self.gov_purchase > 0 and firms:
            purchase_per_firm = self.gov_purchase / len(firms)
            for f in firms:
                f.cash += purchase_per_firm
                f.inventory -= min(f.inventory, purchase_per_firm / f.price)

        # 失业补贴
        n_unemp = len(self.unemployed_households)
        total_subsidy = self.subsidy * n_unemp
        self.govt_expenditure = total_subsidy + self.gov_purchase
        for h in self.unemployed_households:
            h.cash += self.subsidy
        # govt_revenue 已在 Household.pay_taxes() 阶段累计，这里只扣减支出（可正可负）
        self.govt_revenue -= (total_subsidy + self.gov_purchase)

        # 量化宽松：央行直接购买股票（推高股价）
        if self.qe_amount > 0 and self.traders:
            self.stock_price += self.qe_amount / len(self.traders) * 0.01

    def _clear_markets(self) -> None:
        """股市 + 物价清算"""
        # ── 股市：戈登模型锚 + 供需扰动 + 系统风险 ─────────
        self.prev_stock_price = self.stock_price

        # 戈登模型内在价值
        # 用截至本轮末的完整累计值 = 上轮累加值 + 本轮值
        cycles_per_year = 12
        total_sofar = self.all_dividends + self.total_dividends
        avg_div_per_cycle = _safe_div(total_sofar, max(1, self.cycle + 1), 0.0)
        avg_div_annual = avg_div_per_cycle * cycles_per_year  # 年化股息
        avg_shares = max(1, len(self.firms) * 50)
        div_per_share_annual = avg_div_annual / avg_shares

        # Gordon: P = D / (r - g)，g=0（简化：股息永续，当前年化）
        # 折现率用存款利率（风险资产溢价 ~2%）
        disc_rate = self.base_interest_rate + 0.02
        gordon_price = _safe_div(div_per_share_annual, disc_rate, 50.0)
        # 合理区间：[20, 500]
        gordon_price = _clamp(gordon_price, 20.0, 500.0)

        # 供需定价（主导短期波动）
        net_order = self.buy_orders - self.sell_orders
        n = len(self.traders) or 1
        supply_delta = net_order / (n * 2) * DEFAULTS["stock_adjust_speed"]

        # 系统性风险压低股价
        risk_adj = 1.0 - self.systemic_risk * 0.3

        # 融合：供需主导（70%）+ Gordon 锚定（30%）
        self.stock_price = (
            self.stock_price * (1 + supply_delta) * 0.70 * risk_adj
            + gordon_price * 0.30
        )
        self.stock_price += self.random.uniform(-1.0, 1.0)
        self.stock_price = max(1.0, self.stock_price)

        # 滚动波动率
        if self.prev_stock_price > 0:
            ret = (self.stock_price - self.prev_stock_price) / self.prev_stock_price
            self.stock_returns.append(ret)
            winsz = DEFAULTS["vol_window"]
            if len(self.stock_returns) > winsz:
                self.stock_returns.pop(0)
            if len(self.stock_returns) >= 2:
                self.stock_volatility = float(np.std(self.stock_returns) * np.sqrt(252))

        # 系统性风险衰减（每轮自然消退一点）
        self.systemic_risk = max(0.0, self.systemic_risk - 0.01)

        # ── 物价：加权平均企业价格 + 通胀压力 ───────────────
        self.prev_avg_price = self.avg_price
        if self.firms:
            prices = [f.price for f in self.firms if f.inventory > 0]
            self.avg_price = np.mean(prices) if prices else self.avg_price
        else:
            self.avg_price = self.price_adjust_speed * 10

        # 通胀压力 = (GDP - target) / target × speed
        inflation = (
            (self.gdp - DEFAULTS["gdp_target"]) / DEFAULTS["gdp_target"]
            * DEFAULTS["price_adjust_speed"]
        )
        self.avg_price += self.random.uniform(-0.2, 0.2) + inflation
        self.avg_price = max(1.0, self.avg_price)

    def _compute_macro(self) -> None:
        """计算宏观指标"""
        # 每轮重置城际贸易统计
        self.city_a_exports = 0.0
        self.city_b_exports = 0.0
        self.city_a_imports = 0.0
        self.city_b_imports = 0.0

        self.gdp = compute_gdp(self)
        self.unemployment = compute_unemployment(self)
        self.gini = compute_gini(self)

        if self.firms:
            self.default_count = sum(
                1 for f in self.firms
                if f.default_probability > DEFAULTS["default_threshold"]
            )
        if self.banks:
            total_bad = sum(b.bad_debts for b in self.banks)
            total_loans = sum(b.total_loans for b in self.banks) + 1e-6
            # 坏账率：[0, 1]，防止负数/超限
            self.bank_bad_debt_rate = _clamp(total_bad / total_loans, 0.0, 1.0)

        # ── 城市级统计 ─────────────────────────────
        self._compute_city_stats()

    def _compute_city_stats(self) -> None:
        """计算双城指标（供 UI 对比面板使用）"""
        # 城市 A
        hh_a = [h for h in self.households if h.city == City.CITY_A]
        firm_a = [f for f in self.firms if f.city == City.CITY_A]
        self.city_a_pop = len(hh_a)
        self.city_a_firms = len(firm_a)
        self.city_a_unemp = (
            sum(1 for h in hh_a if not h.employed) / max(1, len(hh_a))
        )
        self.city_a_gdp = sum(
            h.goods * self.avg_price for h in hh_a
        ) + sum(
            f.production * f.price for f in firm_a
        ) + (self.city_a_exports - self.city_a_imports)
        # 城市 B
        hh_b = [h for h in self.households if h.city == City.CITY_B]
        firm_b = [f for f in self.firms if f.city == City.CITY_B]
        self.city_b_pop = len(hh_b)
        self.city_b_firms = len(firm_b)
        self.city_b_unemp = (
            sum(1 for h in hh_b if not h.employed) / max(1, len(hh_b))
        )
        self.city_b_gdp = sum(
            h.goods * self.avg_price for h in hh_b
        ) + sum(
            f.production * f.price for f in firm_b
        ) + (self.city_b_exports - self.city_b_imports)

    def _collect_data(self) -> None:
        self.datacollector.collect(self)

    # ── 政策干预（UI 按钮调用） ─────────────────────────────

    def adjust_interest_rate(self, delta: float) -> None:
        """利率政策传导：↑利率→企业成本↑→招聘↓→失业↑"""
        self.base_interest_rate = _clamp(self.base_interest_rate + delta, 0.0, 0.25)

    def adjust_tax_rate(self, delta: float) -> None:
        self.tax_rate = _clamp(self.tax_rate + delta, 0.0, 0.45)

    def adjust_subsidy(self, delta: float) -> None:
        self.subsidy = _clamp(self.subsidy + delta, 0.0, 50.0)

    def adjust_productivity(self, delta: float) -> None:
        self.productivity = _clamp(self.productivity + delta, 0.1, 5.0)

    def adjust_gov_purchase(self, delta: float) -> None:
        """政府购买（扩张性财政政策）"""
        self.gov_purchase = _clamp(self.gov_purchase + delta, 0.0, 200.0)

    def adjust_capital_gains_tax(self, delta: float) -> None:
        """资本利得税（抑制投机）"""
        self.capital_gains_tax = _clamp(
            getattr(self, "capital_gains_tax", 0.10) + delta, 0.0, 0.50
        )
