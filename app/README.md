# AI Workbench

One Windows program for the five projects: settings in one place, file handling with dialogs and drag-and-drop, a push-to-talk voice tab with device choice, and the test suites one click away. Everything runs locally on Ollama.

**Stack:** Python 3.12 · PySide6 (Qt 6) · the project modules in `01-rag` … `05-eval`, imported directly

| Tab | What you do there |
|---|---|
| **Documents** | Add PDFs, Markdown and text (Add files / Add folder / drag-and-drop), see each file's chunk count, remove files, and chat with answers that cite their sources |
| **Voice** | Hold to talk (or click to start/stop), live input level, the conversation as text, a box for typed messages, Stop speaking, New conversation |
| **Invoices** | Add invoices (they're copied to the inbox), process now or watch the inbox, a table of processed invoices, the review list with the reason and a retry button, open the CSV in Excel |
| **Research** | Ask a question, watch the agent's steps live, read the report, and see the grounding check (numbers/IDs that weren't in any page it read); earlier reports in a list |
| **Evaluation** | Run the RAG or invoice test suite, failures first with the reason, open promptfoo's web viewer |
| **Settings** | Models per task (filled from Ollama), folders, microphone and speakers, speech model and device, voices (with Listen and a voice library), speed and volume, push-to-talk, answer/research tuning, Vikunja, promptfoo, updates |

## Voices

The default voices are female: **Amy** (English, US) and **Alma** (Swedish). Settings → *Voice library…* lists every English and Swedish Piper voice, marked female or male, with a filter; *Listen* downloads a voice (about 60 MB) and plays a sample, and *Use this voice* picks it. Piper's catalog has no gender field, so `wb_voices.py` keeps a list of the single-speaker voices whose speaker is known.

All installed voices were checked by running a sample through Whisper; each was understood word for word (apart from "i dag" vs "idag").

## Updates at startup

When the program opens, it checks in the background (about 10 seconds, without blocking anything):

| Check | Needed (pre-ticked) | Optional |
|---|---|---|
| Ollama | not running; a model your settings use isn't downloaded | a newer build of a model (its manifest digest differs from Ollama's registry); a newer Ollama release (you install it) |
| Voices | a chosen voice isn't downloaded | |
| Python packages | missing or older than `requirements.txt` | a newer release on PyPI |
| The program | | new commits on GitHub (`git pull --ff-only`) |
| promptfoo | | not installed, or a newer version on npm |

Nothing is installed without your click. Models, voices and promptfoo install while the program runs. Python packages and program code are in use while it runs, so `apply_updates.py` applies them after the program closes, in a small window, and then starts it again. Its log is `%APPDATA%\AI Workbench\update.log`. *Not now* hides the optional updates shown until a newer version comes out. Turn the startup check off, or run it by hand, in Settings → Integrations → Updates.

## Run

After the setup in the main README:

```powershell
powershell -ExecutionPolicy Bypass -File app\install-shortcut.ps1   # Start menu; add -Desktop for a desktop icon
```

Then start **AI Workbench** from the Start menu, or run `.venv\Scripts\pythonw.exe app\main.py`.

## Where things are stored

| What | Where |
|---|---|
| Settings | `%APPDATA%\AI Workbench\settings.json` (plain JSON, no secrets) |
| Log (when started without a console) | `%APPDATA%\AI Workbench\workbench.log` |
| Document database, invoice inbox/output, research reports | `Documents\AI Workbench\` by default; changeable in Settings |

The Vikunja token is never saved: Settings stores the *name* of the environment variable that holds it (default `VIKUNJA_TOKEN`).

## Design notes

- **Settings → modules.** The project modules read module-level names (`rag.CHAT_MODEL`, `agent.MAX_ROUNDS`, …) at call time, so `Settings.apply()` just sets them. The CLI scripts keep working unchanged.
- **Long work runs on a thread pool** (`wb_common.run_in_background`) and reports back through Qt signals. Invoices and voice turns each get a pool with a single thread: one model on a 12 GB GPU at a time.
- **Sound uses Windows' MME devices**, which resample for you; WASAPI refuses 16 kHz recording on a 48 kHz microphone.
- **Bugs the self-test found:** workers could be garbage-collected before their results reached the window (now kept alive until done), and two threads opening the ChromaDB folder at once crashed (one client per folder, created under a lock). After a Swedish question, a typed English message got a Swedish reply; the detected language is now sent with every turn.

## Self-test

```powershell
.venv\Scripts\python.exe app\selftest.py            # ~4 min, includes a web search
.venv\Scripts\python.exe app\selftest.py --skip-research
```

It drives the real tabs (no window) with made-up data in a temp folder and its own settings file: settings save/load, model and device lists, adding documents and a cited answer, invoices to CSV and review, a spoken Swedish question, a typed English message, speech synthesis, a research report, and the invoice test suite. Last runs: 12/12 with `--skip-research`, and the research step passed in the full run before that.
