import { useCallback, useEffect, useRef, useState } from 'react'
import { API } from '../api'

// Voice-first mode: the whole app by ear. Hold Space (or tap the big button), speak, and the answer is read aloud
// while the app behind moves to match.
//
// Speech runs on open-source models on our own server (backend/app/speech.py): Whisper hears, Piper speaks.
// Nothing said leaves the machine. If those aren't installed, it falls back to the browser's speech engines.

interface Props {
  ask: (text: string) => Promise<string>
  onClose: () => void
}

/* eslint-disable @typescript-eslint/no-explicit-any */
const BrowserSR: any = (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition
const GREETING =
  'Voice mode is on. Hold the space bar, or tap the big button, and tell me what you need. ' +
  'For example: I am blind and use a wheelchair, find me a home near Midtown under two thousand dollars. ' +
  'You can also ask about anything on the screen. Say help for more, or press Escape to stop me talking.'

type Phase = 'idle' | 'listening' | 'thinking' | 'speaking'

function sentences(text: string): string[] {
  const parts = text.match(/[^.!?]+[.!?]+(?=\s|$)|[^.!?]+$/g)?.map((s) => s.trim()).filter(Boolean) ?? [text]
  // join very short pieces ("1.", "St.") to their neighbor so speech doesn't stutter
  const out: string[] = []
  for (const p of parts) {
    if (out.length && (out[out.length - 1].length < 28 || p.length < 14)) out[out.length - 1] += ' ' + p
    else out.push(p)
  }
  return out
}

export default function VoiceMode({ ask, onClose }: Props) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [heard, setHeard] = useState('')
  const [said, setSaid] = useState(GREETING)
  const [rate, setRate] = useState(1)
  const [handsFree, setHandsFree] = useState(false)
  const [typed, setTyped] = useState('')
  const [engine, setEngine] = useState<{ listen: boolean; speak: boolean; names?: string } | null>(null)
  const [micError, setMicError] = useState('')

  const phaseRef = useRef<Phase>('idle')
  const setP = (p: Phase) => {
    phaseRef.current = p
    setPhase(p)
  }
  const rateRef = useRef(rate)
  rateRef.current = rate
  const handsRef = useRef(handsFree)
  handsRef.current = handsFree
  const engineRef = useRef(engine)
  engineRef.current = engine
  const stream = useRef<MediaStream | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const browserRec = useRef<any>(null)
  const audio = useRef<HTMLAudioElement | null>(null)
  const speakToken = useRef(0)
  const stopTimer = useRef<number | undefined>(undefined)

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

  // ---- speaking
  const speak = useCallback(
    async (text: string) => {
      setSaid(text)
      stopSpeaking()
      const token = ++speakToken.current
      setP('speaking')
      const finish = () => {
        if (token !== speakToken.current) return
        setP('idle')
        if (handsRef.current) window.setTimeout(() => listenRef.current(), 300)
      }
      if (engineRef.current?.speak) {
        // Piper on our server: fetch each sentence (the next one loads while this one plays)
        const parts = sentences(text)
        const fetchPart = (t: string) =>
          fetch(`${API}/speech/say`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text: t, speed: rateRef.current }) })
            .then((r) => (r.ok ? r.blob() : Promise.reject(new Error('speech failed'))))
            .then((b) => URL.createObjectURL(b))
        try {
          let next = fetchPart(parts[0])
          for (let i = 0; i < parts.length; i++) {
            const url = await next
            if (i + 1 < parts.length) next = fetchPart(parts[i + 1])
            if (token !== speakToken.current) return URL.revokeObjectURL(url)
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
      if (!synth) return finish()
      const parts = sentences(text)
      parts.forEach((part, i) => {
        const u = new SpeechSynthesisUtterance(part)
        u.rate = rateRef.current
        if (i === parts.length - 1) u.onend = finish
        synth.speak(u)
      })
    },
    [stopSpeaking],
  )

  // ---- understanding
  const send = useCallback(
    async (text: string) => {
      setHeard(text)
      setP('thinking')
      const low = text.toLowerCase().trim().replace(/[.!?]+$/, '')
      if (/^(stop|quiet|be quiet|cancel|never mind)$/.test(low)) return setP('idle')
      if (/^(slower|slow down|speak slower)$/.test(low)) {
        rateRef.current = Math.max(0.6, rateRef.current - 0.2)
        setRate(rateRef.current)
        return speak('Okay, slower.')
      }
      if (/^(faster|speed up|speak faster)$/.test(low)) {
        rateRef.current = Math.min(1.8, rateRef.current + 0.2)
        setRate(rateRef.current)
        return speak('Okay, faster.')
      }
      if (/^(exit|close|leave) voice( mode)?$/.test(low)) return onClose()
      try {
        void speak(await ask(text))
      } catch {
        void speak('Sorry, I could not reach the service. Please try again.')
      }
    },
    [ask, speak, onClose],
  )

  // ---- hearing
  const listen = useCallback(async () => {
    if (phaseRef.current === 'listening' || phaseRef.current === 'thinking') return
    stopSpeaking()
    setHeard('')
    setMicError('')
    if (engineRef.current?.listen && navigator.mediaDevices?.getUserMedia) {
      try {
        stream.current ??= await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
      } catch {
        setMicError('The microphone is blocked. Allow it in the browser, or type below.')
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
            setHeard('(I did not hear anything)')
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
        const longEnough = (spoke && quiet >= 1400) || (!spoke && Date.now() - began > 7000) || Date.now() - began > 25000
        if (longEnough || rec.state !== 'recording') {
          window.clearInterval(stopTimer.current)
          void ctx.close()
          if (rec.state === 'recording') rec.stop()
        }
      }, 100)
      return
    }
    if (!BrowserSR) return setMicError('This browser has no speech input. Type below.')
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
  const listenRef = useRef(listen)
  listenRef.current = listen

  useEffect(() => {
    let alive = true
    fetch(`${API}/health`)
      .then((r) => r.json())
      .then((h) => {
        if (!alive) return
        const s = h.speech ?? {}
        const e = { listen: !!s.listen, speak: !!s.speak, names: s.listen && s.speak ? `${s.listen_model} and ${s.speak_model}, running on this machine` : undefined }
        engineRef.current = e
        setEngine(e)
        void speak(GREETING)
      })
      .catch(() => {
        engineRef.current = { listen: false, speak: false }
        setEngine(engineRef.current)
        void speak(GREETING)
      })
    const down = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === 'INPUT') return
      if (e.code === 'Space' && !e.repeat) {
        e.preventDefault()
        void listenRef.current()
      } else if (e.key === 'Escape') {
        if (phaseRef.current === 'speaking') stopSpeaking()
        else onClose()
      }
    }
    const up = (e: KeyboardEvent) => {
      if (e.code === 'Space' && (e.target as HTMLElement).tagName !== 'INPUT') stopListening()
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      alive = false
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
      window.clearInterval(stopTimer.current)
      speakToken.current++
      audio.current?.pause()
      window.speechSynthesis?.cancel()
      browserRec.current?.abort?.()
      if (recorder.current?.state === 'recording') recorder.current.stop()
      stream.current?.getTracks().forEach((t) => t.stop())
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const label = {
    idle: 'Hold Space or tap to talk',
    listening: 'Listening… pause when you are done',
    thinking: 'Looking that up…',
    speaking: 'Speaking. Tap to interrupt and talk',
  }[phase]

  return (
    <div className="voice" role="dialog" aria-modal="true" aria-label="Voice mode">
      <button className="voice-close" onClick={onClose}>
        Leave voice mode
      </button>
      <button className={`voice-mic ${phase}`} onClick={() => (phase === 'listening' ? stopListening() : void listen())} aria-label={label} autoFocus>
        <span className="voice-ring" aria-hidden />
        <span className="voice-label">{label}</span>
      </button>
      <div className="voice-text">
        {micError && (
          <p className="voice-heard" role="alert">
            {micError}
          </p>
        )}
        {heard && (
          <p className="voice-heard">
            <span className="sr-only">You said: </span>“{heard}”
          </p>
        )}
        <p className="voice-said" aria-live="polite">
          {said}
        </p>
      </div>
      <form
        className="voice-controls"
        onSubmit={(e) => {
          e.preventDefault()
          if (typed.trim()) void send(typed)
          setTyped('')
        }}
      >
        <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Or type here and press Enter" aria-label="Type instead of speaking" />
        <button type="button" onClick={() => void speak(said)}>
          Repeat
        </button>
        <button type="button" onClick={stopSpeaking}>
          Stop talking
        </button>
        <label>
          <input type="checkbox" checked={handsFree} onChange={(e) => setHandsFree(e.target.checked)} /> Hands-free: listen again after each answer
        </label>
        <label>
          Speed <input type="range" min={0.6} max={1.8} step={0.1} value={rate} onChange={(e) => setRate(Number(e.target.value))} aria-label="Speaking speed" />
        </label>
        <p className="voice-engine">
          {engine === null ? '' : engine.names ? `Speech: ${engine.names}. Nothing you say leaves it.` : 'Speech: your browser’s built-in engines (the local models are not installed).'}
        </p>
      </form>
    </div>
  )
}
