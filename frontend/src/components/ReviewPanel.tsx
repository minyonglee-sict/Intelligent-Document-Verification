import { useState } from 'react'
import { api } from '../api'
import { formatDateTime } from '../format'
import type { DocumentSummary } from '../types'
import { DocumentDetailPanel } from './DocumentDetail'

/**
 * 검수 화면. Streamlit ui_review 와 같이 두 구역으로 나눈다.
 *
 *   검수 대기 (검증 오류)  status=ERROR    값을 고쳐야 승인할 수 있다
 *   승인 대기 (검증 통과)  status=PENDING  고칠 것이 없으니 확인 후 승인만
 *
 * 둘 다 최종 상태는 VALIDATED 이고, 전환은 사람의 승인으로만 일어난다. 한 목록으로
 * 합치면 '고쳐야 하는 건'과 '그냥 넘기면 되는 건'이 섞여, 검수자가 매번 열어 봐야
 * 어느 쪽인지 알 수 있다.
 */

function Section({
  title,
  caption,
  rows,
  emptyText,
  selected,
  onSelect,
  onChanged,
  onApproved,
  bulkApprove,
}: {
  title: string
  caption: string
  rows: DocumentSummary[]
  emptyText: string
  selected: number | null
  onSelect: (id: number | null) => void
  onChanged: () => void
  onApproved: (message: string) => void
  bulkApprove?: () => void
}) {
  if (rows.length === 0) {
    return (
      <div className="panel">
        <h2>{title}</h2>
        <div className="notice ok">{emptyText}</div>
      </div>
    )
  }

  return (
    <div className="panel">
      <h2>{title}</h2>
      <p className="muted small" style={{ margin: '0 0 .8rem' }}>{caption}</p>

      {bulkApprove && (
        <div className="row" style={{ marginBottom: '.8rem' }}>
          <button className="btn primary" onClick={bulkApprove}>
            전체 승인 → VALIDATED ({rows.length}건)
          </button>
        </div>
      )}

      <table>
        <thead>
          <tr>
            <th className="num" style={{ width: 56 }}>ID</th>
            <th>파일명</th>
            <th style={{ width: 120 }}>상태</th>
            <th className="num" style={{ width: 60 }}>오류</th>
            <th className="num" style={{ width: 60 }}>쪽</th>
            <th style={{ width: 150 }}>등록</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.id}
              className={`clickable ${selected === row.id ? 'selected' : ''}`}
              onClick={() => onSelect(selected === row.id ? null : row.id)}
            >
              <td className="num">{row.id}</td>
              <td>{row.filename}</td>
              <td><span className={`badge ${row.status}`}>{row.status_label}</span></td>
              <td className="num">{row.error_count || ''}</td>
              <td className="num">{row.page_count ?? ''}</td>
              <td className="small muted">{formatDateTime(row.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small" style={{ margin: '.4rem 0 0' }}>
        행을 누르면 아래에 상세가 열립니다. 한 번 더 누르면 닫힙니다.
      </p>

      {selected !== null && rows.some((r) => r.id === selected) && (
        <div style={{ marginTop: '1rem' }}>
          <DocumentDetailPanel
            documentId={selected}
            onChanged={onChanged}
            onApproved={(id) => onApproved(`문서 #${id} 를 승인했습니다. 검수 목록에서 내렸습니다.`)}
          />
        </div>
      )}
    </div>
  )
}

interface Props {
  rows: DocumentSummary[]
  onChanged: () => void
  onNotice: (message: string) => void
}

export function ReviewPanel({ rows, onChanged, onNotice }: Props) {
  const [selected, setSelected] = useState<number | null>(null)
  const errorRows = rows.filter((r) => r.status === 'ERROR')
  const pendingRows = rows.filter((r) => r.status === 'PENDING')

  const approveAll = async () => {
    const ids = pendingRows.map((r) => r.id)
    if (!confirm(`검증을 통과한 ${ids.length}건을 한 번에 승인합니다.`)) return
    const result = await api.bulkApprove(ids)
    onNotice(`${result.approved}건을 승인했습니다.`)
    setSelected(null)
    onChanged()
  }

  return (
    <>
      <Section
        title="검수 대기 (검증 오류)"
        caption={`${errorRows.length}건의 문서에 검증 오류가 있습니다. 값을 확인·수정한 뒤 승인하세요.`}
        rows={errorRows}
        emptyText="검증 오류 상태의 문서가 없습니다. 🎉"
        selected={selected}
        onSelect={setSelected}
        onChanged={onChanged}
        onApproved={onNotice}
      />

      <Section
        title="승인 대기 (검증 통과)"
        caption={`${pendingRows.length}건이 검증을 통과했습니다. 고칠 것이 없다면 그대로 승인하면 됩니다.`}
        rows={pendingRows}
        emptyText="승인을 기다리는 문서가 없습니다."
        selected={selected}
        onSelect={setSelected}
        onChanged={onChanged}
        onApproved={onNotice}
        bulkApprove={approveAll}
      />
    </>
  )
}
