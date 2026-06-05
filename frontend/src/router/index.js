import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'Dashboard', component: () => import('../views/Dashboard.vue') },
  { path: '/macro', name: 'Macro', component: () => import('../views/Macro.vue') },
  { path: '/agent/:id', name: 'AgentDetail', component: () => import('../views/AgentDetail.vue') },
  { path: '/chat', name: 'AIChat', component: () => import('../views/AIChat.vue') },
  { path: '/stock', name: 'StockViewer', component: () => import('../views/StockViewer.vue') },
  { path: '/backtest', name: 'Backtest', component: () => import('../views/Backtest.vue') },
  { path: '/simulation', name: 'Simulation', component: () => import('../views/Simulation.vue') },
  { path: '/telegram', name: 'Telegram', component: () => import('../views/Telegram.vue') },
]

export default createRouter({ history: createWebHistory(), routes })
