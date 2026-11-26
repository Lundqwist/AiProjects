"""The conversation: one input, spoken or typed, and one conversation that both continue.

Each message is routed before any model sees it (wb_abilities.pick): only the tool groups its words call
for are offered, with only their instructions, so a turn costs a few hundred tokens instead of thousands.
Questions that need no tools but fit an expert (code, reasoning, writing) go straight to that expert with a
short summary of the conversation instead of the whole of it. Spoken questions get short spoken answers;
typed ones get full answers on screen. Both land in the same conversation, which the assistant keeps.
"""
from __future__ import annotations

import html
import os
import re
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QTextBrowser, QVBoxLayout, QWidget)

import voice
import wb_abilities
import wb_access
import wb_ai
import wb_brain
import wb_code
import wb_conversations
import wb_create
import wb_notify
import wb_spotify
import wb_spotify_tools
import wb_task_tools
import wb_team_tools
import wb_trace
import wb_tv_tools
import wb_wake
import wb_web_tools
from wb_common import run_in_background
from wb_home import EMBLEM
from wb_settings import SETTINGS_DIR, Settings

RATE = 16000

MODES = {  # mode: (button label, what it does)
    "wake": ("Hey Luna", "Listening for “{word}”. Say it, then your question, e.g. “{word}, open Spotify”."),
    "conversation": ("Conversation", "Just talk: every sentence is a question, no wake word. Ends after "
                                     "{idle} s of silence, or when you say “stop listening” / “sluta lyssna”."),
    "hold": ("Hold to talk", "Not listening in the background. Hold the microphone button while you speak."),
}
END_PHRASES = ("stop listening", "end conversation", "end the conversation", "that's all", "that is all",
               "sluta lyssna", "avsluta samtalet", "det var allt", "tack det var allt")
SYSTEM = """You are the assistant in AI Workbench. You run locally on the user's own Windows PC (Ollama), not on
any company's servers, and you act on this PC only through the tools you are given.
This is one conversation: the user speaks or types, and each message ends with how it came.
(spoken) means your answer is read aloud: 1-3 short sentences, no lists, no Markdown, no emoji, no web addresses.
(typed) means your answer is shown on screen: complete but to the point; Markdown is fine.
Reply in the user's language. Only say you did something if a tool did it; if a tool says the user did not
allow it, say so plainly. You cannot delete files, send messages, make payments or change system settings.
Say where an answer came from: a site, an expert, the user's notes or documents, or your own knowledge."""
EXPERT_SYSTEM = ("You are the {name} in AI Workbench, on the user's own PC. You are given a short summary of the "
                 "conversation so far, then the user's message. Answer it correctly and clearly; say so when you "
                 "are unsure instead of guessing. Things done on the PC (programs, the TV, tasks, files) are done "
                 "by the assistant, not you: just answer.")
EXPERT_SPOKEN = " Your answer is read aloud: 2-4 short sentences, no lists, no Markdown, no code."
EXPERT_TYPED = " Your answer is shown on screen: use Markdown, with code in fenced blocks."
FILES_SYSTEM = """
You CAN create files. When the user asks for a website, a program, a script or any files, write every file
COMPLETE in exactly this form, one after another:

=== FILE: project-name/relative/path.ext ===
```language
the whole file
```

Start every path with one short folder name for the project (e.g. cat-website/index.html). Whole files only,
never fragments or "...". Before the files, say in short what you made and how to open or run it. The user saves
them into their work folder ({folder}) with one click. Never say you can't create files."""
FILE_WORDS = re.compile(r"\b(website|web ?site|web ?page|web app|app|program|script|project|files?|game|"
                        r"hemsida|webbsida|webbplats|projekt|filer|spel|skript)\b", re.I)
# An expert that pretends it ran something on the PC: the assistant, which has the tools, takes over
FAKED = re.compile(r"```\s*tool_code|\bI(?:'ve| have)? (?:just )?(?:run|ran|executed|checked|opened|listed)\b", re.I)
CONTEXT_TURNS = 6        # messages of the conversation an expert is given...
CONTEXT_CHARS = 500      # ...each cut to this length
SPOKEN_CHARS = 420       # longer answers to a spoken question: the start is spoken, the rest is on screen
STALL_SECONDS = 60       # one step taking longer than this turns the bar red
TURN_LIMIT = 300         # a question still not done after this is given up by itself
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
    words = set(re.sub(r"[?,.!]", " ", text.lower()).split())
    sv = len(words & SWEDISH) + (2 if any(c in text.lower() for c in "åäö") else 0)
    en = len(words & ENGLISH)
    if sv == en:
        return default
    return "sv" if sv > en else "en"


def for_speech(text: str) -> str:
    """Answers come with Markdown, code and links: keep the words, drop code, URLs and symbols."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\(?https?://\S+\)?", "", text)
    return re.sub(r"[*#`_>|]+", "", text).strip()


def spoken_part(text: str, lang: str) -> str:
    """What is read aloud: a long answer is cut after a few sentences, with a pointer to the screen."""
    clean = " ".join(for_speech(text).split())
    if len(clean) <= SPOKEN_CHARS:
        return clean
    cut = clean[:SPOKEN_CHARS]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    cut = cut[:end + 1] if end > 80 else cut.rsplit(" ", 1)[0] + "…"
    return cut + (" Resten står på skärmen." if lang == "sv" else " The rest is on the screen.")


def _role(m) -> str:
    return m.get("role") if isinstance(m, dict) else getattr(m, "role", "")


def _content(m) -> str:
    return (m.get("content") if isinstance(m, dict) else getattr(m, "content", "")) or ""


def _last_answer(history: list) -> str:
    return next((_content(m) for m in reversed(history) if _role(m) == "assistant" and _content(m)), "")


HINT_RE = re.compile(r"\n\n\((spoken|typed|talat|skrivet)[^)]*\)$")
# Small models sometimes start their answer by repeating the mark: "(talat) Huvudstaden är Oslo."
HINT_ECHO = re.compile(r"^\s*\((spoken|typed|talat|skrivet)[^)]*\)\s*", re.I)


def compact_context(history: list, turns: int = CONTEXT_TURNS, chars: int = CONTEXT_CHARS) -> list[dict]:
    """The last few user and assistant messages, each cut short, without tool calls or results: what an
    expert needs to follow the conversation, at a fraction of the tokens."""
    out = []
    for m in history[1:]:
        role, text = _role(m), HINT_RE.sub("", _content(m)).strip()
        if role in ("user", "assistant") and text and not text.startswith("(Note from AI Workbench"):
            out.append({"role": role, "content": text if len(text) <= chars else text[:chars] + " …(shortened)"})
    out = out[-turns:]
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def stop_sound() -> None:
    "Stop any playing sound. sounddevice raises if the last sound's stream is already gone."
    try:
        sd.stop()
    except sd.PortAudioError:
        pass


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


class MessageBox(QPlainTextEdit):
    """The text half of the one input: Enter sends, Shift+Enter makes a new line."""
    send = Signal()

    def keyPressEvent(self, e):  # noqa: N802 - Qt's name
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not e.modifiers() & Qt.ShiftModifier:
            self.send.emit()
            return
        super().keyPressEvent(e)


class ChatTab(QWidget):
    """The conversation. Kept API from the old Voice tab: run_turn, turns, bot, set_mode, next_mode, wake."""
    abilities_used = Signal(list)   # tool groups a turn used: the window lights them up
    conversations_changed = Signal(str)  # a conversation was saved or another opened: the id now open
    toggle_list = Signal()          # ☰ Conversations: show or hide the list on the right

    def __init__(self, settings: Settings, status):
        super().__init__()
        self.s = settings
        self.status = status
        self.bot: voice.Assistant | None = None
        self.stream = None
        self.frames: list[np.ndarray] = []
        self.level = 0.0
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)  # one turn at a time
        self.turns: list[tuple[str, str, str]] = []  # (who, text, info); who = you, bot or note
        self.previous_groups: list[str] = []          # tool groups the last turn used (follow-ups keep them)
        self.pending_files: list[wb_code.Change] = []
        self.conv = wb_conversations.Conversation()  # the conversation on screen, saved after every answer
        self._saved_as = ("", 0)                         # (id, turns) last saved: unchanged ones aren't saved again
        self.pending_history: list | None = None    # messages for the assistant when it is (re)loaded

        # ---- conversation view
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)

        # ---- busy bar: moves while a question is worked on, red when a step takes too long
        self.busy = QProgressBar(textVisible=False)
        self.busy.setRange(0, 0)
        self.busy.setFixedHeight(10)
        self.busy.hide()
        self.cancel_btn = QPushButton("Stop waiting")
        self.cancel_btn.setToolTip("Give up on this question")
        self.cancel_btn.clicked.connect(lambda: self.abandon_turn("Stopped. Ask again when you like."))
        self.cancel_btn.hide()
        self.state = QLabel("")
        self.state.setObjectName("muted")
        self.turn_id = self.abandoned_upto = self.pending = 0
        self.is_busy = False
        self.step_text = ""
        self.turn_started = self.step_started = 0.0
        self._old_pools: list[QThreadPool] = []
        self.busy_timer = QTimer(self, interval=500, timeout=self._tick_busy)

        # ---- files an expert wrote (websites, programs): saved with one click
        self.files_label = QLabel()
        self.files_label.setWordWrap(True)
        self.save_btn = QPushButton("Save files")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self.save_files)
        folder_btn = QPushButton("Folder…")
        folder_btn.setToolTip("The work folder files are saved in (also used by the Code panel)")
        folder_btn.clicked.connect(self.pick_folder)
        self.files_row = QWidget()
        fr = QHBoxLayout(self.files_row)
        fr.setContentsMargins(0, 0, 0, 0)
        fr.addWidget(self.files_label, 1)
        fr.addWidget(folder_btn)
        fr.addWidget(self.save_btn)
        self.files_row.hide()

        # ---- the one input: type, or hold the microphone and talk
        self.typed = MessageBox()
        self.typed.setPlaceholderText("Type a message (Enter sends, Shift+Enter for a new line), or hold 🎤 and talk")
        self.typed.setFixedHeight(64)
        self.typed.send.connect(self.send_typed)
        self.talk = QPushButton("🎤")
        self.talk.setObjectName("primary")
        self.talk.setFixedSize(64, 64)
        self.talk.setStyleSheet("font-size: 20pt;")
        self.talk.pressed.connect(self._pressed)
        self.talk.released.connect(self._released)
        self.talk.clicked.connect(self._clicked)
        send = QPushButton("Send")
        send.setFixedHeight(64)
        send.clicked.connect(self.send_typed)
        inp = QHBoxLayout()
        inp.addWidget(self.typed, 1)
        inp.addWidget(send)
        inp.addWidget(self.talk)
        self.meter = QProgressBar(maximum=100, textVisible=False)
        self.meter.setFixedHeight(5)

        # ---- listening mode and conversation buttons
        self.wake: wb_wake.WakeListener | None = None
        self.wake_uses_microphone = True  # the self-test feeds audio itself
        self.wake_signals = _WakeSignals()
        self.wake_signals.state.connect(self._wake_state)
        self.wake_signals.wake.connect(self._wake_heard)
        self.wake_signals.command_audio.connect(
            lambda audio, quick_rest: self.run_turn(audio=audio, strip_wake=bool(quick_rest), fallback=quick_rest))
        self.wake_signals.heard.connect(lambda t: self.heard_label.setText(f"Last heard: “{t[:120]}”"))
        self.wake_signals.idle.connect(self._conversation_idle)
        self.wake_signals.notice.connect(self._notice)
        self.mode = "hold"
        self.mode_btn = QPushButton()
        self.mode_btn.setToolTip("Click (or press F2) to switch: Hey Luna → Conversation → Hold to talk")
        self.mode_btn.setStyleSheet("QPushButton { font-weight: 600; border: 2px solid #d4af37; }")
        self.mode_btn.clicked.connect(self.next_mode)
        QShortcut(QKeySequence("F2"), self, activated=self.next_mode)
        self.wake_state = QLabel("")
        self.mode_help = QLabel("")
        self.mode_help.setObjectName("muted")
        self.mode_help.setWordWrap(True)
        self.heard_label = QLabel("")
        self.heard_label.setObjectName("muted")
        stop = QPushButton("Stop speaking")
        stop.clicked.connect(stop_sound)
        self.keep_btn = QPushButton("Save to brain")
        self.keep_btn.setToolTip("Keep what's worth remembering from this conversation as a note in the Brain")
        self.keep_btn.clicked.connect(self.save_to_brain)
        self.new_btn = new = QPushButton("New conversation")
        new.clicked.connect(lambda: self.new_conversation())  # not clicked's own argument as save_current
        self.list_btn = QPushButton("☰ Conversations")
        self.list_btn.setToolTip("Show or hide your saved conversations on the right")
        self.list_btn.clicked.connect(self.toggle_list.emit)
        top = QHBoxLayout()
        top.addWidget(self.mode_btn)
        top.addWidget(self.wake_state, 1)
        top.addWidget(stop)
        top.addWidget(self.keep_btn)
        top.addWidget(new)
        top.addWidget(self.list_btn)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.mode_help)
        lay.addWidget(self.heard_label)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.files_row)
        busy_row = QHBoxLayout()
        busy_row.addWidget(self.busy, 1)
        busy_row.addWidget(self.cancel_btn)
        lay.addLayout(busy_row)
        lay.addWidget(self.state)
        lay.addLayout(inp)
        lay.addWidget(self.meter)

        self.timer = QTimer(self, interval=50, timeout=self._update_meter)
        wb_notify.subscribe(self._from_job)
        self.apply_settings()
        if self.s.conversation_id:  # carry on with the conversation open when the program closed
            last = wb_conversations.load(self.s, self.s.conversation_id)
            if last and not last.archived:
                self._show_conversation(last)
        self.render()
        # Start listening for "Hey Luna", unless Hold to talk (no listening) was chosen last. Conversation
        # mode answers everything said near the microphone, so it is never where the program starts.
        start = "hold" if self.s.voice_mode == "hold" else "wake"
        self._show_mode(start)
        if start == "wake":
            QTimer.singleShot(2000, lambda: self.set_mode("wake") if self.mode == "wake" and not self.wake else None)

    def _update_meter(self):
        level = self.level if self.stream else min(1.0, (self.wake.level if self.wake else 0) * 8)
        self.meter.setValue(int(level * 100))

    # ---- modes: Hey Luna / Conversation / Hold to talk ----------------------------------------
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
        if self.wake:
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
            self.wake_state.setText(f"<span style='color:#ff8a80'>Could not listen: {html.escape(e)}</span>")

        run_in_background(start, on_result=started, on_error=failed)

    def _conversation_idle(self):
        if self.mode == "conversation":
            self.set_mode("wake")
            self.wake_state.setText(f"<span style='color:#9fb0cc'>●</span> Conversation ended after "
                                    f"{self.s.conversation_idle} s of silence. Say “{self.s.wake_word}” to talk again.")

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

    # ---- settings and tools -------------------------------------------------------------------
    def apply_settings(self):
        """Called after Settings or the abilities change: the models are reloaded on the next turn,
        and the reloaded assistant gets the conversation so far."""
        self._keep_history()
        self.bot = None
        self._sync_tools()
        ptt = self.s.push_to_talk
        self.talk.setCheckable(not ptt)
        self.talk.setToolTip("Hold while you speak" if ptt else "Click to start talking, click again to send")
        self._show_mode(self.mode)
        if self.wake and (self.wake.wake_word != self.s.wake_word or self.wake.device != self.s.input_device):
            self._stop_listener()  # a new word or microphone: listen again with it
            self._start_listener()
        elif self.wake:
            self.wake.idle_limit = self.s.conversation_idle

    def _sync_tools(self):
        """Every tool the assistant has; the router picks which of them a turn is offered."""
        wb_tv_tools.configure(self.s)
        wb_spotify_tools.configure(self.s)
        wb_ai.configure(self.s)
        wb_create.configure(self.s)
        wb_code.configure(self.s)
        wb_brain.configure(self.s)
        wb_task_tools.configure(self.s)
        wb_team_tools.configure(self.s)
        for module in (wb_access, wb_web_tools, wb_ai, wb_create, wb_code, wb_brain, wb_task_tools,
                       wb_tv_tools, wb_spotify_tools, wb_team_tools):
            voice.TOOLS.update(module.TOOLS)
        voice.STEP_NAMES.update(wb_brain.STEP_NAMES)
        voice.STEP_NAMES.update(wb_task_tools.STEP_NAMES)
        voice.STEP_NAMES.update(wb_team_tools.STEP_NAMES)

    def on_groups(self) -> list[str]:
        """The tool groups of the abilities that are switched on and can work now."""
        keys = [k for k in wb_abilities.enabled(self.s)
                if wb_abilities.ready(self.s, k, wb_spotify.connected())[0]]
        return wb_abilities.groups_for(keys)

    def brain_on(self) -> bool:
        return "brain" in wb_abilities.enabled(self.s) and self.s.brain_in_voice

    def _ensure_bot(self, emit):
        self._sync_tools()
        if self.bot is None:
            emit("Loading speech recognition and voices (first time only)…")
            s = self.s
            self.bot = voice.Assistant(chat_model=s.agent_model, whisper_size=s.whisper_size,
                                       whisper_device=s.whisper_device, voices={"en": s.voice_en, "sv": s.voice_sv},
                                       voices_dir=s.voices_dir, speed=s.speech_speed, volume=s.speech_volume,
                                       output_device=s.output_device, system=SYSTEM)
        if self.pending_history is not None:  # an opened conversation, or the one before a reload
            self.bot.history = self.bot.history[:1] + list(self.pending_history)
            self.pending_history = None
        return self.bot

    # ---- recording ------------------------------------------------------------------------------
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
        except Exception as e:  # noqa: BLE001
            self.stream = None
            QMessageBox.warning(self, "Microphone", f"Could not open the microphone: {e}")
            self._resume_wake()
            return
        self.timer.start()
        self.state.setText("Listening… release to send" if self.s.push_to_talk else "Listening… click 🎤 to send")

    def stop_recording(self):
        if not self.stream:
            return
        try:
            self.stream.stop()
            self.stream.close()
        except sd.PortAudioError:
            pass
        self.stream = None
        if not self.wake:
            self.timer.stop()
        self.level = 0
        self.meter.setValue(0)
        self.talk.setChecked(False)
        audio = np.concatenate(self.frames).flatten() if self.frames else np.zeros(0, "float32")
        if len(audio) < RATE * 0.3:
            self.state.setText("Too short: hold 🎤 while you speak.")
            self._resume_wake()
            return
        self.run_turn(audio=audio)

    # ---- a turn ---------------------------------------------------------------------------------
    def send_typed(self):
        text = self.typed.toPlainText().strip()
        if text:
            self.typed.clear()
            self.run_turn(text=text)

    def run_turn(self, audio: np.ndarray | None = None, text: str | None = None, lang: str | None = None,
                 strip_wake: bool = False, fallback: str = ""):
        s = self.s
        speak = s.speak_replies
        wake_word = s.wake_word
        if self.wake:
            self.wake.pause()  # don't listen to ourselves while thinking and talking
        self.turn_id += 1
        my_turn = self.turn_id
        stale = lambda: my_turn <= self.abandoned_upto  # noqa: E731 - given up: ignore what it sends
        self.pending += 1
        self._start_busy("Waiting for the previous question…" if self.pending > 1 else "Starting…")
        known_lang = lang
        in_conversation = self.mode == "conversation" and audio is not None and not strip_wake
        on_groups = self.on_groups()
        previous = list(self.previous_groups)
        brain = self.brain_on()
        folder = Path(s.code_projects_dir)

        def work(emit):
            # The whole turn is one run in the AI activity window
            title = "Chat: (listening)" if audio is not None else f"Chat: {text}"
            with wb_trace.run(title, "chat") as tr:
                return turn(emit, tr)

        def turn(emit, tr):
            emit("begin")
            bot = self._ensure_bot(emit)
            bot.on_step = emit
            bot.span = wb_trace.span
            if strip_wake:
                wb_trace.event("listen", f"Heard “{wake_word}”", by="wake word listener (Whisper tiny on the CPU)")
            t0 = time.time()
            spoken = audio is not None
            if spoken:
                emit("Transcribing…")
                with wb_trace.span("stt", f"Whisper {bot.whisper_size}", audio_s=round(len(audio) / RATE, 1)) as sp:
                    said, lng = bot.transcribe(audio)
                    sp.set(heard=said, language=lng)
                tr.set_title(f"Chat: {said or '(heard nothing)'}")
                if strip_wake:  # "Hey Luna, put CNN on" -> "put CNN on"
                    found, rest = wb_wake.find_wake_word(said, wake_word)
                    said = rest if found else (fallback or said)
                if not said:
                    return None
                if in_conversation:
                    clean = said.lower().strip(" .!?,")
                    if clean in PHANTOM_PHRASES:
                        return None  # Whisper "hears" these in noise
                    if any(p in clean for p in END_PHRASES):
                        emit(("you", said, "spoken"))
                        emit("end")
                        return "ended"
            else:
                said, lng = text, known_lang or guess_language(text, "en")
            emit(("you", said, f"{'spoken' if spoken else 'typed'}, {'Swedish' if lng == 'sv' else 'English'}"))
            if brain:  # "No, I meant…": kept for the daily rule learning
                try:
                    wb_brain.note_correction(said, _last_answer(bot.history), "chat")
                except OSError:
                    pass
            t1 = time.time()
            emit("Thinking…")
            answer, info, used_groups, files = self._answer(bot, said, lng, spoken, on_groups, previous, brain,
                                                            folder, emit)
            t2 = time.time()
            took = f"{'speech to text ' + format(t1 - t0, '.1f') + ' s · ' if spoken else ''}answer {t2 - t1:.1f} s"
            emit(("bot", answer, f"{info} · {took}"))
            emit(("groups", used_groups))
            if files:
                emit(("files", files))
            if spoken and speak and answer:
                emit("Speaking…")
                voice_lang = guess_language(answer, lng)
                said_aloud = spoken_part(answer, voice_lang)
                with wb_trace.span("tts", f"Piper ({voice_lang})", text=said_aloud,
                                   voice=s.voice_sv if voice_lang == "sv" else s.voice_en):
                    bot.speak(said_aloud, voice_lang)
            return True

        def progress(item):
            if stale():
                return
            if isinstance(item, tuple):
                kind = item[0]
                if kind == "piece":
                    self._grow_answer(item[1])
                elif kind == "groups":
                    self.previous_groups = item[1]
                    self.abilities_used.emit(item[1])
                elif kind == "files":
                    self.show_files(item[1])
                else:
                    if kind == "bot" and self.turns and self.turns[-1][0] == "live":
                        self.turns.pop()  # the streamed answer is replaced by the finished one
                    self.turns.append(item)
                    self.render()
            elif item == "begin":
                self.turn_started = time.time()
                self._set_step("Starting…")
            elif item == "end":
                self.set_mode("wake")
                self.wake_state.setText(f"<span style='color:#9fb0cc'>●</span> Conversation ended. "
                                        f"Say “{self.s.wake_word}” to talk again.")
            else:
                self._set_step(item)

        def failed(e):
            if not stale():
                if self.turns and self.turns[-1][0] == "live":
                    self.turns.pop()
                self.turns.append(("note", f"Error: {e}", "Is Ollama running, and are the models installed?"))
                self.render()

        def finished():
            if stale():
                return
            self.pending = max(0, self.pending - 1)
            if self.pending == 0:
                self._stop_busy()
                self.save_conversation()
            self._resume_wake()

        def done(result):
            if stale():
                return
            if result == "ended":
                self.state.setText("Conversation ended")
            elif result:
                self.state.setText("")
            elif in_conversation:
                self.state.setText("Listening…")
            else:
                self.state.setText("Heard nothing. Check the microphone in Settings.")

        run_in_background(work, on_progress=progress, on_result=done, on_error=failed, on_done=finished,
                          pool=self.pool)

    def _answer(self, bot, said: str, lang: str, spoken: bool, on_groups: list[str], previous: list[str],
                brain: bool, folder: Path, emit) -> tuple[str, str, list[str], list]:
        """(answer, who/how line, tool groups used, files written) for one message. Runs on the turn's thread."""
        route = wb_abilities.pick(said, on_groups, previous)
        tool_groups = [g for g in route.groups if g != "expert"]
        expert, why_expert = wb_ai.route(said)
        # Straight to an expert when no tool is called for, or only Code and the files can be shown here
        direct = expert != "general" and (not tool_groups or (tool_groups == ["code"] and not spoken))
        wb_trace.event("route", "Straight to the " + wb_ai.EXPERTS[expert].name.lower() if direct
                       else f"Assistant with {len(wb_abilities.tools_for(route.groups, voice.TOOLS))} of "
                            f"{len(wb_abilities.tools_for(route.all_groups, voice.TOOLS))} tools",
                       why=why_expert if direct else {g: route.why[g] for g in route.groups},
                       groups=route.groups)
        asked, steps = said, []
        if brain and wb_brain.wants_remembering(said):
            path = wb_brain.remember(said, "chat", background=True)
            wb_trace.event("remember", "Saved as a note", note=path.stem, info="Filed into the wiki in the background")
            asked = said + "\n\n(This was saved to the user's notes already. Just confirm it briefly.)"
            steps.append(("remember", {"fact": said}))
            direct = False
        if direct:
            result = self._expert_answer(bot, said, lang, spoken, expert, why_expert, brain, folder, emit)
            if result:
                return result
            wb_trace.event("route", "The expert couldn't do it here: the assistant with all its tools takes over")
            route = wb_abilities.Route(route.all_groups, {}, route.all_groups)
        return self._assistant_answer(bot, asked, said, lang, spoken, route, brain, steps)

    def _brain_block(self, said: str) -> tuple[str, list]:
        hits = wb_brain.relevant(said, k=4)
        return wb_brain.context() + wb_brain.facts_block(hits), hits

    def _assistant_answer(self, bot, asked: str, said: str, lang: str, spoken: bool, route, brain: bool,
                          steps: list | None = None):
        tools = wb_abilities.tools_for(route.groups, voice.TOOLS)
        wider = wb_abilities.tools_for(route.all_groups, voice.TOOLS)
        system = SYSTEM + "\n" + wb_abilities.prompt_for(route.groups)
        steps = list(steps or [])
        if brain:
            block, hits = self._brain_block(said)
            system += "\n" + wb_brain.VOICE_SYSTEM + block
            if hits:
                steps.append(("recall", {"question": said}))
        bot.history[0]["content"] = system
        hint = (wb_abilities.SPEAK if spoken else wb_abilities.TYPE).get(lang, "")
        answer = HINT_ECHO.sub("", bot.reply(asked, lang, tools=tools, wider_tools=wider, hint=hint))
        # Only tools the model really called count for follow-ups (not the recall done before every turn)
        groups = list(dict.fromkeys(g for g in (wb_abilities.group_of_tool(n) for n, _ in bot.last_steps)
                                    if g and g != "expert"))
        bot.last_steps = steps + bot.last_steps
        used = wb_ai.describe_steps(bot.last_steps)
        offered = f"{len(tools)} of {len(wider)} tools offered"
        info = (f"answered by {bot.chat_model} on this PC" + (f" · {used}" if used else " from its own knowledge")
                + f" · {_n(bot.tokens[0])} tokens in, {_n(bot.tokens[1])} out ({offered})")
        return answer, info, groups, []

    def _expert_answer(self, bot, said: str, lang: str, spoken: bool, key: str, why: str, brain: bool,
                       folder: Path, emit):
        s = self.s
        e = wb_ai.EXPERTS[key]
        system = EXPERT_SYSTEM.format(name=e.name.lower()) + (EXPERT_SPOKEN if spoken else EXPERT_TYPED)
        files = key == "code" and not spoken and bool(FILE_WORDS.search(said))
        if files:
            system += FILES_SYSTEM.format(folder=folder)
        msgs = [{"role": "system", "content": system}] + compact_context(bot.history) + \
               [{"role": "user", "content": said}]
        notes = []
        if brain and s.brain_in_experts:
            # Your notes stay on this PC: they are only used when a local expert answers
            online = bool(wb_ai.online_ready(s)) and wb_ai.wants_online(s, msgs, "chat", None)[0]
            if online and not s.online_for_documents:
                notes.append("your brain was not used: this went online, and your notes stay on this PC")
            else:
                block, hits = self._brain_block(said)
                if block:
                    msgs[0]["content"] += "\n" + block
        a = wb_ai.ask(s, msgs, expert=key, big=False if spoken else None,
                      on_text=(lambda p: emit(("piece", p)) if p else None))
        if FAKED.search(a.text) or voice.REFUSAL.search(a.text):
            return None  # it says it can't, or pretends it ran something: the assistant has the tools
        text = HINT_ECHO.sub("", a.text)
        changes = []
        if files:
            explanation, changes = wb_code.parse(a.text, folder, a.notes)
            if changes:
                text = explanation + "\n\n*Files written: " + ", ".join(c.path for c in changes) + \
                       " (not saved yet: see Save files below).*"
        # The answer joins the conversation, so the assistant and later experts know what was said
        hint = (wb_abilities.SPEAK if spoken else wb_abilities.TYPE).get(lang, "")
        bot.history.append({"role": "user", "content": said + (f"\n\n{hint}" if hint else "")})
        bot.history.append({"role": "assistant", "content": text[:2000]})
        bot.last_steps = [("ask_expert", {"kind": key})]
        sent = sum(len(m["content"]) for m in msgs)
        info = (f"{a.by} {a.where} · picked because {why}" + (f" · {_n(a.tokens[0])} tokens in, "
                f"{_n(a.tokens[1])} out" if any(a.tokens) else "") + f" · given {len(msgs) - 2} earlier messages "
                f"in short ({_n(sent)} characters)" + "".join(f" · {n}" for n in notes + a.notes))
        return text, info, ["code"] if changes else [], changes

    # ---- background jobs, streaming and files -----------------------------------------------------
    def _from_job(self, text: str, path: str):
        self.wake_signals.notice.emit(text)  # called on the job's thread: hop to the GUI thread

    def _notice(self, text: str):
        self.turns.append(("bot", text, "a background job finished"))
        if self.bot:
            self.bot.history.append({"role": "assistant", "content": text})
        elif self.pending_history is not None:
            self.pending_history.append({"role": "assistant", "content": text})
        self.render()
        self.status(text)
        if not self.pending:
            self.save_conversation()

    def _grow_answer(self, piece: str):
        if not self.turns or self.turns[-1][0] != "live":
            self.turns.append(("live", "", "writing…"))
        who, text, info = self.turns[-1]
        self.turns[-1] = (who, text + piece, info)
        self.render()

    def show_files(self, changes: list):
        self.pending_files = changes
        if not changes:
            self.files_row.hide()
            return
        replaced = [c.path for c in changes if not c.is_new]
        self.save_btn.setText(f"Save {len(changes)} file{'s' if len(changes) != 1 else ''}")
        self.files_label.setText(f"Not saved yet, into {self.s.code_projects_dir}: " + ", ".join(
            f"{c.path}{' (replaces the old one)' if not c.is_new else ''}" for c in changes)
            + (". Replaced files are copied to .workbench-history first." if replaced else "."))
        self.files_row.show()

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Work folder for files the experts write", self.s.code_projects_dir)
        if d:
            self.s.code_projects_dir = str(Path(d))
            self.s.save()
            wb_code.configure(self.s)
            if self.pending_files:  # same files, new place: see again which are new and which replace one
                def old(path):
                    p = Path(d) / path
                    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
                self.show_files([wb_code.Change(c.path, c.new_text, old(c.path)) for c in self.pending_files])

    def save_files(self):
        if not self.pending_files:
            return
        folder = Path(self.s.code_projects_dir)
        try:
            done = wb_code.apply(folder, self.pending_files)
        except OSError as e:
            QMessageBox.warning(self, "Save files", f"Couldn't save in {folder}: {e}")
            return
        tops = sorted({p.split("/")[0] for p in done})
        opened = folder / tops[0] if len(tops) == 1 and (folder / tops[0]).is_dir() else folder
        self.show_files([])
        self.turns.append(("note", f"Saved {len(done)} file(s) in {opened} and opened it in VS Code.", ""))
        self.render()
        self.save_conversation()
        try:
            wb_code.open_in_vscode(opened)
        except RuntimeError:
            os.startfile(opened)  # noqa: S606 - the user's own folder

    # ---- busy bar -------------------------------------------------------------------------------
    def _start_busy(self, text: str):
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
            self.abandon_turn(f"No answer after {TURN_LIMIT // 60} minutes, so I gave up on that question. Ask again.")
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
        self.pool.clear()
        self._old_pools.append(self.pool)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._keep_history()
        self.bot = None  # the stuck thread may still be using the old one
        if self.turns and self.turns[-1][0] == "live":
            self.turns.pop()
            self.render()
        self._stop_busy()
        self.state.setText(message)
        self._resume_wake()

    # ---- conversation -----------------------------------------------------------------------------
    def save_to_brain(self):
        turns = [(who, text) for who, text, _ in self.turns if who in ("you", "bot")]
        if not turns:
            return
        self.keep_btn.setEnabled(False)
        self.state.setText("Picking out what's worth remembering…")

        def done(path):
            self.state.setText(f"Saved to your brain: {path.stem}" if path else
                               "Nothing in this conversation seemed worth remembering.")

        run_in_background(lambda emit: wb_brain.save_conversation(turns, "conversation"), on_result=done,
                          on_error=lambda e: self.state.setText(f"Could not save: {e}"),
                          on_done=lambda: self.keep_btn.setEnabled(True))

    # ---- saved conversations (wb_conversations.py) ------------------------------------------------
    def _keep_history(self):
        """Before the assistant is dropped (new settings, a stuck question): keep what it remembers."""
        if self.bot is not None and self.pending_history is None:
            self.pending_history = [d for d in (wb_conversations.message_to_dict(m) for m in self.bot.history[1:]) if d]

    def save_conversation(self):
        """Save the conversation on screen (after every answer, and when another one is opened)."""
        c = self.conv
        c.turns = [t for t in self.turns if t[0] != "live"]
        if self._saved_as == (c.id, len(c.turns)):
            return  # nothing new: keep its place in the list (switching away mustn't move it to the top)
        c.groups = list(self.previous_groups)
        history = self.bot.history if self.bot else None
        if history is None and self.pending_history is not None:
            history = [None] + self.pending_history  # the first entry stands for the instructions
        try:
            saved = wb_conversations.save(self.s, c, history)
        except OSError as e:
            self.state.setText(f"Could not save the conversation: {e}")
            return
        if saved and self.s.conversation_id != c.id:
            self.s.conversation_id = c.id
            try:
                self.s.save()
            except OSError:
                pass
        if saved:
            self._saved_as = (c.id, len(c.turns))
            self.conversations_changed.emit(c.id)

    def _show_conversation(self, c: wb_conversations.Conversation):
        stop_sound()
        self.conv = c
        self.turns = [tuple(t) for t in c.turns]
        self._saved_as = (c.id, len(self.turns))
        self.previous_groups = list(c.groups)
        self.show_files([])
        if self.bot:
            self.bot.history = self.bot.history[:1] + list(c.history)
            self.pending_history = None
        else:
            self.pending_history = list(c.history)
        self.render()

    def busy_reason(self) -> str:
        return "Wait for the answer (or click Stop waiting) before switching conversations." if self.pending else ""

    def open_conversation(self, cid: str) -> bool:
        """Make a saved conversation the one on screen, and the one the assistant continues."""
        if cid == self.conv.id:
            return True
        if self.busy_reason():
            self.state.setText(self.busy_reason())
            return False
        c = wb_conversations.load(self.s, cid)
        if not c:
            self.state.setText("That conversation could not be read.")
            return False
        self.save_conversation()
        self._show_conversation(c)
        self.s.conversation_id = c.id
        try:
            self.s.save()
        except OSError:
            pass
        self.state.setText(f"Opened “{c.title}”. Carry on where you left off.")
        self.conversations_changed.emit(c.id)
        return True

    def new_conversation(self, save_current: bool = True):
        if self.busy_reason():
            self.state.setText(self.busy_reason())
            return
        if save_current:
            self.save_conversation()
        stop_sound()
        self.conv = wb_conversations.Conversation()
        if self.bot:
            self.bot.reset()
        self.pending_history = None if self.bot else []
        self.turns.clear()
        self.previous_groups = []
        self.show_files([])
        self.state.setText("")
        self.render()
        self.conversations_changed.emit(self.conv.id)

    def render(self):
        if not self.turns:
            on = [wb_abilities.ABILITIES[k].label for k in wb_abilities.enabled(self.s)]
            emblem = EMBLEM.as_uri() if EMBLEM.exists() else ""
            self.view.setHtml(
                "<div align='center'>" + (f"<img src='{emblem}' width='300'><br>" if emblem else "") +
                "<p style='color:#d4af37; font-size:15pt'>What can I do for you?</p>"
                "<p style='color:#9fb0cc'>Type below, or hold 🎤 and talk (or say “" + html.escape(self.s.wake_word)
                + "” in Hey Luna mode), in English or Swedish. Spoken questions get short spoken answers, typed "
                "ones full answers here; both are one conversation.</p>"
                "<p style='color:#9fb0cc'>Switched on: " + html.escape(", ".join(on)) + ".</p>"
                "<p style='color:#9fb0cc'><i>Try “Write a Python function that checks a Swedish personnummer”, "
                "“Make a song about coffee”, “What's on my to-do list?” or “Put CNN on the TV”.</i></p></div>")
            return
        md = []
        for who, text, info in self.turns:
            if who == "you":
                md.append(f"**You** *({info})*: {text}\n")
            elif who == "note":
                md.append(f"*{text}*" + (f"  \n*{info}*" if info else "") + "\n")
            else:
                md.append(f"{text or '*Thinking…*'}\n\n> {info}\n")
            md.append("\n---\n")
        self.view.setMarkdown("\n".join(md))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def shutdown(self):
        wb_notify.unsubscribe(self._from_job)
        try:
            self.save_conversation()
        except Exception as e:  # noqa: BLE001 - closing must go on
            print(f"chat shutdown: could not save the conversation: {e}")
        for step in ((lambda: self.wake.stop()) if self.wake else None,
                     (lambda: self.stream.close()) if self.stream else None,
                     stop_sound):
            if step:
                try:
                    step()
                except Exception as e:  # noqa: BLE001
                    print(f"chat shutdown: {type(e).__name__}: {e}")
        self.pool.clear()
        for pool in self._old_pools:
            pool.clear()


class _WakeSignals(QObject):
    """Carries events from other threads (the listener, background jobs) to the GUI thread."""
    state = Signal(str)
    wake = Signal()
    command_audio = Signal(object, str)
    heard = Signal(str)
    idle = Signal()
    notice = Signal(str)
