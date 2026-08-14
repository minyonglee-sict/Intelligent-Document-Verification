import { useState } from 'react'
import { api } from '../api'

/**
 * 왼쪽 사이드바. Streamlit main.py 의 sidebar() 와 같은 구성이다.
 *   ⚙️ 환경  — 모델·호스트·DB + 연결 확인 버튼
 *   📊 현황  — 상태별 문서 수
 */

const STATUS_ROWS = [
  { key: 'ERROR', dot: '🔴', label: '오류' },
  { key: 'PENDING', dot: '🟡', label: '검증 통과' },
  { key: 'VALIDATED', dot: '🟢', label: '승인 완료' },
  { key: 'FAILED', dot: '⚫', label: '처리 실패' },
] as const

interface Props {
  counts: Record<string, number>
  env: { database: string; ollama: string } | null
}

export function Sidebar({ counts, env }: Props) {
  const [checked, setChecked] = useState<{ ok: boolean; text: string; which: string } | null>(null)
  const [busy, setBusy] = useState(false)

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

  return (
    <aside className="sidebar">
      {/* 배경은 화면 아래까지 이어지게 두고, 안쪽 내용만 스크롤을 따라온다. */}
      <div className="sidebar-inner">
      <h2>⚙️ 환경</h2>
      <dl className="env">
        <dt>모델</dt>
        <dd><code>{modelLine.replace(' 사용 가능', '')}</code></dd>
        <dt>DB</dt>
        <dd><code>{dbLine.split('(')[0].trim()}</code></dd>
      </dl>

      <button className="btn wide" onClick={() => check('ollama')} disabled={busy}>
        Ollama 연결 확인
      </button>
      <button className="btn wide" onClick={() => check('database')} disabled={busy}>
        DB 연결 확인
      </button>

      {checked && (
        <div className={`notice ${checked.ok ? 'ok' : 'err'} small`} style={{ marginTop: 10 }}>
          {checked.text}
        </div>
      )}

      <hr />

      <h2>📊 현황</h2>
      {STATUS_ROWS.map((row) => (
        <div className="stat" key={row.key}>
          <div className="label">{row.dot} {row.label}</div>
          <div className="value">{counts[row.key] ?? 0}</div>
        </div>
      ))}
      </div>
    </aside>
  )
}
