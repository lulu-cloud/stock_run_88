<template>
  <div>
    <div v-if="loadError" class="card dashboard-error">
      <h3>首页数据加载失败</h3>
      <p>{{ loadError }}</p>
      <button class="btn btn-primary btn-sm" @click="loadData">重试</button>
    </div>

    <!-- Agent Cards -->
    <div class="grid-4" style="margin-bottom:24px" v-if="!loadError">
      <div class="agent-card" v-for="a in agents" :key="a.id" :class="{off: a.status!=='active'}">
        <div class="ac-top">
          <div class="ac-avatar">{{ (a.display_name || a.name || '?')[0] }}</div>
          <div style="flex:1">
            <div class="ac-name">{{ a.display_name }}</div>
            <div class="ac-type">{{ a.agent_type }}</div>
          </div>
          <button class="btn btn-sm" :style="{background:a.status==='active'?'var(--accent-green)':a.status==='paused'?'#eab308':'var(--accent-red)',color:'#fff',border:'none'}"
                  @click="toggleStatus(a)">
            {{ a.status==='active'?'启用中':a.status==='paused'?'暂停中':'禁用中' }}
          </button>
        </div>
        <div class="ac-stats">
          <div class="acs"><span class="acsl">总资产</span><span class="acsv">{{ money(a.total_assets) }}</span></div>
          <div class="acs"><span class="acsl">收益率</span><span class="acsv" :class="a.cumulative_return>=0?'green':'red'">{{ a.cumulative_return>=0?'+':'' }}{{ a.cumulative_return }}%</span></div>
          <div class="acs"><span class="acsl">持仓</span><span class="acsv">{{ a.position_count }}<small>只</small></span></div>
        </div>
        <div class="race-strip">
          <span>赛马 {{ score(a.race_metric?.race_score) }}</span>
          <span>回撤 {{ score(a.race_metric?.max_drawdown) }}%</span>
          <span>{{ a.race_metric?.style_tag || '未评级' }}</span>
        </div>
        <div class="skill-line" v-if="a.top_skill?.skill_id">
          {{ a.top_skill.skill_name }} · {{ score(a.top_skill.confidence_score) }}
        </div>
        <router-link v-if="a.id" :to="`/agent/${a.id}`" class="btn btn-sm" style="width:100%;text-align:center;display:block;margin-top:10px;">查看详情 →</router-link>
      </div>
    </div>

    <div class="card" style="margin-bottom:24px;" v-if="!loadError">
      <div class="card-header-row">
        <h3>新增参赛 Agent</h3>
        <button class="btn btn-primary btn-sm" @click="createAgent">创建</button>
      </div>
      <div class="create-grid">
        <input v-model="createForm.display_name" placeholder="显示名，如 情绪试验Agent" />
        <input v-model="createForm.name" placeholder="唯一名，如 agent_experiment" />
        <select v-model="createForm.agent_type" @change="applyStyleTemplate">
          <option value="chaser">追高打板</option>
          <option value="autonomous">自主决策</option>
          <option value="user_style">用户风格交易员</option>
          <option value="custom">自定义</option>
        </select>
        <input v-model.number="createForm.initial_capital" type="number" placeholder="初始资金" />
        <input v-model.number="createForm.max_tool_turns" type="number" min="2" max="50" placeholder="工具轮数" />
        <select v-model="createForm.reasoning_effort">
          <option value="high">high</option>
          <option value="max">max</option>
        </select>
      </div>
      <div class="config-board">
        <div class="config-block">
          <div class="cfg-title">Agent风格模板</div>
          <p class="small-muted">第三列是风格模板，用来初始化这段提示词；不是选股策略。</p>
          <textarea v-model="createForm.style_prompt" rows="5" class="wide-textarea"></textarea>
        </div>
        <div class="config-block">
          <div class="cfg-title">用户原始交易策略</div>
          <p class="small-muted">创建后会单独留历史版本；进化系统只能补充执行细节。</p>
          <textarea v-model="createForm.user_strategy_original" rows="5" class="wide-textarea" placeholder="写入这个交易员要模拟执行的用户交易风格..."></textarea>
          <div class="pool-switches">
            <label><input type="checkbox" v-model="createForm.stock_pool_enabled" /> 启用股票池约束</label>
            <label><input type="checkbox" v-model="createForm.allow_out_of_pool" /> 允许池外探索</label>
          </div>
        </div>
        <div class="config-block">
          <div class="cfg-title">买入板块权限</div>
          <p class="small-muted">自动：总资产20万且首笔交易满60自然日解锁创业板；60万且满60日解锁科创板/北交所。手动：以前端开关为准。</p>
          <select v-model="createForm.board_permission_mode">
            <option value="auto">自动解锁</option>
            <option value="manual">手动配置</option>
          </select>
          <div class="board-switches">
            <label><input type="checkbox" v-model="createForm.board_permissions.main_sme" disabled /> 主板/中小板</label>
            <label><input type="checkbox" v-model="createForm.board_permissions.chinext" :disabled="createForm.board_permission_mode==='auto'" /> 创业板</label>
            <label><input type="checkbox" v-model="createForm.board_permissions.star" :disabled="createForm.board_permission_mode==='auto'" /> 科创板</label>
            <label><input type="checkbox" v-model="createForm.board_permissions.bj" :disabled="createForm.board_permission_mode==='auto'" /> 北交所</label>
          </div>
        </div>
        <div class="config-block">
          <div class="cfg-title">优先选股策略</div>
          <p class="small-muted">这是偏好顺序，不等于唯一可用工具；真正的工具限制在右侧白名单。</p>
          <div class="strategy-checks">
            <label v-for="s in strategyOptions" :key="s.name" class="strategy-check" :title="s.description">
              <input type="checkbox" v-model="createForm.preferred_strategies" :value="s.name" />
              <span><b>{{ s.name }}</b><em>{{ s.description }}</em></span>
            </label>
          </div>
        </div>
        <div class="config-block">
          <div class="cfg-title">严格工具白名单</div>
          <div class="tool-actions">
            <button class="btn btn-sm" @click="selectAllTools">全选</button>
            <button class="btn btn-sm" @click="selectCoreTools">核心工具</button>
          </div>
          <div class="tool-groups">
            <div v-for="group in groupedTools" :key="group.category" class="tool-group">
              <b>{{ group.category }}</b>
              <label v-for="t in group.tools" :key="t.name" class="tool-check" :title="t.description">
                <input type="checkbox" v-model="createForm.allowed_tools" :value="t.name" :disabled="t.mandatory" />
                <span>{{ t.name }}</span>
              </label>
            </div>
          </div>
        </div>
        <div class="config-block stage-block">
          <div class="cfg-title">各阶段提示词</div>
          <div class="stage-grid">
            <label v-for="item in stagePromptFields" :key="item.key">
              <span>{{ item.label }}</span>
              <textarea v-model="createForm.stage_prompts[item.key]" rows="2"></textarea>
            </label>
          </div>
        </div>
      </div>
      <p v-if="createMsg" class="small-muted" style="margin-top:8px;">{{ createMsg }}</p>
    </div>

    <div class="card" style="margin-bottom:24px;" v-if="racePanel.length">
      <div class="card-header-row">
        <h3>赛马竞技场</h3>
        <span class="small-muted">赛马只评价表现并生成提示，不强制限制仓位</span>
      </div>
      <table>
        <thead><tr><th>Agent</th><th>赛马分</th><th>超额</th><th>最大回撤</th><th>夏普</th><th>风格</th><th>核心技能</th></tr></thead>
        <tbody>
          <tr v-for="r in racePanel" :key="r.agent_id">
            <td>{{ r.display_name }}</td>
            <td class="mono">{{ score(r.metric?.race_score) }}</td>
            <td class="mono" :class="(r.metric?.excess_return || 0) >= 0 ? 'green' : 'red'">{{ signedPct(r.metric?.excess_return) }}</td>
            <td class="mono">{{ score(r.metric?.max_drawdown) }}%</td>
            <td class="mono">{{ score(r.metric?.sharpe_ratio) }}</td>
            <td>{{ r.metric?.style_tag || '-' }}</td>
            <td>{{ (r.skills || []).map(s => `${s.skill_name}${score(s.confidence_score)}`).join('、') || '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="grid-2" style="margin-bottom:24px" v-if="positionOverview.length || sectorTemperature">
      <div class="card" v-if="positionOverview.length">
        <div class="card-header-row">
          <h3>Agent 仓位</h3>
          <span class="small-muted">总仓位与个股权重</span>
        </div>
        <div class="position-list">
          <div v-for="a in positionOverview" :key="a.agent_id" class="position-agent">
            <div class="position-head">
              <b>{{ a.display_name }}</b>
              <span>{{ a.position_ratio.toFixed(1) }}% 仓位 · 现金 {{ money(a.cash) }}</span>
            </div>
            <div class="position-bar"><i :style="{width: Math.min(100, a.position_ratio) + '%'}"></i></div>
            <div class="position-stocks" v-if="a.positions?.length">
              <div v-for="p in a.positions.slice(0,5)" :key="p.ts_code" class="position-stock">
                <span>{{ p.stock_name || p.ts_code }}</span>
                <span class="mono">{{ p.weight.toFixed(1) }}%</span>
                <span class="mono" :class="p.unrealized_pnl >= 0 ? 'green' : 'red'">{{ p.unrealized_pnl >= 0 ? '+' : '' }}{{ p.unrealized_pnl.toFixed(0) }}</span>
              </div>
            </div>
            <div v-else class="small-muted">空仓</div>
          </div>
        </div>
      </div>
      <div class="card" v-if="sectorTemperature">
        <div class="card-header-row">
          <h3>板块温度</h3>
          <span class="small-muted">{{ sectorTemperature.trade_date }} · {{ sectorTemperature.market_regime }} {{ sectorTemperature.risk_on_score }}</span>
        </div>
        <div class="breadth-line" v-if="marketBreadth">
          <span>上涨 {{ marketBreadth.up_count }}</span>
          <span>涨停 {{ marketBreadth.limit_up_count }}</span>
          <span>大涨 {{ marketBreadth.big_up_count }}</span>
          <span>跌停 {{ marketBreadth.limit_down_count }}</span>
        </div>
        <div class="sector-list">
          <div v-for="s in sectorTemperature.sectors?.slice(0,8)" :key="s.sector" class="sector-row">
            <div class="sector-main">
              <b>{{ s.sector }}</b>
              <span>温度 {{ s.heat_score }} / 均涨 {{ s.avg_pct }}%</span>
            </div>
            <div class="leader-line">{{ (s.leaders||[]).slice(0,4).map(x=>`${x.name}${x.pct_chg>=0?'+':''}${x.pct_chg}%`).join('、') }}</div>
          </div>
        </div>
      </div>
    </div>

    <div class="card macro-card" style="margin-bottom:24px" v-if="!loadError">
      <div class="card-header-row">
        <div>
          <h3>每日宏观报告</h3>
          <span class="small-muted" v-if="macroReport">
            {{ macroReport.trade_date }} · {{ macroReport.status }} · {{ macroReport.market_regime }} · risk-on {{ score(macroReport.risk_on_score) }}
          </span>
          <span class="small-muted" v-else>{{ macroLoading ? '正在生成公共市场日报' : '暂无报告，可手动生成' }}</span>
        </div>
        <button class="btn btn-sm btn-primary" @click="generateMacroReport" :disabled="macroLoading">
          {{ macroLoading ? '刷新中...' : '刷新宏观/政策' }}
        </button>
      </div>
      <div v-if="macroReport" class="macro-grid">
        <div class="macro-panel">
          <div class="cfg-title">市场摘要</div>
          <p class="macro-summary">{{ macroStructured.summary || macroReport.summary || '暂无摘要' }}</p>
          <div class="breadth-line">
            <span v-for="s in (macroStructured.hot_sectors || []).slice(0,8)" :key="s">热 {{ s }}</span>
          </div>
          <div class="breadth-line">
            <span v-for="s in (macroStructured.risk_sectors || []).slice(0,6)" :key="s">险 {{ s }}</span>
          </div>
        </div>
        <div class="macro-panel">
          <div class="cfg-title">情绪 / 政策 / 指引</div>
          <p>{{ macroStructured.limit_up_summary || '-' }}</p>
          <p>{{ macroStructured.lhb_summary || '-' }}</p>
          <p>{{ macroStructured.policy_signal || '-' }}</p>
          <p>{{ macroStructured.chip_signal || '-' }}</p>
          <p class="macro-guidance">{{ macroStructured.trade_agent_guidance || '-' }}</p>
        </div>
      </div>
      <details v-if="macroReport?.report_md" class="macro-details">
        <summary>查看完整报告</summary>
        <div class="md-content" v-html="renderMD(macroReport.report_md)"></div>
      </details>
      <div class="data-quality" v-if="macroDataStatus.length">
        <span v-for="s in macroDataStatus.slice(0,18)" :key="s.source" :class="s.ok ? 'ok' : 'fail'">
          {{ s.source }} {{ s.ok ? 'ok' : 'fail' }}
        </span>
      </div>
    </div>

    <div class="grid-2" style="margin-bottom:24px" v-if="!loadError">
      <div class="card">
        <h3>上证指数</h3>
        <div ref="indexChart" style="width:100%;height:500px;"></div>
      </div>
      <div class="card">
        <h3>Agent 净值趋势对比</h3>
        <div ref="trendChart" style="width:100%;height:500px;"></div>
      </div>
    </div>

    <div class="grid-2" style="margin-bottom:24px" v-if="sectorStrength">
      <div class="card">
        <div class="card-header-row">
          <h3>近{{ sectorStrength.lookback_days }}日强势板块</h3>
          <span class="small-muted">{{ sectorStrength.trade_date }}</span>
        </div>
        <div class="sector-list">
          <div v-for="s in sectorStrength.strong?.slice(0,8)" :key="s.sector" class="sector-row">
            <div class="sector-main">
              <b>{{ s.sector }}</b>
              <span>强度 {{ s.strength_score }} / 均涨 {{ s.avg_pct }}%</span>
            </div>
            <div class="leader-line">{{ (s.leaders||[]).slice(0,4).map(x=>`${x.name}${x.pct>=0?'+':''}${x.pct}%`).join('、') }}</div>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="card-header-row">
          <h3>近{{ sectorStrength.lookback_days }}日弱势板块</h3>
        </div>
        <div class="sector-list">
          <div v-for="s in sectorStrength.weak?.slice(0,8)" :key="s.sector" class="sector-row weak">
            <div class="sector-main">
              <b>{{ s.sector }}</b>
              <span>强度 {{ s.strength_score }} / 均涨 {{ s.avg_pct }}%</span>
            </div>
            <div class="leader-line">{{ (s.leaders||[]).slice(0,4).map(x=>`${x.name}${x.pct>=0?'+':''}${x.pct}%`).join('、') }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- PnL Calendar -->
    <div class="card" style="margin-bottom:24px;" v-if="pnlCalendar.length">
      <div class="card-header-row">
        <h3>每日盈亏日历</h3>
        <div class="pnl-tabs">
          <button class="pnl-tab" :class="{active: pnlMode === 'total'}" @click="pnlMode = 'total'">总盈亏</button>
          <button
            v-for="name in pnlAgentOptions"
            :key="name"
            class="pnl-tab"
            :class="{active: pnlMode === name}"
            @click="pnlMode = name"
          >
            {{ name }}
          </button>
        </div>
      </div>
      <div class="pnl-calendar">
        <div v-for="day in displayedPnlCalendar" :key="day.date" class="pnl-day"
             :class="{ 'pnl-positive': day.total > 0, 'pnl-negative': day.total < 0 }"
             :title="day.date + ': ' + day.detail">
          <span class="pnl-d">{{ day.date.slice(4,6) }}/{{ day.date.slice(6,8) }}</span>
          <span class="pnl-v">{{ day.total >= 0 ? '+' : '' }}{{ day.total.toFixed(0) }}</span>
        </div>
      </div>
    </div>

    <!-- Policy -->
    <div class="card">
      <div class="card-header-row">
        <h3>宏观政策动态</h3>
        <div style="display:flex;gap:8px;align-items:center;">
          <div class="dept-tabs">
            <button v-for="d in deptOptions" :key="d.key" class="dept-tab"
                    :class="{active: policyDept === d.key}"
                    @click="switchPolicyDept(d.key)">{{ d.label }}</button>
          </div>
          <button class="btn btn-sm btn-primary" @click="loadPolicySignals" :disabled="policyLoading">
            {{ policyLoading?'爬取中...':'刷新政策' }}
          </button>
        </div>
      </div>
      <div v-if="policySignals" style="margin-bottom:14px;">
        <p class="policy-summary">{{ policySignals.summary }}</p>
        <div class="policy-tags" v-if="policySignals.top_industries?.length">
          <span v-for="ind in policySignals.top_industries.slice(0,10)" :key="ind.industry" class="policy-tag">
            {{ ind.industry }}<span class="tag-s">{{ (ind.strength*100).toFixed(0) }}</span>
          </span>
        </div>
      </div>
      <div v-if="policyList.length" style="margin-top:14px;">
        <div class="policy-list">
          <div v-for="(p,idx) in policyList.slice(0,12)" :key="idx" class="policy-item" @click="viewPolicy(p)">
            <span class="pd">{{ fmtDate(p.date||p.filename?.slice(0,8)) }}</span>
            <span class="pt">{{ p.title }}</span>
            <span class="ps">{{ p.source }}</span>
          </div>
        </div>
      </div>
      <div v-if="showPolicyModal" class="modal-overlay" @click.self="showPolicyModal=false">
        <div class="modal-card" style="max-width:780px;">
          <div class="card-header-row">
            <h3>{{ selectedPolicy?.title }}</h3>
            <button class="btn btn-sm" @click="showPolicyModal=false">关闭</button>
          </div>
          <div v-if="policyContent" class="md-content" v-html="policyContent"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { marked } from 'marked'
import { agentAPI, marketAPI, policyAPI, strategyAPI, macroAPI } from '../api'

const agents = ref([]); const indexChart = ref(null); const trendChart = ref(null)
const pnlCalendar = ref([])
const comparisonAgents = ref([])
const pnlMode = ref('total')
const racePanel = ref([])
const loadError = ref('')
const createMsg = ref('')
const toolCatalog = ref([])
const strategyOptions = ref([])
const stagePromptFields = [
  { key: 'market_scan', label: '行情感知' },
  { key: 'stock_selection', label: '选股择时' },
  { key: 'risk_control', label: '风控仓位' },
  { key: 'order_plan', label: '订单规划' },
  { key: 'reflection', label: '复盘反思' },
]
const defaultStagePrompts = {
  market_scan: '先判断大盘、市场宽度、板块温度和政策方向，再决定 risk-on/neutral/risk-off。',
  stock_selection: '打板与多头均线发散并重；候选股必须结合热点板块、业务基本面、量价和流动性二次筛选。',
  risk_control: '行情 risk-on 可适当提高进攻仓位；行情差轻仓或空仓。参考赛马指标、技能置信度和失败订单记录，自主解释仓位。',
  order_plan: '挂单价必须先计算涨跌幅并校验；换仓必须说明非原子顺序风险。',
  reflection: '复盘时把已验证规律写入记忆，删除没有证据的判断。',
}
const styleTemplates = {
  chaser: '追高打板情绪猎手：偏短线强势与情绪周期，但不机械追逐所有涨停。必须同时关注多头均线发散、右侧趋势、板块温度、封板质量、换手、炸板风险和次日离场条件；行情 risk-on 时可更积极。',
  autonomous: '全因子自主决策交易者：综合政策、基本面、技术、资金、板块温度与情绪，不简单复制追高打板候选。优先寻找多头均线发散、回踩支撑、热点共振的右侧机会；行情好适度进攻，行情差控制仓位。',
  user_style: '用户风格交易员：以用户写入的原始交易策略为最高风格锚点，在前端配置股票池内模拟执行；进化系统只补充执行细节，不覆盖用户原始策略。若允许池外探索，必须说明突破股票池的原因。',
  custom: '自定义交易员：按前端配置的策略偏好、工具白名单和阶段提示词执行。若配置不足，采用稳健均衡风格并清楚说明假设。',
}
const createForm = ref({
  display_name: '',
  name: '',
  agent_type: 'custom',
  style_prompt: styleTemplates.custom,
  preferred_strategies: [],
  allowed_tools: [],
  board_permission_mode: 'auto',
  board_permissions: { main_sme: true, chinext: false, star: false, bj: false },
  stock_pool_enabled: false,
  allow_out_of_pool: false,
  user_strategy_original: '',
  stage_prompts: { ...defaultStagePrompts },
  initial_capital: 150000,
  max_tool_turns: 8,
  reasoning_effort: 'high',
})
let idxInst = null; let trendInst = null
const policyList = ref([]); const policySignals = ref(null); const policyLoading = ref(false)
const sectorStrength = ref(null)
const marketBreadth = ref(null)
const sectorTemperature = ref(null)
const positionOverview = ref([])
const macroReport = ref(null)
const macroLoading = ref(false)
const showPolicyModal = ref(false); const selectedPolicy = ref(null); const policyContent = ref('')
const policyDept = ref('')
const deptOptions = [
  { key: '', label: '全部' },
  { key: '工信部', label: '工信部' },
  { key: '发改委', label: '发改委' },
  { key: '财政部', label: '财政部' },
]

function fmtDate(s) { if(!s)return''; const d=String(s).slice(0,8); return d.slice(0,4)+'/'+d.slice(4,6)+'/'+d.slice(6,8) }
function renderMD(md) { return md?marked(md,{breaks:true}):'' }
function money(value) {
  return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function score(value) { return Number(value || 0).toFixed(2) }
function signedPct(value) { const n=Number(value||0); return `${n>=0?'+':''}${n.toFixed(2)}%` }
function parseJSONSafe(raw, fallback) {
  try { return raw ? JSON.parse(raw) : fallback } catch(e) { return fallback }
}
const macroStructured = computed(() => parseJSONSafe(macroReport.value?.structured_json, {}))
const macroDataStatus = computed(() => parseJSONSafe(macroReport.value?.data_status_json, []))
const groupedTools = computed(() => {
  const groups = {}
  for (const t of toolCatalog.value) {
    const key = t.category || '其他'
    if (!groups[key]) groups[key] = []
    groups[key].push(t)
  }
  return Object.entries(groups).map(([category, tools]) => ({ category, tools }))
})
const pnlAgentOptions = computed(() => {
  const names = new Set((comparisonAgents.value || []).map(a => a.display_name).filter(Boolean))
  for (const day of pnlCalendar.value) {
    for (const name of Object.keys(day.byAgent || {})) names.add(name)
  }
  return [...names]
})
const displayedPnlCalendar = computed(() => {
  if (pnlMode.value === 'total') return pnlCalendar.value
  return pnlCalendar.value.map((day) => {
    const value = Number((day.byAgent || {})[pnlMode.value] || 0)
    const hasReport = Object.prototype.hasOwnProperty.call(day.byAgent || {}, pnlMode.value)
    return {
      date: day.date,
      total: Math.round(value * 100) / 100,
      detail: hasReport
        ? `${pnlMode.value}: ${value >= 0 ? '+' : ''}${value.toFixed(0)}`
        : `${pnlMode.value}: 暂无日报`,
      byAgent: day.byAgent,
    }
  })
})

function selectAllTools() {
  createForm.value.allowed_tools = toolCatalog.value.map(t => t.name)
}
function selectCoreTools() {
  const core = new Set([
    'search_stocks_by_strategy', 'get_market_overview', 'get_stock_kline',
    'get_recent_order_history', 'calculate_price_by_pct', 'validate_order_price_limit',
    'get_evolution_context', 'get_strategy_param_schema',
  ])
  createForm.value.allowed_tools = toolCatalog.value.filter(t => t.mandatory || core.has(t.name)).map(t => t.name)
}
function applyStyleTemplate() {
  createForm.value.style_prompt = styleTemplates[createForm.value.agent_type] || styleTemplates.custom
  if (createForm.value.agent_type === 'user_style') createForm.value.stock_pool_enabled = true
}

async function loadPolicyData() {
  try {
    const dept = policyDept.value ? `&department=${encodeURIComponent(policyDept.value)}` : ''
    const [l,s] = await Promise.all([
      policyAPI.latest(policyDept.value),
      policyAPI.signals()
    ])
    policyList.value = l.data.policies || []
    policySignals.value = s.data
  } catch(e) {}
}
async function switchPolicyDept(key) {
  policyDept.value = key
  await loadPolicyData()
}
async function loadPolicySignals() {
  policyLoading.value=true
  try { await policyAPI.crawl(); setTimeout(async()=>{await loadPolicyData();policyLoading.value=false},3000) } catch(e){policyLoading.value=false}
}
async function viewPolicy(p) {
  selectedPolicy.value=p; showPolicyModal.value=true; policyContent.value=''
  try { const r=await policyAPI.content(p.source_dir,p.filename); policyContent.value=renderMD(r.data.content||'') } catch(e){policyContent.value='加载失败'}
}

async function loadMacroReport() {
  try {
    const r = await macroAPI.report('')
    macroReport.value = r.data.exists ? r.data : null
  } catch(e) {
    macroReport.value = null
  }
}

async function generateMacroReport() {
  macroLoading.value = true
  try {
    const r = await macroAPI.refresh('', true, true)
    macroReport.value = r.data.report?.exists ? r.data.report : null
  } finally {
    macroLoading.value = false
  }
}

async function loadData() {
  loadError.value = ''
  let aRes, mRes
  try {
    ;[aRes, mRes] = await Promise.all([agentAPI.list(), marketAPI.index(2000)])
  } catch (e) {
    loadError.value = e.response?.data?.detail || e.message || '请确认后端服务已启动，且前端代理端口与后端端口一致。'
    return
  }
  agents.value=aRes.data.agents; loadPolicyData()
  loadMacroReport()
  agentAPI.tools().then(r => {
    toolCatalog.value = r.data.tools || []
    if (!createForm.value.allowed_tools.length) selectAllTools()
  }).catch(() => {})
  strategyAPI.builtin().then(r => { strategyOptions.value = r.data.strategies || [] }).catch(() => {})
  agentAPI.race(90).then(r => { racePanel.value = r.data.agents || [] }).catch(() => { racePanel.value = [] })
  agentAPI.positions().then(r => { positionOverview.value = r.data.agents || [] }).catch(() => { positionOverview.value = [] })
  // 板块强弱不阻塞大盘渲染
  marketAPI.sectorStrength('',3).then(r => { sectorStrength.value = r.data }).catch(() => {})
  marketAPI.breadth('').then(r => { marketBreadth.value = r.data }).catch(() => { marketBreadth.value = null })
  marketAPI.sectorTemperature('',20).then(r => { sectorTemperature.value = r.data }).catch(() => { sectorTemperature.value = null })
  await nextTick()

  const raw=mRes.data.data
  if(raw?.length && indexChart.value) {
    if(idxInst) idxInst.dispose(); idxInst=echarts.init(indexChart.value)
    const dates=raw.map(d=>d.trade_date)
    const ohlc=raw.map(d=>[d.open,d.close,d.low,d.high])
    const ls=raw.map(d=>d.low); const hs=raw.map(d=>d.high)
    const pad=(Math.max(...hs)-Math.min(...ls))*0.05
    idxInst.setOption({
      backgroundColor:'transparent',animation:false,
      tooltip:{trigger:'axis',axisPointer:{type:'cross'},formatter:p=>{const d=raw[p[0].dataIndex];return`${d.trade_date}<br/>开:${d.open.toFixed(2)} 收:${d.close.toFixed(2)}<br/>高:${d.high.toFixed(2)} 低:${d.low.toFixed(2)}<br/>涨跌:${d.pct_chg.toFixed(2)}%`}},
      toolbox:{right:8,top:4,feature:{dataZoom:{yAxisIndex:'none',title:{zoom:'区域缩放',back:'还原'}},restore:{title:'重置'}}},
      dataZoom:[
        {type:'inside',start:0,end:100,zoomOnMouseWheel:true,moveOnMouseMove:true},
        {type:'slider',start:90,end:100,height:24,bottom:4,borderColor:'#d1d5db',backgroundColor:'#f0f2f5',fillerColor:'rgba(184,134,11,0.15)',handleStyle:{color:'#b8860b'},textStyle:{color:'#5a5d6e',fontSize:10}},
      ],
      grid:{left:65,right:18,top:30,bottom:38},
      xAxis:{type:'category',data:dates,axisLabel:{color:'#9ca3af',fontSize:10,formatter:v=>v.slice(4)},axisLine:{lineStyle:{color:'#e2e5ea'}},axisTick:{show:false}},
      yAxis:{type:'value',scale:true,axisLabel:{color:'#9ca3af',fontSize:10},splitLine:{lineStyle:{color:'#e2e5ea',type:'dashed'}}},
      series:[{type:'candlestick',data:ohlc,itemStyle:{color:'#dc2626',color0:'#059669',borderColor:'#dc2626',borderColor0:'#059669'}}],
    })
  }
  // Agent comparison trend + PnL calendar
  try {
    const comp = await agentAPI.comparison(90)
    const agentData = comp.data.agents || []
    comparisonAgents.value = agentData
    const allAgentNames = agentData.map(a => a.display_name).filter(Boolean)
    const calData = comp.data.pnl_calendar || {}

    // Build PnL calendar
    const calEntries = Object.entries(calData).map(([date, agents_pnl]) => {
      let total = 0; let detail = []
      for (const name of allAgentNames) {
        if (!Object.prototype.hasOwnProperty.call(agents_pnl, name)) {
          detail.push(`${name}: 暂无`)
          continue
        }
        const pnl = agents_pnl[name] || 0
        total += pnl
        detail.push(`${name}: ${pnl>=0?'+':''}${pnl.toFixed(0)}`)
      }
      return { date, total: Math.round(total*100)/100, detail: detail.join('; '), byAgent: agents_pnl }
    }).sort((a,b) => a.date.localeCompare(b.date))
    pnlCalendar.value = calEntries
    if (pnlMode.value !== 'total' && !allAgentNames.includes(pnlMode.value)) {
      pnlMode.value = 'total'
    }

    // Trend chart
    if (agentData.length && trendChart.value) {
      if (trendInst) trendInst.dispose()
      trendInst = echarts.init(trendChart.value)
      const allDates = new Set()
      const series = []
      const colors = ['#b8860b', '#0891b2', '#7c3aed', '#64748b']
      for (let i = 0; i < agentData.length; i++) {
        const a = agentData[i]
        const data = []
        for (const pt of (a.equity_curve || [])) {
          allDates.add(pt.date)
          data.push({ date: pt.date, value: pt.return_pct })
        }
        series.push({
          name: a.display_name, type: 'line', data,
          lineStyle: { color: colors[i % colors.length], width: 2 },
          itemStyle: { color: colors[i % colors.length] },
          showSymbol: false,
        })
      }
      const indexBase = raw.find(d => allDates.has(String(d.trade_date)))?.close || raw[0]?.close || 0
      if (indexBase > 0) {
        const indexData = raw
          .filter(d => allDates.has(String(d.trade_date)))
          .map(d => ({ date: String(d.trade_date), value: ((d.close - indexBase) / indexBase) * 100 }))
        for (const pt of indexData) allDates.add(pt.date)
        series.push({
          name: '上证指数', type: 'line', data: indexData,
          lineStyle: { color: '#64748b', width: 2, type: 'dashed' },
          itemStyle: { color: '#64748b' },
          showSymbol: false,
        })
      }
      const dates = [...allDates].sort()
      // Map series data to indexed array
      const seriesData = series.map(s => {
        const map = Object.fromEntries(s.data.map(d => [d.date, d.value]))
        return dates.map(d => map[d] ?? null)
      })
      series.forEach((s, i) => { s.data = seriesData[i] })

      trendInst.setOption({
        backgroundColor: 'transparent',
        tooltip: { trigger: 'axis' },
        legend: { data: series.map(s => s.name), bottom: 0, textStyle: { color: '#9ca3af', fontSize: 11 } },
        grid: { left: 60, right: 16, top: 16, bottom: 40 },
        xAxis: { type: 'category', data: dates, axisLabel: { color: '#9ca3af', fontSize: 9, formatter: v => String(v).slice(4) }, axisLine: { lineStyle: { color: '#e2e5ea' } } },
        yAxis: { type: 'value', axisLabel: { color: '#9ca3af', formatter: '{value}%' }, splitLine: { lineStyle: { color: '#e2e5ea', type: 'dashed' } } },
        series,
      })
    }
  } catch (e) { console.error('Comparison load error:', e) }
}
async function toggleStatus(a) { const r=await agentAPI.toggleStatus(a.id); a.status=r.data.status }
async function createAgent() {
  createMsg.value = ''
  const display = createForm.value.display_name.trim()
  if (!display) { createMsg.value = '请填写显示名'; return }
  const preferred = createForm.value.preferred_strategies || []
  await agentAPI.create({
    name: createForm.value.name.trim() || `agent_${Date.now()}`,
    display_name: display,
    agent_type: createForm.value.agent_type,
    strategy_ids: preferred.join(','),
    initial_capital: Number(createForm.value.initial_capital || 150000),
    risk_config: {
      max_position_count: 5,
      max_daily_loss: 0.05,
      reasoning_effort: createForm.value.reasoning_effort || 'high',
      max_tool_turns: Math.max(2, Math.min(50, Number(createForm.value.max_tool_turns || 8))),
      style_prompt: createForm.value.style_prompt,
      preferred_strategies: preferred,
      allowed_tools: createForm.value.allowed_tools,
      board_permission_mode: createForm.value.board_permission_mode,
      board_permissions: createForm.value.board_permissions,
      stock_pool_enabled: !!createForm.value.stock_pool_enabled,
      allow_out_of_pool: !!createForm.value.allow_out_of_pool,
      user_strategy_original: createForm.value.user_strategy_original || '',
      stage_prompts: createForm.value.stage_prompts,
    },
  })
  createMsg.value = '已创建'
  createForm.value.display_name = ''
  createForm.value.name = ''
  createForm.value.style_prompt = styleTemplates[createForm.value.agent_type] || styleTemplates.custom
  createForm.value.user_strategy_original = ''
  createForm.value.stage_prompts = { ...defaultStagePrompts }
  await loadData()
}
onMounted(loadData)
</script>

<style scoped>
.agent-card { background:var(--bg-card); border:1px solid var(--border); border-radius:8px; padding:18px; transition:all .2s; }
.agent-card.off { opacity:0.45; }
.agent-card:hover { border-color:var(--border-light); }
.ac-top { display:flex; align-items:center; gap:14px; margin-bottom:16px; }
.ac-avatar { width:40px;height:40px;border-radius:8px;background:var(--bg-elevated);display:flex;align-items:center;justify-content:center;font-family:var(--font-mono);font-weight:700;font-size:18px;color:var(--accent-gold);border:1px solid var(--border-light); }
.ac-name { font-weight:600; font-size:15px; }
.ac-type { font-size:12px; color:var(--text-dim); margin-top:2px; }
.ac-stats { display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; }
.acs { text-align:center; }
.acsl { display:block; font-size:10px; color:var(--text-dim); text-transform:uppercase; letter-spacing:1px; }
.acsv { font-family:var(--font-mono); font-size:18px; font-weight:600; margin-top:3px; display:block; }
.acsv small { font-size:12px; font-weight:400; color:var(--text-dim); }
.race-strip { display:flex; gap:6px; flex-wrap:wrap; margin-top:12px; }
.race-strip span { font-size:10px; color:var(--text-secondary); background:var(--bg-deep); border:1px solid var(--border); padding:3px 6px; border-radius:4px; }
.skill-line { margin-top:8px; font-size:11px; color:var(--accent-cyan); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.mono { font-family:var(--font-mono); }

.card-header-row { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; flex-wrap:wrap; gap:8px; }
.card-header-row h3 { margin-bottom:0; }

.dept-tabs { display:flex; gap:2px; background:var(--bg-deep); border-radius:5px; padding:2px; }
.dept-tab { font-family:var(--font-mono); font-size:11px; padding:4px 12px; border:none; background:transparent; color:var(--text-secondary); cursor:pointer; border-radius:4px; transition:all .15s; }
.dept-tab:hover { color:var(--text-primary); }
.dept-tab.active { background:#fff; color:var(--accent-gold); font-weight:600; box-shadow:0 1px 2px rgba(0,0,0,0.06); }

.policy-summary { color:var(--accent-cyan); font-size:13px; margin-bottom:10px; }
.policy-tags { display:flex; gap:6px; flex-wrap:wrap; }
.policy-tag { font-size:11px; padding:4px 10px; background:rgba(212,168,83,0.08); border:1px solid rgba(212,168,83,0.15); border-radius:4px; color:var(--accent-gold); }
.tag-s { color:var(--text-dim); margin-left:4px; }
.pool-switches { display:flex; gap:14px; flex-wrap:wrap; margin-top:10px; font-size:12px; color:var(--text-secondary); }
.pool-switches label { display:flex; align-items:center; gap:6px; }
.strategy-checks { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; max-height:260px; overflow:auto; }
.strategy-check { display:flex; gap:8px; align-items:flex-start; border:1px solid var(--border); background:var(--bg-deep); border-radius:6px; padding:8px; font-size:12px; min-width:0; }
.strategy-check span { min-width:0; }
.strategy-check b { display:block; color:var(--text-primary); }
.strategy-check em { display:block; color:var(--text-dim); font-style:normal; margin-top:3px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.policy-list { max-height:320px; overflow-y:auto; }
.policy-item { padding:8px 0; border-bottom:1px solid var(--border); display:flex; gap:14px; align-items:baseline; cursor:pointer; transition:background .15s; }
.policy-item:hover { background:rgba(255,255,255,0.015); }
.pd { font-family:var(--font-mono); font-size:11px; color:var(--text-dim); white-space:nowrap; min-width:78px; }
.pt { color:var(--text-primary); font-size:13px; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.ps { font-size:10px; color:var(--text-dim); white-space:nowrap; }

.small-muted { color: var(--text-dim); font-size: 11px; font-family: var(--font-mono); }
.macro-card { overflow:hidden; }
.macro-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.macro-panel { border:1px solid var(--border); background:var(--bg-deep); border-radius:6px; padding:12px; min-width:0; }
.macro-panel p { margin:0 0 8px; color:var(--text-secondary); font-size:12px; line-height:1.7; }
.macro-summary { color:var(--text-primary) !important; }
.macro-guidance { color:var(--accent-cyan) !important; }
.macro-details { margin-top:12px; border-top:1px solid var(--border); padding-top:10px; }
.macro-details summary { cursor:pointer; font-size:12px; color:var(--accent-gold); margin-bottom:8px; }
.data-quality { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
.data-quality span { font-size:10px; border-radius:4px; padding:3px 6px; border:1px solid var(--border); color:var(--text-dim); }
.data-quality span.ok { background:rgba(5,150,105,0.05); color:var(--accent-green); border-color:rgba(5,150,105,0.15); }
.data-quality span.fail { background:rgba(220,38,38,0.04); color:var(--accent-red); border-color:rgba(220,38,38,0.14); }
.sector-list { display:flex; flex-direction:column; gap:8px; }
.sector-row { border:1px solid rgba(220,38,38,0.10); background:rgba(220,38,38,0.035); border-radius:6px; padding:9px 10px; }
.sector-row.weak { border-color:rgba(5,150,105,0.10); background:rgba(5,150,105,0.025); }
.sector-main { display:flex; justify-content:space-between; gap:10px; align-items:center; }
.sector-main b { font-size:13px; color:var(--text-primary); }
.sector-main span { font-size:11px; color:var(--text-secondary); font-family:var(--font-mono); white-space:nowrap; }
	.leader-line { margin-top:5px; font-size:11px; color:var(--text-dim); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
	.position-list { display:flex; flex-direction:column; gap:10px; }
	.position-agent { border:1px solid var(--border); background:var(--bg-deep); border-radius:6px; padding:10px; }
	.position-head { display:flex; justify-content:space-between; gap:10px; align-items:center; }
	.position-head b { font-size:13px; }
	.position-head span { font-size:11px; color:var(--text-secondary); font-family:var(--font-mono); }
	.position-bar { height:6px; background:var(--bg-card); border-radius:4px; overflow:hidden; margin:8px 0; }
	.position-bar i { display:block; height:100%; background:linear-gradient(90deg,var(--accent-green),var(--accent-gold)); border-radius:4px; }
	.position-stocks { display:grid; grid-template-columns:1fr; gap:4px; }
	.position-stock { display:grid; grid-template-columns:minmax(0,1fr) 58px 76px; gap:8px; font-size:11px; color:var(--text-secondary); align-items:center; }
	.position-stock span:first-child { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
	.breadth-line { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:10px; }
	.breadth-line span { font-size:11px; padding:4px 8px; border:1px solid var(--border); border-radius:4px; background:var(--bg-deep); color:var(--text-secondary); }

.pnl-calendar { display: grid; grid-template-columns: repeat(auto-fill, minmax(100px, 1fr)); gap: 4px; }
.pnl-tabs { display:flex; gap:4px; flex-wrap:wrap; justify-content:flex-end; }
.pnl-tab { border:1px solid var(--border); background:var(--bg-deep); color:var(--text-secondary); border-radius:4px; padding:5px 9px; font-size:11px; cursor:pointer; max-width:130px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.pnl-tab.active { background:#fff; color:var(--accent-gold); border-color:var(--accent-gold); font-weight:700; }
.pnl-day { padding: 8px 10px; border-radius: 5px; background: var(--bg-deep); text-align: center; border: 1px solid transparent; cursor: default; }
.pnl-day:hover { border-color: var(--border-light); }
.pnl-positive { background: rgba(5,150,105,0.06); border-color: rgba(5,150,105,0.12); }
.pnl-negative { background: rgba(220,38,38,0.04); border-color: rgba(220,38,38,0.10); }
.pnl-d { display: block; font-size: 10px; color: var(--text-dim); font-family: var(--font-mono); }
.pnl-v { font-family: var(--font-mono); font-size: 13px; font-weight: 600; display: block; margin-top: 2px; }
.pnl-positive .pnl-v { color: var(--accent-green); }
.pnl-negative .pnl-v { color: var(--accent-red); }
.dashboard-error { margin-bottom:24px; }
.dashboard-error p { color:var(--text-secondary); margin:8px 0 12px; }
.create-grid { display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:8px; }
.create-grid input, .create-grid select { width:100%; font-size:12px; }
.config-board { display:grid; grid-template-columns:1fr 1.4fr; gap:12px; margin-top:12px; }
.config-block { border:1px solid var(--border); border-radius:8px; padding:12px; background:var(--bg-deep); min-width:0; }
.cfg-title { font-size:12px; font-weight:700; margin-bottom:6px; color:var(--text-primary); }
.multi-select { width:100%; min-height:160px; font-size:12px; }
.wide-textarea { width:100%; min-height:124px; resize:vertical; font-size:12px; }
.board-switches { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin-top:10px; font-size:12px; color:var(--text-secondary); }
.board-switches label { display:flex; align-items:center; gap:6px; }
.tool-actions { display:flex; gap:6px; margin-bottom:8px; }
.tool-groups { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; max-height:260px; overflow:auto; }
.tool-group { border:1px solid var(--border); border-radius:6px; padding:8px; background:var(--bg-card); }
.tool-group b { display:block; font-size:11px; margin-bottom:6px; color:var(--accent-gold); }
.tool-check { display:flex; align-items:center; gap:6px; font-size:11px; color:var(--text-secondary); margin:4px 0; min-width:0; }
.tool-check span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.stage-block { grid-column:1 / -1; }
.stage-grid { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:8px; }
.stage-grid label span { display:block; font-size:11px; color:var(--text-dim); margin-bottom:4px; }
.stage-grid textarea { width:100%; min-height:70px; resize:vertical; font-size:12px; }
@media (max-width: 1000px) { .create-grid { grid-template-columns:1fr 1fr; } .macro-grid { grid-template-columns:1fr; } }
@media (max-width: 1000px) { .config-board, .stage-grid, .tool-groups { grid-template-columns:1fr; } }
</style>
