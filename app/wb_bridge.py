"""The bridge between VS Code and AI Workbench: a small local server the AI Workbench extension talks to.

Everything stays on this PC and is answered by the workbench's own local experts (never an online AI):
  GET  /api/health   is it running, and with which models
  POST /api/chat     {"messages": [...], "context": {"file", "language", "selection", "text"}, "expert": "auto"}
                     -> streamed lines of JSON: {"type": "meta"|"text"|"done"|"error", ...}
  POST /api/edit     {"instruction", "file", "language", "text"} -> {"code", "by", "tokens"}

Safety: it listens on 127.0.0.1 only; every request needs the token in %APPDATA%\\AI Workbench\\bridge-token
(made on first start, readable only by this Windows user, and read by the extension from the same place);
requests from web pages (with an Origin header) and to other host names are refused, so a web site in a
browser can't use it. Only the code, reasoning and writing experts answer; nothing is run on the PC.

Tokens are kept short: an expert gets the selected code (or the open file, cut to FILE_CHARS around the
cursor) and the last few chat messages, each cut short, instead of the whole chat.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import wb_ai
import wb_settings
import wb_trace
from wb_settings import Settings

FILE_CHARS = 12000      # the open file is cut to this much, around the selection
MESSAGE_CHARS = 1500    # earlier chat messages are cut to this much each...
MESSAGES = 6            # ...and only the last few are sent
MAX_BODY = 2_000_000

SYSTEM = ("You are the {name} in AI Workbench, a local AI on the user's own PC, helping inside Visual Studio Code. "
          "Answer correctly and to the point; use Markdown, with code in fenced blocks marked with the language. "
          "When you change code, show the whole changed function or block, not fragments. Say so when you are "
          "unsure instead of guessing.")
EDIT_SYSTEM = ("You are the code expert in AI Workbench, a local AI on the user's PC. You get code and an "
               "instruction. Reply with ONLY the complete new version of the given code, in one fenced code block, "
               "with no explanation before or after. Keep its indentation and style; change only what the "
               "instruction asks for.")


def token_file() -> Path:
    return wb_settings.SETTINGS_FILE.parent / "bridge-token"


def token() -> str:
    """The shared secret, made on first use. Only this Windows user can read the file."""
    f = token_file()
    try:
        t = f.read_text(encoding="utf-8").strip()
        if len(t) >= 32:
            return t
    except OSError:
        pass
    f.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(32)
    f.write_text(t, encoding="utf-8")
    try:
        os.chmod(f, 0o600)  # on Windows the folder (AppData) is already private to the user
    except OSError:
        pass
    return t


def _cut_around(text: str, selection: str, limit: int = FILE_CHARS) -> str:
    if len(text) <= limit:
        return text
    at = text.find(selection) if selection else -1
    start = max(0, (at if at >= 0 else 0) - limit // 2)
    part = text[start:start + limit]
    return ("…(start of the file left out)\n" if start else "") + part + \
        ("\n…(rest of the file left out)" if start + limit < len(text) else "")


def build_messages(s: Settings, body: dict) -> tuple[list[dict], str, str]:
    """(messages for the expert, expert key, why) from a chat request, kept short."""
    msgs = [m for m in body.get("messages") or [] if m.get("role") in ("user", "assistant") and m.get("content")]
    if not msgs or msgs[-1]["role"] != "user":
        raise ValueError("the last message must be the user's question")
    question = msgs[-1]["content"]
    choice = (body.get("expert") or "auto").lower()
    if choice in ("code", "reasoning", "writing"):
        key, why = choice, "you chose it"
    else:
        key, why = wb_ai.route(question)
        if key == "general":  # in VS Code, a question without other clues is about the code
            key, why = "code", "asked from VS Code"
    ctx = body.get("context") or {}
    parts = []
    if ctx.get("file"):
        parts.append(f"File: {ctx['file']}" + (f" ({ctx.get('language')})" if ctx.get("language") else ""))
    lang = ctx.get("language") or ""
    if ctx.get("selection"):
        parts.append(f"Selected code:\n```{lang}\n{ctx['selection'][:FILE_CHARS]}\n```")
    elif ctx.get("text"):
        parts.append(f"The open file:\n```{lang}\n{_cut_around(ctx['text'], '')}\n```")
    earlier = []
    for m in msgs[:-1][-MESSAGES:]:
        c = m["content"]
        earlier.append({"role": m["role"], "content": c if len(c) <= MESSAGE_CHARS else c[:MESSAGE_CHARS] + " …(shortened)"})
    while earlier and earlier[0]["role"] != "user":
        earlier.pop(0)
    user = ("\n\n".join(parts) + "\n\nQuestion: " if parts else "") + question
    system = SYSTEM.format(name=wb_ai.EXPERTS[key].name.lower())
    return [{"role": "system", "content": system}] + earlier + [{"role": "user", "content": user}], key, why


def strip_fence(text: str) -> str:
    """The code inside the first fenced block (or the whole text if there is none)."""
    t = text.strip()
    start = t.find("```")
    if start < 0:
        return t
    nl = t.find("\n", start)
    end = t.find("```", nl + 1)
    return t[nl + 1:end if end > 0 else len(t)].rstrip("\n") + "\n"


class Handler(BaseHTTPRequestHandler):
    server_version = "AIWorkbenchBridge/1"

    def log_message(self, fmt, *args):  # the workbench log is not a web server log
        pass

    # ---- safety ------------------------------------------------------------------------------
    def _allowed(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        if host not in ("127.0.0.1", "localhost"):
            return self._fail(403, "wrong host")
        if self.headers.get("Origin"):
            return self._fail(403, "requests from web pages are not allowed")
        if not secrets.compare_digest(self.headers.get("Authorization", ""), f"Bearer {self.server.token}"):
            return self._fail(401, "wrong or missing token")
        return True

    def _fail(self, code: int, msg: str) -> bool:
        self._json(code, {"error": msg})
        return False

    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            raise ValueError("missing or too large request")
        return json.loads(self.rfile.read(n).decode("utf-8"))

    # ---- requests ----------------------------------------------------------------------------
    def do_GET(self):  # noqa: N802 - http.server's name
        if not self._allowed():
            return
        if self.path == "/api/health":
            s = self.server.settings
            return self._json(200, {"ok": True, "app": "AI Workbench", "experts": {
                k: wb_ai.expert_model(s, k) for k in ("code", "reasoning", "writing")}, "online": False})
        self._fail(404, "unknown address")

    def do_POST(self):  # noqa: N802
        if not self._allowed():
            return
        try:
            body = self._body()
        except (ValueError, json.JSONDecodeError) as e:
            return self._fail(400, str(e))
        if self.path == "/api/chat":
            return self._chat(body)
        if self.path == "/api/edit":
            return self._edit(body)
        self._fail(404, "unknown address")

    def _chat(self, body: dict):
        s = self.server.settings
        try:
            msgs, key, why = build_messages(s, body)
        except ValueError as e:
            return self._fail(400, str(e))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        lock = threading.Lock()

        def send(obj):
            with lock:
                self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
                self.wfile.flush()

        e = wb_ai.EXPERTS[key]
        send({"type": "meta", "expert": e.name, "model": wb_ai.expert_model(s, key), "why": why})
        question = msgs[-1]["content"].rsplit("Question: ", 1)[-1]
        try:
            with wb_trace.run(f"VS Code: {question}", "vscode"):
                wb_trace.event("route", f"{e.name} on this PC", why=why, sent_messages=len(msgs) - 1,
                               sent_chars=sum(len(m["content"]) for m in msgs))
                a = wb_ai.ask(s, msgs, expert=key, big=False, on_text=lambda p: send({"type": "text", "text": p})
                              if p else None)
            send({"type": "done", "by": f"{a.by} {a.where}", "why": why, "tokens": list(a.tokens)})
        except (BrokenPipeError, ConnectionError):
            pass  # VS Code stopped listening (the user pressed Stop)
        except Exception as ex:  # noqa: BLE001
            try:
                send({"type": "error", "error": f"{type(ex).__name__}: {ex}"})
            except OSError:
                pass

    def _edit(self, body: dict):
        s = self.server.settings
        code, instruction = body.get("text") or "", (body.get("instruction") or "").strip()
        if not code or not instruction:
            return self._fail(400, "both the code and the instruction are needed")
        lang = body.get("language") or ""
        msgs = [{"role": "system", "content": EDIT_SYSTEM},
                {"role": "user", "content": f"File: {body.get('file', '')}\nInstruction: {instruction}\n\n"
                                            f"```{lang}\n{code[:FILE_CHARS * 2]}\n```"}]
        try:
            with wb_trace.run(f"VS Code edit: {instruction}", "vscode"):
                a = wb_ai.ask(s, msgs, expert="code", big=False, temperature=0.1)
        except Exception as ex:  # noqa: BLE001
            return self._fail(500, f"{type(ex).__name__}: {ex}")
        self._json(200, {"code": strip_fence(a.text), "by": f"{a.by} {a.where}", "tokens": list(a.tokens)})


class Bridge:
    """The server, run on its own thread while the workbench runs."""

    def __init__(self, s: Settings):
        self.s = s
        self.port = s.bridge_port
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)  # OSError if the port is taken
        self.httpd.daemon_threads = True
        self.httpd.settings = s
        self.httpd.token = token()
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="vscode-bridge", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
