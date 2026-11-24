"""The brain as a graph that grows over time, for the Brain map window.

Read straight from the Brain folder's Markdown (nothing is stored besides it):
- a node per note, per wiki page and per rule, plus a "Rules" hub
- note → page when a fact on the page came from the note; another note → page when it outdated a fact
- page ↔ page when two pages were filed from the same note, or a fact links to another page with [[…]]
- rule → hub
Every node, edge and fact has the time it appeared, so the window can replay how the brain grew.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import wb_brain

LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
SRC_RE = re.compile(r"\[\[([^\]]+)\]\](?:,\s*(\d{4}-\d{2}-\d{2}))?")
OUTDATED_BY_RE = re.compile(r"outdated by \[\[([^\]]+)\]\](?:,\s*(\d{4}-\d{2}-\d{2}))?")
LEARNED_RE = re.compile(r"\s*\((?:learned|lärd) (\d{4}-\d{2}-\d{2})[^)]*\)\s*$")
LOG_RE = re.compile(r"^- (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) · (.*)$")
STEM_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})")


def _time(text: str | None) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime((text or "").strip(), fmt)
        except ValueError:
            pass
    return None


def _mtime(p: Path) -> datetime:
    return datetime.fromtimestamp(p.stat().st_mtime)


@dataclass
class Fact:
    text: str
    page: str
    notes: list[str]                 # the notes it came from
    added: datetime
    outdated: datetime | None = None
    outdated_by: str = ""

    def active(self, t: datetime) -> bool:
        return self.added <= t and not (self.outdated and self.outdated <= t)


@dataclass
class Node:
    id: str
    kind: str                        # page, note, rule, hub
    title: str
    t: datetime
    path: Path | None = None
    facts: list[Fact] = field(default_factory=list)
    text: str = ""                   # a note's body, a rule's text
    filed: bool = True
    learned: bool = False            # a rule learned from corrections (not one you wrote)

    def active_facts(self, t: datetime) -> list[Fact]:
        return [f for f in self.facts if f.active(t)]


@dataclass
class Edge:
    a: str
    b: str
    t: datetime
    kind: str                        # filed, outdated, related, rule
    weight: int = 1


@dataclass
class Graph:
    nodes: dict[str, Node]
    edges: list[Edge]
    facts: list[Fact]
    log: list[tuple[datetime, str]]
    start: datetime
    end: datetime

    def counts_at(self, t: datetime) -> dict[str, int]:
        kinds = [n.kind for n in self.nodes.values() if n.t <= t]
        return {"notes": kinds.count("note"), "pages": kinds.count("page"), "rules": kinds.count("rule"),
                "facts": sum(f.active(t) for f in self.facts),
                "outdated": sum(1 for f in self.facts if f.outdated and f.outdated <= t)}

    def times(self) -> list[datetime]:
        """Every moment something was added or changed, oldest first."""
        ts = {n.t for n in self.nodes.values()} | {f.added for f in self.facts} | \
             {f.outdated for f in self.facts if f.outdated} | {t for t, _ in self.log}
        return sorted(ts)

    def neighbours(self, node_id: str) -> set[str]:
        return {e.b if e.a == node_id else e.a for e in self.edges if node_id in (e.a, e.b)}


def signature() -> tuple:
    """Changes whenever a file the map is drawn from changes (checked every few seconds)."""
    r = wb_brain.root()
    files = [r / "rules.md", r / "log.md"] + sorted((r / "wiki").glob("*.md")) + sorted((r / "notes").glob("*.md"))
    out = []
    for f in files:
        try:
            st = f.stat()
            out.append((f.name, st.st_mtime_ns, st.st_size))
        except OSError:
            pass
    return tuple(out)


def build() -> Graph:
    r = wb_brain.ensure()
    nodes: dict[str, Node] = {}
    note_time: dict[str, datetime] = {}

    for p in wb_brain.notes():
        meta, body = wb_brain._note_body(p)
        m = STEM_TIME_RE.match(p.stem)
        t = _time(meta.get("created")) or (_time(f"{m.group(1)} {m.group(2)}:{m.group(3)}") if m else None) \
            or _mtime(p)
        title = next((l[2:].strip() for l in body.splitlines() if l.startswith("# ")), p.stem)
        text = re.sub(r"(?m)^# .*$|^Filed in: .*$", "", body).strip()
        nodes[f"note:{p.stem}"] = Node(f"note:{p.stem}", "note", title, t, p, text=text,
                                       filed=meta.get("filed", "yes") == "yes")
        note_time[p.stem] = t

    facts: list[Fact] = []
    for title in wb_brain.pages():
        p = r / "wiki" / f"{title}.md"
        page_facts = []
        for line in p.read_text(encoding="utf-8").splitlines():
            m = wb_brain.FACT_RE.match(line)
            if not m:
                continue
            src = m.group("src")
            keep, _, gone = src.partition("outdated by")
            sources = SRC_RE.findall(keep)
            times = [note_time.get(n) or _time(d) for n, d in sources]
            times = [t for t in times if t] or [_mtime(p)]
            f = Fact(m.group("fact").strip("~ "), title, [n for n, _ in sources], min(times))
            ob = OUTDATED_BY_RE.search(src)
            if ob:
                f.outdated_by = ob.group(1)
                f.outdated = note_time.get(ob.group(1)) or _time(ob.group(2)) or _mtime(p)
                f.outdated = max(f.outdated, f.added)
            page_facts.append(f)
        facts += page_facts
        t = min((f.added for f in page_facts), default=_mtime(p))
        nodes[f"page:{title}"] = Node(f"page:{title}", "page", title, t, p, facts=page_facts)

    # rules: yours were there from the start, learned ones from the day they were learned
    rule_lines, section = [], ""
    rules_file = r / "rules.md"
    if rules_file.exists():
        text = re.sub(r"<!--.*?-->", "", rules_file.read_text(encoding="utf-8"), flags=re.S)
        for line in text.splitlines():
            if line.startswith("## "):
                section = line[3:].strip().lower()
            elif line.startswith(("- ", "* ")) and line[2:].strip():
                rule_lines.append((section, line[2:].strip()))

    log = []
    log_file = r / "log.md"
    if log_file.exists():
        for line in log_file.read_text(encoding="utf-8").splitlines():
            m = LOG_RE.match(line)
            if m and _time(m.group(1)):
                log.append((_time(m.group(1)), m.group(2)))

    known = [n.t for n in nodes.values()] + [t for t, _ in log]
    start = min(known) if known else (_mtime(rules_file) if rules_file.exists() else datetime.now())
    for i, (sec, line) in enumerate(rule_lines):
        m = LEARNED_RE.search(line)
        learned = bool(m) or sec.startswith("learn")
        plain = LEARNED_RE.sub("", line)
        # the log line that learned it has the exact time; rules.md only the day
        logged = next((when for when, what in log if what.startswith("learned") and plain in what), None)
        t = logged or (_time(m.group(1)) if m else None) or (_mtime(rules_file) if learned else start)
        nodes[f"rule:{i}"] = Node(f"rule:{i}", "rule", plain, t, rules_file,
                                  text=line, learned=learned)
    rule_nodes = [n for n in nodes.values() if n.kind == "rule"]
    if rule_nodes:
        nodes["hub:rules"] = Node("hub:rules", "hub", "Rules", min(n.t for n in rule_nodes), rules_file)
    start = min([n.t for n in nodes.values()] + [t for t, _ in log] or [start])

    # edges
    links: dict[tuple[str, str, str], datetime] = {}

    def link(a: str, b: str, kind: str, t: datetime):
        if a in nodes and b in nodes and a != b:
            key = (a, b, kind)
            links[key] = min(links.get(key, t), t)

    for f in facts:
        for n in f.notes:
            link(f"note:{n}", f"page:{f.page}", "filed", f.added)
        if f.outdated_by:
            link(f"note:{f.outdated_by}", f"page:{f.page}", "outdated", f.outdated)
        for target in LINK_RE.findall(f.text):
            link(f"page:{f.page}", f"page:{wb_brain._canonical(target)}", "related", f.added)
    for n in rule_nodes:
        link(n.id, "hub:rules", "rule", n.t)

    edges = [Edge(a, b, t, kind) for (a, b, kind), t in links.items()]
    # pages filed from the same note are related; the more notes they share, the closer
    by_note: dict[str, list[Edge]] = {}
    for e in edges:
        if e.kind in ("filed", "outdated"):
            by_note.setdefault(e.a, []).append(e)
    shared: dict[tuple[str, str], Edge] = {}
    for es in by_note.values():
        for i, x in enumerate(es):
            for y in es[i + 1:]:
                if x.b == y.b:
                    continue
                a, b = sorted((x.b, y.b))
                t = max(x.t, y.t)
                if (a, b) in shared:
                    shared[(a, b)].weight += 1
                    shared[(a, b)].t = min(shared[(a, b)].t, t)
                else:
                    shared[(a, b)] = Edge(a, b, t, "related")
    edges += shared.values()
    edges.sort(key=lambda e: e.kind == "outdated")   # drawn last, so it shows on top of the same note's line

    end = max([n.t for n in nodes.values()] + [f.outdated for f in facts if f.outdated] +
              [t for t, _ in log] + [start])
    return Graph(nodes, edges, facts, log, start, end)
