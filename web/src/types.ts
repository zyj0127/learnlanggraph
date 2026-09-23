// 与后端 api/server.py 严格对齐的类型定义

export type Role = 'anonymous' | 'employee' | 'hr' | 'admin'

export interface Identity {
  uid: string
  name: string
  role: Role
}

// POST /auth/token 请求体
export interface TokenRequest {
  uid: string
  role: string
  name?: string
  department?: string
}

// POST /auth/token 响应
export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
}

// POST /chat/stream 请求体
export interface ChatRequest {
  uid: string
  question: string
  thread_id: string
}

// POST /chat/resume 请求体
export interface ResumeRequest {
  thread_id: string
  decision: 'approve' | 'reject'
}

// SSE 事件（data: {...}\n\n）
export type SseEvent =
  | { type: 'token'; content: string }
  | { type: 'approval_required'; thread_id: string; detail: string }
  | { type: 'done' }

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface PendingApproval {
  threadId: string
  detail: string
  applicantUid: string
}

// 闲置会话总结指令（与 agent/constants.py 的 IDLE_TIMEOUT_CMD 一致）
export const IDLE_TIMEOUT_CMD = '__SYS_IDLE_TIMEOUT__'
