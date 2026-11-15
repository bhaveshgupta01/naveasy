import { useEffect, useRef, useState } from 'react'
import type { AgentStep } from '../types'

export interface Msg {
  role: 'user' | 'assistant'
  content: string
  steps?: AgentStep[]
  mode?: string
}

interface Props {
  msgs: Msg[]
  ask: (text: string) => Promise<string>
  live?: AgentStep[]
  onVoice: () => void
}

const WHO: Record<string, string> = {
  planner: 'Planner', building: 'Building · CoStar', inside: 'Inside · Matterport', outside: 'Outside · Vexcel, OSM, Valhalla', checker: 'Checker', agent: 'Agent',
}
const MODE: Record<string, string> = { deep: 'Planned with LangChain deepagents', claude: 'Claude agent with tools', local: 'Local, on this machine' }

// The agent's work, shown under its answer: the plan, each subagent's task, every tool call, the checks.
function Steps({ steps, mode, open }: { steps: AgentStep[]; mode?: string; open?: boolean }) {
  if (!steps.length) return null
  const icon = (s: AgentStep) => (s.kind === 'plan' ? '📝' : s.kind === 'subagent' ? '➜' : s.kind === 'tool' ? '🔧' : s.kind === 'check' ? (s.status === 'warn' ? '⚠' : '✓') : '•')
  return (
    <details className="steps" open={open}>
      <summary>
        How I worked this out: {steps.length} step{steps.length === 1 ? '' : 's'}
        {mode && MODE[mode] ? ` · ${MODE[mode]}` : ''}
      </summary>
      <ol>
        {steps.map((s, i) => (
          <li key={i} className={`step ${s.kind} ${s.status ?? ''}`}>
            <span aria-hidden>{icon(s)}</span> {s.who && <strong>{WHO[s.who] ?? s.who}:</strong>} {s.text}
          </li>
        ))}
      </ol>
    </details>
  )
}

const STARTERS = [
  'I use a power wheelchair and my wife is hard of hearing. Find us a place near the Medical Center under $2,000.',
  "I'm moving from 1200 W 7th St, Los Angeles to Houston. I use a wheelchair.",
  'My mother is 82 and tires quickly. Find her a home in Midtown.',
]
const FOLLOW_UPS = ['Tell me about the first one', 'What is it like inside?', 'Tell me about the windows and the sun', 'How do I get to the pharmacy?', 'Next one']

// The same conversation as voice mode, typed. The assistant keeps the thread (needs, the homes it listed, the one
// being discussed) and moves the map, panels and routes as it answers.
export default function ChatPanel({ msgs, ask, live = [], onVoice }: Props) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => end.current?.scrollIntoView({ block: 'end' }), [msgs, busy])

  const send = (content: string) => {
    if (!content.trim() || busy) return
    setText('')
    setBusy(true)
    void ask(content).finally(() => setBusy(false))
  }

  return (
    <div className="chat">
      <div className="chat-log" role="log" aria-live="polite" aria-label="Conversation with the NavEasy assistant">
        {!msgs.length && (
          <div className="starters">
            <p className="muted">Tell me what you need in your own words. I look it up in the measured data and move the map for you. You can keep asking follow-ups.</p>
            {STARTERS.map((s) => (
              <button key={s} onClick={() => send(s)}>
                {s}
              </button>
            ))}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i}>
            <p className={`msg ${m.role}`}>
              <span className="sr-only">{m.role === 'user' ? 'You: ' : 'NavEasy: '}</span>
              {m.content}
            </p>
            {m.steps && <Steps steps={m.steps} mode={m.mode} open={m.mode === 'deep'} />}
          </div>
        ))}
        {busy && (
          <>
            <p className="msg assistant muted">Looking that up…</p>
            <Steps steps={live} open />
          </>
        )}
        {msgs.length > 0 && !busy && (
          <div className="chips">
            {FOLLOW_UPS.map((f) => (
              <button key={f} onClick={() => send(f)}>
                {f}
              </button>
            ))}
          </div>
        )}
        <div ref={end} />
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          send(text)
        }}
      >
        <label htmlFor="chat-in" className="sr-only">
          Message
        </label>
        <textarea
          id="chat-in"
          rows={2}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send(text)
            }
          }}
          placeholder="I use a wheelchair and work at the Medical Center…"
        />
        <div className="row">
          <button className="primary" disabled={busy || !text.trim()}>
            Send
          </button>
          <button type="button" onClick={onVoice}>
            🎙 Talk instead
          </button>
        </div>
      </form>
    </div>
  )
}
