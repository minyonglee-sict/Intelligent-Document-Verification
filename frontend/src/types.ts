// 백엔드(=Python 엔진)가 돌려주는 모양. 스키마의 주인은 엔진이므로 여기서는
// 화면이 실제로 쓰는 필드만 적는다.

export type DocStatus = 'PROCESSING' | 'PENDING' | 'ERROR' | 'VALIDATED' | 'FAILED'

export interface LineItem {
  position: number | null
  description: string
  quantity: number | null
  unit_price: number | null
  amount: number | null
  tax: number | null
}

export interface InvoiceFields {
  doc_type: 'INVOICE' | 'RECEIPT' | 'UNKNOWN'
  invoice_number: string | null
  issue_date: string | null
  due_date: string | null
  vendor_name: string | null
  buyer_name: string | null
  po_number: string | null
  currency: string | null
  subtotal: number | null
  tax: number | null
  shipping: number | null
  total_amount: number | null
  line_items: LineItem[]
}

export interface ValidationError {
  id: number | null
  field: string | null
  message: string
  severity: 'critical' | 'warning'
  source: string
  resolved: boolean
}

export interface DocumentSummary {
  id: number
  filename: string
  status: DocStatus
  status_label: string
  page_count: number | null
  model: string | null
  error_count: number
  created_at: string | null
  validated_at: string | null
}

export interface DocumentDetail extends DocumentSummary {
  fields: InvoiceFields
  errors: ValidationError[]
  reviewer_note: string | null
  failure_reason: string | null
}

export interface Job {
  job_id: string
  state: 'QUEUED' | 'RUNNING' | 'DONE' | 'FAILED'
  filename: string
  document_id: number | null
  document_status: string | null
  error_count: number
  skipped: boolean
  message: string
  started_at: string
  finished_at: string | null
}

export interface RecheckResult {
  document_id: number
  errors: ValidationError[]
  critical_count: number
}

export interface ReportSummary {
  slug: string
  number: number
  status: 'OPEN' | 'RESOLVED'
  created_at: string
  section: string
  document_id: number | null
  message: string
  images: string[]
  exception: string | null
}
