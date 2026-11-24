"""A research agent: the model decides when to search and what to read, then writes a cited report.

Usage: python agent.py "your question"
"""
from __future__ import annotations

import json
import re
import sys
import time
import warnings

warnings.filterwarnings("ignore", message=".*SOCKS support.*")
from datetime import datetime
from pathlib import Path

import ollama
import trafilatura
from ddgs import DDGS

MODEL = "qwen3:8b"
MAX_ROUNDS = 10
MIN_PAGES = 2
PAGE_CHARS = 4000
HERE = Path(__file__).parent

SYSTEM = f"""You are a careful research assistant. Today is {datetime.now():%Y-%m-%d}.
Use web_search to find sources and read_page to read the most promising ones (read at least 2 pages).
Only state facts you read with read_page; search snippets are not sources. Never invent numbers or IDs. When you have enough, write the final report in Markdown:
a 2-3 sentence answer first, then short sections, and cite pages as [1], [2] using the order you read them.
End with a "Sources" list: [n] title - url. Never cite a page you did not read."""


class Research:
    def __init__(self, log):
        self.log = log
        self.sources: list[tuple[str, str]] = []  # (title, url) in the order read
        self.texts: list[str] = []  # what was read from each page, for a separate report writer

    def web_search(self, query: str) -> str:
        """Search the web. Returns up to 6 results with title, url and a snippet.

        Args:
            query: the search query, a few keywords
        """
        results = DDGS().text(query, max_results=6) or []
        return ("Search results (snippets are NOT sources: read_page a result before using its facts):\n"
                + json.dumps([{"title": r["title"], "url": r["href"], "snippet": r["body"]} for r in results],
                             ensure_ascii=False))

    def read_page(self, url: str) -> str:
        """Download a web page and return its main text (first 4000 characters).

        Args:
            url: the full URL of the page, from a search result
        """
        html = trafilatura.fetch_url(url)
        text = trafilatura.extract(html, include_comments=False) if html else None
        if not text:
            return "Could not read this page. Try another result."
        meta = trafilatura.extract_metadata(html)
        title = (meta.title if meta and meta.title else url)
        self.sources.append((title, url))
        self.texts.append(text[:PAGE_CHARS])
        n = len(self.sources)
        return f"Source [{n}]: {title}\n\n{text[:PAGE_CHARS]}"


WRITER_SYSTEM = f"""You write research reports. Today is {datetime.now():%Y-%m-%d}.
Use ONLY the numbered pages below; never add facts, numbers or IDs that are not in them.
Write Markdown: a 2-3 sentence answer first, then short sections, citing pages as [1], [2].
End with a "Sources" list: [n] title - url. Say plainly when the pages don't answer something."""


def run(question: str, on_step=print, out_dir: Path | None = None, writer=None) -> tuple[str, Path]:
    """Research a question. on_step gets a line per tool call; reports go to out_dir.

    writer(messages) -> (report, credit) or None: writes the report from the pages read (e.g. a
    stronger online AI). None, or a writer that returns None, keeps the local model's own report.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(out_dir or HERE / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"{stamp}.log.jsonl"
    log = log_path.open("w", encoding="utf-8")
    r = Research(log)
    tools = {"web_search": r.web_search, "read_page": r.read_page}
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]

    for round_no in range(1, MAX_ROUNDS + 1):
        resp = ollama.chat(model=MODEL, messages=messages, tools=list(tools.values()),
                           think=False, options={"temperature": 0.3, "num_ctx": 16384})
        msg = resp.message
        messages.append(msg)
        if not msg.tool_calls:
            if len(r.sources) < MIN_PAGES and round_no < MAX_ROUNDS - 1:
                messages.append({"role": "user", "content":
                                 f"You have read {len(r.sources)} page(s). Read at least {MIN_PAGES} different "
                                 "sources with read_page first, then write the report."})
                continue
            break
        for call in msg.tool_calls:
            name, args = call.function.name, call.function.arguments
            on_step(f"  [{round_no}] {name}({', '.join(f'{v}' for v in args.values())})")
            try:
                result = tools[name](**args) if name in tools else f"Unknown tool {name}"
            except Exception as e:
                result = f"Tool error: {e}"
            # Full results, so every claim in the report can be traced to what the model saw
            log.write(json.dumps({"round": round_no, "tool": name, "args": args,
                                  "result": result}, ensure_ascii=False) + "\n")
            messages.append({"role": "tool", "content": result, "tool_name": name})
    else:
        messages.append({"role": "user", "content": "Stop researching. Write the final report now from what you read."})
        on_step("  Round limit reached: writing the report from what was read")
        msg = ollama.chat(model=MODEL, messages=messages, think=False).message

    report = re.sub(r"<think>.*?</think>", "", msg.content or "", flags=re.S).strip()
    credit = f"Searched, read and written by {MODEL} on this PC."
    if writer and r.sources:
        on_step("  Writing the report…")
        pages = "\n\n".join(f"[{i}] {t} - {u}\n{text}" for i, ((t, u), text) in enumerate(zip(r.sources, r.texts), 1))
        written = writer([{"role": "system", "content": WRITER_SYSTEM},
                          {"role": "user", "content": f"Question: {question}\n\nPages read:\n\n{pages}"}])
        if written and written[0].strip():
            report = written[0].strip()
            credit = f"Pages found and read by {MODEL} on this PC. Report: {written[1]}"
        elif written:
            credit += f" {written[1]}"
    on_step("  " + credit.splitlines()[0])
    # Always append the real reading list, so citations can be checked against what was read
    report += f"\n\n---\n**Who did the work:** {credit}\n\nPages actually read by the agent:\n" + "\n".join(
        f"[{i}] {t} - {u}" for i, (t, u) in enumerate(r.sources, 1))
    report_path = out_dir / f"{stamp}.md"
    report_path.write_text(f"# {question}\n\n{report}\n", encoding="utf-8")
    log.close()
    return report, report_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    start = time.time()
    report, path = run(" ".join(sys.argv[1:]), on_step=lambda s: print(s, flush=True))
    print("\n" + report)
    print(f"\nSaved to {path} ({time.time() - start:.0f}s)")
