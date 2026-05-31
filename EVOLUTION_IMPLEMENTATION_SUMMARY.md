# A股 Agent 自我进化实现总结

更新时间：2026-05-16

## 1. 这次已经实现了哪些功能

### 1.1 交易 Agent 进化记忆

新增目录：

```text
agent_memory/{agent_id}/
├── trade_fact.md
├── trade_prefer.md
├── short_ring.md
└── snapshots/{trade_date}.json
```

含义：

- `trade_fact.md`：客观市场规律记忆，限制约 2200 字。
- `trade_prefer.md`：Agent 主观交易偏好，限制约 1375 字。
- `short_ring.md`：近 3 日极端行情短期记忆。
- `snapshots/{trade_date}.json`：每日复盘前冻结的记忆快照，保证当天决策认知统一。

当前实现是“确定性摘要 + 限容裁剪”，不是再额外调用 LLM 总结。优点是稳定、便宜、不会因为模型失败阻塞流水线。

### 1.2 自适应技能库

新增表：

- `agent_evolution_skill`
- `agent_evolution_event`

Agent3 默认技能：

| 技能ID | 技能名 | 初始置信度 |
|---|---|---:|
| `momentum_hunt` | 情绪周期龙头追涨 | 0.66 |
| `risk_exit` | 退潮风险压制与离场 | 0.60 |

Agent4 默认技能：

| 技能ID | 技能名 | 初始置信度 |
|---|---|---:|
| `balanced_factor` | 全因子均衡选股 | 0.62 |
| `position_rotate` | 非原子换仓与资金顺序控制 | 0.58 |

每日复盘后会基于最近订单状态更新：

- 近 10 笔同技能订单失败率。
- 技能置信度。
- 部分动态参数，例如单票仓位上限、止损参数。
- `evolution_record` 进化说明。

### 1.3 Agent 决策工具增强

新增 Agent 工具：

| 工具 | 作用 |
|---|---|
| `get_evolution_context(agent_id)` | 读取进化记忆、技能索引、上次进化结果 |
| `get_skill_params(agent_id, skill_id)` | 读取某个技能完整参数 |
| `get_strategy_param_schema(strategy_name)` | 查询选股策略支持哪些自定义敏感参数 |

原有 `search_stocks_by_strategy(strategy_name, params_json)` 已经支持自定义参数，这次把能力显式暴露给 Agent。

例如打板策略可以让 Agent 调整：

```json
{
  "min_limit_up_days": 2,
  "lookback_days": 8,
  "healthy_turnover_min": 1.2
}
```

### 1.4 订单和日报增加进化字段

`agent_order` 新增：

| 字段 | 作用 |
|---|---|
| `skill_id` | 订单使用的技能 |
| `skill_confidence` | 下单时技能置信度 |
| `failure_attribution` | 失败归因：market / timing / strategy 等 |
| `evolution_mark` | 进化标签，例如 `#情绪回暖#` |

`agent_daily_report` 新增：

| 字段 | 作用 |
|---|---|
| `factor_weight_log` | 当日因子权重记录 |
| `risk_adjust_log` | 风控和技能调整记录 |

### 1.5 5分钟 K 线复盘

新增：

```text
backend/evolution/minute_replay.py
```

逻辑：

- 只获取 Agent 当日相关股票的 5 分钟 K 线。
- 相关股票包括：
  - 当日订单股票。
  - 当日成交股票。
  - 当前持仓股票。
- 不会全市场拉取，避免浪费。
- 数据缓存到：

```text
data/minute/{trade_date}/{ts_code}_5m.csv
```

当前用途：

- 估算订单最早触发时间。
- 判断“先卖 A 再买 B”的资金顺序风险。
- 如果买入条件早于卖出条件触达，会标记为非原子换仓风险。

### 1.6 非原子换仓规则已写入 Agent Prompt

Agent Prompt 已明确：

- A 股换仓不是原子操作。
- 如果计划卖出 A 再买入 B，必须在理由里说明顺序风险。
- 如果买入条件先触达、卖出未成交，买入可能失败。

### 1.7 过期订单自动释放冻结资金

现有流水线已有：

- 每日复盘前调用 `_expire_stale_pending_orders`。
- 只要 pending 单的 `trade_date < 当前复盘日`，会自动：
  - 标记 `expired`
  - 写入失败原因
  - 释放买单冻结资金

撮合当天未成交的订单也会：

- 标记 `expired`
- 释放冻结资金
- 写入 `fail_reason`
- 写入 `failure_attribution`

注意：如果订单是未来交易日，比如当前库里的 `20260518` pending 单，它还不是历史过期单，不会被强制撤销。

### 1.8 Markdown 日报增强

`review.md` 现在包含：

- 可用资金
- 冻结资金
- 持仓市值
- 浮动盈亏
- 总资产
- 今日收益
- 累计收益
- 新生成条件单的技能 ID 和技能置信度
- 进化技能快照

### 1.9 Telegram 股票推荐反馈记录

新增表：

```text
telegram_recommend_feedback
```

已实现：

- 每次推荐结果落库。
- 记录：
  - 用户 chat_id
  - 查询语句
  - 推荐股票
  - 推荐策略
  - 推荐评分
  - 原始推荐 JSON
- 用户后续说：
  - “跌了”
  - “亏了”
  - “不喜欢”
  - “太激进”
  - “太保守”

会被识别成反馈并更新最近一次推荐记录。

其中：

- “太激进 / 太冒险 / 恐高”会把画像风险等级调低。
- “太保守 / 机会少”会把画像风险等级调高。

## 2. 每天完整流程现在是什么

### 2.1 数据更新阶段

默认数据拉取时间：

```text
18:00
```

流水线会检查本地日线数据是否新鲜。

如果数据不足，会等待重试，不直接跑 Agent，避免用旧数据决策。

### 2.2 Agent 日终复盘阶段

默认 Agent 复盘时间目前由数据库或配置控制，当前库里 Agent3 / Agent4 是：

```text
20:00
```

每日复盘顺序：

1. 取消旧 pending 单
   - 条件：`trade_date < 当前复盘日`
   - 买单释放冻结资金
   - 写入失败原因

2. 持仓按最新收盘价估值

3. 撮合当天 pending 单
   - `open_get_in=1`：
     - 买单：`open <= limit_price`，按开盘价成交
     - 卖单：`open >= limit_price`，按开盘价成交
   - 普通限价：
     - 当日 `low <= price <= high` 成交
   - 未成交：
     - 当日结束标记 `expired`
     - 买单释放冻结资金
     - 写入失败原因和失败归因

4. 再次估值

5. 冻结进化上下文
   - 读取 `trade_fact.md`
   - 读取 `trade_prefer.md`
   - 读取 `short_ring.md`
   - 读取技能库置信度
   - 写入当天 memory snapshot

6. 调用 LLM Agent 复盘
   - 输入资金、持仓、成交、近期挂单、进化上下文
   - Agent 自主调用工具
   - Agent 输出明日条件单

7. 校验订单价格
   - 必须在最新收盘价 ±10% 内
   - 否则要求 Agent 修复

8. 替换明日旧预操作单
   - 释放旧明日 pending 单冻结资金
   - 标记旧单 expired

9. 插入新条件单
   - 买单冻结资金
   - 卖单校验可卖数量
   - 保存 `skill_id / skill_confidence / evolution_mark`

10. 生成 `review.md`

11. 执行日终进化
    - 拉取相关股票 5 分钟 K 线
    - 复盘订单触发顺序
    - 判断非原子换仓风险
    - 更新技能置信度
    - 更新记忆文件
    - 写入 `agent_evolution_event`
    - 写入 `evolution_evolve.log`
    - 更新日报 `factor_weight_log / risk_adjust_log`

### 2.3 Telegram 推送阶段

默认推送时间由数据库或配置控制，当前库里 Agent3 / Agent4 是：

```text
21:00
```

推送读取的是日报和订单/成交数据。

当前重点：

- 每个 Agent 单独生成文案。
- 按 `agent_id + chat_id + trade_date` 控制推送。
- 避免串 Agent 和重复推送。

## 3. 股票推荐助手现在是什么状态

### 3.1 当前它不是完整交易 Agent

Telegram 股票推荐助手目前更准确地说是：

```text
自然语言选股 Bot + 用户画像 + 推荐反馈记录
```

它和 Agent3 / Agent4 的区别：

| 项目 | Agent3 / Agent4 | Telegram 推荐助手 |
|---|---|---|
| 是否管理账户资金 | 是 | 否 |
| 是否生成订单 | 是 | 否 |
| 是否参与每日撮合 | 是 | 否 |
| 是否有技能置信度库 | 交易 Agent 有 | 当前只记录推荐策略和反馈 |
| 是否有用户画像 | 否 | 有 |
| 是否能接收用户反馈 | 间接通过订单盈亏 | 是 |

### 3.2 推荐助手已实现的能力

已实现：

- 自然语言解析选股需求。
- 调用策略选股。
- 结合用户画像：
  - 风险等级
  - 周期偏好
  - 偏好板块
  - 排除板块
  - 推荐数量
- 推荐结果落库。
- 用户反馈落库。
- 基于“太激进/太保守”调整用户画像。

### 3.3 推荐助手还没完全实现的 Agent 化能力

还没完全做：

- 独立的 `telegram_recommend_skill` 技能库。
- 每类推荐策略的近 N 次推荐胜率统计。
- 推荐后自动跟踪未来 1 / 3 / 5 日收益。
- 根据推荐结果涨跌自动更新推荐技能置信度。
- 对用户明确解释：
  - 这次推荐依托哪类规律。
  - 当前该规律胜率是多少。
  - 最近为什么增强或削弱。
- 拟人化话术自适应：
  - 精简型用户少说逻辑。
  - 深度型用户展开政策、板块、资金流。

也就是说，它现在有“反馈记账”和“画像调整”，但还没有完整 Hermes 式“推荐技能自进化”。

## 4. 当前代码入口

核心新增文件：

```text
backend/evolution/memory.py
backend/evolution/skills.py
backend/evolution/minute_replay.py
backend/evolution/engine.py
```

核心改动文件：

```text
backend/db/schema.py
backend/db/models.py
backend/db/repository.py
backend/agents/base.py
backend/agents/tools.py
backend/agents/llm_agent.py
backend/pipeline/daily_pipeline.py
backend/pipeline/order_executor.py
backend/logs/report_generator.py
backend/telegram/recommender.py
```

## 5. 当前验证结果

已经执行：

```bash
python -m compileall backend scripts
npm --prefix frontend run build
```

结果：

- 后端编译通过。
- 前端构建通过。
- 数据库迁移 smoke 通过。
- 进化上下文 smoke 通过。
- Telegram feedback smoke 通过。
- 后端接口 `/api/agent/list`、`/api/agent/3` 返回正常。

当前开发服务：

```text
前端：http://localhost:5173/
后端：http://localhost:18000/
API 文档：http://localhost:18000/docs
```

## 6. 建议下一步

优先级从高到低：

1. 给 Telegram 推荐助手补完整推荐技能库。
2. 给推荐记录增加 1 / 3 / 5 日收益跟踪任务。
3. 前端增加“进化面板”：
   - 技能置信度曲线
   - 最近失败归因
   - 记忆文件预览
   - 分钟复盘结果
4. Telegram 日报推送增加进化表格：
   - 可用资金
   - 冻结资金
   - 总资产
   - 浮盈浮亏
   - 技能置信度变化
   - 风控调整说明
5. 将当前确定性记忆更新升级为可选 LLM 总结，但必须放在流水线末尾，失败不能阻塞交易。

