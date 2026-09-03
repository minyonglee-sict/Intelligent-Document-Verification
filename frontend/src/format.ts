/** 서버가 UTC ISO 문자열로 주는 시각을 브라우저 로컬 시간으로 바꿔 보여준다.
 *  예전엔 문자열을 그냥 잘라서(`slice(0, 16)`) UTC 시각을 로컬 시각인 것처럼
 *  보여줬다 -- KST 등 UTC가 아닌 시간대에서는 실제 시각과 어긋나 보였다. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(0, 16).replace('T', ' ')
  return d.toLocaleString('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

/** 처리에 걸린 시간(초)을 '9분 56초' 식으로 보여준다. 1분 미만이면 초만 보인다. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return ''
  const total = Math.round(seconds)
  const m = Math.floor(total / 60)
  const s = total % 60
  return m > 0 ? `${m}분 ${s}초` : `${s}초`
}

/** 작업(Job)의 시작~종료 시각으로 걸린 시간을 잰다. 아직 안 끝났으면 빈 문자열. */
export function jobDuration(startedAt: string, finishedAt: string | null): string {
  if (!finishedAt) return ''
  const start = new Date(startedAt).getTime()
  const end = new Date(finishedAt).getTime()
  if (Number.isNaN(start) || Number.isNaN(end)) return ''
  return formatDuration((end - start) / 1000)
}
