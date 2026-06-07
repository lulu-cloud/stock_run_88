<template>
  <div class="login-page">
    <section class="login-panel">
      <div class="login-head">
        <div class="brand-line">
          <span class="brand-mark">◆</span>
          <span>A股多Agent智能投顾</span>
        </div>
        <h1>看板登录</h1>
        <p>在 Telegram 给机器人发送 <code>/login</code>，输入收到的 6 位验证码。</p>
      </div>

      <form class="login-form" @submit.prevent="submitCode">
        <label>
          <span>Telegram 验证码</span>
          <input
            v-model="code"
            inputmode="numeric"
            maxlength="6"
            autocomplete="one-time-code"
            placeholder="请输入 6 位验证码"
            autofocus
          />
        </label>
        <button class="btn btn-primary" type="submit" :disabled="loading || code.length < 6">
          {{ loading ? '验证中...' : '进入看板' }}
        </button>
      </form>

      <div v-if="passwordEnabled" class="password-box">
        <button class="link-btn" type="button" @click="showPassword = !showPassword">
          {{ showPassword ? '收起备用密码登录' : '使用备用密码登录' }}
        </button>
        <form v-if="showPassword" class="login-form compact" @submit.prevent="submitPassword">
          <label>
            <span>备用管理员密码</span>
            <input v-model="password" type="password" autocomplete="current-password" />
          </label>
          <button class="btn" type="submit" :disabled="loading || !password">
            密码登录
          </button>
        </form>
      </div>

      <p v-if="error" class="error-text">{{ error }}</p>
      <p class="hint">验证码 5 分钟内有效，仅允许白名单 Telegram 用户生成。</p>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authAPI } from '../api'

const route = useRoute()
const router = useRouter()
const code = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)
const passwordEnabled = ref(false)
const showPassword = ref(false)

const nextPath = computed(() => {
  const next = String(route.query.next || '/')
  return next.startsWith('/') && !next.startsWith('//') ? next : '/'
})

async function loadStatus() {
  try {
    const res = await authAPI.status()
    passwordEnabled.value = Boolean(res.data?.password_enabled)
  } catch (_) {
    passwordEnabled.value = false
  }
}

async function afterLogin() {
  await router.replace(nextPath.value)
}

async function submitCode() {
  error.value = ''
  loading.value = true
  try {
    await authAPI.verifyCode(code.value)
    await afterLogin()
  } catch (err) {
    error.value = err?.response?.data?.detail || '验证码无效或已过期'
  } finally {
    loading.value = false
  }
}

async function submitPassword() {
  error.value = ''
  loading.value = true
  try {
    await authAPI.loginPassword(password.value)
    await afterLogin()
  } catch (err) {
    error.value = err?.response?.data?.detail || '密码登录失败'
  } finally {
    loading.value = false
  }
}

onMounted(loadStatus)
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 32px 18px;
  background:
    linear-gradient(180deg, rgba(255,255,255,0.82), rgba(240,242,245,0.95)),
    radial-gradient(circle at 20% 20%, rgba(184,134,11,0.10), transparent 32%),
    var(--bg-deep);
}

.login-panel {
  width: min(440px, 100%);
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 32px;
  box-shadow: 0 18px 55px rgba(17, 24, 39, 0.10);
}

.login-head { display: flex; flex-direction: column; gap: 12px; margin-bottom: 24px; }
.brand-line {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--accent-gold);
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 700;
}
.login-head h1 { font-size: 28px; line-height: 1.2; }
.login-head p { color: var(--text-secondary); line-height: 1.7; }
.login-head code {
  font-family: var(--font-mono);
  background: #f6f7f9;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 2px 6px;
}

.login-form { display: flex; flex-direction: column; gap: 14px; }
.login-form.compact { margin-top: 12px; }
.login-form label { display: flex; flex-direction: column; gap: 8px; }
.login-form label span {
  font-size: 12px;
  color: var(--text-secondary);
  font-family: var(--font-mono);
}
.login-form input {
  width: 100%;
  font-size: 22px;
  letter-spacing: 0;
  text-align: center;
}
.login-form.compact input {
  font-size: 14px;
  text-align: left;
}
.login-form button { width: 100%; justify-content: center; }

.password-box {
  margin-top: 18px;
  padding-top: 18px;
  border-top: 1px solid var(--border);
}
.link-btn {
  border: 0;
  background: transparent;
  color: var(--accent-blue);
  cursor: pointer;
  padding: 0;
  font-size: 13px;
}
.error-text {
  margin-top: 16px;
  color: var(--accent-red);
  line-height: 1.6;
}
.hint {
  margin-top: 18px;
  color: var(--text-dim);
  line-height: 1.7;
  font-size: 12px;
}
</style>
