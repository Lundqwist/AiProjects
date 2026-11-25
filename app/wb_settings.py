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

    # Experts (wb_ai.py): a local model per kind of work; the general expert is chat_model
    code_model: str = "qwen2.5-coder:7b"
    reasoning_model: str = "deepseek-r1:8b"
    writing_model: str = "gemma3:12b"
    # The coding team (team\): the Builder is the code expert; these two read and judge
    team_scout_model: str = "qwen2.5-coder:7b"
    # the Checker writes and runs a small test per requirement (probes.py). Off: with qwen3:8b judging its own
    # probes it was worse (misused harness, unexplained failures, runaway answers) and six times slower (Day 5b)
    team_probes: bool = False
    team_checker_model: str = "qwen3:8b"   # thinks first; deepseek-r1:8b gave empty answers, and the Builder's
    #                                         own model shared its blind spots (Day 4 trial)

    # Online AI for larger jobs, used while it has tokens. Keys stay in environment variables.
    online_mode: str = "big"          # off, big (larger jobs only), always
    online_min_chars: int = 6000      # a job this long counts as a larger job
    online_for_documents: bool = False  # your own documents stay on this PC unless this is on
    online_providers: list = field(default_factory=lambda: [
        {"name": "Claude", "kind": "anthropic", "base_url": "https://api.anthropic.com",
         "model": "claude-sonnet-5-5", "key_env": "ANTHROPIC_API_KEY", "monthly_tokens": 0, "enabled": True}])

    # Folders
    rag_db: str = str(DATA / "Document database")
    invoice_inbox: str = str(DATA / "Invoices" / "Inbox")
    invoice_output: str = str(DATA / "Invoices" / "Output")
    reports_dir: str = str(DATA / "Research reports")
    creations_dir: str = str(DATA / "Creations")          # Images, Music and Stories inside
    code_projects_dir: str = str(DATA / "Code projects")  # the work folder: Code panel, experts and the assistant
    code_roots: list = field(default_factory=lambda: [str(DATA / "Code projects"), str(ROOT.parent)])  # voice may open these
    brain_dir: str = str(DATA / "Brain")                  # long-term memory as Markdown (wb_brain.py)
    conversations_dir: str = str(DATA / "Conversations")  # every conversation, one JSON file each (wb_conversations.py)
    jobs_dir: str = str(DATA / "Jobs")                    # the coding team's client jobs (team\jobs.py)

    # Brain
    brain_in_voice: bool = True       # the assistant can remember and recall, and follows the rules
    brain_in_experts: bool = True     # Experts answers use the rules and related notes (only when answered locally)
    brain_learn_daily: bool = True    # turn repeated corrections into rules once a day
    brain_last_learned: str = ""

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
    voice_en: str = "en_US-amy-medium"     # female
    voice_sv: str = "sv_SE-alma-medium"    # female
    voices_dir: str = str(ROOT / "02-voice" / "voices")
    speech_speed: float = 1.0
    speech_volume: float = 1.0
    speak_replies: bool = True
    push_to_talk: bool = True         # hold the button; off = click to start, click to stop
    wake_word: str = "Hey Luna"       # any word or phrase; two words trigger by mistake less often
    wake_at_start: bool = False       # (old; replaced by voice_mode)
    wake_chime: bool = True           # play a chime when the wake word is heard
    voice_mode: str = "wake"          # wake (Hey Luna), conversation, hold (hold to talk); starts in Hey Luna
    conversation_idle: int = 60       # conversation mode ends after this many seconds of silence

    # Integrations
    vikunja_url: str = "http://localhost:3456"
    vikunja_token_env: str = "VIKUNJA_TOKEN"

    # TV (filled by "Find TV"; nothing about the TV is in the code)
    tv_ip: str = ""
    tv_mac: str = ""
    tv_name: str = ""
    tv_favorites: list = field(default_factory=list)  # [{"name": "CNN", "app": "Samsung TV Plus", "channel": "4556"}]

    # Spotify (wb_spotify.py). The token is in %APPDATA%\AI Workbench\spotify-token.json, not here.
    spotify_client_id: str = ""       # from the user's own app at developer.spotify.com (not a secret)
    spotify_device: str = ""          # preferred device name; empty = this PC
    spotify_ask_first: bool = True    # ask on screen before playing or changing music

    # What the assistant may do (wb_access.py). Filled in Settings → Access; nothing personal in the code.
    allowed_programs: list = field(default_factory=list)    # Start-menu names it may open freely
    status_targets: list = field(default_factory=list)      # [{"name", "host", "port"}]
    allowed_containers: list = field(default_factory=list)  # [{"name", "host"}]; host = an ssh alias

    # Abilities (wb_abilities.py): which ones the assistant may use; missing keys count as on
    abilities: dict = field(default_factory=dict)
    panel_open: str = ""              # the tool panel shown beside the conversation ("" = none)
    conversation_id: str = ""         # the conversation open when the program closed: reopened at start
    conversations_shown: bool = True  # the list of conversations on the right

    # VS Code bridge (wb_bridge.py): the extension talks to this program on 127.0.0.1 only
    bridge_on: bool = True
    bridge_port: int = 8765

    # Evaluation
    promptfoo_path: str = field(default_factory=_promptfoo)
    eval_dir: str = str(ROOT / "05-eval")

    # Updates
    check_updates_at_start: bool = True
    skipped_updates: list = field(default_factory=list)  # optional updates the user said "not now" to

    # Window
    window_geometry: str = ""
    last_tab: int = 0
    settings_version: int = 2

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
                if data.get("settings_version", 1) < 2:
                    # Version 2 switched the default voices to female ones; only replace the old
                    # defaults, never a voice the user picked themselves
                    if s.voice_en == "en_US-lessac-medium":
                        s.voice_en = "en_US-amy-medium"
                    if s.voice_sv == "sv_SE-nst-medium":
                        s.voice_sv = "sv_SE-alma-medium"
                    s.settings_version = 2
                if "voice_mode" not in data and data.get("wake_at_start"):
                    s.voice_mode = "wake"  # "listen at start" became the remembered mode
            except (OSError, ValueError):
                pass  # a broken file falls back to defaults; saving rewrites it
        return s

    def save(self) -> None:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(SETTINGS_FILE)

    def ensure_folders(self) -> None:
        for d in (self.rag_db, self.invoice_inbox, self.invoice_output, self.reports_dir, self.creations_dir,
                  self.code_projects_dir, self.brain_dir, self.conversations_dir, self.jobs_dir):
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
