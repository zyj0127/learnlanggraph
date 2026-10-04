<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useChatStore } from '../stores/chat'

const auth = useAuthStore()
const chat = useChatStore()

// 演示账号（统一初始密码 Hr@2026；角色由服务端按员工表返回，前端不可选）
const demoAccounts = [
  { uid: '1001', label: '1001 · 张三（员工）' },
  { uid: '8001', label: '8001 · 林敏（HR）' },
  { uid: '9001', label: '9001 · 安管理员（管理员）' },
]

const loginUid = ref('1001')
const loginPassword = ref('')
const loginError = ref('')
const loggingIn = ref(false)

async function doLogin() {
  loginError.value = ''
  if (!loginUid.value.trim() || !loginPassword.value) {
    loginError.value = '请输入账号与密码'
    return
  }
  loggingIn.value = true
  try {
    await auth.login(loginUid.value.trim(), loginPassword.value, chat.mock)
    loginPassword.value = ''
    chat.resetConversation()
  } catch (e) {
    loginError.value = e instanceof Error ? e.message : String(e)
  } finally {
    loggingIn.value = false
  }
}

function doLogout() {
  auth.logout()
  chat.resetConversation()
}
</script>

<template>
  <div class="login-panel">
    <template v-if="auth.isLoggedIn">
      <div class="logged-in">
        <div class="logged-name">✅ {{ auth.identity?.name || auth.identity?.uid }}</div>
        <div class="logged-role">{{ auth.roleLabel }}</div>
        <button class="btn btn-ghost btn-block" @click="doLogout">🚪 退出登录</button>
      </div>
    </template>
    <template v-else>
      <p class="anon-hint">未登录（匿名）：仅可咨询政策类问题，个人数据功能需登录</p>
      <label class="field">
        <span>员工账号</span>
        <input
          v-model="loginUid"
          list="demo-uids"
          placeholder="请输入账号 uid"
          autocomplete="username"
          @keydown.enter="doLogin"
        />
        <datalist id="demo-uids">
          <option v-for="a in demoAccounts" :key="a.uid" :value="a.uid">
            {{ a.label }}
          </option>
        </datalist>
      </label>
      <label class="field">
        <span>密码</span>
        <input
          v-model="loginPassword"
          type="password"
          placeholder="演示账号统一密码 Hr@2026"
          autocomplete="current-password"
          @keydown.enter="doLogin"
        />
      </label>
      <p v-if="loginError" class="error-text">{{ loginError }}</p>
      <button class="btn btn-primary btn-block" :disabled="loggingIn" @click="doLogin">
        {{ loggingIn ? '登录中…' : '🔑 登录' }}
      </button>
      <p class="anon-hint">演示账号：1001 员工 / 8001 HR / 9001 管理员，密码 Hr@2026</p>
    </template>
  </div>
</template>
