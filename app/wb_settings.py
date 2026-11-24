"""Settings for AI Workbench, stored as JSON in %APPDATA%\\AI Workbench\\settings.json.

No secrets are stored: the Vikunja token is read from an environment variable whose
*name* is a setting.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # the AiProjects folder
SETTINGS_DIR = Path(os.environ.get("APPDATA", Path.home())) / "AI Workbench"
SETTINGS_FILE = Path(os.environ.get("AI_WORKBENCH_SETTINGS") or SETTINGS_DIR / "settings.json")  # env: tests


def _documents() -> Path:
    try:
        from PySide6.QtCore import QStandardPaths
        loc = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        if loc:
            return Path(loc)
    except Exception:
        pass
    return Path.home() / "Documents"


DATA = _documents() / "AI Workbench"


def _promptfoo() -> str:
    found = shutil.which("promptfoo")
    if found:
        return found
    return str(Path(os.environ.get("APPDATA", "")) / "npm" / "promptfoo.cmd")


@dataclass
class Settings:
    # Ollama
    ollama_host: str = "http://127.0.0.1:11434"
    chat_model: str = "gemma3:12b"        # documents answers
    vision_model: str = "gemma3:12b"      # invoices
    embed_model: str = "nomic-embed-text"
    agent_model: str = "qwen3:8b"         # research + voice (needs tool calling)

    # Folders
    rag_db: str = str(DATA / "Document database")
    invoice_inbox: str = str(DATA / "Invoices" / "Inbox")
    invoice_output: str = str(DATA / "Invoices" / "Output")
    reports_dir: str = str(DATA / "Research reports")

    # Documents
    top_k: int = 5
    temperature: float = 0.2

    # Research
    max_rounds: int = 10
    min_pages: int = 2
    page_chars: int = 4000

    # Voice and sound
    input_device: int | None = None   # None = Windows default (MME device index otherwise)
    output_device: int | None = None
    whisper_size: str = "small"
    whisper_device: str = "auto"      # auto, cuda, cpu
    voice_en: str = "en_US-lessac-medium"
    voice_sv: str = "sv_SE-nst-medium"
    voices_dir: str = str(ROOT / "02-voice" / "voices")
    speech_speed: float = 1.0
    speech_volume: float = 1.0
    speak_replies: bool = True
    push_to_talk: bool = True         # hold the button; off = click to start, click to stop

    # Integrations
    vikunja_url: str = "http://localhost:3456"
    vikunja_token_env: str = "VIKUNJA_TOKEN"

    # Evaluation
    promptfoo_path: str = field(default_factory=_promptfoo)
    eval_dir: str = str(ROOT / "05-eval")

    # Window
    window_geometry: str = ""
    last_tab: int = 0

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                names = {f.name for f in fields(cls)}
                for k, v in data.items():
                    if k in names:
                        setattr(s, k, v)
            except (OSError, ValueError):
                pass  # a broken file falls back to defaults; saving rewrites it
        return s

    def save(self) -> None:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(SETTINGS_FILE)

    def ensure_folders(self) -> None:
        for d in (self.rag_db, self.invoice_inbox, self.invoice_output, self.reports_dir):
            Path(d).mkdir(parents=True, exist_ok=True)

    def apply(self) -> None:
        """Push settings into the project modules (they read module-level names at call time)."""
        import agent
        import invoice
        import rag
        rag.DB_DIR = Path(self.rag_db)
        rag.CHAT_MODEL = self.chat_model
        rag.EMBED_MODEL = self.embed_model
        rag.TOP_K = int(self.top_k)
        invoice.MODEL = self.vision_model
        agent.MODEL = self.agent_model
        agent.MAX_ROUNDS = int(self.max_rounds)
        agent.MIN_PAGES = int(self.min_pages)
        agent.PAGE_CHARS = int(self.page_chars)
        os.environ["VIKUNJA_URL"] = self.vikunja_url
        token = os.environ.get(self.vikunja_token_env) or _user_env(self.vikunja_token_env)
        if token:
            os.environ["VIKUNJA_TOKEN"] = token


def _user_env(name: str) -> str | None:
    """Read a user environment variable from the registry (set after this process started)."""
    if not name or os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None
