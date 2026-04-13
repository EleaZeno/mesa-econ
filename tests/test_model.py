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
        assert m.govt_revenue >= 0

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


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
