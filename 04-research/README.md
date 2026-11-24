# 04 · Research agent

Give it a question; it searches the web, chooses pages to read, and writes a Markdown report with numbered sources.

**Stack:** Ollama `qwen3:8b` with tool calling · [ddgs](https://pypi.org/project/ddgs/) (DuckDuckGo search, no API key) · [trafilatura](https://trafilatura.readthedocs.io) (page text)

## How it works

An agent loop, written by hand (no framework):

1. The model gets two tools, `web_search(query)` and `read_page(url)`, as plain Python functions; the ollama library builds the tool schema from their docstrings.
2. Each round: if the reply has tool calls, run them and send the results back; otherwise it's the final report. Max 10 rounds.
3. It must read at least 2 pages; if it tries to finish earlier it's told to keep reading.
4. Pages are cut to 4 000 characters so an 8B model keeps track.
5. The report gets the real reading list appended, and every tool call and full result is logged to `reports\<time>.log.jsonl`.

## Run

```powershell
.\.venv\Scripts\Activate.ps1
cd 04-research
python agent.py "What is new in .NET 10 for ASP.NET Core developers?"
python check_report.py      # grounding check on the newest report
```

## What I learned

- **The first version invented a CVE number.** It wasn't in any page the agent read. Three changes fixed it: search snippets are labelled "not sources", the prompt forbids invented numbers or IDs, and at least 2 pages must be read.
- **`check_report.py`** lists every number, version, ID and `code name` in a report that never appeared in the logged tool results. The fixed agent's report: 13 checked, 0 missing. It's a cheap check that catches made-up specifics, not wrong reasoning.
- Full logs are what make an agent debuggable: with only 500 characters per tool result I couldn't tell where a claim came from.
