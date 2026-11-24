"""Shared RAG logic: chunking, embedding with Ollama, storage in ChromaDB, answering."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import chromadb
import ollama
from pypdf import PdfReader

EMBED_MODEL = "nomic-embed-text"
CHAT_MODEL = "gemma3:12b"
DB_DIR = Path(os.environ.get("RAG_DB") or Path(__file__).parent / "db")  # RAG_DB: use another database
COLLECTION = "documents"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
TOP_K = 5
TEXT_SUFFIXES = {".md", ".txt"}

SYSTEM_PROMPT = """You answer questions using ONLY the numbered sources given.
Cite every claim with its source number in square brackets, like [2].
Give the complete answer, including conditions and limits stated in the sources.
Prefer specific facts (a named device, an exact value) over general defaults or instructions.
If sources disagree, give the one from the newer or more specific document as the answer,
and mention the other as outdated or conflicting, with both source numbers.
If the sources do not contain the answer, reply exactly: "Not in the documents."
Answer in the language of the question. Be concise."""


@dataclass
class Hit:
    text: str
    source: str
    page: int
    distance: float

    section: str = ""

    @property
    def label(self) -> str:
        where = f"p. {self.page}" if self.page else self.section
        return f"{self.source}, {where}" if where else self.source


def collection():
    client = chromadb.PersistentClient(path=str(DB_DIR))
    return client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def read_pages(path: Path) -> list[tuple[int, str, str]]:
    """Return (page number, section heading, text). Text files count as page 0;
    Markdown is split at its headings so each chunk knows which section it came from."""
    if path.suffix.lower() == ".pdf":
        reader = PdfReader(path)
        return [(i + 1, "", page.extract_text() or "") for i, page in enumerate(reader.pages)]
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() != ".md":
        return [(0, "", text)]
    sections, heading, lines = [], "", []
    for line in text.splitlines():
        if line.startswith("#"):
            sections.append((0, heading, "\n".join(lines)))
            heading, lines = line.lstrip("#").strip(), []
        else:
            lines.append(line)
    sections.append((0, heading, "\n".join(lines)))
    return [s for s in sections if s[2].strip()]


def doc_title(path: Path) -> str:
    """The first Markdown heading, or '' for other files."""
    if path.suffix.lower() != ".md":
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def table_rows(text: str) -> list[str] | None:
    """If the text is a Markdown table, return one 'header: value' string per row,
    so a row keeps its column names after chunking."""
    lines = [l for l in text.splitlines() if l.strip()]
    rows = [l for l in lines if l.strip().startswith("|")]
    if len(rows) < 3 or len(rows) < len(lines) * 0.8:
        return None
    cells = lambda l: [c.strip() for c in l.strip().strip("|").split("|")]
    header = cells(rows[0])
    out = []
    for row in rows[2:]:  # rows[1] is the |---| separator
        out.append("; ".join(f"{h}: {v}" for h, v in zip(header, cells(row)) if v))
    return out


def chunk(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = " ".join(text.split())
    if not text:
        return []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):  # prefer to cut at a sentence or word boundary
            cut = max(text.rfind(". ", start, end), text.rfind(" ", start, end))
            if cut > start + size // 2:
                end = cut + 1
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = end - overlap
    return chunks


def embed(texts: list[str], kind: str) -> list[list[float]]:
    # nomic-embed-text expects these task prefixes for best retrieval quality
    prefix = "search_document: " if kind == "document" else "search_query: "
    return ollama.embed(model=EMBED_MODEL, input=[prefix + t for t in texts]).embeddings


def ingest_file(path: Path, root: Path) -> int:
    col = collection()
    source = str(path.relative_to(root)) if path.is_relative_to(root) else path.name
    col.delete(where={"source": source})  # re-ingesting a file replaces its old chunks
    ids, docs, metas = [], [], []
    sections = read_pages(path)
    # The document's title (its first heading) goes in every chunk: "IT FAQ (old, 2024)" matters
    title = doc_title(path)
    for s, (page, heading, text) in enumerate(sections):
        parts = list(dict.fromkeys(p for p in [source, title, heading] if p))  # drop repeats
        prefix = f"[{' > '.join(parts)}] "
        rows = table_rows(text)
        pieces = [p for row in rows for p in chunk(row)] if rows else chunk(text)
        for n, piece in enumerate(pieces):
            ids.append(hashlib.sha1(f"{source}|{page}|{s}|{n}".encode()).hexdigest())
            docs.append(prefix + piece)
            metas.append({"source": source, "page": page, "section": heading})
    for i in range(0, len(docs), 64):
        col.add(ids=ids[i:i + 64], documents=docs[i:i + 64],
                embeddings=embed(docs[i:i + 64], "document"), metadatas=metas[i:i + 64])
    return len(docs)


def english_query(question: str) -> str:
    """nomic-embed-text only understands English, so a question in another language
    is also searched as an English translation."""
    if question.isascii():
        return question
    reply = ollama.chat(model=CHAT_MODEL, options={"temperature": 0}, messages=[{"role": "user", "content":
        f"Translate this search question to English. Reply with the translation only.\n\n{question}"}])
    return reply.message.content.strip()


def search(question: str, k: int = TOP_K) -> list[Hit]:
    col = collection()
    if col.count() == 0:
        return []
    queries = list(dict.fromkeys([question, english_query(question)]))
    res = col.query(query_embeddings=embed(queries, "query"), n_results=min(k, col.count()))
    best: dict[str, Hit] = {}  # merge the result lists, keeping each chunk's best distance
    for docs, metas, dists in zip(res["documents"], res["metadatas"], res["distances"]):
        for doc, meta, dist in zip(docs, metas, dists):
            if doc not in best or dist < best[doc].distance:
                best[doc] = Hit(doc, meta["source"], meta["page"], dist, meta.get("section", ""))
    return sorted(best.values(), key=lambda h: h.distance)[:k]


def build_messages(question: str, hits: list[Hit]) -> list[dict]:
    sources = "\n\n".join(f"[{i}] ({h.label})\n{h.text}" for i, h in enumerate(hits, 1))
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Sources:\n{sources}\n\nQuestion: {question}"},
    ]


def answer_stream(question: str, hits: list[Hit], model: str = CHAT_MODEL):
    """Yield the answer text piece by piece."""
    if not hits:
        yield "Not in the documents. (The database is empty: run ingest.py first.)"
        return
    extra = {"think": False} if model.startswith("qwen3") else {}
    for part in ollama.chat(model=model, messages=build_messages(question, hits),
                            stream=True, options={"temperature": 0.2}, **extra):
        yield part.message.content
