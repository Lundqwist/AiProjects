"""Voice tab: push-to-talk conversation with the local voice assistant."""
from __future__ import annotations

import html
import re
import time

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
                               QPushButton, QTextBrowser, QVBoxLayout, QWidget)

import voice
import wb_access
import wb_spotify
import wb_spotify_tools
import wb_tv_tools
import wb_wake
import wb_web_tools
from wb_common import run_in_background
from wb_settings import SETTINGS_DIR, Settings

RATE = 16000

MODES = {  # mode: (button label, what it does)
    "wake": ("Hey Luna", "Listening for “{word}”. Say it, then your question, e.g. “{word}, open Spotify”."),
    "conversation": ("Conversation", "Just talk: every sentence is a question, no wake word. Ends after "
                                     "{idle} s of silence, or when you say “stop listening” / “sluta lyssna”."),
    "hold": ("Hold to talk", "Not listening in the background. Hold the big button while you speak."),
}
END_PHRASES = ("stop listening", "end conversation", "end the conversation", "that's all", "that is all",
               "sluta lyssna", "avsluta samtalet", "det var allt", "tack det var allt")
EXTRA_SYSTEM = """You CAN search the web: use web_search, then read_page on the best results, for anything
current (news, prices, job listings). You CAN read the user's CV with read_my_cv. For job hunting, read the CV
first, then search for matching jobs and name the best two or three with company and why they fit.
You cannot change the CV itself; to keep a job for later, offer to save it with add_task in the project
"Remote job search" (title: job and company, with the link). Never claim you can't search the web."""
STALL_SECONDS = 60   # one step taking longer than this turns the bar red
TURN_LIMIT = 240     # a question still not done after this is given up by itself
BAR_OK = "QProgressBar::chunk { background: #d4af37; }"
BAR_STALLED = "QProgressBar::chunk { background: #ff6b6b; }"
PHANTOM_PHRASES = {"thank you", "thanks for watching", "thank you for watching", "you", "bye", "tack",
                   "tack för att du tittade", "tack för att ni tittade", "textning", "musik", "music"}


SWEDISH = {"jag", "är", "och", "vad", "hur", "det", "inte", "min", "mina", "har", "kan", "på", "ett", "du",
            "som", "att", "med", "för", "den", "nu", "här", "till", "av", "om", "så", "huvudstaden", "tv:n"}
ENGLISH = {"the", "is", "and", "you", "what", "how", "it", "not", "my", "have", "can", "on", "a", "to", "of",
           "now", "here", "with", "for", "this", "that", "are", "has", "your", "currently"}


def guess_language(text: str, default: str = "en") -> str:
    """'sv' or 'en' from common words and å/ä/ö; default when it can't tell."""
    words = set(text.lower().replace("?", " ").replace(",", " ").replace(".", " ").replace("!", " ").split())
    sv = len(words & SWEDISH) + (2 if any(c in text.lower() for c in "åäö") else 0)
    en = len(words & ENGLISH)
    if sv == en:
        return default
    return "sv" if sv > en else "en"


def for_speech(text: str) -> str:
    """Web answers come with Markdown and links: keep the link text, drop URLs and symbols."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\(?https?://\S+\)?", "", text)
    return re.sub(r"[*#`_]+", "", text).strip()


def stop_sound() -> None:
    "Stop any playing sound. sounddevice raises if the last sound's stream is already gone."
    try:
        sd.stop()
    except sd.PortAudioError:
        pass


class VoiceTab(QWidget):
    def __init__(self, settings: Settings, status):
        super().__init__()
        self.s = settings
        self.status = status
        self.bot: voice.Assistant | None = None
        self.loading = False
        self.stream = None
        self.frames: list[np.ndarray] = []
        self.level = 0.0
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)  # one turn at a time
        self.turns: list[tuple[str, str, str]] = []  # (who, text, info)

        title = QLabel("Voice")
        title.setObjectName("title")
        self.hint = QLabel()
        self.hint.setObjectName("muted")

        self.talk = QPushButton("Hold to talk")
        self.talk.setObjectName("primary")
        self.talk.setMinimumHeight(56)
        self.talk.pressed.connect(self._pressed)
        self.talk.released.connect(self._released)
        self.talk.clicked.connect(self._clicked)

        self.meter = QProgressBar(maximum=100, textVisible=False)
        self.meter.setFixedHeight(12)
        self.state = QLabel("Loading speech models when you first talk…")
        self.state.setObjectName("muted")

        # Busy bar: moves while a question is being worked on, with the step and a timer, so it's
        # clear whether the assistant is working or stuck. Turns red when a step takes too long.
        self.busy = QProgressBar(textVisible=False)
        self.busy.setRange(0, 0)  # moving back and forth: how long a step takes isn't known
        self.busy.setFixedHeight(14)
        self.busy.hide()
        self.cancel_btn = QPushButton("Stop waiting")
        self.cancel_btn.setToolTip("Give up on this question and listen again")
        self.cancel_btn.clicked.connect(lambda: self.abandon_turn("Stopped. Ask again when you like."))
        self.cancel_btn.hide()
        self.turn_id = 0          # increases with each question
        self.abandoned_upto = 0   # questions up to this one are given up: their late results are ignored
        self.pending = 0          # questions being worked on or waiting
        self.is_busy = False
        self.step_text = ""
        self.turn_started = self.step_started = 0.0
        self._old_pools: list[QThreadPool] = []  # a stuck thread keeps its pool alive
        self.busy_timer = QTimer(self, interval=500, timeout=self._tick_busy)

        stop = QPushButton("Stop speaking")
        stop.clicked.connect(stop_sound)
        new = QPushButton("New conversation")
        new.clicked.connect(self.new_conversation)
        buttons = QHBoxLayout()
        buttons.addWidget(self.talk, 1)
        buttons.addWidget(stop)
        buttons.addWidget(new)

        # Wake word
        self.wake: wb_wake.WakeListener | None = None
        self.wake_uses_microphone = True  # the self-test feeds audio itself
        self.wake_signals = _WakeSignals()
        self.wake_signals.state.connect(self._wake_state)
        self.wake_signals.wake.connect(self._wake_heard)
        self.wake_signals.command_audio.connect(
            lambda audio, quick_rest: self.run_turn(audio=audio, strip_wake=bool(quick_rest),
                                                    fallback=quick_rest))
        self.wake_signals.heard.connect(lambda t: self.heard_label.setText(f"Last heard: “{t[:120]}”"))
        self.wake_signals.idle.connect(self._conversation_idle)
        self.mode = "hold"
        self.mode_btn = QPushButton()
        self.mode_btn.setMinimumHeight(40)
        self.mode_btn.setMinimumWidth(260)
        self.mode_btn.setToolTip("Click (or press F2) to switch: Hey Luna → Conversation → Hold to talk")
        self.mode_btn.setStyleSheet("QPushButton { font-size: 11pt; font-weight: 600; border: 2px solid #d4af37; }")
        self.mode_btn.clicked.connect(self.next_mode)
        QShortcut(QKeySequence("F2"), self, activated=self.next_mode)
        self.wake_state = QLabel("")
        self.mode_help = QLabel("")
        self.mode_help.setObjectName("muted")
        self.heard_label = QLabel("")
        self.heard_label.setObjectName("muted")
        wake_row = QHBoxLayout()
        wake_row.addWidget(self.mode_btn)
        state_col = QVBoxLayout()
        state_col.addWidget(self.wake_state)
        state_col.addWidget(self.mode_help)
        wake_row.addLayout(state_col, 1)

        self.view = QTextBrowser()
        self.typed = QLineEdit()
        self.typed.setPlaceholderText("…or type a message and press Enter (the answer is still spoken)")
        self.typed.returnPressed.connect(self.send_typed)

        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(self.hint)
        lay.addLayout(wake_row)
        lay.addWidget(self.heard_label)
        lay.addLayout(buttons)
        lay.addWidget(self.meter)
        busy_row = QHBoxLayout()
        busy_row.addWidget(self.busy, 1)
        busy_row.addWidget(self.cancel_btn)
        lay.addLayout(busy_row)
        lay.addWidget(self.state)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.typed)

        self.timer = QTimer(self, interval=50, timeout=self._update_meter)
        self.apply_settings()
        self.render()
        self._show_mode(self.s.voice_mode if self.s.voice_mode in MODES else "hold")
        if self.s.voice_mode in ("wake", "conversation"):  # start in the mode used last
            QTimer.singleShot(2000, lambda: self.set_mode(self.s.voice_mode))

    def _update_meter(self):
        # push-to-talk keeps a 0-1 level; the wake listener keeps the raw RMS
        level = self.level if self.stream else min(1.0, (self.wake.level if self.wake else 0) * 8)
        self.meter.setValue(int(level * 100))

    # ---- modes: Hey Luna / Conversation / Hold to talk ------------------------------------
    def next_mode(self):
        order = list(MODES)
        self.set_mode(order[(order.index(self.mode) + 1) % len(order)])

    def _show_mode(self, mode: str):
        self.mode = mode
        label, help_text = MODES[mode]
        self.mode_btn.setText(f"Mode: {label}   ⟳")
        self.mode_help.setText(help_text.format(word=self.s.wake_word, idle=self.s.conversation_idle))

    def set_mode(self, mode: str):
        """Switch how the assistant listens. The choice is remembered for the next start."""
        self._show_mode(mode)
        if self.s.voice_mode != mode:
            self.s.voice_mode = mode
            try:
                self.s.save()
            except OSError:
                pass
        if mode == "hold":
            self._stop_listener()
            self.wake_state.setText("<span style='color:#9fb0cc'>●</span> Not listening in the background")
            return
        if self.wake:  # already listening: just switch between wake word and conversation
            self.wake.idle_limit = self.s.conversation_idle
            if self.wake.paused:
                self.wake.conversation = mode == "conversation"  # takes effect when the answer is done
            else:
                self.wake.set_conversation(mode == "conversation")
            return
        self._start_listener()

    def _stop_listener(self):
        if self.wake:
            self.wake.stop()
            self.wake = None
        self.heard_label.setText("")
        if not self.stream:
            self.timer.stop()
            self.meter.setValue(0)

    def _start_listener(self):
        sig = self.wake_signals
        listener = wb_wake.WakeListener(
            self.s.wake_word, on_state=sig.state.emit, on_wake=sig.wake.emit,
            on_command_audio=sig.command_audio.emit, on_heard=sig.heard.emit, device=self.s.input_device,
            log_path=SETTINGS_DIR / "wake.log")
        listener.on_idle = sig.idle.emit
        listener.idle_limit = self.s.conversation_idle
        listener.conversation = self.mode == "conversation"
        self.wake_state.setText("Starting to listen…")
        use_mic = self.wake_uses_microphone

        def start(emit):
            listener.start(use_microphone=use_mic)
            return listener

        def started(l):
            if self.mode in ("wake", "conversation") and self.wake is None:
                self.wake = l
                l.set_conversation(self.mode == "conversation")
                self.timer.start()
            else:
                l.stop()  # the mode changed while it was starting

        def failed(e):
            self._show_mode("hold")
            self.wake_state.setText(f"<span style='color:#ff8a80'>Could not listen: {e}</span>")

        run_in_background(start, on_result=started, on_error=failed)

    def _conversation_idle(self):
        if self.mode == "conversation":
            self.set_mode("wake")
            self.wake_state.setText(f"<span style='color:#9fb0cc'>●</span> Conversation ended after "
                                    f"{self.s.conversation_idle} s of silence. Say “{self.s.wake_word}” "
                                    "to talk again.")

    def _wake_state(self, text: str):
        if self.mode == "hold":
            return
        colour = "#d4af37" if "command" in text else "#9be59b"
        self.wake_state.setText(f"<span style='color:{colour}'>●</span> {text}")

    def _wake_heard(self):
        if self.s.wake_chime:
            sd.play(wb_wake.chime(), wb_wake.RATE, device=self.s.output_device)

    def _resume_wake(self):
        if self.wake:
            self.wake.resume()

    # ---- settings -----------------------------------------------------------------------
    def apply_settings(self):
        """Called after Settings are saved: the models are reloaded on the next turn."""
        self.bot = None
        self._sync_tools()
        ptt = self.s.push_to_talk
        self.talk.setCheckable(not ptt)
        self.talk.setText("Hold to talk" if ptt else "Click to talk")
        if hasattr(self, "mode_btn"):
            self._show_mode(self.mode)
        if self.wake and (self.wake.wake_word != self.s.wake_word or self.wake.device != self.s.input_device):
            self._stop_listener()  # a new word or microphone: listen again with it
            self._start_listener()
        elif self.wake:
            self.wake.idle_limit = self.s.conversation_idle
        tv = " Say \"turn on CNN\" or \"turn the volume down\" to run the TV." if self.s.tv_ip else ""
        if wb_spotify.connected():
            tv += " Say \"play country music\" or \"next song\" for Spotify."
        self.hint.setText("Speak English or Swedish; the reply comes in the same language. "
                          f"Ask \"what's on my to-do list?\" to hear your Vikunja tasks.{tv} "
                          f"Model: {self.s.agent_model} · speech recognition: Whisper {self.s.whisper_size}")

    def _sync_tools(self):
        """TV tools are offered to the model only when a TV is set up (Find TV can happen any time)."""
        wb_tv_tools.configure(self.s)
        wb_spotify_tools.configure(self.s)
        voice.TOOLS.update(wb_access.TOOLS)  # programs, status, tasks, containers, media (wb_access)
        voice.TOOLS.update(wb_web_tools.TOOLS)  # web search, read a page, read the CV (read-only)
        for tools, on in ((wb_tv_tools.TOOLS, bool(self.s.tv_ip)),
                          (wb_spotify_tools.TOOLS, wb_spotify.connected())):
            if on:
                voice.TOOLS.update(tools)
            else:
                for name in tools:
                    voice.TOOLS.pop(name, None)

    def _ensure_bot(self, emit):
        self._sync_tools()
        if self.bot is None:
            emit("Loading speech recognition and voices (first time only)…")
            s = self.s
            self.bot = voice.Assistant(chat_model=s.agent_model, whisper_size=s.whisper_size,
                                       whisper_device=s.whisper_device,
                                       voices={"en": s.voice_en, "sv": s.voice_sv},
                                       voices_dir=s.voices_dir, speed=s.speech_speed,
                                       volume=s.speech_volume, output_device=s.output_device,
                                       extra_system=EXTRA_SYSTEM)
        return self.bot

    # ---- recording ----------------------------------------------------------------------
    def _pressed(self):
        if self.s.push_to_talk:
            self.start_recording()

    def _released(self):
        if self.s.push_to_talk:
            self.stop_recording()

    def _clicked(self):
        if self.s.push_to_talk:
            return
        if self.stream:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        stop_sound()  # don't record the assistant talking
        if self.wake:
            self.wake.pause()  # the button is in charge now
        self.frames = []

        def cb(data, *_):
            self.frames.append(data.copy())
            self.level = min(1.0, float(np.sqrt(np.mean(data ** 2))) * 8)

        try:
            self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                                         device=self.s.input_device, callback=cb)
            self.stream.start()
        except Exception as e:
            self.stream = None
            QMessageBox.warning(self, "Microphone", f"Could not open the microphone: {e}")
            return
        self.timer.start()
        self.state.setText("Listening…")
        self.talk.setText("Listening… release to send" if self.s.push_to_talk else "Listening… click to send")

    def stop_recording(self):
        if not self.stream:
            return
        self.stream.stop()
        self.stream.close()
        self.stream = None
        self.timer.stop()
        self.level = 0
        self.meter.setValue(0)
        self.talk.setChecked(False)
        self.talk.setText("Hold to talk" if self.s.push_to_talk else "Click to talk")
        audio = np.concatenate(self.frames).flatten() if self.frames else np.zeros(0, "float32")
        if len(audio) < RATE * 0.3:
            self.state.setText("Too short: hold the button while you speak.")
            self._resume_wake()
            return
        self.run_turn(audio=audio)

    # ---- a turn -------------------------------------------------------------------------
    def send_typed(self):
        text = self.typed.text().strip()
        if text:
            self.typed.clear()
            self.run_turn(text=text)

    def run_turn(self, audio: np.ndarray | None = None, text: str | None = None, lang: str | None = None,
                 strip_wake: bool = False, fallback: str = ""):
        speak = self.s.speak_replies
        wake_word = self.s.wake_word
        if self.wake:
            self.wake.pause()  # don't listen to ourselves while thinking and talking
        self.turn_id += 1
        my_turn = self.turn_id
        stale = lambda: my_turn <= self.abandoned_upto  # noqa: E731 - given up: ignore what it sends
        self.pending += 1
        self._start_busy("Waiting for the previous question…" if self.pending > 1 else "Starting…")

        def work(emit):
            emit("begin")
            bot = self._ensure_bot(emit)
            bot.on_step = emit  # "Thinking…", "Searching the web…", …
            t0 = time.time()
            if audio is not None:
                emit("Transcribing…")
                said, lang = bot.transcribe(audio)
                if strip_wake:  # "Hey Luna, put CNN on" -> "put CNN on"
                    found, rest = wb_wake.find_wake_word(said, wake_word)
                    # If the better model misheard the wake word (TV talk around it), it can't tell
                    # where the command starts: then the quick model's words after it are safer
                    said = rest if found else (fallback or said)
                if not said:
                    return None
                if in_conversation:
                    clean = said.lower().strip(" .!?,")
                    if clean in PHANTOM_PHRASES:
                        return None  # Whisper "hears" these in noise; not worth an answer
                    if any(p in clean for p in END_PHRASES):
                        emit(("you", said, lang))
                        emit("end")
                        return "ended"
            else:
                said = text
                lang = known_lang or guess_language(said, "en")
            emit(("you", said, lang))
            t1 = time.time()
            emit("Thinking…")
            answer = bot.reply(said, lang)
            t2 = time.time()
            emit(("bot", answer, f"speech-to-text {t1 - t0:.1f}s · answer {t2 - t1:.1f}s"))
            if speak and answer:
                emit("Speaking…")
                # Speak with the voice that fits the answer itself: a short English command can be
                # mistaken for Swedish ("start TV and CNN" -> "StartTV och CNN")
                bot.speak(for_speech(answer), guess_language(answer, lang))
            return True

        def progress(item):
            if stale():
                return
            if isinstance(item, tuple):
                self.turns.append(item)
                self.render()
            elif item == "begin":
                self.turn_started = time.time()  # the timer counts from when work really starts
                self._set_step("Starting…")
            elif item == "end":
                self.set_mode("wake")
                self.wake_state.setText(f"<span style='color:#9fb0cc'>●</span> Conversation ended. "
                                        f"Say “{self.s.wake_word}” to talk again.")
            else:
                self._set_step(item)

        def failed(e):
            if not stale():
                self.state.setText(f"Error: {e}")

        def finished():
            if stale():
                return
            self.pending = max(0, self.pending - 1)
            if self.pending == 0:
                self._stop_busy()
            self._resume_wake()

        def done(result):
            if stale():
                return
            if result == "ended":
                self.state.setText("Conversation ended")
            elif result:
                self.state.setText("Ready")
            elif in_conversation:
                self.state.setText("Listening…")
            else:
                self.state.setText("Heard nothing. Check the microphone in Settings.")

        known_lang = lang
        in_conversation = self.mode == "conversation" and audio is not None and not strip_wake
        run_in_background(work, on_progress=progress, on_result=done, on_error=failed,
                          on_done=finished, pool=self.pool)

    # ---- busy bar -------------------------------------------------------------------------
    def _start_busy(self, text: str):
        # A flag, not isVisible(): that is False whenever another tab is showing
        if not self.is_busy:
            self.is_busy = True
            self.turn_started = time.time()
            self.busy.setStyleSheet(BAR_OK)
            self.busy.show()
            self.cancel_btn.show()
            self.busy_timer.start()
        self._set_step(text)

    def _set_step(self, text: str):
        self.step_text = text
        self.step_started = time.time()
        self.busy.setStyleSheet(BAR_OK)
        self._tick_busy()

    def _stop_busy(self):
        self.is_busy = False
        self.busy_timer.stop()
        self.busy.hide()
        self.cancel_btn.hide()

    def _tick_busy(self):
        if not self.is_busy:
            return
        now = time.time()
        total, step = now - self.turn_started, now - self.step_started
        if total > TURN_LIMIT:
            self.abandon_turn(f"No answer after {TURN_LIMIT // 60} minutes, so I gave up on that question. "
                              "Ask again.")
            return
        if step > STALL_SECONDS:
            self.busy.setStyleSheet(BAR_STALLED)
            self.state.setText(f"<span style='color:#ff8a80'>{html.escape(self.step_text)} No progress for "
                               f"{step:.0f} s: it may be stuck. Click Stop waiting to start over.</span>")
        else:
            self.state.setText(f"{html.escape(self.step_text)}  ({total:.0f} s)")

    def abandon_turn(self, message: str):
        """Give up on the question being worked on. A stuck thread can't be stopped, so its results
        are ignored and new questions go to a fresh worker (and a freshly loaded assistant)."""
        if self.pending == 0:
            return
        self.abandoned_upto = self.turn_id
        self.pending = 0
        stop_sound()
        self.pool.clear()  # questions still waiting in line are dropped too
        self._old_pools.append(self.pool)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.bot = None  # the stuck thread may still be using the old one
        self._stop_busy()
        self.state.setText(message)
        self._resume_wake()

    def new_conversation(self):
        stop_sound()
        if self.bot:
            self.bot.reset()
        self.turns.clear()
        self.render()

    def render(self):
        if not self.turns:
            self.view.setHtml("<p style='color:#9fb0cc'>Hold the button and talk, or type below.</p>")
            return
        parts = []
        for who, text, info in self.turns:
            if who == "you":
                parts.append(f"<p style='color:#d4af37'><b>You</b> <small>({info})</small>: {html.escape(text)}</p>")
            else:
                parts.append(f"<p><b>Assistant:</b> {html.escape(text)}<br>"
                             f"<small style='color:#9fb0cc'>{info}</small></p>")
        self.view.setHtml("".join(parts))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def shutdown(self):
        # Each step on its own: a microphone that fails to stop mustn't keep the rest running
        for step in ((lambda: self.wake.stop()) if self.wake else None,
                     (lambda: self.stream.close()) if self.stream else None,
                     stop_sound):
            if step:
                try:
                    step()
                except Exception as e:  # noqa: BLE001
                    print(f"voice shutdown: {type(e).__name__}: {e}")
        self.pool.clear()
        for pool in self._old_pools:
            pool.clear()


class _WakeSignals(QObject):
    """Carries the listener's events (from its own thread) to the GUI thread."""
    state = Signal(str)
    wake = Signal()
    command_audio = Signal(object, str)
    heard = Signal(str)
    idle = Signal()
