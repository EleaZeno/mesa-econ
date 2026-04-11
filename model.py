"""
Mesa 经济沙盘 - 核心模型 v2.0
全面经济系统沙盒：消费者 × 企业 × 银行 × 交易者 × 政府

经济逻辑：
  - 商品市场：企业生产 → 居民消费（支出法GDP）
  - 劳动力市场：企业招聘 → 居民就业/失业
  - 信贷市场：银行贷款 ↔ 企业/居民借贷，违约同步记录
  - 股票市场：交易者动量策略 + 居民参与
  - 政府财政：税收收入 - 失业补贴支出

Mesa 3.x 最佳实践：
  - 分阶段调度（Bank → Firm → Household → Trader）
  - Agent 分类缓存（避免 O(n²) 重复筛选）
  - 参数全部可配置（无魔法数字）
  - 数据收集器含 Agent 类型标签
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
from mesa import Agent, Model
from mesa import DataCollector

if TYPE_CHECKING:
    from numpy.random import RandomGenerator

logger = logging.getLogger("econ")

# ─────────────────────────────────────────────
# 全局默认值（所有魔法数字的唯一起源地）
# ─────────────────────────────────────────────

DEFAULTS = dict(
    n_households=20,
    n_firms=10,
    n_banks=2,
    n_traders=20,
    # 政策参数
    tax_rate=0.15,
    base_interest_rate=0.05,
    min_wage=7.0,
    productivity=1.0,
    subsidy=0.0,
    # 行为参数
    consume_prob=0.6,          # 居民消费概率
    job_search_prob=0.3,       # 居民求职概率
    stock_buy_prob=0.1,        # 居民买股概率
    stock_sell_prob=0.08,      # 居民卖股概率
    bank_loan_amount=20.0,     # 银行单次放贷额
    bank_lending_spread=0.02,  # 银行贷款利差
    deposit_rate=0.01,         # 银行存款利率
    div_profit_ratio=0.05,     # 企业股息发放比例
    loan_cap=500.0,           # 企业借贷上限
    household_loan_cap=200.0, # 居民借贷上限
    household_deposit_rate=0.2,  # 居民存款比例（预防银行储备耗尽）
    # 生产参数
    wage_base=2.0,             # 基础工资 = employees * wage_base
    production_noise_std=2.0, # 生产随机波动
    # 宏观锚点
    gdp_target=1500.0,         # 物价稳定时的目标GDP
    price_adjust_speed=0.1,    # 物价调整速度
    stock_adjust_speed=0.03,   # 股价调整速度
    vol_window=10,            # 波动率滚动窗口
    # 风险参数
    default_threshold=0.5,    # 违约概率阈值（超过则计入 default_count）
)


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def _clamp(val: float, lo: float, hi: float) -> float:
    """将 val 限制在 [lo, hi] 范围内"""
    return max(lo, min(hi, val))


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    """安全除法"""
    return a / b if b != 0 else default


# ─────────────────────────────────────────────
# 代理人
# ─────────────────────────────────────────────

class Household(Agent):
    """
    消费者行为：
      get_wage()  → 领工资（扣税）
      repay_loan() → 偿还贷款
      deposit()    → 存款（补充银行储备）
      consume()    → 消费商品
      buy_stock()  → 买股票
      sell_stock() → 卖股票
      find_job()   → 找工作
      update_wealth() → 更新财富（cash - 贷款 + 股票市值）
    """

    def __init__(self, model: EconomyModel):
        super().__init__(model)
        self.cash: float = self.random.uniform(50, 200)
        self.goods: int = 0
        self.salary: float = 0.0
        self.employed: bool = False
        self.wealth: float = self.cash
        self.loan_principal: float = 0.0
        self.shares_owned: int = 0

    # ── 子行为 ──────────────────────────────────

    def get_wage(self) -> None:
        """领取工资，扣除所得税"""
        if not self.employed or self.salary <= 0:
            return
        wage = self.salary
        tax = wage * self.model.tax_rate
        self.cash += wage - tax
        self.model.govt_revenue += tax

    def repay_loan(self) -> None:
        """偿还贷款本金+利息"""
        if self.loan_principal <= 0:
            return
        rate = self.model.base_interest_rate
        interest = self.loan_principal * rate
        repayment = min(self.model.min_wage * 0.1, self.loan_principal + interest)
        if self.cash >= repayment:
            self.cash -= repayment
            # 利息交给银行（模拟信贷市场摩擦）
            self.loan_principal = max(0.0, self.loan_principal - max(0.0, repayment - interest))

    def deposit(self) -> None:
        """存款：将部分现金存入银行（补充银行储备，防止储备耗尽）"""
        if self.cash <= 5:
            return
        deposit_amount = self.cash * self.model.deposit_rate
        if deposit_amount > 1 and self.model.banks:
            self.cash -= deposit_amount
            bank = self.random.choice(self.model.banks)
            bank.reserves += deposit_amount
            bank.deposits += deposit_amount

    def consume(self) -> None:
        """用现金购买商品（受概率控制）"""
        if self.cash <= self.model.price_index:
            return
        if self.random.random() < self.model.consume_prob:
            self.cash -= self.model.price_index
            self.goods += 1

    def buy_stock(self) -> None:
        """持有现金时以一定概率买入股票"""
        if self.cash <= self.model.stock_price:
            return
        if self.random.random() < self.model.stock_buy_prob:
            self.cash -= self.model.stock_price
            self.shares_owned += 1
            self.model.buy_orders += 1

    def sell_stock(self) -> None:
        """持有股票时以一定概率卖出（保留 20% 仓位）"""
        if self.shares_owned <= 1:
            return
        if self.random.random() < self.model.stock_sell_prob:
            self.cash += self.model.stock_price * 0.95
            self.model.sell_orders += 1
            self.shares_owned -= 1

    def find_job(self) -> None:
        """失业时随机求职"""
        if self.employed:
            return
        if self.model.firms and self.random.random() < self.model.job_search_prob:
            firm = self.random.choice(self.model.firms)
            if firm.open_positions > 0:
                firm.open_positions -= 1
                self.employed = True
                self.salary = firm.wage_offer

    def update_wealth(self) -> None:
        """
        财富 = 现金 - 负债 + 股票市值
        贷款是负债，从财富中扣除（正确逻辑）
        """
        stock_value = self.shares_owned * self.model.stock_price
        self.wealth = self.cash - self.loan_principal + stock_value

    # ── 主循环 ──────────────────────────────────

    def step(self) -> None:
        self.get_wage()
        self.repay_loan()
        self.deposit()
        self.consume()
        self.buy_stock()
        self.sell_stock()
        self.find_job()
        self.update_wealth()


class Firm(Agent):
    """
    企业行为：
      hire()       → 招聘（开放岗位→匹配失业居民）
      produce()    → 生产（规模报酬递减：sqrt(employees)）
      sell_goods() → 向居民销售商品
      pay_dividend() → 发放股息
      update_wage() → 动态调整工资
      repay_loan() → 偿还贷款（含违约判定）
      update_wealth() → 更新财富
    """

    def __init__(self, model: EconomyModel):
        super().__init__(model)
        self.cash: float = self.random.uniform(200, 800)
        self.employees: int = 0
        self.wage_offer: float = max(model.min_wage, 8.0)
        self.open_positions: int = self.random.randint(0, 3)
        self.production: float = 0.0
        self.inventory: float = 0.0
        self.loan_principal: float = 0.0
        self.wealth: float = self.cash
        self.dividend_per_share: float = 0.0
        self.default_probability: float = 0.0
        self.wage_base: float = DEFAULTS["wage_base"]

    # ── 子行为 ──────────────────────────────────

    def hire(self) -> None:
        """从失业居民池中招聘"""
        if self.open_positions <= 0:
            return
        unemployed = self.model.unemployed_households
        n_hire = min(self.open_positions, len(unemployed))
        for _ in range(n_hire):
            if unemployed:
                h = self.random.choice(unemployed)
                h.employed = True
                h.salary = self.wage_offer
                self.employees += 1
                unemployed.remove(h)
        self.open_positions = 0

    def produce(self) -> None:
        """
        规模报酬递减生产函数：
          output = sqrt(employees) * wage_base * productivity + noise
        符合经济学"边际产出递减"规律
        """
        efficiency = self.model.productivity
        self.production = (
            np.sqrt(max(0, self.employees)) * self.wage_base * efficiency
            + self.random.gauss(0, DEFAULTS["production_noise_std"])
        )
        self.production = max(0.0, self.production)
        self.inventory += self.production

    def sell_goods(self) -> None:
        """向居民销售商品"""
        if self.inventory <= 0:
            return
        buyers = self.random.sample(
            self.model.households, min(len(self.model.households), 5)
        )
        for h in buyers:
            if (
                h.cash >= self.model.price_index
                and h.goods < 10
                and self.inventory > 0
            ):
                h.cash -= self.model.price_index
                h.goods += 1
                after_tax = self.model.price_index * (1 - self.model.tax_rate)
                self.cash += after_tax
                self.inventory -= 1

    def pay_dividend(self) -> None:
        """利润中提取固定比例作为股息"""
        if self.cash <= 100:
            return
        profit = self.cash * DEFAULTS["div_profit_ratio"]
        self.cash -= profit
        self.dividend_per_share = _safe_div(profit, 50)
        self.model.total_dividends += profit

    def update_wage(self) -> None:
        """根据库存动态调整工资（经济周期自动调节）"""
        if self.inventory > 10:
            self.wage_offer = _clamp(
                self.wage_offer * 1.05,
                self.model.min_wage,
                self.model.min_wage * 10,
            )
        elif self.employees == 0 and self.inventory < 3:
            self.wage_offer = _clamp(
                self.wage_offer * 0.95,
                self.model.min_wage,
                self.model.min_wage * 10,
            )

    def repay_loan(self) -> None:
        """偿还贷款，若无力偿还则判定违约（同步通知银行）"""
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
        """触发违约：通知银行同步坏账，更新全局贷款余额"""
        logger.warning(
            "企业%d违约！现金%.1f，负债%.1f，违约概率%.2f",
            self.unique_id, self.cash, self.loan_principal, self.default_probability,
        )
        if self.model.banks:
            bank = self.random.choice(self.model.banks)
            bank.total_loans -= self.loan_principal
            bank.bad_debts += self.loan_principal
        self.model.total_loans_outstanding -= self.loan_principal
        self.model.default_count += 1
        self.loan_principal = 0.0

    def apply_for_loan(self) -> None:
        """现金不足以发工资时向银行申请贷款"""
        wage_bill = self.employees * self.wage_offer
        if self.cash >= wage_bill * 0.5:
            return
        if self.loan_principal >= DEFAULTS["loan_cap"]:
            return
        loan = min(DEFAULTS["bank_loan_amount"], DEFAULTS["loan_cap"] - self.loan_principal)
        if loan <= 0:
            return
        self.cash += loan
        self.loan_principal += loan
        self.model.total_loans_outstanding += loan
        if self.model.banks:
            bank = self.random.choice(self.model.banks)
            bank.total_loans += loan
            bank.reserves -= loan
        logger.debug(
            "企业%d贷款%.1f（累计负债%.1f，现金%.1f）",
            self.unique_id, loan, self.loan_principal, self.cash,
        )

    def update_default_probability(self) -> None:
        """违约概率 = 1 - 现金/负债比（上限1.0）"""
        total_debt = self.loan_principal + 1e-6
        self.default_probability = _clamp(1.0 - self.cash / total_debt, 0.0, 1.0)

    def update_wealth(self) -> None:
        """
        企业财富 = 现金 - 负债 + 库存价值
        库存按当期物价指数定价
        """
        inventory_value = self.inventory * self.model.price_index
        self.wealth = self.cash - self.loan_principal + inventory_value

    # ── 主循环 ──────────────────────────────────

    def step(self) -> None:
        self.hire()
        self.produce()
        self.sell_goods()
        self.pay_dividend()
        self.update_wage()
        self.update_default_probability()
        self.apply_for_loan()
        self.repay_loan()
        self.update_wealth()


class Bank(Agent):
    """
    银行行为：
      pay_deposit_interest() → 向存款人支付利息（储备减少）
      lend()                  → 向居民和企业放贷
      update_bad_debts()      → 按企业违约概率估算坏账
      update_wealth()         → 财富 = 准备金 + 有效贷款（扣除坏账）
    """

    def __init__(self, model: EconomyModel):
        super().__init__(model)
        self.reserves: float = 1000.0
        self.deposits: float = 0.0
        self.total_loans: float = 0.0
        self.bad_debts: float = 0.0
        self.wealth: float = self.reserves

    def pay_deposit_interest(self) -> None:
        """支付存款利息（从储备中扣减）"""
        interest = self.deposits * DEFAULTS["deposit_rate"]
        self.reserves -= interest

    def lend(self) -> None:
        """向居民和企业放贷"""
        if self.reserves <= 50:
            return
        loan_amt = DEFAULTS["bank_loan_amount"]
        # 混合抽取借款者
        borrowers = self.model.households + self.model.firms
        for _ in range(min(3, len(borrowers))):
            if self.reserves <= loan_amt:
                break
            b = self.random.choice(borrowers)
            existing = getattr(b, "loan_principal", 0.0)
            cap = DEFAULTS["household_loan_cap"] if isinstance(b, Household) else DEFAULTS["loan_cap"]
            if existing >= cap:
                continue
            b.cash += loan_amt
            b.loan_principal = existing + loan_amt
            self.reserves -= loan_amt
            self.total_loans += loan_amt
            self.model.total_loans_outstanding += loan_amt

    def update_bad_debts(self) -> None:
        """
        坏账 = Σ(企业违约概率 × 该企业贷款额)
        按期望值估算坏账（不是实际扣减，用于风险指标）
        """
        total = 0.0
        for f in self.model.firms:
            prob = getattr(f, "default_probability", 0.0)
            loan = getattr(f, "loan_principal", 0.0)
            total += prob * loan
        self.bad_debts = total

    def update_wealth(self) -> None:
        """
        银行财富 = 准备金 + 有效贷款（总贷款 - 坏账）
        坏账全额扣除（比 50% 折价更合理）
        """
        effective_loans = max(0.0, self.total_loans - self.bad_debts)
        self.wealth = self.reserves + effective_loans

    def step(self) -> None:
        self.pay_deposit_interest()
        self.lend()
        self.update_bad_debts()
        self.update_wealth()


class Trader(Agent):
    """
    交易者（动量策略）：
      compute_momentum() → 计算动量指标
      trade()            → 根据动量买入/卖出（含止损）
      update_wealth()     → 财富 = 现金 + 持股 × 股价
    """

    def __init__(self, model: EconomyModel):
        super().__init__(model)
        self.cash: float = self.random.uniform(300, 1000)
        self.shares: int = self.random.randint(0, 20)
        self.momentum: float = 0.0
        self.wealth: float = self.cash + self.shares * model.stock_price

    def compute_momentum(self, price: float, prev_price: float) -> None:
        """动量 = 0.7 × 旧动量 + 0.3 × 当期收益率"""
        if prev_price <= 0:
            return
        ret = (price - prev_price) / prev_price
        self.momentum = 0.7 * self.momentum + 0.3 * ret

    def trade(self) -> None:
        """动量策略交易：追涨杀跌，含止损"""
        m = self.model
        price = m.stock_price
        prev_price = m.prev_stock_price
        self.compute_momentum(price, prev_price)

        buy_prob = _clamp(0.3 + self.momentum * 2, 0.0, 1.0)
        sell_prob = _clamp(0.3 - self.momentum * 2, 0.0, 1.0)

        # 买入（至少持有 2 股现金）
        if self.cash >= price * 2 and self.random.random() < buy_prob:
            self.cash -= price * 2
            self.shares += 2
            m.buy_orders += 2

        # 止损（单日跌幅 > 5% 则清仓）
        if prev_price > 0 and (prev_price - price) / prev_price > 0.05 and self.shares > 0:
            m.sell_orders += self.shares
            self.cash += price * self.shares
            self.shares = 0

        # 正常卖出
        if self.shares > 0 and self.random.random() < sell_prob:
            self.cash += price
            m.sell_orders += 1
            self.shares -= 1

    def update_wealth(self) -> None:
        """财富 = 现金 + 持股 × 股价"""
        self.wealth = self.cash + self.shares * self.model.stock_price

    def step(self) -> None:
        self.trade()
        self.update_wealth()


# ─────────────────────────────────────────────
# 宏观指标
# ─────────────────────────────────────────────

def compute_gini(model: EconomyModel) -> float:
    """基尼系数（Allison 公式，对负财富友好）"""
    wealths = [getattr(a, "wealth", 0.0) for a in model.agents
               if isinstance(a, (Household, Firm, Trader))]
    if len(wealths) < 2:
        return 0.0
    arr = np.sort(np.array(wealths, dtype=float))
    n = len(arr)
    cumsum = np.cumsum(arr)
    mean = arr.mean()
    if mean == 0:
        return 0.0
    # Gini = (2 × Σ(i×w_i) / (n×Σw)) - (n+1)/n
    return float((2 * np.sum(np.arange(1, n + 1) * arr)) / (n * cumsum[-1]) - (n + 1) / n)


def compute_gdp(model: EconomyModel) -> float:
    """
    支出法 GDP：
      消费（C）= 居民购买企业商品的总支出
      投资（I）= 企业本期生产 - 已销售部分（即库存增加）
      政府购买（G）= 失业补贴总额
    GDP = C + I + G
    """
    firms = model.firms
    households = model.households

    # 消费：居民本期消费支出 = 消费数量 × 物价
    consumption = sum(h.goods * model.price_index for h in households)

    # 投资：企业库存增量（本期生产 - 已售出）
    # 简化：企业库存变化 ≈ 本期生产量（假设初始库存≈0）
    investment = sum(f.production * model.price_index for f in firms)

    # 政府支出：失业补贴
    n_unemployed = len(model.unemployed_households)
    gov_spending = model.subsidy * n_unemployed

    return consumption + investment + gov_spending


def compute_unemployment(model: EconomyModel) -> float:
    """失业率 = 失业居民 / 总居民"""
    if not model.households:
        return 0.0
    n_unemployed = sum(1 for h in model.households if not h.employed)
    return n_unemployed / len(model.households)


# ─────────────────────────────────────────────
# 经济模型
# ─────────────────────────────────────────────

class EconomyModel(Model):
    """
    主模型

    Agent 执行顺序（分阶段调度）：
      Bank  →  Firm  →  Household  →  Trader  →  Model（宏观清算）

    经济逻辑：
      1. 银行：支付存款利息，向企业/居民放贷
      2. 企业：招聘，生产，销售，贷款，违约
      3. 居民：领工资，消费，存款，买卖股票，找工作
      4. 交易者：动量交易
      5. 政府：收税（各 Agent step 内累加），发放补贴
      6. 宏观清算：股票价格，物价指数，GDP，失业率，基尼系数
    """

    def __init__(self, **kwargs):
        super().__init__()

        # ── 参数校验 + 注入默认值 ─────────────────────
        for key, default_val in DEFAULTS.items():
            setattr(self, key, _clamp(kwargs.get(key, default_val), 0.0, 1e9))

        # 政策参数单独存储（方便按钮直接读写）
        self.tax_rate: float = _clamp(kwargs.get("tax_rate", DEFAULTS["tax_rate"]), 0.0, 1.0)
        self.base_interest_rate: float = _clamp(kwargs.get("base_interest_rate", DEFAULTS["base_interest_rate"]), 0.0, 0.5)
        self.min_wage: float = max(0.0, kwargs.get("min_wage", DEFAULTS["min_wage"]))
        self.productivity: float = max(0.01, kwargs.get("productivity", DEFAULTS["productivity"]))
        self.subsidy: float = max(0.0, kwargs.get("subsidy", DEFAULTS["subsidy"]))

        # ── Agent 分类缓存（避免 O(n²) 重复筛选） ────────
        self.households: list[Household] = []
        self.firms: list[Firm] = []
        self.banks: list[Bank] = []
        self.traders: list[Trader] = []

        # ── 失业居民缓存（find_job 用，避免每次遍历） ───
        self._unemployed_cache: list[Household] = []

        # ── 市场状态 ───────────────────────────────────
        self.stock_price: float = 100.0
        self.prev_stock_price: float = 100.0
        self.price_index: float = 10.0
        self.buy_orders: int = 0
        self.sell_orders: int = 0

        # ── 政府财政 ───────────────────────────────────
        self.govt_revenue: float = 0.0   # 税收收入（每个 Agent step 累加）
        self.govt_expenditure: float = 0.0  # 财政支出（补贴，每轮单独计算）

        # ── 信贷市场 ───────────────────────────────────
        self.total_loans_outstanding: float = 0.0
        self.total_dividends: float = 0.0

        # ── 宏观指标 ───────────────────────────────────
        self.gdp: float = 0.0
        self.unemployment: float = 0.0
        self.gini: float = 0.0

        # ── 金融风险指标 ────────────────────────────────
        self.stock_volatility: float = 0.0      # 滚动波动率
        self.stock_returns: list[float] = []    # 历史收益率（用于滚动窗口）
        self.default_count: int = 0             # 违约企业数
        self.bank_bad_debt_rate: float = 0.0    # 银行坏账率

        # ── 周期计数器 ─────────────────────────────────
        self.cycle: int = 0

        # ── 创建 Agent（分类存储） ──────────────────────
        n_households = max(1, int(kwargs.get("n_households", DEFAULTS["n_households"])))
        n_firms = max(1, int(kwargs.get("n_firms", DEFAULTS["n_firms"])))
        n_banks = max(1, int(kwargs.get("n_banks", DEFAULTS["n_banks"])))
        n_traders = max(1, int(kwargs.get("n_traders", DEFAULTS["n_traders"])))

        for _ in range(n_households):
            h = Household(self)
            self.agents.add(h)
            self.households.append(h)
            self._unemployed_cache.append(h)

        for _ in range(n_firms):
            f = Firm(self)
            self.agents.add(f)
            self.firms.append(f)

        for _ in range(n_banks):
            b = Bank(self)
            self.agents.add(b)
            self.banks.append(b)

        for _ in range(n_traders):
            t = Trader(self)
            self.agents.add(t)
            self.traders.append(t)

        # ── 数据收集器（含 Agent 类型标签） ────────────
        self.datacollector = DataCollector(
            model_reporters={
                "stock_price":       "stock_price",
                "gdp":               "gdp",
                "unemployment":      lambda m: round(m.unemployment * 100, 1),
                "price_index":       lambda m: round(m.price_index, 2),
                "gini":              lambda m: round(m.gini, 4),
                "buy_orders":        "buy_orders",
                "sell_orders":       "sell_orders",
                "loans":            lambda m: round(m.total_loans_outstanding, 1),
                "stock_volatility": lambda m: round(m.stock_volatility, 4),
                "default_count":     "default_count",
                "bad_debt_rate":    lambda m: round(m.bank_bad_debt_rate, 4),
                "gov_revenue":      lambda m: round(m.govt_revenue, 1),
            },
            agent_reporters={
                "cash":       lambda a: getattr(a, "cash", 0.0),
                "wealth":     lambda a: getattr(a, "wealth", 0.0),
                "agent_type": lambda a: type(a).__name__,
            },
        )

        logger.info(
            "模型初始化完成：%d households, %d firms, %d banks, %d traders",
            n_households, n_firms, n_banks, n_traders,
        )

    # ── 属性代理（方便 UI 读取） ─────────────────────

    @property
    def unemployed_households(self) -> list[Household]:
        """返回当前失业居民列表（实时过滤，不用缓存因为 find_job 会修改）"""
        return [h for h in self.households if not h.employed]

    # ── 主循环（分阶段执行） ──────────────────────────

    def step(self) -> None:
        """每轮执行：市场清算 → 各类 Agent 决策 → 政府 → 宏观指标"""
        self._reset_counters()
        self._clear_unemployed_cache()

        # 1. 银行决策（储备变化 → 放贷）
        for bank in self.banks:
            bank.step()

        # 2. 企业决策（招聘 → 生产 → 销售 → 股息 → 贷款 → 违约）
        for firm in self.firms:
            firm.step()

        # 3. 居民决策（工资 → 消费 → 存款 → 股票 → 求职）
        for household in self.households:
            household.step()

        # 4. 交易者决策（动量交易）
        for trader in self.traders:
            trader.step()

        # 5. 政府活动（发放失业补贴 = 财政支出）
        n_unemployed = len(self.unemployed_households)
        total_subsidy = self.subsidy * n_unemployed
        self.govt_expenditure = total_subsidy
        for h in self.unemployed_households:
            h.cash += self.subsidy
        # 补贴是支出，从财政收入中扣除（正确逻辑）
        self.govt_revenue -= total_subsidy

        # 6. 宏观清算
        self._clear_market()
        self._compute_macro()
        self._collect_data()

        self.cycle += 1

    # ── 辅助方法 ────────────────────────────────────

    def _reset_counters(self) -> None:
        """重置每轮市场计数器"""
        self.buy_orders = 0
        self.sell_orders = 0
        self.govt_revenue = 0.0
        self.total_dividends = 0.0
        self.default_count = 0

    def _clear_unemployed_cache(self) -> None:
        """刷新失业居民列表（Household.find_job 会修改 employed）"""
        self._unemployed_cache = self.unemployed_households

    def _clear_market(self) -> None:
        """股票价格 + 物价指数清算"""
        # 股价：净买入压力 × 弹性系数 + 基本面噪声
        self.prev_stock_price = self.stock_price
        net_order = self.buy_orders - self.sell_orders
        n = len(self.traders) or 1
        delta = net_order / (n * 2) * DEFAULTS["stock_adjust_speed"]
        self.stock_price *= 1 + delta
        self.stock_price += self.random.uniform(-0.5, 0.5)
        self.stock_price = max(1.0, self.stock_price)

        # 股价波动率（滚动窗口标准差）
        if self.prev_stock_price > 0:
            ret = (self.stock_price - self.prev_stock_price) / self.prev_stock_price
            self.stock_returns.append(ret)
            window = DEFAULTS["vol_window"]
            if len(self.stock_returns) > window:
                self.stock_returns.pop(0)
            if len(self.stock_returns) >= 2:
                self.stock_volatility = float(np.std(self.stock_returns) * np.sqrt(252))

        # 物价：通胀压力 = (GDP - target) / target × speed
        inflation = (self.gdp - DEFAULTS["gdp_target"]) / DEFAULTS["gdp_target"] * DEFAULTS["price_adjust_speed"]
        self.price_index += self.random.uniform(-0.3, 0.3) + inflation
        self.price_index = max(1.0, self.price_index)

    def _compute_macro(self) -> None:
        """计算宏观指标"""
        self.gdp = compute_gdp(self)
        self.unemployment = compute_unemployment(self)
        self.gini = compute_gini(self)

        # 金融风险
        if self.firms:
            self.default_count = sum(
                1 for f in self.firms
                if f.default_probability > DEFAULTS["default_threshold"]
            )
        if self.banks:
            total_bad = sum(b.bad_debts for b in self.banks)
            total_loans = sum(b.total_loans for b in self.banks) + 1e-6
            self.bank_bad_debt_rate = total_bad / total_loans

    def _collect_data(self) -> None:
        """收集数据"""
        self.datacollector.collect(self)

    # ─────────────────────────────────────────────
    # 政策干预方法（UI 按钮直接调用）
    # ─────────────────────────────────────────────

    def adjust_interest_rate(self, delta: float) -> None:
        """调整基准利率"""
        self.base_interest_rate = _clamp(self.base_interest_rate + delta, 0.0, 0.25)

    def adjust_tax_rate(self, delta: float) -> None:
        """调整所得税率"""
        self.tax_rate = _clamp(self.tax_rate + delta, 0.0, 0.45)

    def adjust_subsidy(self, delta: float) -> None:
        """调整失业补贴"""
        self.subsidy = _clamp(self.subsidy + delta, 0.0, 50.0)

    def adjust_productivity(self, delta: float) -> None:
        """调整生产率"""
        self.productivity = _clamp(self.productivity + delta, 0.1, 3.0)
