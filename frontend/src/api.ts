import type {
  AdminUser,
  AuthUser,
  DocumentDetail,
  DocumentSummary,
  InvoiceFields,
  Job,
  ChatResponse,
  ChatTurn,
  RecheckResult,
  ReportSummary,
  Role,
} from './types'

const BASE = '/api'
const TOKEN_KEY = 'docverify_token'

/** 백엔드가 4xx/5xx 를 돌려줬을 때, 본문에 담긴 사유를 그대로 들고 올라간다. */
export class ApiError extends Error {
  // 생성자 파라미터 프로퍼티는 이 프로젝트의 erasableSyntaxOnly 설정에서 막힌다.
  readonly status: number
  readonly body: unknown

  constructor(status: number, message: string, body: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

/** 로그인 토큰. localStorage 가 막혀 있어도(사생활 보호 모드 등) 로그인 자체는
 *  되게, 저장 실패는 조용히 삼킨다 -- 대신 새로고침하면 다시 로그인해야 한다. */
export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    // 무시 -- 위 주석 참고.
  }
}

/** 세션이 만료·무효화되어 401 을 받으면 쏜다. App 이 이걸 듣고 로그인 화면으로 돌린다. */
const UNAUTHORIZED_EVENT = 'docverify:unauthorized'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`${BASE}${path}`, { ...init, headers })
  const text = await response.text()
  let body: unknown = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = text
    }
  }

  if (!response.ok) {
    // 로그인 자체의 401(아이디·비밀번호 오류)과 비밀번호 변경의 401(현재 비밀번호
    // 오류)은 세션 만료가 아니다 -- 세션은 멀쩡한데 튕겨낼 이유가 없다.
    const isAuthCheck401 = path === '/auth/login' || path === '/auth/change-password'
    if (response.status === 401 && !isAuthCheck401) {
      setToken(null)
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    }
    // 엔진은 사유를 detail 에 담는다. 승인 거절(409)은 detail 이 객체다.
    const detail = (body as { detail?: unknown })?.detail
    const message =
      typeof detail === 'string'
        ? detail
        : typeof (detail as { message?: string })?.message === 'string'
          ? (detail as { message: string }).message
          // 520~530은 우리 서버가 만드는 코드가 아니라 Cloudflare Tunnel 자체가
          // "origin(우리 컨테이너)한테 응답을 못 받았다"고 붙이는 코드다 --
          // 엔진·백엔드·DB가 실제로 죽은 게 아니라, 메모리 과부하 등으로 응답이
          // 늦어져서 Cloudflare가 먼저 포기한 경우가 대부분이다("연결 안 됨"이라고
          // 하면 서버가 죽은 것처럼 오해하기 쉽다).
          : response.status >= 520 && response.status <= 530
            ? '서버 응답이 늦어지고 있습니다. 처리 중인 작업이 많으면 시간이 걸릴 수 있으니 잠시 후 새로고침해주세요.'
            : `요청이 실패했습니다 (${response.status})`
    throw new ApiError(response.status, message, detail ?? body)
  }
  return body as T
}

export function onUnauthorized(handler: () => void): () => void {
  window.addEventListener(UNAUTHORIZED_EVENT, handler)
  return () => window.removeEventListener(UNAUTHORIZED_EVENT, handler)
}

/** 원본 파일(대개 PDF) 다운로드. 로그인 토큰이 Authorization 헤더로만 실리므로
 *  (쿠키 세션이 아니다) <a href> 로 그냥 못 받는다 -- 브라우저가 커스텀 헤더를
 *  안 붙여준다. fetch 로 직접 받아 Blob 으로 저장한다. */
export async function downloadFile(id: number, fallbackFilename: string): Promise<void> {
  const headers = new Headers()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`${BASE}/documents/${id}/file`, { headers })
  if (!response.ok) {
    if (response.status === 401) {
      setToken(null)
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    }
    throw new ApiError(response.status, `파일을 받지 못했습니다 (${response.status})`, null)
  }

  // 서버가 Content-Disposition 에 원본 파일명을 실어 보내므로, 되도록 그걸 쓰고
  // (프록시가 헤더를 못 살리는 경우를 대비해) 없으면 목록에 있던 이름으로 대신한다.
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)
  const filename = match ? decodeURIComponent(match[1]) : fallbackFilename

  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export const api = {
  health: () => request<{ ok: boolean; database: string; ollama: string }>('/health'),

  // ---- 로그인 ----

  login: (username: string, password: string) =>
    request<{ token: string } & AuthUser>('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }),

  signup: (input: { username: string; displayName: string; password: string }) =>
    request<{ token: string } & AuthUser>('/auth/signup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: input.username,
        display_name: input.displayName,
        password: input.password,
      }),
    }),

  logout: () => request<{ ok: boolean }>('/auth/logout', { method: 'POST' }),

  me: () => request<AuthUser>('/auth/me'),

  forgotPassword: (username: string, message: string) =>
    request<{ ok: boolean }>('/auth/forgot-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, message }),
    }),

  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ ok: boolean }>('/auth/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),

  // ---- 사용자 관리 (관리자 전용) ----

  adminListUsers: () => request<AdminUser[]>('/admin/users'),

  adminSetRole: (userId: number, role: Role) =>
    request<AdminUser>(`/admin/users/${userId}/role`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role }),
    }),

  adminResetPassword: (userId: number, newPassword: string) =>
    request<{ ok: boolean }>(`/admin/users/${userId}/reset-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_password: newPassword }),
    }),

  listDocuments: (status?: string) =>
    request<DocumentSummary[]>(
      status ? `/documents?status=${encodeURIComponent(status)}` : '/documents',
    ),

  counts: () => request<Record<string, number>>('/documents/counts'),

  getDocument: (id: number) => request<DocumentDetail>(`/documents/${id}`),

  getMarkdown: (id: number) =>
    request<{ document_id: number; markdown: string }>(`/documents/${id}/markdown`),

  getDoclingJson: (id: number) =>
    request<{ document_id: number; docling_json: unknown }>(`/documents/${id}/docling-json`),

  upload: (file: File, skipDuplicates = true) => {
    const form = new FormData()
    form.append('file', file)
    return request<Job>(`/documents?skipDuplicates=${skipDuplicates}`, {
      method: 'POST',
      body: form,
    })
  },

  getJob: (jobId: string) => request<Job>(`/jobs/${jobId}`),

  recheck: (id: number, fields: InvoiceFields, note: string | null) =>
    request<RecheckResult>(`/documents/${id}/recheck`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fields, note }),
    }),

  approve: (id: number, fields: InvoiceFields, note: string | null, force: boolean) =>
    request<DocumentDetail>(`/documents/${id}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fields, note, force }),
    }),

  bulkApprove: (documentIds: number[]) =>
    request<{ approved: number; skipped: number[] }>('/documents/bulk-approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document_ids: documentIds }),
    }),

  remove: (id: number) =>
    request<{ deleted: number }>(`/documents/${id}`, { method: 'DELETE' }),

  bulkDelete: (documentIds: number[]) =>
    request<{ deleted: number; document_ids: number[] }>('/documents/bulk-delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document_ids: documentIds }),
    }),

  // ---- 화면에서 MCP 도구 쓰기 ----

  chatTools: () =>
    request<{ connected: boolean; model: string; tools: { name: string; description: string }[] }>(
      '/chat/tools',
    ),

  chat: (question: string, history: ChatTurn[]) =>
    request<ChatResponse>('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, history }),
    }),

  // ---- 오류 신고 ----

  listReports: (scope: 'open' | 'all' = 'all') =>
    request<ReportSummary[]>(`/reports?scope=${scope}`),

  reportCounts: () => request<{ open: number; total: number }>('/reports/counts'),

  createReport: (input: {
    message: string
    section: string
    documentId?: number
    attachContext: boolean
    pasted: string[]
    files: File[]
  }) => {
    const form = new FormData()
    form.append('message', input.message)
    form.append('section', input.section)
    form.append('attach_context', String(input.attachContext))
    if (input.documentId !== undefined) form.append('document_id', String(input.documentId))
    // 붙여넣은 캡처는 data URL 문자열로, 고른 파일은 그대로 보낸다.
    input.pasted.forEach((src) => form.append('pasted', src))
    input.files.forEach((file) => form.append('files', file))
    return request<ReportSummary>('/reports', { method: 'POST', body: form })
  },

  setReportStatus: (slug: string, status: 'OPEN' | 'RESOLVED') =>
    request<{ slug: string }>(`/reports/${encodeURIComponent(slug)}/status?status=${status}`, {
      method: 'POST',
    }),

  deleteReport: (slug: string) =>
    request<{ deleted: number }>(`/reports/${encodeURIComponent(slug)}`, { method: 'DELETE' }),
}
