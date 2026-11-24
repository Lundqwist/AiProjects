# 01 · RAG document assistant

Ask questions about your own PDFs, Markdown and text files. Everything runs locally: no API keys, no data leaves the PC.

**Stack:** Python 3.12 · [Ollama](https://ollama.com) (`nomic-embed-text` for embeddings, `gemma3:12b` for answers) · ChromaDB · Streamlit

## How it works

1. **Ingest** (`ingest.py`): each file is split into pages (PDF) or sections (Markdown headings). Markdown tables become one "column: value" line per row, so a row keeps its column names. Text is cut into ~800-character chunks with 100 characters of overlap, each prefixed with `[file > section]`, embedded, and stored in `db/`.
2. **Retrieve** (`rag.search`): the question is embedded and the 5 closest chunks (cosine distance) are fetched.
3. **Answer** (`rag.answer_stream`): the chunks go to the chat model as numbered sources. The system prompt makes it cite `[n]`, point out sources that disagree, and say "Not in the documents." when the answer isn't there.

## Run

From the `AiProjects` folder (one shared venv for all projects):

```powershell
.\.venv\Scripts\Activate.ps1
cd 01-rag
python ingest.py <file or folder>
python ask.py "What is dependency injection?"
streamlit run app.py
```

Re-ingesting a file replaces its old chunks. To start over, delete the `db` folder.

## What I learned

- **Conflicting sources.** A setup guide gave a service's default port; a device table gave the custom port one server really uses. Plain retrieval found both, and the model picked the general default. Two fixes worked: section headings in each chunk, and a prompt rule to prefer specific facts and name disagreements. Now it reports both and says which applies to that server.
- **Table rows lose context** when chunked as plain text. Turning each row into `column: value` pairs fixed it.
- `nomic-embed-text` expects `search_document:` / `search_query:` prefixes.

- **Still wrong sometimes.** Asked which devices may read a dashboard feed, it named the right TV but added "the Pi" (the feed runs *on* the Pi; the other allowed client is a PC). The source chunk was right; the 12B model blurred "runs on" and "may read". This is what project 05 should catch.

- **Found by project 05's tests** (80 % → 100 %): questions in Swedish found nothing because `nomic-embed-text` is English-only, so non-English questions are now also searched as an English translation. Each chunk carries its document's title, so an "(old, 2024)" FAQ loses to the current handbook. The prompt asks for complete answers, including conditions and limits.

## Next

- Hybrid search (BM25 + vectors) for exact names and numbers.
- Reranking the top 20 down to 5.
- A multilingual embedding model (e.g. `bge-m3`) instead of translating queries.
