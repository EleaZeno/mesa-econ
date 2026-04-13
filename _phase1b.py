#!/usr/bin/env python3
"""
Phase 1B — 废除 base_interest_rate，银行利率纯内生化

自动化重构脚本，精确替换 model.py 中的 11 处改动点。
"""

import re
from pathlib import Path

MODEL_FILE = Path(__file__).parent / "model.py"


def apply_refactoring(content: str) -> str:
    """Apply all refactoring changes"""
    lines = content.split('\n')
    
    # --- 1. Remove base_interest_rate from DEFAULTS (two locations: L193 and L560) ---
    # First DEFAULTS (around L193)
    for i, line in enumerate(lines):
        if 'base_interest_rate=0.05,' in line and i > 180 and i < 210:
            lines[i] = line.replace('base_interest_rate=0.05,   # 基准利率\n', '')
            lines[i] = lines[i].replace('base_interest_rate=0.05,', '')
            print(f"[1a] Removed base_interest_rate from DEFAULTS 1st: L{i+1}")
            break
    
    # Second DEFAULTS (around L560)
    for i, line in enumerate(lines):
        if 'base_interest_rate=0.05,' in line and i > 550 and i < 580:
            lines[i] = line.replace('base_interest_rate=0.05,   # 基准利率\n', '')
            lines[i] = lines[i].replace('base_interest_rate=0.05,', '')
            print(f"[1b] Removed base_interest_rate from DEFAULTS 2nd: L{i+1}")
            break
    
    # --- 2. Add loan_rate and deposit_rate to Bank.__init__ ---
    for i, line in enumerate(lines):
        if 'self.deposits: float = 0.0' in line and 'Bank' in ''.join(lines[max(0,i-20):i]):
            # Insert two lines after deposits
            indent = '        '
            new_lines = [
                indent + 'self.loan_rate: float = 0.05   # Bank loan rate (endogenous)\n',
                indent + 'self.deposit_rate: float = 0.02  # Bank deposit rate (endogenous)\n'
            ]
            # Check if already exists (avoid duplicate)
            if i + 1 < len(lines) and 'loan_rate' not in lines[i + 1]:
                lines[i] = line.rstrip() + '\n' + ''.join(new_lines)
                print(f"[2] Added loan_rate/deposit_rate to Bank.__init__: L{i+1}")
            break
    
    # --- 3. Modify Bank._effective_rate ---
    for i, line in enumerate(lines):
        if 'base = self.model.base_interest_rate' in line:
            lines[i] = line.replace('base = self.model.base_interest_rate', 'base = self.loan_rate')
            print(f"[3a] _effective_rate base reference replaced: L{i+1}")
            # Find return statement
            for j in range(i+1, min(i+5, len(lines))):
                if 'return base + self.lending_spread' in lines[j]:
                    lines[j] = lines[j].replace('return base + self.lending_spread', 'return self.loan_rate + self.lending_spread')
                    print(f"[3b] _effective_rate return modified: L{j+1}")
                    break
            break
    
    # --- 4. Modify Bank.pay_deposit_interest ---
    for i, line in enumerate(lines):
        if 'rate = self.model.base_interest_rate * 0.5' in line:
            lines[i] = line.replace('rate = self.model.base_interest_rate * 0.5', 'rate = self.deposit_rate')
            print(f"[4] pay_deposit_interest rate replaced: L{i+1}")
            break
    
    # --- 5. Replace Bank._auto_adjust_rates ---
    for i, line in enumerate(lines):
        if 'def _auto_adjust_rates(self) -> None:' in line:
            # Find method end
            start = i
            end = i + 1
            indent_level = len(line) - len(line.lstrip())
            
            for j in range(i + 1, len(lines)):
                # Detect same-level or lower-level def/class as end
                if lines[j].strip() and not lines[j].startswith(' ' * (indent_level + 1)):
                    if lines[j].strip().startswith('def ') or lines[j].strip().startswith('class '):
                        end = j
                        break
                if j == len(lines) - 1:
                    end = j + 1
            
            # New method implementation
            new_method = '''    def _auto_adjust_rates(self) -> None:
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

'''
            lines = lines[:start] + [new_method] + lines[end:]
            print(f"[5] _auto_adjust_rates replaced: L{start+1}-{end}")
            break
    
    # --- 6. Modify Household.repay_loan ---
    for i, line in enumerate(lines):
        if 'rate = self.model.base_interest_rate' in line and i > 700 and i < 850:
            lines[i] = line.replace('rate = self.model.base_interest_rate', 'rate = 0.05')
            print(f"[6] Household.repay_loan rate replaced: L{i+1}")
            break
    
    # --- 7. Modify Firm.repay_loan ---
    for i, line in enumerate(lines):
        if 'rate = self.model.base_interest_rate' in line and i > 1200:
            lines[i] = line.replace('rate = self.model.base_interest_rate', 'rate = 0.05')
            print(f"[7] Firm.repay_loan rate replaced: L{i+1}")
            break
    
    # --- 8. Remove self.base_interest_rate from EconomyModel.__init__ ---
    for i, line in enumerate(lines):
        if 'self.base_interest_rate = _clamp' in line:
            lines[i] = ''
            print(f"[8] Removed base_interest_rate assignment: L{i+1}")
            break
    
    # --- 9. Modify _compute_macro (disc_rate calculation) ---
    for i, line in enumerate(lines):
        if 'disc_rate = self.base_interest_rate + 0.02' in line:
            new_code = '        avg_loan_rate = sum(b.loan_rate for b in self.banks) / max(1, len(self.banks))\n' + \
                       '        disc_rate = avg_loan_rate + 0.02'
            lines[i] = new_code
            print(f"[9] _compute_macro disc_rate -> avg bank loan rate: L{i+1}")
            break
    
    # --- 10. Modify adjust_interest_rate ---
    for i, line in enumerate(lines):
        if 'def adjust_interest_rate(self, delta: float) -> None:' in line:
            # Find method body
            start = i
            end = i + 1
            for j in range(i + 1, min(i + 10, len(lines))):
                if lines[j].strip().startswith('def '):
                    end = j
                    break
            
            new_method = '''    def adjust_interest_rate(self, delta: float) -> None:
        """Policy transmission: adjust all banks' loan rates"""
        for b in self.banks:
            b.loan_rate = _clamp(b.loan_rate + delta, 0.01, 0.25)

'''
            lines = lines[:start] + [new_method] + lines[end:]
            print(f"[10] adjust_interest_rate -> impact bank loan_rate: L{start+1}-{end}")
            break
    
    # ── 11. 注释清理 ──
    # 原注释在 _auto_adjust_rates 中，已在步骤 5 替换，无需额外处理
    
    return '\n'.join(lines)


def main():
    print("=" * 60)
    print("Phase 1B - Remove base_interest_rate, bank rates endogenized")
    print("=" * 60)
    
    if not MODEL_FILE.exists():
        print(f"[ERROR] File not found: {MODEL_FILE}")
        return
    
    # 读取原文件
    content = MODEL_FILE.read_text(encoding='utf-8')
    print(f"[OK] Read file: {MODEL_FILE} ({len(content)} chars)")
    
    # 应用重构
    print("\nApplying refactoring...")
    new_content = apply_refactoring(content)
    
    # 写回文件
    MODEL_FILE.write_text(new_content, encoding='utf-8')
    print(f"\n[OK] Wrote back: {MODEL_FILE}")
    
    # 验证：检查是否还有 base_interest_rate 引用
    print("\nVerifying...")
    remaining = []
    for i, line in enumerate(new_content.split('\n'), 1):
        if 'base_interest_rate' in line and 'def adjust_interest_rate' not in line:
            remaining.append((i, line.strip()))
    
    if remaining:
        print("[WARN] Still referencing base_interest_rate:")
        for line_no, line_text in remaining:
            print(f"  L{line_no}: {line_text}")
    else:
        print("[OK] All base_interest_rate references removed")
    
    print("\n" + "=" * 60)
    print("Phase 1B refactoring complete!")
    print("Next step: run tests")
    print("  python -m pytest tests/test_model.py -q")
    print("=" * 60)


if __name__ == "__main__":
    main()
