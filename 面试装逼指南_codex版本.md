# A 股多 Agent 智能投顾系统：面试装逼指南 Codex 版

> 你的主线不要讲“我做了一个股票推荐机器人”，要讲：“我做的是一个把 LLM Agent 放进 A 股交易约束里的工程化实验平台。核心不是让模型会聊天，而是让它的决策可追踪、可评估、可回滚、可进化。”

## 1. 项目核心亮点

### 亮点一：不是单 Agent 玩具，而是多 Agent 交易组织

我这个项目里不是一个通用聊天 Agent 瞎推荐股票，而是把 Agent 做成了“交易员组织”。

- **追高打板 Agent**：偏情绪周期、连板、强势板块、开盘抢入，适合观察高波动策略。
- **自主决策 Agent**：会综合政策、基本面、技术、资金、情绪，不是简单复制打板逻辑。
- **推荐助手 Agent**：面向 Telegram 用户，结合用户画像、关注股、交易员公开记忆和推荐反馈做个性化推荐。

面试里可以这么说：

> 我把多 Agent 的差异不是停留在 prompt 上，而是落到了工具白名单、策略偏好、板块权限、风险配置、复盘时间、推送时间、评估口径和进化记忆上。这样每个 Agent 都有独立资金、持仓、订单、日报和技能置信度，系统能做赛马比较，而不是多个 prompt 共用一套状态。

### 亮点二：把 A 股交易规则做成了 LLM 之后的硬约束层

LLM 可以负责推理，但不能负责“最终可信”。所以项目把 A 股规则工程化封装在撮合和下单链路里：

- 买单先冻结资金 `reserved_cash`，避免重复花同一笔现金。
- 卖单校验 `available_shares` 和 `buy_date`，严格处理 T+1。
- 挂单价必须做涨跌停区间校验，非法价格会触发修复或拒绝入库。
- 一字涨停/跌停禁止交易。
- `open_get_in` 支持开盘抢入/抢出，普通限价按日内 high/low 是否触达撮合。
- 新复盘会替换旧预操作单，旧买单释放冻结资金。

面试里可以这么说：

> 我没有相信 LLM 的输出，而是把 LLM 当成“策略提案者”。真正进入订单系统之前，会经过价格合法性、资金、T+1、板块权限、持仓可卖数量这些 deterministic guardrail。这样模型可以有创造性，但账户状态必须是确定性的。

### 亮点三：手写 ReAct loop，形成“证据 -> 决策 -> 校验 -> 订单”的闭环

项目没有完全依赖黑盒 agent runtime，而是手写 ReAct 工具循环。交易 Agent 会先看市场、政策、板块、K 线、历史订单、进化上下文，再输出 JSON 交易计划。

关键点：

- 工具轮数上限 `max_tool_turns`，防止无限调用。
- 工具白名单按 Agent 配置隔离。
- 工具 trace 记录 LLM 调用、工具调用、耗时、token、失败信息。
- 最终 JSON 解析失败、价格非法都会进入修复流程。
- prompt 明确要求下单必须调用 `validate_order_price_limit`，并参考 `get_recent_order_history` 和 `get_evolution_context`。

面试里可以这么说：

> 我对 ReAct 的理解不是让模型多调用几个工具，而是把工具调用变成可审计证据链。订单理由必须能追溯到工具返回的数据，后面 evaluation 也会检查有没有工具证据、有没有风险解释、JSON 是否合规。

### 亮点四：Harness 把 LLM 不可预测性变成可观测、可评估、可优化

我最想强调的是 Harness。因为 Agent 最大的问题不是“能不能跑一次”，而是“跑错了怎么知道、怎么复盘、怎么改”。

项目里每次复盘会生成 `decision_batch_id`，同一轮买卖订单会被串成一个决策批次。每笔订单还记录：

- `fill_probability`：基于历史区间触达概率估算成交可能性。
- `price_aggressiveness`：挂单价格相对最近收盘价的偏离。
- `agent_order_trace`：记录 created、matched、filled、expired、replaced 等生命周期事件。
- `failure_attribution`：把失败归因到 market、timing、strategy 等。
- `agent_eval_metric`：记录收益、风险、成交率、过期率、工具失败、JSON 修复、token 成本、延迟等。

面试里可以这么说：

> 我解决 LLM 不可预测性的方式不是追求一次输出完美，而是把每次输出放进 Harness。模型说了什么、用了什么工具、为什么生成这批订单、订单有没有触达、为什么过期、下次怎么改，全部落到 trace 和 evaluation。这样 LLM 的不确定性不会消失，但它会被工程系统驯化成可度量的误差。

## 2. 面试官必问的“深水区”问题

### Q1：Telegram 推荐助手是如何利用 RAG 和用户画像结合的？

可以这样答：

> 我这里的 RAG 不是简单“向量库搜几段文本塞 prompt”，而是做了轻量的结构化 RAG。Telegram 用户发来一句自然语言需求后，系统先做 intent 和策略解析，再检索几类上下文：第一是 `telegram_user_profile` 里的风险偏好、周期偏好、偏好板块、排除板块；第二是 `telegram_watchlist` 里的关注股；第三是交易员公开体系，也就是 `recommend_get_trader_memory` 读取的 Agent 赛马表现、高置信技能、交易体系摘要；第四是个股分析、公司业务和政策信号。

继续展开：

> 推荐时不是只按技术指标排序，而是做“候选生成 + 用户适配 + 交易员记忆增强”。比如用户偏短线、风险中等、偏 AI/半导体，那 `recommend_search_stocks` 先产出候选，`recommend_get_user_profile` 约束风险和板块，`recommend_get_trader_memory` 引入当前表现最好的交易员体系，再用 `recommend_analyze_stock` 或 `recommend_compare_stocks` 补证据。最后推荐结果会写入 `telegram_recommend_feedback.trace_json` 和 `telegram_recommend_eval`，用户后续说“太激进”“亏了”“不错”，会反向更新画像和推荐技能置信度。

如果面试官追问“你用了什么向量库”：

> 这个版本我优先做的是结构化 RAG，而不是先堆向量库。因为金融推荐里很多信息本身就是强结构化的，比如用户画像、关注股、Agent 赛马指标、技能置信度、后验收益。向量召回适合政策长文和公司业务文档，后续可以把 `data/policy_docs` 和 `data/company_business` 接到 embedding 索引里，但核心 ranking 仍然要回到用户画像和风险约束。

### Q2：在 A 股 T+1 和资金动态变化环境下，如何保证 Agent 决策的原子性，避免超买超卖？

可以这样答：

> 我把这个问题拆成两个层面：订单入库前的资金/持仓预占用，以及撮合时的最终状态校验。买单入库时会计算成交金额和费用，然后冻结 `reserved_cash`，直接扣减可用现金。这样同一轮复盘里即使 LLM 生成多笔买单，也不能重复使用同一笔资金。卖单入库时会查 `agent_position.available_shares` 和 `buy_date`，如果 T+1 不满足或者可卖数量不足，订单不会进入 pending。

继续展开：

> 真正撮合时还会二次校验。比如买单成交价可能低于限价，会释放多冻结的部分；如果因为价格、资金或行情问题成交不了，会把订单标记为 expired 并释放冻结资金。卖单撮合时还会再次检查持仓和 T+1。项目里还明确处理了 A 股换仓不是原子操作的问题：如果“先卖 A 再买 B”，B 的买入条件可能先触达，但 A 没卖出去，资金就不会凭空出现，所以 prompt 要求 reason 里说明非原子换仓风险，进化模块还会用 5 分钟 K 线复盘触发顺序。

可以点名字段：

> 这里关键字段是 `reserved_cash`、`available_shares`、`buy_date`、`decision_batch_id` 和 `failure_attribution`。如果失败，下一轮 Agent 能看到失败原因，比如“资金不足”“T+1 限制”“限价未触达”，而不是只看到一个收益数字。

### Q3：你提到了 order trace 和评估指标，具体如何用这些数据优化 Agent 推理能力？

可以这样答：

> order trace 是我的核心 Harness。每个订单从 created 到 matched、filled、expired、replaced 都会写入 `agent_order_trace`。每轮复盘有一个 `decision_batch_id`，能把同一批订单串起来看。比如同一批里 3 个买单都 expired，我不会只说“模型推荐错了”，而是看 `fill_probability` 和 `price_aggressiveness`：到底是价格太保守没触达，还是开盘抢入条件太苛刻，还是策略本身在当前市场失效。

继续展开：

> 然后这些结果进入两条优化链路。第一条是 prompt 反哺，下一次复盘的上下文会展示近期失败订单、失败原因、上次成交概率、价格偏离，让 Agent 调整挂单价、仓位或放弃同类方向。第二条是技能进化，`agent_evolution_skill` 会根据近 10 笔同技能订单失败率调整 `confidence_score` 和 `dynamic_params`，比如降低 `max_single_position`、收紧止损或降低某个策略的权重。

一句话总结：

> 我不是用 evaluation 做一个事后报表，而是把 evaluation 作为下一轮 reasoning 的输入。也就是从 “LLM output” 变成 “LLM output -> order outcome -> failure attribution -> prompt/evolution update”。

### Q4：如何评价一个 Agent？评价体系是什么？

可以这样答：

> 我没有只用收益率评价 Agent，因为那样会把运气和能力混在一起。我把评价体系分成四层。

第一层是交易结果：

- 日收益、累计收益、超额收益、Alpha。
- 最大回撤、波动率、VaR、CVaR。
- 胜率、盈亏比、换手率、平均持仓天数。

第二层是订单质量：

- `order_fill_rate` 成交率。
- `pending_expire_rate` 过期率。
- `open_get_in_success_rate` 开盘抢入成功率。
- `fill_probability` 和实际成交的偏差。

第三层是 Agent 过程质量：

- LLM 调用次数、token、延迟。
- 工具调用次数、工具失败率。
- JSON 解析失败次数。
- 价格修复次数。
- 是否使用了必要工具证据、是否解释风险。

第四层是进化质量：

- `skill_confidence_delta` 技能置信度变化。
- `memory_compressions` 记忆压缩次数。
- `reflection_triggers` 反思触发次数。
- 技能失败率是否下降。

收束话术：

> 所以我评价 Agent，不是问它“今天赚没赚钱”，而是问：它有没有遵守规则、有没有证据链、订单有没有可执行性、失败能不能被归因、下一轮能不能变得更稳。

### Q5：如何保证 ReAct 里的 Agent 能选出合适的工具？

可以这样答：

> 我做了三层控制。第一层是 prompt 里的策略约束：要求先看大盘/政策/板块，再筛候选，只深入分析 2-4 只股票，最后必须做挂单价计算和涨跌停校验。第二层是工具白名单，不同 Agent 只能看到自己允许的工具，比如追高打板 Agent 和自主决策 Agent 的工具权限可以不同。第三层是过程评估，tool trace 会记录每轮调用的工具、参数、耗时和错误，`agent_eval_metric` 里会统计 `tool_calls`、`tool_failures`、`tool_failure_rate`。

继续展开：

> 另外我没有让工具选择完全依赖模型自觉。比如最终下单前必须通过 `validate_order_price_limit`，近期失败必须参考 `get_recent_order_history`，进化上下文通过 `get_evolution_context` 注入。就算模型没选对工具，后面 JSON 校验、价格校验、订单入库校验也会兜底。

高级一点的说法：

> ReAct 的工具选择我认为要从“模型自由选择”升级成“模型选择 + 工程约束 + 评估反馈”。否则工具越多，Agent 越容易变成不可控的自动化脚本。

### Q6：怎么证明这个 Agent 不是玩具，而是可以稳定运行的工程系统？

可以这样答：

> 我主要从可恢复、可观测、可降级三个角度做。可恢复是每日流水线有复盘前快照，出错可以回滚，避免半写入污染账户。可观测是 thinking log、tool trace、order trace、eval metric、daily report 都会落库或落文件，前端可以看到 prompt preview、订单 trace、评估指标。可降级是 Telegram 推荐 ReAct 如果 JSON 失败或者没有生成有效推荐，会走规则链 fallback，至少保证用户能拿到结构化结果。

继续展开：

> 另外交易流水线不会在行情没准备好时硬跑。系统要求 A 股主板/中小板数据覆盖率达到阈值，比如 95%，指数数据也要更新到目标交易日；没就绪会退避重试。这一点很关键，因为金融 Agent 最怕用半截数据做一本正经的错误决策。

### Q7：为什么不用 Fine-tuning，而是做进化记忆和技能置信度？

可以这样答：

> 交易 Agent 的问题不是知识不会，而是上下文和市场状态变化太快。Fine-tuning 更适合固化长期能力，但不适合每天根据订单失败、市场风格、用户反馈快速调整。所以我做的是在线进化层：`agent_evolution_skill` 管技能置信度和动态参数，`agent_memory` 管交易事实、偏好和短期极端行情记忆，失败订单和后验收益会反哺下一轮 prompt。

继续展开：

> 这个设计更像可控的 Hermes 式自进化：不是让模型自己改代码，而是在工程系统里动态调整 prompt context、技能权重和工具参数。好处是可解释、可回滚、成本低，而且每次调整都有 `agent_evolution_event` 和 `evolution_record` 记录。

### Q8：如果要接真实交易，你会怎么做风险控制？

可以这样答：

> 我会把现在的模拟撮合层替换成券商委托适配层，但不会让 LLM 直接下实盘单。架构上会多加三道闸：第一是 pre-trade risk check，比如单票仓位、总仓位、黑名单、涨跌停、T+1、资金、频率限制；第二是 human approval 或小资金灰度，尤其是新策略、新技能低置信度的时候；第三是 post-trade surveillance，把成交、撤单、滑点、异常亏损继续写回 Harness。

收束：

> 真实交易里 LLM 只能是决策建议者，不能是最终执行权威。最终权威必须是规则引擎、风控系统和人工授权。

## 3. 针对“自我进化”的吹嘘点

这一段你可以讲得稍微高级一点：

> 我这个项目的自我进化不是一句“把失败经验写进 prompt”这么简单，而是分成记忆、技能、评估、反哺四层。

第一层是记忆：

- `trade_fact.md`：客观市场规律记忆。
- `trade_prefer.md`：Agent 主观交易偏好。
- `short_ring.md`：近 3 日极端行情短期记忆。
- `snapshots/{trade_date}.json`：复盘前冻结记忆快照，保证当天决策认知一致。

第二层是技能：

- `agent_evolution_skill` 记录 `skill_id`、`skill_name`、`confidence_score`、`recent_fail_rate`、`dynamic_params`、`invalid_scene`。
- 追高打板 Agent 有 `momentum_hunt`、`risk_exit`。
- 自主决策 Agent 有 `balanced_factor`、`position_rotate`。
- 每笔订单写入 `skill_id` 和 `skill_confidence`，所以订单结果能反推某个技能是否有效。

第三层是评估：

- 订单是否成交、是否过期。
- 失败归因是 market、timing 还是 strategy。
- `fill_probability` 和真实成交结果是否一致。
- 近 10 笔同技能失败率如何。
- 推荐侧看用户反馈和 T+1/T+3/T+5 后验收益。

第四层是反哺：

- 失败率高，降低 `confidence_score`。
- 如果技能连续失败，动态收紧 `max_single_position`、止损参数或策略参数。
- 如果技能表现稳定，轻微提高置信度。
- 下一轮 prompt 读取进化上下文，低置信技能只能轻仓或不用。

可以直接背这段：

> 我把 Hermes 式自我进化落成了一个可控闭环：Agent 每次决策都带 `skill_id`，订单执行后通过 trace 和 evaluation 得到结果，再更新 `agent_evolution_skill.confidence_score` 和 `dynamic_params`，最后通过 `get_evolution_context` 注入下一轮 ReAct。它不是玄学记忆，而是“技能选择 -> 订单结果 -> 失败归因 -> 置信度更新 -> prompt 注入”的闭环。

更装一点的表述：

> 我没有让 Agent 自己变聪明，而是让系统知道“它什么时候不聪明”。一旦系统能稳定识别哪类推理在什么市场场景下失效，进化就不是口号，而是一个在线控制问题。

## 4. 遇到不懂底层细节时的救命话术

### 场景一：问 Baostock 数据清洗的具体 Pandas 代码

可以这样答：

> 这块我不会把重点放在某一行 Pandas 写法上，因为数据清洗的核心不是 `fillna` 或 `merge` 怎么写，而是数据契约怎么保证。我这里关注的是交易日期对齐、复权口径一致、主板股票池过滤、指数数据新鲜度、覆盖率阈值和异常行情兜底。具体到 Pandas 实现，主要是日线按 `ts_code + trade_date` 去重、排序、计算 MA5/10/20/60 和涨跌停标记，这些是可替换实现；真正不能错的是数据进入 Agent 前的质量门禁。

### 场景二：问 LangChain 内部源码级别实现

可以这样答：

> LangChain 我主要把它当成工具协议和模型适配层，没有把核心控制权交给黑盒 runtime。项目里 ReAct loop 是手写的：LLM 输出 tool_calls，我执行工具，把 ToolMessage 回传，再记录 trace。所以即使不依赖 LangChain 的 create_agent，我也能控制工具轮数、错误处理、JSON 修复、token/latency 统计和 fallback。源码级细节我可以继续深入，但这个项目的关键设计是把 Agent runtime 的关键路径掌握在自己手里。

### 场景三：问“你这个推荐到底是不是 RAG”

可以这样答：

> 如果把 RAG 狭义理解成“向量库召回文本”，那当前主链路更偏结构化 RAG；如果按工程定义，RAG 是检索外部知识增强生成，那它是 RAG。因为回答前会检索用户画像、关注股、交易员公开体系、技能置信度、推荐记忆、政策/公司业务/行情分析，再由模型生成推荐。金融场景里我更看重可解释和可控，所以先做结构化检索增强，再扩展向量检索。

### 场景四：问特别细的策略参数为什么这么设

可以这样答：

> 这些参数我不会说它们是最优解，它们是工程上可迭代的初始值。我的设计重点是把参数暴露给 Agent 和进化系统，比如 `get_strategy_param_schema` 能告诉 Agent 某个策略有哪些敏感参数，`agent_evolution_skill.dynamic_params` 能根据失败率调整仓位和止损。也就是说，参数不是拍脑袋写死，而是进入 Harness 后持续被订单结果校正。

### 场景五：被问“LLM 推荐股票靠谱吗”

可以这样答：

> 我不会把 LLM 当成 alpha 本身。LLM 在这里负责信息组织、策略解释、工具编排和交易计划生成；真正的可信部分来自数据、规则、撮合、评估和风控。这个项目验证的不是“LLM 一定能炒股赚钱”，而是“如何把 LLM 放进一个高约束、高可观测、可评估、可进化的金融 Agent 系统里”。

## 最后一段收尾话术

面试最后可以这样总结：

> 这个项目我最满意的点，不是它能推荐几只股票，而是它把 Agent 从 demo 拉到了工程闭环里。LLM 负责推理，工具负责取证，规则引擎负责兜底，order trace 负责解释，evaluation 负责打分，evolution 负责下一轮变得更稳。我的理解里，Agent 工程化的核心就是：模型可以不稳定，但系统必须稳定。

