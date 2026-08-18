import type {
  DocumentDetail,
  DocumentSummary,
  InvoiceFields,
  Job,
  ChatResponse,
  ChatTurn,
  RecheckResult,
  ReportSummary,
} from './types'

const BASE = '/api'

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init)
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
    // 엔진은 사유를 detail 에 담는다. 승인 거절(409)은 detail 이 객체다.
    const detail = (body as { detail?: unknown })?.detail
    const message =
      typeof detail === 'string'
        ? detail
        : typeof (detail as { message?: string })?.message === 'string'
          ? (detail as { message: string }).message
          : `요청이 실패했습니다 (${response.status})`
    throw new ApiError(response.status, message, detail ?? body)
  }
  return body as T
}

export const api = {
  health: () => request<{ ok: boolean; database: string; ollama: string }>('/health'),

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
