"""Saved conversations: every conversation is kept as one JSON file, so it can be opened again later.

A file holds what the screen shows (the turns, with who answered) and what the assistant remembers of
the conversation (its messages, tool calls and results), so an opened conversation continues where it
stopped. Files are in Settings → Folders → Saved conversations (default Documents\\AI Workbench\\
Conversations), named <id>.json. Archived ones are only hidden from the list. Deleted ones go to the
Windows Recycle Bin, so a mistake can be undone there.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from wb_settings import Settings

TITLE_CHARS = 60


@dataclass
class Conversation:
    id: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
    title: str = ""
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated: str = ""
    archived: bool = False
    turns: list = field(default_factory=list)      # [who, text, info]: what the screen shows
    history: list = field(default_factory=list)    # the assistant's messages, without its instructions
    groups: list = field(default_factory=list)     # tool groups the last answer used (for follow-ups)

    @property
    def empty(self) -> bool:
        return not any(t[0] == "you" for t in self.turns)

    def make_title(self) -> str:
        first = next((t[1] for t in self.turns if t[0] == "you"), "")
        first = " ".join(first.split())
        return first if len(first) <= TITLE_CHARS else first[:TITLE_CHARS - 1].rsplit(" ", 1)[0] + "…"


def folder(s: Settings) -> Path:
    return Path(s.conversations_dir)


def _path(s: Settings, cid: str) -> Path:
    return folder(s) / f"{cid}.json"


def message_to_dict(m) -> dict | None:
    """The assistant's messages are dicts or ollama Message objects; both are stored as plain dicts."""
    if isinstance(m, dict):
        return dict(m)
    dump = getattr(m, "model_dump", None)
    if dump:
        d = dump(exclude_none=True)
        return {k: v for k, v in d.items() if v not in ([], "", {})} | {"role": d.get("role", "assistant"),
                                                                           "content": d.get("content") or ""}
    return None


def save(s: Settings, c: Conversation, history: list | None = None) -> Path | None:
    """Write a conversation (history: the assistant's messages, system message first, which is left out).
    An empty conversation is not saved."""
    if history is not None:
        c.history = [d for d in (message_to_dict(m) for m in history[1:]) if d]
    if c.empty:
        return None
    if not c.title:
        c.title = c.make_title()
    c.updated = datetime.now().isoformat(timespec="seconds")
    p = _path(s, c.id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(c), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(p)
    return p


def load(s: Settings, cid: str) -> Conversation | None:
    try:
        data = json.loads(_path(s, cid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    known = Conversation.__dataclass_fields__
    c = Conversation(**{k: v for k, v in data.items() if k in known})
    c.turns = [tuple(t) for t in c.turns]
    return c


def listing(s: Settings, archived: bool = False) -> list[Conversation]:
    """Saved conversations, newest first (archived ones only when asked for). Turns and history are left
    out of the list to keep it quick; open one with load()."""
    out = []
    for p in folder(s).glob("*.json") if folder(s).exists() else []:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if bool(data.get("archived")) != archived:
            continue
        out.append(Conversation(id=data.get("id", p.stem), title=data.get("title") or "(untitled)",
                                created=data.get("created", ""), updated=data.get("updated", ""),
                                archived=bool(data.get("archived"))))
    return sorted(out, key=lambda c: c.updated or c.created, reverse=True)


def set_archived(s: Settings, cid: str, archived: bool = True) -> bool:
    p = _path(s, cid)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    data["archived"] = archived  # its "updated" time stays: archiving isn't talking
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return True


def recycle(path: Path) -> None:
    """Move a file to the Windows Recycle Bin (it can be restored from there)."""
    if os.name != "nt":
        path.unlink()
        return
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT, FOF_NOERRORUI = 3, 0x40, 0x10, 0x4, 0x400
    op = SHFILEOPSTRUCTW(None, FO_DELETE, str(path.resolve()) + "\0", None,
                         FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI, False, None, None)
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result or op.fAnyOperationsAborted:
        raise OSError(f"Could not move {path.name} to the Recycle Bin (error {result})")


def delete(s: Settings, cid: str) -> bool:
    p = _path(s, cid)
    if not p.exists():
        return False
    recycle(p)
    return True
