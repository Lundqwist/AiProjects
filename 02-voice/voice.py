"""Local voice assistant: speech -> faster-whisper -> Ollama -> Piper -> speaker.

Usage:
  python voice.py                      talk: Enter to start recording, Enter to stop, Ctrl+C to quit
  python voice.py --file question.wav  answer one recorded question
  python voice.py --file q.wav --out reply.wav   save the spoken reply instead of playing it

It answers in Swedish when you speak Swedish, otherwise in English, and can read your
open tasks from Vikunja when the VIKUNJA_URL and VIKUNJA_TOKEN environment variables are set.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np
import ollama
import sounddevice as sd
import soundfile as sf
from faster_whisper import WhisperModel
from piper import PiperVoice

HERE = Path(__file__).parent
CHAT_MODEL = "qwen3:8b"  # supports tool calls, and is fast enough for conversation
WHISPER_SIZE = "small"
VOICES = {"sv": "sv_SE-nst-medium", "en": "en_US-lessac-medium"}
RATE = 16000  # Whisper wants 16 kHz mono

SYSTEM = """You are a friendly voice assistant. Your answers are spoken aloud, so:
answer in 1-3 short sentences, no lists, no Markdown, no emoji, spell out symbols.
Reply in the same language as the user."""


def load_whisper() -> WhisperModel:
    try:
        return WhisperModel(WHISPER_SIZE, device="cuda", compute_type="float16")
    except Exception as e:  # missing CUDA/cuDNN DLLs: fall back to CPU
        print(f"(GPU not available for Whisper: {e}; using CPU)")
        return WhisperModel(WHISPER_SIZE, device="cpu", compute_type="int8")


def load_wav(path: str) -> np.ndarray:
    """Read a WAV file as 16 kHz mono float32, the format Whisper expects.
    (Done here so faster-whisper never needs PyAV, whose versions often don't match.)"""
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if rate != RATE:
        n = int(len(audio) * RATE / rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype("float32")
    return audio


def open_tasks() -> str:
    """Get the user's open tasks from their Vikunja to-do list."""
    url, token = os.environ.get("VIKUNJA_URL"), os.environ.get("VIKUNJA_TOKEN")
    if not (url and token):
        return "The task list is not connected (VIKUNJA_URL and VIKUNJA_TOKEN are not set)."
    req = urllib.request.Request(f"{url.rstrip('/')}/api/v1/tasks?filter=done%3Dfalse&per_page=100",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        tasks = json.load(resp) or []
    # Vikunja uses 0001-01-01 for "no due date"; put those last
    due = lambda t: (t.get("due_date") or "")[:10].replace("0001-01-01", "") or "9999"
    soonest = sorted(tasks, key=due)[:3]
    return json.dumps({
        "open_tasks": len(tasks),
        "due_soonest": [{"title": t["title"], "due": due(t) if due(t) != "9999" else "no date"} for t in soonest],
        "instruction": "Say how many open tasks there are, then name only these few in one sentence.",
    }, ensure_ascii=False)


TOOLS = {"open_tasks": open_tasks}


class Assistant:
    def __init__(self):
        t = time.time()
        self.whisper = load_whisper()
        self.voices = {lang: PiperVoice.load(HERE / "voices" / f"{name}.onnx") for lang, name in VOICES.items()}
        self.history = [{"role": "system", "content": SYSTEM}]
        print(f"Ready ({time.time() - t:.1f}s to load)")

    def transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        try:
            segments, info = self.whisper.transcribe(audio, beam_size=1, vad_filter=True)
            text = " ".join(s.text.strip() for s in segments).strip()
        except RuntimeError as e:
            # The GPU model loads fine without cuBLAS/cuDNN but fails on first use
            if "cublas" not in str(e).lower() and "cudnn" not in str(e).lower():
                raise
            print("(CUDA libraries missing for Whisper; switching to CPU. See README to enable the GPU.)")
            self.whisper = WhisperModel(WHISPER_SIZE, device="cpu", compute_type="int8")
            return self.transcribe(audio)
        lang = info.language if info.language in self.voices else "en"
        return text, lang

    def reply(self, text: str) -> str:
        self.history.append({"role": "user", "content": text})
        for _ in range(3):  # allow a tool call or two before the spoken answer
            msg = ollama.chat(model=CHAT_MODEL, messages=self.history, tools=list(TOOLS.values()),
                              think=False, options={"temperature": 0.5}).message
            self.history.append(msg)
            if not msg.tool_calls:
                return msg.content.strip()
            for call in msg.tool_calls:
                fn = TOOLS.get(call.function.name)
                try:
                    result = fn(**call.function.arguments) if fn else "Unknown tool"
                except Exception as e:
                    result = f"Tool error: {e}"
                self.history.append({"role": "tool", "content": result, "tool_name": call.function.name})
        return "Sorry, I got stuck."

    def speak(self, text: str, lang: str, out: Path | None = None) -> None:
        voice = self.voices[lang]
        chunks = list(voice.synthesize(text))
        audio = np.concatenate([c.audio_int16_array for c in chunks])
        rate = chunks[0].sample_rate
        if out:
            with wave.open(str(out), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(audio.tobytes())
        else:
            sd.play(audio, rate)
            sd.wait()

    def turn(self, audio, out: Path | None = None) -> None:
        t0 = time.time()
        text, lang = self.transcribe(audio)
        if not text:
            print("(heard nothing)")
            return
        t1 = time.time()
        print(f"You ({lang}): {text}")
        answer = self.reply(text)
        t2 = time.time()
        print(f"Assistant: {answer}")
        self.speak(answer, lang, out)
        print(f"  [speech-to-text {t1 - t0:.1f}s, answer {t2 - t1:.1f}s, voice {time.time() - t2:.1f}s]")


def record() -> np.ndarray:
    input("Press Enter and speak...")
    frames = []
    with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=lambda d, *_: frames.append(d.copy())):
        input("Recording - press Enter to stop.")
    return np.concatenate(frames).flatten() if frames else np.zeros(0, dtype="float32")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", help="answer this WAV file instead of using the microphone")
    ap.add_argument("--out", type=Path, help="save the spoken reply to this WAV file")
    args = ap.parse_args()
    bot = Assistant()
    if args.file:
        bot.turn(load_wav(args.file), args.out)
        return
    try:
        while True:
            bot.turn(record())
    except KeyboardInterrupt:
        print("\nBye")


if __name__ == "__main__":
    main()
