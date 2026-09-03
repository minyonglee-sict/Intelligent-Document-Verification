import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { api } from '../api'
import { jobDuration } from '../format'
import type { Job } from '../types'

/**
 * 업로드 화면. Streamlit ui_upload 와 같은 흐름을 따른다.
 *
 *   파일 선택 → [검증 파이프라인 실행 (N건)] → 처리 결과 요약
 *
 * 고르자마자 처리를 시작하지 않는다. 문서 한 건에 수 분이 걸려서, 잘못 고른 파일을
 * 되돌릴 틈이 있어야 한다. 실행은 접수만 하고(202) 진행 상황은 따로 묻는다.
 */

const ACCEPT =
  '.pdf,.docx,.doc,.pptx,.ppt,.xlsx,.xls,.html,.htm,.md,.txt,.csv,' +
  '.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp'

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

interface Props {
  onFinished: () => void
  /** App 이 들고 있는 상태다 -- 업로드 탭을 벗어났다 돌아와도 처리 중 목록이
   *  안 사라지도록, 이 컴포넌트 자체의 useState 로 두지 않는다. */
  jobs: Job[]
  setJobs: Dispatch<SetStateAction<Job[]>>
}

export function UploadPanel({ onFinished, jobs, setJobs }: Props) {
  const [files, setFiles] = useState<File[]>([])
  const [skipDuplicates, setSkipDuplicates] = useState(true)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const pending = jobs.some((j) => j.state === 'QUEUED' || j.state === 'RUNNING')
  const done = jobs.filter((j) => j.state === 'DONE' || j.state === 'FAILED')

  // 접수한 작업이 남아 있는 동안만 진행 상황을 묻는다.
  useEffect(() => {
    if (!pending) return
    const timer = setInterval(async () => {
      const updated = await Promise.all(
        jobs.map((j) =>
          j.state === 'DONE' || j.state === 'FAILED'
            ? Promise.resolve(j)
            : api.getJob(j.job_id).catch(() => j),
        ),
      )
      setJobs(updated)
      const justFinished = updated.some(
        (u, i) => u.state !== jobs[i].state && (u.state === 'DONE' || u.state === 'FAILED'),
      )
      if (justFinished) onFinished()
    }, 3000)
    return () => clearInterval(timer)
  }, [jobs, pending, onFinished, setJobs])

  const addFiles = (picked: FileList | null) => {
    if (!picked?.length) return
    setError(null)
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`))
      const next = Array.from(picked).filter((f) => !seen.has(`${f.name}:${f.size}`))
      return [...prev, ...next]
    })
  }

  const run = async () => {
    if (!files.length) return
    setRunning(true)
    setError(null)
    const started: Job[] = []
    for (const file of files) {
      try {
        started.push(await api.upload(file, skipDuplicates))
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    }
    setJobs((prev) => [...started, ...prev])
    setFiles([])
    if (inputRef.current) inputRef.current.value = ''
    setRunning(false)
  }

  const reset = () => {
    setFiles([])
    setJobs([])
    setError(null)
    if (inputRef.current) inputRef.current.value = ''
  }

  // 처리 결과 요약 (Streamlit 의 4개 지표와 같은 구성)
  const summary = {
    pending: done.filter((j) => !j.skipped && j.document_status === 'PENDING').length,
    error: done.filter((j) => !j.skipped && j.document_status === 'ERROR').length,
    failed: done.filter((j) => !j.skipped && (j.state === 'FAILED' || j.document_status === 'FAILED')).length,
    skipped: done.filter((j) => j.skipped).length,
  }

  return (
    <div className="panel">
      <h2>문서 업로드</h2>

      <div
        className={`dropzone ${dragOver ? 'over' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); addFiles(e.dataTransfer.files) }}
      >
        {/* label 로 감싸면 클릭했을 때 자바스크립트 없이도 파일 선택창이 열린다.
            input 자체는 브라우저 기본 모양이 못생겨서 화면 밖으로 숨기고, 여기
            보이는 아이콘·문구로 대신한다. */}
        <label className="dropzone-label" htmlFor="upload-input">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
            <path d="M7 9l5-5 5 5" />
            <path d="M12 4v12" />
          </svg>
          <div className="dropzone-title">클릭하거나 파일을 끌어다 놓으세요</div>
          <div className="dropzone-sub">PDF · Word · PowerPoint · Excel · 이미지 (여러 개 가능)</div>
        </label>
        <input
          id="upload-input"
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          onChange={(e) => addFiles(e.target.files)}
          disabled={running}
        />
        {files.length > 0 && (
          <ul className="filelist">
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`}>
                <span>{f.name}</span>
                <span className="row" style={{ gap: 10 }}>
                  <span className="muted small">{formatSize(f.size)}</span>
                  <button
                    className="btn danger"
                    style={{ padding: '1px 7px' }}
                    onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                    disabled={running}
                    title="목록에서 빼기"
                  >
                    ×
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="row" style={{ marginTop: 12 }}>
        <label className="row small" style={{ gap: 6 }}>
          <input
            type="checkbox"
            style={{ width: 'auto' }}
            checked={skipDuplicates}
            onChange={(e) => setSkipDuplicates(e.target.checked)}
            disabled={running}
          />
          중복 파일 건너뛰기
        </label>
        <button
          className="btn primary"
          onClick={run}
          disabled={!files.length || running}
          style={{ minWidth: 240 }}
        >
          검증 파이프라인 실행 ({files.length}건)
        </button>
        <button className="btn" onClick={reset} disabled={running || (!files.length && !jobs.length)}>
          초기화
        </button>
      </div>

      {error && <div className="notice err" style={{ marginTop: 12 }}>{error}</div>}

      {jobs.length > 0 && (
        <>
          <div className="row small muted" style={{ marginTop: 16 }}>
            {pending
              ? `처리 중… ${done.length}/${jobs.length}건 완료 (문서당 수 분이 걸립니다)`
              : `완료: ${jobs.length}건 처리`}
          </div>

          {done.length > 0 && (
            <div className="metrics">
              <div className="metric"><div className="label">검증 통과</div><div className="value">{summary.pending}</div></div>
              <div className="metric"><div className="label">검증 오류</div><div className="value">{summary.error}</div></div>
              <div className="metric"><div className="label">처리 실패</div><div className="value">{summary.failed}</div></div>
              <div className="metric"><div className="label">중복 건너뜀</div><div className="value">{summary.skipped}</div></div>
            </div>
          )}

          <table>
            <thead>
              <tr>
                <th>파일</th>
                <th style={{ width: 130 }}>상태</th>
                <th className="num" style={{ width: 70 }}>문서</th>
                <th className="num" style={{ width: 70 }}>오류</th>
                <th style={{ width: 80 }}>작업시간</th>
                <th>비고</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.job_id}>
                  <td>{j.filename}</td>
                  <td>
                    {j.skipped ? (
                      <span className="badge PROCESSING">중복 건너뜀</span>
                    ) : j.state === 'DONE' && j.document_status ? (
                      <span className={`badge ${j.document_status}`}>{j.document_status}</span>
                    ) : j.state === 'FAILED' ? (
                      <span className="badge FAILED">실패</span>
                    ) : (
                      <span className="small muted">{j.state === 'RUNNING' ? '처리 중…' : '대기 중'}</span>
                    )}
                  </td>
                  <td className="num">{j.document_id ?? '-'}</td>
                  <td className="num">{j.state === 'DONE' && !j.skipped ? j.error_count : '-'}</td>
                  <td className="small muted">{jobDuration(j.started_at, j.finished_at)}</td>
                  <td className="small muted">{j.message}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {summary.error > 0 && !pending && (
            <div className="notice warn" style={{ marginTop: 10 }}>
              {summary.error}건이 검증에 실패했습니다. <strong>검수</strong> 탭에서 확인하세요.
            </div>
          )}
        </>
      )}
    </div>
  )
}
