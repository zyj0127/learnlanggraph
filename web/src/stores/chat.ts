import { defineStore } from 'pinia'
import type { ChatMessage, PendingApproval, SseEvent } from '../types'
import { IDLE_TIMEOUT_CMD } from '../types'
import { ApiError, chatResume, chatStream, healthCheck } from '../api/client'
import { mockChatResume, mockChatStream } from '../api/mock'
import { useAuthStore } from './auth'

function newThreadId(uid: string): string {
  return `session_${uid}_${Math.random().toString(16).slice(2, 10)}`
}

const ENV_MOCK = import.meta.env.VITE_MOCK === 'true'

export const useChatStore = defineStore('chat', {
  state: () => ({
    messages: [] as ChatMessage[],
    streaming: false,
    streamingText: '',
    threadId: newThreadId('anon'),
    pendingApproval: null as PendingApproval | null,
    error: '' as string,
    // mock 演示模式：env 强制开启，或检测后端不可达后手动切换
    mock: ENV_MOCK,
    backendDown: false,
  }),
  getters: {
    effectiveUid(): string {
      const auth = useAuthStore()
      return auth.identity?.uid || '1001'
    },
    /** 当前身份是否可执行审批（前端预校验；后端仍是最终裁决） */
    approvalDenial(): string | null {
      const auth = useAuthStore()
      const pending = this.pendingApproval
      if (!pending) return null
      if (!auth.identity) return '权限提示：请先以 HR 或管理员身份登录后再审批。'
      const role = auth.identity.role
      if (role !== 'hr' && role !== 'admin') {
        return '权限提示：仅 HR 或管理员可执行人工审批。'
      }
      if (auth.identity.uid && auth.identity.uid === pending.applicantUid) {
        return '权限提示：审批人不能与申请人为同一人，请交由其他 HR 专员或管理员审批。'
      }
      return null
    },
  },
  actions: {
    async checkBackend() {
      if (this.mock) return
      this.backendDown = !(await healthCheck())
    },
    enableMock() {
      this.mock = true
      this.backendDown = false
    },
    resetConversation() {
      this.threadId = newThreadId(this.effectiveUid)
      this.messages = []
      this.pendingApproval = null
      this.error = ''
      this.streamingText = ''
    },
    clearMessages() {
      this.messages = []
    },

    /** 闲置会话总结（对齐 Streamlit「⏱️ 生成闲置会话总结」）：指令不上屏 */
    async summarizeIdle() {
      if (!this.messages.length || this.streaming) return
      await this.send(IDLE_TIMEOUT_CMD, false)
    },

    /** 发送一轮问答；echoUser=false 时问题不上屏（系统指令，如闲置总结） */
    async send(question: string, echoUser = true) {
      const auth = useAuthStore()
      if (!this.messages.length && !this.pendingApproval) {
        // 首轮确保 threadId 与当前身份绑定
        if (!this.threadId) this.threadId = newThreadId(this.effectiveUid)
      }
      if (echoUser) this.messages.push({ role: 'user', content: question })
      this.error = ''
      this.streaming = true
      this.streamingText = ''

      const onEvent = (ev: SseEvent) => {
        if (ev.type === 'token') {
          this.streamingText += ev.content
        } else if (ev.type === 'approval_required') {
          this.pendingApproval = {
            threadId: ev.thread_id,
            detail: ev.detail,
            applicantUid: this.effectiveUid,
          }
        }
      }

      try {
        const req = { uid: this.effectiveUid, question, thread_id: this.threadId }
        if (this.mock) await mockChatStream(req, onEvent)
        else await chatStream(req, auth.token, onEvent)
      } catch (e) {
        this.handleError(e)
      } finally {
        this.streaming = false
        if (this.streamingText) {
          this.messages.push({ role: 'assistant', content: this.streamingText })
          this.streamingText = ''
        } else if (this.pendingApproval && !this.error) {
          // 审批挂起：不写兜底文案，由审批卡片接管
        } else if (!this.error) {
          this.messages.push({
            role: 'assistant',
            content: '抱歉，本轮没有生成有效答复，请换个问法再试一次。',
          })
        }
      }
    },

    async resume(decision: 'approve' | 'reject') {
      const auth = useAuthStore()
      const pending = this.pendingApproval
      if (!pending) return
      this.pendingApproval = null
      this.error = ''
      this.streaming = true
      this.streamingText = ''

      const onEvent = (ev: SseEvent) => {
        if (ev.type === 'token') this.streamingText += ev.content
      }

      try {
        const req = { thread_id: pending.threadId, decision }
        if (this.mock) {
          await mockChatResume(
            req,
            auth.identity?.uid ?? '',
            auth.identity?.role ?? 'anonymous',
            pending.applicantUid,
            onEvent,
          )
        } else {
          await chatResume(req, auth.token, onEvent)
        }
      } catch (e) {
        // 自审自批 / 越权：展示后端 403 拒答文案
        this.handleError(e)
      } finally {
        this.streaming = false
        if (this.error && !this.streamingText) {
          // 审批被拒（403 等）：把拒答文案作为助理消息落进聊天记录，不显示成功兜底
          this.messages.push({ role: 'assistant', content: this.error })
        } else {
          const text =
            this.streamingText || '本次操作已处理完毕，如需其他帮助请继续提问。'
          this.messages.push({ role: 'assistant', content: text })
        }
        this.streamingText = ''
      }
    },

    handleError(e: unknown) {
      if (e instanceof ApiError) {
        this.error = e.detail
        if (e.status === 0 || e.detail.startsWith('网络错误')) this.backendDown = true
      } else if (e instanceof TypeError) {
        // fetch 网络层失败（后端不可达）
        this.error = '无法连接后端服务，请确认 API 已启动，或切换到 Mock 演示模式。'
        this.backendDown = true
      } else {
        this.error = `请求出错：${e instanceof Error ? e.message : String(e)}`
      }
    },
  },
})
