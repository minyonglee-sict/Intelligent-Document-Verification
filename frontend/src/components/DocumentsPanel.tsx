import { useEffect, useState } from 'react'
import { api } from '../api'
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

interface Props {
  rows: DocumentSummary[]
  counts: Record<string, number>
  onChanged: () => void
  onNotice: (message: string) => void
}

export function DocumentsPanel({ rows, counts, onChanged, onNotice }: Props) {
  const [filter, setFilter] = useState<string>('(전체)')
  const [selected, setSelected] = useState<number | null>(null)
  const [checked, setChecked] = useState<number[]>([])
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  const listed = filter === '(전체)' ? rows : rows.filter((r) => r.status === filter)

  // 필터를 바꾸거나 목록이 줄면, 화면에 없는 문서가 선택된 채로 남지 않게 한다.
  useEffect(() => {
    setChecked((prev) => prev.filter((id) => listed.some((r) => r.id === id)))
  }, [listed])

  const allChecked = listed.length > 0 && checked.length === listed.length
  const targets = listed.filter((r) => checked.includes(r.id))

  const toggle = (id: number) =>
    setChecked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))

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
        <div className="row" style={{ justifyContent: 'space-between', marginBottom: '.5rem' }}>
          <h2 style={{ margin: 0 }}>
            전체 문서
            <span className="muted small" style={{ fontWeight: 400, marginLeft: '.5rem' }}>
              총 {listed.length}건
            </span>
          </h2>
          <select style={{ width: 180 }} value={filter} onChange={(e) => setFilter(e.target.value)}>
            {STATUS_FILTERS.map((s) => (
              <option key={s} value={s}>
                {s === '(전체)' ? s : `${s} (${counts[s] ?? 0})`}
              </option>
            ))}
          </select>
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

        <table>
          <thead>
            <tr>
              <th style={{ width: 36 }}>
                <input
                  type="checkbox"
                  style={{ width: 'auto' }}
                  checked={allChecked}
                  onChange={() => setChecked(allChecked ? [] : listed.map((r) => r.id))}
                  title="모두 선택"
                />
              </th>
              <th className="num" style={{ width: 56 }}>순번</th>
              <th className="num" style={{ width: 56 }}>ID</th>
              <th>파일명</th>
              <th style={{ width: 120 }}>상태</th>
              <th className="num" style={{ width: 60 }}>오류</th>
              <th className="num" style={{ width: 60 }}>쪽</th>
              <th style={{ width: 150 }}>등록</th>
            </tr>
          </thead>
          <tbody>
            {listed.map((row, index) => (
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
                <td className="num muted">{index + 1}</td>
                <td className="num">{row.id}</td>
                <td>{row.filename}</td>
                <td><span className={`badge ${row.status}`}>{row.status_label}</span></td>
                <td className="num">{row.error_count || ''}</td>
                <td className="num">{row.page_count ?? ''}</td>
                <td className="small muted">{(row.created_at ?? '').slice(0, 16).replace('T', ' ')}</td>
              </tr>
            ))}
            {listed.length === 0 && (
              <tr><td colSpan={8} className="muted small">해당하는 문서가 없습니다.</td></tr>
            )}
          </tbody>
        </table>
        <p className="muted small" style={{ margin: '.4rem 0 0' }}>
          행을 누르면 아래에 상세가 열립니다. 한 번 더 누르면 닫힙니다.
        </p>
      </div>

      {selected !== null && listed.some((r) => r.id === selected) && (
        <DocumentInspector documentId={selected} onChanged={onChanged} />
      )}
    </>
  )
}
