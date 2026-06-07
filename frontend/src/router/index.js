import { createRouter, createWebHistory } from 'vue-router'
import { authAPI } from '../api'

const routes = [
  { path: '/login', name: 'Login', component: () => import('../views/Login.vue') },
  { path: '/', name: 'Dashboard', component: () => import('../views/Dashboard.vue') },
  { path: '/macro', name: 'Macro', component: () => import('../views/Macro.vue') },
  { path: '/agent/:id', name: 'AgentDetail', component: () => import('../views/AgentDetail.vue') },
  { path: '/chat', name: 'AIChat', component: () => import('../views/AIChat.vue') },
  { path: '/stock', name: 'StockViewer', component: () => import('../views/StockViewer.vue') },
  { path: '/backtest', name: 'Backtest', component: () => import('../views/Backtest.vue') },
  { path: '/simulation', name: 'Simulation', component: () => import('../views/Simulation.vue') },
  { path: '/telegram', name: 'Telegram', component: () => import('../views/Telegram.vue') },
]

const router = createRouter({ history: createWebHistory(), routes })

router.beforeEach(async (to) => {
  if (to.name === 'Login') return true
  try {
    const res = await authAPI.me()
    if (res.data?.authenticated) return true
  } catch (_) {
    // The axios interceptor also redirects, but returning here keeps router
    // navigation deterministic during initial app load.
  }
  return { name: 'Login', query: { next: to.fullPath } }
})

export default router
