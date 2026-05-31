# A股多Agent智能投顾系统 — 系统架构与功能文档

> 文档日期: 2026-05-15
> 项目路径: `/home/xulu/stock_run_88/`

---

## 一、Agent 体系总览

系统包含两类 Agent，运行在两个不同的通道：

| 类型 | Agent | 通道 | 职责 |
|------|-------|------|------|
| 交易 Agent #3 | 追高打板Agent | 后端调度器 | 绑定 momentum 策略，追涨停龙头 |
| 交易 Agent #4 | 自主决策Agent | 后端调度器 | 完全自主，综合政策/基本面/技术面决策 |
| 交易 Agent #5 | 深度推理Agent | 后端调度器 | max reasoning_effort，深度推理模式 |
| 股票助手 | @xiaoma_make_big_money_bot | Telegram Bot | 对话式股票推荐/分析/战绩查询 |

---

## 二、交易 Agent 详解

### 2.1 Agent 配置

每个交易 Agent 可独立配置：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| status | active / paused / disabled | active |
| schedule_enabled | 是否开启每日定时复盘 | false |
| review_time | LLM 复盘分析时间 | 21:00 |
| push_time | Telegram 日报推送时间 | 23:00 |
| initial_capital | 初始资金 | 150,000 元 |
| strategy_ids | 绑定策略 (momentum/trend/macd 等) | 按类型不同 |
| risk_config.max_tool_turns | LLM 最大工具调用轮数 | 8 |
| risk_config.reasoning_effort | 推理深度 (high/max) | high |

### 2.2 Agent 决策流程 (每日自动化)

```
18:00  自动拉取 baostock 指数 + 3400+ 个股增量数据 (后台线程)
21:00  检测数据就绪 → 若未就绪按 10/20/30/30/60min 退避重试 → 次日8点弃

复盘开始 (run_daily_pipeline):
  1. 市价估值 (mark_to_market) — 更新持仓市值
  2. 条件单撮合 — 用今日收盘价判定前一天生成的条件单是否触发
  3. LLM ReAct 分析 — Agent 自主调用6+工具：
     a. get_market_overview — 大盘走势
     b. get_policy_signals — 政策信号
     c. compute_sector_heat_tool / get_market_strength_sectors — 板块热度
     d. search_stocks_by_strategy — 策略筛选 (13个策略可选)
     e. get_stock_kline — 个股K线+均线
     f. get_company_business — 公司业务
     g. calculate_price_by_pct — 计算目标挂单价
     h. validate_order_price_limit — 校验涨跌停
     i. get_recent_order_history — 查看历史订单成败
  4. 解析 LLM 输出的 JSON 交易计划 → 写入 agent_order 表
  5. 生成每日复盘报告 MD
  6. 保存 agent_daily_report (净值快照)
  7. push_time 到 → Telegram 推送日报

第二天 21:00 再次触发 → 撮合前一天条件单 → 循环
```

### 2.3 Agent 记忆与状态

Agent 的状态持久化在 SQLite 中：

| 表 | 存储内容 | 说明 |
|----|---------|------|
| agent_info | 资金、策略绑定、风控配置、状态 | 核心配置 |
| agent_position | 持仓股票、成本、现价、浮动盈亏 | 逐日 mark_to_market 更新 |
| agent_order | 条件单 (pending/triggered/filled/cancelled) | 含挂单价、开盘抢入标志、冻结资金 |
| agent_trade_log | 已成交记录 | 含佣金、印花税 |
| agent_daily_report | 每日净值快照 + 收益率 | 用于画净值曲线 |
| agent_schedule | 定时配置 + 重试状态 | review_time/push_time/retry_count |

此外，每次复盘生成两类文件：
- `logs/{date}/{agent_name}/thinking.log` — LLM 完整交互日志 (工具调用+返回+分析)
- `reports/{date}/{agent_name}/report.md` — 人类可读的当日复盘报告

### 2.4 Agent 快照与回滚

每次复盘前自动快照 (`snapshot_agent_state`)，保存：现金、持仓、全部订单、订单/成交/日报最大 ID。
若复盘流程中任何步骤抛异常 → 自动回滚到快照点，删除中间产生的半完成数据。

---

## 三、股票助手 Agent (Telegram Bot)

### 3.1 Bot 基本信息

- Bot 名称: `@xiaoma_make_big_money_bot`
- 连接方式: **Long Polling** (不需要 webhook，Bot 主动轮询 Telegram 服务器)
- 启动方式: 后端启动时自动拉起，环境变量 `TELEGRAM_POLLING_ENABLED=1`

### 3.2 支持的命令

| 命令 | 功能 | 示例 |
|------|------|------|
| `/start` | 初始化对话 | `/start` |
| `/help` | 查看帮助 | `/help` |
| `/bind <agent_id>` | 绑定交易 Agent 到当前 Telegram 用户 | `/bind 3` |
| `/status <agent_id>` | 查询交易 Agent 实时战绩 | `/status 3` |
| `/sim <sim_id>` | 查询模拟任务战绩 | `/sim 12` |
| `/recommend <NL>` | 自然语言股票推荐 (融合用户偏好) | `/recommend 帮我推荐3只强势科技股` |
| `/analyze <ts_code>` | 个股深度分析 | `/analyze 600000.SH` |
| `/compare A B` | 多股对比 | `/compare 600000.SH 600036.SH` |
| `/profile set` | 设置投资偏好 | `/profile set 风险=中等 周期=短线 板块=AI,半导体` |
| `/profile` | 查看当前偏好 | `/profile` |
| `/watch add <code>` | 添加到自选 | `/watch add 600000.SH` |
| `/watch list` | 查看自选列表 | `/watch list` |
| `/watch remove <code>` | 移除自选 | `/watch remove 600000.SH` |
| `/daily on/off` | 每日推送开关 | `/daily on` |
| 直接发自然语言 | 等同于 /recommend | `找三只新能源龙头` |

### 3.3 股票推荐算法

`/recommend` 的推荐流程：

1. 解析用户自然语言 → 提取关键词（板块/风格/数量）
2. 读取用户 profile（风险偏好/周期/板块偏好/自选股）
3. 调用交易 Agent 的 13 个策略进行选股
4. 推荐结果融合用户偏好权重
5. 可选：调用 `get_agent_performance` / `get_simulation_performance` 获取战绩参考
6. 返回推荐列表（代码/名称/评分/理由）

### 3.4 Telegram 用户记忆

每个 Telegram 用户绑定到 `telegram_binding` 表 (chat_id ↔ agent_id)。
用户偏好存储在 `telegram_profile` 表：

| 字段 | 说明 |
|------|------|
| risk | 风险偏好: 低/中/高 |
| cycle | 操作周期: 短线/中线/长线 |
| sectors | 关注的板块 |
| watchlist | 自选股列表 |

---

## 四、交易 Agent vs 股票助手 Agent 的对比

| 维度 | 交易 Agent | 股票助手 Agent |
|------|-----------|---------------|
| 运行方式 | 后端调度器自动触发 (每日21:00) | Telegram Bot 按需响应 |
| 决策模式 | ReAct 工具调用 → 生成条件单 | 策略筛选 + 用户偏好融合 → 推荐列表 |
| 执行动作 | 生成真实条件单 → 次日撮合成交 | 仅推荐，不执行交易 |
| 资金 | 真金白银 150,000/Agent | 无资金 |
| 工具集 | 9个工具 (含价格计算/校验) | 策略选股 + 战绩查询 + 个股分析 |
| 输出 | 条件单写入 DB + report MD + Telegram 推送 | Telegram 消息回复 |
| 状态持久化 | agent_info/position/order/trade_log/daily_report | telegram_binding/profile/watchlist |
| LLM 模式 | thinking=disabled (兼容 tool call) | 单次调用 |

---

## 五、模拟交易

模拟交易与实盘使用相同的 LLM Agent 流程，但在历史数据上回放：

- 前端 `Simulation.vue` 配置多 Agent (名称/策略/资金) + 日期范围
- 引擎逐日循环：预加载数据 → 时间隔离工具 → ReAct 分析 → 收盘价撮合
- 每完成一天自动落库中间结果 (equity_curve/trades/decisions)
- 结果页：净值对比图 + 绩效表 + 按天展开的 LLM 完整分析

---

## 六、关键数据流

```
baostock (每日增量)
  → data/daily/{ts_code}_daily.csv (3400+ 文件)
  → data/index/000001.SH_daily.csv
  → compute_mas() / compute_macd() / compute_limit_status()
  → 13 个策略 filter()
  → Agent ReAct 工具调用
  → LLM 分析 → JSON 交易计划
  → agent_order 表 (条件单)
  → 次日撮合 → agent_trade_log
  → agent_daily_report (净值)
  → Telegram 推送
```

政策数据独立流程：
```
工信部/发改委/财政部 网页 → 爬虫抓取 → data/policy_docs/{部门}/*.md → extract_policy_signals() → Agent 工具
```

---

## 七、策略清单 (13个)

| 策略 | lookback | 说明 |
|------|----------|------|
| momentum | 10 | 龙头打板，连板+换手率阶段分析 |
| yang_three_yin | 20 | 一阳夹三阴K线形态 |
| ma_cross | 30 | MA5上穿MA20金叉 |
| volume_pullback | 30 | 缩量回踩MA5/MA10 |
| kline_pullback | 30 | K线回踩MA20 |
| ma_pullback | 30 | 20/60双均线回调 |
| trend | 60 | 多波动量趋势 |
| consolidation | 60 | 横盘突破 |
| box_range | 60 | 箱体震荡波段 |
| sentiment_cycle | 60 | 换手率情绪周期逆势布局 |
| macd | 120 | MACD金叉 |
| bottom_reversal | 120 | 底部放量反转 |
| uptrend | 250 | 长期MA多头排列慢牛 |

---

## 八、当前 Agent 状态

| Agent | 状态 | schedule | 策略 | 最近复盘 |
|-------|------|----------|------|---------|
| #3 追高打板Agent | active | enabled, review=21:00 | momentum | logs/20260513/agent_chaser/ |
| #4 自主决策Agent | active | enabled, review=21:00 | 自主 | logs/20260513/agent_autonomous/ |
| #5 深度推理Agent | disabled | disabled | 自主 | 未启用 |

---

## 九、端口与服务

| 服务 | 端口 | 说明 |
|------|------|------|
| FastAPI 后端 | 18000 | API + 调度器 + 政策爬虫 + Telegram polling |
| Vite 前端 | 5173 | Vue3 SPA |
| FRP 隧道 | — | 阿里云 47.116.11.223 端口转发 |

后台线程 (随 FastAPI 启动自动运行):
- Agent 调度器: 每 5 分钟检查，18:00+ 自动拉数据，21:00+ 运行复盘
- 政策爬虫调度器: 每日 09:20 后抓取一次
- Telegram Long Polling: 持续轮询 Bot 消息
