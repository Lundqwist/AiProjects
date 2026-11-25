# AI Workbench for VS Code

Coding help inside VS Code from **your own AI Workbench**: every answer comes from the local experts on your
PC (Ollama), never from an internet AI.

- **Chat** in the AI Workbench view in the activity bar (Ctrl+Alt+A). With *Include the open file / selection*
  ticked, your selection (or, with nothing selected, the open file) goes along with the question.
  Code blocks in answers have **Insert**, **Replace selection** and **Copy**.
- **Right-click** selected code: *Explain this code*, *Find and fix problems*, *Write tests*.
- **Change this code…** (Ctrl+Alt+E): say what to change; the code expert's version opens as a diff,
  and nothing changes until you click *Apply* (Ctrl+Z undoes it).

## How it connects

AI Workbench runs a small server for this extension on `127.0.0.1:8765` (Settings → VS Code in AI
Workbench). It answers only this PC, only with the token AI Workbench keeps in
`%APPDATA%\AI Workbench\bridge-token`, and refuses requests from web pages. The extension reads the token
from there, so there is nothing to paste. AI Workbench must be running.

To keep each request small, only the selection (or the open file, cut to about 12 000 characters around the
cursor) and the last few chat messages, each cut short, are sent.

Install it with **Install in VS Code** in AI Workbench → Settings → VS Code, which builds the extension
package and installs it with `code --install-extension`.
