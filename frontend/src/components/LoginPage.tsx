import { useState } from 'react'
import { api, setToken } from '../api'
import type { AuthUser } from '../types'

/**
 * 로그인 + 가입 화면. 계정은 두 갈래로 생긴다: 관리자가 create_user.py 로
 * 직접 만들거나, 누구나 여기서 스스로 만든다. 가입으로 만든 계정은 항상
 * role='user' 다 -- 관리자는 여기서 스스로 고를 수 없다(사용자 관리 화면 참고).
 *
 * 로그인이든 가입이든 성공하면 토큰을 저장하고 onLoggedIn 을 부른다.
 */

interface Props {
  onLoggedIn: (user: AuthUser) => void
}

type Mode = 'login' | 'signup'

export function LoginPage({ onLoggedIn }: Props) {
  const [mode, setMode] = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [showForgot, setShowForgot] = useState(false)
  const [forgotBusy, setForgotBusy] = useState(false)
  const [forgotResult, setForgotResult] = useState<{ ok: boolean; text: string } | null>(null)

  // 요청할 아이디는 위쪽 로그인 폼에 입력된 값을 그대로 쓴다(수정 불가) --
  // 여기서 임의로 다른 아이디를 넣어 남의 계정 재설정을 요청할 수 있으면 안 된다.
  const FORGOT_MESSAGE = '비밀번호 변경 요청'

  const switchMode = (next: Mode) => {
    setMode(next)
    setError(null)
    setShowForgot(false)
  }

  const openForgot = () => {
    setShowForgot((v) => !v)
    setForgotResult(null)
  }

  const submitForgot = async () => {
    if (!username.trim()) return
    setForgotBusy(true)
    setForgotResult(null)
    try {
      await api.forgotPassword(username.trim(), FORGOT_MESSAGE)
      setForgotResult({ ok: true, text: '관리자에게 재설정 요청을 보냈습니다. 확인 후 연락드릴 거예요.' })
    } catch (err) {
      setForgotResult({ ok: false, text: err instanceof Error ? err.message : String(err) })
    } finally {
      setForgotBusy(false)
    }
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const result =
        mode === 'login'
          ? await api.login(username.trim(), password)
          : await api.signup({ username: username.trim(), displayName: displayName.trim(), password })
      setToken(result.token)
      onLoggedIn({ username: result.username, display_name: result.display_name, role: result.role })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const canSubmit =
    mode === 'login'
      ? username.trim() && password
      : username.trim() && displayName.trim() && password

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">
          <div className="sidebar-brand-name">DOCVERIFY</div>
          <div className="sidebar-brand-sub">송장·영수증 검증 시스템</div>
        </div>

        <label className="field">
          <span>아이디</span>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
            autoComplete="username"
          />
        </label>

        {mode === 'signup' && (
          <label className="field">
            <span>이름</span>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="화면에 보일 이름 (예: 김검수(재무팀))"
            />
          </label>
        )}

        <label className="field">
          <span>비밀번호</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
          />
        </label>

        {mode === 'login' && (
          <p className="small" style={{ margin: '-.4rem 0 .8rem' }}>
            <button type="button" className="login-link" onClick={openForgot}>
              비밀번호를 잊으셨나요?
            </button>
          </p>
        )}

        {showForgot && (
          <div className="notice warn small">
            <p style={{ margin: '0 0 .6rem' }}>
              위 아이디로 재설정을 요청하면 관리자에게 전달됩니다. 확인되는 대로
              새 비밀번호를 안내해 드립니다.
              {!username.trim() && ' 먼저 위 아이디 칸에 아이디를 입력하세요.'}
            </p>

            <label className="field">
              <span>아이디</span>
              <input type="text" value={username.trim()} readOnly disabled />
            </label>
            <label className="field">
              <span>메세지</span>
              <input type="text" value={FORGOT_MESSAGE} readOnly disabled />
            </label>

            {forgotResult && (
              <div className={`notice ${forgotResult.ok ? 'ok' : 'err'} small`}>
                {forgotResult.text}
              </div>
            )}

            <button
              type="button"
              className="btn primary"
              onClick={submitForgot}
              disabled={forgotBusy || !username.trim()}
            >
              {forgotBusy ? '보내는 중…' : '요청 보내기'}
            </button>
          </div>
        )}

        {error && <div className="notice err">{error}</div>}

        <button className="btn primary" type="submit" disabled={busy || !canSubmit}>
          {busy ? '확인 중…' : mode === 'login' ? '로그인' : '가입'}
        </button>

        <p className="muted small login-footnote">
          {mode === 'login' ? (
            <>계정이 없나요? <button type="button" className="login-link" onClick={() => switchMode('signup')}>가입하기</button></>
          ) : (
            <>이미 계정이 있나요? <button type="button" className="login-link" onClick={() => switchMode('login')}>로그인</button></>
          )}
        </p>
      </form>
    </div>
  )
}
