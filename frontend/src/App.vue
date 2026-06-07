<template>
  <div id="app-container">
    <nav v-if="!isLoginPage" class="top-nav">
      <div class="nav-brand">
        <span class="brand-mark">◆</span>
        <h1 class="logo">A股多Agent<span class="logo-accent">智能投顾</span></h1>
      </div>
      <div class="nav-links">
        <router-link to="/"><span class="nav-icon">⊟</span>大盘看板</router-link>
        <router-link to="/macro"><span class="nav-icon">▤</span>板块热度</router-link>
        <router-link to="/chat"><span class="nav-icon">◇</span>AI 选股</router-link>
        <router-link to="/stock"><span class="nav-icon">⬒</span>K线分析</router-link>
        <router-link to="/backtest"><span class="nav-icon">◫</span>策略回测</router-link>
        <router-link to="/simulation"><span class="nav-icon">◈</span>模拟交易</router-link>
        <router-link to="/telegram"><span class="nav-icon">▣</span>Telegram</router-link>
      </div>
      <div class="nav-status">
        <span class="status-dot"></span>
        LIVE
      </div>
    </nav>
    <main class="main-content">
      <router-view v-slot="{ Component }">
        <keep-alive>
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </main>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const isLoginPage = computed(() => route.name === 'Login')
</script>

<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap');

:root {
  --bg-deep: #f0f2f5;
  --bg-primary: #f8f9fb;
  --bg-card: #ffffff;
  --bg-elevated: #f0f2f5;
  --border: #e2e5ea;
  --border-light: #d1d5db;
  --text-primary: #1a1a2e;
  --text-secondary: #5a5d6e;
  --text-dim: #9ca3af;
  --accent-red: #dc2626;
  --accent-green: #059669;
  --accent-gold: #b8860b;
  --accent-blue: #2563eb;
  --accent-cyan: #0891b2;
  --font-mono: 'JetBrains Mono', monospace;
  --font-ui: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif;
}

* { margin: 0; padding: 0; box-sizing: border-box; }

body {
  font-family: var(--font-ui);
  font-size: 14px;
  background: var(--bg-deep);
  color: var(--text-primary);
  -webkit-font-smoothing: antialiased;
}

.top-nav {
  display: flex; align-items: center; padding: 0 28px; height: 52px;
  background: #fff; border-bottom: 1px solid var(--border); gap: 36px;
  position: sticky; top: 0; z-index: 100; box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.nav-brand { display: flex; align-items: center; gap: 10px; }
.brand-mark { color: var(--accent-gold); font-size: 20px; }
.logo { font-family: var(--font-mono); font-size: 16px; font-weight: 600; color: var(--text-primary); letter-spacing: 0.5px; }
.logo-accent { color: var(--accent-gold); }
.nav-links { display: flex; gap: 2px; flex: 1; }
.nav-links a {
  display: flex; align-items: center; gap: 6px; color: var(--text-secondary);
  text-decoration: none; padding: 8px 16px; border-radius: 6px; font-size: 14px; font-weight: 500; transition: all 0.15s;
}
.nav-links a:hover { color: var(--text-primary); background: var(--bg-deep); }
.nav-links a.router-link-active { color: var(--accent-gold); background: #fef9f0; font-weight: 600; }
.nav-icon { font-size: 14px; }
.nav-status {
  display: flex; align-items: center; gap: 8px; font-family: var(--font-mono);
  font-size: 11px; color: var(--accent-green); letter-spacing: 2px;
}
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent-green); animation: pulse 2s infinite; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

.main-content { padding: 24px 28px; max-width: 1560px; margin: 0 auto; }

.card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 8px; padding: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.03);
}
.card h3 {
  font-family: var(--font-mono); font-size: 13px; font-weight: 500;
  color: var(--text-secondary); text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 16px;
}

table { width: 100%; border-collapse: collapse; font-family: var(--font-mono); font-size: 13px; }
th { padding: 10px 14px; text-align: left; color: var(--text-dim); font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; border-bottom: 2px solid var(--border); }
td { padding: 10px 14px; border-bottom: 1px solid var(--border); color: var(--text-primary); }
tr:hover td { background: #f8f9fb; }

.green { color: var(--accent-green); }
.red { color: var(--accent-red); }

.btn {
  font-family: var(--font-mono); padding: 8px 16px; border-radius: 5px; border: 1px solid var(--border-light);
  cursor: pointer; font-size: 12px; font-weight: 500; color: var(--text-primary);
  background: #fff; transition: all 0.15s; white-space: nowrap;
}
.btn:hover { border-color: var(--accent-gold); background: #fef9f0; }
.btn-primary { background: var(--accent-gold); color: #fff; border-color: var(--accent-gold); font-weight: 600; }
.btn-primary:hover { background: #a0750a; border-color: #a0750a; color: #fff; }
.btn-danger { background: var(--accent-red); color: #fff; border-color: var(--accent-red); }
.btn-sm { padding: 5px 12px; font-size: 11px; }

input, select, textarea {
  font-family: var(--font-mono); padding: 9px 14px; border: 1px solid var(--border-light);
  border-radius: 5px; background: #fff; color: var(--text-primary); font-size: 13px; outline: none; transition: border-color 0.15s;
}
input:focus, select:focus { border-color: var(--accent-gold); box-shadow: 0 0 0 2px rgba(184,134,11,0.08); }

.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; }
.grid-4 { display: grid; grid-template-columns: repeat(auto-fit, minmax(270px, 1fr)); gap: 20px; }

::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--bg-deep); }
::-webkit-scrollbar-thumb { background: #d1d5db; border-radius: 3px; }

.modal-overlay { position: fixed; top:0;left:0;right:0;bottom:0; background: rgba(0,0,0,0.4); display:flex; align-items:center; justify-content:center; z-index:1000; }
.modal-card { background: var(--bg-card); border:1px solid var(--border); border-radius:10px; padding:28px; width:94%; max-height:85vh; overflow-y:auto; box-shadow:0 20px 60px rgba(0,0,0,0.12); }

.md-content {
  font-size: 14px; line-height: 1.8; color: var(--text-primary); padding: 16px 20px;
  background: #fafbfc; border-radius: 6px; border: 1px solid var(--border);
}
.md-content :deep(h1) { font-size: 20px; margin: 14px 0 8px; color: var(--text-primary); }
.md-content :deep(h2) { font-size: 16px; margin: 12px 0 6px; color: var(--accent-gold); }
.md-content :deep(h3) { font-size: 14px; margin: 10px 0 4px; }
.md-content :deep(p) { margin: 6px 0; }
.md-content :deep(ul),.md-content :deep(ol) { padding-left: 22px; margin: 6px 0; }
.md-content :deep(li) { margin: 3px 0; }
.md-content :deep(strong) { color: var(--text-primary); }
.md-content :deep(hr) { border-color: var(--border); margin: 14px 0; }
.md-content :deep(code) { background: #f0f2f5; padding: 2px 6px; border-radius: 3px; font-family: var(--font-mono); font-size: 12px; }
.md-content :deep(blockquote) { border-left: 3px solid var(--accent-gold); padding-left: 14px; color: var(--text-secondary); margin: 10px 0; }
</style>
