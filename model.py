"""
Mesa Economic Sandbox - Core Model (Mesa 3.x compatible)
全面经济系统沙盒：消费者、企业、银行、交易员 × 商品/劳动力/信贷/股票四大市场
"""

import numpy as np
from mesa import Agent, Model
from mesa.datacollection import DataCollector


# ─────────────────────────────────────────────
# 代理人
# ─────────────────────────────────────────────

class Household(Agent):
    """消费者：赚钱、消费、存钱、找工作、买股票"""

    def __init__(self, model):
        super().__init__(model)
        self.cash = self.random.uniform(50, 200)
        self.goods = 0
        self.salary = 0.0
        self.employed = False
        self.wealth = self.cash
        self.loan_principal = 0.0
        self.shares_owned = 0

    def step(self):
        m = self.model

        # 1. 领工资（如果已就业）
        if self.employed and self.salary > 0:
            wage = self.salary
            tax = wage * m.tax_rate
            self.cash += wage - tax
            m.govt_revenue += tax

        # 2. 偿还贷款（如果有）
        if self.loan_principal > 0:
            rate = m.base_interest_rate
            interest = self.loan_principal * rate
            repayment = min(m.min_wage * 0.1, self.loan_principal + interest)
            if self.cash >= repayment:
                self.cash -= repayment
                self.loan_principal -= max(0, repayment - interest)

        # 3. 消费商品
        if self.cash > 5:
            price = m.price_index
            if self.random.random() < 0.6:
                self.cash -= price
                self.goods += 1

        # 4. 买股票（随机）
        if self.cash > m.stock_price and self.random.random() < 0.1:
            self.cash -= m.stock_price
            self.shares_owned += 1
            m.buy_orders += 1

        # 5. 卖股票（随机）
        if self.shares_owned > 0 and self.random.random() < 0.08:
            self.cash += m.stock_price * 0.95
            m.sell_orders += 1
            self.shares_owned -= 1

        # 6. 找工作（如果失业）
        if not self.employed:
            firms = [a for a in m.agents if isinstance(a, Firm)]
            if firms and self.random.random() < 0.3:
                firm = self.random.choice(firms)
                if firm.open_positions > 0:
                    firm.open_positions -= 1
                    self.employed = True
                    self.salary = firm.wage_offer

        # 7. 更新财富
        self.wealth = self.cash + getattr(self, "loan_principal", 0.0)


class Firm(Agent):
    """企业：雇人、生产、定价、销售、借贷"""

    def __init__(self, model):
        super().__init__(model)
        self.cash = self.random.uniform(200, 800)
        self.employees = 0
        self.wage_offer = max(getattr(model, "min_wage", 7.0), 8.0)
        self.open_positions = self.random.randint(0, 3)
        self.production = 0.0
        self.inventory = 0.0
        self.loan_principal = 0.0
        self.wealth = self.cash
        self.dividend_per_share = 0.0

    def step(self):
        m = self.model

        # 1. 招聘
        households = [a for a in m.agents if isinstance(a, Household)]
        unemployed = [h for h in households if not h.employed]
        job_openings = min(self.open_positions, len(unemployed))
        for _ in range(job_openings):
            if unemployed:
                h = self.random.choice(unemployed)
                h.employed = True
                h.salary = self.wage_offer
                self.employees += 1
                unemployed.remove(h)
        self.open_positions = 0

        # 2. 生产
        efficiency = m.productivity
        self.production = self.employees * 2.0 * efficiency + self.random.uniform(0, 2)
        self.inventory += self.production

        # 3. 销售商品
        buyers = self.random.sample(households, min(len(households), 5))
        for h in buyers:
            if h.cash >= m.price_index and h.goods < 10 and self.inventory > 0:
                h.cash -= m.price_index
                h.goods += 1
                self.cash += m.price_index * (1 - m.tax_rate)
                self.inventory -= 1

        # 4. 发放股息
        if self.cash > 100:
            profit = self.cash * 0.05
            self.cash -= profit
            self.dividend_per_share = profit / 50
            m.total_dividends += profit

        # 5. 借贷（如果现金不足）
        wage_bill = self.employees * self.wage_offer
        if self.cash < wage_bill * 0.5 and self.loan_principal < 500:
            loan = min(200.0, wage_bill)
            self.cash += loan
            self.loan_principal += loan
            m.total_loans_outstanding += loan

        # 6. 偿还贷款
        if self.loan_principal > 0:
            rate = m.base_interest_rate
            interest = self.loan_principal * rate * 0.1
            repayment = min(interest + 5, self.cash)
            if self.cash > repayment:
                self.cash -= repayment
                self.loan_principal -= max(0, repayment - interest)

        # 7. 调整工资
        if self.inventory > 10:
            self.wage_offer = max(m.min_wage, self.wage_offer * 1.05)
        elif self.employees == 0 and self.inventory < 3:
            self.wage_offer = max(m.min_wage, self.wage_offer * 0.95)

        self.wealth = self.cash - self.loan_principal


class Bank(Agent):
    """银行：吸收存款、放贷"""

    def __init__(self, model):
        super().__init__(model)
        self.reserves = 1000.0
        self.deposits = 0.0
        self.total_loans = 0.0
        self.wealth = self.reserves

    def step(self):
        m = self.model

        # 付存款利息
        deposit_rate = 0.01
        interest_paid = self.deposits * deposit_rate
        self.reserves -= interest_paid

        # 放贷
        lending_rate = m.base_interest_rate + 0.02
        if self.reserves > 50:
            households = [a for a in m.agents if isinstance(a, Household)]
            firms = [a for a in m.agents if isinstance(a, Firm)]
            borrowers = households + firms
            for _ in range(min(3, len(borrowers))):
                b = self.random.choice(borrowers)
                if getattr(b, "loan_principal", 0) < 200:
                    b.cash += 20.0
                    b.loan_principal = getattr(b, "loan_principal", 0) + 20.0
                    if isinstance(b, Firm):
                        m.total_loans_outstanding += 20.0
                    self.reserves -= 20.0
                    self.total_loans += 20.0

        self.wealth = self.reserves + self.total_loans * 0.5


class Trader(Agent):
    """股票交易员：动量策略"""

    def __init__(self, model):
        super().__init__(model)
        self.cash = self.random.uniform(300, 1000)
        self.shares = self.random.randint(0, 20)
        self.momentum = 0.0
        self.wealth = self.cash + self.shares * model.stock_price

    def step(self):
        m = self.model
        price = m.stock_price
        prev_price = m.prev_stock_price

        # 动量计算
        if prev_price > 0:
            ret = (price - prev_price) / prev_price
            self.momentum = 0.7 * self.momentum + 0.3 * ret

        # 买入信号
        buy_prob = max(0, 0.3 + self.momentum * 2)
        sell_prob = max(0, 0.3 - self.momentum * 2)

        if self.cash >= price * 2 and self.random.random() < buy_prob:
            self.cash -= price * 2
            self.shares += 2
            m.buy_orders += 2

        # 止损
        if prev_price > 0 and (prev_price - price) / prev_price > 0.05 and self.shares > 0:
            m.sell_orders += self.shares
            self.cash += price * self.shares
            self.shares = 0

        # 正常卖出
        if self.shares > 0 and self.random.random() < sell_prob:
            self.cash += price
            m.sell_orders += 1
            self.shares -= 1

        self.wealth = self.cash + self.shares * price


# ─────────────────────────────────────────────
# 宏观指标
# ─────────────────────────────────────────────

def compute_gini(model):
    wealths = []
    for a in model.agents:
        if isinstance(a, (Household, Firm, Trader)):
            wealths.append(getattr(a, "wealth", 0))
    if len(wealths) < 2:
        return 0.0
    wealths = np.sort(np.array(wealths, dtype=float))
    n = len(wealths)
    cumsum = np.cumsum(wealths)
    return (2 * np.sum(np.arange(1, n + 1) * wealths)) / (n * cumsum[-1]) - (n + 1) / n


def compute_gdp(model):
    firms = [a for a in model.agents if isinstance(a, Firm)]
    households = [a for a in model.agents if isinstance(a, Household)]
    firm_revenue = sum(f.inventory + f.production * 10 for f in firms)
    consumption = sum(h.goods * model.price_index for h in households)
    return firm_revenue + consumption


def compute_unemployment(model):
    households = [a for a in model.agents if isinstance(a, Household)]
    if not households:
        return 0.0
    return sum(1 for h in households if not h.employed) / len(households)


# ─────────────────────────────────────────────
# 经济模型
# ─────────────────────────────────────────────

class EconomyModel(Model):
    """主模型：事件调度 + 市场清算 + 宏观指标"""

    def __init__(
        self,
        n_households=20,
        n_firms=10,
        n_banks=2,
        n_traders=20,
        tax_rate=0.15,
        base_interest_rate=0.05,
        min_wage=7.0,
        productivity=1.0,
        subsidy=0.0,
    ):
        super().__init__()

        # 政策参数
        self.tax_rate = tax_rate
        self.base_interest_rate = base_interest_rate
        self.min_wage = min_wage
        self.productivity = productivity
        self.subsidy = subsidy

        # 市场状态
        self.stock_price = 100.0
        self.prev_stock_price = 100.0
        self.price_index = 10.0
        self.buy_orders = 0
        self.sell_orders = 0
        self.govt_revenue = 0.0
        self.total_loans_outstanding = 0.0
        self.total_dividends = 0.0
        self.cycle = 0

        # 宏观指标
        self.gdp = 0.0
        self.unemployment = 0.0
        self.gini = 0.0

        # 创建代理人
        for _ in range(n_households):
            self.agents.add(Household(self))

        for _ in range(n_firms):
            self.agents.add(Firm(self))

        for _ in range(n_banks):
            self.agents.add(Bank(self))

        for _ in range(n_traders):
            self.agents.add(Trader(self))

        # 数据收集器
        self.datacollector = DataCollector(
            model_reporters={
                "stock_price": lambda m: m.stock_price,
                "gdp": lambda m: m.gdp,
                "unemployment": lambda m: round(m.unemployment * 100, 1),
                "price_index": lambda m: round(m.price_index, 2),
                "gini": lambda m: round(m.gini, 3),
                "buy_orders": lambda m: m.buy_orders,
                "sell_orders": lambda m: m.sell_orders,
                "gov_revenue": lambda m: round(m.govt_revenue, 1),
                "loans": lambda m: round(m.total_loans_outstanding, 1),
            },
            agent_reporters={
                "cash": lambda a: getattr(a, "cash", None),
                "wealth": lambda a: getattr(a, "wealth", None),
            },
        )

    def step(self):
        # 重置市场计数器
        self.buy_orders = 0
        self.sell_orders = 0
        self.govt_revenue = 0.0
        self.total_dividends = 0.0

        # 所有代理人决策（Mesa 3.x：手动遍历 AgentSet）
        for agent in self.agents:
            agent.step()

        # 政府活动（补贴失业者）
        households = [a for a in self.agents if isinstance(a, Household)]
        unemployed = [h for h in households if not h.employed]
        for h in unemployed:
            h.cash += self.subsidy
        self.govt_revenue += self.subsidy * len(unemployed)

        # 股票价格清算
        self.prev_stock_price = self.stock_price
        net_order = self.buy_orders - self.sell_orders
        n_traders = len([a for a in self.agents if isinstance(a, Trader)]) or 1
        delta = net_order / (n_traders * 2) * 0.03
        self.stock_price *= 1 + delta
        # 基本面噪声
        self.stock_price += self.random.uniform(-0.5, 0.5)
        self.stock_price = max(1.0, self.stock_price)

        # 物价指数
        inflation_pressure = (self.gdp - 1000) / 1000 * 0.1
        self.price_index = max(1.0, self.price_index + self.random.uniform(-0.5, 0.5) + inflation_pressure)

        # 宏观指标
        self.gdp = compute_gdp(self)
        self.unemployment = compute_unemployment(self)
        self.gini = compute_gini(self)

        self.cycle += 1
        self.datacollector.collect(self)

    # ─────────────────────────────────────────────
    # 政策干预方法
    # ─────────────────────────────────────────────

    def adjust_interest_rate(self, delta: float):
        """调整基准利率（delta 正值=加息，负值=降息）"""
        self.base_interest_rate = max(0.0, min(0.25, self.base_interest_rate + delta))

    def adjust_tax_rate(self, delta: float):
        """调整税率（delta 正值=加税，负值=减税）"""
        self.tax_rate = max(0.0, min(0.45, self.tax_rate + delta))

    def adjust_subsidy(self, delta: float):
        """调整失业补贴"""
        self.subsidy = max(0.0, min(50.0, self.subsidy + delta))
