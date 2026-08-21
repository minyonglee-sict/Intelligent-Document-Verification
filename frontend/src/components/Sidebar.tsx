/**
 * 왼쪽 사이드바. 상태별 문서 현황만 보여준다.
 *
 * 모델·DB 접속 정보와 연결 확인 버튼은 일반 사용자가 볼 필요가 없는 개발 정보라
 * SettingsModal(⚙️ 아이콘)로 옮겼다.
 */

const STATUS_ROWS = [
  { key: 'ERROR', label: '오류' },
  { key: 'PENDING', label: '검증 통과' },
  { key: 'VALIDATED', label: '승인 완료' },
  { key: 'FAILED', label: '처리 실패' },
] as const

interface Props {
  counts: Record<string, number>
}

export function Sidebar({ counts }: Props) {
  return (
    <aside className="sidebar">
      {/* 배경은 화면 아래까지 이어지게 두고, 안쪽 내용만 스크롤을 따라온다. */}
      <div className="sidebar-inner">
        <h2>현황</h2>
        {STATUS_ROWS.map((row) => (
          <div className="stat" key={row.key}>
            <div className="label"><span className={`dot ${row.key}`} />{row.label}</div>
            <div className="value">{counts[row.key] ?? 0}</div>
          </div>
        ))}
      </div>
    </aside>
  )
}
