import { defineStore } from 'pinia'
import type { Identity, Role } from '../types'
import { issueToken } from '../api/client'
import { mockIssueToken } from '../api/mock'

const STORAGE_KEY = 'hr-assistant.auth'

interface PersistedAuth {
  token: string
  identity: Identity
}

function loadPersisted(): PersistedAuth | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as PersistedAuth) : null
  } catch {
    return null
  }
}

export const useAuthStore = defineStore('auth', {
  state: () => {
    const persisted = loadPersisted()
    return {
      token: persisted?.token ?? (null as string | null),
      identity: persisted?.identity ?? (null as Identity | null),
      // mock 模式由 chat store 统一管理（VITE_MOCK 或手动切换）
    }
  },
  getters: {
    isLoggedIn: (s) => s.identity !== null,
    roleLabel: (s): string => {
      const labels: Record<Role, string> = {
        anonymous: '匿名访客',
        employee: '员工',
        hr: 'HR 专员',
        admin: '管理员',
      }
      return s.identity ? labels[s.identity.role] : labels.anonymous
    },
  },
  actions: {
    /** 登录：uid+role 换 JWT（dev 模式端点）；mock 模式走内置签发 */
    async login(uid: string, role: Exclude<Role, 'anonymous'>, name: string, mock: boolean) {
      const resp = mock
        ? await mockIssueToken({ uid, role, name })
        : await issueToken({ uid, role, name })
      this.token = resp.access_token
      this.identity = { uid, name, role }
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ token: this.token, identity: this.identity }),
      )
    },
    logout() {
      this.token = null
      this.identity = null
      localStorage.removeItem(STORAGE_KEY)
    },
  },
})
