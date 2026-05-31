# A股多Agent智能投顾系统 — 开发文档与代码Review指南

> 生成日期: 2026-05-10
> 项目路径: `/home/xulu/stock_run_88/`

---

## 1. 项目概览

基于 FastAPI + Vue3 + ECharts 的 A 股多 Agent 智能投顾模拟交易系统。支持策略选股、LLM Agent 模拟交易、策略回测、K 线分析、政策监控、公司业务搜索等功能。

| 层级 | 技术栈 |
|------|--------|
| 后端 | Python 3.12 + FastAPI + LangChain 1.2 + DeepSeek v4 pro |
| 前端 | Vue3 (Composition API) + Vite + ECharts + marked |
| 数据库 | SQLite (`data/stock_run.db`) |
| 数据源 | baostock 日线 CSV（2145+ 只主板股票，2019-01-01 至今） |
| LLM | DeepSeek v4 pro，OpenAI 兼容接口，base_url=`https://api.deepseek.com` |

---

## 2. 目录结构

```
stock_run_88/
├── backend/
│   ├── main.py                    # FastAPI 入口，路由注册
│   ├── config.py                  # 全局配置 (API keys, 费率, 均线周期)
│   ├── agents/
│   │   ├── base.py                # AgentContext, AgentDecision 数据类
│   │   ├── factory.py             # AgentManager — Agent CRUD
│   │   ├── llm_agent.py           # LLM Agent 调用 (手动 ReAct 循环)
│   │   └── tools.py               # 6个 LangChain 工具 (实盘用)
│   ├── strategies/                # ★ 13个策略 (见第4节)
│   │   ├── base.py                # BaseStrategy + StrategyResult
│   │   ├── registry.py            # StrategyRegistry 装饰器注册
│   │   ├── momentum.py            # 龙头打板 (lookback=10)
│   │   ├── trend.py               # 多波动量趋势 (lookback=60)
│   │   ├── ma_pullback.py         # 20/60均线回调 (lookback=30)
│   │   ├── ma_cross.py            # MA5/MA20金叉 (lookback=30)
│   │   ├── macd.py                # MACD策略 (lookback=120)
│   │   ├── kline_pullback.py      # K线回踩MA20 (lookback=30)
│   │   ├── consolidation.py       # 横盘突破 (lookback=60)
│   │   ├── uptrend.py             # 长期上升趋势 (lookback=250)
│   │   ├── bottom_reversal.py     # 底部放量反转 (lookback=120)
│   │   ├── box_range.py           # 箱体震荡 (lookback=60)
│   │   ├── sentiment_cycle.py     # 情绪周期 (lookback=60)
│   │   ├── volume_pullback.py     # 缩量回踩短均 (lookback=30)
│   │   └── yang_three_yin.py      # 一阳夹三阴 (lookback=20)
│   ├── backtest/
│   │   ├── engine.py              # 回测引擎 v2 (预加载+日志+止损)
│   │   └── metrics.py             # 绩效指标计算
│   ├── simulation/                # ★ LLM Agent 模拟交易框架
│   │   ├── sim_engine.py          # 模拟引擎 (逐日循环+订单撮合)
│   │   ├── sim_agent_runner.py    # 模拟 Agent 决策 (单次 LLM 调用)
│   │   ├── sim_tools.py           # 时间感知工具包装器
│   │   └── __init__.py
│   ├── quant/                     # ML/DL 量化接口 (预留)
│   │   ├── base.py                # BaseQuantModel + QuantPrediction
│   │   └── registry.py            # QuantModelRegistry
│   ├── data/
│   │   ├── loader.py              # CSV 加载 + compute_mas + compute_limit_status
│   │   ├── fetcher.py             # Baostock 数据获取
│   │   └── indicators.py          # MACD/EMA 指标 + 板块热度
│   ├── trading/
│   │   ├── rules.py               # 交易规则 (T+1/涨跌停/费率)
│   │   └── calculator.py          # 盈亏计算
│   ├── pipeline/
│   │   ├── daily_pipeline.py      # 每日流水线
│   │   └── order_executor.py      # 条件单撮合
│   ├── policy/
│   │   ├── crawler.py             # 政策爬虫 (工信部/发改委/财政部)
│   │   └── reader.py              # 政策信号提取
│   ├── search_agent/
│   │   └── searcher.py            # 公司业务搜索缓存
│   ├── llm/
│   │   ├── client.py              # DeepSeek 客户端
│   │   └── strategy_parser.py     # NL→策略解析
│   ├── api/                       # API 路由
│   │   ├── strategy_routes.py     # /api/strategy/*
│   │   ├── agent_routes.py        # /api/agent/*
│   │   ├── market_routes.py       # /api/market/*
│   │   ├── backtest_routes.py     # /api/backtest/*
│   │   ├── simulation_routes.py   # /api/simulation/*
│   │   ├── company_routes.py      # /api/company/*
│   │   └── policy_routes.py       # /api/policy/*
│   └── db/
│       ├── schema.py              # 建表 DDL + 种子数据
│       └── models.py              # Pydantic 模型
├── frontend/
│   └── src/
│       ├── App.vue                # 导航布局 + 全局 CSS 变量
│       ├── router/index.js        # 6 个路由
│       ├── api/index.js           # Axios API 封装
│       └── views/
│           ├── Dashboard.vue      # 大盘看板
│           ├── AIChat.vue         # AI 选股
│           ├── StockViewer.vue    # K线分析（多K线对比 + 保存tab）
│           ├── Backtest.vue       # 策略回测
│           ├── Simulation.vue     # ★ 模拟交易
│           └── AgentDetail.vue    # Agent 详情
├── data/
│   ├── daily/                     # 个股日线 CSV (3400+ 文件)
│   ├── index/                     # 上证指数 CSV
│   ├── company_business/          # 公司业务 MD 缓存
│   ├── policy_docs/               # 政策文件 MD (工信部/发改委/财政部)
│   └── stock_basic_cache.csv      # 股票基本信息
├── logs/
│   └── backtest/                  # 回测日志 JSON
├── scripts/
│   └── fetch_full_history.py      # 全量数据下载脚本
├── requirements.txt
└── DEVELOPMENT_REVIEW.md          # ★ 本文档
```

---

## 3. API 端点全览

### 策略 `/api/strategy`
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/builtin` | 注册策略列表 (13个) |
| POST | `/select` | NL 选股 |
| POST | `/run` | 按名称运行策略 |
| POST | `/select-stream` | SSE 流式 NL 选股 |

### Agent `/api/agent`
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/list` | Agent 列表 (含资产汇总) |
| GET | `/comparison` | 绩效对比 (净值曲线+盈亏日历) |
| GET | `/{id}` | Agent 详情 (持仓/交易/订单) |
| POST | `/create` | 创建 Agent |
| DELETE | `/{id}` | 删除 |
| PUT | `/{id}/rename` | 重命名 |
| PUT | `/{id}/configure` | 配置风控/策略 |
| POST | `/{id}/simulate` | 触发撮合 |
| PUT | `/{id}/status` | 启用/禁用 |
| GET | `/{id}/reports` | 日报列表 |
| GET | `/{id}/reports/{date}` | 日报 MD 内容 |

### 模拟交易 `/api/simulation`
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/start` | 启动模拟 (后台线程) |
| GET | `/status/{id}` | 轮询进度 |
| GET | `/result/{id}` | 完整结果 |
| GET | `/tasks` | 历史列表 |
| DELETE | `/task/{id}` | 删除 |

### 回测 `/api/backtest`
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/periods` | 预设周期 |
| POST | `/run` | 运行回测 |
| GET | `/quick/{strategy}` | 快捷回测 |
| GET | `/tasks` | 历史列表 |
| GET | `/task/{id}` | 详情 |
| DELETE | `/task/{id}` | 删除 |

### 行情 `/api/market`
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/stocks/search` | 代码/名称模糊搜索 |
| GET | `/index` | 上证指数N天 |
| GET | `/sector-heat` | 板块热度 |
| GET | `/stock/kline/{code}` | 个股K线+均线+持仓 |

### 公司 `/api/company`， 政策 `/api/policy`

---

## 4. 策略清单 (13个)

每个策略继承 `BaseStrategy`，实现 `filter(ts_code, name, df) -> StrategyResult | None`。
`StrategyResult` 包含: `ts_code, name, reason, score, extra`。

| 策略名 | lookback | 类型 | 核心逻辑 |
|--------|----------|------|---------|
| `momentum` | 10 | 短线 | 连板检测+换手率阶段分类（一字板/换手板/高位风险） |
| `yang_three_yin` | 20 | 短线 | 大阳→3缩量小阴→确认阳线形态识别 |
| `ma_cross` | 30 | 短线 | MA5上穿MA20金叉+量>均量1.2x |
| `volume_pullback` | 30 | 短线 | 缩量(<0.7x)回踩MA5/MA10(偏离<2%)，均线多头排列 |
| `kline_pullback` | 30 | 中线 | 从高点回落至MA20±3%，缩量后放量反弹 |
| `ma_pullback` | 30 | 中线 | 20/60双均线回调+缩量企稳/放量反弹 |
| `trend` | 60 | 中线 | 多波涨停聚类+波间回调分析+换手率健康度 |
| `consolidation` | 60 | 中线 | 12-25天振幅<5%+缩量→放量1.3x+涨幅>4%突破 |
| `box_range` | 60 | 中线 | 箱体高低点(振幅8-30%)，箱底附近(位置<35%)买入 |
| `sentiment_cycle` | 60 | 中线 | 换手率25%分位以下+价格在MA20附近→逆情绪布局 |
| `macd` | 120 | 中线 | DIF上穿DEA金叉+柱强度>5%+close>MA20 |
| `bottom_reversal` | 120 | 中线 | 60天跌幅>20%+近期量>均量1.3x+价格不再创新低 |
| `uptrend` | 250 | 长线 | MA多头排列(close>MA60>MA120)+高/低点上移+趋势斜率>5% |

---

## 5. 模拟交易框架 (核心新功能)

### 5.1 架构

```
API POST /api/simulation/start
  → 后台线程 run_simulation()
    → _preload_stock_data()  // 预加载全部股票数据 (~15s)
    → for each trading_date:
        for each agent:
          → run_sim_agent_review()
            → _fetch_tool_data()  // 预取大盘/政策/策略信号
            → 组装完整 prompt
            → 单次 LLM 调用 (ChatOpenAI, temperature=0.3)
            → _parse_trade_plan()  // 解析 JSON 交易计划
          → _execute_order()  // 按收盘价撮合，T+1/涨跌停/费率
          → 记录决策日志 + 净值快照
    → 计算 metrics per agent
    → 保存到 simulation_task 表
```

### 5.2 关键设计决策

- **单次 LLM 调用**：预取所有数据注入 prompt，避免 LangChain create_agent 的多轮工具调用在 DeepSeek 上产生 `reasoning_content` 400 错误
- **时间隔离**：预加载数据后，工具使用 `df[df["trade_date"] <= trade_date]` 截断，政策文件按文件名日期过滤
- **数据复用**：`sim_tools.py` 的 `search_stocks_by_strategy` 在预加载模式直接迭代 `_preloaded` 字典，不再逐只读取 CSV
- **代码规范**：撮合时自动补全 `.SH`/`.SZ` 后缀，订单数量兜底 100 股

### 5.3 已知局限性

- 每个 agent 每天执行一次完整策略扫描 (遍历 3400+ 只股票)，3天×2agent 约 4 分钟
- 只有 `search_stocks_by_strategy` 使用了预加载数据的快速路径，其他工具仍可能每次读取 CSV
- LLM 生成订单的 quantity 字段偶尔为 0 或缺失，已加兜底 100 股
- 模拟结果中 decisions 的完整 LLM 分析文本存储在 `analysis` 字段，前端展开可查看

---

## 6. 数据库表

| 表名 | 说明 | 关键列 |
|------|------|--------|
| `stock_basic` | 股票基本信息 | ts_code, name, industry, sector |
| `kline_daily` | 日线K线 | ts_code, trade_date, OHLCV, ma5/10/20/60 |
| `agent_info` | Agent 配置 | name, agent_type, initial_capital, strategy_ids, risk_config, status |
| `agent_position` | 持仓 | agent_id, ts_code, quantity, avg_cost, current_price |
| `agent_order` | 条件单 | agent_id, ts_code, direction, order_type, quantity, price, status |
| `agent_trade_log` | 交易记录 | agent_id, ts_code, direction, quantity, price, commission, stamp_tax |
| `agent_daily_report` | 日报 | agent_id, trade_date, total_assets, daily_pnl, cumulative_return |
| `strategy_repository` | 策略仓库 | name, description, category, params_json |
| `backtest_task` | 回测任务 | strategy_name, start_date, end_date, metrics_json, equity_curve_json |
| `simulation_task` | ★ 模拟任务 | name, start_date, end_date, agents_config(JSON), results_json(JSON), status |

---

## 7. 前端路由

| 路由 | 组件 | 导航名 | 功能 |
|------|------|--------|------|
| `/` | Dashboard.vue | 大盘看板 | Agent卡片、上证K线、Agent净值趋势、政策动态(部门Tab)、盈亏日历 |
| `/chat` | AIChat.vue | AI 选股 | 策略按钮、NL流式选股、结果表格、K线弹窗、业务详情、Agent日报 |
| `/stock` | StockViewer.vue | K线分析 | 搜索+K线图(MA5/10/20/60+成交量)、多K线保存Tab、业务浮窗 |
| `/backtest` | Backtest.vue | 策略回测 | 策略/周期选择、指标卡片、净值曲线、交易明细表、BS点弹窗、回放日志 |
| `/simulation` | Simulation.vue | 模拟交易 | Agent配置面板、净值对比图、绩效表、决策回放(展开LLM分析) |
| `/agent/:id` | AgentDetail.vue | (隐藏) | Agent详情页 |

**全局特性**：`<keep-alive>` 保持 tab 切换状态、`<router-link>` 活跃态高亮。

---

## 8. 交易规则常量

| 规则 | 值 |
|------|-----|
| 初始资金 | 150,000 元/Agent |
| 佣金 | 万 0.854 双向 |
| 印花税 | 万 5 卖出单向 |
| T+1 | 今日买入明日可卖 |
| 主板限制 | 仅 60/00 开头 |
| 最大持仓 | 5 只 |
| 止损线 | -8% (回测中) |
| 涨跌停阈值 | ±9.9% |

---

## 9. 代码 Review 要点

### 策略层
- 所有策略是否正确处理 ST/退市股票过滤
- `compute_macd()` 中 `NaN` 值是否被策略正确跳过
- `recommended_lookback` 与实际 `len(df)` 检查是否一致
- 评分公式是否存在除零风险

### 模拟引擎
- `sim_tools.py` 中 `_preloaded` 直接引用 DataFrame（非 copy），确认策略 filter 不会修改 df
- `sim_engine.py` 中 `_execute_order` 的 T+1 检查使用了 `buy_date >= trade_date`，字符串比较在跨月时是否可靠
- `sim_agent_runner.py` 的 `_parse_trade_plan` 是否对 LLM 输出的各种格式变体都有处理
- 后台线程错误是否被正确捕获并写入 DB

### 前端
- `Simulation.vue` 的 `expandedSet` 使用 `new Set()` 触发响应式，是否有更优雅的方式
- `Backtest.vue` BS 弹窗的双重 `nextTick` 是否在所有浏览器中都可靠
- `StockViewer.vue` 的多 K 线对比模式尚未实现（只在计划中）

### 性能
- 模拟预加载 `_preload_stock_data` 读取 3400+ CSV 文件，约 15s，可考虑用 `kline_daily` 表替代
- `_get_day_price` 每次都做 `df[df["trade_date"] == trade_date]` 筛选，可改用索引加速
- 政策爬虫深层抓取每次间隔 1s，10 个文档 = 10s，可考虑并行

---

## 10. TODO / 已知问题

- [ ] 模拟交易进度条不更新（始终显示 0% → 100%），需要中间的进度更新
- [ ] 个股 K 线多图并排对比模式（已在 StockViewer.vue 中有保存 tab，但对比模式未实现）
- [ ] 策略信号中混入了指数代码（如 000003.SH 上证B股指数），需要在 `list_main_board_stocks` 或策略中过滤
- [ ] 实盘 Agent 流水线（daily_pipeline）尚未实际运行过，数据表为空
- [ ] ML/DL 量化模型接口已定义但无实际实现
- [ ] 后端反复重启时可能有僵尸进程占用 18000 端口，需 `lsof -ti :18000 | xargs kill -9`
- [ ] 前端 Vite 构建需在 `frontend/` 目录下执行，否则报 `Cannot resolve entry module index.html`

---

## 11. Git 提交建议

```
feat: 13策略+LLM Agent模拟交易框架+ML量化接口

策略层:
- 新增10个策略 (MA金叉/MACD/K线回踩/横盘突破/长期趋势/底部反转/箱体/情绪/缩量回踩短均/一阳夹三阴)
- BaseStrategy 增加 recommended_lookback 类属性
- 新增 MACD/EMA 指标计算到 indicators.py

模拟交易:
- 新增 simulation/ 模块：时间隔离工具 + 单次LLM调用决策 + 多Agent并行模拟
- sim_tools.py: 6个时间感知工具，预加载数据快速路径
- sim_agent_runner.py: 单次LLM调用模式，规避DeepSeek reasoning_content多轮问题
- sim_engine.py: 逐日循环+订单撮合(代码后缀补全/T+1/涨跌停/费率)
- 新增 simulation_task 表和 /api/simulation/* 端点
- 新增 Simulation.vue 前端页面 (Agent配置/净值对比/决策回放)

ML量化接口:
- 新增 quant/ 模块: BaseQuantModel + QuantPrediction + QuantModelRegistry

前端优化:
- <keep-alive> 保持 tab 切换状态
- Backtest.vue: BS点K线弹窗 + 交易日志回放 + 历史任务
- Dashboard.vue: Agent净值趋势线 + 盈亏日历 + 政策部门Tab
- StockViewer.vue: 多K线保存Tab
- 新增 /simulation 路由和导航

修复:
- DeepSeek thinking mode 多轮400错误 → 单次LLM调用
- K线图默认缩放至最近1月 (dataZoom start=90)
- BS弹窗chart div竞态条件 (重试循环)
```

---

*本文档由 Claude Code 在 2026-05-10 自动生成，基于 `/home/xulu/stock_run_88/` 实际代码状态。*
