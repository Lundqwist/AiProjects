# AI projects

Five AI engineering projects, built to run fully locally on one PC (RTX 3080 Ti, 12 GB) with [Ollama](https://ollama.com). No API keys, no cloud.

| # | Project | Status |
|---|---|---|
| 01 | [RAG document assistant](01-rag/) | Done |
| 02 | AI voice agent | Planned |
| 03 | Invoice automation | Planned |
| 04 | Research agent | Planned |
| 05 | AI evaluation platform | Planned |

## Setup

```powershell
pip install --user uv
uv venv --python 3.12
.\.venv\Scripts\Activate.ps1
uv pip install ollama chromadb pypdf streamlit
ollama pull gemma3:12b
ollama pull nomic-embed-text
ollama pull qwen3:8b
```

Each project folder has its own README with how it works and how to run it.
