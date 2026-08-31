import { useEffect, useState } from 'react'
import { api, downloadFile } from '../api'
import type { DocumentSummary } from '../types'
import { DocumentInspector } from './DocumentInspector'

/**
 * 전체 문서 화면. Streamlit ui_documents 와 같은 구성이다.
 *
 * 표에서 체크박스로 여러 건을 골라 한 번에 지울 수 있다. 되돌릴 수 없는 동작이라
 * 지우기 전에 대상을 ID·파일명으로 나열해 확인받는다 -- 브라우저 기본 confirm 은
 * 목록이 길면 읽기 어려워서, 화면 안에 펼쳐 보여준다.
 */

const STATUS_FILTERS = ['(전체)', 'ERROR', 'PENDING', 'VALIDATED', 'FAILED'] as const
const DOC_TYPE_FILTERS = ['(전체)', 'INVOICE', 'RECEIPT', 'UNKNOWN'] as const
// app/validator.py 의 DOC_TYPE_LABELS 와 같은 이름을 쓴다.
const DOC_TYPE_NAMES: Record<string, string> = { INVOICE: '송장', RECEIPT: '영수증', UNKNOWN: '문서' }
const PAGE_SIZE = 10

/** 페이지 버튼 목록을 만든다. 페이지가 많으면 가운데를 "..."로 줄인다.
 *  예: 26쪽 중 5쪽 -> [1, '…', 4, 5, 6, '…', 26] */
function pageNumbers(current: number, total: number): (number | '…')[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const out: (number | '…')[] = [1]
  if (current > 3) out.push('…')
  for (let p = Math.max(2, current - 1); p <= Math.min(total - 1, current + 1); p++) out.push(p)
  if (current < total - 2) out.push('…')
  out.push(total)
  return out
}

interface Props {
  rows: DocumentSummary[]
  counts: Record<string, number>
  onChanged: () => void
  onNotice: (message: string) => void
}

export function DocumentsPanel({ rows, counts, onChanged, onNotice }: Props) {
  // 조회 조건이 둘(유형·상태)이라, 드롭다운을 바꾸는 즉시 걸리지 않고 "조회"를
  // 눌러야 적용된다 -- draft* 는 지금 드롭다운에 골라둔 값, filter/docTypeFilter
  // 는 실제로 표를 거르는 데 쓰는 값이다.
  const [draftFilter, setDraftFilter] = useState<string>('(전체)')
  const [draftDocTypeFilter, setDraftDocTypeFilter] = useState<string>('(전체)')
  const [filter, setFilter] = useState<string>('(전체)')
  const [docTypeFilter, setDocTypeFilter] = useState<string>('(전체)')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<number | null>(null)
  const [checked, setChecked] = useState<number[]>([])
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [downloadingId, setDownloadingId] = useState<number | null>(null)

  const listed = rows
    .filter((r) => filter === '(전체)' || r.status === filter)
    .filter((r) => docTypeFilter === '(전체)' || r.doc_type === docTypeFilter)
  const pageCount = Math.max(1, Math.ceil(listed.length / PAGE_SIZE))

  // 필터를 바꾸면 1쪽으로. 삭제 등으로 목록이 줄어 지금 쪽이 없어지면 마지막 쪽으로 당긴다.
  useEffect(() => { setPage(1) }, [filter, docTypeFilter])
  useEffect(() => { setPage((p) => Math.min(p, pageCount)) }, [pageCount])

  const pageStart = (page - 1) * PAGE_SIZE
  const pageRows = listed.slice(pageStart, pageStart + PAGE_SIZE)

  // 필터·페이지가 바뀌어 화면에 없는 문서가 선택된 채로 남지 않게 한다.
  useEffect(() => {
    setChecked((prev) => prev.filter((id) => listed.some((r) => r.id === id)))
  }, [listed])

  // "모두 선택"은 지금 페이지에 보이는 10건 기준이다 -- 화면 밖 250건까지 조용히
  // 딸려 들어가면, 체크박스 하나 잘못 눌러서 대량 삭제로 이어질 수 있다.
  const allChecked = pageRows.length > 0 && pageRows.every((r) => checked.includes(r.id))
  const targets = listed.filter((r) => checked.includes(r.id))

  const toggle = (id: number) =>
    setChecked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))

  const handleDownload = async (row: DocumentSummary) => {
    setDownloadingId(row.id)
    try {
      await downloadFile(row.id, row.filename)
    } catch {
      onNotice(`#${row.id} 파일을 받지 못했습니다.`)
    } finally {
      setDownloadingId(null)
    }
  }

  const deleteChecked = async () => {
    setBusy(true)
    try {
      const result = await api.bulkDelete(targets.map((r) => r.id))
      onNotice(`${result.deleted}건을 삭제했습니다.`)
      setChecked([])
      setConfirming(false)
      setSelected(null)
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="panel">
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: '1rem' }}>
          <h2 style={{ margin: 0 }}>
            전체 문서
            <span className="muted small" style={{ fontWeight: 400, marginLeft: '.5rem' }}>
              총 {listed.length}건
            </span>
          </h2>
          <div className="row">
            <select
              style={{ width: 150 }}
              value={draftDocTypeFilter}
              onChange={(e) => setDraftDocTypeFilter(e.target.value)}
              title="문서 유형"
            >
              {DOC_TYPE_FILTERS.map((t) => (
                <option key={t} value={t}>
                  {t === '(전체)' ? t : `${DOC_TYPE_NAMES[t]} (${rows.filter((r) => r.doc_type === t).length})`}
                </option>
              ))}
            </select>
            <select style={{ width: 180 }} value={draftFilter} onChange={(e) => setDraftFilter(e.target.value)}>
              {STATUS_FILTERS.map((s) => (
                <option key={s} value={s}>
                  {s === '(전체)' ? s : `${s} (${counts[s] ?? 0})`}
                </option>
              ))}
            </select>
            <button
              className="btn primary"
              onClick={() => { setFilter(draftFilter); setDocTypeFilter(draftDocTypeFilter) }}
            >
              조회
            </button>
          </div>
        </div>

        {/* 지금 실제로 계산 가능한 값만 카드로 보여준다 -- 금액 합계·평균 처리 시간은
            문서 목록 API에 그 데이터 자체가 없어서, 지어내지 않고 아예 뺐다. */}
        <div className="metrics">
          <div className="metric"><div className="label">전체 문서</div><div className="value">{rows.length}</div></div>
          <div className="metric"><div className="label">오류</div><div className="value">{counts['ERROR'] ?? 0}</div></div>
          <div className="metric"><div className="label">승인 완료</div><div className="value">{counts['VALIDATED'] ?? 0}</div></div>
          <div className="metric"><div className="label">검수 대기</div><div className="value">{(counts['ERROR'] ?? 0) + (counts['PENDING'] ?? 0)}</div></div>
        </div>

        {checked.length === 0 ? (
          <p className="muted small" style={{ margin: '0 0 .5rem' }}>
            삭제하려면 왼쪽 체크박스로 문서를 고르세요.
          </p>
        ) : (
          <div className="row" style={{ marginBottom: '.6rem' }}>
            <button className="btn danger" onClick={() => setConfirming((v) => !v)}>
              🗑️ 선택한 {checked.length}건 삭제
            </button>
            <button className="btn" onClick={() => setChecked([])}>선택 해제</button>
          </div>
        )}

        {confirming && targets.length > 0 && (
          <div className="notice err" style={{ marginBottom: '.8rem' }}>
            <p style={{ margin: '0 0 .4rem' }}>
              <strong>다음 문서를 삭제합니다. 되돌릴 수 없습니다.</strong>
            </p>
            <ul style={{ margin: '0 0 .5rem', paddingLeft: '1.2rem' }}>
              {targets.map((r) => (
                <li key={r.id}><code>#{r.id}</code> {r.filename}</li>
              ))}
            </ul>
            <p className="small" style={{ margin: '0 0 .6rem' }}>
              문서·품목·검증 오류 기록과 <code>data/uploads</code> 의 원본 파일이 함께 지워집니다.
              같은 파일을 다시 올리면 새로 처리됩니다.
            </p>
            <div className="row">
              <button className="btn primary" onClick={deleteChecked} disabled={busy}>
                삭제
              </button>
              <button className="btn" onClick={() => setConfirming(false)} disabled={busy}>
                취소
              </button>
            </div>
          </div>
        )}

        <table className="doc-table">
          <thead>
            <tr>
              <th style={{ width: 34 }}>
                <input
                  type="checkbox"
                  style={{ width: 'auto' }}
                  checked={allChecked}
                  onChange={() => {
                    const pageIds = pageRows.map((r) => r.id)
                    setChecked((prev) =>
                      allChecked ? prev.filter((id) => !pageIds.includes(id)) : [...new Set([...prev, ...pageIds])],
                    )
                  }}
                  title="이 페이지 모두 선택"
                />
              </th>
              <th className="num" style={{ width: 50 }}>순번</th>
              <th className="num" style={{ width: 50 }}>ID</th>
              <th style={{ width: 260 }}>파일명</th>
              <th style={{ width: 64 }}>유형</th>
              <th style={{ width: 100 }}>상태</th>
              <th className="num" style={{ width: 50 }}>오류</th>
              <th className="num" style={{ width: 50 }}>쪽</th>
              <th style={{ width: 130 }}>등록</th>
              <th style={{ width: 60 }}>파일</th>
            </tr>
          </thead>
          <tbody>
            {pageRows.map((row, index) => (
              <tr
                key={row.id}
                className={`clickable ${selected === row.id ? 'selected' : ''}`}
                onClick={() => setSelected(selected === row.id ? null : row.id)}
              >
                <td onClick={(e) => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    style={{ width: 'auto' }}
                    checked={checked.includes(row.id)}
                    onChange={() => toggle(row.id)}
                  />
                </td>
                <td className="num muted">{pageStart + index + 1}</td>
                <td className="num">{row.id}</td>
                <td className="ellipsis" title={row.filename}>{row.filename}</td>
                <td className="small muted">{row.doc_type_label}</td>
                <td><span className={`badge ${row.status}`}>{row.status_label}</span></td>
                <td className="num">{row.error_count || ''}</td>
                <td className="num">{row.page_count ?? ''}</td>
                <td className="small muted">{(row.created_at ?? '').slice(0, 16).replace('T', ' ')}</td>
                <td onClick={(e) => e.stopPropagation()}>
                  <button
                    className="btn"
                    title={`${row.filename} 다운로드`}
                    disabled={downloadingId === row.id}
                    onClick={() => handleDownload(row)}
                  >
                    {downloadingId === row.id ? '…' : '⬇️'}
                  </button>
                </td>
              </tr>
            ))}
            {listed.length === 0 && (
              <tr><td colSpan={10} className="muted small">해당하는 문서가 없습니다.</td></tr>
            )}
          </tbody>
        </table>

        {listed.length > 0 && (
          <div className="pager">
            <span className="muted small">
              {pageStart + 1}–{Math.min(pageStart + PAGE_SIZE, listed.length)} / {listed.length}건
            </span>
            <div className="pager-buttons">
              <button className="btn" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>이전</button>
              {pageNumbers(page, pageCount).map((p, i) =>
                p === '…' ? (
                  <span key={`e${i}`} className="pager-ellipsis">…</span>
                ) : (
                  <button
                    key={p}
                    className={`btn ${p === page ? 'primary' : ''}`}
                    onClick={() => setPage(p)}
                  >
                    {p}
                  </button>
                ),
              )}
              <button className="btn" disabled={page === pageCount} onClick={() => setPage((p) => p + 1)}>다음</button>
            </div>
          </div>
        )}

        <p className="muted small" style={{ margin: '.6rem 0 0' }}>
          행을 누르면 아래에 상세가 열립니다. 한 번 더 누르면 닫힙니다.
        </p>
      </div>

      {selected !== null && listed.some((r) => r.id === selected) && (
        <DocumentInspector documentId={selected} onChanged={onChanged} />
      )}
    </>
  )
}
