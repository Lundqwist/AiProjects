"""The brain: the assistant's long-term memory, kept as plain Markdown files you can read and edit.

Ideas borrowed from "second brain" projects, done the workbench's way (local, nothing goes online):
- every fact keeps where it came from: the note it was filed from, and when             (gbrain)
- notes are filed into a wiki of topic pages that the local AI keeps up to date, with an
  index and a log; a newer note marks an old fact as outdated instead of deleting it     (Karpathy's LLM wiki)
- one folder of plain Markdown, so any other tool (Claude Code, Codex, Obsidian) can use it (ai-memory)
- "remember that…" by voice becomes a linked note; things to do still go to Vikunja       (agent second brain)
- corrections you keep repeating become rules the assistant follows, learned once a day  (open second brain)
- local search over all of it                                                             (claude second brain)

The folder (Settings.brain_dir, default Documents\\AI Workbench\\Brain):
  README.md          how the folder works, for people and for other AI tools
  rules.md           rules the assistant always follows: "## My rules" (yours), "## Learned" (from corrections)
  index.md           one line per wiki page
  log.md             what was added and learned, newest last
  notes/             one file per note, YYYY-MM-DD-HHMM-title.md
  wiki/              one file per topic; facts are bullets ending in (source: [[note]], date)
  corrections.jsonl  corrections noticed in conversations, waiting to be learned from
  .search/           the search index (safe to delete: it is rebuilt)
  .history/          copies of pages from before a tidy or an edit in the Brain tab
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import ollama

import wb_trace
from wb_settings import DATA, Settings

_settings: Settings | None = None
_lock = threading.RLock()          # one writer at a time (voice thread, the tab, the daily job)
_filing = threading.Lock()         # notes are filed into the wiki one at a time, in the background
_pending = 0
_pending_lock = threading.Lock()
_client_cache: dict[str, object] = {}
_last_saved: tuple | None = None

COLLECTION = "brain"
FACT_RE = re.compile(r"^- (?P<fact>.+?) \(source: (?P<src>.+)\)\s*$")
OUTDATED_RE = re.compile(r"^- ~~")
MIN_RELEVANCE = 0.55                # cosine distance: hits further away than this are not about the question
CORRECTION_DAYS = 60                # corrections older than this are dropped unlearned

CORRECTION_RE = re.compile(
    r"^(no\b|nope\b|that'?s (not|wrong)|that is (not|wrong)|wrong\b|not what i|i (meant|said|asked)|"
    r"i didn'?t (ask|say|mean)|(that'?s|that is|it'?s|way) too\b|too (long|short|much|many)\b|"
    r"don'?t\b|do not\b|stop (saying|doing|using|calling)|please don'?t|"
    r"always\b|never\b|from now on|next time|you should\b|"
    r"nej\b|fel\b|det är fel|inte det|jag menade|jag sa\b|jag bad|sluta\b|gör inte|alltid\b|aldrig\b|"
    r"från och med nu|nästa gång|du ska\b)", re.I)

README = """# Brain

This folder is the long-term memory of AI Workbench. Everything is plain Markdown, written by the
workbench's local AI and by you. Nothing here is sent online.

- `rules.md`: rules the assistant always follows. Write your own under **My rules**. Rules under
  **Learned** were made from corrections you repeated; edit or delete them freely.
- `notes/`: one file per thing you asked it to remember (by voice, typed, or a saved conversation).
- `wiki/`: one page per topic. Each fact is a bullet that ends with where it came from:
  `- The user's cat is called Sixten. (source: [[2026-11-24-1530-my-cat]], 2026-11-24)`.
  When a newer note says otherwise, the old fact is struck through and marked outdated, not deleted.
- `index.md`: every wiki page with its number of facts. `log.md`: what happened, newest last.
- `corrections.jsonl`: corrections noticed in conversations, waiting for the daily rule learning.

Other AI tools may read this folder as the user's memory. To add something, write a new file in
`notes/` (the workbench files it into the wiki when you press "File new notes"), or edit a page
directly; keep the `(source: …)` part of each fact.
"""

RULES_TEMPLATE = """# Rules

The assistant follows these in every conversation. One rule per bullet.

## My rules

## Learned
"""

FILE_SYSTEM = """You keep a personal wiki for the user, filed from their notes. Each page is about one topic
(a person, a pet, a project, a place, a device, an area of preferences, a plan…).
You get a new note, the titles of the existing pages, and the current facts on related pages.
Reply with JSON only:
{"title": "a 2-6 word title for the note",
 "facts": [{"page": "Page title", "fact": "one fact as a full, self-contained sentence"}],
 "outdated": [{"page": "Page title", "fact": "the exact text of an existing fact the note makes wrong"}]}
Rules:
- A page title names a topic, not a sentence: "Sixten (cat)", "Favourite things", "Job search", "Home network",
  "Anna (sister)". Use an existing page when one fits, spelled exactly as listed.
- Every piece of information in the note becomes a fact, names included ("The user's cat is called Sixten.").
- Only what the note really says. No guesses, no advice.
- Each fact names what it is about ("The user's cat Sixten…", not "It…"). Call the writer "the user".
- Leave out facts that are already on a page.
- When the note changes a fact, put the NEW fact in "facts" AND the old one in "outdated". Example: page
  "Home" has "The user lives in Uppsala."; the note says "We moved to Gävle last week." →
  {"title": "Moved to Gävle", "facts": [{"page": "Home", "fact": "The user lives in Gävle (moved in late 2026)."}],
   "outdated": [{"page": "Home", "fact": "The user lives in Uppsala."}]}
- 1 to 8 facts; "facts" is never empty when the note says something new. Write them in the note's language."""

LEARN_SYSTEM = """You turn a user's corrections of their AI assistant into lasting rules for that assistant.
You get the rules that exist already and numbered corrections (what the user said, and the answer they corrected).
Make a rule only when
 (a) two or more corrections ask for the same thing, or
 (b) one correction clearly asks for something lasting ("always", "never", "from now on", "next time",
     "alltid", "aldrig", "från och med nu", "nästa gång").
Skip corrections of a single fact or a mishearing, and anything an existing rule already says.
Each rule is one short instruction to the assistant, e.g. "Keep spoken answers to one or two sentences."
Write rules in English. Reply with JSON only: {"rules": [{"rule": "…", "from": [1, 3]}]}, or {"rules": []}."""

TIDY_SYSTEM = """You tidy one page of a personal wiki. Merge facts that say the same thing, and keep the
page's facts in a sensible order. Reply with JSON only: {"facts": [{"fact": "…", "sources": [1, 4]}]},
where "sources" are the numbers of the original facts each merged fact comes from. Never invent facts,
never drop a fact unless it is said by another one, and keep every outdated fact out of your reply."""

VOICE_SYSTEM = """You have a long-term memory, the user's "brain". When the user tells you something worth
keeping about themselves, people, pets, plans, preferences or decisions, or asks you to remember something,
save it with remember (not for questions or small talk; something to do goes to add_task instead).
When an answer may depend on what the user told you before, use recall first, and say it is from their notes."""


# ---- set-up ------------------------------------------------------------------------------------


def configure(s: Settings) -> None:
    global _settings
    _settings = s


def _s() -> Settings:
    return _settings or Settings.load()


def root() -> Path:
    return Path(getattr(_s(), "brain_dir", "") or DATA / "Brain")


def ensure() -> Path:
    r = root()
    for d in (r, r / "notes", r / "wiki"):
        d.mkdir(parents=True, exist_ok=True)
    for name, text in (("README.md", README), ("rules.md", RULES_TEMPLATE),
                       ("index.md", "# Index\n\nNo wiki pages yet.\n"), ("log.md", "# Log\n\n")):
        if not (r / name).exists():
            (r / name).write_text(text, encoding="utf-8")
    return r


def _model() -> str:
    s = _s()
    # The writing expert: it files facts and names pages far better than the voice model. Filing runs
    # in the background, so a voice answer doesn't wait for it.
    return s.writing_model or s.chat_model


def _now() -> datetime:
    return datetime.now()


def _slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.U)
    return re.sub(r"[\s_-]+", "-", s).strip("-")[:n].strip("-") or "note"


def _page_file(title: str) -> Path:
    safe = re.sub(r'[<>:"/\\|?*\[\]#^]', "", title).strip(" .")[:80] or "Untitled"
    return root() / "wiki" / f"{safe}.md"


def _log(line: str) -> None:
    with _lock:
        with open(ensure() / "log.md", "a", encoding="utf-8") as f:
            f.write(f"- {_now():%Y-%m-%d %H:%M} · {line}\n")


def _json_chat(system: str, user: str) -> dict:
    client = ollama.Client(timeout=300)
    model = _model()
    extra = {"think": False} if model.startswith(("qwen3", "deepseek-r1")) else {}
    with wb_trace.span("model", model, expert="Writing expert", asked=user) as sp:
        resp = client.chat(model=model, format="json", options={"temperature": 0.1, "num_ctx": 8192},
                           messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                           **extra)
        msg = resp.message
        sp.set(**wb_trace.ollama_stats(resp), answer=msg.content or "")
    text = re.sub(r"<think>.*?</think>", "", msg.content or "", flags=re.S).strip()
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        data = json.loads(m.group(0)) if m else {}
    return data if isinstance(data, dict) else {}


# ---- reading the files -------------------------------------------------------------------------


def pages() -> list[str]:
    d = root() / "wiki"
    return sorted(p.stem for p in d.glob("*.md")) if d.exists() else []


def notes() -> list[Path]:
    d = root() / "notes"
    return sorted(d.glob("*.md"), reverse=True) if d.exists() else []


def page_facts(title: str, include_outdated: bool = False) -> list[str]:
    """The fact sentences on a page (without their sources)."""
    p = _page_file(title)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if OUTDATED_RE.match(line) and not include_outdated:
            continue
        m = FACT_RE.match(line)
        if m:
            out.append(m.group("fact"))
        elif line.startswith("- ") and include_outdated:
            out.append(line[2:])
    return out


def rules() -> list[str]:
    p = root() / "rules.md"
    if not p.exists():
        return []
    text = re.sub(r"<!--.*?-->", "", p.read_text(encoding="utf-8"), flags=re.S)
    out = []
    for line in text.splitlines():
        if line.startswith(("- ", "* ")):
            rule = re.sub(r"\s*\((learned|lärd) [^)]*\)\s*$", "", line[2:]).strip()
            if rule:
                out.append(rule)
    return out


def stats() -> dict:
    corr = [c for c in _load_corrections() if not c.get("used")]
    return {"notes": len(notes()), "pages": len(pages()), "facts": sum(len(page_facts(t)) for t in pages()),
            "rules": len(rules()), "corrections": len(corr)}


# ---- adding notes and filing them into the wiki ------------------------------------------------


def add_note(text: str, source: str = "typed", title: str = "") -> Path:
    """Save a note at once. It is filed into the wiki by file_note()."""
    text = text.strip()
    if not text:
        raise ValueError("Nothing to remember.")
    with _lock:
        d = ensure() / "notes"
        now = _now()
        first = title or " ".join(text.split()[:6])
        path = d / f"{now:%Y-%m-%d-%H%M}-{_slug(first)}.md"
        n = 2
        while path.exists():
            path = d / f"{now:%Y-%m-%d-%H%M}-{_slug(first)}-{n}.md"
            n += 1
        path.write_text(f"---\ncreated: {now:%Y-%m-%d %H:%M}\nsource: {source}\nfiled: no\n---\n\n"
                        f"# {title or first}\n\n{text}\n", encoding="utf-8")
    _index_file(path)
    return path


def _note_body(path: Path) -> tuple[dict, str]:
    raw = path.read_text(encoding="utf-8")
    meta, body = {}, raw
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = raw[m.end():]
    return meta, body.strip()


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.lower()).strip()


def _related_pages(text: str, k: int = 5) -> list[str]:
    found: list[str] = []
    words = set(_norm(text).split())
    for t in pages():  # a page whose title is named in the note
        if set(_norm(t).split()) <= words:
            found.append(t)
    try:
        for h in search(text, k=8, kind="wiki"):
            if h.distance < 0.6:
                found.append(Path(h.source).stem)
    except Exception:  # noqa: BLE001 - no search yet (no embed model): titles are enough
        pass
    return list(dict.fromkeys(found))[:k]


def file_note(path: Path, emit=lambda m: None) -> list[str]:
    """Let the local AI file a note's facts into wiki pages. Returns the pages it changed."""
    with wb_trace.span("filing", path.stem, note=path.stem) as sp:
        changed = _file_note(path, emit)
        sp.set(pages=changed, sources=[f"wiki/{c}.md" for c in changed] + [f"notes/{path.name}"])
        return changed


def _file_note(path: Path, emit) -> list[str]:
    with _filing:
        meta, body = _note_body(path)
        if meta.get("filed") == "yes":
            return []
        emit("Filing the note into your wiki…")
        related = _related_pages(body)
        context = "\n".join(f"## {t}\n" + "\n".join(f"- {f}" for f in page_facts(t)) for t in related) or "(none)"
        titles = ", ".join(pages()[:300]) or "(no pages yet)"
        data = _json_chat(FILE_SYSTEM, f"Existing pages: {titles}\n\nFacts on related pages:\n{context}\n\n"
                                       f"New note:\n{body}")
        facts = [f for f in data.get("facts") or [] if isinstance(f, dict) and f.get("page") and f.get("fact")]
        outdated = [f for f in data.get("outdated") or [] if isinstance(f, dict) and f.get("page") and f.get("fact")]
        date = meta.get("created", f"{_now():%Y-%m-%d}")[:10]
        link = f"[[{path.stem}]]"
        changed: list[str] = []
        with _lock:
            for o in outdated:
                if _mark_outdated(str(o["page"]).strip(), str(o["fact"]).strip(), link, date):
                    changed.append(_canonical(str(o["page"]).strip()))
            for f in facts:
                page = _canonical(str(f["page"]).strip())
                fact = " ".join(str(f["fact"]).split())
                if _add_fact(page, fact, link, date):
                    changed.append(page)
            changed = list(dict.fromkeys(changed))
            new_title = str(data.get("title") or "").strip()
            raw = path.read_text(encoding="utf-8").replace("filed: no", "filed: yes", 1)
            if new_title:
                raw = re.sub(r"(?m)^# .*$", f"# {new_title}", raw, count=1)
            if changed:
                raw = raw.rstrip() + "\n\nFiled in: " + ", ".join(f"[[{c}]]" for c in changed) + "\n"
            path.write_text(raw, encoding="utf-8")
            _write_index()
        _log(f"note {link} ({meta.get('source', 'typed')})" +
             (" → " + ", ".join(f"[[{c}]]" for c in changed) if changed else ": nothing new to file"))
        _index_file(path)
        for c in changed:
            _index_file(_page_file(c))
        return changed


def _canonical(title: str) -> str:
    """An existing page's spelling when the AI writes a title slightly differently."""
    existing = {_norm(t): t for t in pages()}
    return existing.get(_norm(title), title)


def _add_fact(page: str, fact: str, link: str, date: str) -> bool:
    # Already there: the same words. (Not a fuzzy match: "…colour is blue" must not count as "…is green")
    words = set(_norm(fact).split())
    if any(words == set(_norm(f).split()) for f in page_facts(page)):
        return False
    p = _page_file(page)
    text = p.read_text(encoding="utf-8") if p.exists() else f"# {page}\n\n"
    p.write_text(text.rstrip() + f"\n- {fact} (source: {link}, {date})\n", encoding="utf-8")
    return True


def _mark_outdated(page: str, old: str, link: str, date: str) -> bool:
    p = _page_file(_canonical(page))
    if not p.exists():
        return False
    lines = p.read_text(encoding="utf-8").splitlines()
    best, best_ratio = -1, 0.0
    for i, line in enumerate(lines):
        m = FACT_RE.match(line)
        if m and not OUTDATED_RE.match(line):
            r = difflib.SequenceMatcher(None, _norm(old), _norm(m.group("fact"))).ratio()
            if r > best_ratio:
                best, best_ratio = i, r
    if best < 0 or best_ratio < 0.8:
        return False
    m = FACT_RE.match(lines[best])
    lines[best] = f"- ~~{m.group('fact')}~~ (source: {m.group('src')}; outdated by {link}, {date})"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def _write_index() -> None:
    lines = ["# Index", ""]
    for t in pages():
        p = _page_file(t)
        n = len(page_facts(t))
        lines.append(f"- [[{t}]]: {n} fact{'s' if n != 1 else ''}, updated {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d}")
    if len(lines) == 2:
        lines.append("No wiki pages yet.")
    (root() / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def file_unfiled(emit=lambda m: None) -> int:
    """File every note that isn't filed yet (e.g. ones written by hand or by another tool)."""
    count = 0
    for n in sorted(notes()):
        meta, _ = _note_body(n)
        if meta.get("filed") != "yes":
            file_note(n, emit)
            count += 1
    return count


def remember(text: str, source: str = "typed", background: bool = False, on_done=None) -> Path:
    """Save a note now and file it into the wiki (in a background thread when background=True)."""
    global _last_saved
    last = _last_saved
    if last and _norm(last[0]) == _norm(text) and (_now() - last[1]).total_seconds() < 300 and last[2].exists():
        return last[2]  # the same thing twice in a row (saved for "remember that…", then by the tool too)
    path = add_note(text, source)
    _last_saved = (text, _now(), path)

    def work():
        global _pending
        try:
            changed = file_note(path)
            if on_done:
                on_done(changed)
        except Exception as e:  # noqa: BLE001 - the note itself is saved; it can be filed later
            _log(f"could not file [[{path.stem}]]: {type(e).__name__}: {e}")
        finally:
            with _pending_lock:
                _pending -= 1

    global _pending
    with _pending_lock:
        _pending += 1
    if background:
        threading.Thread(target=work, daemon=True, name="brain-filing").start()
    else:
        work()
    return path


def busy() -> bool:
    return _pending > 0


def save_conversation(turns: list[tuple[str, str]], source: str = "voice conversation") -> Path | None:
    """Keep what is worth remembering from a conversation [(who, text)], or None if nothing is."""
    talk = "\n".join(f"{'User' if who == 'you' else 'Assistant'}: {text}" for who, text in turns)[-12000:]
    data = _json_chat("From this conversation, write down what is worth remembering about the user: facts, "
                      "plans, preferences, decisions, things they asked to remember. Not the small talk, not "
                      "general knowledge. Reply with JSON only: {\"note\": \"short sentences\"}, or "
                      "{\"note\": \"\"} if nothing is worth keeping.", talk)
    note = str(data.get("note") or "").strip()
    if not note:
        return None
    return remember(note, source)


# ---- search ------------------------------------------------------------------------------------


@dataclass
class Hit:
    text: str
    source: str     # wiki/Topic.md or notes/2026-….md
    distance: float

    @property
    def label(self) -> str:
        p = Path(self.source)
        return f"wiki page “{p.stem}”" if p.parent.name == "wiki" else f"note {p.stem}"


def _collection():
    import chromadb
    with _lock:
        key = str(root() / ".search")
        if key not in _client_cache:
            _client_cache[key] = chromadb.PersistentClient(path=key)
        return _client_cache[key].get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def _embed(texts: list[str], kind: str) -> list[list[float]]:
    import rag
    return rag.embed(texts, kind)


def _index_file(path: Path) -> None:
    """(Re)index one file. Fails quietly when there is no embed model: search then finds nothing new."""
    try:
        import rag
        rel = path.relative_to(root()).as_posix()
        col = _collection()
        col.delete(where={"source": rel})
        if not path.exists():
            return
        kind = path.parent.name if path.parent.name in ("wiki", "notes") else "other"
        lines = [l for l in path.read_text(encoding="utf-8").splitlines()
                 if not OUTDATED_RE.match(l) and not re.match(r"^(created|source|filed):|^---$", l)]
        text = "\n".join(lines)
        title = path.stem
        if kind == "wiki":  # one chunk per few facts: a hit then points at the right ones
            facts = [l[2:] for l in lines if l.startswith("- ")]
            pieces = [" ".join(facts[i:i + 4]) for i in range(0, len(facts), 4)]
        else:
            pieces = rag.chunk(text)
        pieces = [f"[{title}] {p}" for p in pieces if p.strip()]
        if not pieces:
            return
        ids = [hashlib.sha1(f"{rel}|{i}".encode()).hexdigest() for i in range(len(pieces))]
        col.add(ids=ids, documents=pieces, embeddings=_embed(pieces, "document"),
                metadatas=[{"source": rel, "kind": kind}] * len(pieces))
    except Exception as e:  # noqa: BLE001
        print(f"brain: could not index {path.name}: {type(e).__name__}: {e}")


def reindex_all(emit=lambda m: None) -> int:
    import chromadb  # noqa: F401
    r = ensure()
    col = _collection()
    existing = col.get(include=[])["ids"]
    if existing:
        col.delete(ids=existing)
    files = list((r / "wiki").glob("*.md")) + list((r / "notes").glob("*.md"))
    for i, f in enumerate(files, 1):
        emit(f"Indexing {i} of {len(files)}…")
        _index_file(f)
    return len(files)


def search(question: str, k: int = 6, kind: str | None = None) -> list[Hit]:
    col = _collection()
    if col.count() == 0:
        if (root() / "wiki").exists() and any((root() / "wiki").glob("*.md")) or notes():
            reindex_all()
        if col.count() == 0:
            return []
    where = {"kind": kind} if kind else None
    res = col.query(query_embeddings=_embed([question], "query"), n_results=min(k, col.count()), where=where)
    return [Hit(d, m["source"], dist) for d, m, dist in
            zip(res["documents"][0], res["metadatas"][0], res["distances"][0])]


def relevant(question: str, k: int = 5) -> list[Hit]:
    """Hits close enough to the question to be worth showing the AI."""
    with wb_trace.span("recall", "Search the brain", question=question) as sp:
        try:
            all_hits = search(question, k)
        except Exception as e:  # noqa: BLE001
            sp.set(problem=f"{type(e).__name__}: {e}")
            return []
        hits = [h for h in all_hits if h.distance < MIN_RELEVANCE]
        sp.set(found=[f"{h.label} ({1 - h.distance:.0%} match): {h.text[:200]}" for h in hits],
               left_out=len(all_hits) - len(hits), sources=list(dict.fromkeys(h.source for h in hits)),
               embed_model=getattr(_s(), "embed_model", ""))
        return hits


# ---- what the assistant is told ----------------------------------------------------------------


def context() -> str:
    """For a system prompt: the user's rules."""
    rs = rules()
    if not rs:
        return ""
    text = "\n".join(f"- {r}" for r in rs)[:2000]
    return f"\nRules the user has set (always follow them):\n{text}"


def facts_block(hits: list[Hit]) -> str:
    if not hits:
        return ""
    lines = "\n".join(f"- {h.text} (from your {h.label})" for h in hits)
    return ("\nFrom the user's own notes (their 'brain'; use when relevant, and say it came from their notes):\n"
            + lines)


# ---- corrections → rules -----------------------------------------------------------------------


REMEMBER_RE = re.compile(r"\b(please )?(remember|note down|make a note|write down|don'?t forget)( that)?\b|"
                         r"\bkom ihåg( att)?\b|\banteckna\b|\bskriv upp\b|\bglöm inte\b", re.I)


def wants_remembering(text: str) -> bool:
    """An explicit "remember that…": saved without waiting for the model to choose the tool."""
    return bool(REMEMBER_RE.search(text)) and not text.rstrip().endswith("?")


def looks_like_correction(text: str) -> bool:
    return bool(CORRECTION_RE.match(text.strip().lstrip("\"'“").strip()))


def _corrections_file() -> Path:
    return root() / "corrections.jsonl"


def _load_corrections() -> list[dict]:
    p = _corrections_file()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _save_corrections(items: list[dict]) -> None:
    p = _corrections_file()
    tmp = p.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in items), encoding="utf-8")
    tmp.replace(p)


def note_correction(said: str, previous_answer: str, where: str = "voice") -> bool:
    """Keep a correction for the daily rule learning. Returns whether it looked like one."""
    if not previous_answer or not looks_like_correction(said):
        return False
    with _lock:
        ensure()
        with open(_corrections_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": f"{_now():%Y-%m-%d %H:%M}", "where": where, "said": said[:500],
                                "answer": previous_answer[:500], "used": False}, ensure_ascii=False) + "\n")
    return True


def learning_due() -> bool:
    s = _s()
    if not getattr(s, "brain_learn_daily", True):
        return False
    if not any(not c.get("used") for c in _load_corrections()):
        return False
    last = getattr(s, "brain_last_learned", "")
    try:
        return _now() - datetime.fromisoformat(last) > timedelta(hours=20)
    except ValueError:
        return True


def learn_rules(emit=lambda m: None) -> list[str]:
    """Turn repeated or lasting corrections into rules under "## Learned" in rules.md."""
    with wb_trace.span("learn", "Rules from corrections") as sp:
        new = _learn_rules(emit)
        sp.set(new_rules=new)
        return new


def _learn_rules(emit) -> list[str]:
    with _lock:
        cutoff = f"{_now() - timedelta(days=CORRECTION_DAYS):%Y-%m-%d}"
        items = [c for c in _load_corrections() if c.get("used") or c.get("at", "") >= cutoff]
        open_items = [c for c in items if not c.get("used")]
        s = _s()
        s.brain_last_learned = _now().isoformat(timespec="minutes")
        try:
            s.save()
        except OSError:
            pass
        if not open_items:
            _save_corrections(items)
            return []
    emit("Learning rules from your corrections…")
    listed = "\n".join(f"{i}. ({c['at'][:10]}) The user said: {c['said']}\n   to this answer: {c['answer'][:300]}"
                       for i, c in enumerate(open_items, 1))
    existing = "\n".join(f"- {r}" for r in rules()) or "(none)"
    data = _json_chat(LEARN_SYSTEM, f"Existing rules:\n{existing}\n\nCorrections:\n{listed}")
    new: list[str] = []
    with _lock:
        have = [_norm(r) for r in rules()]
        for r in data.get("rules") or []:
            if not isinstance(r, dict) or not str(r.get("rule", "")).strip():
                continue
            rule = " ".join(str(r["rule"]).split())
            if any(difflib.SequenceMatcher(None, _norm(rule), h).ratio() > 0.85 for h in have):
                continue
            nums = [int(n) for n in r.get("from") or [] if str(n).isdigit() and 1 <= int(n) <= len(open_items)]
            for n in nums:
                open_items[n - 1]["used"] = True
            _append_learned(rule, len(nums))
            have.append(_norm(rule))
            new.append(rule)
        _save_corrections(items)
    _log(f"learned {len(new)} rule{'s' if len(new) != 1 else ''} from {len(open_items)} corrections"
         + (": " + "; ".join(new) if new else ""))
    return new


def _append_learned(rule: str, n: int) -> None:
    p = ensure() / "rules.md"
    text = p.read_text(encoding="utf-8")
    if "## Learned" not in text:
        text = text.rstrip() + "\n\n## Learned\n"
    note = f" (learned {_now():%Y-%m-%d} from {n} correction{'s' if n != 1 else ''})" if n else \
        f" (learned {_now():%Y-%m-%d})"
    head, tail = text.split("## Learned", 1)
    tail_lines = tail.rstrip("\n").split("\n")
    tail_lines.append(f"- {rule}{note}")
    p.write_text(head + "## Learned" + "\n".join(tail_lines) + "\n", encoding="utf-8")


# ---- editing and tidying -----------------------------------------------------------------------


def backup(path: Path) -> None:
    if path.exists():
        d = root() / ".history"
        d.mkdir(exist_ok=True)
        (d / f"{path.stem}.{_now():%Y%m%d-%H%M%S}{path.suffix}").write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8")


def save_file(path: Path, text: str) -> None:
    """Save an edit from the Brain tab (with a copy of the old version) and reindex it."""
    with _lock:
        backup(path)
        path.write_text(text, encoding="utf-8")
        if path.parent.name == "wiki":
            _write_index()
    _index_file(path)


def tidy_page(title: str, emit=lambda m: None) -> int:
    """Merge duplicate facts on one page. Sources are kept; outdated facts stay as they are.
    Returns how many facts the page lost by merging."""
    p = _page_file(title)
    lines = p.read_text(encoding="utf-8").splitlines()
    facts = [(FACT_RE.match(l).group("fact"), FACT_RE.match(l).group("src")) for l in lines
             if FACT_RE.match(l) and not OUTDATED_RE.match(l)]
    if len(facts) < 2:
        return 0
    emit(f"Tidying “{title}”…")
    listed = "\n".join(f"{i}. {f}" for i, (f, _) in enumerate(facts, 1))
    data = _json_chat(TIDY_SYSTEM, f"Page: {title}\n\nFacts:\n{listed}")
    merged = [m for m in data.get("facts") or [] if isinstance(m, dict) and m.get("fact")]
    used = {int(n) for m in merged for n in m.get("sources") or [] if str(n).isdigit()}
    if not merged or used != set(range(1, len(facts) + 1)):
        raise RuntimeError("The tidy-up would have lost facts, so the page was left as it was.")
    out = []
    for m in merged:
        srcs = [facts[int(n) - 1][1] for n in m["sources"] if str(n).isdigit()]
        out.append(f"- {' '.join(str(m['fact']).split())} (source: {'; '.join(dict.fromkeys(srcs))})")
    keep = [l for l in lines if not (FACT_RE.match(l) and not OUTDATED_RE.match(l))]
    head = [l for l in keep if not l.startswith("- ")]
    old = [l for l in keep if l.startswith("- ")]
    with _lock:
        backup(p)
        p.write_text("\n".join(head).rstrip() + "\n" + "\n".join(out + old) + "\n", encoding="utf-8")
        _write_index()
    _index_file(p)
    _log(f"tidied [[{title}]]: {len(facts)} → {len(out)} facts")
    return len(facts) - len(out)


# ---- tools for the assistant -------------------------------------------------------------------


def remember_tool(fact: str) -> str:
    """Save something the user wants kept in their long-term memory (their "brain"): facts about
    themselves, people, pets, plans, preferences or decisions. It is filed into their notes wiki.

    Args:
        fact: what to remember, as full sentences in the user's words, e.g. "My cat is called Sixten."
    """
    path = remember(fact, "voice", background=True)
    return f"Saved in the user's brain as note {path.stem}; it is being filed into the wiki. Tell the user it's noted."


def recall(question: str) -> str:
    """Look in the user's long-term memory (their notes and wiki) for what they told you before:
    preferences, people, pets, plans, past decisions. Use it before answering questions about them.

    Args:
        question: what to look for, e.g. "the user's cat's name"
    """
    hits = relevant(question, k=6)
    if not hits:
        return "Nothing about this in the user's notes."
    return json.dumps({"found": [{"text": h.text, "from": h.label} for h in hits],
                       "instruction": "Answer from these, and say it's from the user's notes."},
                      ensure_ascii=False)


remember_tool.__name__ = "remember"
TOOLS = {"remember": remember_tool, "recall": recall}
STEP_NAMES = {"remember": "Writing it down…", "recall": "Looking in your notes…"}
