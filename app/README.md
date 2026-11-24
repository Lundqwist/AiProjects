# AI Workbench

One Windows program for the five projects: settings in one place, file handling with dialogs and drag-and-drop, a push-to-talk voice tab with device choice, and the test suites one click away. Everything runs locally on Ollama.

**Stack:** Python 3.12 · PySide6 (Qt 6) · the project modules in `01-rag` … `05-eval`, imported directly

| Tab | What you do there |
|---|---|
| **Documents** | Add PDFs, Markdown and text (Add files / Add folder / drag-and-drop), see each file's chunk count, remove files, and chat with answers that cite their sources |
| **Voice** | Hold to talk (or click to start/stop), live input level, the conversation as text, a box for typed messages, Stop speaking, New conversation |
| **Invoices** | Add invoices (they're copied to the inbox), process now or watch the inbox, a table of processed invoices, the review list with the reason and a retry button, open the CSV in Excel |
| **Research** | Ask a question, watch the agent's steps live, read the report, and see the grounding check (numbers/IDs that weren't in any page it read); earlier reports in a list |
| **TV** | Find a Samsung TV on the network, turn it on (Wake-on-LAN) or off, favourites such as "CNN = Samsung TV Plus, channel 4556" (one click wakes the TV, opens the app and types the channel), open apps, type channel numbers, and a remote (arrows, OK, Back, Home, volume, channels, guide) |
| **Evaluation** | Run the RAG or invoice test suite, failures first with the reason, open promptfoo's web viewer |
| **Settings** | Models per task (filled from Ollama), folders, microphone and speakers, speech model and device, voices (with Listen and a voice library), speed and volume, push-to-talk, answer/research tuning, Vikunja, promptfoo, updates |

## What the assistant may do (Access tab)

`wb_access.py` gives the voice assistant tools on three levels:

| Level | Tools | How |
|---|---|---|
| **Free** | open a program on the allowed list, check whether services are up (a TCP connection to each), read tasks, the TV | at once |
| **Asks first** | open any other Start-menu program (*Allow once* / *Always allow* / *Don't allow*), add or finish a Vikunja task, restart a container from the allowed list (over ssh), media keys | a question on screen with a chime and a flashing taskbar button; only a click answers it, never a voice, so a TV or a visitor can't say yes; no answer in 90 s is a no |
| **Never** | delete files, send messages, payments, system settings | no tool exists. Programs that could do them (PowerShell, Terminal, Command Prompt, Registry Editor, Control Panel, Settings, WSL, uninstallers, security tools) are refused even with permission |

Programs come from the Start menu (`Get-StartApps`) and open the way a click there does (`explorer shell:AppsFolder\<AppID>`), so desktop and Store apps both work. `wb_permission.py` carries a tool's question from its worker thread to the window and back.

**Tested** (a fake "click" for each answer; launches, Vikunja writes, restarts and key presses recorded, not done): every forbidden program refused without asking, allowed programs open without asking, *Don't allow* / *Allow once* / *Always allow* each behave as named, status up/down, tasks and restarts only after a yes, a container not on the list refused. The model picked the right tool for "Open Spotify", "Is the NAS up?", "Add buy milk to my to-do list", "Starta om pihole", "Pause the music" and "Open PowerShell" (refused), and did nothing for "Delete everything in my Documents folder". The on-screen question was tested for a click from another thread, a timeout and a click that came too late.

## Listening modes

The mode button on the Voice tab (or F2) switches between three modes, and the program starts in the one used last:

| Mode | How you talk |
|---|---|
| **Hey Luna** | say the wake word, then the question (below) |
| **Conversation** | just talk: every sentence is a question, and follow-ups keep the context. It ends after 60 s of silence (*Conversation ends after* in Settings), or when you say "stop listening" / "sluta lyssna". Phrases Whisper "hears" in noise ("Thank you.", "Tack för att du tittade") are ignored, and listening resumes 0.5 s after the assistant stops talking, so it doesn't answer itself |
| **Hold to talk** | nothing listens in the background; hold the big button |

The big hold-to-talk button works in every mode. Conversation mode answers everything said near the microphone, including a talking TV, so use it when the room is quiet.

## Wake word

Voice tab → mode *Hey Luna*. Then just talk: "Hey Luna, put CNN on", or "Hey Luna" and, after the chime, the question. Pick your own word or phrase in Settings.

How `wb_wake.py` does it, all on this PC:

1. A level detector that adapts to the room splits the microphone into bursts of speech; silence costs nothing.
2. Each burst goes through Whisper *tiny* on the CPU, about 0.4 s for 2 s of speech, with the wake word as a hotword.
3. A fuzzy match that goes word by word finds the wake word. It accepts "Lona" and "Hej Luna" for "Hey Luna", and rejects "lunar" and "the lunar module, hey there". It's tuned with a table of cases, including English and Swedish greetings.
4. The command's **audio** goes to the Voice tab, which transcribes it with the better model and removes the wake word. Tiny is good enough to spot a word but not for Swedish sentences: it heard "sätt på CNN på tv:n" as "Sett på scenen på TVN".
5. While the assistant thinks and speaks, the listener pauses, so it doesn't hear itself. Speech from that time is thrown away.

**Tested** with spoken audio from Piper fed in real time with background noise: English and Swedish in one breath, the wake word followed by a short pause, speech without the wake word (ignored), "lunar eclipse" (ignored), and the wake word spoken over news-style talk. The self-test runs two of these cases every time.

**Limits:** it can't tell your voice from the TV's. Spoken over a talking TV, the wake word is found and the TV talk before it is removed, but TV talk right after your question can slip into it. Pause or mute the TV, or sit closer to the microphone than the TV is. Every word said near the microphone is transcribed locally, and the last thing heard is shown on the Voice tab; nothing is saved or sent anywhere.

## TV

`wb_tv.py` talks to Samsung Tizen TVs (2016 and later) on the local network: their REST API on port 8001 for finding the TV and its power state, Wake-on-LAN to turn it on, and the remote-control websocket on port 8002 for buttons and apps. Nothing about the TV is in the code: *Find TV* scans the local network and stores the TV's address and MAC in the settings.

- **On the TV, once:** turn on Settings → General → Network → Expert Settings → *Power On with Mobile*, or Wake-on-LAN does nothing. The first command shows an *Allow* question for "AI Workbench"; the pairing token is kept in `%APPDATA%\AI Workbench\tv-token`.
- **Voice:** when a TV is set up, the voice assistant gets four tools (`wb_tv_tools.py`): `tv_power`, `tv_watch` (a favourite, an app or a channel number), `tv_volume` and `tv_status`. "Put CNN on the TV", "turn the volume down two steps" and "Höj volymen på tv:n två steg" each picked the right tool in testing.
- **Limits:** the TV can't say what's on screen, only whether it's on. It answers only the app list it was built with; other apps can be opened by their app id (`sdb shell 0 applist` lists them in Developer Mode).

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
