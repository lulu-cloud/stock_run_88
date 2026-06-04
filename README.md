# stock_run_88

A 股多 Agent 模拟交易与 Telegram 智能推荐系统。

这个项目把 A 股行情数据、策略选股、交易员 Agent、Telegram 推荐助手、记忆系统、每日复盘推送和前端看板串成一套闭环。它不是实盘交易系统，当前定位是研究、模拟交易、Agent 工程展示和个人投研自动化。

## 业务定位

系统核心做三件事：

1. 让多个交易员 Agent 基于不同风格独立复盘、选股、生成条件单，并在模拟账户里执行。
2. 让 Telegram 推荐助手用自然语言回答选股、单股分析、政策偏好、关注股、均线趋势等问题。
3. 让交易员 Agent 与推荐助手共享可复用记忆，包括用户偏好、群聊共识、关注股票研究报告、交易员进化经验和工具 trace。

典型问题：

```text
推荐几个多头均线向上的股票
京东方A如何看
最近十天价格与量趋势怎么样
找几只回踩20日线的强势股
最近国家政策偏好是什么
记住我偏好短线右侧交易，喜欢均线发散后回踩10日线
```

## 技术架构

```text
Telegram / Web 前端
        |
        v
FastAPI backend.main
        |
        +-- Telegram 推荐助手
        |     +-- ReActLoop
        |     +-- 推荐工具
        |     +-- 用户/群聊/topic 记忆
        |     +-- 推荐评估与后验收益
        |
        +-- 交易员 Agent
        |     +-- ReActLoop
        |     +-- 行情/策略/风控/下单草稿工具
        |     +-- 每日流水线
        |     +-- 进化记忆与赛马指标
        |
        +-- 数据与研究
        |     +-- A 股日线 CSV
        |     +-- SQLite 运行态 DB
        |     +-- 政策文档缓存
        |     +-- 公司主营业务缓存
        |
        +-- 前端看板
              +-- Agent 看板
              +-- 每日盈亏日历
              +-- 评估指标
              +-- 成本/trace/提示词预览
```

主要技术：

| 层 | 技术 |
| --- | --- |
| 后端 API | FastAPI, Uvicorn |
| Agent 编排 | LangChain, 自研 `ReActLoop` |
| LLM | DeepSeek OpenAI-compatible API |
| 数据库 | SQLite + WAL |
| 行情数据 | 本地 CSV, Baostock 数据脚本 |
| 前端 | Vue 3, Vite, ECharts, Axios |
| 推送与对话 | Telegram Bot long polling |
| 部署 | GitHub Actions + SSH/SCP + systemd + Nginx |

## 目录结构

```text
backend/
  agents/              交易员 Agent、工具、通用 ReAct loop
  api/                 FastAPI 路由
  data/                行情读取、股票池、标签
  db/                  SQLite schema 和 repository
  evolution/           Agent 进化、反思、记忆、赛马
  llm/                 LLM 客户端和自然语言策略解析
  pipeline/            每日交易流水线与订单撮合
  policy/              政策爬虫和政策信号读取
  search_agent/        公司主营业务搜索与缓存
  simulation/          模拟任务
  strategies/          内置选股策略
  telegram/            Telegram 推荐助手、推送、记忆、评估
  trading/             交易规则、费用、T+1、计算器

frontend/
  src/                 Vue 前端源码

scripts/
  update_stock_universe.py
  fetch_full_history.py
  run_due_agents.py
  recommend_smoke.py

data/                  运行态数据，不随 GitHub 发版覆盖
logs/                  运行日志，不随 GitHub 发版覆盖
reports/               复盘报告，不随 GitHub 发版覆盖
agent_memory/          Agent 文件记忆，不随 GitHub 发版覆盖
```

## 系统流程

### 每日交易流水线

```text
行情数据更新
    |
数据完整性检查
    |
撮合昨日/当前 pending 条件单
    |
更新持仓、现金、交易日志
    |
每个交易员 Agent 复盘
    |
Agent 调用行情、策略、板块、政策、共享研究等工具
    |
生成买入/卖出/条件单草稿
    |
订单冲突与价格校验
    |
写入条件单、trace、评估指标
    |
生成 Telegram 每日推送
```

### Telegram 推荐助手流程

```text
Telegram message
    |
polling.py 取 chat_id/user_id/thread_id
    |
handle_text_message 记录短期消息
    |
读取短期上下文 + session 摘要 + 长期记忆
    |
规则直答 或 ReAct 推荐助手
    |
调用选股、单股分析、政策、关注股、共享研究等工具
    |
返回 Markdown/HTML 兼容文本
    |
后台 memory_distiller 异步提炼长期画像
```

## 交易员 Agent 功能

交易员 Agent 用于模拟不同交易风格。

已支持能力：

- 多 Agent 独立账户、独立持仓、独立条件单。
- 自定义交易员，可配置股票池和用户原始策略。
- 可配置是否允许脱离用户股票池自主选股。
- 交易风格注入，原始策略与进化记忆分开保存。
- ReAct 工具循环，支持重试、熔断、工具 trace。
- 可调用行情、K 线、策略选股、政策、板块热度、共享研究、风险指标等工具。
- 支持下单草稿、取消草稿、订单 trace 和跨 Agent 冲突 warning。
- 支持多头均线发散、回踩均线、龙头强势、动量趋势等策略工具。
- 支持赛马指标、成本指标、prompt 预览、Agent 评估看板。
- 支持进化记忆、技能置信度、事件触发反思、因子权重调整建议。

交易执行仍是模拟环境，不连接券商，不自动实盘下单。

## 推荐助手 Agent 功能

Telegram 推荐助手用于自然语言投研。

已支持能力：

- 推荐股票：
  - 龙头股
  - 强势股
  - 多头均线发散
  - 回踩 5/10/20 日线
  - 多头均线向上
- 单股分析：
  - `京东方A如何看`
  - `利通电子是不是大牛股`
  - `最近十天价格与量趋势`
  - `是否多头均线发散`
- 多股对比：
  - `/compare 000725.SZ 600519.SH`
- 政策偏好：
  - 读取本地政策缓存，归纳高频产业方向。
- 用户画像：
  - 显式 `/profile set`
  - 多轮对话后自动提炼长期偏好。
- 关注股：
  - `/watch add`
  - `/watch list`
  - `/watch remove`
- 推荐评估：
  - 记录 trace
  - 记录 token/耗时/工具调用
  - 记录用户反馈
  - 计算 T+1/T+3/T+5 后验收益
- 动态过程展示：
  - Telegram 中先展示“正在处理/调用工具”
  - 最终回复后删除过程消息，只保留正式结果。

常用指令：

```text
/help
/profile
/profile set 风险=中等 周期=短线 板块=AI,半导体
/memory
/memory forget 关键词
/watch add 000725.SZ
/watch list
/recommend 推荐几个多头均线向上的股票
```

## 股票与行情功能

股票数据以本地运行态为主：

- `data/daily/`：个股日线 CSV。
- `data/index/`：指数日线 CSV。
- `data/stock_basic_cache.csv`：股票基础信息缓存。
- `data/policy_docs/`：政策文档缓存。
- `data/company_business/`：公司主营业务缓存。

核心能力：

- 股票基础信息每周更新，覆盖新股。
- 日线增量更新。
- 均线计算：MA5/MA10/MA20/MA30/MA60。
- 涨跌停识别。
- 换手率、量比、放量、趋势摘要。
- 板块温度统计：
  - 涨停数量
  - 大涨数量
  - 跌停数量
  - 大跌数量
  - 板块热度排行
- 自然语言策略解析：
  - 多头均线发散
  - 均线回踩
  - 龙头强势
  - 动量趋势
  - 箱体震荡
  - 底部反转

## 记忆系统

记忆系统用于让推荐助手和交易员 Agent 形成可演化上下文。

### 分层记忆

| 层级 | 范围 | 说明 |
| --- | --- | --- |
| 短期记忆 | 当前 chat/thread 最近 5-8 轮 | 默认 6 轮，即 12 条 user/assistant 消息 |
| 中期记忆 | `chat_id + user_id + thread_id` | 当前 session 摘要、当前任务、未解决问题 |
| 长期用户画像 | `scope=user` | 用户偏好、风险画像、关注股票 |
| 长期群聊画像 | `scope=chat` | 群规则、群共识、群聊长期背景 |
| topic 画像 | `scope=thread` | Telegram topic 独立沉淀 |
| 全局记忆 | `scope=global` | 预留的系统级长期经验 |

### 自动提炼

每次 Telegram 对话都会写入 `telegram_conversation_message`。

当同一 `chat_id + user_id + thread_id` 在上次提炼之后新增至少 3 条用户消息时，后台 `memory_distiller` 会异步调用 LLM，输出：

```json
{
  "session_summary": {},
  "user_preferences": [],
  "risk_profile": [],
  "stock_interests": [],
  "chat_norms": [],
  "thread_summary": [],
  "memory_updates": [],
  "do_not_remember": []
}
```

### 智能冲突合并

长期记忆不是无限追加。distiller 会读取已有长期记忆，让 LLM 判断是否冲突，并输出更新动作：

| 动作 | 效果 |
| --- | --- |
| `replace` | 归档旧记忆，新记忆由本轮提炼补入 |
| `deprioritize` | 降低旧记忆权重 |
| `expire` | 归档旧记忆 |
| `keep` | 小幅增强旧记忆权重 |

长期记忆有状态：

- `active`
- `archived`

Prompt 只检索 `active` 记忆。归档记忆保留历史，不进入上下文。

默认每个 `scope + scope_id` 最多保留 80 条 active 长期记忆，避免上下文膨胀：

```bash
TELEGRAM_LONG_TERM_MEMORY_MAX_PER_SCOPE=80
```

## 前端看板

前端用于本地或服务器 Web 查看：

- 总览 Dashboard。
- 多 Agent 战绩。
- 每日盈亏日历，支持总览和单 Agent。
- Agent 详情：
  - 基础配置
  - 风格提示词
  - 工具权限
  - 板块权限
  - 进化记忆
  - 评估指标
  - 持仓与个股仓位
  - 最近交易
  - 条件单
  - 买卖逻辑侧边提示
- 评估看板：
  - token
  - 决策耗时
  - 工具调用
  - 工具失败率
  - JSON 修复
  - Alpha
  - 三日均值和环比
- 推荐助手 trace 面板。
- 成本看板和 prompt 预览。

## 部署流程

生产代码仓库在 GitHub：

```text
https://github.com/lulu-cloud/stock_run_88.git
```

部署原则：

- GitHub 管代码。
- 服务器保留运行态数据。
- GitHub Actions 推送代码到服务器。
- systemd 管理后端服务。
- Nginx 反代前端/API。

服务器目录：

```text
/opt/stock_run_88
```

这些目录和文件属于运行态，不随 GitHub 覆盖：

```text
.env
.venv
data/
logs/
reports/
agent_memory/
frontend/node_modules/
frontend/dist/
```

GitHub Actions 流程：

```text
push main
    |
checkout
    |
scp 上传源码到 /tmp/stock_run_88_release
    |
rsync 到 /opt/stock_run_88
    |
保留 .env/.venv/data/logs/reports/agent_memory
    |
pip install -r requirements.txt
    |
npm ci && npm run build
    |
systemctl restart stock-run-api
```

生产服务：

```bash
sudo systemctl status stock-run-api
sudo systemctl restart stock-run-api
```

本地开发：

```bash
cd /home/xulu/stock_run_88
.venv/bin/python3 -c "from backend.db.schema import init_db; init_db().close()"
.venv/bin/python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev
```

## 环境变量

`.env` 不提交 GitHub，在服务器本地维护。

常用变量：

```bash
DEEPSEEK_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_API_BASE=https://api.telegram.org
MINIMAX_API_KEY=...
AGENT_MAX_CONCURRENCY=1
TELEGRAM_SHORT_TERM_TURNS=6
TELEGRAM_MEMORY_DISTILL_MIN_USER_MESSAGES=3
TELEGRAM_MEMORY_DISTILL_CONFIDENCE=0.72
TELEGRAM_LONG_TERM_MEMORY_MAX_PER_SCOPE=80
```

## 关键 API

```text
GET  /api/telegram/status
POST /api/telegram/polling/start
POST /api/telegram/polling/stop
POST /api/telegram/chat/test
POST /api/telegram/recommend
GET  /api/telegram/memory
GET  /api/telegram/memory/session
GET  /api/telegram/memory/distill/status
POST /api/telegram/memory/distill/run

GET  /api/agents
GET  /api/agent/{agent_id}
GET  /api/agent/{agent_id}/eval
GET  /api/agent/{agent_id}/cost
GET  /api/agent/{agent_id}/prompt-preview

GET  /api/market/overview
GET  /api/market/sector-strength
POST /api/strategy/search
```

## 数据库说明

核心表：

- `agent_info`
- `agent_position`
- `agent_order`
- `agent_trade_log`
- `agent_order_trace`
- `agent_daily_report`
- `agent_eval_metric`
- `agent_race_metric`
- `agent_shared_context`
- `telegram_user_profile`
- `telegram_watchlist`
- `telegram_recommend_feedback`
- `telegram_recommend_eval`
- `telegram_recommend_outcome`
- `telegram_recommend_cost`
- `telegram_conversation_message`
- `telegram_memory_item`
- `telegram_session_summary`
- `telegram_memory_distill_state`
- `shared_stock_report`

SQLite 使用 WAL 和 busy timeout，适合当前单用户/轻量服务规模。

## 当前限制

- 这是模拟交易系统，不是实盘交易系统。
- SQLite 对当前个人服务足够，但多人高并发建议迁移 PostgreSQL。
- 推荐结果仅供研究，不构成投资建议。
- 自动画像依赖 LLM 输出质量，已经有置信度过滤和冲突合并，但仍需要人工观察。
- 行情数据依赖本地更新任务，数据缺口会影响策略结果。

## 运维检查

查看生产状态：

```bash
ssh -i /home/xulu/ssh.pem ubuntu@43.131.254.210
cd /opt/stock_run_88
git rev-parse --short HEAD
systemctl is-active stock-run-api
curl -fsS http://127.0.0.1:8000/api/telegram/status
```

检查记忆表：

```bash
cd /opt/stock_run_88
.venv/bin/python3 - <<'PY'
from backend.db.schema import init_db
conn = init_db()
for table in ["telegram_memory_item", "telegram_session_summary", "telegram_memory_distill_state"]:
    print(table, len(conn.execute(f"PRAGMA table_info({table})").fetchall()))
conn.close()
PY
```

## 开发原则

- 交易计算由 Python 工具完成，LLM 只做推理与决策。
- Agent 输出必须结构化，并保留 trace。
- 推荐助手必须能 fallback，不能因为 ReAct JSON 失败而无回复。
- 生产数据不进 GitHub，部署只覆盖代码。
- 长期记忆必须有上限、可归档、可冲突合并，避免上下文无限膨胀。
