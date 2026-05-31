<template>
  <div class="telegram-page">
    <section class="page-head">
      <div>
        <h2>Telegram 配置</h2>
        <p>Bot Token 长轮询、Agent 绑定、自然语言推荐测试。</p>
      </div>
      <button class="btn" @click="loadAll">刷新</button>
    </section>

    <section class="grid-3">
      <div class="card metric">
        <h3>Bot</h3>
        <div class="metric-value" :class="status.token_configured ? 'green' : 'red'">
          {{ status.token_configured ? '已配置' : '未配置' }}
        </div>
        <div class="muted">{{ botName }}</div>
      </div>
      <div class="card metric">
        <h3>Long Polling</h3>
        <div class="metric-value" :class="polling.running ? 'green' : 'red'">
          {{ polling.running ? '运行中' : '已停止' }}
        </div>
        <div class="muted">已处理 {{ polling.handled || 0 }} 条</div>
      </div>
      <div class="card metric">
        <h3>状态</h3>
        <div class="metric-value">{{ polling.last_update_id || 0 }}</div>
        <div class="muted">{{ polling.last_error || '无错误' }}</div>
      </div>
    </section>

    <section class="grid-2 main-grid">
      <div class="card">
        <h3>轮询控制</h3>
        <div class="actions">
          <button class="btn btn-primary" @click="startPolling">启动轮询</button>
          <button class="btn" @click="stopPolling">停止轮询</button>
        </div>
        <div class="steps">
          <div>1. 在环境变量配置 <code>TELEGRAM_BOT_TOKEN</code></div>
          <div>2. 重启后端，或点击启动轮询</div>
          <div>3. 给 Bot 发送 <code>/bind 1</code> 绑定 Agent</div>
          <div>4. 发送 <code>/analyze 600000.SH</code> 生成个股报告</div>
          <div>5. 发送 <code>/recommend 推荐3只强势科技股</code> 或 <code>/daily on</code></div>
        </div>
      </div>

      <div class="card">
        <h3>手动绑定</h3>
        <div class="form-row">
          <input v-model.number="bindForm.agent_id" type="number" placeholder="Agent ID" />
          <input v-model="bindForm.chat_id" placeholder="Chat ID" />
          <input v-model="bindForm.username" placeholder="用户名" />
          <button class="btn btn-primary" @click="bindChat">绑定</button>
        </div>
        <table v-if="bindings.length">
          <thead><tr><th>Agent</th><th>Chat</th><th>User</th><th>启用</th></tr></thead>
          <tbody>
            <tr v-for="b in bindings" :key="b.id">
              <td>{{ b.agent_id }}</td>
              <td class="mono">{{ b.chat_id }}</td>
              <td>{{ b.username }}</td>
              <td>{{ b.enabled ? '是' : '否' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <div class="section-head">
        <div>
          <h3>全局推送模块</h3>
          <p class="muted">控制所有 Chat 和 Agent 日报中是否展示板块强弱、政策方向、关注股。</p>
        </div>
        <button class="btn btn-primary" @click="savePushSettings" :disabled="settingsLoading">保存开关</button>
      </div>
      <div class="toggle-grid">
        <label class="toggle-row">
          <input type="checkbox" v-model="pushSettings.push_sector_strength_enabled" />
          <span>板块强弱</span>
        </label>
        <label class="toggle-row">
          <input type="checkbox" v-model="pushSettings.push_policy_enabled" />
          <span>政策方向</span>
        </label>
        <label class="toggle-row">
          <input type="checkbox" v-model="pushSettings.push_watchlist_enabled" />
          <span>关注股</span>
        </label>
      </div>
      <div v-if="settingsMessage" class="muted settings-message">{{ settingsMessage }}</div>
    </section>

    <section class="card">
      <div class="section-head">
        <div>
          <h3>用户画像与每日推送</h3>
          <p class="muted">选择 Chat 后，可在前端切换每日摘要推送；Telegram 内发送 <code>/daily on</code> 或 <code>/daily off</code> 也可以修改。</p>
        </div>
        <button class="btn" @click="loadProfile" :disabled="!selectedChatId || profileLoading">刷新画像</button>
      </div>

      <div class="profile-toolbar">
        <select v-model="selectedChatId" @change="loadProfile">
          <option value="">选择 Chat ID</option>
          <option v-for="chat in chatOptions" :key="chat.chat_id" :value="chat.chat_id">
            {{ chat.username ? `${chat.username} · ` : '' }}{{ chat.chat_id }}
          </option>
        </select>
        <input v-model="selectedChatId" @keyup.enter="loadProfile" placeholder="或输入 Chat ID" />
        <button class="btn btn-primary" @click="loadProfile" :disabled="!selectedChatId || profileLoading">读取</button>
      </div>

      <div v-if="profileError" class="alert">{{ profileError }}</div>
      <div v-if="profile.chat_id" class="profile-panel">
        <div class="profile-state">
          <div>
            <span class="label">每日摘要</span>
            <strong :class="profile.daily_push_enabled ? 'green' : 'red'">
              {{ profile.daily_push_enabled ? '已开启' : '已关闭' }}
            </strong>
          </div>
          <div class="actions">
            <button
              class="btn btn-primary"
              @click="setDailyPush(true)"
              :disabled="profile.daily_push_enabled || profileLoading"
            >
              开启每日摘要
            </button>
            <button
              class="btn"
              @click="setDailyPush(false)"
              :disabled="!profile.daily_push_enabled || profileLoading"
            >
              关闭
            </button>
          </div>
        </div>
        <div class="profile-grid">
          <div><span class="label">Chat</span><strong class="mono">{{ profile.chat_id }}</strong></div>
          <div><span class="label">用户名</span><strong>{{ profile.username || '-' }}</strong></div>
          <div><span class="label">风险偏好</span><strong>{{ profile.risk_level || '-' }}</strong></div>
          <div><span class="label">周期</span><strong>{{ profile.horizon || '-' }}</strong></div>
          <div><span class="label">推荐数量</span><strong>{{ profile.max_results || '-' }}</strong></div>
          <div><span class="label">更新时间</span><strong>{{ profile.updated_at || '-' }}</strong></div>
        </div>
      </div>
    </section>

    <section class="card">
      <h3>自然语言解析测试</h3>
      <div class="chat-test">
        <input v-model="testText" @keyup.enter="testChat" placeholder="/analyze 600000.SH" />
        <button class="btn btn-primary" @click="testChat" :disabled="loading">发送</button>
      </div>
      <pre v-if="reply" class="reply">{{ reply }}</pre>
      <div v-if="traceInfo.id" class="trace-panel">
        <div class="section-head">
          <div>
            <h3>最近推荐逻辑溯源</h3>
            <p class="muted">{{ traceInfo.source_summary || traceInfo.trace?.source_summary }}</p>
          </div>
          <div class="actions">
            <button class="btn btn-sm" @click="refreshOutcomes">刷新后验</button>
            <button class="btn btn-sm" @click="sendFeedback('positive')">有用</button>
            <button class="btn btn-sm" @click="sendFeedback('negative')">无用</button>
            <button class="btn btn-sm" @click="sendFeedback('risk_too_high')">太激进</button>
          </div>
        </div>
        <div class="trace-grid">
          <div><span class="label">Eval</span><strong class="mono">{{ traceInfo.eval?.id || traceInfo.eval_id || '-' }}</strong></div>
          <div><span class="label">耗时</span><strong class="mono">{{ num(traceInfo.eval?.response_latency_ms || traceInfo.cost?.response_latency_ms) }}ms</strong></div>
          <div><span class="label">Token</span><strong class="mono">{{ traceInfo.cost?.total_tokens ?? traceInfo.eval?.total_tokens ?? '-' }}</strong></div>
          <div><span class="label">工具</span><strong class="mono">{{ traceInfo.eval?.tool_calls || 0 }}/{{ traceInfo.eval?.tool_failures || 0 }}</strong></div>
          <div><span class="label">T+1/T+3/T+5</span><strong class="mono">{{ ret(traceInfo.outcome?.return_1d) }} / {{ ret(traceInfo.outcome?.return_3d) }} / {{ ret(traceInfo.outcome?.return_5d) }}</strong></div>
          <div><span class="label">状态</span><strong>{{ traceInfo.outcome?.status || traceInfo.eval?.status || '-' }}</strong></div>
        </div>
        <pre class="reply small">{{ traceInfo.trace?.system_excerpt || traceInfo.source_summary || '暂无溯源摘要' }}</pre>
        <details class="trace-details">
          <summary>工具 Trace / 推荐理由</summary>
          <pre class="reply small">{{ pretty(traceInfo.trace || {}) }}</pre>
        </details>
      </div>
    </section>

    <section class="card">
      <div class="section-head">
        <div>
          <h3>推荐评估</h3>
          <p class="muted">按请求聚合反馈、trace 完整度、成本和工具调用。</p>
        </div>
        <button class="btn" @click="loadRecommendEval">刷新</button>
      </div>
      <table>
        <thead><tr><th>时间</th><th>Chat</th><th>Mode</th><th>Intent</th><th>Trace</th><th>JSON</th><th>反馈</th><th>Token</th><th>工具</th><th>耗时</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="item in evalItems" :key="item.id">
            <td class="mono">{{ item.created_at }}</td>
            <td class="mono">{{ item.chat_id }}</td>
            <td>
              <span class="mode-pill" :class="`mode-${item.mode || 'react'}`">{{ item.mode || '-' }}</span>
              <span v-if="item.fallback_used" class="fallback-dot" :title="item.fallback_error || 'fallback'">fallback</span>
            </td>
            <td>{{ item.intent || '-' }}</td>
            <td>{{ item.trace_complete ? '完整' : '缺失' }}</td>
            <td :class="item.json_parse_ok ? 'green' : 'red'">{{ item.json_parse_ok ? 'ok' : 'fail' }}</td>
            <td class="mono">+{{ item.positive_count || 0 }} / -{{ item.negative_count || 0 }}</td>
            <td class="mono">{{ item.total_tokens ?? '-' }}</td>
            <td class="mono">{{ item.tool_calls || 0 }}/{{ item.tool_failures || 0 }}</td>
            <td class="mono">{{ num(item.response_latency_ms) }}ms</td>
            <td>{{ item.status }}</td>
          </tr>
          <tr v-if="!evalItems.length"><td colspan="11" class="empty-row">暂无推荐评估</td></tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { telegramAPI } from '../api'

const status = ref({})
const polling = ref({})
const bindings = ref([])
const profile = ref({})
const loading = ref(false)
const profileLoading = ref(false)
const settingsLoading = ref(false)
const profileError = ref('')
const settingsMessage = ref('')
const reply = ref('')
const traceInfo = ref({})
const evalItems = ref([])
const testText = ref('/analyze 600000.SH')
const bindForm = ref({ agent_id: 1, chat_id: '', username: '' })
const selectedChatId = ref('')
const pushSettings = ref({
  push_sector_strength_enabled: true,
  push_policy_enabled: true,
  push_watchlist_enabled: true,
})

const botName = computed(() => {
  const bot = status.value.bot || {}
  const result = bot.result || {}
  return result.username ? `@${result.username}` : (bot.error || '等待检测')
})

const chatOptions = computed(() => {
  const seen = new Set()
  return bindings.value
    .filter((b) => b.chat_id && !seen.has(b.chat_id) && seen.add(b.chat_id))
    .map((b) => ({ chat_id: b.chat_id, username: b.username }))
})

async function loadAll() {
  const [s, b, ps] = await Promise.all([
    telegramAPI.status(),
    telegramAPI.bindings(),
    telegramAPI.pushSettings(),
  ])
  status.value = s.data || {}
  polling.value = status.value.polling || {}
  bindings.value = b.data.bindings || []
  pushSettings.value = { ...pushSettings.value, ...(ps.data.settings || {}) }
  if (!selectedChatId.value && chatOptions.value.length) {
    selectedChatId.value = chatOptions.value[0].chat_id
  }
  if (selectedChatId.value) {
    await loadProfile()
  }
  await loadRecommendEval()
}

async function savePushSettings() {
  settingsLoading.value = true
  settingsMessage.value = ''
  try {
    const res = await telegramAPI.updatePushSettings(pushSettings.value)
    pushSettings.value = { ...pushSettings.value, ...(res.data.settings || {}) }
    settingsMessage.value = '已保存'
  } catch (err) {
    settingsMessage.value = err.response?.data?.detail || err.message || '保存失败'
  } finally {
    settingsLoading.value = false
  }
}

async function startPolling() {
  const res = await telegramAPI.startPolling()
  polling.value = res.data || {}
  await loadAll()
}

async function stopPolling() {
  const res = await telegramAPI.stopPolling()
  polling.value = res.data || {}
}

async function bindChat() {
  await telegramAPI.bind(bindForm.value)
  await loadAll()
}

function num(value) { return Number(value || 0).toFixed(2) }
function ret(value) { return value === null || value === undefined ? '-' : `${Number(value).toFixed(2)}%` }
function pretty(value) { return JSON.stringify(value || {}, null, 2) }

async function loadProfile() {
  if (!selectedChatId.value) return
  profileLoading.value = true
  profileError.value = ''
  try {
    const res = await telegramAPI.profile(selectedChatId.value)
    profile.value = res.data.profile || {}
  } catch (err) {
    profile.value = {}
    profileError.value = err.response?.data?.detail || err.message || '读取用户画像失败'
  } finally {
    profileLoading.value = false
  }
}

async function setDailyPush(enabled) {
  if (!selectedChatId.value) return
  profileLoading.value = true
  profileError.value = ''
  try {
    const res = await telegramAPI.updateProfile(selectedChatId.value, {
      daily_push_enabled: enabled,
    })
    profile.value = res.data.profile || {}
  } catch (err) {
    profileError.value = err.response?.data?.detail || err.message || '更新每日推送失败'
  } finally {
    profileLoading.value = false
  }
}

async function testChat() {
  loading.value = true
  reply.value = ''
  traceInfo.value = {}
  try {
    const isRecommend = /recommend|推荐|选股/.test(testText.value)
    const res = isRecommend
      ? await telegramAPI.recommend(testText.value, selectedChatId.value || 'local')
      : await telegramAPI.chatTest({ text: testText.value, chat_id: selectedChatId.value || 'local' })
    reply.value = res.data.reply || res.data.message || ''
    if (res.data.trace_id) {
      const trace = await telegramAPI.recommendTrace(res.data.trace_id)
      traceInfo.value = trace.data || res.data
    } else {
      await loadLatestTrace()
    }
    await loadRecommendEval()
  } finally {
    loading.value = false
  }
}

async function loadLatestTrace() {
  try {
    const res = await telegramAPI.latestRecommendTrace(selectedChatId.value || 'local')
    if (!res.data.error) traceInfo.value = res.data || {}
  } catch (e) {}
}

async function loadRecommendEval() {
  try {
    const res = await telegramAPI.recommendEval(selectedChatId.value || '', 90)
    evalItems.value = res.data.items || []
  } catch (e) {}
}

async function refreshOutcomes() {
  await telegramAPI.updateRecommendOutcome()
  if (traceInfo.value.id) {
    const res = await telegramAPI.recommendTrace(traceInfo.value.id)
    traceInfo.value = res.data || traceInfo.value
  }
  await loadRecommendEval()
}

async function sendFeedback(type) {
  if (!traceInfo.value.id) return
  await telegramAPI.recommendFeedback(traceInfo.value.id, { feedback_type: type, feedback_text: type })
  const res = await telegramAPI.recommendTrace(traceInfo.value.id)
  traceInfo.value = res.data || traceInfo.value
  await loadRecommendEval()
}

onMounted(loadAll)
</script>

<style scoped>
.telegram-page { display: flex; flex-direction: column; gap: 20px; }
.page-head { display: flex; align-items: center; justify-content: space-between; }
.page-head h2 { font-family: var(--font-mono); font-size: 22px; margin-bottom: 6px; }
.page-head p, .muted { color: var(--text-secondary); font-size: 13px; }
.metric { min-height: 132px; }
.metric-value { font-family: var(--font-mono); font-size: 28px; font-weight: 700; margin: 12px 0 8px; }
.main-grid { align-items: start; }
.actions, .form-row, .chat-test { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.form-row input { min-width: 140px; flex: 1; }
.section-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.section-head h3 { margin-bottom: 6px; }
.profile-toolbar { display: grid; grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) auto; gap: 10px; align-items: center; }
.profile-toolbar select, .profile-toolbar input { width: 100%; }
.toggle-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.toggle-row { display: flex; align-items: center; gap: 10px; border: 1px solid var(--border); border-radius: 6px; padding: 12px; background: #fafbfc; }
.settings-message { margin-top: 10px; }
.alert { margin-top: 12px; color: #b42318; background: #fff1f0; border: 1px solid #ffccc7; border-radius: 6px; padding: 10px 12px; }
.profile-panel { margin-top: 16px; display: grid; gap: 16px; }
.profile-state { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 14px; border: 1px solid var(--border); border-radius: 6px; background: #fafbfc; }
.profile-state strong { display: block; margin-top: 5px; font-size: 20px; }
.profile-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.profile-grid > div { border: 1px solid var(--border); border-radius: 6px; padding: 12px; min-width: 0; }
.profile-grid strong { display: block; margin-top: 6px; overflow-wrap: anywhere; }
.label { color: var(--text-secondary); font-size: 12px; }
.steps { margin-top: 18px; display: grid; gap: 10px; color: var(--text-secondary); }
.mono { font-family: var(--font-mono); }
.chat-test input { flex: 1; min-width: 320px; }
.reply {
  margin-top: 16px; padding: 16px; border: 1px solid var(--border);
  border-radius: 6px; background: #fafbfc; white-space: pre-wrap;
  font-family: var(--font-mono); line-height: 1.7;
}
.reply.small { font-size: 12px; max-height: 260px; overflow: auto; }
.trace-panel { margin-top: 16px; border: 1px solid var(--border); border-radius: 6px; padding: 14px; background: #fafbfc; }
.trace-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin:10px 0; }
.trace-grid > div { border:1px solid var(--border); border-radius:6px; padding:10px; background:#fff; min-width:0; }
.trace-grid strong { display:block; margin-top:5px; overflow-wrap:anywhere; }
.trace-details summary { cursor:pointer; color:var(--text-secondary); font-size:13px; margin-top:8px; }
.mode-pill, .fallback-dot { display:inline-block; border-radius:4px; padding:2px 6px; font-size:11px; border:1px solid var(--border); }
.mode-rule { color:#0f766e; background:#ecfdf5; border-color:#a7f3d0; }
.mode-react { color:#1d4ed8; background:#eff6ff; border-color:#bfdbfe; }
.mode-fallback { color:#b45309; background:#fffbeb; border-color:#fde68a; }
.fallback-dot { margin-left:4px; color:#b42318; background:#fff1f0; border-color:#ffccc7; }
.empty-row { text-align:center; color:var(--text-secondary); padding:18px; }
code { font-family: var(--font-mono); background: #f0f2f5; padding: 2px 5px; border-radius: 3px; }
@media (max-width: 900px) {
  .grid-2, .grid-3 { grid-template-columns: 1fr; }
  .section-head, .profile-state { flex-direction: column; align-items: stretch; }
  .profile-toolbar, .profile-grid, .toggle-grid, .trace-grid { grid-template-columns: 1fr; }
  .chat-test input { min-width: 100%; }
}
</style>
