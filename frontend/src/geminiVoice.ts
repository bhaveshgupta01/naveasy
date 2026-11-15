// Optional voice engine: Gemini Live as the ears and the mouth only (docs/VOICE.md).
//
// The browser opens a Live session with a one-use token from our backend (POST /voice/token; the key stays on the
// server), streams the microphone, and plays Gemini's audio. Gemini has one tool, ask_naveasy: everything the person
// says goes to our own /agent/chat (the same conversation as the local engine) and Gemini reads the reply word for
// word, so it never supplies a fact. Checked from Python on Nov 14: the tool call comes 0.4 s after the person stops,
// and the first audio 0.6 s after the tool answers. Actions with a cue run when Gemini's transcript reaches the cue.

import { API } from './api'
import type { UiAction } from './types'

type Phase = 'idle' | 'listening' | 'thinking' | 'speaking'
export interface GeminiHandlers {
  ask: (text: string) => Promise<{ text: string; ui_actions: UiAction[] }>
  runActions: (actions: UiAction[]) => void
  onHeard: (text: string) => void
  onSaid: (text: string) => void
  onPhase: (phase: Phase) => void
  onError: (message: string) => void
}

const SYSTEM =
  'You are the voice of NavEasy, a housing assistant. You never answer from your own knowledge. For everything the person says, ' +
  'call ask_naveasy with their exact words (fix only obvious mishearings of place names), then read the "say" field of the result ' +
  'aloud word for word, in a warm, clear voice. Do not add, drop or change any word, number or fact, and say nothing else. ' +
  'If the person only says stop, wait or quiet, stop talking and do not call the tool. If the tool fails, say: Sorry, I could not look that up. ' +
  'The session opens with the message __start__: call ask_naveasy with the text __start__ and read the result.'
const WS = 'wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContentConstrained'

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim()

function toBase64(bytes: Uint8Array): string {
  let s = ''
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(s)
}

export class GeminiVoice {
  private ws: WebSocket | null = null
  private mic: MediaStream | null = null
  private inCtx: AudioContext | null = null
  private outCtx: AudioContext | null = null
  private proc: ScriptProcessorNode | null = null
  private sources: AudioBufferSourceNode[] = []
  private nextAt = 0
  private heard = ''
  private said = ''
  private cues: { key: string; action: UiAction }[] = []
  private turnDone = false
  private closed = false
  private h: GeminiHandlers

  constructor(handlers: GeminiHandlers) {
    this.h = handlers
  }

  async start(): Promise<void> {
    const r = await fetch(`${API}/voice/token`, { method: 'POST' })
    if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? 'no voice token')
    const { token, model } = (await r.json()) as { token: string; model: string }
    this.mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 } })
    this.outCtx = new AudioContext({ sampleRate: 24000 })
    const ws = new WebSocket(`${WS}?access_token=${encodeURIComponent(token)}`)
    this.ws = ws
    ws.onopen = () =>
      ws.send(
        JSON.stringify({
          setup: {
            model: `models/${model}`,
            generationConfig: { responseModalities: ['AUDIO'] },
            systemInstruction: { parts: [{ text: SYSTEM }] },
            tools: [
              {
                functionDeclarations: [
                  {
                    name: 'ask_naveasy',
                    description: "Answers anything the person says, from NavEasy's measured data about homes, buildings, routes and neighborhoods.",
                    parameters: { type: 'OBJECT', properties: { text: { type: 'STRING', description: 'What the person said, word for word' } }, required: ['text'] },
                  },
                ],
              },
            ],
            inputAudioTranscription: {},
            outputAudioTranscription: {},
          },
        }),
      )
    ws.onmessage = (e) => void this.onMessage(e.data)
    ws.onerror = () => this.h.onError('The Gemini voice connection failed. Switch it off to use the local voice.')
    ws.onclose = () => {
      if (!this.closed) this.h.onError('The Gemini voice session ended. Press the button to start again.')
      this.stop()
    }
  }

  stop(): void {
    this.closed = true
    this.proc?.disconnect()
    this.mic?.getTracks().forEach((t) => t.stop())
    this.flushAudio()
    void this.inCtx?.close().catch(() => undefined)
    void this.outCtx?.close().catch(() => undefined)
    if (this.ws && this.ws.readyState <= 1) this.ws.close()
    this.ws = null
    this.h.onPhase('idle')
  }

  private send(msg: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg))
  }

  private async onMessage(data: Blob | string) {
    const m = JSON.parse(typeof data === 'string' ? data : await data.text())
    if (m.setupComplete) {
      this.startMic()
      // the conversation the agent leads: the opening goes through the tool like everything else
      this.send({ clientContent: { turns: [{ role: 'user', parts: [{ text: '__start__' }] }], turnComplete: true } })
      return
    }
    if (m.toolCall) {
      for (const fc of m.toolCall.functionCalls ?? []) {
        this.h.onPhase('thinking')
        let say = 'Sorry, I could not look that up.'
        try {
          const r = await this.h.ask(String(fc.args?.text ?? this.heard))
          say = r.text
          const now: UiAction[] = []
          for (const a of r.ui_actions ?? []) {
            if (a.cue) this.cues.push({ key: norm(a.cue).split(' ').slice(0, 4).join(' '), action: a })
            else now.push(a)
          }
          if (now.length) this.h.runActions(now)
        } catch {
          /* say the apology */
        }
        this.said = ''
        this.send({ toolResponse: { functionResponses: [{ id: fc.id, name: fc.name, response: { say } }] } })
      }
      return
    }
    const sc = m.serverContent
    if (!sc) return
    if (sc.inputTranscription?.text) {
      if (this.turnDone) {
        this.heard = ''
        this.turnDone = false
      }
      this.heard += sc.inputTranscription.text
      this.h.onHeard(this.heard.trim())
    }
    for (const p of sc.modelTurn?.parts ?? []) if (p.inlineData?.data) this.play(p.inlineData.data)
    if (sc.outputTranscription?.text) {
      this.said += sc.outputTranscription.text
      this.h.onSaid(this.said.trim().split(/(?<=[.!?])\s+/).slice(-2).join(' '))
      const heardSoFar = norm(this.said)
      const due = this.cues.filter((c) => heardSoFar.includes(c.key))
      if (due.length) {
        this.cues = this.cues.filter((c) => !due.includes(c))
        this.h.runActions(due.map((c) => c.action))
      }
    }
    if (sc.interrupted) this.flushAudio()
    if (sc.turnComplete) this.turnDone = true
  }

  private startMic() {
    if (!this.mic) return
    this.inCtx = new AudioContext({ sampleRate: 16000 })
    const src = this.inCtx.createMediaStreamSource(this.mic)
    this.proc = this.inCtx.createScriptProcessor(2048, 1, 1)
    this.proc.onaudioprocess = (e) => {
      const f = e.inputBuffer.getChannelData(0)
      const pcm = new Int16Array(f.length)
      for (let i = 0; i < f.length; i++) pcm[i] = Math.max(-1, Math.min(1, f[i])) * 0x7fff
      this.send({ realtimeInput: { audio: { data: toBase64(new Uint8Array(pcm.buffer)), mimeType: 'audio/pcm;rate=16000' } } })
    }
    src.connect(this.proc)
    this.proc.connect(this.inCtx.destination)
    this.h.onPhase('listening')
  }

  private play(b64: string) {
    const ctx = this.outCtx
    if (!ctx) return
    const raw = atob(b64)
    const pcm = new Int16Array(raw.length / 2)
    for (let i = 0; i < pcm.length; i++) pcm[i] = (raw.charCodeAt(2 * i) | (raw.charCodeAt(2 * i + 1) << 8)) << 16 >> 16
    const buf = ctx.createBuffer(1, pcm.length, 24000)
    const ch = buf.getChannelData(0)
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 0x8000
    const src = ctx.createBufferSource()
    src.buffer = buf
    src.connect(ctx.destination)
    this.nextAt = Math.max(this.nextAt, ctx.currentTime + 0.02)
    src.start(this.nextAt)
    this.nextAt += buf.duration
    this.sources.push(src)
    this.h.onPhase('speaking')
    src.onended = () => {
      this.sources = this.sources.filter((s) => s !== src)
      if (!this.sources.length && !this.closed) this.h.onPhase('listening')
    }
  }

  private flushAudio() {
    for (const s of this.sources) {
      try {
        s.stop()
      } catch {
        /* already stopped */
      }
    }
    this.sources = []
    this.nextAt = 0
  }
}
