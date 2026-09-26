import { defineStore } from 'pinia'
import type { AdminLeaveRequest, SecuritySummary } from '../types'
import {
  decideLeaveRequest,
  fetchLeaveRequests,
  fetchSecuritySummary,
} from '../api/client'
import {
  mockDecideLeaveRequest,
  mockFetchLeaveRequests,
  mockFetchSecuritySummary,
} from '../api/mock'
import { useAuthStore } from './auth'
import { useChatStore } from './chat'

/** 管理台（审批队列 + 安全看板）：仅 HR/ADMIN；mock 模式走演示数据 */
export const useAdminStore = defineStore('admin', {
  state: () => ({
    queue: [] as AdminLeaveRequest[],
    queueLoading: false,
    queueError: '',
    summary: null as SecuritySummary | null,
    summaryLoading: false,
    // 最近一次行内操作的反馈文案（成功/失败统一落这里）
    feedback: '' as string,
  }),
  getters: {
    isMock(): boolean {
      return useChatStore().mock
    },
  },
  actions: {
    async loadQueue(status = 'pending') {
      const auth = useAuthStore()
      this.queueLoading = true
      this.queueError = ''
      try {
        const resp = this.isMock
          ? await mockFetchLeaveRequests(status)
          : await fetchLeaveRequests(status, auth.token)
        this.queue = resp.items
      } catch (e) {
        this.queueError = e instanceof Error ? e.message : String(e)
        this.queue = []
      } finally {
        this.queueLoading = false
      }
    },

    async decide(id: number, decision: 'approve' | 'reject') {
      const auth = useAuthStore()
      this.feedback = ''
      try {
        const resp = this.isMock
          ? await mockDecideLeaveRequest(
              id, decision, auth.identity?.uid ?? '', auth.identity?.role ?? 'anonymous',
            )
          : await decideLeaveRequest(id, decision, auth.token)
        this.feedback = resp.message
      } catch (e) {
        this.feedback = e instanceof Error ? e.message : String(e)
      } finally {
        // 无论成败都刷新队列（状态单一事实源在后端 / mock 池）
        await this.loadQueue()
      }
    },

    async loadSummary(days = 7) {
      const auth = useAuthStore()
      this.summaryLoading = true
      try {
        this.summary = this.isMock
          ? await mockFetchSecuritySummary()
          : await fetchSecuritySummary(days, auth.token)
      } catch {
        this.summary = null
      } finally {
        this.summaryLoading = false
      }
    },
  },
})
