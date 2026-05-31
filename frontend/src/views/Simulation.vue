<template>
  <div>
    <h2 style="font-family:var(--font-mono);font-size:16px;margin-bottom:16px;">模拟交易</h2>

    <!-- Config Panel -->
    <div class="card" style="margin-bottom:16px;">
      <h3>模拟配置</h3>
      <div style="margin-bottom:12px;">
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap;">
          <span style="font-size:12px;color:var(--text-dim);">名称:</span>
          <input v-model="simName" placeholder="模拟任务名称" style="width:200px;font-size:12px;" />
          <span style="font-size:12px;color:var(--text-dim);margin-left:12px;">日期:</span>
          <input v-model="startDate" placeholder="YYYYMMDD" style="width:110px;font-size:12px;" />
          <span style="color:var(--text-dim);">-</span>
          <input v-model="endDate" placeholder="YYYYMMDD" style="width:110px;font-size:12px;" />
          <select v-model="presetPeriod" @change="applyPreset" style="width:120px;font-size:11px;">
            <option value="">自定义</option>
            <option value="1w">近1周</option>
            <option value="1m">近1月</option>
            <option value="1q">近1季度</option>
            <option value="ytd">今年以来</option>
          </select>
        </div>

        <div style="font-size:12px;color:var(--text-dim);margin-bottom:12px;">Agent 配置:</div>
        <div v-for="(agent, idx) in simAgents" :key="idx" class="agent-config-row">
          <input v-model="agent.display_name" placeholder="名称" style="width:120px;font-size:12px;" />
          <select v-model="agent.strategy_name" style="width:160px;font-size:12px;">
            <option v-for="s in strategies" :key="s.name" :value="s.name">{{ s.name }}</option>
          </select>
          <input v-model.number="agent.initial_capital" type="number" placeholder="初始资金" style="width:110px;font-size:12px;" />
          <select v-model="agent.reasoning_effort" style="width:80px;font-size:12px;">
            <option value="high">high</option>
            <option value="max">max</option>
          </select>
          <button class="btn btn-sm" style="color:var(--accent-red);" @click="removeAgent(idx)" :disabled="simAgents.length<=1">移除</button>
        </div>
        <button class="btn btn-sm" style="margin-top:8px;" @click="addAgent">+ 添加 Agent</button>
      </div>

      <button class="btn btn-primary" @click="startSimulation" :disabled="simRunning">
        {{ simRunning ? '模拟运行中...' : '启动模拟' }}
      </button>
      <span v-if="simRunning" style="margin-left:12px;font-size:12px;color:var(--accent-cyan);">
        运行中... 进度 {{ simProgress }}%
      </span>
    </div>

    <!-- Results -->
    <div v-if="simResult">
      <!-- Equity Curves -->
      <div class="card" style="margin-bottom:16px;">
        <h3>净值曲线对比</h3>
        <div ref="equityCompareChart" style="width:100%;height:420px;"></div>
      </div>

      <!-- Metrics Table -->
      <div class="card" style="margin-bottom:16px;" v-if="simResult.agents">
        <h3>绩效对比</h3>
        <table>
          <thead>
            <tr><th>Agent</th><th>策略</th><th>初始资金</th><th>最终资产</th><th>累计收益</th><th>最大回撤</th><th>胜率</th><th>交易次数</th><th>夏普</th></tr>
          </thead>
          <tbody>
            <tr v-for="a in simResult.agents" :key="a.agent_id">
              <td>{{ a.display_name }}</td>
              <td class="mono">{{ a.strategy_name }}</td>
              <td class="mono">{{ (a.initial_capital/10000).toFixed(1) }}万</td>
              <td class="mono">{{ (a.final_assets/10000).toFixed(1) }}万</td>
              <td class="mono" :class="a.metrics.total_return>=0?'green':'red'">{{ a.metrics.total_return>=0?'+':'' }}{{ a.metrics.total_return }}%</td>
              <td class="mono red">{{ a.metrics.max_drawdown }}%</td>
              <td class="mono">{{ a.metrics.win_rate }}%</td>
              <td class="mono">{{ a.metrics.total_trades }}</td>
              <td class="mono">{{ a.metrics.sharpe_ratio }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Decision Replay -->
      <div class="card" v-if="simResult.agents">
        <h3>决策回放</h3>
        <div style="display:flex;gap:8px;margin-bottom:12px;">
          <button v-for="a in simResult.agents" :key="'tab'+a.agent_id" class="btn btn-sm"
                  :style="selectedAgent===a.agent_id?'background:var(--accent-gold);color:#fff;':''"
                  @click="selectedAgent=a.agent_id">{{ a.display_name }}</button>
        </div>
        <div v-if="currentDecisions.length" class="decision-list">
          <div v-for="d in currentDecisions" :key="d.trade_date" class="decision-item">
            <div class="decision-header" @click="toggleExpanded(d.trade_date)">
              <span class="mono" style="color:var(--accent-gold);">{{ d.trade_date }}</span>
              <span style="margin-left:12px;font-size:12px;">
                订单: {{ d.orders?.length || 0 }} 条
                <span v-if="d.orders?.length">({{ d.orders.map(o=>o.direction).join(', ') }})</span>
              </span>
              <span style="margin-left:auto;font-size:10px;color:var(--text-dim);">{{ isExpanded(d.trade_date) ? '收起' : '展开' }}</span>
            </div>
            <div v-if="isExpanded(d.trade_date)" class="decision-body">
              <div v-if="d.error" style="color:var(--accent-red);padding:8px;background:#fef2f2;border-radius:4px;margin-bottom:8px;">
                {{ d.error }}
              </div>
              <div v-if="d.market_analysis" style="margin-bottom:8px;padding:8px;background:#f0fdf4;border-radius:4px;border-left:3px solid var(--accent-green);">
                <b style="font-size:11px;color:var(--accent-green);">市场分析</b>
                <pre style="white-space:pre-wrap;font-size:12px;margin:4px 0;font-family:var(--font-mono);">{{ d.market_analysis }}</pre>
              </div>
              <div v-if="d.risk_assessment" style="margin-bottom:8px;padding:8px;background:#fefce8;border-radius:4px;border-left:3px solid #eab308;">
                <b style="font-size:11px;color:#a16207;">风险评估</b>
                <pre style="white-space:pre-wrap;font-size:12px;margin:4px 0;font-family:var(--font-mono);">{{ d.risk_assessment }}</pre>
              </div>
              <div v-if="d.orders?.length" style="margin-bottom:8px;">
                <b style="font-size:11px;">订单:</b>
                <table style="margin-top:4px;">
                  <thead><tr><th>方向</th><th>代码</th><th>名称</th><th>数量</th><th>价格</th><th>理由</th></tr></thead>
                  <tbody>
                    <tr v-for="o in d.orders" :key="o.ts_code+o.direction">
                      <td><span class="tag" :class="o.direction==='buy'?'tag-buy':'tag-sell'">{{ o.direction==='buy'?'买入':'卖出' }}</span></td>
                      <td class="mono">{{ o.ts_code }}</td>
                      <td>{{ o.stock_name || '-' }}</td>
                      <td class="mono">{{ o.quantity }}</td>
                      <td class="mono">{{ o.price?.toFixed(2)||'-' }}</td>
                      <td style="font-size:11px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" :title="o.reason">{{ o.reason }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <details>
                <summary style="cursor:pointer;font-size:11px;color:var(--text-dim);">LLM 完整分析</summary>
                <pre style="white-space:pre-wrap;font-size:12px;line-height:1.6;margin-top:8px;padding:12px;background:#f8f9fb;border-radius:4px;max-height:400px;overflow-y:auto;font-family:var(--font-mono);">{{ d.analysis }}</pre>
              </details>
            </div>
          </div>
        </div>
        <div v-else style="text-align:center;padding:20px;color:var(--text-dim);">选择 Agent 查看决策详情</div>
      </div>
    </div>

    <!-- History -->
    <div class="card" style="margin-top:16px;" v-if="simTasks.length">
      <h3>历史模拟</h3>
      <table>
        <thead><tr><th>ID</th><th>名称</th><th>区间</th><th>状态</th><th>时间</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="t in simTasks" :key="t.id">
            <td class="mono">#{{ t.id }}</td>
            <td>{{ t.name }}</td>
            <td class="mono">{{ t.start_date }}-{{ t.end_date }}</td>
            <td><span :style="{color:t.status==='completed'?'var(--accent-green)':t.status==='failed'?'var(--accent-red)':'var(--accent-cyan)'}">{{ t.status }}</span></td>
            <td style="font-size:11px;">{{ formatDateTimeCN(t.created_at) }}</td>
            <td>
              <button v-if="t.status==='completed'" class="btn btn-sm" @click="loadResult(t.id)">查看</button>
              <button class="btn btn-sm" style="margin-left:4px;color:var(--accent-red);" @click="deleteSim(t.id)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { strategyAPI } from '../api'
import { formatDateTimeCN, todayCN, offsetDateCN } from '../utils/time'
const JSON = window.JSON

const strategies = ref([])
const simName = ref('')
const startDate = ref('')
const endDate = ref('')
const presetPeriod = ref('1m')
const simAgents = ref([{ display_name: 'Agent1', strategy_name: 'ma_pullback', initial_capital: 150000, reasoning_effort: 'high' }])
const simRunning = ref(false)
const simProgress = ref(0)
const simResult = ref(null)
const simTasks = ref([])
const selectedAgent = ref(0)
const equityCompareChart = ref(null)
let eqCompInst = null

const expandedDates = ref([])
function toggleExpanded(date) {
  expandedDates.value = expandedDates.value.includes(date)
    ? expandedDates.value.filter(d => d !== date)
    : [...expandedDates.value, date]
}
function isExpanded(date) { return expandedDates.value.includes(date) }

const currentDecisions = computed(() => {
  if (!simResult.value?.agents) return []
  const agent = simResult.value.agents.find(a => a.agent_id === selectedAgent.value)
  return agent?.decisions || []
})

onMounted(async () => {
  const s = await strategyAPI.builtin()
  strategies.value = s.data.strategies || []
  endDate.value = todayCN()
  startDate.value = offsetDateCN(0, -1)
  loadTasks()
})

function applyPreset() {
  const today = todayCN()
  if (presetPeriod.value === '1w') startDate.value = offsetDateCN(-7, 0)
  else if (presetPeriod.value === '1m') startDate.value = offsetDateCN(0, -1)
  else if (presetPeriod.value === '1q') startDate.value = offsetDateCN(0, -3)
  else if (presetPeriod.value === 'ytd') startDate.value = `${today.slice(0,4)}0101`
  else return
  endDate.value = today
}

function addAgent() {
  simAgents.value.push({ display_name: `Agent${simAgents.value.length+1}`, strategy_name: 'ma_pullback', initial_capital: 150000, reasoning_effort: 'high' })
}
function removeAgent(idx) { simAgents.value.splice(idx, 1) }

async function loadTasks() {
  try {
    const r = await fetch('/api/simulation/tasks?limit=20')
    const d = await r.json()
    simTasks.value = d.tasks || []
  } catch(e){}
}

async function startSimulation() {
  simRunning.value = true; simProgress.value = 0; simResult.value = null
  const body = {
    name: simName.value || `模拟_${startDate.value}_${endDate.value}`,
    agents: simAgents.value,
    start_date: startDate.value,
    end_date: endDate.value,
  }
  try {
    const r = await fetch('/api/simulation/start', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    })
    const d = await r.json()
    const simId = d.id
    // Poll for completion
    const poll = setInterval(async () => {
      const sr = await fetch(`/api/simulation/status/${simId}`)
      const sd = await sr.json()
      if (sd.status === 'completed' || sd.status === 'failed') {
        clearInterval(poll)
        simRunning.value = false
        if (sd.status === 'completed') {
          await loadResult(simId)
        }
        loadTasks()
      }
      simProgress.value = sd.status === 'completed' ? 100 : Math.min(sd.progress || 0, 99)
    }, 2000)
  } catch(e) {
    simRunning.value = false
  }
}

async function loadResult(simId) {
  const r = await fetch(`/api/simulation/result/${simId}`)
  const d = await r.json()
  if (d.results) {
    simResult.value = d.results
    selectedAgent.value = d.results.agents?.[0]?.agent_id || 0
    await nextTick()
    renderEquityCompare()
  }
}

async function deleteSim(simId) {
  await fetch(`/api/simulation/task/${simId}`, { method: 'DELETE' })
  loadTasks()
}

function renderEquityCompare() {
  if (!simResult.value?.agents?.length || !equityCompareChart.value) return
  if (eqCompInst) eqCompInst.dispose()
  eqCompInst = echarts.init(equityCompareChart.value)
  const colors = ['#b8860b', '#0891b2', '#7c3aed', '#dc2626', '#059669']
  const allDates = new Set()
  const series = simResult.value.agents.map((a, i) => {
    const map = {}
    for (const pt of (a.equity_curve || [])) {
      allDates.add(pt.date)
      map[pt.date] = pt.return_pct
    }
    return { name: a.display_name, map, color: colors[i%colors.length] }
  })
  const dates = [...allDates].sort()
  const seriesData = series.map(s => ({
    name: s.name, type: 'line', data: dates.map(d => s.map[d] ?? null),
    lineStyle: { color: s.color, width: 2 }, showSymbol: false,
  }))
  eqCompInst.setOption({
    backgroundColor:'transparent', tooltip:{trigger:'axis'},
    legend:{data:series.map(s=>s.name),bottom:0,textStyle:{color:'#9ca3af',fontSize:11}},
    grid:{left:60,right:16,top:16,bottom:40},
    xAxis:{type:'category',data:dates,axisLabel:{color:'#9ca3af',fontSize:9,formatter:v=>String(v).slice(4)},axisLine:{lineStyle:{color:'#e2e5ea'}}},
    yAxis:{type:'value',axisLabel:{color:'#9ca3af',formatter:'{value}%'},splitLine:{lineStyle:{color:'#e2e5ea',type:'dashed'}}},
    series:seriesData,
  })
}
</script>

<style scoped>
.mono { font-family: var(--font-mono); }
.agent-config-row { display:flex; gap:8px; align-items:center; margin-bottom:6px; }
.tag { display:inline-block; padding:2px 6px; border-radius:3px; font-size:10px; font-weight:600; font-family:var(--font-mono); }
.tag-buy { background:#fce4e4; color:var(--accent-red); }
.tag-sell { background:#e4f5ec; color:var(--accent-green); }

.decision-list { max-height:600px; overflow-y:auto; }
.decision-item { border:1px solid var(--border); border-radius:6px; margin-bottom:6px; overflow:hidden; }
.decision-header { display:flex; align-items:center; padding:10px 14px; background:var(--bg-deep); cursor:pointer; transition:background .15s; }
.decision-header:hover { background:#eef0f3; }
.decision-body { padding:14px; border-top:1px solid var(--border); }
</style>
