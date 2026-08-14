import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import type { DocumentDetail as Detail, InvoiceFields, ValidationError } from '../types'
import { FieldEditor } from './FieldEditor'
import { ReportForm } from './ReportPanel'

/**
 * 문서 한 건의 상세: 검증 오류 + 값 편집 + 재검증 + 승인.
 *
 * 승인은 엔진이 한 번 되묻는다(409). 그 되물음을 화면이 삼키지 않고 그대로 보여준 뒤,
 * 사용자가 다시 눌렀을 때만 force 로 넘긴다 -- Streamlit 검수 화면과 같은 흐름이다.
 */

function Issues({ errors }: { errors: ValidationError[] }) {
  return (
    <>
      {errors.map((e, i) => (
        <div key={i} className={`issue ${e.severity === 'warning' ? 'warning' : ''}`}>
          {e.severity === 'warning' ? '⚠️' : '🚨'} {e.message}
          <br />
          <span className="field">{e.field ?? 'document'} · {e.source === 'rule' ? '규칙' : 'LLM'}</span>
        </div>
      ))}
    </>
  )
}

interface Props {
  documentId: number
  onChanged: () => void
  /** 승인이 끝나면 알린다. 승인한 문서는 검수 목록에서 빠지므로 이 패널이
   *  곧 사라진다 -- 완료 안내는 부모가 띄워야 남는다. */
  onApproved?: (id: number) => void
}

export function DocumentDetailPanel({ documentId, onChanged, onApproved }: Props) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [fields, setFields] = useState<InvoiceFields | null>(null)
  const [note, setNote] = useState('')
  const [markdown, setMarkdown] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [flash, setFlash] = useState<{ kind: 'ok' | 'err' | 'warn'; text: string } | null>(null)
  // 엔진이 승인을 거절하면 세운다. 사용자가 그것을 보고도 한 번 더 누르면 force.
  const [forcing, setForcing] = useState(false)

  useEffect(() => {
    let alive = true
    setDetail(null)
    setFields(null)
    setMarkdown(null)
    setFlash(null)
    setForcing(false)
    api.getDocument(documentId).then((d) => {
      if (!alive) return
      setDetail(d)
      setFields(d.fields)
      setNote(d.reviewer_note ?? '')
    })
    return () => { alive = false }
  }, [documentId])

  if (!detail || !fields) return <div className="panel muted">불러오는 중…</div>

  const openErrors = detail.errors.filter((e) => !e.resolved)

  const reload = async () => {
    const fresh = await api.getDocument(documentId)
    setDetail(fresh)
    setFields(fresh.fields)
    onChanged()
  }

  const doRecheck = async () => {
    setBusy(true)
    setFlash(null)
    try {
      const result = await api.recheck(documentId, fields, note || null)
      await reload()
      setForcing(false)
      setFlash(
        result.critical_count > 0
          ? { kind: 'err', text: `아직 ${result.critical_count}건의 오류가 남아 있습니다.` }
          : { kind: 'ok', text: '규칙 검증을 모두 통과했습니다. 승인할 수 있습니다.' },
      )
    } catch (e) {
      setFlash({ kind: 'err', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const doApprove = async () => {
    setBusy(true)
    setFlash(null)
    try {
      await api.approve(documentId, fields, note || null, forcing)
      setForcing(false)
      setFlash({ kind: 'ok', text: `문서 #${documentId} 를 승인했습니다.` })
      onApproved?.(documentId)
      await reload()
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setForcing(true)
        setFlash({ kind: 'warn', text: `${e.message} 그대로 승인하려면 버튼을 한 번 더 누르세요.` })
      } else {
        setFlash({ kind: 'err', text: e instanceof Error ? e.message : String(e) })
      }
    } finally {
      setBusy(false)
    }
  }

  const doDelete = async () => {
    if (!confirm(`문서 #${documentId} ${detail.filename} 을(를) 지웁니다.\n원본 파일까지 함께 지워지며 되돌릴 수 없습니다.`)) return
    setBusy(true)
    try {
      await api.remove(documentId)
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  const loadMarkdown = async () => {
    if (markdown !== null) return
    const r = await api.getMarkdown(documentId)
    setMarkdown(r.markdown)
  }

  return (
    <div className="panel">
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
        <div>
          <strong>#{detail.id} {detail.filename}</strong>{' '}
          <span className={`badge ${detail.status}`}>{detail.status_label}</span>
        </div>
        <div className="small muted">
          {detail.page_count ?? '-'}쪽 · {detail.model ?? '-'}
        </div>
      </div>

      {detail.failure_reason && <div className="notice err">{detail.failure_reason}</div>}
      {flash && <div className={`notice ${flash.kind}`}>{flash.text}</div>}

      {/* 오류가 없으면 구역째 내리지 않는다. 위의 상태 뱃지가 이미 통과를
          말하고 있어서, '미해결 오류가 없습니다' 는 같은 말을 한 번 더 하는 것이다. */}
      {openErrors.length > 0 && (
        <>
          <h2>검증 오류 <span className="muted small" style={{ fontWeight: 400 }}>{openErrors.length}건</span></h2>
          <Issues errors={openErrors} />
        </>
      )}

      <h2 style={{ marginTop: 20 }}>추출 결과 수정</h2>
      <FieldEditor fields={fields} onChange={setFields} disabled={busy} />

      <label className="field" style={{ marginTop: 16 }}>
        <span>검수 메모</span>
        <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} disabled={busy} />
      </label>

      <div className="row" style={{ marginTop: 12 }}>
        {/* 검증을 통과한 건은 고칠 것이 없으므로 재검증을 내지 않는다.
            Streamlit ui_review 의 show_recheck 와 같은 판단이다. */}
        {detail.status !== 'PENDING' && (
          <button className="btn" onClick={doRecheck} disabled={busy}>재검증</button>
        )}
        <button className="btn primary" onClick={doApprove} disabled={busy}>
          {forcing
            ? '오류가 남아 있지만 승인'
            : detail.status === 'PENDING'
              ? '승인 → VALIDATED'
              : '수정 후 승인 → VALIDATED'}
        </button>
        <span style={{ flex: 1 }} />
        <button className="btn danger" onClick={doDelete} disabled={busy}>문서 삭제</button>
      </div>

      <details className="raw" style={{ marginTop: 18 }} onToggle={loadMarkdown}>
        <summary>Docling 추출 원문 (Markdown)</summary>
        <pre>{markdown ?? '불러오는 중…'}</pre>
      </details>

      {/* 오류를 만난 그 자리에서 신고한다. 문서 맥락이 자동으로 붙는다. */}
      <details className="report" style={{ marginTop: 10 }}>
        <summary>🐞 이 화면 오류 신고</summary>
        <div className="report-body">
          <ReportForm documentId={documentId} section="검수" onCreated={() => {}} />
        </div>
      </details>
    </div>
  )
}
