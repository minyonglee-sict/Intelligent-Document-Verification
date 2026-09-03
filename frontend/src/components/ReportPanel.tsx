import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { formatDateTime } from '../format'
import type { ReportSummary } from '../types'

/**
 * 오류 신고 화면. Streamlit ui_report 와 같은 구성이다.
 *
 * 붙여넣기는 Streamlit 에서는 위젯이 없어 커스텀 컴포넌트를 만들어야 했지만,
 * 브라우저에서는 paste 이벤트를 그대로 받으면 된다.
 */

const MAX_IMAGES = 4

function ReportForm({
  documentId,
  section,
  onCreated,
}: {
  documentId?: number
  section: string
  onCreated: () => void
}) {
  const [message, setMessage] = useState('')
  const [pasted, setPasted] = useState<string[]>([])
  const [files, setFiles] = useState<File[]>([])
  const [attach, setAttach] = useState(true)
  const [busy, setBusy] = useState(false)
  const [flash, setFlash] = useState<string | null>(null)
  const zoneRef = useRef<HTMLDivElement>(null)

  const readAsDataUrl = (file: File) =>
    new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.onerror = reject
      reader.readAsDataURL(file)
    })

  const addImages = async (list: FileList | File[] | null) => {
    if (!list) return
    const picked = Array.from(list).filter((f) => f.type.startsWith('image/'))
    if (!picked.length) return
    const encoded = await Promise.all(picked.map(readAsDataUrl))
    setPasted((prev) => [...prev, ...encoded].slice(0, MAX_IMAGES))
  }

  const submit = async () => {
    setBusy(true)
    setFlash(null)
    try {
      const created = await api.createReport({
        message,
        section,
        documentId,
        attachContext: attach,
        pasted,
        files,
      })
      setMessage('')
      setPasted([])
      setFiles([])
      setFlash(`수정 요청을 남겼습니다 → data/reports/${created.slug}/report.md`)
      onCreated()
    } catch (e) {
      setFlash(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      {flash && <div className="notice ok small" style={{ marginBottom: 10 }}>{flash}</div>}

      <div
        ref={zoneRef}
        className="paste-zone"
        tabIndex={0}
        onPaste={(e) => {
          if (e.clipboardData.files.length) {
            e.preventDefault()
            addImages(e.clipboardData.files)
          }
        }}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => { e.preventDefault(); addImages(e.dataTransfer.files) }}
        onClick={() => zoneRef.current?.focus()}
      >
        {pasted.length === 0
          ? '여기를 클릭한 뒤 Ctrl+V 로 화면 캡처를 붙여넣으세요 (끌어다 놓아도 됩니다)'
          : `${pasted.length}장 붙여넣었습니다. 더 붙이려면 다시 클릭하고 Ctrl+V`}
        {pasted.length > 0 && (
          <div className="thumbs">
            {pasted.map((src, i) => (
              <div className="thumb" key={i}>
                <img src={src} alt={`캡처 ${i + 1}`} />
                <button
                  onClick={(e) => { e.stopPropagation(); setPasted((p) => p.filter((_, j) => j !== i)) }}
                  title="이 캡처 빼기"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <label className="field" style={{ marginTop: 10 }}>
        <span>캡처 파일로 올리기</span>
        <input
          type="file"
          accept="image/*"
          multiple
          onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
        />
      </label>

      <label className="field">
        <span>무엇이 잘못됐나요?</span>
        <textarea
          rows={3}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="예) 품목 3행 단가가 원문(12,000)과 다르게 1,200 으로 들어옵니다."
          disabled={busy}
        />
      </label>

      {documentId !== undefined && (
        <label className="row small" style={{ gap: 6, marginBottom: 10 }}>
          <input
            type="checkbox"
            style={{ width: 'auto' }}
            checked={attach}
            onChange={(e) => setAttach(e.target.checked)}
          />
          이 문서의 맥락을 함께 보내기 (문서 행·검증 오류·품목·Docling 원문)
        </label>
      )}

      <div className="row">
        <button className="btn primary" onClick={submit} disabled={busy || !message.trim()}>
          🐞 수정 요청 보내기
        </button>
        {!message.trim() && <span className="muted small">증상을 한 줄이라도 적어야 보낼 수 있습니다.</span>}
      </div>
    </div>
  )
}

interface ReportPanelProps {
  /** 미처리 건수가 바뀔 때(처리 완료·다시 열기·삭제) 상단 탭 배지도 새로 고치라고 부모에 알린다. */
  onChanged?: () => void
}

export function ReportPanel({ onChanged }: ReportPanelProps) {
  const [reports, setReports] = useState<ReportSummary[]>([])
  const [scope, setScope] = useState<'open' | 'all'>('open')
  const [openForm, setOpenForm] = useState(false)

  const load = () => { api.listReports('all').then(setReports) }
  useEffect(load, [])

  const shown = scope === 'open' ? reports.filter((r) => r.status === 'OPEN') : reports
  const openCount = reports.filter((r) => r.status === 'OPEN').length

  const setStatus = async (slug: string, status: 'OPEN' | 'RESOLVED') => {
    await api.setReportStatus(slug, status)
    load()
    onChanged?.()
  }

  const remove = async (slug: string) => {
    if (!confirm('이 신고를 지웁니다. 캡처 파일까지 함께 지워지며 되돌릴 수 없습니다.')) return
    await api.deleteReport(slug)
    load()
    onChanged?.()
  }

  return (
    <>
      <div className="panel">
        <h2>🐞 오류 신고</h2>
        <p className="muted small" style={{ margin: '0 0 10px' }}>
          검수 화면에서 오류를 만나면 그 자리에서 캡처와 함께 신고하세요. 신고는{' '}
          <code>data/reports/</code> 아래에 파일로 쌓입니다 — DB가 죽어 있어도 남습니다.
        </p>

        <button className="btn" onClick={() => setOpenForm((v) => !v)}>
          {openForm ? '닫기' : '문서와 무관한 오류 신고하기'}
        </button>
        {openForm && (
          <div style={{ marginTop: 12 }}>
            <ReportForm section="일반" onCreated={() => { load(); setOpenForm(false); onChanged?.() }} />
          </div>
        )}
      </div>

      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
          <div className="row" style={{ gap: 6 }}>
            {(['open', 'all'] as const).map((s) => (
              <button
                key={s}
                className={`btn ${scope === s ? 'primary' : ''}`}
                style={{ padding: '5px 12px' }}
                onClick={() => setScope(s)}
              >
                {s === 'open' ? '미처리' : '전체'}
              </button>
            ))}
          </div>
          <span className="muted small">
            미처리 {openCount}건 / 전체 {reports.length}건
          </span>
        </div>

        {shown.length === 0 ? (
          <p className="muted small">{scope === 'open' ? '미처리 신고가 없습니다. 🎉' : '아직 신고가 없습니다.'}</p>
        ) : (
          shown.map((r) => (
            <details key={r.slug} className="report">
              <summary>
                {r.status === 'RESOLVED' ? '✅' : '🐞'} #{String(r.number).padStart(4, '0')} ·{' '}
                {formatDateTime(r.created_at)} ·{' '}
                {r.message.split('\n')[0].slice(0, 70)}
              </summary>
              <div className="report-body">
                <div className="small muted">
                  화면 {r.section}
                  {r.document_id ? ` · 문서 #${r.document_id}` : ''}
                </div>
                <p style={{ whiteSpace: 'pre-wrap', margin: '8px 0' }}>{r.message}</p>

                {r.images.length > 0 && (
                  <div className="thumbs" style={{ justifyContent: 'flex-start' }}>
                    {r.images.map((name) => (
                      <a
                        key={name}
                        href={`/api/reports/${r.slug}/images/${name}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <img
                          src={`/api/reports/${r.slug}/images/${name}`}
                          alt={name}
                          style={{ height: 110, borderRadius: 6, border: '1px solid var(--line)' }}
                        />
                      </a>
                    ))}
                  </div>
                )}

                {r.exception && (
                  <details className="raw" style={{ marginTop: 8 }}>
                    <summary>첨부된 예외 스택</summary>
                    <pre>{r.exception}</pre>
                  </details>
                )}

                <div className="small muted" style={{ margin: '8px 0' }}>
                  파일 위치 <code>data/reports/{r.slug}/report.md</code>
                </div>

                <div className="row">
                  {r.status === 'RESOLVED' ? (
                    <button className="btn" onClick={() => setStatus(r.slug, 'OPEN')}>다시 열기</button>
                  ) : (
                    <button className="btn" onClick={() => setStatus(r.slug, 'RESOLVED')}>처리 완료로 표시</button>
                  )}
                  <button className="btn danger" onClick={() => remove(r.slug)}>삭제</button>
                </div>
              </div>
            </details>
          ))
        )}
      </div>
    </>
  )
}

export { ReportForm }
