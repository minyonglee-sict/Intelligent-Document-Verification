import type {
  DocumentDetail,
  DocumentSummary,
  InvoiceFields,
  Job,
  RecheckResult,
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

  upload: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<Job>('/documents', { method: 'POST', body: form })
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

  remove: (id: number) =>
    request<{ deleted: number }>(`/documents/${id}`, { method: 'DELETE' }),
}
