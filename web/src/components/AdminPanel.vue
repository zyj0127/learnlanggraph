<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useAdminStore } from '../stores/admin'
import { useAuthStore } from '../stores/auth'

const admin = useAdminStore()
const auth = useAuthStore()

const statusFilter = ref<'pending' | 'approved' | 'rejected' | 'all'>('pending')

onMounted(() => {
  void admin.loadQueue(statusFilter.value)
  void admin.loadSummary(7)
})

const statusLabel: Record<string, string> = {
  pending: '待审批',
  approved: '已批准',
  rejected: '已驳回',
}

function changeFilter() {
  void admin.loadQueue(statusFilter.value)
}

const summaryCards = computed(() => {
  const s = admin.summary
  if (!s) return []
  return [
    { label: '越权拦截总数', value: s.access_denied_total, icon: '🛡️' },
    { label: '审批通过', value: s.approvals.approved, icon: '✅' },
    { label: '审批拒绝', value: s.approvals.rejected, icon: '❌' },
    { label: '审批拦截（越权/自批）', value: s.approvals.denied, icon: '⛔' },
    { label: '证明开具', value: s.cert_issued, icon: '📄' },
  ]
})

const topActions = computed(() => {
  const s = admin.summary
  if (!s) return []
  return s.access_denied_top_actions.map((a) => ({
    label: s.action_labels[a.action] || a.action,
    count: a.count,
  }))
})
</script>

<template>
  <div class="admin-panel">
    <h2>🗂️ 审批队列</h2>
    <div class="queue-toolbar">
      <label>
        状态：
        <select v-model="statusFilter" @change="changeFilter">
          <option value="pending">待审批</option>
          <option value="approved">已批准</option>
          <option value="rejected">已驳回</option>
          <option value="all">全部</option>
        </select>
      </label>
      <button class="btn btn-ghost" :disabled="admin.queueLoading" @click="changeFilter()">
        🔄 刷新
      </button>
    </div>

    <p v-if="admin.feedback" class="feedback">{{ admin.feedback }}</p>
    <p v-if="admin.queueError" class="feedback error">{{ admin.queueError }}</p>

    <table v-if="admin.queue.length" class="queue-table">
      <thead>
        <tr>
          <th>编号</th><th>员工</th><th>类型</th><th>起止日期</th>
          <th>天数</th><th>事由</th><th>申请时间</th><th>状态</th>
          <th v-if="statusFilter === 'pending'">操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in admin.queue" :key="r.id">
          <td>LR-{{ r.id }}</td>
          <td>{{ r.name || '未知' }}（{{ r.uid }}）</td>
          <td>{{ r.leave_type }}</td>
          <td>{{ r.start_date }} ~ {{ r.end_date }}</td>
          <td>{{ r.days }}</td>
          <td class="reason">{{ r.reason || '—' }}</td>
          <td>{{ r.created_at || '—' }}</td>
          <td>
            <span class="status-tag" :class="r.status">{{ statusLabel[r.status] || r.status }}</span>
            <span v-if="r.approver" class="approver">by {{ r.approver }}</span>
          </td>
          <td v-if="statusFilter === 'pending'" class="actions">
            <button class="btn btn-approve" @click="admin.decide(r.id, 'approve')">批准</button>
            <button class="btn btn-reject" @click="admin.decide(r.id, 'reject')">拒绝</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else-if="!admin.queueLoading" class="empty-queue">
      📭 当前没有{{ statusLabel[statusFilter] }}的请假工单。
    </p>

    <h2>🛡️ 安全看板（近 7 天）</h2>
    <div v-if="admin.summary" class="summary-grid">
      <div v-for="c in summaryCards" :key="c.label" class="summary-card">
        <div class="summary-icon">{{ c.icon }}</div>
        <div class="summary-value">{{ c.value }}</div>
        <div class="summary-label">{{ c.label }}</div>
      </div>
    </div>
    <div v-if="topActions.length" class="top-actions">
      <h3>Top 越权动作</h3>
      <ul>
        <li v-for="a in topActions" :key="a.label">{{ a.label }}：{{ a.count }} 次</li>
      </ul>
    </div>
    <p class="admin-note">当前身份：{{ auth.identity?.name || auth.identity?.uid }}（{{ auth.roleLabel }}）</p>
  </div>
</template>

<style scoped>
.admin-panel h2 { margin: 18px 0 10px; font-size: 17px; }
.queue-toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 10px; }
.queue-toolbar select { padding: 4px 8px; border-radius: 6px; border: 1px solid #d0d7de; }
.feedback { padding: 8px 12px; border-radius: 8px; background: #ecfdf5; color: #047857; font-size: 13px; }
.feedback.error { background: #fef2f2; color: #b91c1c; }
.queue-table { width: 100%; border-collapse: collapse; font-size: 13px; background: #fff; }
.queue-table th, .queue-table td { padding: 8px 10px; border-bottom: 1px solid #e5e7eb; text-align: left; }
.queue-table th { background: #f8fafc; color: #475569; font-weight: 600; }
.queue-table .reason { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.status-tag { padding: 2px 8px; border-radius: 999px; font-size: 12px; }
.status-tag.pending { background: #fef3c7; color: #92400e; }
.status-tag.approved { background: #d1fae5; color: #065f46; }
.status-tag.rejected { background: #fee2e2; color: #991b1b; }
.approver { margin-left: 6px; color: #94a3b8; font-size: 12px; }
.actions .btn { padding: 4px 10px; font-size: 12px; margin-right: 6px; }
.empty-queue { padding: 24px; text-align: center; color: #64748b; background: #f8fafc; border-radius: 10px; }
.summary-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
.summary-card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 14px; text-align: center; }
.summary-icon { font-size: 20px; }
.summary-value { font-size: 24px; font-weight: 700; color: #0f172a; margin: 4px 0; }
.summary-label { font-size: 12px; color: #64748b; }
.top-actions { margin-top: 12px; font-size: 13px; color: #334155; }
.top-actions h3 { font-size: 14px; margin-bottom: 6px; }
.admin-note { margin-top: 16px; color: #94a3b8; font-size: 12px; }
</style>
