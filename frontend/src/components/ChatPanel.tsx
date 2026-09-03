import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { ChatToolCall, ChatTurn } from '../types'

/**
 * 화면에서 MCP 도구를 쓰는 자리.
 *
 * 사용자는 한국말로 묻고, 엔진 쪽 LLM 이 어떤 MCP 도구를 부를지 골라 실행한 뒤
 * 답을 만든다. Claude Desktop 이 내부에서 하는 일과 같은 구조다.
 *
 * 어떤 도구를 거쳤는지 숨기지 않고 보여준다 -- 무엇을 근거로 답했는지 볼 수 있어야
 * 답을 믿을 수 있고, 이 구조를 이해하는 데도 그게 핵심이다.
 */

interface Message extends ChatTurn {
  toolCalls?: ChatToolCall[]
  rounds?: number
}

const EXAMPLES = [
  '지금 오류 난 문서 뭐 있어?',
  '상태별로 몇 건씩이야?',
  '32번 문서 어떻게 돼 있어?',
  '미처리 오류 신고 있어?',
]

interface Props {
  /** 채팅이 resolve_report 를 쓰면(유일하게 데이터를 바꾸는 도구) 상단 배지도
   *  새로 고치라고 부모에 알린다. 나머지 9개 도구는 전부 읽기 전용이라 필요 없다. */
  onChanged?: () => void
}

export function ChatPanel({ onChanged }: Props) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tools, setTools] = useState<{ name: string; description: string }[]>([])
  const [connected, setConnected] = useState<boolean | null>(null)
  const [model, setModel] = useState('')
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.chatTools()
      .then((r) => { setTools(r.tools); setConnected(r.connected); setModel(r.model) })
      .catch(() => setConnected(false))
  }, [])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, busy])

  const send = async (question: string) => {
    if (!question.trim() || busy) return
    setError(null)
    setInput('')
    // 보내기 전 기록만 넘긴다. 방금 질문은 서버가 따로 받는다.
    const history: ChatTurn[] = messages.map((m) => ({ role: m.role, content: m.content }))
    setMessages((prev) => [...prev, { role: 'user', content: question }])
    setBusy(true)
    try {
      const r = await api.chat(question, history)
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: r.answer, toolCalls: r.tool_calls, rounds: r.rounds },
      ])
      // resolve_report 는 MCP 도구 중 유일하게 데이터를 바꾼다(신고 처리 완료·재오픈).
      // 그것 말고는 전부 조회뿐이라, 그 도구가 쓰였을 때만 상단 배지를 새로 고친다.
      if (r.tool_calls.some((c) => c.name === 'resolve_report')) {
        onChanged?.()
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="panel">
      <h2>문서 물어보기</h2>
      <p className="muted small" style={{ margin: '0 0 .8rem' }}>
        저장된 문서에 대해 한국말로 물어보세요. 답을 지어내지 않고 <strong>MCP 도구로 실제
        값을 확인한 뒤</strong> 답합니다. 승인·삭제·업로드는 도구에 없으니 화면에서 하세요.
      </p>

      {connected === false && (
        <div className="notice err">
          MCP 서버에 연결되어 있지 않습니다. 엔진을 다시 시작하세요.
        </div>
      )}

      {connected && (
        <details className="raw" style={{ marginBottom: '.9rem' }}>
          <summary>쓸 수 있는 도구 {tools.length}개 · 모델 {model}</summary>
          <table>
            <tbody>
              {tools.map((t) => (
                <tr key={t.name}>
                  <td style={{ width: 200 }}><code>{t.name}</code></td>
                  <td className="small muted">{t.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      <div className="chat">
        {messages.length === 0 && (
          <div className="chat-empty">
            <p className="muted small" style={{ margin: '0 0 .6rem' }}>이렇게 물어보세요</p>
            <div className="row">
              {EXAMPLES.map((q) => (
                <button key={q} className="btn" style={{ fontSize: '.86rem' }} onClick={() => send(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>
            <div className="bubble-body">{m.content}</div>
            {m.toolCalls && m.toolCalls.length > 0 && (
              <details className="toolcalls">
                <summary>
                  {m.toolCalls.map((c) => c.name).join(' · ')}
                </summary>
                {m.toolCalls.map((c, j) => (
                  <div key={j} className="toolcall">
                    <code>{c.name}({JSON.stringify(c.arguments)})</code>
                    <pre>{c.result}</pre>
                  </div>
                ))}
              </details>
            )}
          </div>
        ))}

        {busy && (
          <div className="bubble assistant">
            <div className="bubble-body muted">생각하는 중… (도구를 부르면 수십 초 걸립니다)</div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      {error && <div className="notice err" style={{ marginTop: '.8rem' }}>{error}</div>}

      <div className="row" style={{ marginTop: '.9rem', flexWrap: 'nowrap' }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input) } }}
          placeholder="예) 지금 오류 난 문서 뭐 있어?"
          disabled={busy || connected === false}
        />
        <button className="btn primary" onClick={() => send(input)} disabled={busy || !input.trim()}>
          보내기
        </button>
        <button className="btn" onClick={() => { setMessages([]); setError(null) }} disabled={busy || !messages.length}>
          비우기
        </button>
      </div>
    </div>
  )
}
