import { useEffect, useState } from 'react'
import { api } from '../api'
import type { DocumentDetail } from '../types'
import { JsonView } from './JsonView'

/**
 * 전체 문서 화면의 상세. Streamlit ui_documents._render_detail 과 같은 구성이다.
 *
 * 검수 화면과 달리 여기서는 고치지 않는다 -- 저장된 것이 어떻게 들어가 있는지
 * 확인하는 자리다. 그래서 편집 폼 대신 다섯 갈래로 나눠 보여준다.
 */

const HEADER_LABELS: [string, string][] = [
  ['doc_type', '문서 유형'],
  ['invoice_number', '문서 번호'],
  ['issue_date', '발행일'],
  ['due_date', '지급 기한'],
  ['vendor_name', '공급자명'],
  ['buyer_name', '수신자명'],
  ['po_number', '발주 번호'],
  ['currency', '통화'],
  ['subtotal', '공급가액'],
  ['tax', '세액'],
  ['shipping', '배송비'],
  ['total_amount', '총 청구액'],
]

type Pane = 'fields' | 'errors' | 'markdown' | 'docling' | 'record'

const PANES: [Pane, string][] = [
  ['fields', '추출 필드'],
  ['errors', '검증 오류'],
  ['markdown', 'Markdown'],
  ['docling', 'Docling JSON'],
  ['record', 'DB 레코드'],
]

export function DocumentInspector({
  documentId,
  onChanged,
}: {
  documentId: number
  onChanged: () => void
}) {
  const [detail, setDetail] = useState<DocumentDetail | null>(null)
  const [pane, setPane] = useState<Pane>('fields')
  const [markdown, setMarkdown] = useState<string | null>(null)
  const [docling, setDocling] = useState<unknown | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let alive = true
    setDetail(null)
    setPane('fields')
    setMarkdown(null)
    setDocling(null)
    api.getDocument(documentId).then((d) => { if (alive) setDetail(d) })
    return () => { alive = false }
  }, [documentId])

  // 원문과 Docling 원시 출력은 무겁다. 그 갈래를 열 때만 불러온다.
  useEffect(() => {
    if (pane === 'markdown' && markdown === null) {
      api.getMarkdown(documentId).then((r) => setMarkdown(r.markdown))
    }
    if (pane === 'docling' && docling === null) {
      api.getDoclingJson(documentId).then((r) => setDocling(r.docling_json))
    }
  }, [pane, documentId, markdown, docling])

  if (!detail) return <div className="panel muted">불러오는 중…</div>

  const remove = async () => {
    if (!confirm(`문서 #${documentId} ${detail.filename} 을(를) 지웁니다.\n원본 파일까지 함께 지워지며 되돌릴 수 없습니다.`)) return
    setBusy(true)
    try {
      await api.remove(documentId)
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  const items = detail.fields.line_items

  return (
    <div className="panel">
      <h2 style={{ marginBottom: '.4rem' }}>#{detail.id} · {detail.filename}</h2>
      <div className="row" style={{ marginBottom: '.9rem' }}>
        <span className={`badge ${detail.status}`}>{detail.status_label}</span>
        <span className="muted small">{detail.page_count ?? '-'}쪽 · {detail.model ?? '-'}</span>
      </div>

      {detail.failure_reason && <div className="notice err">{detail.failure_reason}</div>}

      <nav className="subtabs">
        {PANES.map(([key, label]) => (
          <button
            key={key}
            className={pane === key ? 'active' : ''}
            onClick={() => setPane(key)}
          >
            {label}
            {key === 'errors' && detail.errors.length > 0 && (
              <span className="count">{detail.errors.length}</span>
            )}
          </button>
        ))}
      </nav>

      {pane === 'fields' && <JsonView data={detail.fields} expanded />}

      {pane === 'errors' && (
        detail.errors.length === 0 ? (
          <p className="muted small">검증 오류가 없습니다.</p>
        ) : (
          <>
            {detail.errors.map((e, i) => (
              <div key={i} className={`issue ${e.severity === 'warning' ? 'warning' : ''}`}>
                {e.resolved ? '☑️' : e.severity === 'warning' ? '⚠️' : '🚨'} {e.message}
                <br />
                <span className="field">
                  {e.field ?? 'document'} · {e.source === 'rule' ? '규칙' : 'LLM'}
                  {e.resolved ? ' · 해소됨' : ''}
                </span>
              </div>
            ))}
          </>
        )
      )}

      {pane === 'markdown' && (
        <pre className="raw-text">{markdown ?? '불러오는 중…'}</pre>
      )}

      {pane === 'docling' && (
        docling === null ? <p className="muted small">불러오는 중…</p> : <JsonView data={docling} />
      )}

      {pane === 'record' && (
        <>
          <p className="muted small" style={{ margin: '0 0 .6rem' }}>
            검수 화면에서 확정한 값이 <code>documents</code> 컬럼과 <code>line_items</code> 테이블에
            저장된 모습입니다.
          </p>

          <h3 style={{ fontSize: '1rem', margin: '0 0 .4rem' }}>
            <code>documents</code> — 머리말 컬럼
          </h3>
          <table>
            <thead>
              <tr><th style={{ width: 180 }}>컬럼</th><th style={{ width: 140 }}>항목</th><th>값</th></tr>
            </thead>
            <tbody>
              {HEADER_LABELS.map(([column, label]) => {
                const value = (detail.fields as unknown as Record<string, unknown>)[column]
                return (
                  <tr key={column}>
                    <td><code>{column}</code></td>
                    <td className="muted">{label}</td>
                    <td>
                      {value === null || value === undefined || value === ''
                        ? <span className="json-null">NULL</span>
                        : String(value)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <h3 style={{ fontSize: '1rem', margin: '1.2rem 0 .4rem' }}>
            <code>line_items</code> — 품목 {items.length}행
          </h3>
          {items.length === 0 ? (
            <p className="muted small">저장된 품목이 없습니다.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th className="num" style={{ width: 60 }}>번호</th>
                  <th>품목</th>
                  <th className="num" style={{ width: 90 }}>수량</th>
                  <th className="num" style={{ width: 110 }}>단가</th>
                  <th className="num" style={{ width: 100 }}>세액</th>
                  <th className="num" style={{ width: 120 }}>금액</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item, i) => (
                  <tr key={i}>
                    <td className="num">{item.position ?? ''}</td>
                    <td>{item.description}</td>
                    <td className="num">{item.quantity ?? ''}</td>
                    <td className="num">{item.unit_price ?? ''}</td>
                    <td className="num">{item.tax ?? ''}</td>
                    <td className="num">{item.amount ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      <div className="row" style={{ marginTop: '1.2rem' }}>
        <button className="btn danger" onClick={remove} disabled={busy}>이 문서 삭제</button>
      </div>
    </div>
  )
}
