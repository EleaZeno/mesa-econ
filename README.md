# Mesa Economic Sandbox

基于 Mesa 3.x + Solara 的多智能体经济仿真沙盒。模拟消费者、企业、银行、交易员四大主体在商品、劳动力、信贷、股票四个市场中的互动。

![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)
![Mesa](https://img.shields.io/badge/Mesa-3.5.1-green.svg)
![Solara](https://img.shields.io/badge/Solara-1.56.0-purple.svg)

## 安装

```bash
pip install -r requirements.txt
```

## 运行

```bash
cd mesa-econ
solara run server.py
```

打开 http://127.0.0.1:8521

## 仿真内容

| 市场 | 机制 |
|------|------|
| **商品市场** | 企业生产 →  Household 消费 → 价格指数 |
| **劳动力市场** | Household 找工作 → 企业雇佣 → 工资竞争 |
| **信贷市场** | Household/Trader 借贷 → 银行收息 → 违约风险 |
| **股票市场** | 交易员低买高卖 → 股价波动 → 分红驱动 |

## 核心指标

- **GDP** — 企业总产出
- **失业率** — 求职中 Household 比例
- **价格指数** — 商品市场通胀指标
- **股价** — 供需 + 分红预期定价
- **Gini 系数** — 财富分配不平等
- **贷款余额** — 信贷市场规模

## 参数说明

| 参数 | 范围 | 说明 |
|------|------|------|
| Consumers | 5–80 | 消费者数量 |
| Firms | 3–40 | 企业数量 |
| Traders | 5–80 | 股票交易员数量 |
| Income Tax Rate | 0–45% | 所得税率，影响 govt_revenue |
| Base Interest Rate | 0–25% | 贷款利率，影响借贷意愿 |
| Minimum Wage | 0–20 | 最低工资 |
| Productivity | 0.1–3.0 | 全要素生产率，影响企业产出 |
| Unemployment Subsidy | 0–20 | 失业补贴 |

## 文件结构

```
mesa-econ/
├── model.py       # 经济模型（4类智能体 + 4个市场）
├── server.py      # Solara 可视化 Web 界面
├── requirements.txt
└── README.md
```

## 架构说明

- **Household** — 赚工资、交税、消费、借贷、炒股、找工作
- **Firm** — 招聘/裁员、定价、生产商品、支付工资、贷款扩张
- **Bank** — 撮合借贷、收取利息、记录违约
- **Trader** — 技术分析 + 情绪驱动的股票日内交易

智能体通过 `model.schedule_recurring()` 每步激活，数据通过 `DataCollector` 采集并渲染为 matplotlib 图表。

## 技术栈

- Mesa 3.5.1 — 多智能体仿真框架
- Solara 1.56.0 — React-style Python Web UI
- NumPy / Pandas — 数值计算
- Matplotlib — 图表渲染（backend）
