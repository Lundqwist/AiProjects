"""What the AI is doing, step by step: the record behind the AI activity window.

A *run* is one job (a voice question, an Experts answer, a note being filed). It is made of *spans*:
timed steps such as speech to text, a brain recall, a model round, a tool call or speaking. Spans can
hold spans (a tool that asks an expert). Code says what it does with

    with wb_trace.run("Voice question", "voice"):
        with wb_trace.span("model", "qwen3:8b", round=1) as sp:
            ...
            sp.set(tokens_out=120)

A span outside any run starts a run of its own (e.g. filing a note in a background thread).
Nothing is written to disk and nothing leaves the PC: the last runs are kept in memory, and
listeners (the window, the Brain map) are told about every change, from whichever thread it happened in.
"""
from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field

KINDS = {  # kind → (label, colour)
    "listen": ("Listening", "#9fb0cc"),
    "stt": ("Speech to text", "#7fb3ff"),
    "recall": ("Brain recall", "#5ce1e6"),
    "remember": ("Saved to brain", "#5ce1e6"),
    "route": ("Choosing who answers", "#c39bff"),
    "model": ("Model on this PC", "#d4af37"),
    "online": ("Online AI", "#ff9f5a"),
    "tool": ("Tool", "#7ddc9a"),
    "tts": ("Speaking", "#f48fb1"),
    "filing": ("Filing into the wiki", "#5ce1e6"),
    "learn": ("Learning rules", "#7ddc9a"),
    "guard": ("Wrong refusal caught", "#ff8a80"),
    "step": ("Step", "#9fb0cc"),
}
MAX_RUNS = 100
MAX_TEXT = 1500

_ids = itertools.count(1)
_lock = threading.RLock()
_runs: deque = deque(maxlen=MAX_RUNS)
_listeners: list = []
_local = threading.local()


def _short(v):
    if isinstance(v, str) and len(v) > MAX_TEXT:
        return v[:MAX_TEXT] + " …"
    return v


@dataclass
class Span:
    id: int
    run: "Run"
    kind: str
    name: str
    start: float
    parent: int | None = None
    end: float | None = None
    detail: dict = field(default_factory=dict)
    status: str = "running"          # running, ok, error
    error: str = ""

    def set(self, **kw) -> None:
        """Add details (tokens, text, hits…). Listeners see them at once, also while it runs."""
        with _lock:
            self.detail.update({k: _short(v) for k, v in kw.items()})
        _notify("span", self)

    @property
    def duration(self) -> float:
        return (self.end or time.time()) - self.start

    @property
    def depth(self) -> int:
        d, p = 0, self.parent
        by_id = {s.id: s for s in self.run.spans}
        while p is not None and p in by_id:
            d, p = d + 1, by_id[p].parent
        return d


@dataclass
class Run:
    id: int
    title: str
    source: str
    start: float
    end: float | None = None
    spans: list[Span] = field(default_factory=list)
    status: str = "running"
    error: str = ""

    def set_title(self, title: str) -> None:
        self.title = " ".join(title.split())[:100]
        _notify("run", self)

    @property
    def duration(self) -> float:
        return (self.end or time.time()) - self.start


# ---- listening --------------------------------------------------------------------------------


def subscribe(fn) -> None:
    """fn(event, obj): event is "run" or "span"; called from the thread where it happened."""
    with _lock:
        _listeners.append(fn)


def unsubscribe(fn) -> None:
    with _lock:
        if fn in _listeners:
            _listeners.remove(fn)


def _notify(event: str, obj) -> None:
    with _lock:
        listeners = list(_listeners)
    for fn in listeners:
        try:
            fn(event, obj)
        except Exception as e:  # noqa: BLE001 - a broken window must never break the AI
            print(f"trace: listener failed: {type(e).__name__}: {e}")


def runs() -> list[Run]:
    with _lock:
        return list(_runs)


def busy() -> bool:
    return any(r.status == "running" for r in runs())


def clear() -> None:
    with _lock:
        for r in [r for r in _runs if r.status != "running"]:
            _runs.remove(r)
    _notify("clear", None)


# ---- recording --------------------------------------------------------------------------------


def _stack() -> list:
    if not hasattr(_local, "stack"):
        _local.stack = []          # [Run, Span, Span…] for this thread
    return _local.stack


def current_run() -> Run | None:
    st = _stack()
    return st[0] if st else None


@contextmanager
def run(title: str, source: str = "step"):
    """One job. Inside another run (a tool that asks an expert) it simply joins that run."""
    st = _stack()
    if st:
        yield st[0]
        return
    r = Run(next(_ids), " ".join(title.split())[:100], source, time.time())
    with _lock:
        _runs.append(r)
    st.append(r)
    _notify("run", r)
    try:
        yield r
    except BaseException as e:
        r.status, r.error = "error", f"{type(e).__name__}: {e}"[:300]
        raise
    finally:
        st.clear()
        r.end = time.time()
        if r.status == "running":
            r.status = "ok"
        _notify("run", r)


@contextmanager
def span(kind: str, name: str, **detail):
    st = _stack()
    if not st:  # on its own: becomes a run of its own
        with run(name if kind not in KINDS else f"{KINDS[kind][0]}: {name}", kind):
            with span(kind, name, **detail) as sp:
                yield sp
        return
    r = st[0]
    parent = st[-1].id if isinstance(st[-1], Span) else None
    sp = Span(next(_ids), r, kind, name, time.time(), parent, detail={k: _short(v) for k, v in detail.items()})
    with _lock:
        r.spans.append(sp)
    st.append(sp)
    _notify("span", sp)
    try:
        yield sp
    except BaseException as e:
        sp.status, sp.error = "error", f"{type(e).__name__}: {e}"[:300]
        raise
    finally:
        if st and st[-1] is sp:
            st.pop()
        sp.end = time.time()
        if sp.status == "running":
            sp.status = "ok"
        _notify("span", sp)


def event(kind: str, name: str, **detail) -> None:
    """A step that takes no time: a decision, something saved."""
    with span(kind, name, **detail):
        pass


def ollama_stats(resp) -> dict:
    """Token counts and speed from an Ollama reply (or the last part of a streamed one)."""
    out = {}
    tin, tout = getattr(resp, "prompt_eval_count", None), getattr(resp, "eval_count", None)
    if tin:
        out["tokens_in"] = tin
    if tout:
        out["tokens_out"] = tout
    ed = getattr(resp, "eval_duration", None)
    if tout and ed:
        out["tokens_per_s"] = round(tout / (ed / 1e9), 1)
    pd = getattr(resp, "prompt_eval_duration", None)
    if pd:
        out["reading_s"] = round(pd / 1e9, 2)
    ld = getattr(resp, "load_duration", None)
    if ld and ld > 2e8:  # only worth showing when the model had to be loaded
        out["loading_s"] = round(ld / 1e9, 1)
    return out
