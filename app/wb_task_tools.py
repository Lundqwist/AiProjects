"""Tools for the Documents, Invoices and Research abilities, so the conversation can use them too.

ask_my_documents answers from the user's own documents (the Documents panel's database), on this PC
unless Settings allows online AI for documents. process_invoices reads the invoice inbox like the
Invoices panel does. research_report writes a full cited report in the background and announces it.
"""
from __future__ import annotations

import csv
import threading
import traceback
from datetime import datetime
from pathlib import Path

import wb_ai
import wb_notify
from wb_settings import Settings

_settings: Settings | None = None
_on_invoices = None   # set by the Invoices panel: refreshes its table after the assistant read invoices
_on_report = None     # set by the Research panel: lists the new report


def configure(s: Settings, on_invoices=None, on_report=None) -> None:
    global _settings, _on_invoices, _on_report
    _settings = s
    if on_invoices:
        _on_invoices = on_invoices
    if on_report:
        _on_report = on_report


def _s() -> Settings:
    return _settings or Settings.load()


def ask_my_documents(question: str) -> str:
    """Answer a question from the user's own documents (PDFs and notes they added to Documents), with
    numbered sources. Pass on the answer and name the documents it came from.

    Args:
        question: the question, with every detail needed
    """
    import rag
    s = _s()
    hits = rag.search(question)
    if not hits:
        return "The document database is empty: the user can add files in the Documents panel."
    a = wb_ai.ask(s, rag.build_messages(question, hits), task="documents", expert="general",
                  big=None if s.online_for_documents else False, temperature=s.temperature)
    files = list(dict.fromkeys(Path(h.source).name for h in hits))
    return (f"Answer from {a.by} {a.where}, from {len(hits)} passages in: {', '.join(files)}"
            + ("" if a.online else " (nothing was sent online)") + f"\n\n{a.text}")


def invoice_summary() -> str:
    """Say how many invoices are waiting in the inbox, how many have been read into the CSV file, and
    which need review and why."""
    s = _s()
    inbox, out = Path(s.invoice_inbox), Path(s.invoice_output)
    waiting = [p.name for p in inbox.glob("*") if p.is_file()] if inbox.exists() else []
    rows = []
    csv_path = out / "invoices.csv"
    if csv_path.exists():
        with csv_path.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    review = out / "needs-review"
    problems = {p.name.removesuffix(".txt"): p.read_text(encoding="utf-8").splitlines()[0]
                for p in review.glob("*.txt")} if review.exists() else {}
    last = [f"{r['supplier']} {r['invoice_number']}: {r['total']} {r['currency']}" for r in rows[-5:]]
    return (f"{len(waiting)} waiting in the inbox; {len(rows)} read into {csv_path}"
            + (f" (latest: {'; '.join(last)})" if last else "")
            + (f"; {len(problems)} need review: " + "; ".join(f"{k}: {v}" for k, v in problems.items())
               if problems else "; none need review"))


def process_invoices() -> str:
    """Read every invoice waiting in the inbox (PDFs and photos) into checked rows of a CSV file. Ones
    whose numbers don't add up are put aside for review. Say how many were read and which need review."""
    import invoice
    s = _s()
    inbox, out = Path(s.invoice_inbox), Path(s.invoice_output)
    files = sorted(p for p in inbox.glob("*") if p.suffix.lower() in {".pdf"} | invoice.IMAGE_SUFFIXES) \
        if inbox.exists() else []
    if not files:
        return f"No invoices are waiting in the inbox ({inbox})."
    ok, bad = [], []
    for p in files:
        good, msg = invoice.process(p, out)
        (ok if good else bad).append(f"{p.name}: {msg}")
    if _on_invoices:
        _on_invoices()
    return (f"Read {len(ok)} of {len(files)} into {out / 'invoices.csv'}"
            + (f": {'; '.join(ok)}" if ok else "") + (f". Need review: {'; '.join(bad)}" if bad else "."))


def research_report(question: str) -> str:
    """Start a full research report: the web is searched, pages are read, and a cited report is written
    (an online AI writes it while it has tokens). It takes a few minutes and opens by itself when ready;
    tell the user that.

    Args:
        question: what to research, with every detail
    """
    import agent
    s = _s()
    out_dir = Path(s.reports_dir)

    def writer(messages):
        a = wb_ai.ask(s, messages, task="report", local_ok=False, temperature=0.2)
        if a is None:
            return None
        return (a.text, a.credit()) if a.text else ("", "Online AI not used: " + "; ".join(a.notes))

    def run():
        try:
            _, path = agent.run(question, on_step=lambda m: None, out_dir=out_dir, writer=writer)
        except Exception:  # noqa: BLE001 - nobody waits for this thread: log it, and say so
            print(f"[{datetime.now():%H:%M:%S}] research report failed:", flush=True)
            traceback.print_exc()
            wb_notify.announce(f"The research report on “{question}” failed; the details are in the log.")
            return
        if _on_report:
            _on_report(path)
        import wb_common
        wb_common.open_path(path)
        first = path.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
        wb_notify.announce(f"The research report “{first}” is ready and open ({path.name}).", str(path))

    threading.Thread(target=run, name="research", daemon=True).start()
    return "Started the report. It takes a few minutes and opens by itself when it's ready."


TOOLS = {f.__name__: f for f in (ask_my_documents, invoice_summary, process_invoices, research_report)}
STEP_NAMES = {"ask_my_documents": "Reading your documents…", "invoice_summary": "Looking at the invoices…",
              "process_invoices": "Reading the invoices…", "research_report": "Starting the report…"}
