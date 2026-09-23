<script setup lang="ts">
import { ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useChatStore } from '../stores/chat'
import type { Role } from '../types'

const auth = useAuthStore()
const chat = useChatStore()

const uidOptions: Record<string, string> = {
  '1001': '1001 · 张三（P5 北京）',
  '1002': '1002 · 李四（P4 成都）',
  '1003': '1003 · 王五（P7 上海）',
  '1004': '1004 · 赵六（P3 深圳）',
}

const loginUid = ref('1001')
const loginRole = ref<Exclude<Role, 'anonymous'>>('employee')
const loginError = ref('')
const loggingIn = ref(false)

async function doLogin() {
  loginError.value = ''
  loggingIn.value = true
  try {
    const name = uidOptions[loginUid.value].split('·')[1].trim().split('（')[0]
    await auth.login(loginUid.value, loginRole.value, name, chat.mock)
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
        <select v-model="loginUid">
          <option v-for="(label, uid) in uidOptions" :key="uid" :value="uid">
            {{ label }}
          </option>
        </select>
      </label>
      <label class="field">
        <span>角色</span>
        <select v-model="loginRole">
          <option value="employee">员工</option>
          <option value="hr">HR 专员</option>
          <option value="admin">管理员</option>
        </select>
      </label>
      <p v-if="loginError" class="error-text">{{ loginError }}</p>
      <button class="btn btn-primary btn-block" :disabled="loggingIn" @click="doLogin">
        {{ loggingIn ? '登录中…' : '🔑 登录' }}
      </button>
    </template>
  </div>
</template>
