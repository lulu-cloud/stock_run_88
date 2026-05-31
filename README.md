# A股多Agent智能投顾模拟交易系统

基于 LangChain + FastAPI + Vue3 构建的 A 股多 Agent 模拟交易系统，集成 LLM 决策、策略选股、回测引擎、宏观政策监控，完整覆盖 T+1 交易闭环。

> 新人或评审请先读：[PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md)。这份文档用更短路径说明当前业务、技术架构、已完成功能、技术深度和 Todo。

## 技术栈

### 后端

| 组件 | 技术 | 说明 |
|------|------|------|
| Web 框架 | FastAPI 0.115+ | 异步高性能 API，自动生成 OpenAPI 文档 |
| ASGI 服务器 | Uvicorn 0.32+ | 支持热重载开发模式 |
| LLM 框架 | LangChain 1.2+ | `create_agent` API，工具调用编排 |
| LLM 模型 | DeepSeek v4 pro | OpenAI 兼容接口，reasoning_effort="high"，thinking mode 开启 |
| 数据源 | Baostock 0.9+ | A 股日线 K 线、指数、季报数据 |
| 数据分析 | Pandas 2.2+ / NumPy 1.26+ | K 线指标计算、策略筛选、回测计算 |
| 数据验证 | Pydantic 2.10+ | API 请求/响应模型校验 |
| 网页解析 | BeautifulSoup4 | 政策文件爬虫 HTML 解析 |
| 数据库 | SQLite 3 | 本地文件数据库，零配置 |

### 前端

| 组件 | 技术 |
|------|------|
| 框架 | Vue 3.5+ (Composition API + `<script setup>`) |
| 构建工具 | Vite 6.0 |
| 路由 | Vue Router 4.5 |
| 状态管理 | Pinia 2.3 |
| 图表 | ECharts 5.6 |
| HTTP 客户端 | Axios 1.7 |

### 外部 API

| 用途 | 接口 | 说明 |
|------|------|------|
| LLM 推理 | DeepSeek API (`api.deepseek.com`) | 策略解析 + Agent 决策 + 自然语言选股 |
| 联网搜索 | Minimax MCP web_search | 公司主营业务搜索，结果缓存为本地 MD |

---

## 项目结构

```
stock_run_88/
├── backend/
│   ├── main.py                  # FastAPI 入口，路由注册，CORS 中间件
│   ├── config.py                # 全局配置：API Key、交易常量、路径
│   ├── agents/                  # Agent 模块
│   │   ├── base.py              # AgentContext / AgentDecision 数据类
│   │   ├── factory.py           # AgentManager：创建/删除/重命名/配置/查询
│   │   ├── llm_agent.py         # LLM Agent：系统提示词 + LangChain create_agent
│   │   └── tools.py             # 6 个 Agent 工具函数（@tool 装饰器）
│   ├── api/                     # REST API 路由
│   │   ├── strategy_routes.py   # 策略选股 API
│   │   ├── agent_routes.py      # Agent CRUD API
│   │   ├── market_routes.py     # 大盘/板块热度 API
│   │   └── backtest_routes.py   # 回测 API
│   ├── strategies/              # 内置策略
│   │   ├── base.py              # 策略基类 + StrategyResult
│   │   ├── registry.py          # @StrategyRegistry.register 装饰器注册中心
│   │   ├── momentum.py          # 龙头打板战法
│   │   ├── trend.py             # 动量趋势策略
│   │   └── ma_pullback.py       # 20/60 均线回调企稳策略
│   ├── trading/                 # 交易规则与计算
│   │   ├── rules.py             # T+1、涨跌停、一字板、费率、撮合
│   │   └── calculator.py        # 仓位/盈亏/最大可买计算（纯函数，禁止 LLM 算数）
│   ├── backtest/                # 回测引擎
│   │   ├── engine.py            # 历史 K 线逐日模拟，T+1/费率/涨跌停约束
│   │   └── metrics.py           # 年化收益/最大回撤/胜率/夏普/盈亏比
│   ├── pipeline/                # 每日交易流水线
│   │   ├── daily_pipeline.py    # 市价估值 → 撮合 → LLM 分析 → 条件单 → 复盘报告
│   │   └── order_executor.py    # 条件单撮合引擎
│   ├── data/                    # 数据层
│   │   ├── loader.py            # CSV 加载器 + 均线/涨跌停状态计算
│   │   ├── indicators.py        # 板块热度 / 均线偏离计算
│   │   └── fetcher.py           # Baostock 增量数据获取
│   ├── search_agent/            # 公司业务搜索
│   │   ├── searcher.py          # Minimax 联网搜索 + MD 缓存管理
│   │   └── sector.py            # 关键词 → 板块分类匹配
│   ├── policy/                  # 宏观政策监控
│   │   ├── crawler.py           # 爬虫：发改委/工信部/财政部政策文件抓取
│   │   └── reader.py            # 政策文件解析 + 产业政策信号提取
│   ├── llm/                     # LLM 客户端
│   │   ├── client.py            # DeepSeek ChatOpenAI 构建
│   │   └── strategy_parser.py   # 自然语言 → 策略规则解析
│   ├── logs/                    # 日志模块
│   │   ├── thinking_logger.py   # Agent 思维链日志记录
│   │   └── report_generator.py  # 每日 Markdown 复盘报告生成
│   └── db/                      # 数据库
│       ├── schema.py            # 8 张核心表 DDL + 预置 Seed 数据
│       └── repository.py        # CRUD 操作封装
├── frontend/
│   └── src/
│       ├── App.vue              # 根组件（侧边栏导航 + 路由出口）
│       ├── main.js              # Vue 应用入口
│       ├── api/index.js         # Axios 封装 + API 方法
│       ├── router/index.js      # 4 个路由页面
│       └── views/
│           ├── Dashboard.vue    # 大盘看板（Agent 卡片 + K 线 + 板块热度）
│           ├── AgentDetail.vue  # Agent 详情（持仓/净值/交易记录/条件单）
│           ├── AIChat.vue       # AI 对话选股（策略按钮 + 自然语言搜索）
│           └── Backtest.vue     # 策略回测（指标卡 + 净值曲线）
├── data/
│   ├── daily/                   # 2145 只主板股票日线 CSV
│   ├── index/                   # 指数 K 线数据
│   ├── company_business/        # 公司主营业务 MD 缓存
│   ├── policy_docs/             # 宏观政策文件 MD（发改委/工信部/财政部）
│   └── stock_run.db             # SQLite 数据库
├── logs/                        # Agent 运行日志
├── reports/                     # 每日 Markdown 复盘报告
├── requirements.txt             # Python 依赖
├── start.sh                     # 一键启动脚本
└── README.md
```

---

## Agent 设计

### 架构概览

系统采用 **LLM Agent + 工具调用** 架构。Agent 本身由 LangChain `create_agent` 创建，绑定 6 个工具函数，通过系统提示词约束行为边界。Agent 不直接操作数据库——所有计算（仓位、费率、盈亏）封装为纯 Python 函数，禁止 LLM 自行算数。

```
用户 / 定时流水线
       │
       ▼
┌─────────────────────────────────────┐
│           AgentManager              │
│  - create / delete / rename         │
│  - configure (策略绑定 + 风控参数)    │
│  - get_context (组装决策上下文)       │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│        LLM Agent (LangChain)        │
│  ┌─────────────────────────────┐    │
│  │   System Prompt             │    │
│  │   - 交易约束（T+1/费率/仓位）  │    │
│  │   - 决策流程（6步）           │    │
│  │   - 输出格式（JSON 交易计划）  │    │
│  └─────────────────────────────┘    │
│  ┌─────────────────────────────┐    │
│  │   6 个 Tool Functions       │    │
│  │   - search_stocks_by_strategy│   │
│  │   - get_stock_kline          │   │
│  │   - get_market_overview      │   │
│  │   - get_company_business     │   │
│  │   - compute_sector_heat_tool │   │
│  │   - get_policy_signals       │   │
│  └─────────────────────────────┘    │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│         AgentDecision               │
│  - market_analysis: 大盘分析         │
│  - selected_stocks: 选股结果         │
│  - orders: 条件单列表                │
│  - risk_assessment: 风险评估         │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│        Order Executor               │
│  - T+1 / 一字板 / 费率检查           │
│  - 条件单撮合（price ∈ [low, high]） │
│  - 更新持仓 / 余额 / 交易记录          │
└─────────────────────────────────────┘
```

### Agent 决策流程

Agent 每次运行按以下步骤依次调用工具：

1. **get_market_overview** — 获取上证指数走势，判断市场整体趋势
2. **get_policy_signals** — 获取近期宏观政策信号（发改委/工信部/财政部），识别政策利好板块
3. **compute_sector_heat_tool** — 获取板块热度排行（涨停家数/连板高度/成交量放量）
4. **search_stocks_by_strategy** — 使用内置策略（龙头打板/动量趋势/均线回调）筛选候选股
5. **get_stock_kline** — 获取候选股 K 线数据，分析技术面（均线/换手/量价）
6. **get_company_business** — 获取公司主营业务信息，确认基本面
7. 综合上述信息，生成 JSON 格式交易计划

### Agent 工具函数

| 工具 | 功能 | 数据来源 |
|------|------|----------|
| `search_stocks_by_strategy` | 调用策略筛选股票 | CSV K 线 + 策略引擎 |
| `get_stock_kline` | 获取个股日线(收盘/涨跌/换手/均线) | CSV K 线 + 均线计算 |
| `get_market_overview` | 获取上证指数概况 | 指数 CSV |
| `get_company_business` | 获取公司主营业务 | 本地 MD 缓存 / Minimax 搜索 |
| `compute_sector_heat_tool` | 板块热度排行 | K 线 + 板块分类 |
| `get_policy_signals` | 宏观政策信号提取 | 发改委/工信部/财政部 MD 文件 |

---

## Agent 参数配置

### 创建 Agent 参数

```python
AgentManager.create(
    name="agent_xxx",           # 内部标识名，唯一
    display_name="XXX Agent",   # 前端显示名称
    agent_type="custom",        # 类型: chaser / autonomous / custom
    strategy_ids="momentum",    # 绑定策略 ID（逗号分隔）
    initial_capital=150_000.0,  # 初始本金（元）
)
```

### 风控配置 (risk_config)

```json
{
    "max_position_count": 5,     // 最大持仓股票数（硬约束）
    "max_daily_loss": 0.05,      // 单日最大亏损比例（触发则停止新开仓）
    "max_total_position": 0.8    // 总仓位上限（占总资产比例）
}
```

- **max_position_count = 5**：系统层硬限制，持仓数达上限后禁止开新仓。建议保持 3 只左右
- **max_daily_loss = 5%**：当日亏损超过总资产的 5% 触发熔断，仅停止新开仓，不强制平仓
- **max_total_position = 80%**：总仓位（持仓市值/总资产）上限，保留至少 20% 现金

### 预置 Agent

| Agent | 类型 | 绑定策略 | 风格 |
|-------|------|----------|------|
| 追高打板Agent | chaser | 龙头打板战法 | 关注涨停连板、换手率变化、资金接力 |
| 自主决策Agent | autonomous | 无预设 | 完全自主决策，无人工提示词干涉 |

---

## 交易规则

### 制度约束

| 规则 | 说明 |
|------|------|
| **标的限制** | 仅 60（上证主板）/ 00（深证主板）开头 A 股 |
| **T+1** | 当日买入次日方可卖出 |
| **涨跌停** | 一字涨停/一字跌停当日禁止买卖（非一字的涨跌停可交易） |
| **ST 过滤** | 自动排除 ST / *ST 股票 |

### 费率

| 费用 | 费率 | 方向 |
|------|------|------|
| 券商佣金 | 万 0.854 | 买入 + 卖出双向 |
| 印花税 | 万 5 | 仅卖出单向 |

### 条件单撮合

- 条件单设定价格落在当日最低价~最高价区间内，按设定价成交
- 不在区间内的条件单自动跳过，不成交

### 做T

- 正T（先买后卖）和反T（先卖后买）技术层面允许
- **不推荐**频繁做T，由 Agent 自行判断是否操作

---

## API 路由

### 策略选股

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/strategy/builtin` | 内置策略列表 |
| POST | `/api/strategy/run` | 按策略名称运行选股 |
| POST | `/api/strategy/select` | 自然语言选股（LLM 解析） |

### Agent 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/agent/list` | Agent 列表 |
| GET | `/api/agent/{id}` | Agent 详情（含持仓/净值） |
| POST | `/api/agent/create` | 创建 Agent |
| DELETE | `/api/agent/{id}` | 删除 Agent |
| PUT | `/api/agent/{id}/rename` | 重命名 |
| PUT | `/api/agent/{id}/configure` | 更新配置/风控参数 |

### 市场数据

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/market/index` | 上证指数走势（最近 N 天） |
| GET | `/api/market/sector-heat` | 板块热度排行 |

### 回测

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/backtest/periods` | 快捷回测区间列表 |
| GET | `/api/backtest/quick/{strategy}` | 快捷回测 |
| POST | `/api/backtest/run` | 自定义区间回测 |

---

## 数据库

### 核心表

| 表 | 说明 |
|------|------|
| `stock_basic` | 个股基础信息 + 板块分类 |
| `kline_daily` | 日线 K 线 + 均线(M5/M10/M20/M60) + 涨跌停标记 |
| `agent_info` | Agent 配置（名称/类型/资金/策略绑定/风控参数） |
| `agent_position` | 持仓表（股数/可用数/成本/市价/浮盈） |
| `agent_order` | 条件单（方向/类型/价格/状态） |
| `agent_trade_log` | 交易记录（成交价/手续费/印花税） |
| `agent_daily_report` | 每日快照（总资产/收益率/复盘路径） |
| `strategy_repository` | 策略仓库（内置 + 自定义） |

---

## 内置策略

### 1. 龙头打板战法 (momentum)

追踪涨停龙头股，分析连板阶段和换手率变化，识别妖股中后期资金接力机会。

### 2. 动量趋势策略 (trend)

识别多波趋势上涨行情，捕捉健康回调后的二次启动点。

### 3. 20/60均线回调企稳策略 (ma_pullback)

股价回调至 20 日或 60 日均线附近企稳，成交量萎缩后放量反弹。

---

## 宏观政策监控

系统内置三大部委政策爬虫：

- **发改委** (ndrc.gov.cn) — 产业政策、投资目录、价格管理
- **工信部** (miit.gov.cn) — 新能源汽车、人工智能、5G、工业互联网
- **财政部** (mof.gov.cn) — 税收优惠、财政补贴、政府债券

爬取的政策文件保存为本地 MD 文件（`data/policy_docs/`），Agent 调用 `get_policy_signals` 工具时自动提取产业政策信号，识别政策利好的行业板块。支持通过 `backend/policy/crawler.py` 定时运行更新。

---

## 快速启动

```bash
# 1. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 2. 安装依赖（清华源）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 初始化数据库
.venv/bin/python3 -c "
import sys; sys.path.insert(0, '.')
from backend.db.schema import init_db
init_db()
"

# 4. 一键启动
bash start.sh
```

启动后访问：
- 前端：`http://localhost:5173`
- 后端 API：`http://localhost:18000`
- API 文档：`http://localhost:18000/docs`
