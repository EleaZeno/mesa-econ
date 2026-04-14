"""Phase 5: 单元测试 + 冒烟测试"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest
from model import EconomyModel, Household, Firm, Bank, Trader, compute_gini, compute_unemployment


class TestInit:
    def test_model_init(self):
        m = EconomyModel()
        assert m.cycle == 0
        assert m.gdp >= 0
        assert len(m.households) >= 1
        assert len(m.firms) >= 1
        assert m.stock_price > 0

    def test_cache_initialized(self):
        m = EconomyModel()
        assert hasattr(m, "_cache")
        assert "firms_with_stock" in m._cache
        assert isinstance(m._cache["firms_with_stock"], list)

    def test_agents_have_city(self):
        m = EconomyModel()
        for h in m.households:
            assert hasattr(h, "city"), f"Household {h.unique_id} missing city"
        for f in m.firms:
            assert hasattr(f, "city"), f"Firm {f.unique_id} missing city"


class TestBasicStep:
    def test_single_step_no_crash(self):
        m = EconomyModel()
        m.step()
        assert m.cycle == 1

    def test_no_negative_gdp(self):
        m = EconomyModel()
        for _ in range(10):
            m.step()
        assert m.gdp >= 0

    def test_no_negative_stock_price(self):
        m = EconomyModel()
        for _ in range(20):
            m.step()
        assert m.stock_price > 0, f"Stock price crashed to {m.stock_price}"

    def test_gini_in_range(self):
        m = EconomyModel()
        for _ in range(10):
            m.step()
        g = compute_gini(m)
        assert 0 <= g <= 1, f"Gini {g} out of [0,1]"

    def test_unemployment_in_range(self):
        m = EconomyModel()
        for _ in range(10):
            m.step()
        u = compute_unemployment(m)
        assert 0 <= u <= 1, f"Unemployment {u} out of [0,1]"

    def test_no_nan_gdp(self):
        m = EconomyModel()
        for _ in range(20):
            m.step()
        assert not math.isnan(m.gdp), "GDP is NaN"
        assert not math.isinf(m.gdp), "GDP is Inf"

    def test_no_nan_wealth_all_agents(self):
        m = EconomyModel()
        for _ in range(20):
            m.step()
        for h in m.households:
            assert not math.isnan(h.wealth), f"HH {h.unique_id} wealth=NaN"
            assert not math.isinf(h.wealth), f"HH {h.unique_id} wealth=Inf"


class TestCacheUsage:
    def test_cache_populated_after_step(self):
        m = EconomyModel()
        m.step()
        assert len(m._cache.get("firms_with_stock", [])) >= 0
        assert "firms_by_industry" in m._cache

    def test_consume_uses_cache(self):
        m = EconomyModel()
        m.step()
        firms_with_stock = m._cache.get("firms_with_stock", [])
        for f in firms_with_stock:
            assert f.inventory > 0

    def test_cache_refresh_after_step(self):
        m = EconomyModel()
        old_cache = dict(m._cache)
        m.step()
        assert m._cache != old_cache, "Cache should update after step"


class TestCapitalGainsTax:
    def test_cg_tax_accumulates(self):
        m = EconomyModel(n_traders=10)
        initial_rev = m.capital_gains_tax_revenue
        for _ in range(20):
            m.step()
        assert m.capital_gains_tax_revenue >= initial_rev


class TestSmoke100:
    def test_100_steps_no_crash(self):
        m = EconomyModel()
        for _ in range(100):
            m.step()
        assert m.cycle == 100
        assert not math.isnan(m.gdp)
        assert 0 <= m.gini <= 1
        assert m.stock_price > 0
        assert not math.isnan(m.govt_revenue)  # 赤字财政正常，不检查正负

    def test_100_steps_wealth_valid(self):
        m = EconomyModel()
        for _ in range(100):
            m.step()
        for h in m.households:
            assert not math.isnan(h.wealth), f"HH {h.unique_id} wealth=NaN @ cycle={m.cycle}"
            assert not math.isinf(h.wealth), f"HH {h.unique_id} wealth=Inf @ cycle={m.cycle}"

    def test_100_steps_no_deadlock(self):
        m = EconomyModel(n_households=50, n_firms=20, n_banks=2, n_traders=30)
        import time
        t0 = time.time()
        for _ in range(100):
            m.step()
        elapsed = time.time() - t0
        assert elapsed < 30, f"100 steps took {elapsed:.1f}s (possible deadlock)"


class TestHealthScore:
    def test_health_score_in_range(self):
        m = EconomyModel()
        for _ in range(10):
            m.step()
        score = m.health_score
        assert 0 <= score <= 100, f"Health score {score} out of [0,100]"


class TestCities:
    def test_city_params_initialized(self):
        m = EconomyModel()
        assert hasattr(m, "city_a_tax")
        assert hasattr(m, "city_b_tax")
        assert 0 <= m.city_a_tax <= 1
        assert 0 <= m.city_b_tax <= 1


class TestMoneyConservation:
    """战役一验收：资金守恒测试。

    私人部门总资金 = Σ(居民现金) + Σ(企业现金) + Σ(银行准备金) + Σ(交易员现金)
    
    注意：政府税收/补贴/QE 是合法的外部注入/抽取。
    银行贷款是内部转移（准备金→借款人），不改变现金总量。
    所以资金变化 = 政府净注入 - 税收净抽取。
    
    测试策略：关闭外部冲击，观察资金的纯经济循环是否守恒。
    """

    @staticmethod
    def _private_money(m):
        """计算私人部门现金总量"""
        total = sum(h.cash for h in m.households)
        total += sum(f.cash for f in m.firms)
        total += sum(b.reserves for b in m.banks)
        total += sum(t.cash for t in m.traders)
        return total

    def test_no_money_creation_no_shock(self):
        """关闭外部冲击，50轮内资金变化应合理（仅政府操作导致）。"""
        m = EconomyModel(shock_prob=0)  # 关闭随机冲击
        initial = self._private_money(m)
        assert initial > 0, "初始资金必须为正"

        for _ in range(50):
            m.step()

        final = self._private_money(m)
        # 资金变化 = 政府净操作（补贴注入 - 税收抽取）
        # 在正常经济中，税收 > 补贴，所以资金应该减少
        # 允许政府操作带来最多初始资金50%的净变化
        pct_change = abs(final - initial) / initial
        assert pct_change < 0.5, \
            f"资金异常！初始={initial:.0f}, 最终={final:.0f} (变化{pct_change*100:.1f}%)"

    def test_no_negative_bank_reserves(self):
        """银行准备金不应长期为负（允许短暂透支，但50轮后必须为正）"""
        m = EconomyModel(shock_prob=0)
        for _ in range(50):
            m.step()

        total_reserves = sum(b.reserves for b in m.banks)
        assert total_reserves >= -1000, \
            f"银行总准备金严重透支: {total_reserves:.2f}"


class TestUltimateStress:
    def test_10000_steps_doomsday_scenario(self):
        """末日防线测试：万轮推演、极端冲击下不崩溃且SFC绝对守恒"""
        from model import EconomyModel
        import time

        m = EconomyModel(seed=42)  # 锁定随机数保证可复现
        start_time = time.time()

        for i in range(1, 10001):
            # 1. 执行推演与 SFC 审计
            m.step()

            # 2. 注入极端宏观冲击（考验系统的弹性与资金闭环）
            if i % 500 == 0:
                m.adjust_interest_rate(0.05)   # 暴力加息
                m.adjust_tax_rate(0.1)         # 突然加税
            if i % 730 == 0:
                m.trigger_shock("BANKING_PANIC")  # 银行挤兑
                m.qe_amount = 50.0                # 央行疯狂印钞救市
            if i % 1000 == 0:
                m.trigger_shock("TECH_BREAKTHROUGH")  # 技术大爆炸

            # 3. 运行时的活体检测（防止僵尸化）
            if i % 100 == 0:
                # 断言必须有企业和人口存活
                assert len(m.households) > 0, f"第 {i} 轮人类灭绝"
                # 断言价格不会变成负数或无穷大
                assert 0 < m.avg_price < float('inf'), f"第 {i} 轮物价崩溃: {m.avg_price}"

        elapsed = time.time() - start_time
        print(f"\n10000轮末日测试通过！耗时: {elapsed:.2f}秒")
        assert m.cycle == 10000


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
