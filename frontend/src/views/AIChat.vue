<template>
  <div>
    <h2 style="font-family:var(--font-mono);font-size:18px;margin-bottom:18px;">AI 对话选股</h2>

    <!-- Strategies -->
    <div class="card" style="margin-bottom:18px;">
      <h3>内置策略</h3>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;">
        <button v-for="s in strategies" :key="s.name" class="btn btn-primary btn-sm"
                @click="runStrategy(s.name)">{{ s.name }}</button>
      </div>
      <div v-for="s in strategies" :key="'d'+s.name" style="font-size:12px;color:var(--text-dim);margin-top:4px;">
        <b style="color:var(--accent-gold);">{{ s.name }}</b>: {{ s.description }}
      </div>
    </div>

    <!-- NL Input -->
    <div class="card" style="margin-bottom:18px;">
      <h3>自然语言选股（流式）</h3>
      <div style="display:flex;gap:10px;">
        <input v-model="nlQuery" placeholder="例如：帮我找最近涨停回调到20日均线附近的股票"
               style="flex:1;" @keyup.enter="searchByNLStream" />
        <button class="btn btn-primary" @click="searchByNLStream" :disabled="loading">搜索</button>
      </div>
    </div>

    <!-- Streaming -->
    <div v-if="streamOutput.length" class="card stream-panel" style="margin-bottom:18px;">
      <h3>AI 推理过程</h3>
      <div class="stream-box" ref="streamBox">
        <div v-for="(item, idx) in streamOutput" :key="idx">
          <template v-if="item.type === 'token'">
            <span class="stream-token">{{ item.content }}</span>
          </template>
          <template v-else-if="item.type === 'phase'">
            <div class="stream-phase">{{ item.content }}</div>
          </template>
          <template v-else-if="item.type === 'parsed'">
            <div class="stream-parsed">
              <span class="sl">解析完成</span>
              <b>{{ item.strategy }}</b> — {{ item.explanation }}
              <span class="sm" v-if="item.max_results">目标 {{ item.max_results }} 只</span>
            </div>
          </template>
          <template v-else-if="item.type === 'progress'">
            <div class="stream-progress">
              <div class="pbar"><div class="pfill" :style="{width:(item.current/item.total*100)+'%'}"></div></div>
              <span>{{ item.current }}/{{ item.total }} (命中 {{ item.hits }})</span>
            </div>
          </template>
          <template v-else-if="item.type === 'error'">
            <div class="stream-error">{{ item.content }}</div>
          </template>
        </div>
      </div>
    </div>

    <!-- Results -->
    <div v-if="results.length" class="card" style="margin-bottom:18px;">
      <h3>选股结果 ({{ results.length }})</h3>
      <table>
        <thead><tr><th>代码</th><th>名称</th><th>评分</th><th>理由</th><th style="width:140px;">操作</th></tr></thead>
        <tbody>
          <tr v-for="r in results" :key="r.ts_code">
            <td class="mono">{{ r.ts_code }}</td><td>{{ r.name }}</td>
            <td class="mono">{{ r.score.toFixed(1) }}</td>
            <td style="font-size:12px;">{{ r.reason }}</td>
            <td>
              <button class="btn btn-sm" style="margin-right:4px;" @click="viewBusiness(r.ts_code, r.name)">业务</button>
              <button class="btn btn-sm btn-primary" @click="openKlinePopup(r.ts_code, r.name)">K线</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="loading" class="loading-hint">搜索中...</div>

    <!-- Business Modal -->
    <div v-if="showBizModal" class="modal-overlay" @click.self="showBizModal=false">
      <div class="modal-card" style="max-width:780px;">
        <div class="card-header-row">
          <h3>{{ bizTitle }} 业务详情</h3>
          <div style="display:flex;gap:8px;">
            <button class="btn btn-primary btn-sm" @click="refreshBusiness" :disabled="bizLoading">
              <span v-if="bizLoading" class="spin"></span>
              {{ bizLoading ? '搜索中...' : '刷新' }}
            </button>
            <button class="btn btn-sm" @click="showBizModal=false">关闭</button>
          </div>
        </div>
        <div v-if="bizLoading" class="biz-loading">
          <div class="spin-big"></div>
          <p>正在搜索 {{ bizTsCode }} 的业务信息...</p>
        </div>
        <div v-else-if="bizContent" class="md-content" v-html="renderMD(bizContent)"></div>
        <div v-else style="text-align:center;padding:40px;color:var(--text-dim);">暂无业务信息，点击「刷新」搜索</div>
        <div v-if="bizFreshness" class="freshness-bar">
          缓存: {{ bizFreshness.date }} ({{ bizFreshness.age_days }}天前)
          <span :style="{color:bizFreshness.is_fresh?'var(--accent-green)':'#fbbf24'}">{{ bizFreshness.is_fresh?'[有效]':'[已过期]' }}</span>
        </div>
      </div>
    </div>

    <!-- K-line Popup Modal -->
    <div v-if="showKlineModal" class="modal-overlay" @click.self="showKlineModal=false">
      <div class="modal-card" style="max-width:1100px;width:96%;">
        <div class="card-header-row">
          <h3>{{ klineTitle }} K线图</h3>
          <button class="btn btn-sm" @click="showKlineModal=false">关闭</button>
        </div>
        <div v-if="klineLoading" class="biz-loading"><div class="spin-big"></div><p>加载K线数据...</p></div>
        <div v-else ref="klinePopupChart" style="width:100%;height:520px;"></div>
      </div>
    </div>

    <!-- Agent Reports -->
    <div class="card">
      <h3>Agent 操作日报</h3>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:14px;">
        <select v-model="reportAgentId" @change="loadReports" style="width:200px;">
          <option :value="0" disabled>选择 Agent</option>
          <option v-for="a in agents" :key="a.id" :value="a.id">{{ a.display_name }}</option>
        </select>
        <button class="btn btn-primary btn-sm" @click="loadReports" :disabled="!reportAgentId">查看报告</button>
      </div>
      <div v-if="reports.length" style="margin-bottom:12px;">
        <div style="display:flex;gap:6px;flex-wrap:wrap;">
          <button v-for="r in reports" :key="r.trade_date" class="btn btn-sm"
                  :style="selectedDate===r.trade_date?'background:var(--accent-gold);color:#0f1119;':''"
                  @click="openReport(r.trade_date)">
            {{ String(r.trade_date).slice(0,4) }}/{{ String(r.trade_date).slice(4,6) }}/{{ String(r.trade_date).slice(6,8) }}
            <span style="font-size:10px;margin-left:4px;opacity:0.7;">({{ r.cumulative_return }}%)</span>
          </button>
        </div>
      </div>
      <div v-if="reportContent" class="card" style="background:var(--bg-deep);max-height:500px;overflow-y:auto;">
        <pre style="white-space:pre-wrap;font-size:13px;line-height:1.7;font-family:var(--font-mono);padding:12px;">{{ reportContent }}</pre>
      </div>
      <p v-if="reportAgentId&&!reports.length" style="color:var(--text-dim);font-size:12px;">暂无日报数据</p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { marked } from 'marked'
import * as echarts from 'echarts'
import { strategyAPI, agentAPI, companyAPI } from '../api'
const JSON = window.JSON
function renderMD(md) { return md ? marked(md, { breaks: true }) : '' }

const strategies = ref([]); const agents = ref([]); const nlQuery = ref('')
const results = ref([]); const loading = ref(false); const streamOutput = ref([])
const streamBox = ref(null)

// Business modal
const showBizModal = ref(false); const bizTitle = ref(''); const bizTsCode = ref('')
const bizContent = ref(''); const bizLoading = ref(false); const bizFreshness = ref(null)
const bizVersions = ref([])

// K-line popup
const showKlineModal = ref(false); const klineTitle = ref(''); const klineTsCode = ref('')
const klineLoading = ref(false); const klinePopupChart = ref(null)
let klinePopupInst = null

// Reports
const reportAgentId = ref(0); const reports = ref([]); const selectedDate = ref(''); const reportContent = ref('')

onMounted(async () => {
  const [s, a] = await Promise.all([strategyAPI.builtin(), agentAPI.list()])
  strategies.value = s.data.strategies; agents.value = a.data.agents
})

async function runStrategy(name) {
  loading.value = true; results.value = []; streamOutput.value = []
  const res = await strategyAPI.run({ strategy_name: name, max_results: 20 })
  results.value = res.data.results; loading.value = false
}

async function searchByNLStream() {
  if (!nlQuery.value.trim()) return
  loading.value = true; results.value = []; streamOutput.value = []
  try {
    const resp = await fetch('/api/strategy/select-stream', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: nlQuery.value, max_results: 20 }),
    })
    const reader = resp.body.getReader(); const decoder = new TextDecoder(); let buf = ''
    while (true) {
      const { done, value } = await reader.read(); if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n'); buf = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const d = JSON.parse(line.slice(6)); streamOutput.value.push(d)
          if (d.type === 'parsed') { const i = streamOutput.value.findLastIndex(s=>s.type==='phase'); if(i>=0) streamOutput.value.splice(i,1) }
          if (d.type === 'results') results.value = d.data || []
          if (d.type === 'progress') { const i = streamOutput.value.findLastIndex(s=>s.type==='progress'); if(i>=0&&i<streamOutput.value.length-1) streamOutput.value.splice(i,1) }
          await nextTick(); if (streamBox.value) streamBox.value.scrollTop = streamBox.value.scrollHeight
        } catch (e) {}
      }
    }
  } catch (e) { streamOutput.value.push({ type: 'error', content: '连接失败: ' + e.message }) }
  loading.value = false
}

// Business
async function viewBusiness(code, name) {
  bizTsCode.value = code; bizTitle.value = `${code} ${name||''}`; showBizModal.value = true
  bizContent.value = ''; bizFreshness.value = null; bizVersions.value = []
  try {
    const r = await companyAPI.getBusiness(code)
    if (r.data.cached) { bizContent.value = r.data.content; bizFreshness.value = r.data.freshness }
  } catch (e) {}
}
async function refreshBusiness() {
  if (!bizTsCode.value) return
  bizLoading.value = true; bizContent.value = ''
  try {
    const r = await companyAPI.search({ ts_code: bizTsCode.value, name: bizTitle.value.replace(bizTsCode.value,'').trim() })
    if (r.data.content) { bizContent.value = r.data.content; bizFreshness.value = r.data.freshness }
    else if (r.data.error) bizContent.value = '## 搜索失败\n\n' + r.data.error
  } catch (e) { bizContent.value = '## 请求失败\n\n' + (e.message||'') }
  bizLoading.value = false
}

// K-line popup
async function openKlinePopup(code, name) {
  klineTitle.value = `${code} ${name||''}`; klineTsCode.value = code
  showKlineModal.value = true; klineLoading.value = true
  let raw = null
  try {
    const resp = await fetch(`/api/market/stock/kline/${code}?days=2000`)
    const d = await resp.json()
    if (d.data?.length) raw = d.data
  } catch (e) { console.error(e) }

  klineLoading.value = false
  if (!raw) return
  await nextTick()
  await nextTick() // double tick to ensure v-else div renders

  if (!klinePopupChart.value) return
  if (klinePopupInst) klinePopupInst.dispose()
  klinePopupInst = echarts.init(klinePopupChart.value)
  const dates = raw.map(r=>String(r.trade_date))
  const ohlc = raw.map(r=>[r.open,r.close,r.low,r.high])
  const vols = raw.map(r=>r.vol||0)
  const ma5 = raw.map(r=>r.ma5??null); const ma10 = raw.map(r=>r.ma10??null)
  const ma20 = raw.map(r=>r.ma20??null); const ma60 = raw.map(r=>r.ma60??null)
  const vc = vols.map((v,i)=>i>0?(raw[i].close>=raw[i-1].close?'#dc2626':'#059669'):'#dc2626')
  const ls = raw.map(r=>r.low); const hs = raw.map(r=>r.high)
  const pad = (Math.max(...hs)-Math.min(...ls))*0.06
  klinePopupInst.setOption({
    backgroundColor:'transparent',animation:false,
    tooltip:{trigger:'axis',axisPointer:{type:'cross'},formatter:p=>{if(!p?.length)return'';const i=p[0].dataIndex;const r=raw[i];return`${r.trade_date}<br/>开:${r.open.toFixed(2)} 收:${r.close.toFixed(2)} 高:${r.high.toFixed(2)} 低:${r.low.toFixed(2)}<br/>涨跌:${r.pct_chg?.toFixed(2)||'-'}% 换手:${r.turnover_rate?.toFixed(2)||'-'}%<br/>MA5:${r.ma5?.toFixed(2)||'-'} MA10:${r.ma10?.toFixed(2)||'-'} MA20:${r.ma20?.toFixed(2)||'-'} MA60:${r.ma60?.toFixed(2)||'-'}`}},
    toolbox:{right:8,top:4,feature:{dataZoom:{yAxisIndex:'none',title:{zoom:'区域缩放',back:'还原'}},restore:{title:'重置'}}},
    dataZoom:[
      {type:'inside',xAxisIndex:[0,1],start:0,end:100,zoomOnMouseWheel:true,moveOnMouseMove:true},
      {type:'slider',xAxisIndex:[0,1],start:90,end:100,height:22,bottom:4,borderColor:'#d1d5db',backgroundColor:'#f0f2f5',fillerColor:'rgba(184,134,11,0.15)',handleStyle:{color:'#b8860b'},textStyle:{color:'#5a5d6e',fontSize:10}},
    ],
    axisPointer:{link:[{xAxisIndex:'all'}]},
    grid:[{left:70,right:18,top:30,height:'60%'},{left:70,right:18,top:'76%',height:'16%'}],
    xAxis:[
      {type:'category',data:dates,gridIndex:0,axisLabel:{color:'#9ca3af',fontSize:9,formatter:v=>String(v).slice(4)},axisLine:{lineStyle:{color:'#e2e5ea'}},axisTick:{show:false}},
      {type:'category',data:dates,gridIndex:1,axisLabel:{show:false},axisLine:{lineStyle:{color:'#e2e5ea'}},axisTick:{show:false}},
    ],
    yAxis:[
      {type:'value',gridIndex:0,scale:true,axisLabel:{color:'#9ca3af',fontSize:10},splitLine:{lineStyle:{color:'#e2e5ea',type:'dashed'}}},
      {type:'value',gridIndex:1,axisLabel:{color:'#9ca3af',fontSize:8,formatter:v=>v>1e8?(v/1e8).toFixed(1)+'亿':(v/1e4).toFixed(0)+'万'},splitLine:{show:false}},
    ],
    series:[
      {type:'candlestick',data:ohlc,xAxisIndex:0,yAxisIndex:0,itemStyle:{color:'#dc2626',color0:'#059669',borderColor:'#dc2626',borderColor0:'#059669'}},
      {type:'line',data:ma5,xAxisIndex:0,yAxisIndex:0,showSymbol:false,lineStyle:{color:'#000',width:1},name:'MA5'},
      {type:'line',data:ma10,xAxisIndex:0,yAxisIndex:0,showSymbol:false,lineStyle:{color:'#ff8c00',width:1},name:'MA10'},
      {type:'line',data:ma20,xAxisIndex:0,yAxisIndex:0,showSymbol:false,lineStyle:{color:'#dc143c',width:1},name:'MA20'},
      {type:'line',data:ma60,xAxisIndex:0,yAxisIndex:0,showSymbol:false,lineStyle:{color:'#00aa6c',width:1.5},name:'MA60'},
      {type:'bar',data:vols.map((v,i)=>({value:v,itemStyle:{color:vc[i]}})),xAxisIndex:1,yAxisIndex:1},
    ],
  })
}

async function loadReports() { if(!reportAgentId.value)return;reportContent.value='';selectedDate.value='';const r=await agentAPI.reports(reportAgentId.value,30);reports.value=r.data.reports||[] }
async function openReport(d){ if(!reportAgentId.value)return;selectedDate.value=d;const r=await agentAPI.reportContent(reportAgentId.value,d);reportContent.value=r.data.report_content||r.data.think_log_content||'暂无内容' }
</script>

<style scoped>
.mono { font-family: var(--font-mono); }
.stream-panel { background: var(--bg-card) !important; }
.stream-box { max-height: 420px; overflow-y: auto; font-family: var(--font-mono); font-size: 13px; line-height: 1.7; padding: 16px; background: #f8f9fb; border-radius: 6px; border: 1px solid var(--border); }
.stream-token { color: #374151; }
.stream-phase { color: var(--accent-cyan); margin: 8px 0; padding: 8px 12px; background: rgba(14,165,197,0.08); border-left: 3px solid var(--accent-cyan); font-size: 13px; }
.stream-parsed { color: var(--accent-green); margin: 8px 0; padding: 8px 14px; background: rgba(0,184,122,0.06); border-radius: 5px; font-size: 13px; }
.sl { font-size: 10px; color: var(--text-dim); margin-right: 8px; }
.sm { font-size: 10px; color: var(--text-dim); margin-left: 8px; }
.stream-progress { display: flex; align-items: center; gap: 10px; color: var(--text-dim); font-size: 12px; margin: 4px 0; }
.pbar { flex: 1; height: 4px; background: var(--border); border-radius: 2px; overflow: hidden; }
.pfill { height: 100%; background: var(--accent-gold); transition: width 0.3s; }
.stream-error { color: var(--accent-red); padding: 6px 10px; font-size: 13px; }

.card-header-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
.card-header-row h3 { margin-bottom: 0; }

.loading-hint { text-align: center; padding: 40px; color: var(--text-dim); font-size: 14px; }
.biz-loading { text-align: center; padding: 50px 20px; }
.spin-big { width: 40px; height: 40px; margin: 0 auto 16px; border: 3px solid var(--border); border-top-color: var(--accent-gold); border-radius: 50%; animation: sp .8s linear infinite; }
@keyframes sp { to { transform: rotate(360deg); } }
.biz-loading p { color: var(--text-dim); font-size: 13px; }
.spin { display: inline-block; width: 14px; height: 14px; border: 2px solid rgba(0,0,0,.3); border-top-color: #000; border-radius: 50%; animation: sp .6s linear infinite; vertical-align: middle; margin-right: 4px; }
.freshness-bar { margin-top: 12px; font-size: 11px; color: var(--text-dim); padding: 8px 12px; background: var(--bg-deep); border-radius: 5px; }
</style>
