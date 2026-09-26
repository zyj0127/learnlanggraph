// Mock 演示模式：无后端时内置流式响应与模拟审批流。
// 事件序列与后端 SSE 契约一致（token / approval_required / done）。
import type {
  ChatRequest,
  LeaveDecisionResponse,
  LeaveRequestListResponse,
  ResumeRequest,
  SecuritySummary,
  SseEvent,
  TokenRequest,
  TokenResponse,
} from '../types'
import { IDLE_TIMEOUT_CMD } from '../types'
import { ApiError } from './client'

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}

/** 按字符切片逐 token 推送，模拟流式输出 */
async function streamText(
  text: string,
  onEvent: (ev: SseEvent) => void,
  chunkSize = 3,
): Promise<void> {
  for (let i = 0; i < text.length; i += chunkSize) {
    onEvent({ type: 'token', content: text.slice(i, i + chunkSize) })
    await sleep(30)
  }
}

// 模拟敏感操作关键词（触发审批挂起）
const SENSITIVE_RE = /(证明|在职证明|收入证明)/
// 请假申请（写操作，同样触发审批挂起）
const LEAVE_RE = /(请假|休年假|请年假|请病假|请事假)/

// mock 会话内最近一次挂起的场景（resume 应答据此分支）
let lastPendingKind: 'cert' | 'leave' = 'cert'

export async function mockIssueToken(req: TokenRequest): Promise<TokenResponse> {
  await sleep(200)
  const role = req.role.trim().toLowerCase()
  if (!['employee', 'hr', 'admin'].includes(role)) {
    throw new ApiError(400, 'role 必须是 employee / hr / admin')
  }
  // mock 模式签发伪 token（仅前端展示用，不用于真实验签）
  const fake = btoa(
    JSON.stringify({ uid: req.uid, role, name: req.name || '', mock: true }),
  )
  return {
    access_token: `mock.${fake}.signature`,
    token_type: 'bearer',
    expires_in: 3600,
  }
}

export async function mockChatStream(
  req: ChatRequest,
  onEvent: (ev: SseEvent) => void,
): Promise<void> {
  await sleep(300)

  // 闲置会话总结指令（对齐后端 IDLE_TIMEOUT_CMD 处理路径）
  if (req.question === IDLE_TIMEOUT_CMD) {
    await streamText(
      '【Mock 演示】⏱️ 本次会话总结：\n\n- 您咨询了年假余额、差旅报销政策等 HR 问题\n- 助理已逐一解答，未涉及敏感操作\n\n如需继续咨询，欢迎随时提问。',
      onEvent,
    )
    onEvent({ type: 'done' })
    return
  }

  if (SENSITIVE_RE.test(req.question)) {
    // 敏感操作：挂起等待人工审批
    lastPendingKind = 'cert'
    onEvent({
      type: 'approval_required',
      thread_id: req.thread_id,
      detail: '检测到敏感操作（开具证明），请调用 /chat/resume 提交人工审批决定',
    })
    onEvent({ type: 'done' })
    return
  }

  if (LEAVE_RE.test(req.question)) {
    // 请假申请（写操作）：挂起等待人工审批，detail 透出申请详情
    lastPendingKind = 'leave'
    onEvent({
      type: 'approval_required',
      thread_id: req.thread_id,
      detail:
        `Agent 正在为 uid ${req.uid} 提交请假申请（编号 LR-3）：\n` +
        '类型：年假；起止：2026-10-09 至 2026-10-10（共 2 天）；事由：家中有事。\n' +
        '是否授权执行？（输入approve或者reject）',
    })
    onEvent({ type: 'done' })
    return
  }

  const q = req.question
  let answer: string
  if (/年假|假期|休假/.test(q)) {
    answer = `【Mock 演示】您好，${req.uid} 员工：您本年度年假总额为 15 天，已使用 4 天，剩余 **11 天**。如需查询具体休假记录，可在人事系统中查看明细。\n\n> 演示模式数据为内置样例，与真实后端无关。`
  } else if (/报销|差旅/.test(q)) {
    answer = `【Mock 演示】差旅报销政策要点：\n\n1. 市内交通凭发票实报实销，单日上限 100 元\n2. 出差住宿按城市分级报销，一线城市上限 500 元/晚\n3. 报销需在出差结束后 30 天内提交\n\n详情见《员工手册》第 7 章。`
  } else if (/档案|个人信息|工资|薪资/.test(q)) {
    answer = `【Mock 演示】员工档案概要（${req.uid}）：\n\n- 部门：产品研发部\n- 职级：P5\n- 入职日期：2021-03-15\n- 工作地点：北京\n\n如需变更个人信息请联系 HR。`
  } else {
    answer = `【Mock 演示】收到您的问题：「${q}」。\n\n这是演示模式下的模拟流式回答。我可以协助查询：\n\n- 员工档案与假期余额\n- 员工手册政策问答（考勤、差旅、报销等）\n- 开具在职证明 / 收入证明（需人工审批）\n- 提交请假申请（年假/病假/事假，需人工审批）\n\n试试输入「帮我开一份在职证明」或「帮我请两天年假」体验审批流。`
  }

  await streamText(answer, onEvent)
  onEvent({ type: 'done' })
}

export async function mockChatResume(
  req: ResumeRequest,
  approverUid: string,
  approverRole: string,
  applicantUid: string,
  onEvent: (ev: SseEvent) => void,
): Promise<void> {
  await sleep(300)

  // 复刻后端审批人校验（auth/guard.py 口径）
  if (approverRole !== 'hr' && approverRole !== 'admin') {
    throw new ApiError(403, '权限提示：仅 HR 或管理员可执行人工审批。')
  }
  if (approverUid && approverUid === applicantUid) {
    throw new ApiError(
      403,
      '权限提示：审批人不能与申请人为同一人，请交由其他 HR 专员或管理员审批。',
    )
  }

  if (req.decision === 'approve') {
    await streamText(
      lastPendingKind === 'leave'
        ? `【Mock 演示】✅ 审批已通过（审批人 ${approverUid}）。\n\n员工 ${applicantUid} 的请假申请已生效（编号 LR-3）：年假 2026-10-09 至 2026-10-10（共 2 天），假期余额已同步扣减。`
        : `【Mock 演示】✅ 审批已通过（审批人 ${approverUid}）。\n\n已为员工 ${applicantUid} 开具在职证明，证明编号 CERT-2024-MOCK01，可在人事系统「我的证明」中下载 PDF 版本。`,
      onEvent,
    )
  } else {
    await streamText(
      lastPendingKind === 'leave'
        ? `【Mock 演示】❌ 员工 ${applicantUid} 的请假申请已被审批人 ${approverUid} 驳回，假期余额未发生变化。如需调整请假时间或类型，可重新发起申请。`
        : `【Mock 演示】❌ 本次开具证明的申请已被审批人 ${approverUid} 拒绝。如有疑问请联系 HR 专员了解具体原因，或补充材料后重新申请。`,
      onEvent,
    )
  }
  onEvent({ type: 'done' })
}

// ---- Mock 管理台：审批队列 + 安全看板（演示数据，无后端可预览）----

// mock 会话内工单池（approve/reject 会就地改状态，模拟单一事实源）
const mockQueue = [
  {
    id: 3, uid: '1003', name: '王五', leave_type: '年假',
    start_date: '2026-10-09', end_date: '2026-10-10', days: 2,
    reason: '家中有事', status: 'pending', approver: null,
    created_at: '2026-09-25 10:12:00', decided_at: null,
  },
  {
    id: 4, uid: '1002', name: '李四', leave_type: '病假',
    start_date: '2026-10-12', end_date: '2026-10-12', days: 1,
    reason: '感冒发烧', status: 'pending', approver: null,
    created_at: '2026-09-25 11:03:00', decided_at: null,
  },
  {
    id: 1, uid: '1001', name: '张三', leave_type: '年假',
    start_date: '2026-01-05', end_date: '2026-01-06', days: 2,
    reason: '家中事务', status: 'approved', approver: 'hr01',
    created_at: '2026-01-02 10:00:00', decided_at: '2026-01-02 15:30:00',
  },
] as {
  id: number; uid: string; name: string; leave_type: string
  start_date: string; end_date: string; days: number; reason: string
  status: 'pending' | 'approved' | 'rejected'; approver: string | null
  created_at: string; decided_at: string | null
}[]

export async function mockFetchLeaveRequests(
  status: string,
): Promise<LeaveRequestListResponse> {
  await sleep(200)
  const items =
    status === 'all' ? [...mockQueue] : mockQueue.filter((r) => r.status === status)
  return { status, count: items.length, items }
}

export async function mockDecideLeaveRequest(
  id: number,
  decision: 'approve' | 'reject',
  approverUid: string,
  approverRole: string,
): Promise<LeaveDecisionResponse> {
  await sleep(250)
  if (approverRole !== 'hr' && approverRole !== 'admin') {
    throw new ApiError(403, '权限提示：仅 HR 或管理员可执行人工审批。')
  }
  const row = mockQueue.find((r) => r.id === id)
  if (!row) throw new ApiError(404, `工单 ${id} 不存在`)
  if (row.status !== 'pending') {
    throw new ApiError(409, `工单 ${id} 已是 ${row.status} 状态，请勿重复审批`)
  }
  if (approverUid && approverUid === row.uid) {
    throw new ApiError(403, '权限提示：审批人不能与申请人为同一人，请交由其他 HR 专员或管理员审批。')
  }
  row.status = decision === 'approve' ? 'approved' : 'rejected'
  row.approver = approverUid || 'hr-mock'
  row.decided_at = new Date().toISOString().slice(0, 19).replace('T', ' ')
  return {
    ok: true,
    id,
    status: row.status,
    message:
      decision === 'approve'
        ? `工单 LR-${id} 已批准，假期余额已同步扣减（年假）。`
        : `工单 LR-${id} 已驳回，假期余额未发生变化。`,
  }
}

export async function mockFetchSecuritySummary(): Promise<SecuritySummary> {
  await sleep(200)
  return {
    total: 17,
    by_action_result: {
      'approval/approved': 5, 'approval/rejected': 2, 'approval/denied': 1,
      'view_profile/denied': 3, 'view_leave/denied': 2, 'cert_issued/success': 4,
    },
    by_role: { anonymous: 3, employee: 8, hr: 5, admin: 1 },
    approvals: { approved: 5, rejected: 2, denied: 1 },
    access_denied_total: 6,
    access_denied_top_actions: [
      { action: 'view_profile', count: 3 },
      { action: 'view_leave', count: 2 },
      { action: 'issue_cert', count: 1 },
    ],
    cert_issued: 4,
    period_days: 7,
    action_labels: {
      view_profile: '越权查档案', view_leave: '越权查假期',
      issue_cert: '越权开证明', approval: '人工审批', cert_issued: '证明开具',
    },
    role_labels: { anonymous: '匿名', employee: '员工', hr: 'HR', admin: '管理员' },
  }
}
