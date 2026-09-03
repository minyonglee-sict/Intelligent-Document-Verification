import { useCallback, useEffect, useState } from 'react'
import { api, getToken, onUnauthorized, setToken } from './api'
import { ChatPanel } from './components/ChatPanel'
import { DocumentsPanel } from './components/DocumentsPanel'
import { LoginPage } from './components/LoginPage'
import { ReportPanel } from './components/ReportPanel'
import { ReviewPanel } from './components/ReviewPanel'
import { SettingsModal } from './components/SettingsModal'
import { Sidebar } from './components/Sidebar'
import { StepIndicator } from './components/StepIndicator'
import { UploadPanel } from './components/UploadPanel'
import { UserAdminPanel } from './components/UserAdminPanel'
import type { AuthUser, DocumentSummary, Job } from './types'
import './styles.css'

type Tab = 'upload' | 'review' | 'documents' | 'reports' | 'chat' | 'users'

export default function App() {
  // undefined = 토큰이 있는지 아직 확인 중, null = 로그인 안 됨, 값 있음 = 로그인됨.
  // undefined 단계를 따로 두는 이유는, 저장된 토큰이 있는데도 확인 전에 로그인
  // 화면을 잠깐 보여줬다 다시 본 화면으로 바뀌는 깜빡임을 막기 위해서다.
  const [user, setUser] = useState<AuthUser | null | undefined>(undefined)

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

  // 저장된 토큰이 아직 유효한지 시작할 때 한 번 확인한다. 세션이 만료된 채로
  // 남아 있으면(예: 하루 지나서 다시 켠 브라우저) 조용히 로그인 화면으로 보낸다.
  useEffect(() => {
    if (!getToken()) {
      setUser(null)
      return
    }
    api.me().then(setUser).catch(() => { setToken(null); setUser(null) })
  }, [])

  // 다른 요청 도중 세션이 만료되면(토큰 유효기간 만료, 다른 곳에서 로그아웃 등)
  // api.ts 가 이 이벤트를 쏜다 -- 어디서 401 이 났든 로그인 화면으로 돌린다.
  useEffect(() => onUnauthorized(() => setUser(null)), [])

  const logout = useCallback(async () => {
    try { await api.logout() } catch { /* 토큰이 이미 무효해도 로컬은 지운다 */ }
    setToken(null)
    setUser(null)
  }, [])

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
    if (!user) return
    refresh()
    api.health().then(setEnv).catch(() => setEnv(null))
  }, [user, refresh])

  // 문서는 이제 워커가 백그라운드에서 처리한다 -- 업로드 탭을 보고 있지 않은
  // 사이에도 언제든 끝날 수 있다. 업로드 탭의 폴링(진행 중인 작업이 있을 때만
  // 3초마다)만으로는, 다른 탭에 가 있으면 그 완료를 아무도 못 잡아서 목록이
  // 낡은 채로 남는다. 그래서 탭과 무관하게 주기적으로도 갱신한다.
  useEffect(() => {
    if (!user) return
    const timer = setInterval(refresh, 15000)
    return () => clearInterval(timer)
  }, [user, refresh])

  if (user === undefined) return null
  if (user === null) return <LoginPage onLoggedIn={setUser} />

  // 검수 대상은 오류 건과 승인 대기 건. 둘 다 사람 손이 필요하다.
  const reviewRows = rows.filter((r) => r.status === 'ERROR' || r.status === 'PENDING')
  const openCount = reviewRows.length

  const TABS: { key: Tab; label: string; badge?: number }[] = [
    { key: 'upload', label: '📥 업로드' },
    { key: 'review', label: '🔍 검수', badge: openCount },
    { key: 'documents', label: '🗂️ 전체 문서' },
    { key: 'reports', label: '🐞 오류 신고', badge: openReports },
    { key: 'chat', label: '💬 물어보기' },
    // 관리자만 보인다 -- 일반 사용자한테는 탭 자체가 안 보여야, 있는 줄도 모른다.
    // (실제 접근 제한은 백엔드의 require_admin 이 한다. 이건 안 보이게만 한다.)
    ...(user.role === 'admin' ? [{ key: 'users' as const, label: '👤 사용자 관리' }] : []),
  ]

  return (
    <div className="layout">
      <Sidebar
        counts={counts}
        documentsActive={tab === 'documents'}
        onOpenDocuments={() => setTab('documents')}
        onOpenSettings={() => setSettingsOpen(true)}
        user={user}
        onLogout={logout}
      />

      <main className="content">
        <header>
          <div className="header-top">
            <div>
              <h1>Intelligent Document Verification</h1>
              <p className="subtitle">송장·영수증을 업로드하면 아래 단계로 자동 처리됩니다.</p>
            </div>
            <div className="row" style={{ gap: '.5rem' }}>
              <button className="icon-btn" onClick={() => refresh()} aria-label="새로고침" title="목록 새로고침">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 12a9 9 0 1 1-2.64-6.36" />
                  <path d="M21 4v6h-6" />
                </svg>
              </button>
              <button className="icon-btn" onClick={() => setSettingsOpen(true)} aria-label="환경 설정" title="환경 설정">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="3" />
                  <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
                </svg>
              </button>
            </div>
          </div>
          <StepIndicator />
        </header>

        <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} env={env} />

        {/* "연결 못함" 이라고 고정해서 붙이지 않는다 -- 아래 failure 문구 자체가
            이미 원인(연결 실패인지, 응답 지연인지)을 구분해서 담고 있다. 예전엔
            항상 "백엔드에 연결하지 못했습니다"를 붙였는데, Cloudflare Tunnel의
            520~530(서버는 살아있는데 응답만 늦은 경우)까지 "연결 못함"으로
            보여서 실제론 안 죽었는데 죽은 것처럼 보이는 오해가 있었다. */}
        {failure && <div className="notice err">{failure}</div>}
        {flash && <div className="notice ok">{flash}</div>}

        <nav className="tabs">
          {TABS.map((t) => (
            <button
              key={t.key}
              className={tab === t.key ? 'active' : ''}
              onClick={() => { setTab(t.key); setFlash(null); refresh() }}
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

        {tab === 'users' && <UserAdminPanel currentUsername={user.username} onNotice={setFlash} />}

      </main>
    </div>
  )
}
