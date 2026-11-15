"""Open-source speech, running on this machine: nothing spoken or heard leaves it.

- Hearing: Whisper (OpenAI's open-source model) through faster-whisper, model "base.en" on the CPU.
- Speaking: Piper (open-source neural text-to-speech), voice en_US-lessac-medium.

Audio from the browser (webm/opus, mp4, wav) is decoded with ffmpeg. Models load on first use.
Voice files live in data/speech (see README); if anything is missing, the endpoints return 503 and the
web app falls back to the browser's own speech.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import threading
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
VOICE = ROOT / "data" / "speech" / "en_US-lessac-medium.onnx"
WHISPER_MODEL = "base.en"
# words Whisper should expect, so place names and our vocabulary come out right
HINT = ("NavEasy. Houston, Midtown, Montrose, Downtown, Medical Center, Heights, Galleria, Los Angeles, Koreatown. "
        "Wheelchair, blind, low vision, deaf, hard of hearing, older adult. Apartment, pharmacy, clinic, grocery, bus stop, "
        "doorway, bathroom, bedroom, neighborhood, route, inside, next, repeat.")

_lock = threading.Lock()
_whisper = None
_piper = None


def available() -> dict:
    ok_tts = VOICE.exists() and VOICE.with_suffix(".onnx.json").exists()
    try:
        import faster_whisper  # noqa: F401
        ok_stt = bool(shutil.which("ffmpeg"))
    except ImportError:
        ok_stt = False
    try:
        import piper  # noqa: F401
    except ImportError:
        ok_tts = False
    return {"listen": ok_stt, "speak": ok_tts, "listen_model": f"whisper {WHISPER_MODEL} (faster-whisper)", "speak_model": "piper en_US-lessac-medium"}


def _decode(audio: bytes) -> np.ndarray:
    """Any browser recording -> 16 kHz mono float32, via ffmpeg."""
    p = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", "pipe:0", "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
                       input=audio, capture_output=True, timeout=60)
    if p.returncode != 0 or not p.stdout:
        raise ValueError(f"couldn't decode the recording: {p.stderr.decode()[:160]}")
    return np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768.0


def transcribe(audio: bytes) -> dict:
    global _whisper
    samples = _decode(audio)
    if len(samples) < 16000 * 0.25:
        return {"text": "", "seconds": round(len(samples) / 16000, 2)}
    with _lock:
        if _whisper is None:
            from faster_whisper import WhisperModel
            _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        segments, _info = _whisper.transcribe(samples, beam_size=2, vad_filter=True, initial_prompt=HINT, condition_on_previous_text=False)
        text = " ".join(s.text.strip() for s in segments).strip()
    return {"text": text, "seconds": round(len(samples) / 16000, 2)}


def say(text: str, speed: float = 1.0) -> bytes:
    """Text -> WAV bytes."""
    global _piper
    with _lock:
        if _piper is None:
            from piper import PiperVoice
            _piper = PiperVoice.load(str(VOICE))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            try:
                from piper import SynthesisConfig
                _piper.synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=1.0 / max(0.5, min(2.0, speed))))
            except ImportError:
                _piper.synthesize_wav(text, w)
    return buf.getvalue()


def warm() -> None:
    """Load both models in the background so the first request isn't slow."""
    def run():
        try:
            a = available()
            if a["speak"]:
                wav = say("Ready.")
                if a["listen"]:
                    transcribe(wav)
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=run, daemon=True).start()
