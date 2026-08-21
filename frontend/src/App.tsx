import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { ChatPanel } from './components/ChatPanel'
import { DocumentsPanel } from './components/DocumentsPanel'
import { ReportPanel } from './components/ReportPanel'
import { ReviewPanel } from './components/ReviewPanel'
import { SettingsModal } from './components/SettingsModal'
import { Sidebar } from './components/Sidebar'
import { StepIndicator } from './components/StepIndicator'
import { UploadPanel } from './components/UploadPanel'
import type { DocumentSummary, Job } from './types'
import './styles.css'

type Tab = 'upload' | 'review' | 'documents' | 'reports' | 'chat'

export default function App() {
  const [tab, setTab] = useState<Tab>('upload')
  const [rows, setRows] = useState<DocumentSummary[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  // 업로드 탭을 벗어나도 처리 중 목록이 안 사라지게 여기서 들고 있는다 -- 탭 전환은
  // <UploadPanel/> 을 통째로 언마운트시키므로, 그 안에 두면 다시 들어올 때 비어 버린다.
  const [jobs, setJobs] = useState<Job[]>([])
  const [openReports, setOpenReports] = useState(0)
  const [env, setEnv] = useState<{ database: string; ollama: string } | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)

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
    { key: 'chat', label: '💬 물어보기' },
  ]

  return (
    <div className="layout">
      <Sidebar counts={counts} />

      <main className="content">
        <header>
          <div className="header-top">
            <div>
              <h1>Intelligent Document Verification</h1>
              <p className="subtitle">송장·영수증을 업로드하면 아래 단계로 자동 처리됩니다.</p>
            </div>
            <button className="icon-btn" onClick={() => setSettingsOpen(true)} aria-label="환경 설정" title="환경 설정">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
            </button>
          </div>
          <StepIndicator />
        </header>

        <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} env={env} />

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

        {tab === 'upload' && <UploadPanel jobs={jobs} setJobs={setJobs} onFinished={refresh} />}
        {tab === 'reports' && <ReportPanel onChanged={refresh} />}
        {tab === 'chat' && <ChatPanel onChanged={refresh} />}

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
