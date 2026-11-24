# AI projects

Five AI engineering projects, built to run fully locally on one PC (RTX 3080 Ti, 12 GB) with [Ollama](https://ollama.com). No API keys, no cloud.

| # | Project | What it does |
|---|---|---|
| 01 | [RAG document assistant](01-rag/) | Answers from your own PDFs and notes, with citations; says so when the answer isn't there |
| 02 | [AI voice agent](02-voice/) | Spoken conversation in English or Swedish, with a tool that reads a to-do list |
| 03 | [Invoice automation](03-invoices/) | Invoice PDFs and photos → checked CSV rows; wrong totals go to review |
| 04 | [Research agent](04-research/) | Searches, reads pages and writes a cited report, plus a check for made-up facts |
| 05 | [AI evaluation platform](05-eval/) | promptfoo tests for 01 and 03; its first run found 3 bugs in 01 (80 % → 100 %) |

## Setup

```powershell
pip install --user uv
uv venv --python 3.12
.\.venv\Scripts\Activate.ps1
uv pip install ollama chromadb pypdf streamlit pymupdf pydantic watchdog ddgs trafilatura faster-whisper piper-tts sounddevice soundfile numpy
ollama pull gemma3:12b
ollama pull nomic-embed-text
ollama pull qwen3:8b
npm install -g promptfoo
```

Each project folder has its own README with how it works and how to run it.
