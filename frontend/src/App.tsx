import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { DocumentDetailPanel } from './components/DocumentDetail'
import { UploadPanel } from './components/UploadPanel'
import type { DocumentSummary } from './types'
import './styles.css'

type Tab = 'upload' | 'review' | 'documents'

const STATUS_FILTERS = ['(전체)', 'ERROR', 'PENDING', 'VALIDATED', 'FAILED'] as const

function DocumentTable({
  rows,
  selected,
  onSelect,
}: {
  rows: DocumentSummary[]
  selected: number | null
  onSelect: (id: number) => void
}) {
  return (
    <table>
      <thead>
        <tr>
          <th className="num" style={{ width: 56 }}>순번</th>
          <th className="num" style={{ width: 56 }}>ID</th>
          <th>파일명</th>
          <th style={{ width: 120 }}>상태</th>
          <th className="num" style={{ width: 60 }}>오류</th>
          <th className="num" style={{ width: 60 }}>쪽</th>
          <th style={{ width: 160 }}>등록</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr
            key={row.id}
            className={`clickable ${selected === row.id ? 'selected' : ''}`}
            onClick={() => onSelect(row.id)}
          >
            <td className="num muted">{index + 1}</td>
            <td className="num">{row.id}</td>
            <td>{row.filename}</td>
            <td><span className={`badge ${row.status}`}>{row.status_label}</span></td>
            <td className="num">{row.error_count || ''}</td>
            <td className="num">{row.page_count ?? ''}</td>
            <td className="small muted">{(row.created_at ?? '').slice(0, 16).replace('T', ' ')}</td>
          </tr>
        ))}
        {rows.length === 0 && (
          <tr><td colSpan={7} className="muted small">해당하는 문서가 없습니다.</td></tr>
        )}
      </tbody>
    </table>
  )
}

export default function App() {
  const [tab, setTab] = useState<Tab>('review')
  const [rows, setRows] = useState<DocumentSummary[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [filter, setFilter] = useState<string>('(전체)')
  const [selected, setSelected] = useState<number | null>(null)
  const [health, setHealth] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    const [all, c] = await Promise.all([api.listDocuments(), api.counts()])
    setRows(all)
    setCounts(c)
    setSelected((current) => (current && all.some((r) => r.id === current) ? current : null))
  }, [])

  useEffect(() => {
    refresh().catch((e) => setHealth(e instanceof Error ? e.message : String(e)))
  }, [refresh])

  // 검수 대상은 오류 건과 승인 대기 건. 둘 다 사람 손이 필요하다.
  const reviewRows = rows.filter((r) => r.status === 'ERROR' || r.status === 'PENDING')
  const openCount = reviewRows.length

  const listed =
    tab === 'review'
      ? reviewRows
      : filter === '(전체)'
        ? rows
        : rows.filter((r) => r.status === filter)

  return (
    <div className="shell">
      <header>
        <h1>Intelligent Document Verification</h1>
        <p className="small">
          Docling 추출 → 필드 추출 → 규칙 검증 → MS-SQL 저장 → 사람이 검수·승인
        </p>
      </header>

      {health && <div className="notice err">백엔드에 연결하지 못했습니다: {health}</div>}

      <nav className="tabs">
        <button className={tab === 'upload' ? 'active' : ''} onClick={() => setTab('upload')}>
          업로드
        </button>
        <button className={tab === 'review' ? 'active' : ''} onClick={() => setTab('review')}>
          검수{openCount > 0 && <span className="count">{openCount}</span>}
        </button>
        <button className={tab === 'documents' ? 'active' : ''} onClick={() => setTab('documents')}>
          전체 문서
        </button>
      </nav>

      {tab === 'upload' && <UploadPanel onFinished={refresh} />}

      {tab !== 'upload' && (
        <div className="panel">
          <div className="row" style={{ justifyContent: 'space-between', marginBottom: 10 }}>
            <h2 style={{ margin: 0 }}>
              {tab === 'review' ? '검수 대기' : '전체 문서'}
              <span className="muted small" style={{ fontWeight: 400, marginLeft: 8 }}>
                총 {listed.length}건
              </span>
            </h2>
            {tab === 'documents' && (
              <select
                style={{ width: 160 }}
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              >
                {STATUS_FILTERS.map((s) => (
                  <option key={s} value={s}>
                    {s === '(전체)' ? s : `${s} (${counts[s] ?? 0})`}
                  </option>
                ))}
              </select>
            )}
          </div>
          <DocumentTable rows={listed} selected={selected} onSelect={setSelected} />
          <p className="muted small" style={{ marginBottom: 0 }}>
            행을 누르면 아래에 상세가 열립니다.
          </p>
        </div>
      )}

      {tab !== 'upload' && selected !== null && (
        <DocumentDetailPanel documentId={selected} onChanged={refresh} />
      )}
    </div>
  )
}
