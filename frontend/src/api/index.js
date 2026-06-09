import axios from 'axios'

const api = axios.create({ baseURL: '/api', withCredentials: true })

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401 && window.location.pathname !== '/login') {
      const next = encodeURIComponent(window.location.pathname + window.location.search)
      window.location.href = `/login?next=${next}`
    }
    return Promise.reject(error)
  },
)

export const authAPI = {
  me: () => api.get('/auth/me'),
  status: () => api.get('/auth/status'),
  verifyCode: (code) => api.post('/auth/verify-code', { code }),
  loginPassword: (password) => api.post('/auth/login-password', { password }),
  logout: () => api.post('/auth/logout'),
}

export const agentAPI = {
  list: () => api.get('/agent/list'),
  get: (id) => api.get(`/agent/${id}`),
  create: (data) => api.post('/agent/create', data),
  delete: (id) => api.delete(`/agent/${id}`),
  rename: (id, displayName) => api.put(`/agent/${id}/rename?display_name=${encodeURIComponent(displayName)}`),
  configure: (id, data) => api.put(`/agent/${id}/configure`, data),
  toggleStatus: (id) => api.put(`/agent/${id}/status`),
  setStatus: (id, status) => api.put(`/agent/${id}/status`, { status }),
  schedule: (id) => api.get(`/agent/${id}/schedule`),
  configureSchedule: (id, data) => api.put(`/agent/${id}/schedule`, data),
  runDue: () => api.post('/agent/run-due'),
  reports: (id, limit = 20) => api.get(`/agent/${id}/reports?limit=${limit}`),
  reportContent: (id, date) => api.get(`/agent/${id}/reports/${date}`),
  comparison: (days = 90) => api.get(`/agent/comparison?days=${days}`),
  positions: () => api.get('/agent/positions'),
  race: (days = 90) => api.get(`/agent/race?days=${days}`),
  raceDetail: (id, days = 90) => api.get(`/agent/${id}/race?days=${days}`),
  eval: (id, days = 90) => api.get(`/agent/${id}/eval?days=${days}`),
  cost: (id, days = 30) => api.get(`/agent/${id}/cost?days=${days}`),
  ideas: (id, days = 30, status = '') => api.get(`/agent/${id}/ideas?days=${days}${status ? `&status=${encodeURIComponent(status)}` : ''}`),
  ideaOutcomes: (id, days = 90) => api.get(`/agent/${id}/idea-outcomes?days=${days}`),
  updateIdeaOutcomes: (limit = 300) => api.post(`/agent/ideas/outcome/update?limit=${limit}`),
  decisionBatches: (id, limit = 30) => api.get(`/agent/${id}/decision-batches?limit=${limit}`),
  orderTrace: (id, orderId = '', limit = 80) => api.get(`/agent/${id}/orders/trace?limit=${limit}${orderId ? `&order_id=${orderId}` : ''}`),
  promptPreview: (id) => api.get(`/agent/${id}/prompt-preview`),
  stockPool: (id) => api.get(`/agent/${id}/stock-pool`),
  replaceStockPool: (id, items) => api.put(`/agent/${id}/stock-pool`, { items }),
  upsertStockPoolItem: (id, data) => api.post(`/agent/${id}/stock-pool/item`, data),
  deleteStockPoolItem: (id, tsCode) => api.delete(`/agent/${id}/stock-pool/${encodeURIComponent(tsCode)}`),
  tools: () => api.get('/agent/tools'),
  evolutionTimeline: (id, limit = 50) => api.get(`/agent/${id}/evolution/timeline?limit=${limit}`),
  systemDoc: (id) => api.get(`/agent/${id}/system-doc`),
  systemDocVersions: (id) => api.get(`/agent/${id}/system-doc/versions`),
  runReflection: (id, taskType = 'manual') => api.post(`/agent/${id}/reflection/run?task_type=${encodeURIComponent(taskType)}`),
}

export const strategyAPI = {
  builtin: () => api.get('/strategy/builtin'),
  run: (data) => api.post('/strategy/run', data),
  select: (data) => api.post('/strategy/select', data),
}

export const marketAPI = {
  index: (days = 30) => api.get(`/market/index?days=${days}`),
  sectorHeat: (date = '') => api.get(`/market/sector-heat?trade_date=${date}`),
  sectorStrength: (date = '', lookbackDays = 3) => api.get(`/market/sector-strength?trade_date=${date}&lookback_days=${lookbackDays}`),
  breadth: (date = '') => api.get(`/market/breadth?trade_date=${date}`),
  sectorTemperature: (date = '', topN = 20) => api.get(`/market/sector-temperature?trade_date=${date}&top_n=${topN}`),
  tags: () => api.get('/market/tags'),
  stockSearch: (q = '') => api.get(`/market/stocks/search?q=${encodeURIComponent(q)}`),
}

export const macroAPI = {
  status: () => api.get('/macro/status'),
  report: (date = '') => api.get(`/macro/report?trade_date=${date}`),
  generate: (date = '', force = true) => api.post(`/macro/report/generate?trade_date=${date}&force=${force}`),
  refresh: (date = '', refreshPolicy = true, force = true) => api.post(`/macro/refresh?trade_date=${date}&refresh_policy=${refreshPolicy}&force=${force}`),
  topic: (topic = 'report', date = '') => api.get(`/macro/topic?topic=${encodeURIComponent(topic)}&trade_date=${date}`),
  chip: (tsCode) => api.get(`/macro/chip/${encodeURIComponent(tsCode)}`),
  fundamental: (tsCode, date = '', days = 365) => api.get(`/macro/fundamental/${encodeURIComponent(tsCode)}?trade_date=${date}&days=${days}`),
}

export const telegramAPI = {
  status: () => api.get('/telegram/status'),
  bindings: (agentId = '') => api.get(`/telegram/bindings${agentId ? `?agent_id=${agentId}` : ''}`),
  bind: (data) => api.post('/telegram/bindings', data),
  testPush: (agentId, tradeDate = '') => api.post(`/telegram/push/test?agent_id=${agentId}&trade_date=${tradeDate}`),
  pushSettings: () => api.get('/telegram/push/settings'),
  updatePushSettings: (data) => api.put('/telegram/push/settings', data),
  startPolling: () => api.post('/telegram/polling/start'),
  stopPolling: () => api.post('/telegram/polling/stop'),
  chatTest: (data) => api.post('/telegram/chat/test', data),
  recommend: (text, chatId = 'local') => api.post('/telegram/recommend', { text, chat_id: chatId }),
  recommendTrace: (id) => api.get(`/telegram/recommend/trace/${id}`),
  latestRecommendTrace: (chatId = 'local') => api.get(`/telegram/recommend/latest?chat_id=${encodeURIComponent(chatId)}`),
  recommendEval: (chatId = '', days = 90) => api.get(`/telegram/recommend/eval?chat_id=${encodeURIComponent(chatId)}&days=${days}`),
  recommendOutcome: (id) => api.get(`/telegram/recommend/outcome/${id}`),
  updateRecommendOutcome: () => api.post('/telegram/recommend/outcome/update'),
  recommendFeedback: (id, data) => api.post(`/telegram/recommend/${id}/feedback`, data),
  profile: (chatId) => api.get(`/telegram/profile/${encodeURIComponent(chatId)}`),
  updateProfile: (chatId, data) => api.put(`/telegram/profile/${encodeURIComponent(chatId)}`, data),
  watchlist: (chatId) => api.get(`/telegram/watchlist/${encodeURIComponent(chatId)}`),
  addWatch: (chatId, data) => api.post(`/telegram/watchlist/${encodeURIComponent(chatId)}`, data),
  removeWatch: (chatId, tsCode) => api.delete(`/telegram/watchlist/${encodeURIComponent(chatId)}/${encodeURIComponent(tsCode)}`),
  analyze: (data) => api.post('/telegram/analyze', data),
  compare: (data) => api.post('/telegram/compare', data),
}

export const backtestAPI = {
  periods: () => api.get('/backtest/periods'),
  run: (data) => api.post('/backtest/run', data),
  quick: (strategy, period = '1m') => api.get(`/backtest/quick/${strategy}?period=${period}`),
  tasks: (limit = 20) => api.get(`/backtest/tasks?limit=${limit}`),
  task: (id) => api.get(`/backtest/task/${id}`),
  deleteTask: (id) => api.delete(`/backtest/task/${id}`),
}

export const companyAPI = {
  getBusiness: (tsCode) => api.get(`/company/business/${tsCode}`),
  getHistory: (tsCode) => api.get(`/company/business/${tsCode}/history`),
  search: (data) => api.post('/company/search', data),
  save: (data) => api.post('/company/save', data),
}

export const policyAPI = {
  list: (limit = 20) => api.get(`/policy/list?limit=${limit}`),
  signals: () => api.get('/policy/signals'),
  content: (sourceDir, filename) => api.get(`/policy/content?source_dir=${encodeURIComponent(sourceDir)}&filename=${encodeURIComponent(filename)}`),
  latest: (department = '') => {
    const dept = department ? `&department=${encodeURIComponent(department)}` : ''
    return api.get(`/policy/latest?${dept}`)
  },
  crawl: () => api.post('/policy/crawl'),
}
