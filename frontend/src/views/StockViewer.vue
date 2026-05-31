<template>
  <div>
    <div class="page-header">
      <h2>K线分析</h2>
      <div class="search-wrapper">
        <div class="search-input-wrap">
          <input v-model="searchQuery" placeholder="输入股票代码或名称搜索..."
                 class="search-input" @input="onSearchInput" @focus="showDropdown = true"
                 @keyup.enter="selectTopResult" />
          <div v-if="searchQuery" class="search-clear" @click="clearSearch">✕</div>
        </div>
        <div v-if="showDropdown && searchResults.length" class="search-dropdown">
          <div v-for="s in searchResults" :key="s.ts_code" class="search-item"
               @click="selectStock(s)">
            <span class="si-code">{{ s.ts_code }}</span>
            <span class="si-name">{{ s.name }}</span>
          </div>
        </div>
      </div>
      <select v-if="holdings.length" v-model="selectedHolding" @change="onHoldingSelect" class="holding-select">
        <option value="">-- Agent 持仓 --</option>
        <option v-for="h in holdings" :key="h.ts_code" :value="h.ts_code">
          {{ h.ts_code }} {{ h.stock_name }}
        </option>
      </select>
      <button v-if="stockInfo" class="btn btn-sm" style="white-space:nowrap;" @click="saveCurrentKline">
        + 保存当前
      </button>
    </div>

    <!-- Saved K-line Tabs -->
    <div v-if="savedStocks.length" class="saved-tabs">
      <span class="saved-label">已保存:</span>
      <span v-for="(s, idx) in savedStocks" :key="s.ts_code" class="saved-tab"
            :class="{active: currentTsCode === s.ts_code}"
            @click="loadSavedStock(s)">
        <span class="tab-code">{{ s.ts_code }}</span>
        <span class="tab-name">{{ s.name }}</span>
        <span class="tab-close" @click.stop="removeSavedStock(idx)">✕</span>
      </span>
      <button class="btn btn-sm" @click="toggleCompareMode">
        {{ compareMode ? '退出并排' : '并排对比' }}
      </button>
    </div>

    <div v-if="compareMode && savedStocks.length" class="compare-panel">
      <div class="compare-toolbar">
        <span class="saved-label">对比个股</span>
        <label v-for="s in savedStocks" :key="'cmp'+s.ts_code" class="compare-check">
          <input type="checkbox" :value="s.ts_code" v-model="compareCodes" :disabled="!compareCodes.includes(s.ts_code) && compareCodes.length>=4" />
          {{ s.ts_code }} {{ s.name }}
        </label>
      </div>
      <div class="compare-grid" :class="'cols-'+Math.min(compareChartsData.length || 1, 4)">
        <div v-for="item in compareChartsData" :key="item.ts_code" class="compare-card">
          <div class="compare-title">
            <span class="mono">{{ item.ts_code }}</span>
            <span>{{ item.name }}</span>
          </div>
          <div :ref="el => setCompareChartRef(item.ts_code, el)" class="compare-chart"></div>
        </div>
      </div>
    </div>

    <!-- Stock Info Bar -->
    <div v-if="stockInfo" class="info-bar">
      <div class="info-item"><span class="ilabel">名称</span><span class="ival">{{ stockInfo.name }}</span></div>
      <div class="info-item"><span class="ilabel">最新价</span><span class="ival" :class="stockInfo.pct_chg>=0?'green':'red'">{{ stockInfo.close.toFixed(2) }}</span></div>
      <div class="info-item"><span class="ilabel">涨跌幅</span><span class="ival" :class="stockInfo.pct_chg>=0?'green':'red'">{{ stockInfo.pct_chg>=0?'+':'' }}{{ stockInfo.pct_chg.toFixed(2) }}%</span></div>
      <div class="info-item"><span class="ilabel">MA5</span><span class="ival" style="color:#000;background:#f0f0f0;border-radius:3px;padding:0 4px;">{{ stockInfo.ma5?.toFixed(2)||'-' }}</span></div>
      <div class="info-item"><span class="ilabel">MA10</span><span class="ival" style="color:#ff8c00">{{ stockInfo.ma10?.toFixed(2)||'-' }}</span></div>
      <div class="info-item"><span class="ilabel">MA20</span><span class="ival" style="color:#dc143c">{{ stockInfo.ma20?.toFixed(2)||'-' }}</span></div>
      <div class="info-item"><span class="ilabel">MA60</span><span class="ival" style="color:#00aa6c">{{ stockInfo.ma60?.toFixed(2)||'-' }}</span></div>
      <div class="info-item"><span class="ilabel">换手</span><span class="ival">{{ stockInfo.turnover?.toFixed(2)||'-' }}%</span></div>
    </div>

    <!-- K-line Chart -->
    <div class="card" style="margin-bottom:16px;position:relative;" v-if="stockInfo">
      <div ref="klineChart" style="width:100%;height:560px;"></div>
      <!-- Floating business button -->
      <button class="biz-float" @click="openBizModal" :title="bizCached?'查看业务详情':'查看业务详情'">
        <span v-if="bizLoading">⏳</span>
        <span v-else>📄</span>
      </button>
    </div>

    <!-- Related Positions -->
    <div v-if="relatedPositions.length" class="card">
      <h3>Agent 持仓参考</h3>
      <table>
        <thead><tr><th>Agent</th><th>持仓量</th><th>成本</th><th>现价</th><th>浮动盈亏</th></tr></thead>
        <tbody>
          <tr v-for="p in relatedPositions" :key="p.agent_name">
            <td>{{ p.agent_name }}</td><td>{{ p.quantity }}</td>
            <td>{{ p.avg_cost.toFixed(2) }}</td><td>{{ p.current_price.toFixed(2) }}</td>
            <td :class="p.unrealized_pnl>=0?'green':'red'">{{ p.unrealized_pnl>=0?'+':'' }}{{ p.unrealized_pnl.toFixed(2) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Business Modal -->
    <div v-if="showBizModal" class="modal-overlay" @click.self="showBizModal=false">
      <div class="modal-card" style="max-width:750px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
          <h3>{{ bizTitle }} 业务详情</h3>
          <button class="btn btn-sm" @click="showBizModal=false">关闭</button>
        </div>
        <div v-if="bizContent" class="md-content" v-html="renderMD(bizContent)"></div>
        <div v-else-if="bizLoading" style="text-align:center;padding:40px;color:var(--text-dim);">
          <div class="spin-big"></div>
          <p style="margin-top:12px;">正在加载业务数据...</p>
        </div>
        <div v-else style="text-align:center;padding:40px;color:var(--text-dim);">
          <p style="margin-bottom:12px;">暂无缓存业务信息</p>
          <button class="btn btn-primary" @click="refreshBizData">刷新业务信息</button>
        </div>
      </div>
    </div>

    <!-- Empty state -->
    <div v-if="!stockInfo" class="empty-state">
      <div class="empty-icon">⬒</div>
      <p>输入股票代码或名称开始分析</p>
      <p class="empty-hint">支持代码模糊搜索和名称搜索</p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import * as echarts from 'echarts'
import { marked } from 'marked'
import { agentAPI, companyAPI } from '../api'

const route = useRoute()
function renderMD(md) { return md ? marked(md, { breaks: true }) : '' }

const searchQuery = ref('')
const searchResults = ref([])
const showDropdown = ref(false)
const selectedHolding = ref('')
const stockInfo = ref(null)
const holdings = ref([])
const relatedPositions = ref([])
const klineChart = ref(null)
let chartInst = null
const compareMode = ref(false)
const compareCodes = ref([])
const compareChartsData = ref([])
const compareChartRefs = new Map()
const compareChartInst = new Map()
let searchTimer = null

// Saved K-line tabs
const currentTsCode = ref('')
const savedStocks = ref(loadSavedStocks())

function loadSavedStocks() {
  try {
    return JSON.parse(localStorage.getItem('savedKlines') || '[]')
  } catch { return [] }
}
function persistSavedStocks() {
  localStorage.setItem('savedKlines', JSON.stringify(savedStocks.value))
}
function saveCurrentKline() {
  const code = currentTsCode.value
  const name = stockInfo.value?.name || code
  if (!code) return
  if (savedStocks.value.some(s => s.ts_code === code)) return
  savedStocks.value.push({ ts_code: code, name, savedAt: new Date().toISOString() })
  persistSavedStocks()
  if (compareMode.value && compareCodes.value.length < 4) compareCodes.value.push(code)
}
function loadSavedStock(s) {
  searchQuery.value = `${s.ts_code} ${s.name}`
  loadStock(s.ts_code)
}
function removeSavedStock(idx) {
  const removed = savedStocks.value[idx]
  savedStocks.value.splice(idx, 1)
  compareCodes.value = compareCodes.value.filter(c => c !== removed?.ts_code)
  persistSavedStocks()
}

function toggleCompareMode() {
  compareMode.value = !compareMode.value
  if (compareMode.value && !compareCodes.value.length) {
    compareCodes.value = savedStocks.value.slice(0, 2).map(s => s.ts_code)
  }
}

function setCompareChartRef(code, el) {
  if (el) compareChartRefs.set(code, el)
}

// Business data
const showBizModal = ref(false)
const bizTitle = ref('')
const bizTsCode = ref('')
const bizContent = ref('')
const bizLoading = ref(false)
const bizCached = ref(false)

onMounted(async () => {
  try {
    const r = await agentAPI.list()
    const agents = r.data.agents || []
    const posMap = new Map()
    for (const a of agents) {
      try {
        const d = await agentAPI.get(a.id)
        for (const p of d.data.positions || []) {
          if (!posMap.has(p.ts_code)) posMap.set(p.ts_code, { ts_code: p.ts_code, stock_name: p.stock_name })
        }
      } catch (e) {}
    }
    holdings.value = [...posMap.values()]
  } catch (e) {}

  if (route.query.code) {
    searchQuery.value = route.query.code
    loadStock(route.query.code)
  }
})

function onSearchInput() {
  clearTimeout(searchTimer)
  if (!searchQuery.value.trim()) { searchResults.value = []; showDropdown.value = false; return }
  searchTimer = setTimeout(async () => {
    try {
      const r = await fetch(`/api/market/stocks/search?q=${encodeURIComponent(searchQuery.value.trim())}`)
      const d = await r.json()
      searchResults.value = d.results || []
      showDropdown.value = true
    } catch (e) {}
  }, 150)
}

function selectStock(s) {
  searchQuery.value = `${s.ts_code} ${s.name}`
  showDropdown.value = false; searchResults.value = []
  loadStock(s.ts_code)
}

function selectTopResult() {
  if (searchResults.value.length > 0) selectStock(searchResults.value[0])
}

function clearSearch() { searchQuery.value = ''; searchResults.value = []; showDropdown.value = false; stockInfo.value = null }

function onHoldingSelect() {
  if (selectedHolding.value) {
    const h = holdings.value.find(x => x.ts_code === selectedHolding.value)
    searchQuery.value = h ? `${h.ts_code} ${h.stock_name}` : selectedHolding.value
    loadStock(selectedHolding.value)
  }
}

async function loadBizData(tsCode, name) {
  bizTsCode.value = tsCode; bizTitle.value = `${tsCode} ${name||''}`
  bizLoading.value = true; bizContent.value = ''; bizCached.value = false
  try {
    const cached = await companyAPI.getBusiness(tsCode)
    if (cached.data.cached && cached.data.content) {
      bizContent.value = cached.data.content
      bizCached.value = true
    }
  } catch (e) { console.error('Biz load error:', e) }
  bizLoading.value = false
}

function openBizModal() {
  if (!bizTsCode.value) return
  showBizModal.value = true
}

async function refreshBizData() {
  if (!bizTsCode.value) return
  bizLoading.value = true
  try {
    const res = await companyAPI.search({ ts_code: bizTsCode.value, name: stockInfo.value?.name || '' })
    if (res.data.content) {
      bizContent.value = res.data.content
      bizCached.value = false
    }
  } catch (e) { console.error('Biz refresh error:', e) }
  bizLoading.value = false
}

async function loadStock(code) {
  stockInfo.value = null; relatedPositions.value = []
  currentTsCode.value = code
  // 默认只读缓存；联网搜索由用户在弹窗中点击“刷新业务信息”触发。
  loadBizData(code, '')

  try {
    const resp = await fetch(`/api/market/stock/kline/${code.trim()}?days=2000`)
    const d = await resp.json()
    if (d.error || !d.data?.length) return

    const raw = d.data
    const last = raw[raw.length - 1]
    stockInfo.value = {
      name: d.name || code,
      close: last.close, pct_chg: last.pct_chg || 0, turnover: last.turnover_rate,
      ma5: last.ma5, ma10: last.ma10, ma20: last.ma20, ma60: last.ma60,
    }
    relatedPositions.value = d.related_positions || []
    bizTitle.value = `${code} ${d.name||''}`

    await nextTick()
    if (klineChart.value) {
      if (chartInst) chartInst.dispose()
      chartInst = echarts.init(klineChart.value)

      const dates = raw.map(r => String(r.trade_date))
      const ohlc = raw.map(r => [r.open, r.close, r.low, r.high])
      const volumes = raw.map(r => r.vol || 0)
      const ma5 = raw.map(r => r.ma5 ?? null)
      const ma10 = raw.map(r => r.ma10 ?? null)
      const ma20 = raw.map(r => r.ma20 ?? null)
      const ma60 = raw.map(r => r.ma60 ?? null)

      const volColors = volumes.map((v, i) => i > 0 ? (raw[i].close >= raw[i-1].close ? '#dc2626' : '#059669') : '#dc2626')
      const indexByDate = new Map(dates.map((d, i) => [d, i]))
      const tradeMarks = (d.agent_trades || [])
        .map(t => {
          const idx = indexByDate.get(String(t.trade_date))
          if (idx === undefined) return null
          const isBuy = t.direction === 'buy'
          return {
            name: isBuy ? 'B' : 'S',
            coord: [idx, Number(t.price || 0)],
            value: isBuy ? 'B' : 'S',
            itemStyle: { color: isBuy ? '#dc2626' : '#059669' },
            label: { color: '#fff', fontWeight: 700 },
            trade: t,
          }
        })
        .filter(Boolean)

      chartInst.setOption({
        backgroundColor: 'transparent', animation: false,
        tooltip: {
          trigger: 'axis', axisPointer: { type: 'cross' },
          formatter: p => {
            if (!p?.length) return ''
            const mark = p.find(x => x.data?.trade)
            if (mark) {
              const t = mark.data.trade
              return `${t.trade_date}<br/>${t.agent_name || 'Agent'} ${t.direction === 'buy' ? '买入' : '卖出'} ${t.quantity}股<br/>价格:${Number(t.price || 0).toFixed(2)} 金额:${Number(t.total_value || 0).toFixed(2)}`
            }
            const i = p[0].dataIndex; const r = raw[i]
            return `${r.trade_date}<br/>开:${r.open.toFixed(2)} 收:${r.close.toFixed(2)} 高:${r.high.toFixed(2)} 低:${r.low.toFixed(2)}<br/>涨跌:${r.pct_chg?.toFixed(2)||'-'}% 换手:${r.turnover_rate?.toFixed(2)||'-'}%<br/>MA5:${r.ma5?.toFixed(2)||'-'} MA10:${r.ma10?.toFixed(2)||'-'} MA20:${r.ma20?.toFixed(2)||'-'} MA60:${r.ma60?.toFixed(2)||'-'}`
          },
        },
        toolbox: { right: 8, top: 4, feature: { dataZoom: { yAxisIndex: 'none', title: { zoom: '区域缩放', back: '还原' } }, restore: { title: '重置' } } },
        dataZoom: [
          { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: true },
          { type: 'slider', xAxisIndex: [0, 1], start: 90, end: 100, height: 24, bottom: 4,
            borderColor: '#d1d5db', backgroundColor: '#f0f2f5',
            fillerColor: 'rgba(184,134,11,0.15)', handleStyle: { color: '#b8860b' },
            textStyle: { color: '#5a5d6e', fontSize: 10 },
          },
        ],
        axisPointer: { link: [{ xAxisIndex: 'all' }] },
        grid: [{ left: 70, right: 18, top: 30, height: '60%' }, { left: 70, right: 18, top: '76%', height: '16%' }],
        xAxis: [
          { type: 'category', data: dates, gridIndex: 0,
            axisLabel: { color: '#9ca3af', fontSize: 10, formatter: v => String(v).slice(4) },
            axisLine: { lineStyle: { color: '#e2e5ea' } }, axisTick: { show: false },
          },
          { type: 'category', data: dates, gridIndex: 1,
            axisLabel: { show: false },
            axisLine: { lineStyle: { color: '#e2e5ea' } }, axisTick: { show: false },
          },
        ],
        yAxis: [
          { type: 'value', gridIndex: 0, scale: true,
            axisLabel: { color: '#9ca3af', fontSize: 10 },
            splitLine: { lineStyle: { color: '#e2e5ea', type: 'dashed' } },
          },
          { type: 'value', gridIndex: 1,
            axisLabel: { color: '#9ca3af', fontSize: 9, formatter: v => v>1e8?(v/1e8).toFixed(1)+'亿':(v/1e4).toFixed(0)+'万' },
            splitLine: { show: false },
          },
        ],
        series: [
          { type: 'candlestick', data: ohlc, xAxisIndex: 0, yAxisIndex: 0,
            itemStyle: { color: '#dc2626', color0: '#059669', borderColor: '#dc2626', borderColor0: '#059669' },
            markPoint: { symbolSize: 28, data: tradeMarks },
          },
          { type: 'line', data: ma5, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#000000', width: 1 }, name: 'MA5' },
          { type: 'line', data: ma10, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#ff8c00', width: 1 }, name: 'MA10' },
          { type: 'line', data: ma20, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#dc143c', width: 1 }, name: 'MA20' },
          { type: 'line', data: ma60, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#00aa6c', width: 1.5 }, name: 'MA60' },
          { type: 'bar', data: volumes.map((v,i)=>({value:v,itemStyle:{color:volColors[i]}})), xAxisIndex:1, yAxisIndex:1 },
        ],
      })
    }
  } catch (e) { console.error(e) }
}

function buildKlineOption(raw, compact = false) {
  const dates = raw.map(r => String(r.trade_date))
  const ohlc = raw.map(r => [r.open, r.close, r.low, r.high])
  const volumes = raw.map(r => r.vol || 0)
  const ma5 = raw.map(r => r.ma5 ?? null)
  const ma10 = raw.map(r => r.ma10 ?? null)
  const ma20 = raw.map(r => r.ma20 ?? null)
  const ma60 = raw.map(r => r.ma60 ?? null)
  const volColors = volumes.map((v, i) => i > 0 ? (raw[i].close >= raw[i-1].close ? '#dc2626' : '#059669') : '#dc2626')
  return {
    backgroundColor: 'transparent', animation: false,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    dataZoom: compact ? [{ type: 'inside', xAxisIndex: [0, 1], start: 70, end: 100 }] : [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: true },
      { type: 'slider', xAxisIndex: [0, 1], start: 90, end: 100, height: 24, bottom: 4 },
    ],
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [{ left: 52, right: 12, top: 18, height: '58%' }, { left: 52, right: 12, top: '76%', height: '15%' }],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { color: '#9ca3af', fontSize: 9, formatter: v => String(v).slice(4) }, axisTick: { show: false } },
      { type: 'category', data: dates, gridIndex: 1, axisLabel: { show: false }, axisTick: { show: false } },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, scale: true, axisLabel: { color: '#9ca3af', fontSize: 9 }, splitLine: { lineStyle: { color: '#e2e5ea', type: 'dashed' } } },
      { type: 'value', gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    series: [
      { type: 'candlestick', data: ohlc, xAxisIndex: 0, yAxisIndex: 0, itemStyle: { color: '#dc2626', color0: '#059669', borderColor: '#dc2626', borderColor0: '#059669' } },
      { type: 'line', data: ma5, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#000000', width: 1 }, name: 'MA5' },
      { type: 'line', data: ma10, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#ff8c00', width: 1 }, name: 'MA10' },
      { type: 'line', data: ma20, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#dc143c', width: 1 }, name: 'MA20' },
      { type: 'line', data: ma60, xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { color: '#00aa6c', width: 1 }, name: 'MA60' },
      { type: 'bar', data: volumes.map((v,i)=>({value:v,itemStyle:{color:volColors[i]}})), xAxisIndex:1, yAxisIndex:1 },
    ],
  }
}

async function loadCompareCharts() {
  if (!compareMode.value) return
  const selected = savedStocks.value.filter(s => compareCodes.value.includes(s.ts_code)).slice(0, 4)
  compareChartsData.value = await Promise.all(selected.map(async s => {
    const resp = await fetch(`/api/market/stock/kline/${s.ts_code}?days=260`)
    const d = await resp.json()
    return { ...s, name: d.name || s.name, raw: d.data || [] }
  }))
  await nextTick()
  for (const item of compareChartsData.value) {
    const el = compareChartRefs.get(item.ts_code)
    if (!el || !item.raw.length) continue
    if (compareChartInst.has(item.ts_code)) compareChartInst.get(item.ts_code).dispose()
    const inst = echarts.init(el)
    inst.setOption(buildKlineOption(item.raw, true))
    compareChartInst.set(item.ts_code, inst)
  }
}

watch([compareMode, compareCodes, savedStocks], loadCompareCharts, { deep: true })

document.addEventListener('click', (e) => {
  if (!e.target.closest('.search-wrapper')) showDropdown.value = false
})
</script>

<style scoped>
.page-header { display: flex; gap: 16px; align-items: center; margin-bottom: 16px; flex-wrap: wrap; }
.page-header h2 { font-family: var(--font-mono); font-size: 18px; white-space: nowrap; }

.search-wrapper { position: relative; flex: 1; min-width: 280px; max-width: 420px; }
.search-input-wrap { position: relative; }
.search-input { width: 100%; padding: 10px 36px 10px 14px; font-size: 13px; }
.search-clear { position: absolute; right: 10px; top: 50%; transform: translateY(-50%); cursor: pointer; color: var(--text-dim); font-size: 14px; padding: 4px; }
.search-dropdown {
  position: absolute; top: 100%; left: 0; right: 0; z-index: 200;
  background: #fff; border: 1px solid var(--border-light); border-radius: 6px;
  max-height: 320px; overflow-y: auto; box-shadow: 0 8px 32px rgba(0,0,0,0.1); margin-top: 4px;
}
.search-item { padding: 10px 14px; cursor: pointer; display: flex; gap: 12px; align-items: center; border-bottom: 1px solid var(--border); transition: background 0.1s; }
.search-item:hover { background: #f8f9fb; }
.si-code { font-family: var(--font-mono); font-size: 13px; color: var(--accent-gold); min-width: 90px; }
.si-name { font-size: 13px; color: var(--text-primary); }

.holding-select { width: 210px; font-size: 12px; }

.saved-tabs { display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-bottom:12px; }
.saved-label { font-size:11px; color:var(--text-dim); font-family:var(--font-mono); }
.saved-tab { display:flex; align-items:center; gap:4px; padding:4px 10px; background:var(--bg-card); border:1px solid var(--border); border-radius:5px; cursor:pointer; font-size:12px; transition:all .15s; }
.saved-tab:hover { border-color:var(--accent-gold); }
.saved-tab.active { border-color:var(--accent-gold); background:#fef9f0; }
.tab-code { font-family:var(--font-mono); color:var(--accent-gold); font-weight:600; font-size:12px; }
.tab-name { font-size:11px; color:var(--text-secondary); max-width:60px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tab-close { font-size:10px; color:var(--text-dim); padding:2px; border-radius:2px; }
.tab-close:hover { color:var(--accent-red); background:#fce4e4; }
.mono { font-family: var(--font-mono); }

.compare-panel { margin-bottom: 16px; }
.compare-toolbar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:10px; }
.compare-check { display:flex; align-items:center; gap:4px; font-size:12px; color:var(--text-secondary); }
.compare-grid { display:grid; gap:12px; }
.compare-grid.cols-1 { grid-template-columns: 1fr; }
.compare-grid.cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.compare-grid.cols-3, .compare-grid.cols-4 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.compare-card { border:1px solid var(--border); border-radius:6px; background:#fff; padding:10px; }
.compare-title { display:flex; gap:8px; align-items:center; font-size:12px; margin-bottom:6px; color:var(--text-secondary); }
.compare-chart { width:100%; height:320px; }
@media (max-width: 900px) {
  .compare-grid.cols-2, .compare-grid.cols-3, .compare-grid.cols-4 { grid-template-columns: 1fr; }
}

.info-bar { display: flex; margin-bottom: 14px; background: #fff; border:1px solid var(--border); border-radius: 6px; overflow: hidden; box-shadow: 0 1px 2px rgba(0,0,0,0.03); }
.info-item { flex:1; padding: 12px 10px; text-align: center; border-right:1px solid var(--border); }
.info-item:last-child { border-right: none; }
.ilabel { display: block; font-size: 10px; color: var(--text-dim); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px; }
.ival { font-family: var(--font-mono); font-size: 15px; font-weight: 600; }

/* Floating biz button */
.biz-float {
  position: absolute; bottom: 50px; right: 30px;
  width: 40px; height: 40px; border-radius: 50%;
  background: #fff; border: 2px solid var(--accent-gold);
  cursor: pointer; font-size: 18px; display: flex; align-items: center; justify-content: center;
  box-shadow: 0 2px 8px rgba(0,0,0,0.1); transition: all 0.2s; z-index: 10;
}
.biz-float:hover { transform: scale(1.1); box-shadow: 0 4px 16px rgba(0,0,0,0.15); }

.spin-big { width: 40px; height: 40px; margin: 0 auto; border: 3px solid var(--border); border-top-color: var(--accent-gold); border-radius: 50%; animation: sp .8s linear infinite; }
@keyframes sp { to { transform: rotate(360deg); } }

.empty-state { text-align: center; padding: 100px 20px; color: var(--text-dim); }
.empty-icon { font-size: 64px; margin-bottom: 16px; opacity: 0.2; }
.empty-state p { font-size: 15px; margin-bottom: 4px; }
.empty-hint { font-size: 12px; opacity: 0.6; }
</style>
