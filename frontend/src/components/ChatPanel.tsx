import { useEffect, useRef, useState } from 'react'

export interface Msg {
  role: 'user' | 'assistant'
  content: string
}

interface Props {
  msgs: Msg[]
  ask: (text: string) => Promise<string>
  onVoice: () => void
}

const STARTERS = [
  'I use a power wheelchair and my wife is hard of hearing. Find us a place near the Medical Center under $2,000.',
  "I'm moving from 1200 W 7th St, Los Angeles to Houston. I use a wheelchair.",
  'My mother is 82 and tires quickly. Find her a home in Midtown.',
]
const FOLLOW_UPS = ['Tell me about the first one', 'What is it like inside?', 'Tell me about the windows and the sun', 'How do I get to the pharmacy?', 'Next one']

// The same conversation as voice mode, typed. The assistant keeps the thread (needs, the homes it listed, the one
// being discussed) and moves the map, panels and routes as it answers.
export default function ChatPanel({ msgs, ask, onVoice }: Props) {
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
          <p key={i} className={`msg ${m.role}`}>
            <span className="sr-only">{m.role === 'user' ? 'You: ' : 'NavEasy: '}</span>
            {m.content}
          </p>
        ))}
        {busy && <p className="msg assistant muted">Looking that up…</p>}
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
