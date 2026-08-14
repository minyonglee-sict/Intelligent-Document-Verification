import { useState } from 'react'

/**
 * 접히는 JSON 뷰어. Streamlit st.json 에 대응한다.
 *
 * Docling 원시 출력은 수천 줄이라 통째로 펼쳐 두면 화면이 마비된다. 객체·배열은
 * 접은 채로 시작하고, 필요한 가지만 열게 한다.
 */

type Json = string | number | boolean | null | Json[] | { [key: string]: Json }

function Leaf({ value }: { value: string | number | boolean | null }) {
  if (value === null) return <span className="json-null">NULL</span>
  if (typeof value === 'string') return <span className="json-str">"{value}"</span>
  if (typeof value === 'boolean') return <span className="json-bool">{String(value)}</span>
  return <span className="json-num">{value}</span>
}

function Node({
  name,
  value,
  depth,
  defaultOpen,
}: {
  name?: string
  value: Json
  depth: number
  defaultOpen: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)

  if (value === null || typeof value !== 'object') {
    return (
      <div className="json-row" style={{ paddingLeft: depth * 14 }}>
        {name !== undefined && <span className="json-key">"{name}"</span>}
        {name !== undefined && <span className="json-punct"> : </span>}
        <Leaf value={value as string | number | boolean | null} />
      </div>
    )
  }

  const isArray = Array.isArray(value)
  const entries: [string, Json][] = isArray
    ? (value as Json[]).map((v, i) => [String(i), v])
    : Object.entries(value as { [key: string]: Json })
  const brackets = isArray ? ['[', ']'] : ['{', '}']

  return (
    <div>
      <div
        className="json-row json-toggle"
        style={{ paddingLeft: depth * 14 }}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="json-caret">{open ? '▾' : '▸'}</span>
        {name !== undefined && <span className="json-key">"{name}"</span>}
        {name !== undefined && <span className="json-punct"> : </span>}
        <span className="json-punct">{brackets[0]}</span>
        {!open && (
          <span className="json-collapsed"> {entries.length}개 {brackets[1]}</span>
        )}
      </div>
      {open && (
        <>
          {entries.map(([key, child]) => (
            <Node
              key={key}
              name={isArray ? undefined : key}
              value={child}
              depth={depth + 1}
              defaultOpen={false}
            />
          ))}
          <div className="json-row json-punct" style={{ paddingLeft: depth * 14 }}>
            {brackets[1]}
          </div>
        </>
      )}
    </div>
  )
}

export function JsonView({ data, expanded = false }: { data: unknown; expanded?: boolean }) {
  return (
    <div className="json-view">
      <Node value={data as Json} depth={0} defaultOpen={expanded} />
    </div>
  )
}
