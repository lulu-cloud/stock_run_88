<template>
  <div>
    <h2 style="font-family:var(--font-mono);font-size:16px;margin-bottom:16px;">策略回测</h2>

    <div class="card" style="margin-bottom:16px;">
      <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;">
        <select v-model="strategy" style="width:180px;font-size:12px;">
          <option v-for="s in strategies" :key="s.name" :value="s.name">{{ s.name }}</option>
        </select>
        <select v-model="period" style="width:160px;font-size:12px;">
          <option v-for="(label, key) in periods" :key="key" :value="key">{{ label }}</option>
        </select>
        <button class="btn btn-primary btn-sm" @click="runBacktest" :disabled="loading">
          {{ loading ? '运行中...' : '运行回测' }}
        </button>
        <span v-if="currentTaskId" style="font-size:10px;color:var(--text-dim);">
          已保存 #{{ currentTaskId }}
        </span>
      </div>
    </div>

    <!-- Metrics -->
    <div class="grid-4" style="margin-bottom:16px;" v-if="metrics">
      <div class="stat-card">
        <span class="slabel">累计收益</span>
        <span class="sval" :class="metrics.total_return >= 0 ? 'green' : 'red'">{{ metrics.total_return >= 0 ? '+' : '' }}{{ metrics.total_return }}%</span>
      </div>
      <div class="stat-card"><span class="slabel">年化收益</span><span class="sval">{{ metrics.annual_return }}%</span></div>
      <div class="stat-card"><span class="slabel">最大回撤</span><span class="sval red">{{ metrics.max_drawdown }}%</span></div>
      <div class="stat-card"><span class="slabel">胜率</span><span class="sval">{{ metrics.win_rate }}%</span></div>
      <div class="stat-card"><span class="slabel">交易次数</span><span class="sval">{{ metrics.total_trades }}</span></div>
      <div class="stat-card"><span class="slabel">盈亏比</span><span class="sval">{{ metrics.profit_factor }}</span></div>
      <div class="stat-card"><span class="slabel">夏普</span><span class="sval">{{ metrics.sharpe_ratio }}</span></div>
      <div class="stat-card">
        <span class="slabel">最终盈亏</span>
        <span class="sval" :class="metrics.final_profit >= 0 ? 'green' : 'red'">{{ metrics.final_profit >= 0 ? '+' : '' }}{{ (metrics.final_profit / 10000).toFixed(1) }}万</span>
      </div>
    </div>

    <!-- Equity Curve -->
    <div class="card" v-if="equityCurve.length">
      <h3>净值曲线</h3>
      <div ref="equityChart" style="width:100%;height:380px;"></div>
    </div>

    <!-- Trade Log -->
    <div class="card" style="margin-top:16px;" v-if="trades.length || runLog.length">
      <h3>交易明细 & 回放日志</h3>

      <div v-if="trades.length" style="margin-bottom:16px;">
        <table>
          <thead>
            <tr>
              <th>日期</th><th>方向</th><th>代码</th><th>名称</th>
              <th style="text-align:right;">价格</th><th style="text-align:right;">数量</th>
              <th style="text-align:right;">金额</th><th style="text-align:right;">手续费</th>
              <th style="text-align:right;">盈亏</th>
              <th style="text-align:right;">理由</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(t, idx) in trades" :key="idx">
              <td>{{ t.date }}</td>
              <td>
                <span class="tag" :class="t.direction==='buy'?'tag-buy':'tag-sell'">
                  {{ t.direction==='buy'?'买入':'卖出' }}
                </span>
              </td>
              <td class="mono">{{ t.ts_code }}</td>
              <td class="mono" style="font-size:12px;">{{ t.name || '-' }}</td>
              <td class="mono" style="text-align:right;">{{ t.price?.toFixed(2) }}</td>
              <td class="mono" style="text-align:right;">{{ t.quantity }}</td>
              <td class="mono" style="text-align:right;">{{ (t.total_value/10000).toFixed(1) }}万</td>
              <td class="mono" style="text-align:right;font-size:11px;color:var(--text-dim);">
                {{ ((t.commission||0) + (t.stamp_tax||0)).toFixed(2) }}
              </td>
              <td class="mono" style="text-align:right;" :class="t.pnl>=0?'green':'red'">
                {{ t.pnl ? (t.pnl>=0?'+':'')+t.pnl.toFixed(2) : '-' }}
              </td>
              <td style="font-size:11px;color:var(--text-dim);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" :title="t.reason">
                {{ t.reason || '-' }}
              </td>
              <td>
                <button class="btn btn-sm" style="padding:2px 8px;font-size:10px;" @click="openBSPopup(t.ts_code, t.name)">BS点</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Run Log -->
      <div v-if="runLog.length" class="run-log-box" ref="logBox">
        <div v-for="(entry, idx) in runLog" :key="idx" class="log-line" :class="'log-'+entry.level.toLowerCase()">
          <span class="log-ts">{{ entry.ts }}</span>
          <span class="log-tag">{{ entry.level }}</span>
          <span class="log-date" v-if="entry.date">{{ entry.date }}</span>
          <span class="log-msg">{{ entry.message }}</span>
        </div>
      </div>
    </div>

    <!-- Saved Tasks -->
    <div class="card" style="margin-top:16px;" v-if="savedTasks.length">
      <h3>历史回测记录</h3>
      <table>
        <thead>
          <tr><th>ID</th><th>策略</th><th>区间</th><th>累计收益</th><th>最终盈亏</th><th>交易次数</th><th>时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in savedTasks" :key="t.id" style="cursor:pointer;" @click="loadTask(t.id)"
              :class="{ 'row-selected': t.id === currentTaskId }">
            <td class="mono">#{{ t.id }}</td>
            <td>{{ t.strategy_name }}</td>
            <td class="mono">{{ t.start_date }} - {{ t.end_date }}</td>
            <td class="mono" :class="(t.metrics?.total_return||0)>=0?'green':'red'">
              {{ (t.metrics?.total_return||0)>=0?'+':'' }}{{ t.metrics?.total_return || 0 }}%
            </td>
            <td class="mono" :class="(t.metrics?.final_profit||0)>=0?'green':'red'">
              {{ (t.metrics?.final_profit||0)>=0?'+':'' }}{{ ((t.metrics?.final_profit||0)/10000).toFixed(1) }}万
            </td>
            <td class="mono">{{ t.metrics?.total_trades || 0 }}</td>
            <td style="font-size:11px;color:var(--text-dim);">{{ formatDateTimeCN(t.created_at) }}</td>
            <td>
              <button class="btn btn-sm" style="padding:2px 8px;font-size:10px;margin-right:4px;" @click.stop="loadTask(t.id)">加载</button>
              <button class="btn btn-sm btn-danger" style="padding:2px 8px;font-size:10px;" @click.stop="deleteTask(t.id)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- BS Popup Modal -->
    <div v-if="showBSPopup" class="modal-overlay" @click.self="showBSPopup=false">
      <div class="modal-card" style="max-width:1100px;width:96%;">
        <div class="card-header-row">
          <h3>{{ bsTsCode }} {{ bsName }} 买卖点</h3>
          <button class="btn btn-sm" @click="showBSPopup=false">关闭</button>
        </div>
        <div v-if="bsLoading" class="biz-loading"><div class="spin-big"></div><p>加载K线数据...</p></div>
        <div v-else ref="bsChart" style="width:100%;height:520px;"></div>
      </div>
    </div>

    <div v-if="loading" style="text-align:center;padding:40px;color:var(--text-dim);">回测运行中...</div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { strategyAPI, backtestAPI } from '../api'
import { formatDateTimeCN } from '../utils/time'

const strategies = ref([])
const periods = ref({})
const strategy = ref('ma_pullback')
const period = ref('1m')
const loading = ref(false)
const metrics = ref(null)
const equityCurve = ref([])
const trades = ref([])
const runLog = ref([])
const currentTaskId = ref(null)
const savedTasks = ref([])

const equityChart = ref(null)
const logBox = ref(null)
let eqInst = null

// BS popup
const showBSPopup = ref(false)
const bsTsCode = ref('')
const bsName = ref('')
const bsLoading = ref(false)
const bsChart = ref(null)
let bsInst = null

onMounted(async () => {
  const [s, p] = await Promise.all([
    strategyAPI.builtin(),
    backtestAPI.periods(),
    loadSavedTasks(),
  ])
  strategies.value = s.data.strategies
  periods.value = p.data.periods
})

async function loadSavedTasks() {
  try {
    const r = await backtestAPI.tasks(20)
    savedTasks.value = r.data.tasks || []
  } catch (e) { /* ignore */ }
}

async function runBacktest() {
  loading.value = true
  metrics.value = null
  equityCurve.value = []
  trades.value = []
  runLog.value = []
  currentTaskId.value = null

  const res = await backtestAPI.quick(strategy.value, period.value)
  const d = res.data
  if (d.metrics) {
    currentTaskId.value = d.id || null
    metrics.value = d.metrics
    equityCurve.value = d.equity_curve || []
    trades.value = d.trades || []
    runLog.value = d.log || []

    await nextTick()
    renderEquityChart()
    await nextTick()
    if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight
  }
  loading.value = false
  loadSavedTasks()
}

async function loadTask(taskId) {
  metrics.value = null
  equityCurve.value = []
  trades.value = []
  runLog.value = []

  const r = await backtestAPI.task(taskId)
  const t = r.data.task
  if (!t) return

  currentTaskId.value = t.id
  strategy.value = t.strategy_name
  metrics.value = t.metrics || {}
  equityCurve.value = t.equity_curve || []
  trades.value = t.trades || []
  runLog.value = t.log || []

  await nextTick()
  renderEquityChart()
  await nextTick()
  if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight
}

async function deleteTask(taskId) {
  await backtestAPI.deleteTask(taskId)
  if (currentTaskId.value === taskId) {
    currentTaskId.value = null
    metrics.value = null
    equityCurve.value = []
    trades.value = []
    runLog.value = []
  }
  loadSavedTasks()
}

function renderEquityChart() {
  if (!equityCurve.value.length || !equityChart.value) return
  if (eqInst) eqInst.dispose()
  eqInst = echarts.init(equityChart.value)
  const dates = equityCurve.value.map(e => e.date)
  eqInst.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    grid: { left: 60, right: 16, top: 14, bottom: 30 },
    xAxis: {
      type: 'category', data: dates,
      axisLabel: { color: '#9ca3af', fontSize: 10, formatter: v => String(v).slice(4) },
      axisLine: { lineStyle: { color: '#e2e5ea' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#9ca3af', fontSize: 10 },
      splitLine: { lineStyle: { color: '#e2e5ea', type: 'dashed' } },
    },
    series: [{
      type: 'line', data: equityCurve.value.map(e => e.total_assets),
      lineStyle: { color: '#b8860b', width: 1.5 },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: 'rgba(184,134,11,0.15)' },
        { offset: 1, color: 'rgba(184,134,11,0.01)' },
      ])},
      showSymbol: false,
    }],
  })
}

// BS Popup - K-line with buy/sell markers
async function openBSPopup(tsCode, name) {
  bsTsCode.value = tsCode
  bsName.value = name || ''
  showBSPopup.value = true
  bsLoading.value = true

  let raw = null
  try {
    const resp = await fetch(`/api/market/stock/kline/${encodeURIComponent(tsCode)}?days=500`)
    const d = await resp.json()
    if (d.data?.length) raw = d.data
  } catch (e) { console.error('BS K-line fetch error:', e) }

  bsLoading.value = false
  if (!raw) return

  // Wait for v-else to render chart div (retry loop)
  for (let retry = 0; retry < 10 && !bsChart.value; retry++) {
    await nextTick()
  }
  if (!bsChart.value) return

  try {
    if (bsInst) { bsInst.dispose(); bsInst = null }
    bsInst = echarts.init(bsChart.value)
  } catch (e) {
    console.error('BS chart init error:', e)
    return
  }

  const dates = raw.map(r => String(r.trade_date))
  const ohlc = raw.map(r => [r.open, r.close, r.low, r.high])
  const vols = raw.map(r => r.vol || 0)
  const ma5 = raw.map(r => r.ma5 ?? null)
  const ma10 = raw.map(r => r.ma10 ?? null)
  const ma20 = raw.map(r => r.ma20 ?? null)
  const ma60 = raw.map(r => r.ma60 ?? null)
  const vc = vols.map((v, i) => i > 0 ? (raw[i].close >= raw[i-1].close ? '#dc2626' : '#059669') : '#dc2626')

  // Build BS markers for this stock from trades
  const stockTrades = trades.value.filter(t => t.ts_code === bsTsCode.value)
  const buyMarks = []
  const sellMarks = []
  for (const t of stockTrades) {
    const di = dates.indexOf(t.date)
    if (di < 0) continue
    const td = raw[di]
    if (t.direction === 'buy') {
      buyMarks.push({
        name: 'B', coord: [di, td.low],
        value: 'B', symbol: 'triangle', symbolSize: 16,
        symbolRotate: 0, itemStyle: { color: '#dc2626' },
        label: { show: true, position: 'bottom', fontSize: 11, fontWeight: 'bold', color: '#dc2626', distance: 6 },
      })
    } else {
      sellMarks.push({
        name: 'S', coord: [di, td.high],
        value: 'S', symbol: 'triangle', symbolSize: 16,
        symbolRotate: 180, itemStyle: { color: '#059669' },
        label: { show: true, position: 'top', fontSize: 11, fontWeight: 'bold', color: '#059669', distance: 6 },
      })
    }
  }

  try {
    bsInst.setOption({
      backgroundColor: 'transparent', animation: false,
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross' },
      formatter: p => {
        if (!p?.length) return ''
        const i = p[0].dataIndex; const r = raw[i]
        return `${r.trade_date}<br/>开:${r.open.toFixed(2)} 收:${r.close.toFixed(2)} 高:${r.high.toFixed(2)} 低:${r.low.toFixed(2)}<br/>涨跌:${r.pct_chg?.toFixed(2)||'-'}% 换手:${r.turnover_rate?.toFixed(2)||'-'}%`
      },
    },
    toolbox: { right: 8, top: 4, feature: { dataZoom: { yAxisIndex: 'none', title: { zoom: '区域缩放', back: '还原' } }, restore: { title: '重置' } } },
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: true },
      { type: 'slider', xAxisIndex: [0, 1], start: 90, end: 100, height: 22, bottom: 4, borderColor: '#d1d5db', backgroundColor: '#f0f2f5', fillerColor: 'rgba(184,134,11,0.15)', handleStyle: { color: '#b8860b' }, textStyle: { color: '#5a5d6e', fontSize: 10 } },
    ],
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [{ left: 70, right: 18, top: 30, height: '60%' }, { left: 70, right: 18, top: '76%', height: '16%' }],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { color: '#9ca3af', fontSize: 9, formatter: v => String(v).slice(4) }, axisLine: { lineStyle: { color: '#e2e5ea' } }, axisTick: { show: false } },
      { type: 'category', data: dates, gridIndex: 1, axisLabel: { show: false }, axisLine: { lineStyle: { color: '#e2e5ea' } }, axisTick: { show: false } },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, scale: true, axisLabel: { color: '#9ca3af', fontSize: 10 }, splitLine: { lineStyle: { color: '#e2e5ea', type: 'dashed' } } },
      { type: 'value', gridIndex: 1, axisLabel: { color: '#9ca3af', fontSize: 8, formatter: v => v>1e8?(v/1e8).toFixed(1)+'亿':(v/1e4).toFixed(0)+'万' }, splitLine: { show: false } },
    ],
    series: [
      { type: 'candlestick', data: ohlc, xAxisIndex: 0, yAxisIndex: 0, itemStyle: { color: '#dc2626', color0: '#059669', borderColor: '#dc2626', borderColor0: '#059669' }, markPoint: { data: [...buyMarks, ...sellMarks] } },
      { type: 'line', data: ma5, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#000', width: 1 }, name: 'MA5' },
      { type: 'line', data: ma10, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#ff8c00', width: 1 }, name: 'MA10' },
      { type: 'line', data: ma20, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#dc143c', width: 1 }, name: 'MA20' },
      { type: 'line', data: ma60, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#00aa6c', width: 1.5 }, name: 'MA60' },
      { type: 'bar', data: vols.map((v, i) => ({ value: v, itemStyle: { color: vc[i] } })), xAxisIndex: 1, yAxisIndex: 1 },
    ],
    })
  } catch (e) {
    console.error('BS chart setOption error:', e)
    if (bsInst) { bsInst.dispose(); bsInst = null }
  }
}

// Cleanup BS chart on modal close
import { watch } from 'vue'
watch(showBSPopup, v => {
  if (!v && bsInst) { bsInst.dispose(); bsInst = null }
})
</script>

<style scoped>
.mono { font-family: var(--font-mono); }
.stat-card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 6px; padding: 14px 16px; text-align: center;
}
.slabel { display: block; font-size: 10px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 1px; }
.sval { font-family: var(--font-mono); font-size: 18px; font-weight: 600; margin-top: 4px; display: block; }

.tag { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 11px; font-weight: 600; font-family: var(--font-mono); }
.tag-buy { background: #fce4e4; color: var(--accent-red); }
.tag-sell { background: #e4f5ec; color: var(--accent-green); }

.row-selected { background: #fef9f0 !important; }
.row-selected td { background: #fef9f0 !important; }

/* Run Log */
.run-log-box {
  max-height: 420px; overflow-y: auto;
  background: #f8f9fb; border: 1px solid var(--border);
  border-radius: 6px; padding: 12px;
  font-family: var(--font-mono); font-size: 12px; line-height: 1.8;
}
.log-line { display: flex; gap: 8px; padding: 1px 0; align-items: baseline; }
.log-ts { color: #b0b7c3; font-size: 10px; min-width: 70px; flex-shrink: 0; }
.log-tag { font-weight: 600; font-size: 10px; min-width: 56px; flex-shrink: 0; text-align: center; border-radius: 2px; padding: 0 4px; }
.log-date { color: #6b7280; min-width: 72px; flex-shrink: 0; }
.log-msg { color: #374151; flex: 1; }

.log-buy .log-tag { background: #fce4e4; color: #dc2626; }
.log-sell .log-tag { background: #e4f5ec; color: #059669; }
.log-stop .log-tag { background: #fef3c7; color: #d97706; }
.log-skip .log-tag { background: #f3f4f6; color: #6b7280; }
.log-signal .log-tag { background: #e0f2fe; color: #0891b2; }
.log-summary .log-tag { background: #ede9fe; color: #7c3aed; }
.log-info .log-tag { background: transparent; color: #9ca3af; }
.log-error .log-tag { background: #fce4e4; color: #dc2626; }

/* Modal */
.card-header-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.card-header-row h3 { margin-bottom: 0; }
.biz-loading { text-align: center; padding: 50px 20px; }
.spin-big { width: 40px; height: 40px; margin: 0 auto 16px; border: 3px solid var(--border); border-top-color: var(--accent-gold); border-radius: 50%; animation: sp .8s linear infinite; }
@keyframes sp { to { transform: rotate(360deg); } }
</style>
