# A股多Agent智能投顾系统 — 完成度分析

> 评估日期: 2026-05-11
> 目标架构: 多 Agent 模拟交易 + 股票推荐 Agent (Telegram) + 策略选股 + 板块热度 + 政策监控 + ML/DL 量化

---

## 一、策略选股系统 — 完成度 90%

### 已完成
- **13 个策略**全部注册可用，覆盖短线/中线/长线

| 策略 | lookback | 说明 | 状态 |
|------|----------|------|------|
| `momentum` | 10 | 龙头打板，连板+换手率分析 | ✅ |
| `yang_three_yin` | 20 | 一阳夹三阴K线形态 | ✅ |
| `ma_cross` | 30 | MA5上穿MA20金叉+量确认 | ✅ |
| `volume_pullback` | 30 | 缩量回踩MA5/MA10 | ✅ |
| `kline_pullback` | 30 | K线回踩MA20 | ✅ |
| `ma_pullback` | 30 | 20/60双均线回调 | ✅ |
| `trend` | 60 | 多波动量趋势 | ✅ |
| `consolidation` | 60 | 横盘突破 | ✅ |
| `box_range` | 60 | 箱体震荡波段 | ✅ |
| `sentiment_cycle` | 60 | 情绪周期逆势布局 | ✅ |
| `macd` | 120 | MACD金叉 | ✅ |
| `bottom_reversal` | 120 | 底部放量反转 | ✅ |
| `uptrend` | 250 | 长期多头排列慢牛 | ✅ |

- 所有策略通过 `StrategyRegistry.create(name)` 调用
- AI Chat 页面支持自然语言选股（LLM 解析 → 策略筛选）

### 缺失
- 无明显缺失。如需更多策略（缠论/波浪/布林带等）可随时扩展

---

## 二、板块热度统计 — 完成度 60%

### 已完成
- **后端**: `compute_market_strength_by_sector()` 按近N日涨幅/涨停/换手统计
- **API**: `GET /api/market/sector-strength` 返回 strong/weak 板块（5分钟缓存）
- **前端**: Dashboard 显示近3日强势/弱势板块面板
- **数据源**: `stock_industry_cache.csv` (baostock 证监会行业分类，5500+条)
- **关键词匹配**: `backend/search_agent/sector.py` 50+ 关键词→板块映射

### 进行中
- **批量公司业务搜索** (`scripts/batch_company_search.py`): 4699只全市场个股，LLM生成业务MD，用于丰富板块标注。用户正在分批跑。

### 缺失
- 板块标注目前以 baostock 行业名称为主（如"计算机、通信和其他电子设备制造业"），MD文件覆盖量不足
- 批量搜索完成后，板块标签会从长行业名替换为"AI算力/半导体/新能源"等短标签，粒度更好

---

## 三、政策新闻爬虫 — 完成度 70%

### 已完成
- **爬虫**: `backend/policy/crawler.py` 支持 工信部/发改委/财政部 三个来源
- **深层抓取**: `fetch_content()` 可抓取政策页面正文内容
- **API**: `/api/policy/*` 列表/信号/内容/爬取触发
- **前端**: Dashboard 政策面板 + 部门Tab切换 + 点击展开正文
- **信号提取**: `extract_policy_signals()` 关键词匹配产业政策方向

### 缺失
- 爬虫需要手动触发（`POST /api/policy/crawl`），没有定时自动爬取
- 当前缓存的政策文件数量有限（约30个）
- 未接入更多政府网站（央行、证监会、科技部等）

---

## 四、LLM Agent 模拟交易 — 完成度 75%

### 已完成
- **模拟引擎**: `backend/simulation/sim_engine.py` — 预加载数据 + 逐日循环 + 订单撮合
- **ReAct Agent**: `backend/agents/llm_agent.py` — 手动 ReAct 循环，6个工具，支持 tool call
- **时间隔离**: `backend/simulation/sim_tools.py` — 6个工具全部截断到 `trade_date`
- **订单撮合**: T+1/涨跌停/费率全支持，代码自动补全 .SH/.SZ
- **中间落库**: 每完成一天自动保存 decisions/trades/equity 到 DB
- **日志/报告**: 每日写 `.log` (LLM交互) 和 `.md` (复盘报告)
- **前端**: Simulation.vue — 配置面板 + 净值对比 + 指标表 + 决策回放(展开LLM分析)
- **DB**: `simulation_task` 表存储完整结果

### 缺失
- 模拟任务目前需手动从 UI 启动，没有定时自动运行
- 进度条更新粒度粗（只显示百分比，不显示当前处理到哪天）
- 多 Agent 并行模拟速度较慢（受限于单线程 LLM 调用）

---

## 五、实盘 Agent 自动交易 — 完成度 50%

### 已完成
- **Agent CRUD**: `backend/agents/factory.py` — 创建/删除/重命名/配置风控+策略
- **调度系统**: `backend/pipeline/daily_pipeline.py` — `run_due_agents()` 整合了：
  - 交易日判定 (`chinese_calendar`)
  - 数据就绪检查 + 退避重试 (10/20/30/30/60min + 次日8点)
  - 快照+回滚机制
  - 复盘→撮合→生成条件单→写报告
- **内置调度器**: `backend/main.py` 启动时自动拉起后台线程，每5分钟检查
- **前端**: Agent 详情页 — 状态(启用/暂停/禁用) + 复盘开关 + 复盘时间 + 推送时间
- **DB**: `agent_info`, `agent_position`, `agent_order`, `agent_trade_log`, `agent_daily_report`, `agent_schedule`

### 缺失
- **三个 Agent 的 schedule_enabled=0**，还没开启每日复盘
- 实盘流水线从未真正运行过（缺少数据就绪的实际验证）
- 条件单撮合逻辑 (`order_executor.py`) 未在实盘环境测试
- 没有订单执行后的通知机制（Telegram 推送已具备但未配置 Bot Token）

---

## 六、股票推荐 Agent (Telegram) — 完成度 30%

### 已完成
- **Telegram 网关**: `backend/telegram/gateway.py` — 发送消息/绑定chat/构建日报/推送
- **API**: `/api/telegram/*` — 绑定/查询/推送测试
- **DB**: `telegram_binding` 表
- **推送集成**: `run_due_agents()` 完成后如到达 push_time 自动推送

### 缺失 (核心功能)
- **没有对话式推荐 Agent**: Telegram 只能被动推送日报，不能接收用户消息和回复
- **没有工具获取实盘/模拟战绩**: LLM Agent 没有 `get_agent_performance` 工具来查询历史战绩
- **没有 Telegram Webhook**: 无法接收用户发送的 `/recommend` `/status` 等指令
- **推荐算法未实现**: 没有把策略选股包装成 Telegram 对话推荐
- **TELEGRAM_BOT_TOKEN 未配置**: 环境变量为空

### 需要实现的关键功能
1. Telegram Webhook 接收用户消息
2. 对话 Agent: 理解用户意图 → 调用策略选股 → 返回推荐结果
3. 工具: `get_agent_performance(agent_id)` — 返回该 Agent 的累计收益/胜率/夏普/近期交易
4. 工具: `get_simulation_performance(sim_id)` — 返回模拟任务的战绩
5. 自然语言推荐: "帮我推荐3只强势科技股" → 策略筛选 → Telegram 回复

---

## 七、ML/DL 量化接口 — 完成度 10%

### 已完成
- **接口定义**: `backend/quant/base.py` — `BaseQuantModel(ABC)` + `QuantPrediction`
- **注册中心**: `backend/quant/registry.py` — `QuantModelRegistry`
- **集成预留**: `SimAgentConfig` 中有 `quant_model_name` 和 `quant_weight` 字段

### 缺失
- **零个实际模型**: LSTM/Transformer/XGBoost 等均未实现
- **训练流程**: 没有特征工程、数据预处理、模型训练脚本
- **推理集成**: 模拟引擎和实盘 Agent 都没有调用 ML 预测的代码
- **回测验证**: 没有 ML 模型的回测评估框架

### 下一步
- 先实现一个简单的 `DummyQuantModel` 验证接口可用
- 再实现一个基于 XGBoost 的趋势预测模型（特征: MA偏离/换手/量比/MACD）
- 集成到模拟引擎：`SimAgent.decide()` 融合策略信号 + ML 信号

---

## 八、前端功能矩阵

| 页面 | 核心功能 | 状态 |
|------|---------|------|
| Dashboard | Agent卡片、上证K线、净值趋势、板块强弱、政策动态(部门Tab)、盈亏日历 | ✅ 90% |
| AI Chat | 策略按钮、NL流式选股、结果表格、K线弹窗、业务详情、Agent日报 | ✅ 85% |
| K线分析 | 搜索+K线(MA/量)、多K线保存Tab、业务浮窗、Agent持仓参考 | ✅ 85% |
| 策略回测 | 策略/周期选择、指标卡、净值曲线、交易表、BS点弹窗、回放日志、历史任务 | ✅ 90% |
| 模拟交易 | Agent配置面板、净值对比、绩效表、决策回放(展开LLM分析)、历史列表 | ✅ 85% |
| Agent详情 | 持仓/交易/条件单/K线链、状态切换、调度配置(复盘时间/推送时间) | ✅ 80% |
| Telegram配置 | 无前端页面 | ❌ 0% |

---

## 九、总结：距离愿景还差什么

| 模块 | 完成度 | 核心缺失 |
|------|--------|---------|
| 策略选股 | 90% | 基本完整 |
| 板块热度 | 60% | 批量MD待跑完 |
| 政策爬虫 | 70% | 定时自动爬取 |
| 模拟交易 | 75% | 速度优化 |
| 实盘Agent | 50% | 开关打开+首次实跑验证 |
| Telegram推荐 | 30% | Webhook+对话Agent+战绩工具 |
| ML/DL量化 | 10% | 零模型实现 |

### 优先级排序

1. **P0**: 把3个Agent的 schedule 开关打开，跑一次实盘验证
2. **P1**: Telegram Webhook + 对话推荐 + `get_agent_performance` 工具
3. **P1**: 完成批量公司业务搜索（正在跑）
4. **P2**: 实现1个 XGBoost 量化模型并集成到模拟引擎
5. **P2**: 政策爬虫定时自动运行
6. **P3**: 模拟交易速度优化（并行 LLM 调用）
