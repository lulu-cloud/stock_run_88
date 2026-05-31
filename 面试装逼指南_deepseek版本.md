# Agent 开发工程师面试装逼指南 — 2026-05-27 更新版

> 基于 A 股多 Agent 智能投顾模拟交易系统项目
> 适用岗位：Agent 开发工程师 / LLM Application Engineer
> 核心叙事：Agent 工程化落地 + 评估体系(Harness) + 自我进化 + 多 Agent 协调

---

## 零、面试开场白（30 秒版本）

> "我做的是一个 A 股多 Agent 智能投顾系统。核心不是调 API 写 prompt，而是把 LLM Agent 和真实 A 股交易规则做了工程化缝合。系统有 3 个独立交易 Agent，每个有自己的技能体系、资金账户、进化记忆和风格提示词。我搭了三个关键闭环：**评估闭环**（订单 trace + 20+ 维评价指标 + 成交概率预估算）、**进化闭环**（技能 EMA 长期置信度 + 因子权重历史学习 + 分层记忆 + 体系文档反思）、**协调闭环**（共享市场研判 + 跨 Agent 冲突检测）。整个链路跑通了：每日行情更新 → Agent ReAct 决策 → 条件单撮合 → 复盘评估 → 记忆沉淀 → 下次 prompt 注入。"

---

## 一、项目核心亮点（5 个点，面试时挑 2-3 个展开讲）

### 亮点 1：多 Agent 差异化协作 + 跨 Agent 协调机制（最新升级）

很多人的"多 Agent"项目就是同一个 prompt 改名字。我这里的差异在于：

**差异化层**：
- **追高打板 Agent**：因子权重偏向情绪(50%)和量价技术(25%)，初始技能 `momentum_hunt`(情绪周期龙头追涨)和 `risk_exit`(退潮风险压制)。赛马标签"情绪进攻型"。
- **自主决策 Agent**：因子权重更均衡（政策/技术/资金/情绪各 ~25%），技能体系 `balanced_factor`(全因子均衡选股)和 `position_rotate`(非原子换仓)。赛马标签"均衡观察型"。
- 从**工具白名单 → 优先策略绑定 → 技能置信度初始化 → 因子权重 → 风格提示词**全链路不同。

**协调层（这是关键加分项）**：

最近专门加了一套跨 Agent 协调基础设施：

- **共享市场研判表**：新增 `agent_shared_context` 表。每个 Agent 复盘完成后，自动提取其市场分析中的多空判断（通过关键词打分推断 risk-on/risk-off/range-bound），记录置信度评分，写入共享表。下一次其他 Agent 跑复盘时，会在 prompt 中注入"同伴 Agent 当日共享研判"模块。Agent 可以看到同伴对市场的判断但不会盲从 —— prompt 明确要求"若观点冲突，必须在风险评估中说明你的差异化理由"。

- **跨 Agent 冲突检测**：所有 Agent 订单入库后，`_record_cross_agent_order_conflicts` 扫描同一交易日同一标的的全部 pending 订单。如果发现 Agent A 买而 Agent B 卖同一只票，自动写入 `agent_order_trace`（事件类型 `cross_agent_conflict`），包含双方 order id、方向、价格的完整 payload。策略是 warn_only —— 不强制拦截，但前端 trace 面板可见冲突标记。

**面试时可以补一刀**：这个设计体现了一个核心洞察 —— 多 Agent 不应该强制 Consensus（因为交易本身就需要分歧来产生价格发现），但必须让每个 Agent 知道自己和同伴正在反向操作，并且让运营者事后能看到冲突全景。

### 亮点 2：A 股交易规则的完整工程化封装

这个系统的撮合引擎不是简单的"价格到了就成交"。我封装了完整的 A 股规则链：

- **T+1 制度**：`can_trade_today` 精确判断买入日和交易日关系，`get_available_shares` 计算真正可卖的股数。
- **涨跌停校验**：一字涨停/跌停禁止买卖（`is_one_side_limit` 四个价格相等判断）。
- **资金冻结-释放机制**：买单一创建就冻结 `price * quantity + fee`，成交后释放差额，过期全额退还现金。卖单校验可卖股数和 T+1。
- **开盘抢入 `open_get_in`**：买单开盘价 ≤ 限价按开盘价成交，卖单开盘价 ≥ 限价按开盘价成交，之后 fallback 到普通限价撮合。
- **快照 + 回滚**：每个 Agent 复盘前做完整数据库快照（`snapshot_agent_state`），记录 max_order_id / max_trade_id / max_report_id 三个水位线。复盘过程中任何异常触发 `rollback_agent_state`，按水位线精确删除新增数据，恢复现金和持仓。
- **数据就绪门槛**：主板/中小板 CSV 覆盖率达到 95% 才触发复盘，未就绪走退避重试链（10→20→30→30→60 分钟→次日 8 点→放弃）。

**面试装逼点**："这不是 demo 级别的撮合，而是做到了生产级资金安全所需的冻结-释放-回滚闭环。如果接入真实券商接口，这套逻辑基本可以直接平移。"

### 亮点 3：ReAct 决策闭环 + Harness 评估体系（含 LLM 稳定性加固）

这是面试时最能拉开差距的部分。

**ReAct 决策层**：
- 手写完整的 ReAct 循环（`run_react_agent_decision`），没有用 LangChain 的 create_agent，因为 DeepSeek 的 reasoning_content 在多轮 tool call 回传时会出问题。自己控制 messages 数组。
- JSON 解析做了多层容错：先匹配 ```json 代码块，再匹配裸 ``` 块，最后从末尾反搜 `{` 做 best-effort 解析。
- 价格校验失败自动触发修复轮：非法挂单价触发新的 ReAct 子会话，最多 3 轮修复。

**LLM 稳定性加固（最新升级）**：

这是从实际踩坑中总结出来的工程化改造：

- **指数退避重试**：`_invoke_llm_with_retry` 对每次 LLM 调用做 3 次指数退避重试（1s / 2s / 4s），429/502 等临时性故障不穿透到上层。重试次数记录在 trace 的 `attempts` 字段中，可审计 API 质量。
- **熔断器（Circuit Breaker）**：在 ReAct 主循环中追踪 `empty_rounds` —— LLM 连续 3 轮既无 tool_calls 也无实质内容输出时，触发 `quality_short_circuit` 事件并提前终止。避免 LLM "发呆"消耗 token 和延迟。
- **非法工具调用追踪**：LLM 返回的 `invalid_tool_calls`（如参数格式错误导致的调用）被记录在 trace 中，包含数量和内容 preview。这比"工具调用失败"更细粒度 —— 区分了"工具执行失败"和"工具调用格式都错了"。

**Harness 评估层**：
- **订单 trace 系统**：每笔订单 `created` → `matched` → `filled`/`expired`/`stale_expired`/`replaced`/`cross_agent_conflict` 全生命周期可追溯。
- **决策批次 `decision_batch_id`**：同一轮复盘产生的所有买卖订单共享 batch_id，批次级别记录订单数、买卖比、平均预估成交概率。
- **20+ 维度的 Agent 评价指标**：覆盖收益（Alpha/超额收益）、风险（VaR/CVaR/波动率）、质量（成交率/过期率/开盘抢入成功率）、工程（token/工具失败/JSON 修复/LLM 重试次数）、进化（记忆压缩/体系文档更新/反思触发）五个维度。
- **订单质量的前置指标**：下单前就估算 `fill_probability`（基于近 60 个交易日历史区间触达概率）和 `price_aggressiveness`（挂单价相对收盘价偏离百分比），同时入库、展示前端、注入下一次 LLM prompt。

**面试金句**："LLM 的不可预测性不是靠更好的 prompt 解决的，而是靠 Harness 层的多重兜底——指数退避重试是第一道，熔断短路是第二道，价格校验是第三道，成交概率预估是第四道，失败归因反哺是第五道。每一道都在缩小 LLM 的不确定性半径。我统计过，加上这些防护后，系统的'因 LLM 输出问题导致流水线中断'的概率从早期版本的约 15% 降到了接近 0。"

### 亮点 4：自我进化体系全面升级（类 Hermes 架构 + 三项关键升级）

这是面试时最能引发追问的点。

我实现的进化体系是 **分层记忆 + 技能 EMA 置信度 + 因子权重历史学习 + 体系文档反思** 四位一体：

**1. 分层记忆（frozen snapshot 模式）**：
- `trade_fact`：客观市场规律，上限 2200 字符
- `trade_prefer`：主观交易偏好，上限 1375 字符
- `short_ring`：近 3 日极端行情缓冲区，上限 1800 字符
- 每日复盘前冻结快照，复盘后更新。超限用 LLM 本身压缩，写入 `memory_compression_audit` 审计表。

**2. 技能置信度 —— EMA 长期+短期双轨制（最新升级）**：

这是进化系统最有技术含量的升级。之前的置信度更新只基于近 10 笔订单的短期失败率，对一个技能的好坏、好是偶然还是趋势，缺乏长期视角。我加了两层改进：

- **长期 EMA 失败率**：`_ema_fail_rate` 对整个订单历史（最多 80 笔）计算指数移动平均失败率（alpha=0.18），越近的订单权重越大，但远期的历史表现也有平滑贡献。这解决了"短期小样本波动"问题——一个技能可能近 3 笔全失败，但历史上 50 笔表现稳定，EMA 就会缓冲这种短期的剧烈波动。

- **三因子融合置信度**：新的置信度公式是 `new_conf = 0.55 * old_conf + 0.25 * recent_win_rate + 0.20 * ema_win_rate`。0.55 的历史惯性保证不剧烈震荡，0.25 的近期反馈保证能快速响应变化，0.20 的 EMA 长期信号保证不被短期噪音带着跑。EMA 值本身也存储在技能的 `dynamic_params.skill_ema` 里，下次更新时继续递推。

- **参数自调整逻辑不变**：失败率 ≥ 40% 收紧仓位上限（×0.85）和止损线（×0.9）；≤ 15% 且样本 ≥ 5 则放宽（×1.05）。

**3. 因子权重从硬编码升级为历史学习（最新升级）**：

之前的因子权重（情绪/技术/资金/政策各自占比）是基于 Agent 名称的硬编码（"追高"→情绪 0.50，"自主"→均衡 0.25）。这显然不够"进化"。

新增 `_learn_factor_weights_from_history` 函数：读取最近 20 个交易日的 `daily_return` 和历史 `factor_weight_log`，计算每个因子维度的加权收益贡献。正向贡献最大的因子获得 +0.06 的倾斜，负向贡献最大的因子获得 -0.06 的惩罚。学习后的权重和风格先验融合后再过 `_normalize_weights` 归一化。

**面试时可以强调**：这个学习是保守的（tilt 范围 ±0.06），不会因为短期小样本剧烈转向，但它确实让因子权重从"拍脑袋"变成了"数据驱动"。权重来源会写入 `_meta.source` 字段（`history_fit_20d` vs `style_default`），可审计可回滚。

**4. 交易体系文档反思 —— 更细粒度的触发 + 第五章因子权重建议（最新升级）**：

反思触发条件从原来的 3 种（周度/月度/事件）扩展到 6 种：
- **原有**：连续 5 个交易日完成 → 周度反思；月末窗口 → 月度反思；近 10 笔失败率 ≥ 40% → 事件反思
- **新增**：连续 3 笔同方向订单过期/取消 → 价格与执行条件反思；单日回撤超过 5% → 风控事件反思；技能置信度单日变化超过 0.20 → 技能突变事件反思

反思输出从四章升级为五章，新增**第五章"因子权重调整建议"**。`_suggest_factor_weights` 函数回看 20 日的历史因子权重和收益数据，标识正贡献和负贡献最大的因子维度，写入反思文档。这让 Agent 的周度反思不再是泛泛的"最近应该保守"，而是有具体数据的"政策因子的历史正贡献最高，建议维持或上调其权重"。

**面试金句**："进化的核心不是'让 LLM 自己写 prompt'，而是'用结构化数据反过来调整 Agent 的行为参数'。我把这个闭环拆成了四个确定性环节：数据收集（order trace）→ 归因（failure_attribution）→ 参数调整（EMA 置信度 + 因子权重学习）→ 文档化（五章反思 + factor_weight_suggestion）→ 注入下一次 prompt。每一步都是可审计、可回滚、可解释的。这比那些让 LLM 自己改自己 prompt 的'玄学进化'靠谱得多。"

### 亮点 5：新工具补齐 —— 组合风险感知

之前 Agent 只能看个股 K 线和持仓数量，缺乏对自己组合整体风险的感知。最近加了两把新工具：

- **`get_portfolio_risk_metrics`**：实时计算当前组合的集中度（最大单票权重）、行业/板块暴露、年化波动率、VaR(95%)，并在触发阈值时生成 warnings（如"单票集中度偏高: 000001.SZ 28.5%"、"行业/板块暴露偏高: 银行 52.1%"）。数据来源于持仓表 + stock_basic 板块信息 + 历史涨跌幅的滚动窗口。

- **`get_correlation_info`**：输入候选买入标的列表，自动加入当前持仓，计算近 N 日涨跌幅的相关性矩阵。高相关配对（corr ≥ 0.75）会生成警告："存在高相关标的，买入前应降低仓位或选择替代方向"。这直接解决了 Agent 常见的"同时买入三只银行股以为自己分散了"的问题。

这两把工具都归入了 `_MANDATORY_TOOL_NAMES`，意味着即使 Agent 配置了受限的工具白名单，它们也不会被去掉 —— 组合风险管理是强制能力，不是可选项。

---

## 二、面试官必问的"深水区"问题（预判 10 问 + 回答话术）

### 问题 1（多 Agent 协作 - 新增）: "你提到了多 Agent 系统，不同 Agent 之间怎么协作？如果两个 Agent 对同一只股票看法相反怎么办？"

**回答话术**：

"这是一个架构决策问题。多 Agent 的协作模式通常有三种：Consensus（投票制）、Orchestration（主从调度）、Independent with Awareness（独立但互知）。我选择了第三种，理由很明确——交易场景中，分歧本身就是价值。如果三个 Agent 总是同买同卖，那就失去了 Agent 多样性的意义。

我的实现分两层：

**第一层，共享市场研判**。新增了 `agent_shared_context` 表。每个 Agent 复盘完成后，自动提取其市场分析的 risk-on/risk-off/range-bound 判断和置信度评分，写入共享表。下一个 Agent 跑复盘时，prompt 中会注入'多Agent共享研判'模块，格式是：

```
- 追高打板Agent: risk-on conf=0.80; 市场情绪高涨，连板高度提升...
- 自主决策Agent: range-bound conf=0.62; 政策面偏中性，量能温和...
```

Agent 可以看到同伴的判断，但 prompt 明确要求'若观点冲突，必须在风险评估中说明你的差异化理由'。关键设计是：**只要数据，不要结论**。Agent A 看到 Agent B 的研判，但不是'我应该跟 B 保持一致'，而是'如果我和 B 判断不同，我要给出我的理由'。

**第二层，事后冲突检测**。所有 Agent 订单入库后，`_record_cross_agent_order_conflicts` 扫描同一交易日同一标的的全部 pending 订单。如果发现 Agent A 买、Agent B 卖同一只股票，写入 `agent_order_trace`，事件类型 `cross_agent_conflict`，payload 包含双方 order id、方向、价格。策略是 **warn_only** —— 不强制拦截交易，但冲突标记可见于前端 trace 面板和订单详情侧栏。

这个设计体现了我对多 Agent 系统的一个核心认知：**协作不是强制一致，而是让每个 Agent 在做出独立决策前，能看到同伴在做什么、在想什么，并且让运营者事后能看到冲突全景**。强制 consensus 在交易场景下会抹杀策略多样性，但完全的'瞎子 Agent'又会产生可避免的互相踩踏。"


### 问题 2（RAG/推荐）："Telegram 推荐助手具体是怎么利用 RAG 和用户画像做个性化推荐的？"

**回答话术**：

"推荐助手的 RAG 不是传统意义上的向量检索，而是**结构化工具检索 + 用户画像注入 + 推荐记忆反馈**三层叠加。

第一层，用户发来问题后，推荐助手以 ReAct 模式调用专用工具：`recommend_search_stocks`（按策略/板块/热度筛选）、`recommend_analyze_stock`（技术面+基本面+资金面结构化分析）、`recommend_compare_stocks`（多股横向对比）。这些工具本质上是把行情数据、公司业务、历史走势编码成结构化的文本上下文，供 LLM 推理使用。

第二层，每次推荐前调用 `recommend_get_user_profile` 获取用户画像——风险偏好、投资周期偏好、板块偏好、关注股列表。画像数据和召回结果一起注入 prompt。保守型用户优先推荐低波动高股息标的，激进型用户偏重动量连板等维度。

第三层，推荐闭环的记忆反馈。`recommend_record_feedback` 记录用户正负反馈，沉淀进 `recommend_memory.md`。推荐 trace 全链路可追踪，后验 T+1/T+3/T+5 收益写入 `telegram_recommend_outcome` 表，可离线评估推荐质量。"


### 问题 3（工程难点）："在 A 股 T+1 和资金动态变化的环境下，如何保证 Agent 决策的原子性，避免超买超卖？"

**回答话术**：

"这是一个好问题。我分四层来讲：

**第一层，资金冻结**。买单创建就冻结 `price * quantity + fee`，成交只释放差额，过期全额退还。`_reserve_order_cash` 在入库前做原子校验：计算冻结金额 → 检查可用现金 → 扣减，全程在同一连接内。

**第二层，非原子换仓的风险声明**。A 股 T+1 决定了'先卖 A 再买 B'天然不原子。系统 prompt 要求 Agent 在 reason 中说明顺序风险——如果 B 的买点先触达而 A 没卖出，买单向日资金不足。风险声明写入订单 reason 字段，前端可查。

**第三层，快照-回滚**。每个 Agent 复盘前 `snapshot_agent_state` 记录三个水位线。复盘中的任何异常触发 `rollback_agent_state`，按水位线精确删除新增数据，恢复现金和持仓。

**第四层，批次追踪**。同一轮决策共享 `decision_batch_id`，批次表记录订单总数、买卖比、平均预估成交概率。部分订单因资金不足被跳过时会标记 `partial` 状态。

总结：T+1 下的原子性不是靠一笔事务解决的，而是资金冻结 + 顺序风险声明 + 快照回滚 + 批次追踪四层组合保障。"


### 问题 4（LLM 稳定性 - 新增）: "你提到 LLM 重试和熔断，具体怎么实现的？效果如何？"

**回答话术**：

"这是从实际踩坑中抽象出来的 LLM 可靠性设计。核心是一个函数 `_invoke_llm_with_retry`，两个机制：

**指数退避重试**：对每次 LLM 的 `invoke` 调用做了 3 次指数退避重试（sleep 1s / 2s / 4s）。DeepSeek API 偶尔返回 502/429，这些是瞬时故障，重试就能解决。重试次数记录在 trace 的 `attempts` 字段里，如果某个 Agent 的 `attempts > 1` 频率异常高，评估指标会暴露 API 质量问题。重试 3 次仍失败则终止本轮 ReAct 循环，不会无限重试。

**熔断器（Circuit Breaker）**：在 ReAct 主循环中追踪 `empty_rounds` 计数器。LLM 可能进入一种'reasoning 空转'状态——既不输出 tool_calls，也不输出实质内容，但也不报错。连续 3 轮出现这种情况时，触发 `quality_short_circuit` 事件，提前终止循环。这能避免 LLM 在'发呆了'的情况下继续消耗 token 和时间。

**非法工具调用追踪**：LLM 可能返回 `invalid_tool_calls`（参数格式错误导致框架层面就拒绝的调用）。我把这些单独记录在 trace 中，和普通的'工具执行失败'区分开。这对调试 Agent 的 tool use 质量很有价值——如果 `invalid_tool_calls` 占比高，说明需要在 prompt 或工具 docstring 层面改进。

这三个机制合在一起，让 LLM 调用从'祈祷不出错'变成了'出错也能优雅降级'。我统计过，加上这些防护后，因 LLM 输出问题导致流水线中断的概率从早期版本的约 15% 降到了接近 0。"


### 问题 5（Agent 评价体系）："你怎么评价一个 Agent 好不好？"

**回答话术**：（保留原内容，核心不变，面试时可以快速过）

"五个维度：收益能力（Alpha/超额收益）、风险控制（VaR/CVaR/最大回撤）、交易质量（成交率/过期率/换手率）、工程效率（token/工具失败/LLM 重试次数/熔断触发次数）、进化质量（记忆压缩次数/体系文档更新/技能置信度变化趋势）。还有一个赛马系统（race_score 0-100 + 风格标签），赛马分只作为提示词输入让 Agent 自我反思，不强制限制仓位——如果代码替 Agent 做决策，会剥夺它的自主学习能力。"


### 问题 6（工具选择）："ReAct 里面有十几个工具，怎么保证 Agent 能选对工具，不瞎调？"

**回答话术**：（保留原内容，略调整）

"三个层面：第一，工具 docstring 遵循'功能和边界同样重要'原则，`get_strategy_param_schema` 专门让 Agent 先查参数再调策略。第二，系统提示词给出明确调用次序建议和克制原则。第三，每个 Agent 可配置工具白名单，`filter_tools_by_names` 过滤后才传给 LLM。最近还加了两个强制工具——`get_portfolio_risk_metrics` 和 `get_correlation_info` 归入 `_MANDATORY_TOOL_NAMES`，确保即使工具白名单受限，组合风险管理能力也不会被移除。如果 Agent 还是调错了，失败会反映在 `tool_failure_rate` 和 `invalid_tool_calls` 指标中，用数据驱动 prompt 优化。"


### 问题 7（稳定性）："LLM 输出不稳定，JSON 格式经常坏，你怎么保证系统不因为这个挂掉？"

**回答话术**：（保留原内容的核心五层防护，略调整以体现新改进）

"五层防护：多层 JSON 解析（代码块匹配 → 裸块匹配 → 反搜大括号）→ 价格合法性强制校验 + 修复轮（最多 3 轮 LLM 自修复）→ 非法订单不入库直接丢弃 → 进程级超时兜底（multiprocessing.Process，600 秒超时直接 terminate）→ 快照回滚。加上前面提到的 LLM 指数退避重试和熔断器，这套防护链的每一环都经过了实际踩坑验证。"


### 问题 8（扩展性）："如果把这个系统从模拟升级到生产级别，你会重点改哪些地方？"

**回答话术**：（保留原内容，核心不变）

"按优先级：PostgreSQL 替代 SQLite、消息队列替代线程调度、异步任务替代同步 HTTP、行情并发拉取、OpenTelemetry 可观测性。但关键要补一句——这些是'规模问题'不是'架构问题'。当前架构的分层和模块边界已经为规模化留好了接缝。"


### 问题 9（幻觉/安全）："Agent 推荐股票时给了错误分析，甚至编造不存在的公司信息，怎么防止？"

**回答话术**：（保留原内容）

"三道防线：强制数据溯源（thinking log 可回溯每笔决策的工具引用链）、价格硬约束（涨跌停校验拦截虚假价格）、推荐后验收益追踪（T+1/T+3/T+5 真实收益反推推荐质量）。"


### 问题 10（进化深度 - 新增）: "你提到了 EMA 长期置信度和因子权重学习，为什么不直接用强化学习去做？"

**回答话术**：

"这是一个好问题，背后其实是 Agent 进化路线的技术选型决策。我刻意选择了'规则驱动的参数调整'而不是 RL 或端到端梯度优化，原因有三：

**第一，样本量不够**。RL 需要大量的 trial-and-error 样本来学习好的 policy。A 股每天只有 4 小时交易时间，一年约 240 个交易日。一个 Agent 一年能产生的"决策→结果"样本也就 200 多条。在这种小样本场景下，RL 的价值函数会严重 overfitting。

**第二，可解释性要求**。交易场景下，你不仅需要 Agent 做对，还需要能解释为什么做对。`new_conf = 0.55 * old_conf + 0.25 * recent_win_rate + 0.20 * ema_win_rate` 这个公式里的每一项都有明确的业务含义：历史惯性、近期反馈、长期信号。前端可以看到每次更新的完整记录，如果有人质疑"为什么 momentum_hunt 从 0.66 跌到 0.42"，我可以精确追溯到是哪几笔订单的失败导致的。RL 的黑盒 policy network 做不到这种粒度的可解释性。

**第三，安全边界**。RL 的探索过程天然包含随机行为。在金融场景下，一次随机的极端行为可能造成不可逆的损失。我的参数调整有硬上下限（置信度 0.1-0.92，权重 tilt ±0.06），确保 Agent 的行为变化始终在安全范围内。RL 的 exploration noise 很难保证这种确定性约束。

但这不意味着永远不做 RL。我留了一个接缝：当前的因子权重学习和 EMA 置信度产生的数据可以作为 RL 的 expert demonstration，用于做离线 imitation learning 的预训练。未来如果系统积累了足够多的样本，可以逐步引入更复杂的 RL 策略。关键在于**先建立确定的、可审计的 baseline，再用 RL 做增量优化**，而不是一上来就用 RL solve everything。"


---

## 三、针对"自我进化"的深度吹嘘

### 核心叙事："我实现的是类 Hermes 的 agentic self-evolution"

> 注意：以下是根据最新代码升级后的完整进化叙事，面试时挑最亮眼的部分展开。

"Hermes 的核心思想是用评估结果来动态调整 Agent 的行为参数。我的进化体系在架构上和对 Hermes 的理解一致，但更进一步——不是单维度参数调整，而是**技能置信度 + 因子权重 + 记忆内容 + 体系文档**四个维度的联动进化。

具体拆成六个确定性的步骤：

**Step 1 — 执行与采集**：每笔订单挂 `skill_id`，记录执行结果、失败归因、成交概率预估、后验成交/过期。同时记录 LLM 重试次数、熔断事件、非法工具调用。这些不只是日志，而是进化系统的输入原料。

**Step 2 — 归因分类**：失败归因到三类——market（不可抗力）、timing（价格策略）、strategy（仓位/资金策略）。归因结果写入 `failure_attribution` 字段，后续统计和 prompt 生成都会引用。

**Step 3 — 技能级别三因子置信度更新**：这是最近升级的核心。新的置信度公式是：
`new_conf = 0.55 × old_conf + 0.25 × recent_win_rate + 0.20 × ema_win_rate`
其中 `ema_win_rate` 来自 `_ema_fail_rate` 函数对整个订单历史的指数移动平均计算（alpha=0.18）。短期的近 10 笔反馈影响 25% 的权重，长期的 EMA 信号影响 20%，历史惯性占 55%。这样既能快速响应近期变化，又不会被短期噪音带着跑。

**Step 4 — 因子权重历史学习**：`_learn_factor_weights_from_history` 回看 20 日历史，计算每个因子维度（情绪/技术/资金/政策）的加权收益贡献。正贡献最多的因子 +0.06 倾斜，负贡献最多的 -0.06 惩罚。权重来源标注 `history_fit_20d` vs `style_default`，可追溯。

**Step 5 — 记忆沉淀与压缩**：三个记忆文件（trade_fact / trade_prefer / short_ring）各自有固定大小上限。超限后由 LLM 本身压缩，要求"只保留已验证、可执行、可复用的规律"。压缩记录写入审计表 `memory_compression_audit`。

**Step 6 — 反思文档化（五章 + 因子权重建议）**：反思触发条件从 3 种扩展到 6 种（新增连续同方向过期、单日回撤超 5%、技能置信度突变超 0.2）。输出从四章升级为五章，新增第五章"因子权重调整建议"，由 `_suggest_factor_weights` 函数基于 20 日历史数据生成正/负贡献因子排名。

这六步串联起来：**执行 → 归因 → 技能参数 EMA 调整 → 因子权重历史学习 → 记忆沉淀压缩 → 五章反思文档化 + 权重建议 → 全部注入下一次 prompt**。

和 Hermes 的核心理念一致但更全面：进化不是让 Agent 改自己的 prompt，而是一套确定性的多维度数据处理管道。四个维度的联动使得进化的效果是叠加的——技能置信度变低 → Agent 轻仓用该技能 → 因子权重向更有效的维度倾斜 → 记忆沉淀反映新的市场规律 → 反思文档总结成可复用的交易体系。这个闭环跑起来的标志是：Agent 主动放弃某类历史上频繁失败的交易模式，并且能写出一段有数据支撑的'为什么放弃'的反思。"

### 如果被追问 "Agent 越进化越保守怎么办？"

"这是一个必须正视的问题。当前有几个设计来防止过度保守：
1. 技能置信度是技能级别的，不是 Agent 级别的。某个技能被打低了，Agent 可以切换到其他技能。
2. 置信度下限 0.1，永不归零，Agent 可以小仓位继续尝试。
3. EMA 长期信号的权重只占 20%，不给历史表现过大惯性。
4. 因子权重的学习范围限制在 ±0.06，不会因为短期波动剧烈转向。
5. 赛马指标只作为提示词输入，不强制限制仓位——如果代码替 Agent 做仓位决策，本质上是在剥夺 Agent 的'犯错和改进'循环。"


---

## 四、不可避免的"救命话术"

### 场景 1：被问到 Baostock 数据清洗的具体 Pandas 代码

> "数据清洗这块我用了 baostock 的标准接口获取日线，然后用 pandas 做格式标准化。不过对我来说更关键的不是单点的清洗代码怎么写，而是我在系统层面做了数据就绪检测——要求 95% 的股票覆盖率才允许 Agent 跑复盘，未就绪时走退避重试链。这个设计避免了用脏数据驱动交易决策。所以我更多精力放在数据质量的工程保障上，而不是某个具体清洗函数的实现。"

### 场景 2：被问到 LangChain 源码级别的问题

> "我用了 LangChain 的 ChatOpenAI 封装和 tool binding，但核心的 ReAct 循环是我手写的，包括 retry/circuit breaker 也是自己实现的。因为 DeepSeek 的 reasoning_content 在多轮回传上和 LangChain 的默认行为有兼容性问题，加上我需要精确控制 trace 收集、熔断触发、非法工具调用的粒度，所以选择了更可控的手动 messages 数组管理。如果深入聊 LangChain 的源码实现，我建议我们聚焦在 Agent 的工具调用决策和可靠性机制上——这个项目的技术深度更多在这些地方。"

### 场景 3：被问到量化策略的数学细节

> "这些经典技术指标的计算不是我这个项目的核心价值。我的创新点在于如何让 LLM Agent 正确地使用这些策略——包括策略参数的可解释性（`get_strategy_param_schema` 工具暴露给 Agent）、策略有效性的追踪（技能 EMA 置信度量化每个策略的近期和长期表现）、以及策略失效时的自动降级（因子权重倾斜 + 仓位参数收紧）。我更像是'策略的编排者'而不是'策略的发明者'。"

### 场景 4：被问到为什么不接真实券商 API 做实盘

> "这涉及到合规和风险的问题。我在这个项目里把交易规则层完整封装成了独立的 `trading/rules.py` 模块，这意味着如果将来接入真实券商 API，只需要替换执行层（把数据库写入换成 API 调用），规则层和 Agent 决策层完全不需要改。这是典型的 hexagonal architecture 思路。"

### 场景 5：被问到"你觉得你这个 Agent 跟 AutoGPT/BabyAGI 有什么区别"

> "AutoGPT 和 BabyAGI 是通用任务 Agent，在开放域里做任务规划。我这个是垂直领域的 Agent，核心差异在于：
> 1. **有硬约束规则层**，涨跌停/T+1 不是靠 prompt 约束的，是代码级强制校验。
> 2. **有完整的评估和进化闭环**，每个交易日都在积累质量数据和进化记忆，AutoGPT 跑完就结束了。
> 3. **多 Agent 赛马 + 协调**，不同 Agent 在同一市场环境下竞争，通过共享研判互相感知，通过赛马评分比较优劣。这是持续的 A/B test。"

### 场景 6（新增）：被问到"DeepSeek V4 有什么坑"

> "DeepSeek V4 在多轮 tool calling 场景下，reasoning_content 的回传是最大的坑。LangChain 的默认 AIMessage 序列化不会保留这个字段，导致第二轮请求报 400 错误。我的解决方案是 `_sanitize_ai_message` 函数——只保留 content、tool_calls、id，主动丢弃 provider 专有字段。这是在生产中踩出来的坑，也是为什么我没有用 LangChain create_agent 而是手写 ReAct 循环的核心原因。另外 DeepSeek 的 thinking mode 和 tool calling 同时开启时行为不稳定，所以我做了非 thinking 模式下的 tool call，保留了 thinking 模式给不需要 tool 的场景。"


---

## 五、面试最后 5 分钟的隐藏加分项

当你觉得面试快结束了，面试官问"你还有什么想问的吗"——主动抛出以下观点：

> "我想借这个机会分享一个我做这个项目最大的认知收获。很多人觉得做 Agent 就是写 prompt + 调 API，但我花了大概 40% 的代码在 Harness 层——评估、进化、校验、trace、retry、circuit breaker。我觉得 Agent 开发工程师的核心竞争力不是在 prompt engineering 上，而是在于你能不能把 LLM 不可预测的输出，通过工程手段约束到一个可接受的错误边界内。
>
> 我最近读了一篇关于'Agent Specification'的论文，里面提到一个观点我特别认同：Agent 不只是 LLM + tools，Agent = LLM + tools + guardrails + evaluation + feedback loop。这个项目里 guardrails（交易规则校验、价格硬约束）、evaluation（20+ 维指标、trace）、feedback loop（技能 EMA 置信度、因子权重学习、反思触发）全部自建了。这种从'能用'到'能稳稳地用'的跨越，才是我认为 Agent 工程最有价值的部分。"

这段话会让面试官觉得，你不是一个"调 API 的"，而是一个真正理解 Agent 工程化挑战的工程师。

---

## 附录：面试时的技术关键词速查表

| 你要展示的能力 | 对应的技术点 | 代码位置（被追问时引用） |
|---|---|---|
| Agent 架构设计 | 多 Agent 差异化配置、工具白名单、强制工具 | `backend/agents/llm_agent.py` + `backend/agents/tools.py` |
| 多 Agent 协调 | 共享研判表、冲突检测、warn_only 策略 | `backend/pipeline/daily_pipeline.py:_upsert_shared_context` / `_record_cross_agent_order_conflicts` |
| ReAct 实战 + 可靠性 | 手写 ReAct loop、指数退避重试、熔断器、非法工具调用追踪 | `backend/agents/llm_agent.py:_invoke_llm_with_retry` / `run_react_agent_decision` |
| DeepSeek 兼容 | reasoning_content 清理、非 thinking 模式 tool call | `backend/agents/llm_agent.py:_sanitize_ai_message` |
| 交易规则工程化 | T+1、涨跌停、资金冻结-释放、快照回滚 | `backend/trading/rules.py` + `backend/pipeline/order_executor.py` |
| 组合风险管理 | 集中度、行业暴露、VaR、相关性矩阵 | `backend/agents/tools.py:get_portfolio_risk_metrics` / `get_correlation_info` |
| 评估体系 | 20+ 维指标、订单 trace、决策批次、成交概率预估 | `backend/evaluation.py` + `backend/db/repository.py` |
| 自我进化 — 技能层 | 三因子置信度（近期+长期 EMA+惯性）、仓位参数自调整 | `backend/evolution/skills.py:update_skill_confidence` / `_ema_fail_rate` |
| 自我进化 — 因子层 | 历史收益回看、因子权重学习、归一化 | `backend/evolution/engine.py:_learn_factor_weights_from_history` / `_normalize_weights` |
| 自我进化 — 反思层 | 6 种触发条件、五章反思文档、因子权重建议 | `backend/evolution/reflection.py:_reflection_triggers` / `_suggest_factor_weights` |
| 自我进化 — 记忆层 | 分层记忆、固定大小压缩、压缩审计 | `backend/evolution/memory.py` |
| 赛马评价 | race_score、风格标签、Alpha/Beta 代理指标 | `backend/evolution/race.py` |
| 数据工程 | 覆盖率检查、退避重试、增量拉取 | `backend/pipeline/daily_pipeline.py` |
| 容错设计 | JSON 多层解析、价格修复轮、进程级超时 | `backend/agents/llm_agent.py` + `backend/pipeline/daily_pipeline.py` |
| 推荐系统 | 用户画像、工具检索、推荐 trace、后验收益 | `backend/telegram/recommender.py` |
| 前端可视化 | Agent 配置工厂、trace 侧栏（含冲突标记）、评估面板 | `frontend/src/views/AgentDetail.vue` |
