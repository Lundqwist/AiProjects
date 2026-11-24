"""Voice tab: push-to-talk conversation with the local voice assistant."""
from __future__ import annotations

import html
import time

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QThreadPool, QTimer
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
                               QPushButton, QTextBrowser, QVBoxLayout, QWidget)

import voice
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

        self.view = QTextBrowser()
        self.typed = QLineEdit()
        self.typed.setPlaceholderText("…or type a message and press Enter (the answer is still spoken)")
        self.typed.returnPressed.connect(self.send_typed)

        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(self.hint)
        lay.addLayout(buttons)
        lay.addWidget(self.meter)
        lay.addWidget(self.state)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.typed)

        self.timer = QTimer(self, interval=50, timeout=lambda: self.meter.setValue(int(self.level * 100)))
        self.apply_settings()
        self.render()

    # ---- settings -----------------------------------------------------------------------
    def apply_settings(self):
        """Called after Settings are saved: the models are reloaded on the next turn."""
        self.bot = None
        ptt = self.s.push_to_talk
        self.talk.setCheckable(not ptt)
        self.talk.setText("Hold to talk" if ptt else "Click to talk")
        self.hint.setText("Speak English or Swedish; the reply comes in the same language. "
                          "Ask \"what's on my to-do list?\" to hear your Vikunja tasks. "
                          f"Model: {self.s.agent_model} · speech recognition: Whisper {self.s.whisper_size}")

    def _ensure_bot(self, emit):
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
            return
        self.run_turn(audio=audio)

    # ---- a turn -------------------------------------------------------------------------
    def send_typed(self):
        text = self.typed.text().strip()
        if text:
            self.typed.clear()
            self.run_turn(text=text)

    def run_turn(self, audio: np.ndarray | None = None, text: str | None = None):
        speak = self.s.speak_replies

        def work(emit):
            bot = self._ensure_bot(emit)
            t0 = time.time()
            if audio is not None:
                emit("Transcribing…")
                said, lang = bot.transcribe(audio)
                if not said:
                    return None
            else:
                said = text
                words = set(said.lower().replace("?", " ").replace(",", " ").split())
                swedish = {"jag", "är", "och", "vad", "hur", "det", "inte", "min", "mina", "har", "kan", "på", "en", "ett"}
                lang = "sv" if any(c in said.lower() for c in "åäö") or len(words & swedish) >= 2 else "en"
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

        run_in_background(work, on_progress=progress, on_result=done,
                          on_error=lambda e: self.state.setText(f"Error: {e}"), pool=self.pool)

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
        if self.stream:
            self.stream.close()
        sd.stop()
