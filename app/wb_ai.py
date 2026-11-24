"""Experts and online AI: who answers a question, and a record of who did.

Local experts are Ollama models picked for a kind of work (code, reasoning, writing, general).
Online AIs (Claude, or any OpenAI-compatible service) take the larger jobs while they still
have tokens: when one runs out of credit, hits its monthly limit or rejects its key, the next one
is tried, and finally a local expert does the work. Every answer carries a credit line saying
which model did it, where it ran, why it was picked and what was skipped on the way.

API keys are never stored: each provider names the environment variable that holds its key.
Token use per provider and month is kept in online-usage.json next to settings.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
import ollama

import wb_settings
from wb_settings import Settings

# ---- local experts ----------------------------------------------------------------------------


@dataclass
class Expert:
    key: str
    name: str
    setting: str          # the Settings field that holds its model
    about: str
    keywords: tuple = ()


EXPERTS = {
    "general": Expert("general", "General expert", "chat_model", "everyday questions, facts, advice"),
    "code": Expert("code", "Code expert", "code_model", "programming, scripts, SQL, errors and logs", (
        "code", "python", "c#", "csharp", ".net", "dotnet", "c++", "javascript", "typescript", "java",
        "sql", "regex", "powershell", "bash", "script", "function", "class", "method", "compile",
        "compiler", "exception", "stack trace", "traceback", "bug", "debug", "error", "api", "json",
        "html", "css", "react", "docker", "git", "linq", "async", "unit test", "refactor", "algorithm",
        "variable", "loop", "kod", "programmera", "felmeddelande", "skript", "def ", "```", "();", "=>")),
    "reasoning": Expert("reasoning", "Reasoning expert", "reasoning_model",
                        "maths, logic, planning and step-by-step problems", (
        "calculate", "math", "maths", "equation", "solve", "prove", "proof", "logic", "puzzle",
        "probability", "percent", "%", "how many", "how much", "step by step", "riddle", "estimate",
        "optimal", "plan my", "compare", "pros and cons", "which is better", "why does", "räkna",
        "beräkna", "ekvation", "sannolikhet", "procent", "hur många", "hur mycket", "steg för steg",
        "jämför", "logik", "gåta")),
    "writing": Expert("writing", "Writing expert", "writing_model",
                      "letters, cover letters, e-mails, rewriting, translating, Swedish", (
        "write", "rewrite", "letter", "cover letter", "e-mail", "email", "essay", "story", "poem",
        "translate", "translation", "summarize", "summarise", "summary", "proofread", "grammar",
        "linkedin", "cv", "resume", "résumé", "application", "tone", "skriv", "skriva", "brev",
        "personligt brev", "ansökan", "översätt", "sammanfatta", "dikt", "berättelse", "formulera")),
}

THINKERS = ("qwen3", "deepseek-r1", "gpt-oss", "magistral")  # models that take Ollama's think flag


def expert_model(s: Settings, key: str) -> str:
    return getattr(s, EXPERTS[key].setting) or s.chat_model


def _has(text: str, word: str) -> bool:
    """Whole words for words ("api" is not in "capital"); plain search for symbols like ``` or =>."""
    if word.strip().replace(" ", "").replace("-", "").isalnum():
        return re.search(rf"(?<![\w]){re.escape(word.strip())}(?![\w])", text) is not None
    return word in text


def _n(x: int) -> str:
    return f"{x:,}".replace(",", " ")


def route(text: str) -> tuple[str, str]:
    """(expert key, why) from the words in the question. Ties and no match go to the general expert."""
    low = text.lower()
    scores = {}
    for key, e in EXPERTS.items():
        hits = [k.strip() for k in e.keywords if _has(low, k)]
        if hits:
            scores[key] = hits
    if not scores:
        return "general", "no special kind of work recognised"
    best = max(scores, key=lambda k: len(scores[k]))
    if sum(len(v) == len(scores[best]) for v in scores.values()) > 1:
        return "general", "the question mixes several kinds of work"
    words = ", ".join(f"“{w}”" for w in list(dict.fromkeys(scores[best]))[:4])
    return best, f"the question mentions {words}"


# ---- online providers -------------------------------------------------------------------------

PRESETS = {  # name: (kind, base url) - OpenAI-compatible services need only an address and a model
    "Claude (Anthropic)": ("anthropic", "https://api.anthropic.com"),
    "OpenAI": ("openai", "https://api.openai.com/v1"),
    "Google Gemini": ("openai", "https://generativelanguage.googleapis.com/v1beta/openai"),
    "OpenRouter": ("openai", "https://openrouter.ai/api/v1"),
    "Groq": ("openai", "https://api.groq.com/openai/v1"),
    "Mistral": ("openai", "https://api.mistral.ai/v1"),
}


def default_providers() -> list[dict]:
    return [{"name": "Claude", "kind": "anthropic", "base_url": "https://api.anthropic.com",
             "model": "claude-sonnet-5-5", "key_env": "ANTHROPIC_API_KEY", "monthly_tokens": 0,
             "enabled": True}]


class OutOfTokens(Exception):
    """The provider has no credit left (or our own monthly limit is reached)."""


class KeyRejected(Exception):
    pass


class Busy(Exception):
    """Rate limited or overloaded: worth trying again in a few minutes."""


_lock = threading.Lock()


def _usage_file():
    return wb_settings.SETTINGS_FILE.parent / "online-usage.json"


def _load_usage() -> dict:
    try:
        return json.loads(_usage_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_usage(data: dict) -> None:
    f = _usage_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(f)


def _month() -> str:
    return datetime.now().strftime("%Y-%m")


def _next_month() -> datetime:
    now = datetime.now()
    return datetime(now.year + (now.month == 12), now.month % 12 + 1, 1)


def _key(p: dict) -> str | None:
    name = p.get("key_env", "")
    return (os.environ.get(name) or wb_settings._user_env(name) or "").strip() or None if name else None


def _fingerprint(key: str) -> str:  # notices a changed key without storing it
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def provider_state(p: dict) -> tuple[bool, str]:
    """(usable now, a short status line for the screen)."""
    if not p.get("enabled", True):
        return False, "turned off"
    if not p.get("model"):
        return False, "no model chosen"
    key = _key(p)
    if not key:
        return False, f"no key (set the {p.get('key_env') or '?'} environment variable)"
    u = _load_usage().get(p["name"], {})
    used = u.get("months", {}).get(_month(), 0)
    limit = int(p.get("monthly_tokens") or 0)
    used_txt = f"{_n(used)} tokens this month" + (f" (limit {_n(limit)})" if limit else "")
    if u.get("bad_key") == _fingerprint(key):
        return False, f"key rejected: {u.get('last_error', '')[:80]} (change the key to try again)"
    until = u.get("blocked_until")
    if until and datetime.fromisoformat(until) > datetime.now():
        return False, f"{u.get('blocked_why', 'paused')} until {datetime.fromisoformat(until):%d %b %H:%M} · {used_txt}"
    if limit and used >= limit:
        return False, f"monthly limit reached · {used_txt}"
    return True, f"ready · {used_txt}"


def reset_provider(name: str) -> None:
    """Forget 'out of credit' / 'key rejected' marks, e.g. after buying more credit."""
    with _lock:
        data = _load_usage()
        u = data.get(name, {})
        for k in ("blocked_until", "blocked_why", "bad_key", "last_error"):
            u.pop(k, None)
        _save_usage(data)


def _record(name: str, tokens: int = 0, block: tuple[datetime, str] | None = None,
            bad_key: str | None = None, error: str = "") -> None:
    with _lock:
        data = _load_usage()
        u = data.setdefault(name, {})
        if tokens:
            months = u.setdefault("months", {})
            months[_month()] = months.get(_month(), 0) + tokens
        if block:
            u["blocked_until"], u["blocked_why"] = block[0].isoformat(timespec="minutes"), block[1]
        if bad_key:
            u["bad_key"] = bad_key
        if error:
            u["last_error"] = error
        _save_usage(data)


def _error_text(r: httpx.Response) -> str:
    try:
        body = r.json()
        err = body.get("error", body)
        return (err.get("message") if isinstance(err, dict) else str(err)) or r.text
    except ValueError:
        return r.text[:300]


def _raise_for(r: httpx.Response) -> None:
    if r.status_code < 400:
        return
    msg = _error_text(r)
    low = msg.lower()
    if r.status_code in (401, 403):
        raise KeyRejected(msg)
    if r.status_code == 402 or any(w in low for w in ("credit balance", "insufficient_quota", "exceeded your current quota",
                                                       "billing", "out of credits", "quota exceeded", "insufficient credits")):
        raise OutOfTokens(msg)
    if r.status_code in (429, 500, 502, 503, 529):
        raise Busy(msg)
    raise RuntimeError(f"HTTP {r.status_code}: {msg}")


def _call_online(p: dict, messages: list[dict], max_tokens: int, temperature: float) -> tuple[str, int, int]:
    key = _key(p)
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    rest = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
    timeout = httpx.Timeout(300, connect=15)
    if p["kind"] == "anthropic":
        body = {"model": p["model"], "max_tokens": max_tokens, "messages": rest, "temperature": temperature}
        if system:
            body["system"] = system
        r = httpx.post(p.get("base_url", "https://api.anthropic.com").rstrip("/") + "/v1/messages", json=body,
                       headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=timeout)
        _raise_for(r)
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        u = data.get("usage", {})
        return text, u.get("input_tokens", 0), u.get("output_tokens", 0)
    msgs = ([{"role": "system", "content": system}] if system else []) + rest
    r = httpx.post(p["base_url"].rstrip("/") + "/chat/completions",
                   json={"model": p["model"], "messages": msgs, "max_tokens": max_tokens, "temperature": temperature},
                   headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
    _raise_for(r)
    data = r.json()
    text = data["choices"][0]["message"].get("content") or ""
    u = data.get("usage") or {}
    return text, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


def online_ready(s: Settings) -> list[dict]:
    return [p for p in (s.online_providers or []) if provider_state(p)[0]]


# ---- answers ----------------------------------------------------------------------------------


@dataclass
class Answer:
    text: str
    by: str                   # e.g. "Code expert (qwen2.5-coder:7b)" or "Claude (claude-sonnet-5-5)"
    where: str                # "on this PC" or "online at Anthropic"
    why: str = ""
    online: bool = False
    tokens: tuple[int, int] = (0, 0)
    notes: list[str] = field(default_factory=list)
    thought: bool = False

    def credit(self) -> str:
        parts = [f"Answered by {self.by} {self.where}"]
        if self.why:
            parts.append(f"picked because {self.why}")
        if self.online and any(self.tokens):
            parts.append(f"{_n(self.tokens[0])} tokens in, {_n(self.tokens[1])} out")
        if self.thought:
            parts.append("reasoned step by step before answering")
        line = " · ".join(parts)
        if self.notes:
            line += "\n" + "\n".join(f"– {n}" for n in self.notes)
        return line


def _where(p: dict) -> str:
    host = re.sub(r"^https?://(api\.)?", "", p.get("base_url", "")).split("/")[0]
    return "online at Anthropic" if p["kind"] == "anthropic" else f"online at {host or p['name']}"


def wants_online(s: Settings, messages: list[dict], task: str, big: bool | None) -> tuple[bool, str]:
    """Whether this job should go to an online AI, and why (or why not)."""
    if s.online_mode == "off":
        return False, ""
    if big is False:
        return False, ""
    if s.online_mode == "always":
        return True, "online AI is set to be used for everything"
    if big:
        return True, "this is a larger job"
    size = sum(len(m["content"]) for m in messages)
    if task == "report":
        return True, "a full report is a larger job"
    if size >= s.online_min_chars:
        return True, f"the job is long ({size:,} characters)".replace(",", " ")
    return False, ""


def _local(s: Settings, key: str, messages: list[dict], on_text, temperature: float) -> tuple[str, bool]:
    model = expert_model(s, key)
    extra = {}
    if model.startswith(THINKERS):
        extra["think"] = key == "reasoning"  # the reasoning expert thinks first; others answer at once
    client = ollama.Client(timeout=600)
    opts = {"temperature": temperature, "num_ctx": 16384}
    if on_text is None:
        msg = client.chat(model=model, messages=messages, options=opts, **extra).message
        return _strip_think(msg.content or ""), bool(getattr(msg, "thinking", None))
    out, thought = [], False
    for part in client.chat(model=model, messages=messages, options=opts, stream=True, **extra):
        if getattr(part.message, "thinking", None):
            if not thought:
                on_text("")  # lets a screen show "thinking…" without text yet
            thought = True
        piece = part.message.content or ""
        if piece:
            out.append(piece)
            on_text(piece)
    return _strip_think("".join(out)), thought


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def ask(s: Settings, messages: list[dict], *, task: str = "chat", expert: str | None = None,
        big: bool | None = None, online_first: bool = False, local_ok: bool = True,
        on_text=None, temperature: float = 0.3, max_tokens: int = 8000) -> Answer | None:
    """Answer with the right helper. expert=None routes by the last user message.

    big: True = may use online AI, False = never, None = decide from settings and size.
    local_ok=False never answers locally (the caller has its own local result): it returns None
    when online AI isn't wanted, and an Answer with empty text and the reasons when none worked.
    """
    question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    notes: list[str] = []
    if online_first:
        go, why_online = (s.online_mode != "off"), "you asked for online AI"
        if not go:
            notes.append("Online AI is turned off in Settings, so a local expert answered")
    else:
        go, why_online = wants_online(s, messages, task, big)
    if go:
        for p in s.online_providers or []:
            ok, state = provider_state(p)
            if not ok:
                if p.get("enabled", True):
                    notes.append(f"{p['name']} skipped: {state}")
                continue
            try:
                text, tin, tout = _call_online(p, messages, max_tokens, temperature)
            except OutOfTokens as e:
                _record(p["name"], block=(_next_month(), "out of credit"), error=str(e))
                notes.append(f"{p['name']} is out of tokens ({str(e)[:90]}); moved on")
                continue
            except KeyRejected as e:
                _record(p["name"], bad_key=_fingerprint(_key(p)), error=str(e))
                notes.append(f"{p['name']} rejected its key ({str(e)[:90]}); moved on")
                continue
            except Busy as e:
                _record(p["name"], block=(datetime.now() + timedelta(minutes=5), "busy"), error=str(e))
                notes.append(f"{p['name']} is busy or rate limited ({str(e)[:60]}); moved on")
                continue
            except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as e:
                notes.append(f"{p['name']} failed: {str(e)[:120]}; moved on")
                continue
            _record(p["name"], tokens=tin + tout)
            limit = int(p.get("monthly_tokens") or 0)
            if limit:
                used = _load_usage().get(p["name"], {}).get("months", {}).get(_month(), 0)
                notes.append(f"{p['name']} this month: {_n(used)} of {_n(limit)} tokens")
            if on_text:
                on_text(text)
            return Answer(text.strip(), f"{p['name']} ({p['model']})", _where(p), why_online, True, (tin, tout), notes)
        if not s.online_providers:
            notes.append("no online AI is set up (Settings → Experts and online AI)")
        if not local_ok:
            return Answer("", "", "", notes=notes)  # nothing online worked: the caller keeps its own
        notes.append("no online AI had tokens left, so the work was done on this PC")
    elif not local_ok:
        return None
    key, why = (expert, "you chose it") if expert else route(question)
    text, thought = _local(s, key, messages, on_text, temperature)
    model = expert_model(s, key)
    return Answer(text, f"{EXPERTS[key].name} ({model})", "on this PC", why, False, (0, 0), notes, thought)


# ---- online pictures --------------------------------------------------------------------------


def online_image(s: Settings, prompt: str, size: str = "1024x1024") -> tuple[bytes | None, str, list[str]]:
    """(PNG bytes, who made it, notes) from the first online AI with an image model and tokens left.

    OpenAI-compatible services only (POST /images/generations); Claude makes no pictures.
    Returns (None, "", notes) when none could, so the caller makes it on this PC instead.
    """
    import base64
    notes: list[str] = []
    for p in s.online_providers or []:
        if not p.get("image_model") or p.get("kind") != "openai":
            continue
        q = dict(p, model=p["image_model"])
        ok, state = provider_state(q)
        if not ok:
            if p.get("enabled", True):
                notes.append(f"{p['name']} skipped: {state}")
            continue
        body = {"model": p["image_model"], "prompt": prompt, "size": size, "n": 1}
        if p["image_model"].startswith("dall-e"):
            body["response_format"] = "b64_json"
        try:
            r = httpx.post(p["base_url"].rstrip("/") + "/images/generations", json=body,
                           headers={"Authorization": f"Bearer {_key(p)}"}, timeout=httpx.Timeout(300, connect=15))
            _raise_for(r)
            data = r.json()
            item = data["data"][0]
            png = (base64.b64decode(item["b64_json"]) if item.get("b64_json")
                   else httpx.get(item["url"], timeout=120).content)
        except OutOfTokens as e:
            _record(p["name"], block=(_next_month(), "out of credit"), error=str(e))
            notes.append(f"{p['name']} is out of tokens; moved on")
            continue
        except KeyRejected as e:
            _record(p["name"], bad_key=_fingerprint(_key(p)), error=str(e))
            notes.append(f"{p['name']} rejected its key; moved on")
            continue
        except Busy as e:
            _record(p["name"], block=(datetime.now() + timedelta(minutes=5), "busy"), error=str(e))
            notes.append(f"{p['name']} is busy; moved on")
            continue
        except (httpx.HTTPError, RuntimeError, KeyError, ValueError, IndexError) as e:
            notes.append(f"{p['name']} failed: {str(e)[:120]}; moved on")
            continue
        used = int((data.get("usage") or {}).get("total_tokens") or 0)
        _record(p["name"], tokens=used)
        if used:
            notes.append(f"{_n(used)} tokens")
        return png, f"{p['name']} ({p['image_model']}) {_where(p)}", notes
    return None, "", notes


def any_image_provider(s: Settings) -> bool:
    return any(p.get("image_model") and p.get("kind") == "openai" for p in s.online_providers or [])


# ---- a tool for the voice assistant -----------------------------------------------------------

_settings: Settings | None = None


def configure(s: Settings) -> None:
    global _settings
    _settings = s


def ask_expert(question: str, kind: str = "auto") -> str:
    """Hand a harder question to a specialist: "code" (programming, scripts, errors), "reasoning"
    (maths, logic, planning), "writing" (letters, cover letters, translation), "online" (a big job
    for a stronger online AI while it has tokens), or "auto" to let the workbench choose.
    Tell the user who answered, e.g. "The code expert says…".

    Args:
        question: the full question, with every detail the expert needs
        kind: auto, code, reasoning, writing or online
    """
    s = _settings or Settings.load()
    kind = (kind or "auto").lower()
    msgs = [{"role": "system", "content": "Answer clearly and correctly. Keep it short unless asked for more."},
            {"role": "user", "content": question}]
    a = ask(s, msgs, task="voice", expert=kind if kind in EXPERTS else None, online_first=kind == "online",
            big=None if kind == "online" else False)
    return f"Answer from {a.by} {a.where}:\n{a.text[:4000]}" + (f"\n({'; '.join(a.notes)})" if a.notes else "")


TOOLS = {"ask_expert": ask_expert}


def describe_steps(steps: list[tuple[str, dict]]) -> str:
    """'searched the web for “x” · read svt.se · asked the code expert' from the voice assistant's tool calls."""
    names = {"web_search": "searched the web", "read_page": "read", "read_my_cv": "read your CV",
             "ask_expert": "asked", "open_tasks": "read your Vikunja tasks", "create_image": "made a picture",
             "create_music": "made music", "write_story": "wrote a story", "make_song": "started a song",
             "make_movie": "started a movie", "make_code_project": "wrote a program",
             "open_in_vs_code": "opened VS Code", "explain_project": "read the code"}
    out = []
    for name, args in steps:
        if name == "web_search":
            out.append(f"searched the web for “{args.get('query', '')}”")
        elif name == "read_page":
            host = re.sub(r"^https?://(www\.)?", "", str(args.get("url", ""))).split("/")[0]
            out.append(f"read {host}")
        elif name == "ask_expert":
            out.append(f"asked the {args.get('kind', 'auto')} expert" if args.get("kind", "auto") != "online"
                       else "asked an online AI")
        else:
            out.append(names.get(name, f"used {name}"))
    return " · ".join(dict.fromkeys(out))
