# A 股多 Agent 智能投顾模拟交易系统项目总览

更新时间：2026-05-21

## 1. 一句话说明

这是一个面向 A 股的多 Agent 模拟交易与股票推荐系统。它把行情数据、策略选股、LLM ReAct 决策、条件单撮合、每日复盘、Telegram 推送、用户推荐问答、评估指标和 Agent 进化记忆串成一个闭环。

它不是单纯的聊天机器人，也不是只跑技术指标的量化脚本，而是一个“带交易规则、数据延迟处理、订单生命周期、复盘评估和可解释 trace 的实验型交易 Agent 平台”。

## 2. 业务场景

系统当前服务三个核心场景：

1. 多交易员 Agent 模拟交易
   - 每个 Agent 有独立资金、持仓、策略偏好、提示词、工具权限和定时复盘配置。
   - 每日行情更新后，Agent 自动复盘、调用工具、生成次日条件单。
   - 次日按 A 股规则撮合条件单，更新持仓、现金、净值和交易记录。

2. Telegram 每日推送和股票助手
   - Telegram bot 可推送每个 Agent 的日报。
   - 用户可直接问“推荐一个龙头股”“选几只回踩 20 日线的股票”等问题。
   - 推荐助手会调用选股、分析、用户画像、交易员记忆和反馈工具，生成推荐理由并保存 trace。

3. 评估与进化闭环
   - 记录交易收益、风险、订单质量、LLM 成本、工具调用、JSON 修复、订单 trace。
   - 根据交易结果、失败订单、用户反馈和后验收益调整技能置信度。
   - 将复盘结果沉淀为 Agent 记忆和交易体系文档。

## 3. 当前已经做了什么

### 3.1 交易 Agent

当前主要有三个 Agent：

| Agent | 状态 | 定位 |
|---|---|---|
| 追高打板Agent | active | 偏情绪周期、连板、高开、主线加速 |
| 自主决策Agent | active | 综合政策、基本面、技术、资金、情绪 |
| 深度推理Agent | disabled | 更高推理强度的备用 Agent |

已支持能力：

- Agent 独立配置：状态、复盘时间、推送时间、工具轮数、推理强度、风格提示词。
- 工具白名单：每个 Agent 可限制可调用工具。
- 优先策略：可绑定 momentum、ma_pullback、trend 等策略。
- 板块权限：主板/中小板默认可买，创业板/科创板/北交所按资金和交易天数自动解锁，也可手动配置。
- 每日 ReAct 决策：Agent 读取行情、政策、板块、持仓、近期订单、失败原因、进化记忆后生成 JSON 交易计划。
- 输出校验：挂单价格必须通过涨跌停校验，非法价格会触发修复流程。

### 3.2 订单与撮合

订单链路已经覆盖：

- 买单冻结资金。
- 卖单校验持仓、可卖股数和 T+1。
- 开盘抢入/抢出 `open_get_in`。
- 普通限价撮合：限价落在当日 high/low 区间内即成交。
- 隔日未成交 pending 单自动过期。
- 新复盘替换旧预操作单。
- 订单 trace：记录创建、触达、成交、过期、替换等生命周期事件。
- 决策批次：每次复盘生成 `decision_batch_id`，把同一轮买卖订单串起来。
- 下单前成交概率估计：按历史区间触达概率估算 `fill_probability`，并记录价格偏离 `price_aggressiveness`。
- 失败订单反哺：下一次 LLM 决策会看到失败订单、失败原因、上次成交概率和价格偏离。

### 3.3 每日自动化

默认启动后自动化是开启的：

- Scheduler：每 5 分钟检查是否到复盘/推送时间。
- Market data fetch：交易日 18:00 后自动拉取 baostock 增量行情。
- Policy crawler：每日 21:00 后抓取政策文件。
- Stock universe refresh：每 7 天刷新一次 A 股股票池。
- Telegram polling：自动监听 Telegram 消息。

数据就绪门槛：

- 当前默认要求 A 股主板/中小板数据覆盖率达到 95% 后才触发复盘。
- 指数数据必须更新到目标交易日。
- 未就绪时按退避策略等待重试，避免用半截行情跑复盘。

### 3.4 Telegram 推荐助手

Telegram 侧已经支持：

- Bot 状态查询和 chat 绑定。
- Agent 日报推送。
- 用户画像、风险偏好、周期偏好、关注股。
- 自然语言推荐入口。
- 推荐 trace 和推荐反馈。
- 推荐评估、成本和后验收益表。
- 推荐 ReAct 工具循环，失败时有规则链 fallback。

推荐助手可调用的核心工具包括：

- `recommend_search_stocks`
- `recommend_analyze_stock`
- `recommend_compare_stocks`
- `recommend_get_user_profile`
- `recommend_get_watchlist`
- `recommend_get_trader_memory`
- `recommend_get_agent_performance`
- `recommend_record_feedback`

### 3.5 前端看板

前端是 Vue 3 + Vite 单页应用，当前主要页面包括：

- 首页 Dashboard：Agent 卡片、大盘 K 线、板块强弱、政策动态、赛马对比。
- AgentDetail：基础配置、进化记忆、提示词预览、评估指标、订单 trace、持仓、成交、pending 条件单。
- Telegram 页面：Bot 绑定、推荐测试、trace、用户画像、关注股、推荐评估。
- Backtest/Simulation：策略回测和模拟任务。
- StockViewer：个股 K 线和公司信息。

Agent 详情页已能查看：

- 成交和条件单的买卖逻辑侧栏。
- 订单生命周期 trace。
- 决策批次。
- 估计成交概率和价格偏离。
- 成本、工具调用、JSON 修复、风险/收益评估指标。

## 4. 技术栈

### 4.1 后端

| 层 | 技术 | 作用 |
|---|---|---|
| Web API | FastAPI + Uvicorn | REST API、调度状态、前端数据接口 |
| LLM 接入 | OpenAI-compatible client + LangChain | ReAct 工具调用、推荐助手、自然语言解析 |
| 数据源 | baostock | A 股日线、指数、股票池 |
| 数据处理 | pandas / numpy | K 线计算、均线、涨跌停、策略筛选、回测 |
| 数据库 | SQLite | Agent、订单、成交、日报、trace、推荐评估 |
| 日历 | chinese-calendar | A 股交易日近似判断 |
| 文档爬虫 | requests / BeautifulSoup | 政策文件抓取和政策信号提取 |

### 4.2 前端

| 层 | 技术 | 作用 |
|---|---|---|
| UI 框架 | Vue 3 Composition API | 前端页面和交互 |
| 构建 | Vite | 本地开发和生产构建 |
| 图表 | ECharts | K 线、净值曲线、指标可视化 |
| HTTP | Axios | API 调用 |
| 路由 | Vue Router | 单页应用导航 |

### 4.3 存储与文件

| 路径 | 内容 |
|---|---|
| `data/stock_run.db` | SQLite 主数据库 |
| `data/daily/*.csv` | 个股日线 |
| `data/index/*.csv` | 指数日线 |
| `data/stock_basic_cache.csv` | 股票池缓存 |
| `data/policy_docs/` | 政策文档 Markdown |
| `reports/{date}/{agent}/review.md` | 每日复盘报告 |
| `logs/{date}/{agent}/thinking.log` | Agent 工具调用和 LLM 输出日志 |
| `agent_memory/` | Agent 记忆和推荐助手记忆 |

## 5. 关键模块

| 模块 | 位置 | 说明 |
|---|---|---|
| API 入口 | `backend/main.py` | 注册路由、启动 scheduler/polling/crawler |
| 交易流水线 | `backend/pipeline/daily_pipeline.py` | 数据检查、撮合、复盘、日报、推送 |
| 撮合引擎 | `backend/pipeline/order_executor.py` | 条件单成交规则 |
| LLM 交易 Agent | `backend/agents/llm_agent.py` | 系统提示词、ReAct loop、JSON 解析修复 |
| Agent 工具 | `backend/agents/tools.py` | 行情、策略、政策、历史订单、价格校验 |
| 数据库 schema | `backend/db/schema.py` | 表结构和迁移 |
| 数据访问 | `backend/db/repository.py` | CRUD、订单 trace、批次状态 |
| 评估系统 | `backend/evaluation.py` | 收益、风险、成本、质量指标 |
| 进化系统 | `backend/evolution/` | 技能置信度、记忆、反思、赛马 |
| 推荐助手 | `backend/telegram/recommender.py` | Telegram 推荐 ReAct 和 fallback |
| 推荐评估 | `backend/telegram/evaluation.py` | 推荐成本、反馈、后验收益 |
| Telegram 网关 | `backend/telegram/gateway.py` | sendMessage、日报推送、绑定 |
| 股票池刷新 | `backend/data/stock_universe.py` | 全 A 股票池维护 |
| 前端 API | `frontend/src/api/index.js` | 前端 API 封装 |
| Agent 详情页 | `frontend/src/views/AgentDetail.vue` | 配置、评估、订单、trace 展示 |

## 6. 核心数据流

### 6.1 每日交易闭环

```text
baostock 增量行情
  -> data/daily + data/index
  -> 数据覆盖率检查 >= 95%
  -> 撮合昨天 pending 条件单
  -> 更新现金、持仓、市值、净值
  -> 交易 Agent ReAct 调用工具
  -> 解析 JSON 交易计划
  -> 价格校验 / 资金冻结 / T+1 校验
  -> 写入次日 agent_order
  -> 生成日报和 thinking log
  -> 更新评估指标和进化记忆
  -> Telegram 推送
```

### 6.2 推荐问答闭环

```text
Telegram 用户问题
  -> 用户画像和关注股
  -> 推荐助手 ReAct 工具调用
  -> 策略筛选 / 个股分析 / 多股比较 / 交易员记忆
  -> 推荐 JSON 和自然语言回复
  -> 写入推荐记录、trace、成本
  -> 用户反馈
  -> 后验收益更新
  -> 技能置信度调整
```

### 6.3 订单 trace 闭环

```text
Agent 决策批次 decision_batch_id
  -> 多笔买卖条件单
  -> 创建 trace
  -> 次日撮合 trace
  -> filled / expired / replaced
  -> 失败原因回写
  -> 下一次提示词读取失败反哺
```

## 7. 数据库核心表

| 表 | 用途 |
|---|---|
| `agent_info` | Agent 基础信息、资金、策略和风险配置 |
| `agent_schedule` | 定时复盘、推送、重试状态 |
| `agent_position` | 持仓、成本、市值、浮盈 |
| `agent_order` | 条件单、冻结资金、失败原因、成交概率 |
| `agent_order_trace` | 订单生命周期 trace |
| `agent_decision_batch` | 每次复盘下单批次和批次质量 |
| `agent_trade_log` | 成交流水 |
| `agent_daily_report` | 每日净值和报告路径 |
| `agent_eval_metric` | 收益、风险、质量、成本评估 |
| `agent_race_metric` | 赛马评分和风格标签 |
| `agent_evolution_skill` | 技能置信度和失败率 |
| `telegram_binding` | Telegram chat 与 Agent 绑定 |
| `telegram_user_profile` | 用户风险偏好、周期、板块偏好 |
| `telegram_watchlist` | 用户关注股 |
| `telegram_recommend_feedback` | 推荐记录和用户反馈 |
| `telegram_recommend_eval` | 推荐质量评估 |
| `telegram_recommend_cost` | 推荐成本 |
| `telegram_recommend_outcome` | 推荐后验收益 |

## 8. 当前技术深度

这个项目的技术深度主要不在单点算法，而在“LLM Agent 与真实业务规则的闭环工程化”。

### 8.1 LLM 工程

- 手写 ReAct loop，而不是完全依赖黑盒 agent runtime。
- 工具白名单、工具调用上限、失败 fallback。
- JSON 输出解析、修复、价格合法性二次校验。
- 将失败订单、历史工具证据、赛马指标、技能置信度注入下一次 prompt。
- 交易 Agent 和推荐助手分别有不同工具集、记忆和评价口径。

### 8.2 交易规则工程

- A 股 T+1、涨跌停、开盘抢入、限价触达、资金冻结、卖出可用股数。
- 复盘前快照，失败回滚，避免半写入状态污染。
- 数据覆盖率门槛和退避重试，防止行情未更新时误跑。
- 订单批次和 trace，能解释“为什么有成交、为什么有过期、为什么被替换”。

### 8.3 评估工程

- 收益：日收益、累计收益、超额收益、Alpha。
- 风险：最大回撤、波动率、VaR、CVaR。
- 交易质量：成交率、过期率、开盘抢入成功率、换手率。
- 工程质量：token、工具调用、工具失败、LLM 耗时、JSON 修复。
- 进化质量：技能置信度变化、反思触发、记忆压缩。
- 推荐质量：用户反馈、成本、后验 T+1/T+3/T+5 收益。

### 8.4 产品工程

- 前端可直接配置 Agent 风格、工具、策略、板块权限和阶段提示词。
- 前端能看 prompt preview、成本、评估、订单 trace、买卖逻辑侧栏。
- Telegram 可承接日报、用户问答、推荐、画像、关注股和反馈。

## 9. 如何运行

### 9.1 环境变量

需要在 `.env` 或 shell 中配置：

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=...
OPENAI_MODEL=...
TELEGRAM_BOT_TOKEN=...
```

可选：

```bash
DATA_FRESHNESS_MIN_RATIO=0.95
MARKET_DATA_FETCH_TIME=18:00
SCHEDULER_ENABLED=1
POLICY_CRAWLER_ENABLED=1
STOCK_UNIVERSE_REFRESH_ENABLED=1
TELEGRAM_POLLING_ENABLED=1
```

### 9.2 本地启动

```bash
cd /home/xulu/stock_run_88
scripts/dev_start.sh
```

默认地址：

- 后端：`http://localhost:18000`
- 前端：`http://localhost:5173`

### 9.3 常用命令

```bash
# 前端构建
npm --prefix frontend run build

# 手动触发到点 Agent
curl -X POST http://localhost:18000/api/agent/run-due

# 查看自动化状态
curl http://localhost:18000/api/automation/status

# 刷新股票池
.venv/bin/python scripts/update_stock_universe.py

# 刷新股票池并为新股补日线
.venv/bin/python scripts/update_stock_universe.py --fetch-new-daily

# 推荐助手冒烟
.venv/bin/python scripts/recommend_smoke.py
```

## 10. 当前主要 Todo

### 10.1 高优先级

- 将行情拉取从单线程改为可控并发，提高 3000+ 股票每日更新速度。
- 将数据覆盖率、已更新数量、缺失样本直接展示到前端自动化状态页。
- 修复/优化 scheduler 的 retry 逻辑：数据从未就绪变为就绪时，不应继续等待旧的 `next_retry_at`。
- 给 Telegram 推送增加“数据已就绪/复盘开始/复盘完成”的更细粒度状态通知。
- 为 Agent 复盘增加任务表，替代长 HTTP 请求等待，前端可轮询任务状态。

### 10.2 中优先级

- 推荐助手补更严格的回测型评价：推荐后 T+1/T+3/T+5 与大盘、板块对比。
- 为订单决策批次增加批次复盘：同一批买卖是否互相依赖，是否存在资金非原子风险。
- 将 Agent 交易体系文档做版本 diff 和置信度标注。
- 前端增加分钟复盘摘要和成交触发区间可视化。
- 给每个工具补结构化输入/输出 schema 文档，减少 LLM 工具误用。

### 10.3 低优先级

- SQLite 迁移到 Postgres 或 DuckDB + SQLite 混合模式。
- 引入任务队列，例如 APScheduler/RQ/Celery，替代线程调度。
- 前端做更完整的移动端适配。
- 增加 Playwright E2E 用例，覆盖 Agent 详情、Telegram 推荐和自动化状态。
- 把政策爬虫去重、增量抓取和重启防重复抓取做得更严谨。

## 11. 目前已知边界

- 这是模拟交易系统，不是真实券商交易系统。
- baostock 行情通常在收盘后一段时间才稳定，过早运行会触发等待。
- 当前交易日判断基于 `chinese-calendar`，仍需要关注交易所特殊休市安排。
- LLM 决策具有不确定性，必须依赖工具校验和交易规则兜底。
- 前端构建可验证静态正确性，但当前还缺少系统化浏览器自动测试。
- 数据文件和 SQLite 是运行期资产，开发提交时应避免把大批 CSV/DB 误提交。

## 12. 新人阅读顺序

推荐按这个顺序读代码：

1. `PROJECT_OVERVIEW.md`：先理解业务和闭环。
2. `backend/pipeline/daily_pipeline.py`：理解每日主流程。
3. `backend/agents/llm_agent.py`：理解 Agent 如何调用工具和输出订单。
4. `backend/agents/tools.py`：理解 Agent 能看到什么。
5. `backend/pipeline/order_executor.py`：理解订单如何成交。
6. `backend/db/schema.py`：理解数据模型。
7. `backend/telegram/recommender.py`：理解推荐助手。
8. `frontend/src/views/AgentDetail.vue`：理解前端如何展示配置、评估和 trace。

