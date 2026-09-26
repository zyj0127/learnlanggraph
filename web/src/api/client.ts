// HTTP + SSE 客户端：POST 型 SSE（EventSource 不支持 POST），
// 用 fetch + ReadableStream 手动解析 `data: {json}\n\n` 帧。
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

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail)
  }
}

async function parseError(resp: Response): Promise<ApiError> {
  let detail = `HTTP ${resp.status}`
  try {
    const body = await resp.json()
    if (typeof body?.detail === 'string') {
      detail = body.detail
    } else if (Array.isArray(body?.detail)) {
      // FastAPI 422 校验错误：detail 为 [{loc, msg, ...}] 数组
      detail = body.detail
        .map((d: { msg?: string }) => d?.msg ?? JSON.stringify(d))
        .join('；')
    }
  } catch {
    /* ignore */
  }
  return new ApiError(resp.status, detail)
}

function authHeaders(token: string | null): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

export async function issueToken(req: TokenRequest): Promise<TokenResponse> {
  const resp = await fetch(`${API_BASE}/auth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
  if (!resp.ok) throw await parseError(resp)
  return resp.json()
}

export async function healthCheck(): Promise<boolean> {
  try {
    const resp = await fetch(`${API_BASE}/health`, { method: 'GET' })
    return resp.ok
  } catch {
    return false
  }
}

/** 逐帧解析 SSE 流，回调每个事件；HTTP 非 2xx 抛 ApiError */
async function consumeSse(
  resp: Response,
  onEvent: (ev: SseEvent) => void,
): Promise<void> {
  if (!resp.ok) throw await parseError(resp)
  if (!resp.body) throw new Error('响应无 body，无法读取 SSE 流')

  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE 帧以空行分隔
    let idx: number
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      for (const line of frame.split('\n')) {
        if (line.startsWith('data: ')) {
          try {
            onEvent(JSON.parse(line.slice(6)) as SseEvent)
          } catch {
            /* 跳过无法解析的帧 */
          }
        }
      }
    }
  }
}

export async function chatStream(
  req: ChatRequest,
  token: string | null,
  onEvent: (ev: SseEvent) => void,
): Promise<void> {
  const resp = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify(req),
  })
  await consumeSse(resp, onEvent)
}

export async function chatResume(
  req: ResumeRequest,
  token: string | null,
  onEvent: (ev: SseEvent) => void,
): Promise<void> {
  const resp = await fetch(`${API_BASE}/chat/resume`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify(req),
  })
  await consumeSse(resp, onEvent)
}

// ---- 管理台 API（/api/admin/*，仅 HR/ADMIN）----

export async function fetchLeaveRequests(
  status: string,
  token: string | null,
): Promise<LeaveRequestListResponse> {
  const resp = await fetch(
    `${API_BASE}/admin/leave-requests?status=${encodeURIComponent(status)}`,
    { headers: authHeaders(token) },
  )
  if (!resp.ok) throw await parseError(resp)
  return resp.json()
}

export async function decideLeaveRequest(
  id: number,
  decision: 'approve' | 'reject',
  token: string | null,
): Promise<LeaveDecisionResponse> {
  const resp = await fetch(`${API_BASE}/admin/leave-requests/${id}/${decision}`, {
    method: 'POST',
    headers: authHeaders(token),
  })
  if (!resp.ok) throw await parseError(resp)
  return resp.json()
}

export async function fetchSecuritySummary(
  days: number,
  token: string | null,
): Promise<SecuritySummary> {
  const resp = await fetch(`${API_BASE}/admin/security-summary?days=${days}`, {
    headers: authHeaders(token),
  })
  if (!resp.ok) throw await parseError(resp)
  return resp.json()
}
