import { useEffect, useRef, useState } from 'react'
import { chat } from '../api'
import type { UiAction } from '../types'

interface Msg {
  role: 'user' | 'assistant'
  content: string
}

interface Props {
  context: Record<string, unknown>
  onActions: (a: UiAction[]) => void
}

const STARTERS = [
  'I use a power wheelchair and my wife is hard of hearing. We need a place near the Medical Center under $2,000.',
  "I'm moving from 1200 W 7th St, Los Angeles to Houston. I use a wheelchair.",
  'My mother is 82 and tires quickly. Find her somewhere in Midtown with no stairs.',
]

export default function ChatPanel({ context, onActions }: Props) {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [listening, setListening] = useState(false)
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => end.current?.scrollIntoView({ block: 'end' }), [msgs, busy])

  const send = (content: string) => {
    if (!content.trim() || busy) return
    const next: Msg[] = [...msgs, { role: 'user', content }]
    setMsgs(next)
    setText('')
    setBusy(true)
    chat(next, context)
      .then((r) => {
        setMsgs([...next, { role: 'assistant', content: r.text }])
        onActions(r.ui_actions ?? [])
        if ('speechSynthesis' in window && speakRef.current) window.speechSynthesis.speak(new SpeechSynthesisUtterance(r.text))
      })
      .catch((e) => setMsgs([...next, { role: 'assistant', content: `Sorry, that failed: ${e.message ?? e}` }]))
      .finally(() => setBusy(false))
  }

  // Speak-to-type and read-aloud with the browser's own speech engine (no keys, nothing leaves for a third party
  // beyond what the browser itself does). The Gemini Live voice agent in docs/VOICE.md replaces this when a key is set.
  const speakRef = useRef(false)
  const [speak, setSpeak] = useState(false)
  speakRef.current = speak
  const SR = (window as unknown as { SpeechRecognition?: new () => any; webkitSpeechRecognition?: new () => any }) // eslint-disable-line @typescript-eslint/no-explicit-any
  const Recognition = SR.SpeechRecognition ?? SR.webkitSpeechRecognition
  const listen = () => {
    if (!Recognition) return
    const r = new Recognition()
    r.lang = 'en-US'
    r.onresult = (e: { results: { 0: { transcript: string } }[] }) => send(e.results[0][0].transcript)
    r.onend = () => setListening(false)
    setListening(true)
    r.start()
  }

  return (
    <div className="chat">
      <div className="chat-log" role="log" aria-live="polite" aria-label="Conversation with the NavEasy assistant">
        {!msgs.length && (
          <div className="starters">
            <p className="muted">Tell me what you need in your own words. I look things up in the measured data and move the map for you.</p>
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
          {Recognition && (
            <button type="button" onClick={listen} aria-pressed={listening}>
              {listening ? 'Listening…' : '🎤 Speak'}
            </button>
          )}
          <label className="check">
            <input type="checkbox" checked={speak} onChange={(e) => setSpeak(e.target.checked)} /> Read answers aloud
          </label>
        </div>
      </form>
    </div>
  )
}
