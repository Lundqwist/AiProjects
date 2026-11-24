"""promptfoo providers that run the real project code (not just a bare model).

promptfoo calls these with the rendered prompt; config.model picks the Ollama model under test.
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
os.environ.setdefault("RAG_DB", str(HERE / ".eval-db"))  # never touch 01-rag's own database
sys.path[:0] = [str(ROOT / "01-rag"), str(ROOT / "03-invoices")]

import rag  # noqa: E402
import invoice  # noqa: E402
import make_samples  # noqa: E402

HANDBOOK = HERE / "fixtures" / "handbook"
INVOICES = HERE / "fixtures" / "invoices"


def _ensure_handbook():
    if rag.collection().count() == 0:
        for path in sorted(HANDBOOK.glob("*.md")):
            rag.ingest_file(path, HANDBOOK)


def _ensure_invoices():
    if not any(INVOICES.glob("*.pdf")):
        INVOICES.mkdir(parents=True, exist_ok=True)
        make_samples.INBOX = INVOICES
        for sample in make_samples.SAMPLES:
            make_samples.make(*sample)


def call_rag(prompt, options, context):
    """The RAG assistant from 01-rag: retrieve from the handbook, answer with config.model."""
    _ensure_handbook()
    model = options.get("config", {}).get("model", rag.CHAT_MODEL)
    hits = rag.search(prompt)
    answer = "".join(rag.answer_stream(prompt, hits, model=model))
    return {"output": answer, "metadata": {"sources": [h.label for h in hits]}}


def call_invoice(prompt, options, context):
    """The invoice reader from 03-invoices. The prompt is the file name in fixtures/invoices."""
    _ensure_invoices()
    model = options.get("config", {}).get("model", invoice.MODEL)
    invoice.MODEL = model
    try:
        inv = invoice.extract(INVOICES / prompt.strip())
    except Exception as e:
        return {"output": json.dumps({"error": str(e)})}
    out = json.loads(inv.model_dump_json())
    out["problems"] = invoice.check(inv)
    return {"output": json.dumps(out, ensure_ascii=False)}
