from __future__ import annotations
"""
Mesa 经济沙盘 - 核心模型 v4.6 (Layer 2/3 稳定器版)
基于 FRB/US · NAWM · ABCE 框架设计思路

v4.0 三大战役：
  ┌─────────────────────────────────────────────────────────────┐
  │ 一、复式簿记（资金闭环）                                    │
  │   - 工资发放：企业现金 → 员工现金                          │
  │   - 税收归集：统一 _collect_tax() → govt_wallet            │
  │   - 分红支付：企业现金 → 股东现金                          │
  ├─────────────────────────────────────────────────────────────┤
  │ 二、搜寻匹配定价（废除 avg_price）                          │
  │   - Household.consume()：搜寻3家选最低价                   │
  │   - Firm.price_goods()：按库存去化率动态定价               │
  ├─────────────────────────────────────────────────────────────┤
  │ 三、利率内生化                                              │
  │   - Bank._auto_adjust_rates()：根据准备金充裕度自动调节    │
  │   - 移除基准利率滑块，改为市场利率只读显示                 │
  ├─────────────────────────────────────────────────────────────┤
  │ v4.4 SFC 存量-流量一致性审计                                │
  │   - audit_stock_flow_consistency()：每轮 M0 守恒断言       │
  │   - 修复迁移成本泄漏（HH/Firm._consider_migration）        │
  │   - 修复求职成本泄漏（search_job → govt_wallet）           │
  │   - 修复消费重复记账（删除 Firm.sell_goods()）             │
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
        "wage_floor": 7.5,               # 最低工资
        "subsidy_rate": 0.10,          # 补贴率
        "infrastructure": 0.8,         # 基建水平（影响生产效率）
    },
    City.CITY_B: {
        "corporate_tax_rate": 0.18,    # 企业税率（高税高福利）
        "wage_floor": 6.8,               # 最低工资（劳动力便宜）
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
       # 基准利率
    # min_wage 已废除v4.0
    productivity=1.0,           # 全要素生产率（TFP）
    subsidy=5.0,                # 失业补贴（自动稳定器，防止需求塌缩）
    gov_purchase=50.0,         # 政府购买（自动稳定器，拉动基础需求）
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
from dataclasses import dataclass


@dataclass
class BalanceSheet:
    """通用资产负债表 — v4.0 资金守恒地基"""
    cash: float = 0.0
    deposits: float = 0.0
    inventory: float = 0.0
    loans_outstanding: float = 0.0
    loan_principal: float = 0.0
    shares_owned: int = 0
    cost_basis: float = 0.0
    capital_stock: float = 0.0
    rnd_investment: float = 0.0
    reserves: float = 0.0
    stocks_value: float = 0.0

    @property
    def liquid_assets(self) -> float:
        return self.cash + self.deposits

    @property
    def equity(self) -> float:
        return self.liquid_assets - self.loan_principal


def transfer(sender_bs, receiver_bs, amount, sender_bank=None,
              receiver_bank=None, tax_rate=0.0, govt=None) -> float:
    """统一资金转账 — 所有 Agent 间支付的唯一入口"""
    if amount <= 0:
        return 0.0
    tax = 0.0
    net = amount
    if tax_rate > 0 and govt is not None:
        tax = amount * tax_rate
        net = amount - tax
        govt.govt_revenue += tax
        govt.govt_wallet += tax  # 资金进入真实金库
    if sender_bank is not None and sender_bs.deposits >= net:
        sender_bs.deposits -= net
        sender_bank.deposits -= net
    else:
        from_dep = min(sender_bs.deposits, net) if sender_bank else 0.0
        from_cash = net - from_dep
        sender_bs.deposits -= from_dep
        if sender_bank:
            sender_bank.deposits -= from_dep
        sender_bs.cash -= from_cash
    if receiver_bank is not None:
        receiver_bs.deposits += net
        receiver_bank.deposits += net
    else:
        receiver_bs.cash += net
    return net


def bank_lend(bank, borrower_bs, amount) -> bool:
    """贷款创造存款 — M1 扩张"""
    if amount <= 0:
        return False
    bank.total_loans += amount
    bank.deposits += amount
    borrower_bs.deposits += amount
    return True


def bank_repay(bank, borrower_bs, principal, interest) -> float:
    """还贷 — M1 收缩"""
    total = principal + interest
    if total <= 0 or borrower_bs.deposits <= 0:
        return 0.0
    if borrower_bs.deposits < total:
        ratio = borrower_bs.deposits / total
        principal *= ratio
        interest *= ratio
        total = principal + interest
    borrower_bs.deposits -= total
    bank.deposits -= total
    bank.total_loans -= principal
    bank.reserves += interest
    return total


# ══════════════════════════════════════════════════════════════
# Layer 0: SFC 物理法则层 — 中央账本 + 政府/央行
# ══════════════════════════════════════════════════════════════

class Government:
    """政府与央行实体：统一接收税收、发放补贴、执行QE"""
    def __init__(self):
        self.unique_id = "GOV_CB"
        self.cash = 0.0  # 真实金库（替代 govt_wallet）
        self.total_printed_money = 0.0  # 记录央行合法印钞量


class Ledger:
    """中央清算账本：全系统唯一合法的资金流转通道"""
    def __init__(self, model):
        self.model = model

    def transfer(self, sender, receiver, amount: float, memo: str = "",
                 allow_overdraft: bool = False) -> bool:
        """原子化转账：sender_bal < amount 时返回 False（除非 allow_overdraft）"""
        if amount <= 0:
            return False
        # 自动识别资金字段（Bank用reserves，其余用cash）
        sender_attr = 'reserves' if hasattr(sender, 'reserves') else 'cash'
        receiver_attr = 'reserves' if hasattr(receiver, 'reserves') else 'cash'

        sender_bal = getattr(sender, sender_attr)
        if not allow_overdraft and sender_bal < amount:
            return False  # 余额不足，严禁透支

        # 原子化转账
        setattr(sender, sender_attr, sender_bal - amount)
        receiver_bal = getattr(receiver, receiver_attr)
        setattr(receiver, receiver_attr, receiver_bal + amount)
        return True

    def print_money(self, amount: float) -> None:
        """央行合法印钞：增加政府现金并记录"""
        if amount <= 0:
            return
        self.model.government.cash += amount
        self.model.government.total_printed_money += amount


class _MarketPool:
    """虚拟代理：股市流动性池（有cash属性，可被Ledger操作）"""
    def __init__(self):
        self.cash = 0.0


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
        # 保留工资（心理底线）：现金越少越急，现金多则挑剔
        self.reservation_wage: float = max(2.0, 8.0 * (1.0 - min(1.0, self.cash / 200.0)))
        # 效用参数（CES 替代弹性）
        self.utility_alpha: float = 0.4  # 商品消费权重（vs 储蓄）
        self.utility_rho: float = 0.5    # 替代弹性参数（0→柯布道格拉斯，1→完全替代）
        self.income_history: list[float] = []

    # ── 子行为 ─────────────────────────────────────────────

    def earn_wage(self, amount: float) -> None:
        """记录工资收入（现金由 Ledger 转入，此处仅记录）"""
        if amount <= 0:
            return
        self.income_history.append(amount)
        if len(self.income_history) > 12:
            self.income_history.pop(0)

    def pay_taxes(self) -> None:
        """缴纳个人所得税（通过 Ledger 转入政府金库）"""
        if self.salary <= 0:
            return
        tax = self.salary * self.model.tax_rate
        if tax > 0 and self.model.ledger.transfer(self, self.model.government, tax):
            self.model._collect_tax(tax)

    def repay_loan(self) -> None:
        """定期偿还贷款（含利息）—— 通过 Ledger 资金闭环到银行准备金"""
        if self.loan_principal <= 0:
            return
        rate = 0.05
        interest = self.loan_principal * rate
        repayment = min(max(2.0, self.cash * 0.05), self.loan_principal + interest)
        if self.cash >= repayment:
            # 资金闭环：还款进入银行准备金
            if self.model.banks:
                bank = self.random.choice(self.model.banks)
                if self.model.ledger.transfer(self, bank, repayment):
                    self.loan_principal = max(0.0, self.loan_principal - max(0.0, repayment - interest))

    def deposit(self) -> None:
        """存款：MPC越高→存款比例越低（高收入存更多）—— 通过 Ledger"""
        if self.cash <= 5:
            return
        # 高MPC（低收入）几乎不存款；低MPC（高收入）存款更多
        deposit_rate = (1 - self.mpc) * 0.3
        deposit = self.cash * deposit_rate
        if deposit > 1 and self.model.banks:
            bank = self.random.choice(self.model.banks)
            if self.model.ledger.transfer(self, bank, deposit):
                bank.deposits += deposit

    def _should_consume(self) -> bool:
        """效用最大化消费决策：
        - CES 效用：U = [α·G^(ρ-1)/ρ + (1-α)·C^(ρ-1)/ρ]^(ρ/(ρ-1))
        - 比较消费的边际效用 vs 储蓄的边际效用
        - 简化实现：基于现金缓冲、就业、价格水平的综合评分
        """
        # 现金充裕度：现金越多，越倾向消费
        cash_buffer = min(1.0, self.cash / 100.0)

        # 就业稳定性：有工作更敢消费
        employment_bonus = 0.3 if self.employed else 0.0

        # 价格惩罚：价格越高越克制消费
        avg_price = self.model.avg_price
        price_factor = max(0.0, 1.0 - (avg_price - 10.0) / 50.0) if avg_price > 10 else 1.0

        # 综合消费倾向
        propensity = self.mpc * cash_buffer * price_factor + employment_bonus
        return self.random.random() < min(0.95, propensity)

    def consume(self) -> None:
        """
        搜寻匹配消费（消费者驱动）—— 全程通过 Ledger
        
        1. 消费意愿由 MPC + 效用函数决定
        2. 消费者随机搜寻最多3家有库存的企业
        3. 选择最低价的企业购买（需有足够现金）
        4. 复式簿记：消费者现金 → 企业收入 → 企业交销售税给政府
        """
        if not self._should_consume():
            return

        # 搜寻匹配：从有库存企业中随机抽样最多3家
        firms_with_stock = self.model._cache.get('firms_with_stock', [])
        if not firms_with_stock:
            return

        n_search = min(3, len(firms_with_stock))
        candidates = self.random.sample(firms_with_stock, n_search) \
            if n_search < len(firms_with_stock) else list(firms_with_stock)

        # 选最低价
        best_firm = min(candidates, key=lambda f: f.price)

        # 检查支付能力
        if self.cash < best_firm.price:
            return

        # Ledger 复式簿记（M0 守恒）：
        # 1. 消费者支付 → 企业收入
        if not self.model.ledger.transfer(self, best_firm, best_firm.price):
            return
        # 2. 企业库存-1
        best_firm.inventory -= 1
        # 3. 企业缴纳销售税给政府
        tax = best_firm.price * self.model.tax_rate
        if tax > 0:
            if self.model.ledger.transfer(best_firm, self.model.government, tax):
                self.model._collect_tax(tax)
        # 4. 城际贸易追踪
        if self.city != best_firm.city:
            from model import City
            if best_firm.city == City.CITY_A:
                self.model.city_a_exports += best_firm.price - tax
                self.model.city_b_imports += best_firm.price - tax
            else:
                self.model.city_b_exports += best_firm.price - tax
                self.model.city_a_imports += best_firm.price - tax
        # 5. 统计本轮消费量（供 GDP 计算）
        self.goods += 1

    def invest(self) -> None:
        """股票投资：风险厌恶决定是否参与股市 —— 通过 Ledger"""
        price = self.model.stock_price
        # 风险厌恶高→几乎不参与
        if self.random.random() > (1 - self.risk_aversion) * 0.5 + 0.1:
            return
        # 买入（移动平均成本基准）
        if self.cash >= price * 2 and self.random.random() < self.stock_buy_prob:
            shares_bought = 2
            cost = price * shares_bought
            if self.model.ledger.transfer(self, self.model._market_pool, cost):
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
            # 从市场池取款（池不足则削减收益）
            available = self.model._market_pool.cash
            actual = min(available, proceeds)
            if actual > 0:
                self.model.ledger.transfer(self.model._market_pool, self, actual)
            # 支付资本利得税
            if tax > 0 and self.cash >= tax:
                if self.model.ledger.transfer(self, self.model.government, tax):
                    self.model._collect_tax(tax)
            self.model.capital_gains_tax_revenue += tax
            self.shares_owned -= shares_sold
            # 成本基准不变（平均成本法）
            self.model.sell_orders += shares_sold

    def search_job(self) -> None:
        """找工作（摩擦性失业）：求职成本→政府，实际招聘由 Firm.hire() 统一处理"""
        if self.employed:
            return
        # 求职成本（摩擦成本→政府就业服务费）—— 通过 Ledger
        cost = DEFAULTS["job_search_cost"]
        if self.cash > cost:
            self.model.ledger.transfer(self, self.model.government, cost)
        # 求职成功率：35%（有岗位就能找到）
        # 实际招聘（修改 employees/open_positions）由 Firm.hire() 统一处理

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
        my_min_wage = CITY_PARAMS[City.CITY_A]["wage_floor"] if my_city == City.CITY_A else CITY_PARAMS[City.CITY_B]["wage_floor"]
        other_min_wage = CITY_PARAMS[City.CITY_B]["wage_floor"] if my_city == City.CITY_A else CITY_PARAMS[City.CITY_A]["wage_floor"]
        wage_diff = other_min_wage - my_min_wage
        # 综合迁移评分
        migrate_score = unemp_diff * 3 + wage_diff * 0.5
        if migrate_score > 0.15 and self.random.random() < 0.5:
            self.city = other_city
            self.model.ledger.transfer(self, self.model.government, 50)  # M0 中性：现金→政府
            self.employed = False  # 摩擦性失业
            self.employer = None


    def consider_entrepreneurship(self) -> None:
        """创业机制：高现金+高技能的 Household 可以创建新企业
        
        条件：
        - 现金 > 300（启动资金）
        - 技能等级 >= 2（高技能）
        - 2% 概率触发（不是每轮都创业）
        - 当前企业数 < 上限（防止无限增长）
        """
        if self.random.random() > 0.02:
            return
        if self.cash < 300:
            return
        if self.skill_level < 2:
            return
        # 限制最大企业数
        if len(self.model.firms) >= 40:
            return

        # 启动资金 —— 通过 Ledger 转入新企业
        startup_cost = min(self.cash * 0.6, 500)

        # 选择行业（高技能→偏向科技/服务）
        industry_weights = {
            Industry.MANUFACTURING: 0.2,
            Industry.SERVICE: 0.4,
            Industry.TECH: 0.4,
        }
        industry = self.random.choices(
            list(industry_weights.keys()),
            weights=list(industry_weights.values()), k=1
        )[0]

        # 创建新企业（Firm.__init__ 赋予的随机 cash 会被覆盖）
        new_firm = Firm(self.model)
        new_firm.industry = industry
        new_firm._ind = INDUSTRY_PARAMS[industry]
        new_firm.wage_offer = 8.0 * new_firm._ind["wage_premium"]
        new_firm.cash = 0.0  # 先清零，由 Ledger 转入
        self.model.ledger.transfer(self, new_firm, startup_cost)
        self.model.firms.append(new_firm)
        self.model.agents.add(new_firm)

        # 创业者成为首任员工
        if self.employed:
            # 辞去当前工作
            self.employer.employees = max(0, self.employer.employees - 1)
            self.employer.open_positions += 1
        self.employed = True
        self.employer = new_firm
        self.salary = new_firm.wage_offer
        new_firm.employees = 1
        new_firm.open_positions = max(0, new_firm.open_positions - 1)
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
        # earn_wage() 现在由 Firm.pay_wages() 调用，此处不再调用
        self.pay_taxes()
        self.repay_loan()
        self.deposit()
        self.consume()
        self.invest()
        self.search_job()
        self.update_credit_score()
        self.update_wealth()
        self._consider_migration()
        self.consider_entrepreneurship()


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
        self.wage_offer: float = 8.0 * ip["wage_premium"]
        self.open_positions: int = self.random.randint(0, 4)
        self.production: float = 0.0
        self.inventory: float = 0.0
        self.loan_principal: float = 0.0
        self.wealth: float = self.cash

        # ── BalanceSheet 同步 ─────────────────────────
        self._bs = BalanceSheet()
        self._bs.cash = self.cash
        self._bs.loan_principal = self.loan_principal

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
        """招聘（受生命周期驱动：初创/衰退企业风格不同）—— 统一招聘入口"""
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
            if not candidates:
                break
            h = self.random.choice(candidates)

            # 工资议价（失业率越高，工人议价能力越弱）
            ur = self.model.unemployment
            wpremium = (DEFAULTS["skill_wage_premium_mid"] if h.skill_level == 1
                else DEFAULTS["skill_wage_premium_high"] if h.skill_level == 2 else 0.0)
            wage = self.wage_offer * (1 - ur * DEFAULTS["wage_bargain_strength"]) * (1 + wpremium)
            h.salary = max(h.reservation_wage, wage)

            h.employed = True
            h.employer = self
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
                * self.wage_offer
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
        内生定价（废除全局 avg_price 依赖）：
        
        纯粹基于自身微观信号：
        1. 边际成本锚底：cost_per_unit = capital_intensity × wage_offer × 0.5
        2. 库存去化率驱动：
           - 去化率 < 20% → 降价（需求不足）
           - 去化率 > 70% → 涨价（供不应求）
           - 中间 → 微调向边际成本靠拢
        3. 价格粘性：仅 price_stickiness 比例企业调价
        4. 竞争参考：同行业竞品均价（不使用全局 avg_price）
        """
        if self.price_change_cooldown > 0:
            self.price_change_cooldown -= 1
            return

        # 价格粘性
        if self.random.random() > DEFAULTS["price_stickiness"]:
            return

        # 边际成本
        cost_per_unit = self._ind["capital_intensity"] * self.wage_offer * 0.5
        cost_per_unit = max(cost_per_unit, 1.0)

        # 库存去化率（本轮卖了多少 / 总库存+产量）
        total_supply = self.inventory + max(0, self.production)
        if total_supply > 0:
            sell_rate = max(0, total_supply - self.inventory) / total_supply
        else:
            sell_rate = 0.5  # 无数据时中性

        # 基于去化率的定价决策
        if sell_rate < 0.20:
            # 需求严重不足 → 大幅降价去库存
            target = cost_per_unit * 0.75
        elif sell_rate < 0.40:
            # 需求偏弱 → 小幅降价
            target = cost_per_unit * 0.90
        elif sell_rate > 0.80:
            # 供不应求 → 涨价
            target = cost_per_unit * 1.25
        elif sell_rate > 0.65:
            # 需求偏强 → 小幅涨价
            target = cost_per_unit * 1.12
        else:
            # 中性 → 靠拢边际成本
            target = cost_per_unit * 1.02

        # 竞争参考（仅同行业，不使用全局 avg_price）
        industry_firms = [
            f for f in self.model._cache.get('firms_by_industry', {}).get(self.industry, [])
            if f is not self and hasattr(f, 'price') and f.price > 0
        ]
        if industry_firms:
            avg_peer = np.mean([f.price for f in industry_firms])
            # 混合自身信号(60%) + 同行信号(40%)
            target = 0.6 * target + 0.4 * avg_peer

        # 价格粘性调整：不直接跳到目标价，而是部分靠拢
        self.price = 0.7 * self.price + 0.3 * target
        self.price = max(1.0, cost_per_unit * 0.5, self.price)  # 硬性下限 1.0 + 边际成本50%

        # 调价冷却期
        self.price_change_cooldown = self.random.randint(1, 3)

    def pay_wages(self) -> None:
        """复式簿记工资发放：企业现金 → 员工现金（通过 Ledger）"""
        if self.employees <= 0:
            return
        my_employees = [
            h for h in self.model.households
            if h.employer is self and h.employed
        ]
        if not my_employees:
            return
        total_wage = sum(h.salary for h in my_employees)
        if total_wage <= 0 or self.cash <= 0:
            return

        # 如果现金不足，按比例削减工资（欠薪逻辑）
        ratio = min(1.0, self.cash / total_wage)
        for h in my_employees:
            wage = h.salary * ratio
            if wage > 0 and self.model.ledger.transfer(self, h, wage):
                h.earn_wage(wage)
            # 同步 BalanceSheet
            self._bs.cash = self.cash

    def pay_dividend(self) -> None:
        """生命周期决定分红率：成熟期高分红，初创期不分（通过 Ledger）"""
        if self.cash <= 50:
            return
        div_ratio = self._ind["div_ratio"]
        if self.lifecycle == FirmLifecycle.STARTUP:
            div_ratio *= 0.0   # 初创：不分红，留存扩产
        elif self.lifecycle == FirmLifecycle.DECLINE:
            div_ratio *= 1.5   # 衰退：变现资产

        profit = self.cash * div_ratio
        if profit <= 0:
            return

        self.dividend_per_share = _safe_div(profit, 50)
        self.model.total_dividends += profit

        # 分红给持有股票的 Trader 和 Household（通过 Ledger）
        shareholders = []
        total_shares = 0
        for t in self.model.traders:
            if t.shares > 0:
                shareholders.append(t)
                total_shares += t.shares
        for h in self.model.households:
            if h.shares_owned > 0:
                shareholders.append(h)
                total_shares += h.shares_owned
        if total_shares > 0:
            div_per_share = profit / total_shares
            for s in shareholders:
                shares_held = s.shares if hasattr(s, 'shares') else s.shares_owned
                amount = shares_held * div_per_share
                if amount > 0:
                    self.model.ledger.transfer(self, s, amount)

        # 同步 BalanceSheet（Ledger 已扣减 self.cash）
        self._bs.cash = self.cash

    def update_wage(self) -> None:
        """行业 + 生命周期决定工资调整策略"""
        ur = self.model.unemployment
        # 失业率高→压低工资（劳动市场宽松）；失业率低→提高工资（抢人）
        if ur > 0.15:
            self.wage_offer = _clamp(self.wage_offer * 0.97, 2.0, 50.0)
        elif self.inventory > 20 and self.lifecycle in (FirmLifecycle.GROWTH, FirmLifecycle.MATURE):
            self.wage_offer = _clamp(self.wage_offer * 1.04, 2.0, 50.0)

    def adjust_workforce(self) -> None:
        """生命周期 + 经济状态决定裁员/扩产：
        - 库存积压 → 裁员（与绝对库存挂钩，不依赖 production 初始值）
        - 库存不足 + 现金充足 → 扩招
        """
        # ── 扩招逻辑（库存低 + 现金充足，任何时候可触发）────
        # 扩招不看 production（初始 production=0 会导致永远不扩招）
        if self.employees > 0 and self.inventory < max(1.0, self.production) * 0.5 \
                and self.cash > self.wage_offer * 3:
            self.open_positions += 1

        # ── 裁员逻辑 ────────────────────────────────────────
        if self.employees == 0:
            return
        employed = self.model._cache.get('employees_of', {}).get(self.unique_id, [])
        if not employed:
            return

        # 用绝对库存判断（不受 production 初始值影响）
        # 库存 > 3 * (employees * base_productivity) 才触发裁员
        base_prod_per_worker = 1.5  # 每员工基准产出
        max_comfortable = self.employees * base_prod_per_worker
        if self.inventory > max_comfortable * 3.0:
            layoff_prob = self._ind["layoff_prob"]
            if self.lifecycle == FirmLifecycle.DECLINE:
                layoff_prob *= 2.0
            if self.random.random() < layoff_prob:
                n_layoff = min(len(employed), self.random.randint(1, 2))
                to_layoff = self.random.sample(list(employed), n_layoff)
                for h in to_layoff:
                    h.employed = False
                    h.employer = None
                    h.salary = 0.0
                self.employees -= n_layoff

    def apply_for_loan(self) -> None:
        """申请贷款（有信用审核）—— 通过 Ledger"""
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

        self.loan_principal += loan
        self.model.total_loans_outstanding += loan
        if self.model.banks:
            bank = self.random.choice(self.model.banks)
            bank.total_loans += loan
            # 通过 Ledger：银行准备金 → 企业现金
            self.model.ledger.transfer(bank, self, loan)

    def repay_loan(self) -> None:
        """
        偿还贷款 + 违约判定（通过 Ledger）
        """
        if self.loan_principal <= 0:
            return
        
        # 现金为负时不能还款
        if self.cash <= 0:
            if self.random.random() < self.default_probability:
                self._trigger_default()
            return
        
        rate = 0.05
        interest = self.loan_principal * rate * 0.1
        
        # 应还 = 利息 + 部分本金
        target_repayment = interest + 5
        # 实际还款 = min(目标, 现金)，确保非负
        repayment = min(target_repayment, self.cash)
        repayment = max(0, repayment)
        
        if repayment <= 0:
            return

        # 通过 Ledger：企业现金 → 银行准备金
        if self.model.banks:
            bank = self.random.choice(self.model.banks)
            if self.model.ledger.transfer(self, bank, repayment):
                self.loan_principal -= max(0.0, repayment - interest)
                self._bs.cash = self.cash
                self._bs.loan_principal = self.loan_principal
            elif self.random.random() < self.default_probability:
                self._trigger_default()
        else:
            # 无银行时的后备处理
            if self.cash >= repayment:
                self.cash -= repayment
                self.loan_principal -= max(0.0, repayment - interest)
                self._bs.cash = self.cash
                self._bs.loan_principal = self.loan_principal
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
            # 通知银行：企业消失，贷款清零（复式簿记：坏账冲减资本金）
            if self.loan_principal > 0:
                self.model.total_loans_outstanding -= self.loan_principal
                if self.model.banks:
                    bank = self.random.choice(self.model.banks)
                    loss = self.loan_principal * 0.8  # 破产损失率80%
                    bank.total_loans -= self.loan_principal
                    bank.bad_debts += loss
                    bank.capital = max(0.0, bank.capital - loss)  # 呆账核销：冲减资本金

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
        # 同步 BalanceSheet
        self._bs.cash = self.cash
        self._bs.loan_principal = self.loan_principal

    # ── 主循环 ─────────────────────────────────────────────

    def step(self) -> None:
        if self.check_bankruptcy():
            return  # 已破产，不再执行
        self.hire()
        self.produce()
        self.price_goods()
        self.pay_wages()       # 复式簿记：工资从企业流向员工
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
            self.model.ledger.transfer(self, self.model.government, 200)  # M0 中性：现金→政府
            # 20% 员工离职
            if self.employees > 0:
                n_quit = max(1, int(self.employees * 0.2))
                employed = self.model._cache.get('employees_of', {}).get(self.unique_id, [])
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
        self.loan_rate: float = 0.05   # Bank loan rate (endogenous)
        self.deposit_rate: float = 0.02  # Bank deposit rate (endogenous)
        self.total_loans: float = 0.0
        self.bad_debts: float = 0.0
        self.wealth: float = self.reserves
        # 资本金（用于巴塞尔协议）
        self.capital: float = bp["initial_reserves"] * 0.1
        self._loans: dict[int, float] = {}  # 记录本银行的贷款明细（borrower_id → loan_amount），修复越界问题

        # ── BalanceSheet 同步 ─────────────────────────
        self._bs = BalanceSheet()
        self._bs.reserves = self.reserves
        self._bs.loans_outstanding = self.total_loans
        self._bs.deposits = self.deposits

    def _effective_rate(self, borrower: Agent) -> float:
        """
        差异化利率 = 基准利率 + 信用利差 + 银行风险偏好
        信用评分低 → 利率高（风险溢价）
        银行保守型 → 利差高
        """
        base = self.loan_rate
        credit_score = getattr(borrower, "credit_score", 600)
        # 信用评分映射到利差：[850分→+0%，300分→+5%]
        score_penalty = (DEFAULTS["credit_score_max"] - credit_score) / (DEFAULTS["credit_score_max"] - DEFAULTS["credit_score_min"]) * 0.05
        return self.loan_rate + self.lending_spread + score_penalty

    def pay_deposit_interest(self) -> None:
        """
        支付存款利息（通过 Ledger）：
          银行准备金 → 储户现金，M0 守恒。
        """
        if self.deposits <= 0:
            return
            
        rate = self.deposit_rate
        total_interest = self.deposits * rate
        
        # 确保银行有足够的准备金支付利息
        total_interest = min(total_interest, self.reserves * 0.1)
        if total_interest <= 0:
            return
        
        # 按存款比例分配给储户
        depositors = list(self.model.households) + list(self.model.firms)
        total_deposits = sum(getattr(d, 'cash', 0) for d in depositors)
        
        if total_deposits <= 0:
            return
        
        for d in depositors:
            deposit = getattr(d, 'cash', 0)
            if deposit > 0:
                share = (deposit / total_deposits) * total_interest
                if share > 0:
                    self.model.ledger.transfer(self, d, share)

        # 同步 BalanceSheet
        self._bs.reserves = self.reserves

    def lend(self) -> None:
        """
        复式簿记放贷（通过 Ledger）：
          银行准备金 → 借款人现金，M0 不变。
        """
        if self.reserves <= 50:
            return

        # 资本金充足率检查（风险加权资产 = 贷款额 × 1.0）
        capital_ratio = self.capital / max(1.0, self.total_loans)
        if capital_ratio < 0.08:
            # 低于8%：强制收缩（巴塞尔III）
            for borrower_id, loan_amount in list(self._loans.items()):
                if loan_amount <= 0:
                    continue
                borrower = None
                for h in self.model.households:
                    if h.unique_id == borrower_id:
                        borrower = h
                        break
                if not borrower:
                    for f in self.model.firms:
                        if f.unique_id == borrower_id:
                            borrower = f
                            break
                if borrower and borrower.cash >= loan_amount * 0.3:
                    # 提前收回30% —— 通过 Ledger
                    repay = loan_amount * 0.3
                    if self.model.ledger.transfer(borrower, self, repay):
                        borrower.loan_principal -= repay
                        self._loans[borrower_id] -= repay
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

            # ═══ 复式簿记：贷款创造存款（通过 Ledger）═══
            # 1. 银行资产端：增加贷款债权
            self._loans[b.unique_id] = self._loans.get(b.unique_id, 0) + amount
            self.total_loans += amount
            self.model.total_loans_outstanding += amount
            
            # 2. 通过 Ledger：银行准备金 → 借款人现金
            self.model.ledger.transfer(self, b, amount)
            
            # 3. 借款人负债端：增加贷款债务
            b.loan_principal = existing + amount

            # 同步 BalanceSheet
            self._bs.reserves = self.reserves
            self._bs.loans_outstanding = self.total_loans
            if not hasattr(b, 'creditor_bank') or not b.creditor_bank:
                b.creditor_bank = set()
            b.creditor_bank.add(id(self))

    def update_bad_debts(self) -> None:
        """
        坏账 = Σ(本银行债务人违约概率 × 本银行对其贷款额 × 损失率)
        修复：只统计自己发放的贷款，不越界统计其他银行的贷款
        """
        total = 0.0
        for borrower_id, loan_principal in list(self._loans.items()):
            # 从模型中查找对应债务人（用 unique_id 匹配）
            borrower = next(
                (a for a in list(self.model.households) + list(self.model.firms)
                 if a.unique_id == borrower_id), None
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
        # 同步 BalanceSheet
        self._bs.reserves = self.reserves
        self._bs.loans_outstanding = self.total_loans
        self._bs.deposits = self.deposits

    def step(self) -> None:
        self._auto_adjust_rates()
        self.pay_deposit_interest()
        self.lend()
        self.update_bad_debts()
        self.update_wealth()

    def _auto_adjust_rates(self) -> None:
        """Endogenized rates: bank decides rates based on balance sheet"""
        total_assets = self.reserves + self.total_loans
        reserve_ratio = self.reserves / max(1.0, total_assets)
        bad_debt_ratio = self.bad_debts / max(1.0, self.total_loans)

        # --- Loan rate: low reserves -> hike, high -> cut ---
        target_loan = self.loan_rate
        if reserve_ratio < 0.10:
            target_loan += 0.005
        elif reserve_ratio < 0.20:
            target_loan += 0.002
        elif reserve_ratio > 0.50:
            target_loan -= 0.003

        # Bad debt compensation
        if bad_debt_ratio > 0.20:
            target_loan += 0.003
        elif bad_debt_ratio > 0.10:
            target_loan += 0.001

        # Smooth transition
        self.loan_rate = 0.7 * self.loan_rate + 0.3 * target_loan
        self.loan_rate = max(0.01, min(0.25, self.loan_rate))

        # --- Deposit rate = loan rate - spread ---
        self.deposit_rate = max(0.0, self.loan_rate - self.lending_spread)
        
        # --- Original spread adjustment logic preserved ---
        base_spread = BANK_PARAMS[self.bank_type]["lending_spread"]
        target_spread = base_spread
        if reserve_ratio < 0.10:
            target_spread += 0.03
        elif reserve_ratio < 0.20:
            target_spread += 0.01
        elif reserve_ratio > 0.50:
            target_spread -= 0.015
        if bad_debt_ratio > 0.20:
            target_spread += 0.02
        elif bad_debt_ratio > 0.10:
            target_spread += 0.01
        self.lending_spread = 0.7 * self.lending_spread + 0.3 * target_spread
        self.lending_spread = max(0.005, min(0.10, self.lending_spread))
        
        # Update deposit rate (spread changed, update again)
        self.deposit_rate = max(0.0, self.loan_rate - self.lending_spread)


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

        # ── BalanceSheet 同步 ─────────────────────────
        self._bs = BalanceSheet()
        self._bs.cash = self.cash
        self._bs.stocks_value = self.shares * model.stock_price

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
            if m.ledger.transfer(self, m._market_pool, cost):
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
        """价值投资：内在价值低估则买，高估则卖 —— 通过 Ledger"""
        m = self.model
        # 戈登模型估计内在价值
        avg_div_per_share = m.total_dividends / max(1, len(m.firms) * 50)
        intrinsic = self._gordon_value(avg_div_per_share, 0.05)
        # 平滑估计
        self.intrinsic_value_estimate = 0.7 * self.intrinsic_value_estimate + 0.3 * intrinsic

        # 折价20%以上 → 买入；溢价20%以上 → 卖出
        if price < self.intrinsic_value_estimate * 0.80 and self.cash >= price:
            if m.ledger.transfer(self, m._market_pool, price):
                self.shares += 1
                # 移动平均成本基准
                total_cost = self.cost_basis * (self.shares - 1) + price
                self.cost_basis = total_cost / self.shares
                m.buy_orders += 1
        elif price > self.intrinsic_value_estimate * 1.20 and self.shares > 0:
            self._sell(1, price)

    def _trade_noise(self, price: float) -> None:
        """噪声交易：随机买卖（模拟散户非理性行为）—— 通过 Ledger"""
        m = self.model
        if self.cash >= price and self.random.random() < 0.2:
            cost = price
            if m.ledger.transfer(self, m._market_pool, cost):
                total_cost = self.cost_basis * self.shares + cost
                self.shares += 1
                self.cost_basis = total_cost / self.shares
                m.buy_orders += 1
        if self.shares > 0 and self.random.random() < 0.18:
            self._sell(1, price)

    def _trade_market_maker(self, price: float) -> None:
        """做市商：双向挂单，赚取买卖价差 —— 通过 Ledger"""
        m = self.model
        spread = self.bid_ask_spread
        bid = price * (1 - spread)
        ask = price * (1 + spread)

        # 买入
        if self.cash >= ask and self.random.random() < 0.4:
            if m.ledger.transfer(self, m._market_pool, ask):
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
        """卖出 n 股，含资本利得税，从市场流动性池收款（通过 Ledger）"""
        if n <= 0 or self.shares < n:
            return
        proceeds = price * n
        cost = self.cost_basis * n
        gain = max(0.0, proceeds - cost)
        tax = gain * self.model.capital_gains_tax

        # 从市场流动性池取款（M0 闭环：池不够则削减收益）
        available = self.model._market_pool.cash
        actual = min(available, proceeds)
        if actual > 0:
            self.model.ledger.transfer(self.model._market_pool, self, actual)

        # 支付资本利得税
        if tax > 0 and self.cash >= tax:
            if self.model.ledger.transfer(self, self.model.government, tax):
                self.model._collect_tax(tax)
        self.model.capital_gains_tax_revenue += tax
        # 已实现收益累计（不含税）
        self.realized_gains += gain - tax
        self.shares -= n
        self.model.sell_orders += n

    def update_wealth(self) -> None:
        self.wealth = self.cash + self.shares * self.model.stock_price
        # 同步 BalanceSheet
        self._bs.cash = self.cash
        self._bs.stocks_value = self.shares * self.model.stock_price

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
        # ── Layer 0: SFC 物理法则 ──────────────────────
        self.government = Government()
        self.ledger = Ledger(self)

        # ── 股市流动性池（买方出资、卖方收款，M0 守恒） ───
        self._market_pool = _MarketPool()

        # ── SFC 审计 ───────────────────────────────────────
        self._initial_m0: float = 0.0              # 将在 setup 后计算

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
        self.city_a_subsidy = CITY_PARAMS[City.CITY_A]["subsidy_rate"]
        self.city_a_infra = CITY_PARAMS[City.CITY_A]["infrastructure"]
        self.city_b_tax = CITY_PARAMS[City.CITY_B]["corporate_tax_rate"]
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
            # 初始员工分配（打破 employees=0 的死循环）
            n_init = self.random.randint(3, 6)
            candidates = list(self.households)
            for _ in range(n_init):
                if not candidates:
                    break
                h = self.random.choice(candidates)
                candidates.remove(h)
                h.employed = True
                h.employer = f
                h.salary = f.wage_offer
                f.employees += 1
                f.open_positions = 0
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
        self._initial_m0 = self._calc_m0()  # 记录初始 M0 基准

    @property
    def market_cash_pool(self) -> float:
        """股市流动性池（兼容旧接口）"""
        return self._market_pool.cash

    @market_cash_pool.setter
    def market_cash_pool(self, value: float) -> None:
        self._market_pool.cash = value

    def _refresh_cache(self) -> None:
        """在每个 step 末尾刷新查找缓存，将 O(n²) 遍历降为 O(1) 查找"""
        self._cache = {
            "firms_with_stock": [f for f in self.firms if f.inventory > 0],
            "firms_with_jobs": [f for f in self.firms if f.open_positions > 0],
            "firms_by_industry": {ind: [f for f in self.firms if f.industry == ind] for ind in Industry},
            "employed_hh": [h for h in self.households if h.employed],
            # 企业→员工列表映射（用于 Firm 批量裁员）
            "employees_of": (
                lambda: (d := {f.unique_id: [] for f in self.firms},
                         [d[h.employer.unique_id].append(h) for h in self.households if h.employed and h.employer],
                         d)[2]
            )(),
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

        # 7. SFC 资金守恒审计
        self.audit_sfc()

        # 8. 刷新运行时缓存（Phase 0A）
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
        # 重置消费者消费统计（goods = 本周期消费次数，供 GDP 计算）
        for h in self.households:
            h.goods = 0

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
            for b in self.banks:
                run_amount = b.reserves * 0.3
                if run_amount > 0:
                    b.deposits -= run_amount
                    per_capita = run_amount / max(1, len(self.households))
                    for h in self.households:
                        self.ledger.transfer(b, h, per_capita)

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

        # 银行恐慌：挤兑提取（银行储备→居民现金，M0 不变）—— 通过 Ledger
        if effect.get("bank_run", False):
            self.systemic_risk = min(1.0, self.systemic_risk + 0.2)
            for b in self.banks:
                run_amount = b.reserves * 0.3
                if run_amount > 0:
                    b.deposits -= run_amount
                    # 随机分配给居民 —— 通过 Ledger
                    per_capita = run_amount / max(1, len(self.households))
                    for h in self.households:
                        self.ledger.transfer(b, h, per_capita)

        # 系统性风险累计
        sentiment = effect.get("stock_sentiment", 0.0)
        self.systemic_risk = min(1.0, self.systemic_risk + abs(sentiment) * 0.1)

    def _gov_activity(self) -> None:
        """政府活动：购买商品（G→GDP）、发放补贴（通过 Ledger 从 government.cash 支出）"""
        total_spending = 0.0

        # 政府购买（向企业采购，拉动总需求）—— 通过 Ledger
        firms = self.firms
        if self.gov_purchase > 0 and firms:
            purchase_per_firm = self.gov_purchase / len(firms)
            for f in firms:
                self.ledger.transfer(self.government, f, purchase_per_firm,
                                     allow_overdraft=True)
                f.inventory -= min(f.inventory, purchase_per_firm / f.price)
            total_spending += self.gov_purchase

        # 失业补贴 —— 通过 Ledger（允许政府赤字）
        n_unemp = len(self.unemployed_households)
        total_subsidy = self.subsidy * n_unemp
        for h in self.unemployed_households:
            self.ledger.transfer(self.government, h, self.subsidy,
                                 allow_overdraft=True)
        total_spending += total_subsidy

        self.govt_expenditure = total_spending
        # 统计：收入 - 支出 = 净财政余额
        self.govt_revenue -= total_spending

        # 量化宽松：央行直接购买股票（推高股价，合法印钞）
        if self.qe_amount > 0 and self.traders:
            self.ledger.print_money(self.qe_amount)
            self.ledger.transfer(self.government, self._market_pool, self.qe_amount,
                                 allow_overdraft=True)
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
        avg_loan_rate = sum(b.loan_rate for b in self.banks) / max(1, len(self.banks))
        disc_rate = avg_loan_rate + 0.02
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

    # ── 政府金库 helper ────────────────────────────────────

    def _collect_tax(self, amount: float) -> None:
        """统一税收入口：更新统计值（现金通过 Ledger 流入 government.cash）"""
        self.govt_revenue += amount
        # 注意：现金流转由调用方通过 ledger.transfer 完成，此处不再操作 govt_wallet

    # ── SFC 资金守恒审计 ─────────────────────────────────

    def _calc_m0(self) -> float:
        """计算当前系统 M0（所有现金 + 银行准备金 + 政府金库 + 股市池）"""
        total = self.government.cash + self._market_pool.cash
        total += sum(h.cash for h in self.households)
        total += sum(f.cash for f in self.firms)
        total += sum(b.reserves for b in self.banks)
        total += sum(t.cash for t in self.traders)
        return total

    def audit_sfc(self) -> None:
        """Layer 0: 绝对物理法则锁（SFC 审计）

        任何绕过 Ledger 的私人加钱都会被立刻捕获。
        合法 M0 变动源：央行印钞（total_printed_money）。
        """
        current_m0 = self.government.cash + self._market_pool.cash
        current_m0 += sum(h.cash for h in self.households)
        current_m0 += sum(f.cash for f in self.firms)
        current_m0 += sum(b.reserves for b in self.banks)
        current_m0 += sum(t.cash for t in self.traders)

        expected_m0 = self._initial_m0 + self.government.total_printed_money

        diff = current_m0 - expected_m0
        if abs(diff) > 1e-3:  # 容忍极微小浮点误差
            raise RuntimeError(
                f"🚨 SFC致命崩溃: 第{self.cycle}轮 M0漂移 {diff:+0.4f}！\n"
                f"初始总资金: {self._initial_m0:.4f} | 当前总资金: {current_m0:.4f}\n"
                f"说明有代码绕过了 Ledger 进行私人加钱，必须立即排查！"
            )

    # ── 政策干预（UI 按钮调用） ─────────────────────────────

    def adjust_interest_rate(self, delta: float) -> None:
        """Policy transmission: adjust all banks' loan rates"""
        for b in self.banks:
            b.loan_rate = _clamp(b.loan_rate + delta, 0.01, 0.25)


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
