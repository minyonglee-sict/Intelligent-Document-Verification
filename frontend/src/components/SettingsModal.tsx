import { useEffect, useState } from 'react'
import { api } from '../api'

/**
 * 모델·DB 접속 정보와 연결 확인. 예전엔 사이드바 상단에 늘 보였지만, 이건
 * 개발·운영 진단용이지 업무 사용자가 매번 볼 정보가 아니다. 톱니바퀴 아이콘을
 * 눌렀을 때만 뜨는 모달로 옮겼다 -- Sidebar.tsx 가 이전에 하던 일이다.
 */

interface Props {
  open: boolean
  onClose: () => void
  env: { database: string; ollama: string } | null
}

export function SettingsModal({ open, onClose, env }: Props) {
  const [checked, setChecked] = useState<{ ok: boolean; text: string; which: string } | null>(null)
  const [busy, setBusy] = useState(false)

  const [currentPw, setCurrentPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [newPw2, setNewPw2] = useState('')
  const [pwNotice, setPwNotice] = useState<{ ok: boolean; text: string } | null>(null)
  const [pwBusy, setPwBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  // 화면 진입 때 이미 /health 를 부르므로, 버튼은 그 값을 다시 확인하는 용도다.
  const check = async (which: 'ollama' | 'database') => {
    setBusy(true)
    try {
      const health = await api.health()
      setChecked({ ok: health.ok, text: health[which], which })
    } catch (e) {
      setChecked({ ok: false, text: e instanceof Error ? e.message : String(e), which })
    } finally {
      setBusy(false)
    }
  }

  const dbLine = env?.database?.split('\n')[0] ?? '확인 중…'
  const modelLine = env?.ollama?.split('\n')[0] ?? '확인 중…'

  const changePassword = async (e: React.FormEvent) => {
    e.preventDefault()
    setPwNotice(null)
    if (newPw !== newPw2) {
      setPwNotice({ ok: false, text: '새 비밀번호가 서로 다릅니다.' })
      return
    }
    setPwBusy(true)
    try {
      await api.changePassword(currentPw, newPw)
      setPwNotice({ ok: true, text: '비밀번호를 바꿨습니다. 다른 기기·브라우저에 남아 있던 로그인은 모두 해제됩니다.' })
      setCurrentPw('')
      setNewPw('')
      setNewPw2('')
    } catch (err) {
      setPwNotice({ ok: false, text: err instanceof Error ? err.message : String(err) })
    } finally {
      setPwBusy(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-panel" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="환경 설정">
        <div className="modal-panel-head">
          <h2>환경</h2>
          <button className="icon-btn" onClick={onClose} aria-label="닫기" title="닫기">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        <dl className="env">
          <dt>모델</dt>
          <dd><code>{modelLine.replace(' 사용 가능', '')}</code></dd>
          <dt>DB</dt>
          <dd><code>{dbLine.split('(')[0].trim()}</code></dd>
        </dl>

        <div className="row">
          <button className="btn" onClick={() => check('ollama')} disabled={busy}>
            Ollama 연결 확인
          </button>
          <button className="btn" onClick={() => check('database')} disabled={busy}>
            DB 연결 확인
          </button>
        </div>

        {checked && (
          <div className={`notice ${checked.ok ? 'ok' : 'err'} small`} style={{ marginTop: 12, marginBottom: 0 }}>
            {checked.text}
          </div>
        )}

        <hr style={{ margin: '1.4rem 0', border: 'none', borderTop: '1px solid var(--line)' }} />

        <h2 style={{ fontSize: '1rem', margin: '0 0 .8rem' }}>비밀번호 변경</h2>
        <form onSubmit={changePassword}>
          <label className="field">
            <span>현재 비밀번호</span>
            <input
              type="password"
              value={currentPw}
              onChange={(e) => setCurrentPw(e.target.value)}
              autoComplete="current-password"
            />
          </label>
          <label className="field">
            <span>새 비밀번호</span>
            <input
              type="password"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              autoComplete="new-password"
            />
          </label>
          <label className="field">
            <span>새 비밀번호 확인</span>
            <input
              type="password"
              value={newPw2}
              onChange={(e) => setNewPw2(e.target.value)}
              autoComplete="new-password"
            />
          </label>

          {pwNotice && (
            <div className={`notice ${pwNotice.ok ? 'ok' : 'err'} small`}>{pwNotice.text}</div>
          )}

          <button
            className="btn primary"
            type="submit"
            disabled={pwBusy || !currentPw || !newPw || !newPw2}
          >
            {pwBusy ? '변경 중…' : '비밀번호 변경'}
          </button>
        </form>
      </div>
    </div>
  )
}
