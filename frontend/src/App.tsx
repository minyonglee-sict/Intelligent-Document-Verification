import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { DocumentsPanel } from './components/DocumentsPanel'
import { ReportPanel } from './components/ReportPanel'
import { ReviewPanel } from './components/ReviewPanel'
import { Sidebar } from './components/Sidebar'
import { UploadPanel } from './components/UploadPanel'
import type { DocumentSummary } from './types'
import './styles.css'

type Tab = 'upload' | 'review' | 'documents' | 'reports'

export default function App() {
  const [tab, setTab] = useState<Tab>('upload')
  const [rows, setRows] = useState<DocumentSummary[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [openReports, setOpenReports] = useState(0)
  const [env, setEnv] = useState<{ database: string; ollama: string } | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [flash, setFlash] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [all, c, rc] = await Promise.all([
        api.listDocuments(),
        api.counts(),
        api.reportCounts().catch(() => ({ open: 0, total: 0 })),
      ])
      setRows(all)
      setCounts(c)
      setOpenReports(rc.open)
      setFailure(null)
    } catch (e) {
      setFailure(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    refresh()
    api.health().then(setEnv).catch(() => setEnv(null))
  }, [refresh])

  // 검수 대상은 오류 건과 승인 대기 건. 둘 다 사람 손이 필요하다.
  const reviewRows = rows.filter((r) => r.status === 'ERROR' || r.status === 'PENDING')
  const openCount = reviewRows.length

  const TABS: { key: Tab; label: string; badge?: number }[] = [
    { key: 'upload', label: '📥 업로드' },
    { key: 'review', label: '🔍 검수', badge: openCount },
    { key: 'documents', label: '🗂️ 전체 문서' },
    { key: 'reports', label: '🐞 오류 신고', badge: openReports },
  ]

  return (
    <div className="layout">
      <Sidebar counts={counts} env={env} />

      <main className="content">
        <header>
          <h1>📄 Intelligent Document Verification</h1>
          <p className="small">
            Docling 추출 → Ollama 필드 추출 → 규칙 검증 → MS-SQL 저장 → 사람이 검수·승인
          </p>
        </header>

        {failure && <div className="notice err">백엔드에 연결하지 못했습니다: {failure}</div>}
        {flash && <div className="notice ok">{flash}</div>}

        <nav className="tabs">
          {TABS.map((t) => (
            <button
              key={t.key}
              className={tab === t.key ? 'active' : ''}
              onClick={() => { setTab(t.key); setFlash(null) }}
            >
              {t.label}
              {t.badge ? <span className="count">{t.badge}</span> : null}
            </button>
          ))}
        </nav>

        {tab === 'upload' && <UploadPanel onFinished={refresh} />}
        {tab === 'reports' && <ReportPanel />}

        {tab === 'review' && (
          <ReviewPanel rows={rows} onChanged={refresh} onNotice={setFlash} />
        )}

        {tab === 'documents' && (
          <DocumentsPanel
            rows={rows}
            counts={counts}
            onChanged={refresh}
            onNotice={setFlash}
          />
        )}

      </main>
    </div>
  )
}
