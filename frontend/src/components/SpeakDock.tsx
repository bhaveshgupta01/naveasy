import { useCallback, useEffect, useRef, useState } from 'react'
import { API } from '../api'
import type { UiAction } from '../types'

// A small speak button docked beside the chat button. The assistant drives the app from outside, the way a broker
// drives the screen for a client: the map and panels stay visible and move while it talks. Only the last thing
// heard and the sentence being spoken show in a small caption; the full conversation is in the Ask tab.
//
// The first press starts a conversation the agent leads (it asks what help you need, then budget and area), so it
// works with no screen at all: after a question the microphone opens again by itself. Hold Space to talk, Escape
// stops it talking, V starts or ends the conversation.
//
// Speech runs on open-source models on our own server (backend/app/speech.py): Whisper hears, Piper speaks.
// If those aren't installed it falls back to the browser's speech engines.

export interface Reply {
  text: string
  ui_actions: UiAction[]
}

interface Props {
  ask: (text: string) => Promise<Reply>
  runActions: (actions: UiAction[]) => void
  onChat: () => void
  chatOpen: boolean
  startRef?: React.MutableRefObject<(() => void) | null>
}

/* eslint-disable @typescript-eslint/no-explicit-any */
const BrowserSR: any = (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition
type Phase = 'off' | 'idle' | 'listening' | 'thinking' | 'speaking'

export function sentences(text: string): string[] {
  const parts = text.match(/[^.!?]+[.!?]+(?=\s|$)|[^.!?]+$/g)?.map((s) => s.trim()).filter(Boolean) ?? [text]
  // join very short pieces ("1.", "St.") to their neighbor so speech doesn't stutter
  const out: string[] = []
  for (const p of parts) {
    if (out.length && (out[out.length - 1].length < 28 || p.length < 14)) out[out.length - 1] += ' ' + p
    else out.push(p)
  }
  return out
}

// Which sentence each cued action belongs to: the one in which its cue text starts.
function cueSchedule(text: string, parts: string[], actions: UiAction[]): { now: UiAction[]; at: Map<number, UiAction[]> } {
  const spans: [number, number][] = []
  let pos = 0
  for (const p of parts) {
    const head = p.slice(0, Math.min(20, p.length))
    const i = text.indexOf(head, pos)
    const start = i >= 0 ? i : pos
    spans.push([start, start + p.length])
    pos = start + p.length
  }
  const now: UiAction[] = []
  const at = new Map<number, UiAction[]>()
  const low = text.toLowerCase()
  for (const a of actions) {
    const cue = (a as { cue?: string }).cue
    const idx = cue ? low.indexOf(cue.toLowerCase()) : -1
    const k = idx < 0 ? -1 : spans.findIndex(([s, e]) => idx >= s && idx < e + 2)
    if (k < 0) now.push(a)
    else at.set(k, [...(at.get(k) ?? []), a])
  }
  return { now, at }
}

export default function SpeakDock({ ask, runActions, onChat, chatOpen, startRef }: Props) {
  const [phase, setPhase] = useState<Phase>('off')
  const [heard, setHeard] = useState('')
  const [said, setSaid] = useState('')
  const [note, setNote] = useState('')
  const [autoListen, setAutoListen] = useState(true)

  const phaseRef = useRef<Phase>('off')
  const setP = (p: Phase) => {
    phaseRef.current = p
    setPhase(p)
  }
  const rate = useRef(1)
  const autoRef = useRef(autoListen)
  autoRef.current = autoListen
  const engine = useRef<{ listen: boolean; speak: boolean } | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const browserRec = useRef<any>(null)
  const audio = useRef<HTMLAudioElement | null>(null)
  const speakToken = useRef(0)
  const stopTimer = useRef<number | undefined>(undefined)
  const listenRef = useRef<() => void>(() => undefined)

  const stopSpeaking = useCallback(() => {
    speakToken.current++
    audio.current?.pause()
    audio.current = null
    window.speechSynthesis?.cancel()
    if (phaseRef.current === 'speaking') setP('idle')
  }, [])

  const stopListening = useCallback(() => {
    window.clearInterval(stopTimer.current)
    if (recorder.current?.state === 'recording') recorder.current.stop()
    browserRec.current?.stop?.()
  }, [])

  // ---- speaking, sentence by sentence; cued actions run as their sentence starts
  const speak = useCallback(
    async (text: string, actions: UiAction[] = []) => {
      stopSpeaking()
      const token = ++speakToken.current
      const parts = sentences(text)
      const { now, at } = cueSchedule(text, parts, actions)
      if (now.length) runActions(now)
      setSaid(parts[0] ?? text)
      setP('speaking')
      const begin = (i: number) => {
        setSaid(parts[i])
        const due = at.get(i)
        if (due) runActions(due)
      }
      const finish = () => {
        if (token !== speakToken.current) return
        for (const [i, due] of at) if (i >= parts.length) runActions(due)
        setSaid(text.length > 220 ? parts[parts.length - 1] : text)
        setP('idle')
        if (autoRef.current && /\?\s*$/.test(text)) window.setTimeout(() => listenRef.current(), 350) // the agent asked: listen for the answer
      }
      if (engine.current?.speak) {
        const fetchPart = (t: string) =>
          fetch(`${API}/speech/say`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text: t, speed: rate.current }) })
            .then((r) => (r.ok ? r.blob() : Promise.reject(new Error('speech failed'))))
            .then((b) => URL.createObjectURL(b))
        try {
          let next = fetchPart(parts[0])
          for (let i = 0; i < parts.length; i++) {
            const url = await next
            if (i + 1 < parts.length) next = fetchPart(parts[i + 1])
            if (token !== speakToken.current) return URL.revokeObjectURL(url)
            begin(i)
            await new Promise<void>((resolve) => {
              const a = new Audio(url)
              audio.current = a
              a.onended = a.onerror = () => {
                URL.revokeObjectURL(url)
                resolve()
              }
              a.play().catch(() => resolve())
            })
            if (token !== speakToken.current) return
          }
          return finish()
        } catch {
          /* fall through to the browser voice */
        }
      }
      const synth = window.speechSynthesis
      if (!synth) {
        parts.forEach((_p, i) => begin(i))
        return finish()
      }
      parts.forEach((part, i) => {
        const u = new SpeechSynthesisUtterance(part)
        u.rate = rate.current
        u.onstart = () => token === speakToken.current && begin(i)
        if (i === parts.length - 1) u.onend = finish
        synth.speak(u)
      })
    },
    [runActions, stopSpeaking],
  )

  // ---- one turn: send what was heard, speak the answer while the app moves
  const send = useCallback(
    async (text: string) => {
      const low = text.toLowerCase().trim().replace(/[.!?]+$/, '')
      if (text !== '__start__') setHeard(text)
      if (/^(stop|quiet|be quiet|cancel)$/.test(low)) return setP('idle')
      if (/^(slower|slow down|speak slower)$/.test(low)) {
        rate.current = Math.max(0.6, rate.current - 0.2)
        return speak('Okay, slower.')
      }
      if (/^(faster|speed up|speak faster)$/.test(low)) {
        rate.current = Math.min(1.8, rate.current + 0.2)
        return speak('Okay, faster.')
      }
      setP('thinking')
      setSaid('…')
      try {
        const r = await ask(text)
        void speak(r.text, r.ui_actions ?? [])
      } catch {
        void speak('Sorry, I could not reach the service. Please try again.')
      }
    },
    [ask, speak],
  )

  // ---- hearing: our own Whisper when it is installed, else the browser's recognizer
  const listen = useCallback(async () => {
    if (phaseRef.current === 'listening' || phaseRef.current === 'thinking' || phaseRef.current === 'off') return
    stopSpeaking()
    setNote('')
    if (engine.current?.listen && navigator.mediaDevices?.getUserMedia) {
      try {
        stream.current ??= await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
      } catch {
        setNote('The microphone is blocked. Allow it in the browser, or type in the chat.')
        return
      }
      const rec = new MediaRecorder(stream.current)
      const chunks: Blob[] = []
      rec.ondataavailable = (e) => e.data.size && chunks.push(e.data)
      rec.onstop = async () => {
        window.clearInterval(stopTimer.current)
        const blob = new Blob(chunks, { type: rec.mimeType })
        if (blob.size < 1500) return setP('idle')
        setP('thinking')
        try {
          const r = await fetch(`${API}/speech/transcribe`, { method: 'POST', headers: { 'content-type': rec.mimeType || 'audio/webm' }, body: blob })
          const text = r.ok ? ((await r.json()).text as string) : ''
          if (text.trim()) void send(text.trim())
          else {
            setHeard('(nothing heard)')
            setP('idle')
          }
        } catch {
          setP('idle')
        }
      }
      recorder.current = rec
      setP('listening')
      rec.start()
      // stop by itself after a pause, so tapping once (or hands-free) is enough
      const ctx = new AudioContext()
      const an = ctx.createAnalyser()
      an.fftSize = 1024
      ctx.createMediaStreamSource(stream.current).connect(an)
      const buf = new Float32Array(an.fftSize)
      let spoke = false
      let quiet = 0
      const began = Date.now()
      stopTimer.current = window.setInterval(() => {
        an.getFloatTimeDomainData(buf)
        const rms = Math.sqrt(buf.reduce((s, v) => s + v * v, 0) / buf.length)
        if (rms > 0.02) {
          spoke = true
          quiet = 0
        } else quiet += 100
        const longEnough = (spoke && quiet >= 1400) || (!spoke && Date.now() - began > 8000) || Date.now() - began > 25000
        if (longEnough || rec.state !== 'recording') {
          window.clearInterval(stopTimer.current)
          void ctx.close()
          if (rec.state === 'recording') rec.stop()
        }
      }, 100)
      return
    }
    if (!BrowserSR) return setNote('This browser has no speech input. Type in the chat instead.')
    const r = new BrowserSR()
    r.lang = 'en-US'
    r.interimResults = true
    let finalText = ''
    r.onresult = (e: any) => {
      const t = Array.from(e.results as ArrayLike<any>).map((x) => x[0].transcript).join(' ')
      setHeard(t)
      if (e.results[e.results.length - 1].isFinal) finalText = t
    }
    r.onerror = () => setP('idle')
    r.onend = () => {
      browserRec.current = null
      if (finalText.trim()) void send(finalText)
      else setP('idle')
    }
    browserRec.current = r
    setP('listening')
    r.start()
  }, [send, stopSpeaking])
  listenRef.current = listen

  const start = useCallback(async () => {
    if (phaseRef.current !== 'off') return
    setP('idle')
    if (!engine.current) {
      try {
        const h = await (await fetch(`${API}/health`)).json()
        engine.current = { listen: !!h.speech?.listen, speak: !!h.speech?.speak }
      } catch {
        engine.current = { listen: false, speak: false }
      }
    }
    void send('__start__')
  }, [send])
  const end = useCallback(() => {
    speakToken.current++
    audio.current?.pause()
    window.speechSynthesis?.cancel()
    browserRec.current?.abort?.()
    window.clearInterval(stopTimer.current)
    if (recorder.current?.state === 'recording') recorder.current.stop()
    setP('off')
    setSaid('')
    setHeard('')
  }, [])
  if (startRef) startRef.current = () => void start()

  useEffect(() => {
    const typing = (e: KeyboardEvent) => ['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement).tagName)
    const down = (e: KeyboardEvent) => {
      if (typing(e) || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key.toLowerCase() === 'v' && !e.repeat) {
        if (phaseRef.current === 'off') void start()
        else end()
      } else if (e.code === 'Space' && !e.repeat && phaseRef.current !== 'off') {
        e.preventDefault()
        void listenRef.current()
      } else if (e.key === 'Escape' && phaseRef.current !== 'off') {
        if (phaseRef.current === 'speaking') stopSpeaking()
        else if (phaseRef.current === 'listening') stopListening()
      }
    }
    const up = (e: KeyboardEvent) => {
      if (e.code === 'Space' && !typing(e) && phaseRef.current === 'listening') stopListening()
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  }, [start, end, stopSpeaking, stopListening])
  useEffect(
    () => () => {
      speakToken.current++
      audio.current?.pause()
      window.speechSynthesis?.cancel()
      stream.current?.getTracks().forEach((t) => t.stop())
    },
    [],
  )

  const label = {
    off: 'Talk to NavEasy (V)',
    idle: 'Tap or hold Space to talk',
    listening: 'Listening. Pause when you are done',
    thinking: 'Looking that up',
    speaking: 'Speaking. Tap to interrupt',
  }[phase]
  const press = () => {
    if (phase === 'off') void start()
    else if (phase === 'listening') stopListening()
    else void listen()
  }

  return (
    <div className={`dock ${phase}`} role="region" aria-label="Voice assistant">
      {phase !== 'off' && (heard || said || note) && (
        <div className="dock-caption" aria-hidden={phase === 'speaking'}>
          {note && <p className="dock-note">{note}</p>}
          {heard && (
            <p className="dock-heard">
              <span>You</span> {heard}
            </p>
          )}
          {said && (
            <p className="dock-said">
              <span>NavEasy</span> {said}
            </p>
          )}
          <div className="dock-tools">
            <label>
              <input type="checkbox" checked={autoListen} onChange={(e) => setAutoListen(e.target.checked)} /> Listen after questions
            </label>
            <button type="button" onClick={end}>
              End
            </button>
          </div>
        </div>
      )}
      <button type="button" className={chatOpen ? 'dock-chat on' : 'dock-chat'} onClick={onChat} aria-label="Open the chat" title="Chat">
        <span aria-hidden>💬</span>
      </button>
      <button type="button" className={`dock-mic ${phase}`} onClick={press} aria-label={label} title={label}>
        <span aria-hidden>{phase === 'speaking' ? '🔊' : phase === 'thinking' ? '…' : '🎙'}</span>
      </button>
      <span className="sr-only" aria-live="polite">
        {phase === 'listening' ? 'Listening' : phase === 'thinking' ? 'Looking that up' : ''}
      </span>
    </div>
  )
}
