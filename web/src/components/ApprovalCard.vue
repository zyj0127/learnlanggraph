<script setup lang="ts">
import { computed } from 'vue'
import { useChatStore } from '../stores/chat'
import { useAuthStore } from '../stores/auth'

const chat = useChatStore()
const auth = useAuthStore()

const pending = computed(() => chat.pendingApproval)
const denial = computed(() => chat.approvalDenial)
const canApprove = computed(() => denial.value === null)

const busy = computed(() => chat.streaming)

function decide(decision: 'approve' | 'reject') {
  void chat.resume(decision)
}
</script>

<template>
  <div v-if="pending" class="approval-card">
    <div class="approval-head">🔒 敏感操作需要人工审批</div>
    <p class="approval-detail">{{ pending.detail }}</p>
    <p class="approval-meta">
      申请人 uid：<code>{{ pending.applicantUid || '未知' }}</code>
      ｜ 当前身份：{{ auth.identity ? `${auth.identity.name || auth.identity.uid}（${auth.roleLabel}）` : '匿名访客' }}
    </p>
    <p v-if="denial" class="approval-denial">{{ denial }}</p>
    <div class="approval-actions">
      <button
        class="btn btn-approve"
        :disabled="!canApprove || busy"
        @click="decide('approve')"
      >
        ✅ 批准执行
      </button>
      <button
        class="btn btn-reject"
        :disabled="!canApprove || busy"
        @click="decide('reject')"
      >
        ❌ 拒绝执行
      </button>
    </div>
  </div>
</template>
