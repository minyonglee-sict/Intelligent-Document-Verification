import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Job } from '../types'

/**
 * 업로드는 접수만 하고 작업 번호를 받는다(202). 문서 한 건에 수 분이 걸리므로
 * 응답을 붙들고 기다리지 않고, 진행 상황을 따로 물어 화면에 보여준다.
 */
export function UploadPanel({ onFinished }: { onFinished: () => void }) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const pending = jobs.some((j) => j.state === 'QUEUED' || j.state === 'RUNNING')

  useEffect(() => {
    if (!pending) return
    const timer = setInterval(async () => {
      const updated = await Promise.all(
        jobs.map(async (j) =>
          j.state === 'DONE' || j.state === 'FAILED' ? j : api.getJob(j.job_id).catch(() => j),
        ),
      )
      setJobs(updated)
      // 하나라도 막 끝났으면 목록을 새로 읽게 한다
      if (updated.some((u, i) => u.state !== jobs[i].state && (u.state === 'DONE' || u.state === 'FAILED'))) {
        onFinished()
      }
    }, 3000)
    return () => clearInterval(timer)
  }, [jobs, pending, onFinished])

  const pick = async (files: FileList | null) => {
    if (!files?.length) return
    setError(null)
    for (const file of Array.from(files)) {
      try {
        const job = await api.upload(file)
        setJobs((prev) => [job, ...prev])
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    }
    if (inputRef.current) inputRef.current.value = ''
  }

  return (
    <div className="panel">
      <h2>문서 업로드</h2>
      <p className="muted small">
        PDF·이미지·Office 문서를 올리면 Docling 추출 → 필드 추출 → 규칙 검증 → 저장까지 진행됩니다.
        문서 한 건에 수 분이 걸리므로, 접수 후 진행 상황이 아래에 표시됩니다.
      </p>

      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".pdf,.docx,.doc,.pptx,.ppt,.xlsx,.xls,.html,.htm,.md,.txt,.csv,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp"
        onChange={(e) => pick(e.target.files)}
      />

      {error && <div className="notice err" style={{ marginTop: 12 }}>{error}</div>}

      {jobs.length > 0 && (
        <>
          <h2 style={{ marginTop: 20 }}>처리 상황</h2>
          <table>
            <thead>
              <tr>
                <th>파일</th>
                <th style={{ width: 110 }}>상태</th>
                <th className="num" style={{ width: 70 }}>문서</th>
                <th className="num" style={{ width: 70 }}>오류</th>
                <th>비고</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.job_id}>
                  <td>{j.filename}</td>
                  <td>
                    {j.state === 'DONE' && j.document_status ? (
                      <span className={`badge ${j.document_status}`}>{j.document_status}</span>
                    ) : (
                      <span className="small">{j.state === 'RUNNING' ? '처리 중…' : j.state}</span>
                    )}
                  </td>
                  <td className="num">{j.document_id ?? '-'}</td>
                  <td className="num">{j.state === 'DONE' ? j.error_count : '-'}</td>
                  <td className="small muted">{j.skipped ? '중복 건너뜀' : j.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}
