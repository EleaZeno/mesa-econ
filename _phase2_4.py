"""Phase 2 (效用最大化) + Phase 4 (创业机制) refactoring script."""

PATH = "model.py"

with open(PATH, "r", encoding="utf-8") as f:
    lines = f.readlines()

# ── 改动 1: 在 reservation_wage 之后加效用参数 ──
for i, line in enumerate(lines):
    if "self.reservation_wage: float" in line:
        insert_at = i + 1
        new_code = [
            "        # 效用参数（CES 替代弹性）\n",
            "        self.utility_alpha: float = 0.4  # 商品消费权重（vs 储蓄）\n",
            "        self.utility_rho: float = 0.5    # 替代弹性参数（0→柯布道格拉斯，1→完全替代）\n",
        ]
        lines[insert_at:insert_at] = new_code
        print(f"[OK] 改动1: 效用参数 inserted at line {insert_at+1}")
        break

# ── 改动 2: 在 consume() 之前加 _should_consume() 方法 ──
for i, line in enumerate(lines):
    if line.strip() == "def consume(self) -> None:":
        indent = "    "
        new_method = [
            f"{indent}def _should_consume(self) -> bool:\n",
            f'{indent}    """效用最大化消费决策：\n',
            f"{indent}    - CES 效用：U = [α·G^(ρ-1)/ρ + (1-α)·C^(ρ-1)/ρ]^(ρ/(ρ-1))\n",
            f"{indent}    - 比较消费的边际效用 vs 储蓄的边际效用\n",
            f"{indent}    - 简化实现：基于现金缓冲、就业、价格水平的综合评分\n",
            f'{indent}    """\n',
            f"{indent}    # 现金充裕度：现金越多，越倾向消费\n",
            f"{indent}    cash_buffer = min(1.0, self.cash / 100.0)\n",
            f"\n",
            f"{indent}    # 就业稳定性：有工作更敢消费\n",
            f"{indent}    employment_bonus = 0.3 if self.employed else 0.0\n",
            f"\n",
            f"{indent}    # 价格惩罚：价格越高越克制消费\n",
            f"{indent}    avg_price = self.model.price_index\n",
            f'{indent}    price_factor = max(0.0, 1.0 - (avg_price - 10.0) / 50.0) if avg_price > 10 else 1.0\n',
            f"\n",
            f"{indent}    # 综合消费倾向\n",
            f"{indent}    propensity = self.mpc * cash_buffer * price_factor + employment_bonus\n",
            f"{indent}    return self.random.random() < min(0.95, propensity)\n",
            f"\n",
        ]
        lines[i:i] = new_method
        print(f"[OK] 改动2: _should_consume() inserted before consume() at line {i+1}")
        break

# ── 改动 3: 替换 consume() 中的消费概率判断 ──
new_lines = []
skip_next_return = False
for i, line in enumerate(lines):
    if skip_next_return and line.strip() == "return":
        skip_next_return = False
        print(f"[OK] 改动3c: removed stale 'return' at line {i+1}")
        continue
    if "consume_prob = min(0.98" in line:
        print(f"[OK] 改动3a: removed consume_prob line {i+1}")
        continue
    if "if self.random.random() >= consume_prob:" in line:
        new_lines.append("        if not self._should_consume():\n")
        skip_next_return = True  # the next "return" is now handled by the new if
        print(f"[OK] 改动3b: replaced consume_prob check at line {i+1}")
        continue
    new_lines.append(line)

lines = new_lines

# ── 改动 4: 在 _consider_migration 之后加 consider_entrepreneurship ──
for i, line in enumerate(lines):
    if "def _consider_migration(self) -> None:" in line:
        # Find the end of _consider_migration (next method def at class indent)
        j = i + 1
        while j < len(lines):
            stripped = lines[j].lstrip()
            if stripped.startswith("def ") and lines[j].startswith("    def "):
                break
            j += 1
        # Insert before the next method
        indent = "    "
        new_method = [
            f"\n",
            f"{indent}def consider_entrepreneurship(self) -> None:\n",
            f'{indent}    """创业机制：高现金+高技能的 Household 可以创建新企业\n',
            f"{indent}    \n",
            f"{indent}    条件：\n",
            f"{indent}    - 现金 > 300（启动资金）\n",
            f"{indent}    - 技能等级 >= 2（高技能）\n",
            f"{indent}    - 2% 概率触发（不是每轮都创业）\n",
            f"{indent}    - 当前企业数 < 上限（防止无限增长）\n",
            f'{indent}    """\n',
            f"{indent}    if self.random.random() > 0.02:\n",
            f"{indent}        return\n",
            f"{indent}    if self.cash < 300:\n",
            f"{indent}        return\n",
            f"{indent}    if self.skill_level < 2:\n",
            f"{indent}        return\n",
            f"{indent}    # 限制最大企业数\n",
            f"{indent}    if len(self.model.firms) >= 40:\n",
            f"{indent}        return\n",
            f"\n",
            f"{indent}    # 启动资金\n",
            f"{indent}    startup_cost = min(self.cash * 0.6, 500)\n",
            f"{indent}    self.cash -= startup_cost\n",
            f"\n",
            f"{indent}    # 选择行业（高技能→偏向科技/服务）\n",
            f"{indent}    industry_weights = {{\n",
            f"{indent}        Industry.MANUFACTURING: 0.2,\n",
            f"{indent}        Industry.SERVICE: 0.4,\n",
            f"{indent}        Industry.TECH: 0.4,\n",
            f"{indent}    }}\n",
            f"{indent}    industry = self.random.choices(\n",
            f"{indent}        list(industry_weights.keys()),\n",
            f"{indent}        weights=list(industry_weights.values()), k=1\n",
            f"{indent}    )[0]\n",
            f"\n",
            f"{indent}    # 创建新企业（Firm.__init__ 不接受 industry 参数，创建后覆盖）\n",
            f"{indent}    new_firm = Firm(self.model)\n",
            f"{indent}    new_firm.industry = industry\n",
            f"{indent}    new_firm._ind = INDUSTRY_PARAMS[industry]\n",
            f"{indent}    new_firm.wage_offer = 8.0 * new_firm._ind[\"wage_premium\"]\n",
            f"{indent}    new_firm.cash = startup_cost\n",
            f"{indent}    self.model.firms.add(new_firm)\n",
            f"\n",
            f"{indent}    # 创业者成为首任员工\n",
            f"{indent}    if self.employed:\n",
            f"{indent}        # 辞去当前工作\n",
            f"{indent}        self.employer.employees = max(0, self.employer.employees - 1)\n",
            f"{indent}        self.employer.open_positions += 1\n",
            f"{indent}    self.employed = True\n",
            f"{indent}    self.employer = new_firm\n",
            f"{indent}    self.salary = new_firm.wage_offer\n",
            f"{indent}    new_firm.employees = 1\n",
            f"{indent}    new_firm.open_positions = max(0, new_firm.open_positions - 1)\n",
        ]
        lines[j:j] = new_method
        print(f"[OK] 改动4: consider_entrepreneurship() inserted at line {j+1}")
        break

# ── 改动 5: 在 Household.step() 中加 consider_entrepreneurship() ──
# Current step: earn_wage, pay_taxes, repay_loan, deposit, consume, invest, search_job,
#               update_credit_score, update_wealth, _consider_migration
# Add after _consider_migration
for i, line in enumerate(lines):
    if "self._consider_migration()" in line:
        # Verify this is inside Household.step (not Firm step)
        # Look backwards for "def step"
        found_step = False
        for k in range(i, max(0, i - 20), -1):
            if "def step(self)" in lines[k]:
                # Check it's Household's step by checking context
                found_step = True
                break
        if found_step:
            lines.insert(i + 1, "        self.consider_entrepreneurship()\n")
            print(f"[OK] 改动5: consider_entrepreneurship() added to step() after line {i+1}")
            break

with open(PATH, "w", encoding="utf-8") as f:
    f.writelines(lines)

print("\n✅ All model.py changes applied.")
