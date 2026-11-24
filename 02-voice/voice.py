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
import re
import time
import urllib.request
import wave
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import ollama
import sounddevice as sd
import soundfile as sf
from faster_whisper import WhisperModel
from piper import PiperVoice, SynthesisConfig

HERE = Path(__file__).parent
CHAT_MODEL = "qwen3:8b"  # supports tool calls, and is fast enough for conversation
WHISPER_SIZE = "small"
VOICES = {"sv": "sv_SE-alma-medium", "en": "en_US-amy-medium"}  # both female
RATE = 16000  # Whisper wants 16 kHz mono
CHAT_TIMEOUT = 120  # seconds for one answer from Ollama (the first one also loads the model)
MAX_HISTORY = 30  # messages kept besides the system prompt
OLD_TOOL_CHARS = 600  # earlier tool results are cut to this
STEP_NAMES = {"web_search": "Searching the web…", "read_page": "Reading a web page…",
              "read_my_cv": "Reading your CV…", "open_tasks": "Reading your tasks…",
              "ask_expert": "Asking an expert…", "create_image": "Painting a picture…",
              "create_music": "Composing music…", "write_story": "Writing a story…",
              "make_song": "Starting a song…", "make_movie": "Starting a movie…",
              "make_code_project": "Writing the files…", "change_code_project": "Changing the files…",
              "open_in_vs_code": "Opening VS Code…",
              "explain_project": "Reading the code…", "pc_info": "Looking at this PC…",
              "list_folder": "Looking in the folder…", "find_files": "Looking for files…",
              "read_text_file": "Reading the file…"}
# Small models fall back on their training ("As an AI I can't access your computer") even when a tool
# for it is offered: such an answer, given without trying a tool, is sent back once with a reminder
REFUSAL = re.compile(
    r"\b(I am|I'm) (just |only )?an? (AI|language model)\b|"
    r"\b(I )?(can ?not|can't|don't have the (ability|capability)|do not have the (ability|capability)|"
    r"am (not able|unable)|'m (not able|unable)|have no (way|access))\b[^.]{0,80}"
    r"\b(access|run|execute|interact|reach|see|list|open|read|control|browse|check|look)\b|"
    r"\b(Jag är (bara )?en AI|jag kan inte|jag har inte (möjlighet|tillgång))\b|"
    # ...or promises to do it ("Let me check your folders.") and then stops without calling a tool
    r"\b(let me|I'll|I will|I am going to|I'm going to|låt mig|jag ska)\s+(now\s+)?"
    r"(check|look|search|find|see|open|read|run|kolla|titta|söka|leta|öppna|läsa)\b", re.I)
# Refusals that are right: these are never allowed, so they are not sent back
RIGHTLY_REFUSED = re.compile(r"\b(delet|remov|eras|send|email|e-mail|messag|pay|purchas|buy|setting|password|"
                             r"ta bort|radera|skicka|betal|köp|inställning|lösenord)", re.I)
NUDGE = ("(Note from AI Workbench: you said you can't, or that you would, without calling a tool. You run on "
         "this PC and these "
         "tools are offered to you: {tools}. Use the one that fits and answer from its result. Only if none of "
         "them can do it, say so in one plain sentence, without naming tools or making any up; never say you "
         "are an AI without access.)")

SYSTEM = """You are a friendly voice assistant. Your answers are spoken aloud, so:
answer in 1-3 short sentences, no lists, no Markdown, no emoji, spell out symbols.
Reply in the same language as the user.
Only say you did something if a tool did it; if a tool says the user did not allow it, say so plainly.
You cannot delete files, send messages, make payments or change system settings: say so if asked.
Creating new files is not deleting: if a tool for making files or code is offered, use it."""


class _NoSpan:
    def set(self, **kw) -> None:
        pass


@contextmanager
def no_span(kind: str, name: str, **detail):
    """The default step recorder: does nothing. AI Workbench puts its own in (Assistant.span)."""
    yield _NoSpan()


def _stats(resp) -> dict:
    out = {"tokens_in": getattr(resp, "prompt_eval_count", None), "tokens_out": getattr(resp, "eval_count", None)}
    ed = getattr(resp, "eval_duration", None)
    if out["tokens_out"] and ed:
        out["tokens_per_s"] = round(out["tokens_out"] / (ed / 1e9), 1)
    ld = getattr(resp, "load_duration", None)
    if ld and ld > 2e8:
        out["loading_s"] = round(ld / 1e9, 1)
    return {k: v for k, v in out.items() if v}


_whisper_cache: dict[tuple[str, str], WhisperModel] = {}
_cuda_failed = False  # once the GPU has failed, never try it again in this program run


def _cpu_whisper(size: str) -> WhisperModel:
    key = (size, "cpu")
    if key not in _whisper_cache:
        _whisper_cache[key] = WhisperModel(size, device="cpu", compute_type="int8")
    return _whisper_cache[key]


def load_whisper(size: str = WHISPER_SIZE, device: str = "auto") -> WhisperModel:
    """device: 'auto' (GPU if it works, else CPU), 'cuda' or 'cpu'.

    Models are reused between assistants. Without cuBLAS/cuDNN the GPU model loads, fails on its
    first use, and a second GPU model in the same run then HANGS on first use instead of failing:
    so the GPU is tested once here, and not tried again after it failed."""
    global _cuda_failed
    if device in ("auto", "cuda") and not _cuda_failed:
        if (size, "cuda") in _whisper_cache:
            return _whisper_cache[(size, "cuda")]
        try:
            model = WhisperModel(size, device="cuda", compute_type="float16")
            list(model.transcribe(np.zeros(RATE, "float32"), beam_size=1)[0])  # fails now, not later
            _whisper_cache[(size, "cuda")] = model
            return model
        except Exception as e:  # missing CUDA/cuDNN DLLs: fall back to CPU
            _cuda_failed = True
            print(f"(GPU not available for Whisper: {str(e)[:120]}; using CPU)")
    return _cpu_whisper(size)


def mme_devices(kind: str) -> list[tuple[int, str]]:
    """Input or output devices on Windows' MME host API, which resamples for us
    (WASAPI would refuse 16 kHz recording on a 48 kHz microphone)."""
    apis = sd.query_hostapis()
    mme = next((i for i, a in enumerate(apis) if a["name"] == "MME"), 0)
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    return [(i, d["name"]) for i, d in enumerate(sd.query_devices())
            if d["hostapi"] == mme and d[key] > 0]


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
    def __init__(self, chat_model: str = CHAT_MODEL, whisper_size: str = WHISPER_SIZE,
                 whisper_device: str = "auto", voices: dict[str, str] | None = None,
                 voices_dir: Path = HERE / "voices", speed: float = 1.0, volume: float = 1.0,
                 output_device: int | None = None, extra_system: str = ""):
        t = time.time()
        self.chat_model = chat_model
        self.whisper_size = whisper_size
        self.whisper = load_whisper(whisper_size, whisper_device)
        self.voices = {lang: PiperVoice.load(Path(voices_dir) / f"{name}.onnx")
                       for lang, name in (voices or VOICES).items()}
        self.speed, self.volume, self.output_device = speed, volume, output_device
        self.history = [{"role": "system", "content": SYSTEM + ("\n" + extra_system if extra_system else "")}]
        # A timeout, so a stuck Ollama gives an error instead of waiting forever
        self.client = ollama.Client(timeout=CHAT_TIMEOUT)
        self.on_step = lambda text: None  # told about each step (e.g. "Searching the web…")
        self.last_steps: list[tuple[str, dict]] = []
        self.span = no_span  # records each model round and tool call: span(kind, name, **detail)
        print(f"Ready ({time.time() - t:.1f}s to load)")

    def reset(self) -> None:
        self.history = self.history[:1]

    def _trim_history(self) -> None:
        """Keep the conversation short: long histories (web pages, the CV) make every answer slower."""
        for m in self.history[1:]:  # earlier tool results are only needed in short
            if isinstance(m, dict) and m.get("role") == "tool" and len(m["content"]) > OLD_TOOL_CHARS:
                m["content"] = m["content"][:OLD_TOOL_CHARS] + " …(shortened)"
        if len(self.history) > MAX_HISTORY:
            rest = self.history[-MAX_HISTORY:]
            # start at a user message, so no tool result is left without the call that asked for it
            while rest and not (isinstance(rest[0], dict) and rest[0].get("role") == "user"):
                rest.pop(0)
            self.history = self.history[:1] + rest

    def _chat(self, tools: bool = True, round_no: int = 1):
        with self.span("model", self.chat_model, round=round_no, messages=len(self.history),
                       tools_offered=len(TOOLS) if tools else 0) as sp:
            resp = self.client.chat(model=self.chat_model, messages=self.history,
                                    tools=list(TOOLS.values()) if tools else None, think=False,
                                    options={"temperature": 0.5, "num_ctx": 16384})
            msg = resp.message
            sp.set(**_stats(resp), answer=(msg.content or "").strip(),
                   tool_calls=[c.function.name for c in msg.tool_calls or []])
            return msg

    def transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        try:
            segments, info = self.whisper.transcribe(audio, beam_size=1, vad_filter=True)
            text = " ".join(s.text.strip() for s in segments).strip()
        except RuntimeError as e:
            # The GPU model loads fine without cuBLAS/cuDNN but fails on first use
            if "cublas" not in str(e).lower() and "cudnn" not in str(e).lower():
                raise
            print("(CUDA libraries missing for Whisper; switching to CPU. See README to enable the GPU.)")
            global _cuda_failed
            _cuda_failed = True
            _whisper_cache.pop((self.whisper_size, "cuda"), None)
            self.whisper = _cpu_whisper(self.whisper_size)
            return self.transcribe(audio)
        lang = info.language if info.language in self.voices else "en"
        return text, lang

    def reply(self, text: str, lang: str | None = None) -> str:
        # Name the language each turn: otherwise the model keeps the conversation's earlier language
        hint = {"sv": "\n\n(Svara på svenska.)", "en": "\n\n(Reply in English.)"}.get(lang or "", "")
        self._trim_history()
        self.history.append({"role": "user", "content": text + hint})
        self.last_steps = []  # (tool, arguments) used for this answer, so the screen can say where it came from
        nudged = False
        for n in range(1, 7):  # room for search -> read a page or two -> CV before the spoken answer
            self.on_step("Thinking…")
            # web pages and the CV are long: the default context would cut off the conversation
            msg = self._chat(round_no=n)
            if not msg.tool_calls and not nudged and not self.last_steps and TOOLS \
                    and REFUSAL.search(msg.content or "") \
                    and not RIGHTLY_REFUSED.search(text + " " + (msg.content or "")):
                nudged = True  # once: a second "can't" stands, it may be true
                with self.span("guard", "Said it can't without trying a tool: asked again",
                               refused=(msg.content or "").strip()):
                    pass
                self.history.append({"role": "user", "content": NUDGE.format(tools=", ".join(TOOLS))})
                continue
            if nudged and not msg.tool_calls:  # the reminder isn't part of the conversation
                self.history = [m for m in self.history
                                if not (isinstance(m, dict) and m.get("content", "").startswith("(Note from AI Workbench"))]
            self.history.append(msg)
            if not msg.tool_calls:
                return (msg.content or "").strip()
            for call in msg.tool_calls:
                fn = TOOLS.get(call.function.name)
                args = dict(call.function.arguments or {})
                self.last_steps.append((call.function.name, args))
                self.on_step(STEP_NAMES.get(call.function.name, f"Using {call.function.name}…"))
                with self.span("tool", call.function.name, arguments=args) as sp:
                    try:
                        result = fn(**call.function.arguments) if fn else "Unknown tool"
                    except Exception as e:
                        result = f"Tool error: {e}"
                    sp.set(result=str(result))
                self.history.append({"role": "tool", "content": result, "tool_name": call.function.name})
        # Out of rounds: answer from what the tools returned instead of giving up
        self.history.append({"role": "user", "content": "Answer now from what you found, without more tools."})
        self.on_step("Writing the answer…")
        msg = self._chat(tools=False, round_no=7)
        self.history.append(msg)
        return (msg.content or "").strip() or "Sorry, I got stuck."

    def synth(self, text: str, lang: str) -> tuple[np.ndarray, int]:
        """Text to int16 audio. speed > 1 talks faster (Piper's length_scale is its inverse)."""
        voice = self.voices.get(lang) or next(iter(self.voices.values()))
        cfg = SynthesisConfig(length_scale=1.0 / max(self.speed, 0.1), volume=self.volume)
        chunks = list(voice.synthesize(text, syn_config=cfg))
        return np.concatenate([c.audio_int16_array for c in chunks]), chunks[0].sample_rate

    def speak(self, text: str, lang: str, out: Path | None = None) -> None:
        audio, rate = self.synth(text, lang)
        if out:
            with wave.open(str(out), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(audio.tobytes())
        else:
            sd.play(audio, rate, device=self.output_device)
            # Not sd.wait(): a sound device that stops answering (a Bluetooth headset going away)
            # would make it wait forever. Wait for the sound's length plus a margin, then stop.
            deadline = time.time() + len(audio) / rate + 5
            while time.time() < deadline:
                try:
                    if not sd.get_stream().active:
                        return
                except RuntimeError:  # no stream: already stopped
                    return
                time.sleep(0.05)
            try:
                sd.stop()
            except sd.PortAudioError:
                pass

    def turn(self, audio, out: Path | None = None) -> None:
        t0 = time.time()
        text, lang = self.transcribe(audio)
        if not text:
            print("(heard nothing)")
            return
        t1 = time.time()
        print(f"You ({lang}): {text}")
        answer = self.reply(text, lang)
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
