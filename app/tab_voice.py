"""Voice tab: push-to-talk conversation with the local voice assistant."""
from __future__ import annotations

import html
import time

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
                               QPushButton, QTextBrowser, QVBoxLayout, QWidget)

import voice
import wb_tv_tools
import wb_wake
from wb_common import run_in_background
from wb_settings import Settings

RATE = 16000


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

        stop = QPushButton("Stop speaking")
        stop.clicked.connect(sd.stop)
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
        self.wake_btn = QPushButton()
        self.wake_btn.setCheckable(True)
        self.wake_btn.toggled.connect(self.set_wake)
        self.wake_state = QLabel("")
        self.heard_label = QLabel("")
        self.heard_label.setObjectName("muted")
        wake_row = QHBoxLayout()
        wake_row.addWidget(self.wake_btn)
        wake_row.addWidget(self.wake_state, 1)

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
        lay.addWidget(self.state)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.typed)

        self.timer = QTimer(self, interval=50, timeout=self._update_meter)
        self.apply_settings()
        self.render()
        if self.s.wake_at_start:
            QTimer.singleShot(2000, lambda: self.wake_btn.setChecked(True))

    def _update_meter(self):
        # push-to-talk keeps a 0-1 level; the wake listener keeps the raw RMS
        level = self.level if self.stream else min(1.0, (self.wake.level if self.wake else 0) * 8)
        self.meter.setValue(int(level * 100))

    # ---- wake word ----------------------------------------------------------------------
    def set_wake(self, on: bool):
        if self.wake:
            self.wake.stop()
            self.wake = None
        if not on:
            self.wake_state.setText("Off")
            self.heard_label.setText("")
            if not self.stream:
                self.timer.stop()
                self.meter.setValue(0)
            return
        sig = self.wake_signals
        listener = wb_wake.WakeListener(
            self.s.wake_word, on_state=sig.state.emit, on_wake=sig.wake.emit,
            on_command_audio=sig.command_audio.emit, on_heard=sig.heard.emit, device=self.s.input_device)
        self.wake_state.setText("Loading the wake-word model…")
        use_mic = self.wake_uses_microphone

        def start(emit):
            listener.start(use_microphone=use_mic)
            return listener

        def started(l):
            if self.wake_btn.isChecked():
                self.wake = l
                self.timer.start()
            else:
                l.stop()

        def failed(e):
            self.wake_btn.setChecked(False)
            self.wake_state.setText(f"<span style='color:#ff8a80'>Could not listen: {e}</span>")

        run_in_background(start, on_result=started, on_error=failed)

    def _wake_state(self, text: str):
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
        self.wake_btn.setText(f"Listen for “{self.s.wake_word}”")
        if self.wake and self.wake.wake_word != self.s.wake_word:  # a new word: restart with it
            self.wake_btn.setChecked(False)
            self.wake_btn.setChecked(True)
        tv = " Say \"turn on CNN\" or \"turn the volume down\" to run the TV." if self.s.tv_ip else ""
        self.hint.setText("Speak English or Swedish; the reply comes in the same language. "
                          f"Ask \"what's on my to-do list?\" to hear your Vikunja tasks.{tv} "
                          f"Model: {self.s.agent_model} · speech recognition: Whisper {self.s.whisper_size}")

    def _sync_tools(self):
        """TV tools are offered to the model only when a TV is set up (Find TV can happen any time)."""
        wb_tv_tools.configure(self.s)
        if self.s.tv_ip:
            voice.TOOLS.update(wb_tv_tools.TOOLS)
        else:
            for name in wb_tv_tools.TOOLS:
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
                                       volume=s.speech_volume, output_device=s.output_device)
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
        sd.stop()  # don't record the assistant talking
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

        def work(emit):
            bot = self._ensure_bot(emit)
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
            else:
                said = text
                words = set(said.lower().replace("?", " ").replace(",", " ").split())
                swedish = {"jag", "är", "och", "vad", "hur", "det", "inte", "min", "mina", "har", "kan", "på", "en", "ett"}
                guessed = "sv" if any(c in said.lower() for c in "åäö") or len(words & swedish) >= 2 else "en"
                lang = known_lang or guessed
            emit(("you", said, lang))
            t1 = time.time()
            emit("Thinking…")
            answer = bot.reply(said, lang)
            t2 = time.time()
            emit(("bot", answer, f"speech-to-text {t1 - t0:.1f}s · answer {t2 - t1:.1f}s"))
            if speak and answer:
                emit("Speaking…")
                bot.speak(answer, lang)
            return True

        def progress(item):
            if isinstance(item, tuple):
                self.turns.append(item)
                self.render()
            else:
                self.state.setText(item)

        def done(result):
            self.state.setText("Ready" if result else "Heard nothing. Check the microphone in Settings.")

        known_lang = lang
        run_in_background(work, on_progress=progress, on_result=done,
                          on_error=lambda e: self.state.setText(f"Error: {e}"),
                          on_done=self._resume_wake, pool=self.pool)

    def new_conversation(self):
        sd.stop()
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
        if self.wake:
            self.wake.stop()
        if self.stream:
            self.stream.close()
        sd.stop()


class _WakeSignals(QObject):
    """Carries the listener's events (from its own thread) to the GUI thread."""
    state = Signal(str)
    wake = Signal()
    command_audio = Signal(object, str)
    heard = Signal(str)
