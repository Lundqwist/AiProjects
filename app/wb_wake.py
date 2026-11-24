"""Always-on wake word: listens to the microphone, and when it hears the wake word it records
what is said next and hands it over.

How it works (all on this PC):
1. A simple level detector splits the microphone stream into speech bursts (it adapts to the
   room's background noise).
2. Each burst is transcribed by Whisper "tiny" on the CPU (~0.4 s for 2 s of speech), with the
   wake word as a hotword so Whisper spells it the same way.
3. If the transcript contains the wake word (fuzzy match, so "Lona" counts as "Luna"):
   - words said after it in the same breath are the command ("Luna, put CNN on"), or
   - if nothing followed, a chime plays and the next burst is recorded as the command.
   The command's audio goes to the app, which transcribes it with its better model (tiny is good
   enough to spot a word, not for Swedish sentences).

Any word works; two or three syllables that aren't common in speech (or on TV) work best.
"""
from __future__ import annotations

import difflib
import queue
import re
import threading
import time
import unicodedata

import numpy as np

RATE = 16000
FRAME = 480  # 30 ms


SAME = {"hej": "hey", "hei": "hey", "hay": "hey", "hi": "hey", "okej": "okay", "ok": "okay"}  # sv/en greetings


def _fold(word: str) -> str:
    word = unicodedata.normalize("NFKD", word.lower())
    word = "".join(c for c in word if not unicodedata.combining(c))  # å -> a, so spelling varies less
    return SAME.get(word, word)


def _tokens(text: str) -> list[re.Match]:
    return list(re.finditer(r"[^\W_]+(?:['’][^\W_]+)?", text))  # letters/digits, keeps åäö


def find_wake_word(transcript: str, wake_word: str, threshold: float = 0.75) -> tuple[bool, str]:
    """Return (found, the original text after it)."""
    tokens = _tokens(transcript)
    words = [_fold(t.group()) for t in tokens]
    wake = [_fold(t.group()) for t in _tokens(wake_word)]
    if not words or not wake:
        return False, ""
    def similar(a: str, b: str) -> float:
        s = difflib.SequenceMatcher(None, a, b).ratio()
        if b != a and b.startswith(a):
            s -= 0.25  # a longer word that merely starts with it: "lunar" is not "luna"
        return s

    n = len(wake)
    joined = "".join(wake)
    best, best_end = 0.0, -1
    for i in range(len(words)):
        # word by word: every word must be close on its own ("the lunar" is not "hey luna")
        if i + n <= len(words):
            scores = [similar(w, h) for w, h in zip(wake, words[i:i + n])]
            score = sum(scores) / n if min(scores) >= 0.7 else 0.0
            if score > best:
                best, best_end = score, i + n
        # Whisper sometimes joins or splits the words ("heyluna", "lu na")
        for size in {max(1, n - 1), n + 1}:
            if size != n and i + size <= len(words):
                score = similar(joined, "".join(words[i:i + size]))
                if score >= 0.85 and score > best:  # stricter: joining makes loose matches easy
                    best, best_end = score, i + size
    if best < threshold:
        return False, ""
    # the original text after the wake word, with its capitals, åäö and punctuation
    rest = transcript[tokens[best_end - 1].end():]
    return True, rest.strip(" ,.!?;:-")


def chime(volume: float = 0.25) -> np.ndarray:
    """Two short rising tones: 'I'm listening'."""
    t = np.linspace(0, 0.11, int(RATE * 0.11), endpoint=False)
    fade = np.minimum(1, np.minimum(t, t[::-1]) * 60)
    tone = lambda f: np.sin(2 * np.pi * f * t) * fade  # noqa: E731
    return (np.concatenate([tone(880), np.zeros(int(RATE * 0.03)), tone(1320)]) * volume).astype("float32")


class WakeListener:
    """Feed it 16 kHz mono float32 audio (from the microphone or a file).

    Callbacks (called from the listener's own thread):
      on_state(text)                        what it is doing, for the UI
      on_wake()                             the wake word was heard alone: chime, a command follows
      on_command_audio(audio, quick_rest)   the command's audio. quick_rest is "" for a command said
                                            after the wake word; when it was said in the same breath,
                                            it's tiny's text after the wake word, and the audio still
                                            holds the wake word (remove it with find_wake_word, or
                                            fall back to quick_rest if the better model misses it)
      on_heard(text)                        (optional) every transcript, for debugging
    The listener pauses itself before on_command_audio; call resume() when the answer is done.
    """

    def __init__(self, wake_word: str, on_state, on_wake, on_command_audio,
                 on_heard=None, whisper_size: str = "tiny", device: int | None = None,
                 log_path=None):
        self.wake_word = wake_word
        self.log_path = log_path
        # Conversation mode: every phrase is a command, no wake word needed. Ends (on_idle) after
        # idle_limit seconds with nothing said.
        self.conversation = False
        self.idle_limit = 60
        self.on_idle = lambda: None
        self._last_activity = time.time()
        self._ignore_until = 0.0
        self._frames_seen = 0
        self._peak_since_log = 0.0
        self._last_heartbeat = time.time()
        self.on_state, self.on_wake = on_state, on_wake
        self.on_command_audio = on_command_audio
        self.on_heard = on_heard or (lambda t: None)
        self.whisper_size, self.device = whisper_size, device
        self.model = None
        self.mode = "wake"            # wake: waiting for the word; command: recording the command
        self.paused = False
        self.noise = 0.003            # running estimate of the room's background level
        self.level = 0.0              # last frame's level, for a meter
        self._segment: list[np.ndarray] = []
        self._preroll: list[np.ndarray] = []
        self._speech_frames = self._silent_frames = 0
        self._in_speech = False
        self._command_deadline = 0.0
        self._jobs: queue.Queue = queue.Queue(maxsize=4)
        self._stream = None
        self._worker = None
        self._stop = threading.Event()

    # ---- diagnostics --------------------------------------------------------------------
    def log(self, msg: str) -> None:
        """A small diagnostic log: what the listener heard and did (text only, never audio)."""
        if not self.log_path:
            return
        try:
            from pathlib import Path
            p = Path(self.log_path)
            if p.exists() and p.stat().st_size > 512_000:  # keep it small
                p.replace(p.with_suffix(".old.log"))
            with p.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
        except OSError:
            pass

    # ---- life cycle -----------------------------------------------------------------------
    def load(self):
        if self.model is None:
            from faster_whisper import WhisperModel
            self.model = WhisperModel(self.whisper_size, device="cpu", compute_type="int8")

    def start(self, use_microphone: bool = True):
        self.load()
        self._stop.clear()
        self._worker = threading.Thread(target=self._work, daemon=True, name="wake-word")
        self._worker.start()
        if use_microphone:
            import sounddevice as sd
            name = sd.query_devices(self.device if self.device is not None else sd.default.device[0])["name"]
            self.log(f"start: wake word '{self.wake_word}', microphone {self.device} ({name})")
            self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=FRAME,
                                          device=self.device, callback=self._callback)
            self._stream.start()
        self._set_mode("wake")

    def _callback(self, data, frames, time_info, status):
        # An exception here would silently stop the microphone stream: log it and carry on
        try:
            if status:
                self.log(f"microphone status: {status}")
            # Copy: sounddevice reuses this buffer for the next block, so keeping a view of it
            # left every recorded phrase overwritten (silent) by the time it was transcribed
            self.feed(data[:, 0].copy())
        except Exception as e:  # noqa: BLE001
            self.log(f"error in microphone callback: {type(e).__name__}: {e}")

    def stop(self):
        self._stop.set()
        if self._stream:
            stream, self._stream = self._stream, None
            for step in (stream.stop, stream.close):
                try:  # PortAudio sometimes says "Invalid stream pointer" here; stop anyway
                    step()
                except Exception as e:  # noqa: BLE001
                    self.log(f"stop: {type(e).__name__}: {e}")
        try:
            self._jobs.put_nowait(None)
        except queue.Full:
            pass

    def pause(self):
        """While the assistant thinks and talks: don't listen to it (or to the room)."""
        self.paused = True
        self._reset_segment()

    def resume(self):
        while not self._jobs.empty():  # speech from while we were busy is old news
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                break
        self.paused = False
        self._reset_segment()
        self._last_activity = time.time()
        self._ignore_until = time.time() + 0.5  # the tail of our own voice in the room
        self._set_mode("wake")

    def set_conversation(self, on: bool) -> None:
        self.conversation = on
        self._last_activity = time.time()
        self._reset_segment()
        self._set_mode("wake")

    # ---- audio --------------------------------------------------------------------------
    def feed(self, audio: np.ndarray):
        audio = np.array(audio, dtype="float32", copy=True)  # never keep views of a caller's buffer
        for i in range(0, len(audio) - FRAME + 1, FRAME):
            self._frame(audio[i:i + FRAME])

    def _frame(self, f: np.ndarray):
        rms = float(np.sqrt(np.mean(f * f)))
        self.level = rms
        self._frames_seen += 1
        self._peak_since_log = max(self._peak_since_log, rms)
        if time.time() - self._last_heartbeat > 60:  # proof of life, once a minute
            self.log(f"alive: {self._frames_seen} frames, loudest {self._peak_since_log:.4f}, "
                     f"noise {self.noise:.4f}, mode {self.mode}, paused {self.paused}")
            self._last_heartbeat, self._peak_since_log = time.time(), 0.0
        if self.paused or time.time() < self._ignore_until:
            return
        if (self.conversation and not self._in_speech
                and time.time() - self._last_activity > self.idle_limit):
            self.log(f"conversation ended: {self.idle_limit} s without speech")
            self._last_activity = time.time()
            self.on_idle()
            return
        if self.mode == "command" and not self._in_speech and time.time() > self._command_deadline:
            self.on_state("Didn't hear a command")
            self._set_mode("wake")
        speech = rms > max(self.noise * 3.0, 0.008)
        if not self._in_speech:
            self.noise = 0.95 * self.noise + 0.05 * rms if not speech else self.noise
            self._preroll = (self._preroll + [f])[-10:]  # 0.3 s before speech started
            self._speech_frames = self._speech_frames + 1 if speech else 0
            if self._speech_frames >= 3:
                self._in_speech, self._segment, self._silent_frames = True, list(self._preroll), 0
            return
        self._segment.append(f)
        self._silent_frames = 0 if speech else self._silent_frames + 1
        talk = self.conversation or self.mode == "command"
        needed_silence = 1.0 if talk else 0.6  # seconds of quiet that end a phrase
        max_len = (15 if self.conversation else 8) if talk else 7  # keeps TV talk from piling up
        length = len(self._segment) * FRAME / RATE
        if self._silent_frames * FRAME / RATE >= needed_silence or length >= max_len:
            audio = np.concatenate(self._segment)
            cut = length >= max_len
            self._reset_segment()
            if length >= (0.6 if self.conversation else 0.35):  # a cough isn't a question
                self._last_activity = time.time()
                try:
                    self._jobs.put_nowait((self.mode, audio, cut))
                except queue.Full:
                    self.log("busy: skipped a burst of speech")  # still working on earlier speech

    def _reset_segment(self):
        self._segment, self._preroll = [], []
        self._in_speech, self._speech_frames, self._silent_frames = False, 0, 0

    def _set_mode(self, mode: str):
        self.mode = mode
        if mode == "command":
            self._command_deadline = time.time() + 6
            self.on_state("Listening for your command…")
        elif self.conversation:
            self.on_state("Conversation: listening… just talk")
        else:
            self.on_state(f"Waiting for “{self.wake_word}”")

    # ---- recognition --------------------------------------------------------------------
    def transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        segments, info = self.model.transcribe(audio, beam_size=1, vad_filter=True,
                                               hotwords=self.wake_word, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip(), info.language

    def _work(self):
        while not self._stop.is_set():
            job = self._jobs.get()
            if job is None:
                return
            try:
                self._handle(*job)
            except Exception as e:  # noqa: BLE001 - one bad burst must not end the listening
                self.log(f"error: {type(e).__name__}: {e}")
                self.paused = False
                self._set_mode("wake")

    def _handle(self, mode, audio, cut):
        peak = float(np.abs(audio).max())
        self.log(f"speech burst: {len(audio) / RATE:.1f}s, peak {peak:.3f}, mode {mode}"
                 f"{', cut' if cut else ''}{', ignored (paused)' if self.paused else ''}")
        if self.paused:
            return
        if self.conversation:
            self.log("conversation: a phrase (no wake word needed)")
            self.pause()
            self.on_command_audio(audio, "")
            return
        if mode == "command":
            self.log("command recorded after the wake word")
            self.pause()  # the app resumes us when it has answered
            self.on_command_audio(audio, "")
            return
        text, _lang = self.transcribe(audio)
        if text:
            self.on_heard(text)
        found, rest = find_wake_word(text, self.wake_word)
        if not found:
            # Room talk is never written down: only that something was said, and how much
            self.log(f"no wake word ({len(text.split())} words)")
            return
        self.log(f"heard: {text!r}")
        if len(rest.split()) >= 2:
            # Said in one breath ("Hey Luna, put CNN on"). Hand over the audio, not tiny's text:
            # the app re-transcribes it with its better model and removes the wake word.
            self.log(f"wake word + command in one breath: {rest!r}")
            self.pause()
            self.on_command_audio(audio, rest)
            return
        self.log("wake word alone: waiting for the command")
        self.on_wake()
        # Recognizing the wake word takes ~0.5 s; a command that started meanwhile is already
        # queued as ordinary speech. It's the command.
        try:
            queued = self._jobs.get_nowait()
        except queue.Empty:
            queued = None
        if queued:
            self.log("command was already spoken: using it")
            self.pause()
            self.on_command_audio(queued[1], "")
            return
        self._set_mode("command")  # speech that is still going on, or starts soon, is the command
