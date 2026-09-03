import { Fragment } from 'react'

/**
 * 처리 파이프라인을 글로 나열하는 대신 단계 표시줄로 보여준다.
 * 실제로 문서가 이 순서대로 처리되므로(app/pipeline.py), 번호를 매기는 게
 * 장식이 아니라 사실이다.
 */

const STEPS = [
  'Docling 추출',
  'Ollama 필드 추출',
  '규칙 검증',
  'MS-SQL 저장',
  '검수·승인',
]

export function StepIndicator() {
  return (
    <div className="steps">
      {STEPS.map((label, i) => (
        <Fragment key={label}>
          <div className="step">
            <div className="step-dot">{i + 1}</div>
            <div className="step-label">{label}</div>
          </div>
          {i < STEPS.length - 1 && <div className="step-line" />}
        </Fragment>
      ))}
    </div>
  )
}
