import { useEffect, useState } from 'react'
import { api } from '../api'
import type { AdminUser, Role } from '../types'

/**
 * 사용자 관리 화면. 관리자만 이 탭 자체를 본다(App.tsx 에서 role 로 가린다) --
 * 실제 접근 제한은 엔진의 require_admin 이 한다, 여기는 안 보이게만 한다.
 *
 * 역할 변경과 비밀번호 강제 재설정, 두 가지만 한다. 계정 생성·삭제는 여기 없다
 * -- 계정을 새로 만드는 건 가입 화면이나 create_user.py 의 몫이고, 삭제는 아직
 * 이 프로젝트에 그 기능 자체가 없다(만들려면 세션·문서 소유권 등 따로 생각할
 * 게 많아서 이번 범위에는 안 넣었다).
 */

interface Props {
  currentUsername: string
  onNotice: (message: string) => void
}

export function UserAdminPanel({ currentUsername, onNotice }: Props) {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)

  // 비밀번호 재설정 폼은 한 번에 한 사람만 펼친다.
  const [resetTarget, setResetTarget] = useState<AdminUser | null>(null)
  const [newPassword, setNewPassword] = useState('')
  const [newPassword2, setNewPassword2] = useState('')
  const [resetError, setResetError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      setUsers(await api.adminListUsers())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const toggleRole = async (u: AdminUser) => {
    const next: Role = u.role === 'admin' ? 'user' : 'admin'
    if (
      next === 'user' &&
      !window.confirm(`'${u.display_name}'을(를) 일반 사용자로 강등할까요?`)
    ) {
      return
    }
    setBusyId(u.id)
    try {
      const updated = await api.adminSetRole(u.id, next)
      setUsers((prev) => prev.map((x) => (x.id === u.id ? updated : x)))
      onNotice(`'${updated.display_name}'의 역할을 ${updated.role_label}(으)로 바꿨습니다.`)
    } catch (e) {
      onNotice(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId(null)
    }
  }

  const openReset = (u: AdminUser) => {
    setResetTarget(u)
    setNewPassword('')
    setNewPassword2('')
    setResetError(null)
  }

  const submitReset = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!resetTarget) return
    if (newPassword !== newPassword2) {
      setResetError('두 입력이 다릅니다.')
      return
    }
    setBusyId(resetTarget.id)
    try {
      await api.adminResetPassword(resetTarget.id, newPassword)
      onNotice(
        `'${resetTarget.display_name}'의 비밀번호를 재설정했습니다. 본인에게 안전한 방법으로 새 비밀번호를 알려주세요 -- 기존 로그인은 모두 해제됩니다.`,
      )
      setResetTarget(null)
    } catch (err) {
      setResetError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="panel">
      <h2 style={{ margin: '0 0 .3rem' }}>사용자 관리</h2>
      <p className="muted small" style={{ margin: '0 0 1rem' }}>
        역할을 바꾸거나, 비밀번호를 잊은 사람의 비밀번호를 대신 재설정할 수 있습니다.
      </p>

      {error && <div className="notice err">목록을 불러오지 못했습니다: {error}</div>}

      {!loading && !error && (
        <table>
          <thead>
            <tr>
              <th className="num" style={{ width: 50 }}>ID</th>
              <th>아이디</th>
              <th>이름</th>
              <th style={{ width: 110 }}>역할</th>
              <th style={{ width: 150 }}>가입일</th>
              <th style={{ width: 220 }}>관리</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td className="num muted">{u.id}</td>
                <td>
                  {u.username}
                  {u.username === currentUsername && <span className="muted small"> (나)</span>}
                </td>
                <td>{u.display_name}</td>
                <td>
                  <span className={`badge ${u.role === 'admin' ? 'VALIDATED' : 'PENDING'}`}>
                    {u.role_label}
                  </span>
                </td>
                <td className="small muted">{u.created_at.slice(0, 10)}</td>
                <td>
                  <div className="row">
                    <button
                      className="btn"
                      disabled={busyId === u.id}
                      onClick={() => toggleRole(u)}
                    >
                      {u.role === 'admin' ? '일반으로 강등' : '관리자로 승격'}
                    </button>
                    <button
                      className="btn"
                      disabled={busyId === u.id}
                      onClick={() => openReset(u)}
                    >
                      비밀번호 재설정
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {resetTarget && (
        <div className="notice warn" style={{ marginTop: '1rem' }}>
          <form onSubmit={submitReset}>
            <p style={{ margin: '0 0 .6rem' }}>
              <strong>'{resetTarget.display_name}'</strong>의 비밀번호를 강제로 재설정합니다.
              적용되면 그 사람의 기존 로그인은 전부 해제됩니다.
            </p>
            <label className="field">
              <span>새 비밀번호</span>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                autoFocus
              />
            </label>
            <label className="field">
              <span>새 비밀번호 확인</span>
              <input
                type="password"
                value={newPassword2}
                onChange={(e) => setNewPassword2(e.target.value)}
              />
            </label>
            {resetError && <div className="notice err small">{resetError}</div>}
            <div className="row">
              <button
                className="btn primary"
                type="submit"
                disabled={busyId === resetTarget.id || !newPassword || !newPassword2}
              >
                재설정
              </button>
              <button className="btn" type="button" onClick={() => setResetTarget(null)}>
                취소
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}
