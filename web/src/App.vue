<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useAuthStore } from './stores/auth'
import { useChatStore } from './stores/chat'
import LoginPanel from './components/LoginPanel.vue'
import ApprovalCard from './components/ApprovalCard.vue'
import AdminPanel from './components/AdminPanel.vue'

const auth = useAuthStore()
const chat = useChatStore()

const input = ref('')
const msgListEl = ref<HTMLElement | null>(null)

// 视图切换：chat（聊天）/ admin（管理台）。管理台入口仅 HR/ADMIN 可见
const view = ref<'chat' | 'admin'>('chat')
const isPrivileged = computed(
  () => auth.identity !== null && ['hr', 'admin'].includes(auth.identity.role),
)
watch(isPrivileged, (ok) => {
  if (!ok) view.value = 'chat'   // 退出/降级身份时自动回到聊天视图
})

onMounted(() => {
  void chat.checkBackend()
})

const identityLabel = computed(() =>
  auth.identity
    ? `${auth.identity.name || auth.identity.uid}（${auth.roleLabel}）`
    : '匿名访客（仅政策问答）',
)

/** 极简 markdown：转义后支持 **粗体**、`代码`、有序/无序列表与换行 */
function renderMd(text: string): string {
  const esc = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  const lines = esc.split('\n')
  const out: string[] = []
  let inUl = false
  let inOl = false
  const closeLists = () => {
    if (inUl) { out.push('</ul>'); inUl = false }
    if (inOl) { out.push('</ol>'); inOl = false }
  }
  const inline = (s: string) =>
    s
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
  for (const line of lines) {
    const ul = /^[-•]\s+/.test(line)
    const ol = /^\d+\.\s+/.test(line)
    if (ul) {
      if (!inUl) { closeLists(); out.push('<ul>'); inUl = true }
      out.push(`<li>${inline(line.replace(/^[-•]\s+/, ''))}</li>`)
    } else if (ol) {
      if (!inOl) { closeLists(); out.push('<ol>'); inOl = true }
      out.push(`<li>${inline(line.replace(/^\d+\.\s+/, ''))}</li>`)
    } else if (/^&gt;\s?/.test(line)) {
      closeLists()
      out.push(`<blockquote>${inline(line.replace(/^&gt;\s?/, ''))}</blockquote>`)
    } else if (line.trim() === '') {
      closeLists()
      out.push('<br/>')
    } else {
      closeLists()
      out.push(`<p>${inline(line)}</p>`)
    }
  }
  closeLists()
  return out.join('')
}

async function scrollToBottom() {
  await nextTick()
  msgListEl.value?.scrollTo({ top: msgListEl.value.scrollHeight })
}

watch(
  () => [chat.messages.length, chat.streamingText],
  () => void scrollToBottom(),
)

function submit() {
  const q = input.value.trim()
  if (!q || chat.streaming) return
  input.value = ''
  void chat.send(q)
}
</script>

<template>
  <div class="layout">
    <!-- 侧边栏 -->
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-title">🪶 飞羽科技 HR 助理</div>
        <div class="brand-sub">基于 LangGraph · RAG · 事实审计</div>
      </div>

      <section class="side-section">
        <h3>登录身份</h3>
        <LoginPanel />
      </section>

      <section class="side-section">
        <h3>会话</h3>
        <div class="thread-id">thread_id: {{ chat.threadId }}</div>
        <button class="btn btn-ghost btn-block" @click="chat.resetConversation()">
          🆕 开启新会话
        </button>
        <button class="btn btn-ghost btn-block" @click="chat.clearMessages()">
          🧹 清空显示
        </button>
        <button
          class="btn btn-ghost btn-block"
          :disabled="chat.streaming || !chat.messages.length"
          title="模拟用户长时间未操作，让助理总结本次会话的核心问题与结论"
          @click="chat.summarizeIdle()"
        >
          ⏱️ 生成闲置会话总结
        </button>
      </section>

      <section v-if="isPrivileged" class="side-section">
        <h3>管理台</h3>
        <button
          class="btn btn-ghost btn-block"
          @click="view = view === 'admin' ? 'chat' : 'admin'"
        >
          {{ view === 'admin' ? '💬 返回聊天' : '🗂️ 审批队列与安全看板' }}
        </button>
      </section>

      <section class="side-section caps">
        💡 能力：员工档案查询 / 假期余额 / 请假申请 / 在职·收入证明 / 员工手册政策问答
      </section>
    </aside>

    <!-- 管理台视图 -->
    <main v-if="view === 'admin' && isPrivileged" class="main">
      <header class="main-header">
        <h1>HR 管理台</h1>
        <p class="subtitle">请假审批队列与安全看板（与聊天内审批状态同源）</p>
      </header>
      <div class="messages">
        <AdminPanel />
      </div>
    </main>

    <!-- 主聊天区 -->
    <main v-else class="main">
      <header class="main-header">
        <h1>HR 智能助理</h1>
        <p class="subtitle">
          当前身份：<strong>{{ identityLabel }}</strong> ｜
          可咨询考勤假期、差旅报销等政策，或查询个人档案、假期余额、开具证明
        </p>
        <span v-if="chat.mock" class="badge badge-mock">Mock 演示模式</span>
      </header>

      <div v-if="chat.backendDown && !chat.mock" class="banner banner-warn">
        ⚠️ 无法连接后端服务（http://localhost:8000）。请确认 API 已启动，或
        <button class="link-btn" @click="chat.enableMock()">切换到 Mock 演示模式</button>
        预览完整交互。
      </div>
      <div v-if="chat.error" class="banner banner-error">{{ chat.error }}</div>

      <div ref="msgListEl" class="messages">
        <div v-if="!chat.messages.length && !chat.streaming" class="empty">
          <p>👋 您好，我是 HR 智能助理。</p>
          <p>试试：「我还有多少天年假？」「差旅报销标准是什么？」「帮我开一份在职证明」</p>
        </div>

        <div
          v-for="(m, i) in chat.messages"
          :key="i"
          class="msg"
          :class="m.role"
        >
          <div class="msg-avatar">{{ m.role === 'user' ? '🧑' : '🤖' }}</div>
          <div class="msg-body" v-html="renderMd(m.content)"></div>
        </div>

        <!-- 流式输出中的消息 -->
        <div v-if="chat.streaming || chat.streamingText" class="msg assistant">
          <div class="msg-avatar">🤖</div>
          <div class="msg-body">
            <span v-html="renderMd(chat.streamingText)"></span>
            <span v-if="chat.streaming && !chat.streamingText" class="thinking">思考中…</span>
            <span v-else class="cursor">▌</span>
          </div>
        </div>

        <!-- 审批挂起卡片 -->
        <ApprovalCard />
      </div>

      <footer class="composer">
        <input
          v-model="input"
          class="composer-input"
          placeholder="请输入您的问题，例如：我还有多少天年假？"
          :disabled="chat.streaming"
          @keydown.enter="submit"
        />
        <button
          class="btn btn-primary"
          :disabled="chat.streaming || !input.trim()"
          @click="submit"
        >
          发送
        </button>
      </footer>
    </main>
  </div>
</template>
