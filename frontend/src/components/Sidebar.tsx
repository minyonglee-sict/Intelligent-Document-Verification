import type { AuthUser } from '../types'

/**
 * 왼쪽 사이드바. 상태별 문서 현황 + 실제로 존재하는 화면 두 곳(전체 문서·환경 설정)으로
 * 가는 지름길만 보여준다.
 *
 * 모델·DB 접속 정보와 연결 확인 버튼은 일반 사용자가 볼 필요가 없는 개발 정보라
 * SettingsModal(⚙️ 아이콘)로 옮겼다. "사용자 관리"는 상단 탭에 이미 있어서(관리자만
 * 보임) 여기 또 넣지 않는다 -- 같은 곳으로 가는 지름길을 두 군데 두면 헷갈린다.
 */

const STATUS_ROWS = [
  { key: 'ERROR', label: '오류' },
  { key: 'PENDING', label: '검증 통과' },
  { key: 'VALIDATED', label: '승인 완료' },
  { key: 'FAILED', label: '처리 실패' },
] as const

interface Props {
  counts: Record<string, number>
  /** true면 "전체 문서" 탭이 지금 열려 있다는 뜻 -- 아래 지름길을 강조 표시한다. */
  documentsActive: boolean
  onOpenDocuments: () => void
  onOpenSettings: () => void
  user: AuthUser
  onLogout: () => void
}

export function Sidebar({
  counts, documentsActive, onOpenDocuments, onOpenSettings, user, onLogout,
}: Props) {
  return (
    <aside className="sidebar">
      {/* 배경은 화면 아래까지 이어지게 두고, 안쪽 내용만 스크롤을 따라온다. */}
      <div className="sidebar-inner">
        <div className="sidebar-brand">
          <div className="sidebar-brand-name">DOCVERIFY</div>
          <div className="sidebar-brand-sub">송장·영수증 검증 시스템</div>
        </div>

        <h2>현황</h2>
        {STATUS_ROWS.map((row) => (
          <div className="stat" key={row.key}>
            <span className="label"><span className={`dot ${row.key}`} />{row.label}</span>
            <span className={`value ${row.key}`}>{counts[row.key] ?? 0}</span>
          </div>
        ))}

        <hr />

        {/* 지금 실제로 있는 화면으로만 연결한다 -- 대시보드·사용자 관리처럼 갈 곳이
            없는 메뉴는 눌러도 아무 일도 안 일어나는 죽은 버튼이 되므로 넣지 않는다. */}
        <nav className="sidebar-nav">
          <button
            type="button"
            className={documentsActive ? 'active' : ''}
            onClick={onOpenDocuments}
          >
            🗂️ 문서 관리
          </button>
          <button type="button" onClick={onOpenSettings}>
            ⚙️ 시스템 설정
          </button>
        </nav>
      </div>

      <div className="sidebar-user">
        <span className="sidebar-user-avatar">{user.display_name.slice(0, 1)}</span>
        <span className="sidebar-user-info">
          <span className="sidebar-user-name">{user.display_name}</span>
          <span className="sidebar-user-id">@{user.username}</span>
        </span>
        <button type="button" className="sidebar-user-logout" onClick={onLogout} title="로그아웃">
          ⏻
        </button>
      </div>
    </aside>
  )
}
