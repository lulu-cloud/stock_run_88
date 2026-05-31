<template>
  <div v-if="loadError" class="card error-card">
    <router-link to="/" class="btn btn-sm" style="margin-bottom:8px;display:inline-block;">← 返回</router-link>
    <h3>Agent 加载失败</h3>
    <p>{{ loadError }}</p>
  </div>
  <div v-else-if="loading" class="card">
    正在加载 Agent...
  </div>
  <div v-else-if="agent">
    <div class="detail-header">
      <router-link to="/" class="btn btn-sm" style="margin-bottom:8px;display:inline-block;">← 返回</router-link>
      <h2>{{ agent.display_name }} <span class="type-badge">{{ agent.agent_type }}</span></h2>
    </div>

    <!-- Stats -->
    <div class="grid-4" style="margin-bottom:16px;">
      <div class="stat-card"><span class="slabel">可用资金</span><span class="sval">{{ money(agent.current_cash) }}</span></div>
      <div class="stat-card"><span class="slabel">冻结资金</span><span class="sval">{{ money(agent.frozen_cash || 0) }}</span></div>
      <div class="stat-card"><span class="slabel">持仓市值</span><span class="sval">{{ money(agent.market_value || 0) }}</span></div>
      <div class="stat-card"><span class="slabel">总资产</span><span class="sval">{{ money(agent.total_assets) }}</span></div>
      <div class="stat-card"><span class="slabel">本金</span><span class="sval">{{ money(agent.initial_capital) }}</span></div>
      <div class="stat-card">
        <span class="slabel">浮动盈亏</span>
        <span class="sval" :class="(agent.unrealized_pnl || 0) >= 0 ? 'green' : 'red'">
          {{ signedMoney(agent.unrealized_pnl || 0) }}
        </span>
      </div>
      <div class="stat-card">
        <span class="slabel">累计收益率</span>
        <span class="sval" :class="agent.cumulative_return >= 0 ? 'green' : 'red'">
          {{ agent.cumulative_return >= 0 ? '+' : '' }}{{ agent.cumulative_return }}%
        </span>
      </div>
    </div>

    <div class="detail-tabs">
      <button v-for="tab in tabs" :key="tab.key" class="tab-btn" :class="{ active: activeTab === tab.key }" @click="activeTab = tab.key">
        {{ tab.label }}
      </button>
    </div>

    <div v-if="activeTab === 'config'" class="card" style="margin-bottom:16px;">
      <h3>Agent 配置</h3>
      <div class="config-row">
        <label>状态</label>
        <select v-model="statusForm.status">
          <option value="active">启用</option>
          <option value="paused">暂停</option>
          <option value="disabled">禁用</option>
        </select>
        <label>每日复盘</label>
        <input type="checkbox" v-model="scheduleForm.enabled" />
        <label>复盘时间</label>
        <input v-model="scheduleForm.review_time" placeholder="21:00" />
        <label>推送时间</label>
        <input v-model="scheduleForm.push_time" placeholder="16:00" />
        <label>工具轮数</label>
        <input v-model.number="riskForm.max_tool_turns" type="number" min="2" max="50" />
        <label>推理强度</label>
        <select v-model="riskForm.reasoning_effort">
          <option value="high">high</option>
          <option value="max">max</option>
        </select>
        <button class="btn btn-sm btn-primary" @click="saveConfig">保存配置</button>
      </div>
      <div class="agent-config-board">
        <div class="config-panel">
          <div class="cfg-title">Agent风格提示词</div>
          <p class="small-muted">这是当前 Agent 的人格/交易风格，会注入每日决策提示词。</p>
          <textarea v-model="riskForm.style_prompt" rows="5" class="wide-textarea"></textarea>
        </div>
        <div class="config-panel">
          <div class="cfg-title">用户原始交易策略</div>
          <p class="small-muted">这段文本作为用户风格锚点单独留痕，进化只能补充执行细节，不能覆盖原文。</p>
          <textarea v-model="riskForm.user_strategy_original" rows="5" class="wide-textarea" placeholder="例如：只做多头均线发散、回踩20日线企稳、放量重新上攻的股票；不追一字板..."></textarea>
          <div class="strategy-history" v-if="strategyVersions.length">
            <details>
              <summary>历史版本 {{ strategyVersions.length }} 条</summary>
              <div v-for="v in strategyVersions.slice(0,5)" :key="v.id" class="strategy-version">
                <span class="mono">v{{ v.version_no }} · {{ v.created_at }}</span>
                <p>{{ v.strategy_text }}</p>
              </div>
            </details>
          </div>
        </div>
        <div class="config-panel stock-pool-panel">
          <div class="cfg-title">前端股票池</div>
          <p class="small-muted">启用后，买入默认只能来自该股票池；卖出现有持仓不受限制。允许池外探索时，Agent 必须在理由里说明为什么突破股票池。</p>
          <div class="switch-row">
            <label><input type="checkbox" v-model="riskForm.stock_pool_enabled" /> 启用股票池约束</label>
            <label><input type="checkbox" v-model="riskForm.allow_out_of_pool" /> 允许池外探索</label>
          </div>
          <div class="pool-search-row">
            <input v-model="stockSearchText" placeholder="搜索股票代码或名称，如 京东方 / 000725" @keyup.enter="searchPoolStocks" />
            <button class="btn btn-sm" @click="searchPoolStocks">搜索</button>
          </div>
          <div class="stock-search-results" v-if="stockSearchResults.length">
            <button v-for="s in stockSearchResults" :key="s.ts_code" class="stock-result" @click="addPoolStock(s)">
              <span class="mono">{{ s.ts_code }}</span>
              <b>{{ s.name }}</b>
              <small>{{ s.sector_tag || '-' }} / {{ s.industry_tag || '-' }}</small>
            </button>
          </div>
          <div class="pool-add-row">
            <input v-model="newPoolItem.ts_code" placeholder="手动代码，如 600000.SH" />
            <input v-model="newPoolItem.stock_name" placeholder="名称可空" />
            <input v-model="newPoolItem.note" placeholder="备注，如 用户核心观察" />
            <button class="btn btn-sm" @click="addPoolItem">手动加入</button>
          </div>
          <textarea v-model="poolBulkText" rows="3" class="wide-textarea" placeholder="批量导入：每行一个股票代码，可写：600000.SH 浦发银行 备注"></textarea>
          <div class="tool-actions">
            <button class="btn btn-sm" @click="importPoolBulk">批量导入</button>
            <button class="btn btn-sm" @click="saveStockPool">保存股票池</button>
          </div>
          <table class="pool-table">
            <thead><tr><th>启用</th><th>代码</th><th>名称</th><th>备注</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="item in stockPool" :key="item.ts_code">
                <td><input type="checkbox" v-model="item.enabled" /></td>
                <td class="mono">{{ item.ts_code }}</td>
                <td><input v-model="item.stock_name" placeholder="-" /></td>
                <td><input v-model="item.note" placeholder="-" /></td>
                <td><button class="btn btn-sm btn-danger" @click="removePoolItem(item.ts_code)">删除</button></td>
              </tr>
              <tr v-if="!stockPool.length"><td colspan="5" class="empty-row">暂无股票池。启用约束且股票池为空时，Agent 只能卖出不能新买入。</td></tr>
            </tbody>
          </table>
        </div>
        <div class="config-panel">
          <div class="cfg-title">买入板块权限</div>
          <p class="small-muted">自动模式按资产与首笔交易自然日解锁；手动模式以前端开关为准。</p>
          <select v-model="riskForm.board_permission_mode">
            <option value="auto">自动解锁</option>
            <option value="manual">手动配置</option>
          </select>
          <div class="board-switches">
            <label><input type="checkbox" v-model="riskForm.board_permissions.main_sme" disabled /> 主板/中小板</label>
            <label><input type="checkbox" v-model="riskForm.board_permissions.chinext" :disabled="riskForm.board_permission_mode==='auto'" /> 创业板</label>
            <label><input type="checkbox" v-model="riskForm.board_permissions.star" :disabled="riskForm.board_permission_mode==='auto'" /> 科创板</label>
            <label><input type="checkbox" v-model="riskForm.board_permissions.bj" :disabled="riskForm.board_permission_mode==='auto'" /> 北交所</label>
          </div>
        </div>
        <div class="config-panel">
          <div class="cfg-title">优先选股策略</div>
          <p class="small-muted">可多选。这里是偏好顺序/候选策略，不是唯一硬约束；股票池约束由上方开关决定。</p>
          <div class="strategy-checks">
            <label v-for="s in strategyOptions" :key="s.name" class="strategy-check" :title="s.description">
              <input type="checkbox" v-model="riskForm.preferred_strategies" :value="s.name" />
              <span><b>{{ s.name }}</b><em>{{ s.description }}</em></span>
            </label>
          </div>
        </div>
        <div class="config-panel">
          <div class="cfg-title">严格工具白名单</div>
          <div class="tool-actions">
            <button class="btn btn-sm" @click="selectAllTools">全选工具</button>
            <button class="btn btn-sm" @click="selectCoreTools">核心工具</button>
          </div>
          <div class="tool-groups">
            <div v-for="group in groupedTools" :key="group.category" class="tool-group">
              <b>{{ group.category }}</b>
              <label v-for="t in group.tools" :key="t.name" class="tool-check" :title="t.description">
                <input type="checkbox" v-model="riskForm.allowed_tools" :value="t.name" :disabled="t.mandatory" />
                <span>{{ t.name }}</span>
              </label>
            </div>
          </div>
        </div>
        <div class="config-panel stage-panel">
          <div class="cfg-title">各阶段提示词</div>
          <div class="stage-grid">
            <label v-for="item in stagePromptFields" :key="item.key">
              <span>{{ item.label }}</span>
              <textarea v-model="riskForm.stage_prompts[item.key]" rows="2"></textarea>
            </label>
          </div>
        </div>
      </div>
    </div>

    <div v-if="activeTab === 'evolution'" class="grid-2" style="margin-bottom:16px;">
      <div class="card">
        <div class="card-header-row">
          <h3>赛马指标</h3>
          <button class="btn btn-sm" @click="loadEvolution">刷新</button>
        </div>
        <div class="metric-grid">
          <div><span class="slabel">赛马分</span><strong>{{ num(agent.race_metric?.race_score) }}</strong></div>
          <div><span class="slabel">最大回撤</span><strong>{{ num(agent.race_metric?.max_drawdown) }}%</strong></div>
          <div><span class="slabel">夏普</span><strong>{{ num(agent.race_metric?.sharpe_ratio) }}</strong></div>
          <div><span class="slabel">风格</span><strong>{{ agent.race_metric?.style_tag || '-' }}</strong></div>
          <div><span class="slabel">说明</span><strong>仅评价提示</strong></div>
          <div><span class="slabel">建议</span><strong>{{ agent.capital_policy?.disabled_reason || '无' }}</strong></div>
        </div>
        <div class="metric-help">
          赛马分综合超额收益、回撤、胜率、盈亏比、夏普与Alpha；最大回撤衡量权益高点到低点的跌幅；夏普衡量收益相对波动。指标只进入提示词和看板，不会触发代码强制仓位限制。
        </div>
      </div>
      <div class="card">
        <h3>技能置信度</h3>
        <div class="skill-list">
          <div v-for="s in agent.skills || []" :key="s.skill_id" class="skill-row">
            <span>{{ s.skill_name }}</span>
            <b>{{ num(s.confidence_score) }}</b>
            <small>失败率 {{ num(s.recent_fail_rate) }}</small>
          </div>
          <div v-if="!(agent.skills || []).length" class="empty-row">暂无技能数据</div>
        </div>
      </div>
    </div>

    <div v-if="activeTab === 'evolution'" class="card" style="margin-bottom:16px;">
      <div class="card-header-row">
        <h3>交易体系文档</h3>
        <button class="btn btn-primary btn-sm" @click="runReflection" :disabled="reflectionRunning">
          {{ reflectionRunning ? '反思中...' : '手动反思' }}
        </button>
      </div>
      <pre class="system-doc">{{ systemDoc.system_doc || '暂无交易体系文档' }}</pre>
    </div>

    <div v-if="activeTab === 'evolution'" class="card" style="margin-bottom:16px;">
      <h3>进化时间轴</h3>
      <div class="timeline">
        <div v-for="item in timeline" :key="`${item.kind}-${item.id || item.created_at}`" class="timeline-item">
          <span class="mono">{{ item.trade_date }}</span>
          <b>{{ item.kind === 'reflection' ? item.task_type : '日终进化' }}</b>
          <span>{{ item.summary || item.trigger_reason || item.status }}</span>
        </div>
        <div v-if="!timeline.length" class="empty-row">暂无进化记录</div>
      </div>
    </div>

    <div v-if="activeTab === 'prompt'" class="card" style="margin-bottom:16px;">
      <div class="card-header-row">
        <h3>提示词预览</h3>
        <button class="btn btn-sm" @click="loadPromptPreview">刷新</button>
      </div>
      <div class="prompt-grid">
        <div>
          <div class="cfg-title">系统提示词</div>
          <pre class="system-doc">{{ promptPreview.system_prompt || '暂无' }}</pre>
        </div>
        <div>
          <div class="cfg-title">当日上下文 / 配置注入</div>
          <pre class="system-doc">{{ pretty(promptPreview.daily_context || promptPreview.agent_config || {}) }}</pre>
        </div>
        <div>
          <div class="cfg-title">进化记忆</div>
          <pre class="system-doc">{{ promptPreview.evolution_prompt || '暂无' }}</pre>
        </div>
        <div>
          <div class="cfg-title">交易体系摘要</div>
          <pre class="system-doc">{{ promptPreview.system_doc || '暂无' }}</pre>
        </div>
      </div>
    </div>

    <div v-if="activeTab === 'eval'" class="card" style="margin-bottom:16px;">
      <div class="card-header-row">
        <h3>评估与成本</h3>
        <button class="btn btn-sm" @click="loadEval">刷新</button>
      </div>
      <div class="eval-help">
        Alpha 表示 Agent 累计收益相对同期上证指数收益的超额部分；这里是近似指标，用来观察是否靠自身策略跑赢大盘，而不是风险调整后的严格金融 Alpha。
        工具轮数是 LLM ReAct 轮次上限；工具调用次数是实际 tool call 数，一轮 LLM 可能并发调用多个工具，所以工具调用次数可能大于工具轮数。
      </div>
      <div class="eval-metric-cards">
        <div v-for="metric in evalMetricCards" :key="metric.key" class="eval-metric-card">
          <div class="eval-card-head">
            <span class="slabel">{{ metric.label }}</span>
            <strong>{{ formatMetric(metric.latest, metric) }}</strong>
          </div>
          <div class="eval-card-grid">
            <div><span>{{ metric.mode === 'sum' ? '累计' : '全期均' }}</span><b>{{ formatMetric(metric.total, metric) }}</b></div>
            <div><span>近3日均</span><b>{{ formatMetric(metric.avg3, metric) }}</b></div>
            <div><span>环比前3日</span><b :class="Number.isFinite(metric.wow) ? (metric.wow >= 0 ? 'green' : 'red') : ''">{{ formatChange(metric.wow) }}</b></div>
          </div>
        </div>
      </div>
      <table>
        <thead><tr><th>日期</th><th>日收益</th><th>超额</th><th>Alpha</th><th>回撤</th><th>波动</th><th>成交率</th><th>Token</th><th>LLM/工具</th><th>耗时</th></tr></thead>
        <tbody>
          <tr v-for="item in evalItems" :key="item.id">
            <td class="mono">{{ item.trade_date }}</td>
            <td class="mono" :class="item.daily_return >= 0 ? 'green' : 'red'">{{ num(item.daily_return) }}%</td>
            <td class="mono">{{ num(item.excess_return) }}%</td>
            <td class="mono">{{ num(item.alpha_score) }}</td>
            <td class="mono">{{ num(item.max_drawdown) }}%</td>
            <td class="mono">{{ num(item.volatility) }}</td>
            <td class="mono">{{ num(item.order_fill_rate) }}%</td>
            <td class="mono">{{ item.total_tokens ?? '-' }}</td>
            <td class="mono">{{ item.llm_calls || 0 }}/{{ item.tool_calls || 0 }}，失败{{ item.tool_failures || 0 }}</td>
            <td class="mono">{{ num(item.decision_latency_ms) }}ms</td>
          </tr>
          <tr v-if="!evalItems.length"><td colspan="10" class="empty-row">暂无评估数据</td></tr>
        </tbody>
      </table>
    </div>

    <div v-if="activeTab === 'ordertrace'" class="card" style="margin-bottom:16px;">
      <div class="card-header-row">
        <h3>决策批次</h3>
        <button class="btn btn-sm" @click="loadOrderTrace">刷新</button>
      </div>
      <table style="margin-bottom:16px;">
        <thead><tr><th>批次</th><th>复盘日</th><th>交易日</th><th>订单</th><th>买/卖</th><th>估计成交率</th><th>实际成交率</th><th>摘要</th></tr></thead>
        <tbody>
          <tr v-for="batch in decisionBatches" :key="batch.id">
            <td class="mono">{{ batch.id }}</td>
            <td class="mono">{{ batch.trade_date }}</td>
            <td class="mono">{{ batch.next_trade_date || '-' }}</td>
            <td class="mono">{{ batch.order_count || 0 }}</td>
            <td class="mono">{{ batch.buy_count || 0 }} / {{ batch.sell_count || 0 }}</td>
            <td class="mono">{{ pct(batch.avg_fill_probability) }}</td>
            <td class="mono">{{ pct(batch.fill_rate) }}</td>
            <td class="batch-summary">{{ batch.summary || '-' }}</td>
          </tr>
          <tr v-if="!decisionBatches.length"><td colspan="8" class="empty-row">暂无决策批次，新复盘会自动写入</td></tr>
        </tbody>
      </table>
      <h3>订单 Trace</h3>
      <table>
        <thead><tr><th>时间</th><th>订单</th><th>交易日</th><th>事件</th><th>状态</th><th>原因</th></tr></thead>
        <tbody>
          <tr v-for="item in orderTraceItems" :key="item.id">
            <td class="mono">{{ item.created_at }}</td>
            <td class="mono">#{{ item.order_id }}</td>
            <td class="mono">{{ item.trade_date || '-' }}</td>
            <td>{{ traceEventLabel(item.event_type) }}</td>
            <td class="mono">{{ item.status_from || '-' }} → {{ item.status_to || '-' }}</td>
            <td>{{ item.reason || '-' }}</td>
          </tr>
          <tr v-if="!orderTraceItems.length"><td colspan="6" class="empty-row">暂无订单生命周期记录，新订单会自动写入 trace</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Holdings -->
    <div class="card" style="margin-bottom:16px;">
      <h3>当前持仓</h3>
      <table>
        <thead><tr><th>代码</th><th>名称</th><th>数量</th><th>成本</th><th>现价</th><th>市值</th><th>浮动盈亏</th><th>K线</th></tr></thead>
        <tbody>
          <tr v-for="p in positions" :key="p.ts_code">
            <td class="mono">{{ p.ts_code }}</td><td>{{ p.stock_name }}</td>
            <td class="mono">{{ p.quantity }}</td>
            <td class="mono">{{ p.avg_cost.toFixed(2) }}</td>
            <td class="mono">{{ p.current_price.toFixed(2) }}</td>
            <td class="mono">{{ money(p.market_value) }}</td>
            <td class="mono" :class="p.unrealized_pnl >= 0 ? 'green' : 'red'">{{ signedMoney(p.unrealized_pnl) }}</td>
            <td><router-link :to="`/stock?code=${p.ts_code}`" class="btn btn-sm">K线</router-link></td>
          </tr>
          <tr v-if="!positions.length"><td colspan="8" class="empty-row">空仓</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Trades -->
    <div class="card" style="margin-bottom:16px;">
      <h3>最近交易</h3>
      <table>
        <thead><tr><th>日期</th><th>股票</th><th>方向</th><th>数量</th><th>价格</th><th>金额</th><th>逻辑</th></tr></thead>
        <tbody>
          <tr v-for="t in trades" :key="t.id">
            <td class="mono">{{ t.trade_date }}</td>
            <td><span class="mono">{{ t.ts_code }}</span><span v-if="t.stock_name" class="stock-name">{{ t.stock_name }}</span></td>
            <td :class="t.direction === 'buy' ? 'green' : 'red'">{{ t.direction === 'buy' ? '买入' : '卖出' }}</td>
            <td class="mono">{{ t.quantity }}</td><td class="mono">{{ t.price.toFixed(2) }}</td>
            <td class="mono">{{ money(t.total_value) }}</td>
            <td><button class="btn btn-sm" @click="showLogic('trade', t)">查看</button></td>
          </tr>
          <tr v-if="!trades.length"><td colspan="7" class="empty-row">暂无交易</td></tr>
        </tbody>
      </table>
    </div>

    <!-- Pending Orders -->
    <div class="card">
      <h3>待触发条件单</h3>
      <table>
        <thead><tr><th>股票</th><th>方向</th><th>类型</th><th>数量</th><th>价格</th><th>成交概率</th><th>价格偏离</th><th>交易日</th><th>状态</th><th>逻辑</th></tr></thead>
        <tbody>
          <tr v-for="o in pendingOrders" :key="o.id">
            <td><span class="mono">{{ o.ts_code }}</span><span v-if="o.stock_name" class="stock-name">{{ o.stock_name }}</span></td>
            <td :class="o.direction === 'buy' ? 'green' : 'red'">{{ o.direction === 'buy' ? '买入' : '卖出' }}</td>
            <td>{{ o.order_type }}</td><td class="mono">{{ o.quantity }}</td>
            <td class="mono">{{ o.price.toFixed(2) }}</td>
            <td class="mono">{{ pct(o.fill_probability) }}</td>
            <td class="mono">{{ pct(o.price_aggressiveness) }}</td>
            <td class="mono">{{ o.trade_date }}</td><td>{{ o.status }}</td>
            <td><button class="btn btn-sm" @click="showLogic('order', o)">查看</button></td>
          </tr>
          <tr v-if="!pendingOrders.length"><td colspan="10" class="empty-row">无待触发条件单</td></tr>
        </tbody>
      </table>
    </div>

    <div v-if="logicPanel" class="logic-overlay" @click.self="logicPanel = null">
      <aside class="logic-drawer">
        <div class="card-header-row">
          <h3>{{ logicPanel.title }}</h3>
          <button class="btn btn-sm" @click="logicPanel = null">关闭</button>
        </div>
        <div class="logic-grid">
          <div><span class="slabel">股票</span><strong>{{ logicPanel.item.ts_code }} {{ logicPanel.item.stock_name || '' }}</strong></div>
          <div><span class="slabel">方向</span><strong>{{ logicPanel.item.direction === 'buy' ? '买入' : '卖出' }}</strong></div>
          <div><span class="slabel">类型</span><strong>{{ logicPanel.item.order_type || '-' }}</strong></div>
          <div><span class="slabel">技能</span><strong>{{ logicPanel.item.skill_id || '-' }} {{ logicPanel.item.skill_confidence ? num(logicPanel.item.skill_confidence) : '' }}</strong></div>
          <div><span class="slabel">决策批次</span><strong>{{ logicPanel.item.decision_batch_id || '-' }}</strong></div>
          <div><span class="slabel">估计成交概率</span><strong>{{ pct(logicPanel.item.fill_probability) }}</strong></div>
          <div><span class="slabel">价格偏离</span><strong>{{ pct(logicPanel.item.price_aggressiveness) }}</strong></div>
          <div><span class="slabel">开盘抢入/出</span><strong>{{ logicPanel.item.open_get_in ? '是' : '否' }}</strong></div>
          <div><span class="slabel">股票池状态</span><strong>{{ poolStatusLabel(logicPanel.item.pool_status) }}</strong></div>
        </div>
        <div class="logic-section" v-if="logicPanel.item.out_of_pool_reason">
          <div class="cfg-title">池外探索理由</div>
          <p>{{ logicPanel.item.out_of_pool_reason }}</p>
        </div>
        <div class="logic-section">
          <div class="cfg-title">买入/卖出逻辑</div>
          <p>{{ logicPanel.item.reason || '该记录没有保存下单理由。' }}</p>
        </div>
        <div class="logic-section" v-if="logicPanel.item.evolution_mark || logicPanel.item.fail_reason">
          <div class="cfg-title">进化标记 / 失败原因</div>
          <p>{{ logicPanel.item.evolution_mark || logicPanel.item.fail_reason }}</p>
        </div>
        <div class="logic-section">
          <div class="cfg-title">订单生命周期</div>
          <div v-if="(logicPanel.item.order_trace || []).length" class="trace-list">
            <div v-for="ev in logicPanel.item.order_trace" :key="ev.id" class="trace-item">
              <span class="mono">{{ ev.created_at }}</span>
              <b>{{ traceEventLabel(ev.event_type) }}</b>
              <em>{{ ev.status_from || '-' }} → {{ ev.status_to || '-' }}</em>
              <p>{{ ev.reason || tracePayloadText(ev.payload) || '-' }}</p>
            </div>
          </div>
          <p v-else>暂无生命周期 trace；该订单可能创建早于 trace 机制上线。</p>
        </div>
        <pre class="system-doc">{{ pretty(logicPanel.item) }}</pre>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { agentAPI, strategyAPI, marketAPI } from '../api'

const route = useRoute()
const agent = ref(null)
const loading = ref(false)
const loadError = ref('')
const positions = ref([])
const trades = ref([])
const pendingOrders = ref([])
const orderTraceItems = ref([])
const decisionBatches = ref([])
const timeline = ref([])
const systemDoc = ref({})
const promptPreview = ref({})
const evalItems = ref([])
const costItems = ref([])
const latestEval = computed(() => evalItems.value[0] || agent.value?.eval_summary || {})
const evalMetricDefs = [
  { key: 'total_tokens', label: 'Token', mode: 'sum', decimals: 0, suffix: '' },
  { key: 'decision_latency_ms', label: '决策耗时', mode: 'avg', decimals: 0, suffix: 'ms' },
  { key: 'llm_calls', label: 'LLM轮次', mode: 'sum', decimals: 0, suffix: '' },
  { key: 'tool_calls', label: '工具调用', mode: 'sum', decimals: 0, suffix: '' },
  { key: 'tool_failure_rate', label: '工具失败率', mode: 'avg', decimals: 2, suffix: '%' },
  { key: 'json_repairs', label: 'JSON/价格修复', mode: 'sum', decimals: 0, suffix: '' },
  { key: 'alpha_score', label: 'Alpha', mode: 'avg', decimals: 2, suffix: '' },
]
const evalMetricCards = computed(() => {
  const rows = evalItems.value || []
  return evalMetricDefs.map(def => {
    const latest = metricValue(rows[0], def.key)
    const total = aggregateMetric(rows, def)
    const avg3 = aggregateMetric(rows.slice(0, 3), def, 'avg')
    const prev3 = aggregateMetric(rows.slice(3, 6), def, 'avg')
    return { ...def, latest, total, avg3, prev3, wow: changeRate(avg3, prev3) }
  })
})
const reflectionRunning = ref(false)
const logicPanel = ref(null)
const activeTab = ref('config')
const tabs = [
  { key: 'config', label: '基础配置' },
  { key: 'evolution', label: '进化记忆' },
  { key: 'prompt', label: '提示词预览' },
  { key: 'eval', label: '评估指标' },
  { key: 'ordertrace', label: '订单 Trace' },
]
const statusForm = ref({ status: 'active' })
const scheduleForm = ref({ enabled: false, review_time: '21:00', push_time: '16:00' })
const stockPool = ref([])
const strategyVersions = ref([])
const poolBulkText = ref('')
const newPoolItem = ref({ ts_code: '', stock_name: '', note: '', enabled: true })
const stockSearchText = ref('')
const stockSearchResults = ref([])
const toolCatalog = ref([])
const strategyOptions = ref([])
let detailRequestSeq = 0
const stagePromptFields = [
  { key: 'market_scan', label: '行情感知' },
  { key: 'stock_selection', label: '选股择时' },
  { key: 'risk_control', label: '风控仓位' },
  { key: 'order_plan', label: '订单规划' },
  { key: 'reflection', label: '复盘反思' },
]
const defaultStagePrompts = {
  market_scan: '先判断大盘、情绪周期、板块强弱，再决定是否降低交易频率。',
  stock_selection: '优先使用配置的选股策略；若候选与风格不匹配，需要说明放弃原因。',
  risk_control: '参考赛马指标、技能置信度和失败订单记录，自主决定仓位；系统不做盈亏驱动的强制仓位限制。',
  order_plan: '挂单价必须先计算涨跌幅并校验；换仓必须说明非原子顺序风险。',
  reflection: '复盘时把已验证规律写入记忆，删除没有证据的判断。',
}
const styleTemplates = {
  chaser: '追高打板情绪猎手：偏短线强势与情绪周期，允许研究连板、高开、主线加速和开盘抢入。必须严格解释情绪周期、封板质量、换手、炸板风险和次日离场条件。',
  autonomous: '全因子自主决策交易者：综合政策、基本面、技术、资金与情绪，不简单复制追高打板候选。需要说明与其他风格的差异化理由，仓位更分散，避免单一题材过度集中。',
  custom: '自定义交易员：按前端配置的策略偏好、工具白名单和阶段提示词执行。若配置不足，采用稳健均衡风格并清楚说明假设。',
}
const riskForm = ref({
  max_tool_turns: 8,
  reasoning_effort: 'high',
  style_prompt: styleTemplates.custom,
  preferred_strategies: [],
  allowed_tools: [],
  board_permission_mode: 'auto',
  board_permissions: { main_sme: true, chinext: false, star: false, bj: false },
  stock_pool_enabled: false,
  allow_out_of_pool: false,
  user_strategy_original: '',
  stage_prompts: { ...defaultStagePrompts },
})

const groupedTools = computed(() => {
  const groups = {}
  for (const t of toolCatalog.value) {
    const key = t.category || '其他'
    if (!groups[key]) groups[key] = []
    groups[key].push(t)
  }
  return Object.entries(groups).map(([category, tools]) => ({ category, tools }))
})

function money(value) {
  return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function signedMoney(value) {
  const n = Number(value || 0)
  return `${n >= 0 ? '+' : ''}${money(n)}`
}
function num(value) { return Number(value || 0).toFixed(2) }
function pct(value) { return value === null || value === undefined || value === '' ? '-' : `${Number(value).toFixed(2)}%` }
function pretty(value) { return JSON.stringify(value || {}, null, 2) }
function metricValue(row, key) {
  if (!row) return null
  if (key === 'json_repairs') return Number(row.json_parse_failures || 0) + Number(row.price_repair_count || 0)
  const value = row[key]
  return value === null || value === undefined || value === '' ? null : Number(value)
}
function aggregateMetric(rows, def, forcedMode = '') {
  const values = (rows || []).map(r => metricValue(r, def.key)).filter(v => Number.isFinite(v))
  if (!values.length) return null
  const mode = forcedMode || def.mode
  const sum = values.reduce((acc, v) => acc + v, 0)
  return mode === 'avg' ? sum / values.length : sum
}
function changeRate(current, previous) {
  if (!Number.isFinite(current) || !Number.isFinite(previous) || previous === 0) return null
  return (current - previous) / Math.abs(previous) * 100
}
function formatMetric(value, def) {
  if (!Number.isFinite(value)) return '-'
  const fixed = Number(value).toFixed(def.decimals ?? 2)
  return `${fixed}${def.suffix || ''}`
}
function formatChange(value) {
  if (!Number.isFinite(value)) return '-'
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}
async function showLogic(kind, item) {
  logicPanel.value = {
    title: kind === 'trade' ? '成交逻辑' : '条件单逻辑',
    item,
  }
  const orderId = item.order_id || item.id
  if (orderId && !(item.order_trace || []).length) {
    try {
      const res = await agentAPI.orderTrace(route.params.id, orderId)
      item.order_trace = res.data.items || []
    } catch (e) {}
  }
}
function traceEventLabel(event) {
  return ({
    created: '创建预操作单',
    matched: '价格触达',
    filled: '成交入账',
    expired: '未触达/过期',
    replaced: '被新复盘替换',
    stale_expired: '跨日自动取消',
    cancelled: '取消',
  })[event] || event || '-'
}
function tracePayloadText(payload) {
  if (!payload || typeof payload !== 'object') return ''
  if (payload.exec_price) return `成交参考价 ${Number(payload.exec_price).toFixed(2)}`
  if (payload.price) return `挂单价 ${Number(payload.price).toFixed(2)}`
  return ''
}
function poolStatusLabel(status) {
  return ({
    disabled: '未启用约束',
    in_pool: '股票池内',
    out_of_pool_explore: '池外探索',
    blocked_out_of_pool: '池外被拦截',
    position_exit: '持仓卖出',
    unknown: '未知',
  })[status] || status || '-'
}
function selectAllTools() {
  riskForm.value.allowed_tools = toolCatalog.value.map(t => t.name)
}
function selectCoreTools() {
  const core = new Set([
    'search_stocks_by_strategy', 'get_market_overview', 'get_stock_kline',
    'get_recent_order_history', 'calculate_price_by_pct', 'validate_order_price_limit',
    'get_evolution_context', 'get_strategy_param_schema',
  ])
  riskForm.value.allowed_tools = toolCatalog.value.filter(t => t.mandatory || core.has(t.name)).map(t => t.name)
}
function normalizePoolCode(code) {
  const text = String(code || '').trim().toUpperCase()
  if (!text) return ''
  if (text.includes('.')) return text
  return text.startsWith('6') ? `${text}.SH` : `${text}.SZ`
}
function addPoolItem() {
  const code = normalizePoolCode(newPoolItem.value.ts_code)
  if (!code) return
  const existing = stockPool.value.find(x => normalizePoolCode(x.ts_code) === code)
  if (existing) {
    existing.stock_name = newPoolItem.value.stock_name || existing.stock_name || ''
    existing.note = newPoolItem.value.note || existing.note || ''
    existing.enabled = true
  } else {
    stockPool.value.push({
      ts_code: code,
      stock_name: newPoolItem.value.stock_name || '',
      note: newPoolItem.value.note || '',
      enabled: true,
    })
  }
  newPoolItem.value = { ts_code: '', stock_name: '', note: '', enabled: true }
}
function addPoolStock(stock) {
  newPoolItem.value = {
    ts_code: stock.ts_code,
    stock_name: stock.name || '',
    note: '',
    enabled: true,
  }
  addPoolItem()
}
async function searchPoolStocks() {
  const q = String(stockSearchText.value || '').trim()
  if (!q) {
    stockSearchResults.value = []
    return
  }
  try {
    const res = await marketAPI.stockSearch(q)
    stockSearchResults.value = res.data.results || []
  } catch (e) {
    stockSearchResults.value = []
  }
}
function importPoolBulk() {
  const lines = String(poolBulkText.value || '').split(/\r?\n/).map(x => x.trim()).filter(Boolean)
  for (const line of lines) {
    const parts = line.split(/[\s,，]+/).filter(Boolean)
    const code = normalizePoolCode(parts[0])
    if (!code) continue
    const existing = stockPool.value.find(x => normalizePoolCode(x.ts_code) === code)
    const item = {
      ts_code: code,
      stock_name: parts[1] || '',
      note: parts.slice(2).join(' '),
      enabled: true,
    }
    if (existing) Object.assign(existing, { ...item, stock_name: item.stock_name || existing.stock_name })
    else stockPool.value.push(item)
  }
  poolBulkText.value = ''
}
function removePoolItem(tsCode) {
  const code = normalizePoolCode(tsCode)
  stockPool.value = stockPool.value.filter(x => normalizePoolCode(x.ts_code) !== code)
}
async function saveStockPool() {
  await agentAPI.replaceStockPool(route.params.id, stockPool.value)
  await loadStockPool()
}
async function loadStockPool() {
  const id = route.params.id
  if (!id || id === 'undefined') return
  try {
    const res = await agentAPI.stockPool(id)
    stockPool.value = (res.data.items || []).map(x => ({ ...x, enabled: !!x.enabled }))
    strategyVersions.value = res.data.strategy_versions || []
  } catch (e) {
    stockPool.value = []
    strategyVersions.value = []
  }
}

async function loadCatalog() {
  try {
    const [tools, strategies] = await Promise.all([agentAPI.tools(), strategyAPI.builtin()])
    toolCatalog.value = tools.data.tools || []
    strategyOptions.value = strategies.data.strategies || tools.data.strategies || []
  } catch (e) {}
}

async function loadDetail() {
  const id = route.params.id
  const seq = ++detailRequestSeq
  if (!id || id === 'undefined') {
    loadError.value = '无效的 Agent ID'
    agent.value = null
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    const res = await agentAPI.get(id)
    if (seq !== detailRequestSeq) return
    const d = res.data
    if (d.error) throw new Error(d.error)
    agent.value = d.agent
    positions.value = d.positions
    trades.value = d.trades
    pendingOrders.value = d.pending_orders
    statusForm.value.status = d.agent.status || 'active'
    scheduleForm.value = { enabled: false, review_time: '21:00', push_time: '16:00', ...(d.agent.schedule || {}) }
    const cfg = d.agent.risk_config || {}
    riskForm.value = {
      max_tool_turns: Number(cfg.max_tool_turns || 8),
      reasoning_effort: ['high', 'max'].includes(cfg.reasoning_effort) ? cfg.reasoning_effort : 'high',
      style_prompt: cfg.style_prompt || styleTemplates[d.agent.agent_type] || styleTemplates.custom,
      preferred_strategies: cfg.preferred_strategies || (d.agent.strategy_ids || '').split(',').filter(Boolean),
      allowed_tools: cfg.allowed_tools || toolCatalog.value.map(t => t.name),
      board_permission_mode: cfg.board_permission_mode || 'auto',
      board_permissions: {
        main_sme: true,
        chinext: false,
        star: false,
        bj: false,
        ...(cfg.board_permission_mode === 'auto' ? (d.agent.board_permissions_effective || {}) : (cfg.board_permissions || {})),
      },
      stock_pool_enabled: !!cfg.stock_pool_enabled,
      allow_out_of_pool: !!cfg.allow_out_of_pool,
      user_strategy_original: cfg.user_strategy_original || '',
      stage_prompts: { ...defaultStagePrompts, ...(cfg.stage_prompts || {}) },
    }
    stockPool.value = (d.agent.stock_pool || []).map(x => ({ ...x, enabled: !!x.enabled }))
    strategyVersions.value = d.agent.strategy_versions || []
    resetLazyTabData()
    if (activeTab.value === 'config') await loadStockPool()
    else await loadActiveTabData()
  } catch (err) {
    if (seq !== detailRequestSeq) return
    agent.value = null
    positions.value = []
    trades.value = []
    pendingOrders.value = []
    loadError.value = err.response?.data?.detail || err.message || '加载失败'
  } finally {
    if (seq === detailRequestSeq) loading.value = false
  }
}

function resetLazyTabData() {
  timeline.value = []
  systemDoc.value = {}
  promptPreview.value = {}
  evalItems.value = []
  costItems.value = []
  orderTraceItems.value = []
  decisionBatches.value = []
}

async function loadActiveTabData() {
  if (activeTab.value === 'config') return loadStockPool()
  if (activeTab.value === 'evolution') return loadEvolution()
  if (activeTab.value === 'prompt') return loadPromptPreview()
  if (activeTab.value === 'eval') return loadEval()
  if (activeTab.value === 'ordertrace') return loadOrderTrace()
}

async function loadEvolution() {
  const id = route.params.id
  if (!id || id === 'undefined') return
  try {
    const [tl, doc] = await Promise.all([
      agentAPI.evolutionTimeline(id, 30),
      agentAPI.systemDoc(id),
    ])
    timeline.value = tl.data.timeline || []
    systemDoc.value = doc.data || {}
  } catch (e) {}
}

async function loadEval() {
  const id = route.params.id
  if (!id || id === 'undefined') return
  try {
    const [ev, cost] = await Promise.all([
      agentAPI.eval(id, 365),
      agentAPI.cost(id, 30),
    ])
    evalItems.value = ev.data.items || []
    costItems.value = cost.data.items || []
  } catch (e) {}
}

async function loadOrderTrace() {
  const id = route.params.id
  if (!id || id === 'undefined') return
  try {
    const [traceRes, batchRes] = await Promise.all([
      agentAPI.orderTrace(id, '', 120),
      agentAPI.decisionBatches(id, 30),
    ])
    orderTraceItems.value = traceRes.data.items || []
    decisionBatches.value = batchRes.data.items || []
  } catch (e) {
    orderTraceItems.value = []
    decisionBatches.value = []
  }
}

async function loadPromptPreview() {
  const id = route.params.id
  if (!id || id === 'undefined') return
  try {
    const res = await agentAPI.promptPreview(id)
    promptPreview.value = res.data || {}
  } catch (e) {
    promptPreview.value = {}
  }
}

async function runReflection() {
  if (!agent.value) return
  reflectionRunning.value = true
  try {
    await agentAPI.runReflection(route.params.id, 'manual')
    await loadEvolution()
  } finally {
    reflectionRunning.value = false
  }
}

async function saveConfig() {
  if (!agent.value) return
  await agentAPI.setStatus(route.params.id, statusForm.value.status)
  await agentAPI.configure(route.params.id, {
    risk_config: {
      ...(agent.value.risk_config || {}),
      max_tool_turns: Math.max(2, Math.min(50, Number(riskForm.value.max_tool_turns || 8))),
      reasoning_effort: ['high', 'max'].includes(riskForm.value.reasoning_effort) ? riskForm.value.reasoning_effort : 'high',
      style_prompt: riskForm.value.style_prompt || '',
      preferred_strategies: riskForm.value.preferred_strategies || [],
      allowed_tools: riskForm.value.allowed_tools || [],
      board_permission_mode: riskForm.value.board_permission_mode || 'auto',
      board_permissions: riskForm.value.board_permissions || {},
      stock_pool_enabled: !!riskForm.value.stock_pool_enabled,
      allow_out_of_pool: !!riskForm.value.allow_out_of_pool,
      user_strategy_original: riskForm.value.user_strategy_original || '',
      stage_prompts: riskForm.value.stage_prompts || {},
    },
    strategy_ids: (riskForm.value.preferred_strategies || []).join(','),
  })
  await agentAPI.replaceStockPool(route.params.id, stockPool.value)
  await agentAPI.configureSchedule(route.params.id, scheduleForm.value)
  await loadDetail()
}

onMounted(async () => { await loadCatalog(); await loadDetail() })
watch(() => route.params.id, loadDetail)
watch(activeTab, () => {
  loadActiveTabData()
})
</script>

<style scoped>
.detail-header { margin-bottom: 16px; }
.detail-header h2 { font-family: var(--font-mono); font-size: 18px; }
.type-badge {
  font-size: 10px; color: var(--accent-gold); background: rgba(201,168,76,0.1);
  padding: 2px 8px; border-radius: 3px; margin-left: 8px; font-weight: 400;
}
.stat-card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 6px; padding: 14px 16px; text-align: center;
}
.slabel { display: block; font-size: 10px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 1px; }
.sval { font-family: var(--font-mono); font-size: 18px; font-weight: 600; margin-top: 4px; display: block; }
.sval small { font-size: 12px; font-weight: 400; color: var(--text-dim); }
.mono { font-family: var(--font-mono); }
.stock-name { display:block; margin-top:3px; color:var(--text-secondary); font-size:12px; }
.empty-row { text-align: center; color: var(--text-dim); padding: 20px; }
.config-row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; font-size:12px; }
.config-row label { color:var(--text-dim); }
.config-row input:not([type="checkbox"]), .config-row select { width:92px; font-size:12px; }
.agent-config-board { display:grid; grid-template-columns:1fr 1.4fr; gap:12px; margin-top:12px; }
.config-panel { border:1px solid var(--border); border-radius:8px; padding:12px; background:var(--bg-deep); min-width:0; }
.cfg-title { font-size:12px; font-weight:700; color:var(--text-primary); margin-bottom:8px; }
.multi-select { width:100%; min-height:160px; font-size:12px; }
.small-muted { color: var(--text-dim); font-size: 11px; font-family: var(--font-mono); }
.wide-textarea { width:100%; min-height:124px; resize:vertical; font-size:12px; }
.stock-pool-panel { grid-column:1 / -1; }
.switch-row { display:flex; gap:16px; flex-wrap:wrap; margin:10px 0; font-size:12px; color:var(--text-secondary); }
.switch-row label { display:flex; align-items:center; gap:6px; }
.pool-search-row { display:grid; grid-template-columns:1fr auto; gap:8px; margin:10px 0; }
.pool-search-row input { width:100%; font-size:12px; }
.stock-search-results { display:flex; gap:8px; flex-wrap:wrap; margin:8px 0 10px; }
.stock-result { border:1px solid var(--border); background:var(--bg-card); border-radius:6px; padding:7px 9px; cursor:pointer; display:flex; gap:6px; align-items:center; color:var(--text-secondary); font-size:12px; }
.stock-result:hover { border-color:var(--accent-gold); color:var(--text-primary); }
.stock-result b { color:var(--text-primary); }
.stock-result small { color:var(--text-dim); }
.pool-add-row { display:grid; grid-template-columns:150px 150px 1fr auto; gap:8px; margin:10px 0; }
.pool-add-row input, .pool-table input { width:100%; font-size:12px; }
.pool-table { margin-top:10px; }
.strategy-history { margin-top:10px; font-size:12px; color:var(--text-secondary); }
.strategy-version { border-top:1px solid var(--border); padding:8px 0; }
.strategy-version p { margin:4px 0 0; white-space:pre-wrap; line-height:1.6; color:var(--text-secondary); }
.strategy-checks { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; max-height:260px; overflow:auto; }
.strategy-check { display:flex; gap:8px; align-items:flex-start; border:1px solid var(--border); background:var(--bg-card); border-radius:6px; padding:8px; font-size:12px; min-width:0; }
.strategy-check span { min-width:0; }
.strategy-check b { display:block; color:var(--text-primary); }
.strategy-check em { display:block; color:var(--text-dim); font-style:normal; margin-top:3px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.board-switches { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin-top:10px; font-size:12px; color:var(--text-secondary); }
.board-switches label { display:flex; align-items:center; gap:6px; }
.tool-actions { display:flex; gap:6px; margin-bottom:8px; }
.tool-groups { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; max-height:260px; overflow:auto; }
.tool-group { border:1px solid var(--border); border-radius:6px; padding:8px; background:var(--bg-card); }
.tool-group b { display:block; font-size:11px; margin-bottom:6px; color:var(--accent-gold); }
.tool-check { display:flex; align-items:center; gap:6px; font-size:11px; color:var(--text-secondary); margin:4px 0; min-width:0; }
.tool-check span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.stage-panel { grid-column:1 / -1; }
.stage-grid { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:8px; }
.stage-grid label span { display:block; font-size:11px; color:var(--text-dim); margin-bottom:4px; }
.stage-grid textarea { width:100%; min-height:70px; resize:vertical; font-size:12px; }
.error-card p { color: var(--text-secondary); margin-top: 8px; }
.detail-tabs { display:flex; gap:8px; flex-wrap:wrap; margin:0 0 16px; border-bottom:1px solid var(--border); }
.tab-btn { border:0; background:transparent; color:var(--text-secondary); padding:10px 12px; cursor:pointer; font-size:13px; border-bottom:2px solid transparent; }
.tab-btn.active { color:var(--text-primary); border-bottom-color:var(--accent-gold); font-weight:700; }
.card-header-row { display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:10px; }
.metric-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }
.metric-grid > div { border:1px solid var(--border); border-radius:6px; padding:10px; background:var(--bg-deep); }
.metric-grid strong { display:block; margin-top:5px; font-family:var(--font-mono); }
.metric-help { margin-top:10px; color:var(--text-secondary); font-size:12px; line-height:1.7; border-top:1px solid var(--border); padding-top:10px; }
.skill-list, .timeline { display:flex; flex-direction:column; gap:8px; }
.skill-row, .timeline-item { display:flex; gap:10px; align-items:center; border-bottom:1px solid var(--border); padding:8px 0; font-size:12px; }
.skill-row span { flex:1; }
.skill-row small { color:var(--text-dim); }
.system-doc { max-height:360px; overflow:auto; white-space:pre-wrap; font-size:12px; line-height:1.7; background:var(--bg-deep); border:1px solid var(--border); border-radius:6px; padding:12px; }
.prompt-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
.eval-summary { margin-bottom:14px; }
.eval-help { margin-bottom:12px; color:var(--text-secondary); font-size:12px; line-height:1.7; border:1px solid var(--border); border-radius:6px; background:var(--bg-deep); padding:10px 12px; }
.eval-metric-cards { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin-bottom:14px; }
.eval-metric-card { border:1px solid var(--border); border-radius:6px; background:var(--bg-deep); padding:10px; min-width:0; }
.eval-card-head { display:flex; justify-content:space-between; gap:8px; align-items:flex-start; border-bottom:1px solid var(--border); padding-bottom:8px; margin-bottom:8px; }
.eval-card-head strong { font-family:var(--font-mono); font-size:15px; overflow-wrap:anywhere; text-align:right; }
.eval-card-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; }
.eval-card-grid div { min-width:0; }
.eval-card-grid span { display:block; color:var(--text-dim); font-size:10px; margin-bottom:3px; }
.eval-card-grid b { font-family:var(--font-mono); font-size:12px; overflow-wrap:anywhere; }
.logic-overlay { position:fixed; inset:0; background:rgba(15,23,42,0.24); z-index:40; display:flex; justify-content:flex-end; }
.logic-drawer { width:min(460px, 94vw); height:100%; background:var(--bg-card); border-left:1px solid var(--border); padding:18px; overflow:auto; box-shadow:-10px 0 24px rgba(15,23,42,0.16); }
.logic-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin-bottom:14px; }
.logic-grid > div { border:1px solid var(--border); border-radius:6px; padding:10px; background:var(--bg-deep); min-width:0; }
.logic-grid strong { display:block; margin-top:5px; overflow-wrap:anywhere; }
.logic-section { border:1px solid var(--border); border-radius:6px; padding:12px; background:var(--bg-deep); margin-bottom:12px; }
.logic-section p { color:var(--text-secondary); line-height:1.7; margin:0; white-space:pre-wrap; }
.trace-list { display:flex; flex-direction:column; gap:8px; }
.trace-item { border-bottom:1px solid var(--border); padding-bottom:8px; }
.trace-item:last-child { border-bottom:0; padding-bottom:0; }
.trace-item b { display:inline-block; margin:0 8px; }
.trace-item em { color:var(--text-dim); font-style:normal; font-family:var(--font-mono); font-size:11px; }
.trace-item p { margin-top:4px; }
.batch-summary { max-width:360px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; color:var(--text-secondary); }
@media (max-width: 900px) {
  .agent-config-board, .stage-grid, .prompt-grid, .logic-grid, .eval-metric-cards, .pool-add-row, .pool-search-row, .strategy-checks { grid-template-columns:1fr; }
}
@media (max-width: 1000px) {
  .agent-config-board, .stage-grid, .tool-groups { grid-template-columns:1fr; }
}
</style>
