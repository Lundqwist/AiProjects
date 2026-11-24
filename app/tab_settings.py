"""Settings tab: Ollama models, folders, sound devices and voice, integrations."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import ollama
import sounddevice as sd
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
                               QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget)

import voice
import wb_voices
from voice_library import VoiceLibrary
from wb_common import PathPicker, open_path, run_in_background
from wb_settings import SETTINGS_FILE, Settings


def _combo(items: list[str], current: str, editable: bool = True) -> QComboBox:
    c = QComboBox()
    c.setEditable(editable)
    c.addItems(items)
    if current and current not in items:
        c.addItem(current)
    c.setCurrentText(current)
    return c


class SettingsTab(QWidget):
    saved = Signal()
    check_now = Signal()

    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self._level = 0.0

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.addWidget(self._ollama_group())
        lay.addWidget(self._folders_group())
        lay.addWidget(self._sound_group())
        lay.addWidget(self._tuning_group())
        lay.addWidget(self._integrations_group())
        lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)

        save = QPushButton("Save settings")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        revert = QPushButton("Undo changes")
        revert.clicked.connect(self.load_values)
        open_file = QPushButton("Open settings folder")
        open_file.clicked.connect(lambda: open_path(SETTINGS_FILE.parent))
        self.saved_label = QLabel("")
        self.saved_label.setObjectName("muted")
        bar = QHBoxLayout()
        bar.addWidget(QLabel(f"Stored in {SETTINGS_FILE}"), 1)
        bar.addWidget(self.saved_label)
        bar.addWidget(open_file)
        bar.addWidget(revert)
        bar.addWidget(save)

        outer = QVBoxLayout(self)
        outer.addWidget(scroll, 1)
        outer.addLayout(bar)

        self.meter_timer = QTimer(self, interval=50, timeout=lambda: self.meter.setValue(int(self._level * 100)))
        self.load_values()
        self.refresh_models()

    # ---- groups -------------------------------------------------------------------------
    def _ollama_group(self) -> QGroupBox:
        g = QGroupBox("Ollama and models")
        f = QFormLayout(g)
        self.host = QLineEdit(self.s.ollama_host)
        self.chat_model = _combo([], self.s.chat_model)
        self.vision_model = _combo([], self.s.vision_model)
        self.embed_model = _combo([], self.s.embed_model)
        self.agent_model = _combo([], self.s.agent_model)
        refresh = QPushButton("Refresh model list")
        refresh.clicked.connect(self.refresh_models)
        self.models_status = QLabel("")
        self.models_status.setObjectName("muted")
        row = QHBoxLayout()
        row.addWidget(refresh)
        row.addWidget(self.models_status, 1)
        f.addRow("Ollama address", self.host)
        f.addRow("Documents (answers)", self.chat_model)
        f.addRow("Invoices (vision)", self.vision_model)
        f.addRow("Embeddings", self.embed_model)
        f.addRow("Research and voice (tools)", self.agent_model)
        f.addRow("", row)
        hint = QLabel("A changed Ollama address takes effect after restarting the app. "
                      "Changing the embedding model needs a new document database.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        f.addRow("", hint)
        return g

    def _folders_group(self) -> QGroupBox:
        g = QGroupBox("Folders")
        f = QFormLayout(g)
        self.rag_db = PathPicker(self.s.rag_db)
        self.invoice_inbox = PathPicker(self.s.invoice_inbox)
        self.invoice_output = PathPicker(self.s.invoice_output)
        self.reports_dir = PathPicker(self.s.reports_dir)
        self.voices_dir = PathPicker(self.s.voices_dir)
        f.addRow("Document database", self.rag_db)
        f.addRow("Invoice inbox", self.invoice_inbox)
        f.addRow("Invoice output", self.invoice_output)
        f.addRow("Research reports", self.reports_dir)
        f.addRow("Piper voices", self.voices_dir)
        return g

    def _sound_group(self) -> QGroupBox:
        g = QGroupBox("Sound and voice")
        f = QFormLayout(g)
        self.input_device = QComboBox()
        self.output_device = QComboBox()
        self._fill_devices()
        self.whisper_size = _combo(["tiny", "base", "small", "medium", "large-v3"], self.s.whisper_size, False)
        self.whisper_device = _combo(["auto", "cuda", "cpu"], self.s.whisper_device, False)
        self.voice_en, self.voice_sv = QComboBox(), QComboBox()
        self._fill_voices()

        self.speed = QSlider(Qt.Horizontal, minimum=50, maximum=200)
        self.speed_label = QLabel()
        self.speed.valueChanged.connect(lambda v: self.speed_label.setText(f"{v / 100:.2f}×"))
        self.volume = QSlider(Qt.Horizontal, minimum=0, maximum=200)
        self.volume_label = QLabel()
        self.volume.valueChanged.connect(lambda v: self.volume_label.setText(f"{v} %"))
        self.speak_replies = QCheckBox("Speak replies out loud")
        self.push_to_talk = QCheckBox("Push to talk (hold the button); off = click to start and stop")
        self.wake_word = QLineEdit()
        self.wake_word.setPlaceholderText("e.g. Hey Luna")
        self.conversation_idle = QSpinBox(minimum=10, maximum=1800, singleStep=10, suffix=" s")
        self.wake_chime = QCheckBox("Play a chime when the wake word is heard")

        self.meter = QProgressBar(maximum=100, textVisible=False)
        self.meter.setFixedHeight(10)
        test_mic = QPushButton("Test microphone (3 s, then plays back)")
        test_mic.clicked.connect(self.test_mic)
        test_voice = QPushButton("Test voices")
        test_voice.clicked.connect(self.test_voice)
        rescan = QPushButton("Rescan devices")
        rescan.clicked.connect(self._fill_devices)
        tests = QHBoxLayout()
        tests.addWidget(test_mic)
        tests.addWidget(test_voice)
        tests.addWidget(rescan)
        tests.addStretch()

        f.addRow("Microphone", self.input_device)
        f.addRow("Speakers", self.output_device)
        f.addRow("Input level", self.meter)
        f.addRow("", tests)
        f.addRow("Speech recognition model", self.whisper_size)
        f.addRow("Run speech recognition on", self.whisper_device)
        f.addRow("English voice", self._voice_row(self.voice_en, "en"))
        f.addRow("Swedish voice", self._voice_row(self.voice_sv, "sv"))
        f.addRow("Speaking speed", self._with_label(self.speed, self.speed_label))
        f.addRow("Voice volume", self._with_label(self.volume, self.volume_label))
        f.addRow("", self.speak_replies)
        f.addRow("", self.push_to_talk)
        f.addRow("Wake word", self.wake_word)
        f.addRow("", self.wake_chime)
        f.addRow("Conversation ends after", self.conversation_idle)
        wake_hint = QLabel("Any word or phrase. Two words, or one that's rare in speech and on TV, "
                           "trigger by mistake less often. Everything is heard and recognized on this PC only. "
                           "Switch between Hey Luna, Conversation and Hold to talk with the mode button on "
                           "the Voice tab; the program starts in the mode you used last.")
        wake_hint.setObjectName("muted")
        wake_hint.setWordWrap(True)
        f.addRow("", wake_hint)
        return g

    def _tuning_group(self) -> QGroupBox:
        g = QGroupBox("Answers and research")
        f = QFormLayout(g)
        self.top_k = QSpinBox(minimum=1, maximum=20)
        self.temperature = QDoubleSpinBox(minimum=0, maximum=1.5, singleStep=0.1, decimals=1)
        self.max_rounds = QSpinBox(minimum=2, maximum=30)
        self.min_pages = QSpinBox(minimum=0, maximum=10)
        self.page_chars = QSpinBox(minimum=1000, maximum=20000, singleStep=500)
        f.addRow("Sources per answer", self.top_k)
        f.addRow("Answer creativity (temperature)", self.temperature)
        f.addRow("Research: max rounds", self.max_rounds)
        f.addRow("Research: pages to read at least", self.min_pages)
        f.addRow("Research: characters per page", self.page_chars)
        return g

    def _integrations_group(self) -> QGroupBox:
        g = QGroupBox("Integrations")
        f = QFormLayout(g)
        self.vikunja_url = QLineEdit()
        self.vikunja_token_env = QLineEdit()
        self.promptfoo_path = QLineEdit()
        self.eval_dir = PathPicker(self.s.eval_dir)
        f.addRow("Vikunja address", self.vikunja_url)
        f.addRow("Vikunja token: environment variable", self.vikunja_token_env)
        note = QLabel("The token itself is never saved here; it's read from that variable.")
        note.setObjectName("muted")
        f.addRow("", note)
        f.addRow("promptfoo program", self.promptfoo_path)
        f.addRow("Evaluation folder", self.eval_dir)
        self.check_updates = QCheckBox("Check for updates when the program starts")
        check_now = QPushButton("Check for updates now")
        check_now.clicked.connect(self.check_now.emit)
        row = QHBoxLayout()
        row.addWidget(self.check_updates)
        row.addWidget(check_now)
        row.addStretch()
        f.addRow("Updates", row)
        return g

    def _voice_row(self, combo: QComboBox, lang: str) -> QWidget:
        listen = QPushButton("Listen")
        listen.clicked.connect(lambda: self._preview(combo.currentData()))
        library = QPushButton("Voice library…")
        library.clicked.connect(lambda: self.open_library(lang))
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(combo, 1)
        h.addWidget(listen)
        h.addWidget(library)
        return w

    def _fill_voices(self, en: str | None = None, sv: str | None = None):
        voices_dir = self.voices_dir.text() if hasattr(self, "voices_dir") else self.s.voices_dir
        for combo, lang, current in ((self.voice_en, "en", en or self.s.voice_en),
                                     (self.voice_sv, "sv", sv or self.s.voice_sv)):
            combo.clear()
            for name in wb_voices.installed(voices_dir, lang):
                combo.addItem(wb_voices.label(name), name)
            if combo.findData(current) < 0:
                combo.addItem(f"{current} (not downloaded)", current)
            combo.setCurrentIndex(combo.findData(current))

    def _preview(self, name: str):
        if not name:
            return
        run_in_background(lambda emit: wb_voices.preview(
            name, self.voices_dir.text(), self.speed.value() / 100, self.volume.value() / 100,
            self.output_device.currentData()))

    def open_library(self, lang: str):
        combo = self.voice_en if lang == "en" else self.voice_sv
        dlg = VoiceLibrary(self, self.voices_dir.text(), lang, combo.currentData(),
                           self.speed.value() / 100, self.volume.value() / 100,
                           self.output_device.currentData())
        if dlg.exec() and dlg.chosen:
            chosen_lang, name = dlg.chosen
            self._fill_voices(en=name if chosen_lang == "en" else self.voice_en.currentData(),
                              sv=name if chosen_lang == "sv" else self.voice_sv.currentData())
            self.saved_label.setText("Voice changed: press Save settings to keep it")

    @staticmethod
    def _with_label(slider, label) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(slider, 1)
        label.setMinimumWidth(50)
        h.addWidget(label)
        return w

    # ---- values -------------------------------------------------------------------------
    def _fill_devices(self):
        for combo, kind, current in ((self.input_device, "input", self.s.input_device),
                                     (self.output_device, "output", self.s.output_device)):
            combo.clear()
            combo.addItem("Windows default", None)
            for idx, name in voice.mme_devices(kind):
                combo.addItem(name, idx)
            i = combo.findData(current)
            combo.setCurrentIndex(max(i, 0))

    def load_values(self):
        s = self.s
        for combo, value in ((self.chat_model, s.chat_model), (self.vision_model, s.vision_model),
                             (self.embed_model, s.embed_model), (self.agent_model, s.agent_model),
                             (self.whisper_size, s.whisper_size), (self.whisper_device, s.whisper_device)):
            combo.setCurrentText(value)
        self._fill_voices(s.voice_en, s.voice_sv)
        self.check_updates.setChecked(s.check_updates_at_start)
        self.host.setText(s.ollama_host)
        for picker, value in ((self.rag_db, s.rag_db), (self.invoice_inbox, s.invoice_inbox),
                              (self.invoice_output, s.invoice_output), (self.reports_dir, s.reports_dir),
                              (self.voices_dir, s.voices_dir), (self.eval_dir, s.eval_dir)):
            picker.edit.setText(value)
        self.speed.setValue(int(s.speech_speed * 100))
        self.volume.setValue(int(s.speech_volume * 100))
        self.speak_replies.setChecked(s.speak_replies)
        self.push_to_talk.setChecked(s.push_to_talk)
        self.wake_word.setText(s.wake_word)
        self.conversation_idle.setValue(s.conversation_idle)
        self.wake_chime.setChecked(s.wake_chime)
        self.top_k.setValue(s.top_k)
        self.temperature.setValue(s.temperature)
        self.max_rounds.setValue(s.max_rounds)
        self.min_pages.setValue(s.min_pages)
        self.page_chars.setValue(s.page_chars)
        self.vikunja_url.setText(s.vikunja_url)
        self.vikunja_token_env.setText(s.vikunja_token_env)
        self.promptfoo_path.setText(s.promptfoo_path)
        self._fill_devices()
        self.saved_label.setText("")

    def save(self):
        s = self.s
        s.ollama_host = self.host.text().strip()
        s.chat_model = self.chat_model.currentText().strip()
        s.vision_model = self.vision_model.currentText().strip()
        s.embed_model = self.embed_model.currentText().strip()
        s.agent_model = self.agent_model.currentText().strip()
        s.rag_db, s.invoice_inbox = self.rag_db.text(), self.invoice_inbox.text()
        s.invoice_output, s.reports_dir = self.invoice_output.text(), self.reports_dir.text()
        s.voices_dir, s.eval_dir = self.voices_dir.text(), self.eval_dir.text()
        s.input_device = self.input_device.currentData()
        s.output_device = self.output_device.currentData()
        s.whisper_size = self.whisper_size.currentText()
        s.whisper_device = self.whisper_device.currentText()
        s.voice_en, s.voice_sv = self.voice_en.currentData(), self.voice_sv.currentData()
        s.check_updates_at_start = self.check_updates.isChecked()
        s.speech_speed = self.speed.value() / 100
        s.speech_volume = self.volume.value() / 100
        s.speak_replies = self.speak_replies.isChecked()
        s.push_to_talk = self.push_to_talk.isChecked()
        s.wake_word = self.wake_word.text().strip() or "Hey Luna"
        s.conversation_idle = self.conversation_idle.value()
        s.wake_chime = self.wake_chime.isChecked()
        s.top_k, s.temperature = self.top_k.value(), self.temperature.value()
        s.max_rounds, s.min_pages = self.max_rounds.value(), self.min_pages.value()
        s.page_chars = self.page_chars.value()
        s.vikunja_url = self.vikunja_url.text().strip()
        s.vikunja_token_env = self.vikunja_token_env.text().strip()
        s.promptfoo_path = self.promptfoo_path.text().strip()
        try:
            s.ensure_folders()
            s.save()
            s.apply()
        except OSError as e:
            QMessageBox.warning(self, "Settings", f"Could not save: {e}")
            return
        self.saved_label.setText(f"Saved {time.strftime('%H:%M:%S')}")
        self.saved.emit()

    # ---- models -------------------------------------------------------------------------
    def refresh_models(self):
        def fetch(emit):
            return sorted(m.model for m in ollama.list().models)

        def show(models):
            for combo in (self.chat_model, self.vision_model, self.embed_model, self.agent_model):
                current = combo.currentText()
                combo.clear()
                combo.addItems(models)
                combo.setCurrentText(current)
            self.models_status.setText(f"{len(models)} models installed")

        run_in_background(fetch, on_result=show,
                          on_error=lambda e: self.models_status.setText(f"Ollama not reachable: {e}"))

    # ---- sound tests --------------------------------------------------------------------
    def test_mic(self):
        device = self.input_device.currentData()
        out = self.output_device.currentData()
        self.meter_timer.start()

        def work(emit):
            frames = []

            def cb(data, *_):
                frames.append(data.copy())
                self._level = min(1.0, float(np.sqrt(np.mean(data ** 2))) * 8)

            with sd.InputStream(samplerate=16000, channels=1, dtype="float32", device=device, callback=cb):
                time.sleep(3)
            self._level = 0
            audio = np.concatenate(frames).flatten()
            sd.play(audio, 16000, device=out)
            sd.wait()
            return float(np.abs(audio).max()) if len(audio) else 0.0

        def done(peak):
            self.meter_timer.stop()
            self.meter.setValue(0)
            if peak < 0.01:
                QMessageBox.information(self, "Microphone", "Almost no sound was recorded. "
                                        "Check the microphone choice and Windows' privacy settings.")

        run_in_background(work, on_result=done, on_error=lambda e: (self.meter_timer.stop(),
                          QMessageBox.warning(self, "Microphone", e)))

    def test_voice(self):
        cfg = dict(voices_dir=Path(self.voices_dir.text()), en=self.voice_en.currentData(),
                   sv=self.voice_sv.currentData(), speed=self.speed.value() / 100,
                   volume=self.volume.value() / 100, out=self.output_device.currentData())

        def work(emit):
            from piper import PiperVoice, SynthesisConfig
            syn = SynthesisConfig(length_scale=1 / max(cfg["speed"], 0.1), volume=cfg["volume"])
            for name, text in ((cfg["en"], "Hello! This is the English voice."),
                               (cfg["sv"], "Hej! Det här är den svenska rösten.")):
                v = PiperVoice.load(cfg["voices_dir"] / f"{name}.onnx")
                chunks = list(v.synthesize(text, syn_config=syn))
                sd.play(np.concatenate([c.audio_int16_array for c in chunks]), chunks[0].sample_rate,
                        device=cfg["out"])
                sd.wait()

        run_in_background(work, on_error=lambda e: QMessageBox.warning(self, "Voices", e))
