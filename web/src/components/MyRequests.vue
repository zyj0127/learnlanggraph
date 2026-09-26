<script setup lang="ts">
import { onMounted, ref } from 'vue'
import type { AdminLeaveRequest } from '../types'
import { fetchMyLeaveRequests } from '../api/client'
import { mockFetchMyLeaveRequests } from '../api/mock'
import { useAuthStore } from '../stores/auth'
import { useChatStore } from '../stores/chat'

const auth = useAuthStore()
const chat = useChatStore()

const items = ref<AdminLeaveRequest[]>([])
const loading = ref(false)
const error = ref('')

const statusLabel: Record<string, string> = {
  pending: '等待审批',
  approved: '已批准',
  rejected: '已驳回',
}

async function load() {
  if (!auth.identity) return
  loading.value = true
  error.value = ''
  try {
    const resp = chat.mock
      ? await mockFetchMyLeaveRequests(auth.identity.uid, 'all')
      : await fetchMyLeaveRequests('all', auth.token)
    items.value = resp.items
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    items.value = []
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <div class="my-requests">
    <div class="toolbar">
      <h2>📋 我的工单</h2>
      <button class="btn btn-ghost" :disabled="loading" @click="load()">🔄 刷新</button>
    </div>

    <p v-if="error" class="feedback error">{{ error }}</p>

    <table v-if="items.length" class="queue-table">
      <thead>
        <tr>
          <th>编号</th><th>类型</th><th>起止日期</th><th>天数</th>
          <th>事由</th><th>申请时间</th><th>状态</th><th>审批人 / 时间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in items" :key="r.id">
          <td>LR-{{ r.id }}</td>
          <td>{{ r.leave_type }}</td>
          <td>{{ r.start_date }} ~ {{ r.end_date }}</td>
          <td>{{ r.days }}</td>
          <td class="reason">{{ r.reason || '—' }}</td>
          <td>{{ r.created_at || '—' }}</td>
          <td>
            <span class="status-tag" :class="r.status">{{ statusLabel[r.status] || r.status }}</span>
            <div v-if="r.status === 'pending'" class="pending-hint">等待 HR 审批中</div>
          </td>
          <td>
            <template v-if="r.approver">{{ r.approver }}<br />{{ r.decided_at || '' }}</template>
            <template v-else>—</template>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else-if="!loading" class="empty-queue">
      📭 您还没有请假工单。在聊天里对我说「帮我请两天年假」即可发起申请（需人工审批后生效）。
    </p>
  </div>
</template>

<style scoped>
.my-requests h2 { margin: 0; font-size: 17px; }
.toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.feedback.error { padding: 8px 12px; border-radius: 8px; background: #fef2f2; color: #b91c1c; font-size: 13px; }
.queue-table { width: 100%; border-collapse: collapse; font-size: 13px; background: #fff; }
.queue-table th, .queue-table td { padding: 8px 10px; border-bottom: 1px solid #e5e7eb; text-align: left; }
.queue-table th { background: #f8fafc; color: #475569; font-weight: 600; }
.queue-table .reason { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status-tag { padding: 2px 8px; border-radius: 999px; font-size: 12px; }
.status-tag.pending { background: #fef3c7; color: #92400e; }
.status-tag.approved { background: #d1fae5; color: #065f46; }
.status-tag.rejected { background: #fee2e2; color: #991b1b; }
.pending-hint { margin-top: 4px; font-size: 12px; color: #b45309; }
.empty-queue { padding: 24px; text-align: center; color: #64748b; background: #f8fafc; border-radius: 10px; }
</style>
