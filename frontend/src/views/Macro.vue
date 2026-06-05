<template>
  <div class="macro-page">
    <div class="page-header">
      <div>
        <h2>板块热度与宏观情报</h2>
        <p>每日宏观报告、板块温度、涨跌停池、龙虎榜和政策刷新入口。</p>
      </div>
      <button class="btn btn-primary" @click="refreshMacro" :disabled="loading">
        {{ loading ? '刷新中...' : '刷新宏观/政策' }}
      </button>
    </div>

    <div class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        :class="{ active: activeTab === tab.key }"
        @click="selectTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </div>

    <div class="card status-card">
      <div class="status-main">
        <span>交易日</span>
        <strong>{{ status?.trade_date || '-' }}</strong>
      </div>
      <div class="status-main">
        <span>报告时间</span>
        <strong>{{ status?.report_time || '-' }}</strong>
      </div>
      <div class="status-main">
        <span>报告状态</span>
        <strong>{{ status?.report?.exists ? status.report.status : '暂无' }}</strong>
      </div>
      <div class="status-main">
        <span>Risk-on</span>
        <strong>{{ status?.report?.risk_on_score ?? '-' }}</strong>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <h3>{{ currentLabel }}</h3>
        <button class="btn btn-sm" @click="loadTopic(activeTab)" :disabled="topicLoading">
          {{ topicLoading ? '加载中...' : '重新加载' }}
        </button>
      </div>
      <div v-if="topicLoading" class="empty">正在加载...</div>
      <div v-else-if="activeTab === 'report' && renderedReport" class="md-content" v-html="renderedReport"></div>
      <pre v-else class="topic-text">{{ topicMessage || '暂无数据' }}</pre>
    </div>

    <div class="grid-2">
      <div class="card">
        <h3>单股筹码峰</h3>
        <div class="inline-form">
          <input v-model="chipCode" placeholder="输入股票代码，如 000001.SZ" @keyup.enter="loadChip" />
          <button class="btn btn-sm btn-primary" @click="loadChip" :disabled="chipLoading">查询</button>
        </div>
        <pre class="topic-text small">{{ chipMessage || '输入股票后查看前复权筹码分布。' }}</pre>
      </div>

      <div class="card">
        <h3>单股业绩事件</h3>
        <div class="inline-form">
          <input v-model="fundCode" placeholder="输入股票代码，如 600000.SH" @keyup.enter="loadFundamental" />
          <button class="btn btn-sm btn-primary" @click="loadFundamental" :disabled="fundLoading">查询</button>
        </div>
        <pre class="topic-text small">{{ fundamentalText || '输入股票后查看业绩预告/快报。' }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { marked } from 'marked'
import { macroAPI } from '../api'

const tabs = [
  { key: 'sector', label: '板块热度' },
  { key: 'lhb', label: '龙虎榜' },
  { key: 'limit_up', label: '涨停板' },
  { key: 'broken_limit', label: '涨停炸板' },
  { key: 'limit_down', label: '跌停板' },
  { key: 'strong', label: '强势股池' },
  { key: 'report', label: '完整报告' },
]

const activeTab = ref('sector')
const loading = ref(false)
const topicLoading = ref(false)
const status = ref(null)
const topicMessage = ref('')
const chipCode = ref('')
const chipMessage = ref('')
const chipLoading = ref(false)
const fundCode = ref('')
const fundamentalText = ref('')
const fundLoading = ref(false)

const currentLabel = computed(() => tabs.find(t => t.key === activeTab.value)?.label || '宏观情报')
const renderedReport = computed(() => activeTab.value === 'report' ? marked(topicMessage.value || '', { breaks: true }) : '')

async function loadStatus() {
  const res = await macroAPI.status()
  status.value = res.data
}

async function loadTopic(topic) {
  topicLoading.value = true
  try {
    const res = await macroAPI.topic(topic)
    topicMessage.value = res.data.message || ''
  } catch (e) {
    topicMessage.value = e.response?.data?.detail || e.message || '加载失败'
  } finally {
    topicLoading.value = false
  }
}

async function selectTab(tab) {
  activeTab.value = tab
  await loadTopic(tab)
}

async function refreshMacro() {
  loading.value = true
  try {
    await macroAPI.refresh('', true, true)
    await loadStatus()
    await loadTopic(activeTab.value)
  } catch (e) {
    topicMessage.value = e.response?.data?.detail || e.message || '刷新失败'
  } finally {
    loading.value = false
  }
}

async function loadChip() {
  if (!chipCode.value.trim()) return
  chipLoading.value = true
  try {
    const res = await macroAPI.chip(chipCode.value.trim())
    chipMessage.value = res.data.message || ''
  } catch (e) {
    chipMessage.value = e.response?.data?.detail || e.message || '查询失败'
  } finally {
    chipLoading.value = false
  }
}

function formatFundamental(data) {
  const events = data?.events || []
  if (!events.length) return '暂无近期开披露业绩预告/快报。'
  return events.slice(-10).map(item => {
    const row = item.data || {}
    if (item.type === 'forecast') {
      return `业绩预告 ${row.profitForcastExpPubDate || ''}: ${row.profitForcastType || ''} ${row.profitForcastAbstract || ''}`
    }
    return `业绩快报 ${row.performanceExpPubDate || ''}: EPS增速${row.performanceExpressEPSChgPct || '-'} 营收同比${row.performanceExpressGRYOY || '-'} 营业利润同比${row.performanceExpressOPYOY || '-'}`
  }).join('\n')
}

async function loadFundamental() {
  if (!fundCode.value.trim()) return
  fundLoading.value = true
  try {
    const res = await macroAPI.fundamental(fundCode.value.trim())
    fundamentalText.value = formatFundamental(res.data.data)
  } catch (e) {
    fundamentalText.value = e.response?.data?.detail || e.message || '查询失败'
  } finally {
    fundLoading.value = false
  }
}

onMounted(async () => {
  await loadStatus()
  await loadTopic(activeTab.value)
})
</script>

<style scoped>
.macro-page { display: flex; flex-direction: column; gap: 18px; }
.page-header {
  display: flex; align-items: center; justify-content: space-between; gap: 16px;
}
.page-header h2 { font-size: 22px; margin-bottom: 6px; }
.page-header p { color: var(--text-secondary); }
.tabs {
  display: flex; flex-wrap: wrap; gap: 8px; padding: 4px 0;
}
.tabs button {
  border: 1px solid var(--border); background: #fff; color: var(--text-secondary);
  padding: 8px 12px; border-radius: 6px; cursor: pointer; font-weight: 600;
}
.tabs button.active { color: var(--accent-blue); border-color: var(--accent-blue); background: #eff6ff; }
.card {
  background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px;
  padding: 18px; box-shadow: 0 1px 2px rgba(0,0,0,0.03);
}
.status-card {
  display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 12px;
}
.status-main { display: flex; flex-direction: column; gap: 4px; }
.status-main span { color: var(--text-secondary); font-size: 12px; }
.status-main strong { font-size: 16px; }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.topic-text {
  white-space: pre-wrap; word-break: break-word; line-height: 1.7;
  font-family: var(--font-ui); background: #f8fafc; border: 1px solid var(--border);
  border-radius: 6px; padding: 14px; max-height: 620px; overflow: auto;
}
.topic-text.small { min-height: 180px; max-height: 360px; }
.grid-2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.inline-form { display: flex; gap: 8px; margin: 12px 0; }
.inline-form input {
  flex: 1; border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px;
}
.btn {
  border: 1px solid var(--border); background: #fff; border-radius: 6px; padding: 8px 12px;
  cursor: pointer; font-weight: 600;
}
.btn-sm { padding: 6px 10px; font-size: 13px; }
.btn-primary { background: var(--accent-blue); color: #fff; border-color: var(--accent-blue); }
.btn:disabled { opacity: 0.55; cursor: not-allowed; }
.empty { color: var(--text-secondary); padding: 20px 0; }
.md-content :deep(h1), .md-content :deep(h2), .md-content :deep(h3) { margin: 12px 0 8px; }
.md-content :deep(p), .md-content :deep(li) { line-height: 1.7; }
@media (max-width: 900px) {
  .page-header { align-items: flex-start; flex-direction: column; }
  .status-card, .grid-2 { grid-template-columns: 1fr; }
}
</style>
