// AI Workbench for VS Code: a chat and code commands answered by AI Workbench on this PC.
// It talks only to 127.0.0.1 (AI Workbench's VS Code bridge), with the token AI Workbench keeps in
// %APPDATA%\AI Workbench\bridge-token. No internet AI is ever used. No npm packages: Node's own fetch.
"use strict";

const vscode = require("vscode");
const fs = require("fs");
const path = require("path");
const os = require("os");

const PROPOSAL = "aiworkbench-proposal";

function tokenPath() {
  const appdata = process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming");
  return path.join(appdata, "AI Workbench", "bridge-token");
}

function base() {
  const port = vscode.workspace.getConfiguration("aiWorkbench").get("port", 8765);
  return `http://127.0.0.1:${port}`;
}

function headers() {
  let token = "";
  try {
    token = fs.readFileSync(tokenPath(), "utf8").trim();
  } catch {
    throw new Error("AI Workbench has not made its bridge token yet. Start AI Workbench (and check that " +
      "Settings → VS Code → Bridge is on).");
  }
  return { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
}

function friendly(err) {
  const msg = String((err && err.message) || err);
  if (/fetch failed|ECONNREFUSED/i.test(msg)) {
    return "AI Workbench is not running, or its VS Code bridge is off. Start AI Workbench and try again.";
  }
  return msg;
}

async function health() {
  const r = await fetch(`${base()}/api/health`, { headers: headers() });
  if (!r.ok) throw new Error(`AI Workbench answered ${r.status}: ${await r.text()}`);
  return r.json();
}

/** The code the user means: the selection, else the open file (if allowed). */
function editorContext(includeFile) {
  const ed = vscode.window.activeTextEditor;
  if (!ed) return {};
  const doc = ed.document;
  const ctx = { file: vscode.workspace.asRelativePath(doc.uri), language: doc.languageId };
  if (!ed.selection.isEmpty) ctx.selection = doc.getText(ed.selection);
  else if (includeFile) ctx.text = doc.getText();
  return ctx;
}

class ChatView {
  constructor(context) {
    this.context = context;
    this.view = undefined;
    this.messages = [];      // the chat, kept here; AI Workbench gets only the last few, cut short
    this.abort = undefined;
    this.queue = [];         // questions asked before the view was opened
  }

  resolveWebviewView(view) {
    this.view = view;
    const media = vscode.Uri.joinPath(this.context.extensionUri, "media");
    view.webview.options = { enableScripts: true, localResourceRoots: [media] };
    const nonce = Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
    const css = view.webview.asWebviewUri(vscode.Uri.joinPath(media, "chat.css"));
    const js = view.webview.asWebviewUri(vscode.Uri.joinPath(media, "chat.js"));
    view.webview.html = `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${view.webview.cspSource};
 script-src 'nonce-${nonce}';">
<link rel="stylesheet" href="${css}"></head><body>
<div id="status">Connecting to AI Workbench…</div>
<div id="log"></div>
<div id="bar">
  <label title="Send the selection, or the open file when nothing is selected">
    <input type="checkbox" id="useFile" checked> Include the open file / selection</label>
  <select id="expert" title="Which local expert answers">
    <option value="auto">Auto</option><option value="code">Code expert</option>
    <option value="reasoning">Reasoning expert</option><option value="writing">Writing expert</option>
  </select>
</div>
<textarea id="input" rows="3" placeholder="Ask about your code (Enter sends, Shift+Enter new line)"></textarea>
<div id="buttons"><button id="send">Send</button><button id="stop" disabled>Stop</button></div>
<script nonce="${nonce}" src="${js}"></script></body></html>`;
    view.webview.onDidReceiveMessage((m) => this.onMessage(m));
    const expert = vscode.workspace.getConfiguration("aiWorkbench").get("expert", "auto");
    view.webview.postMessage({ type: "init", expert,
      sendFile: vscode.workspace.getConfiguration("aiWorkbench").get("sendOpenFile", true) });
    this.checkHealth();
    for (const q of this.queue.splice(0)) this.ask(q.text, q.ctx);
  }

  async checkHealth() {
    try {
      const h = await health();
      const e = h.experts || {};
      this.post({ type: "status", ok: true,
        text: `Connected to AI Workbench on this PC · code: ${e.code} · reasoning: ${e.reasoning} · ` +
              `writing: ${e.writing} · no internet AI` });
    } catch (err) {
      this.post({ type: "status", ok: false, text: friendly(err) });
    }
  }

  post(msg) {
    if (this.view) this.view.webview.postMessage(msg);
  }

  async onMessage(m) {
    if (m.type === "ask") {
      this.ask(m.text, m.useFile ? editorContext(true) : {}, m.expert);
    } else if (m.type === "stop") {
      if (this.abort) this.abort.abort();
    } else if (m.type === "insert" || m.type === "replace") {
      const ed = vscode.window.activeTextEditor || vscode.window.visibleTextEditors[0];
      if (!ed) return vscode.window.showWarningMessage("Open a file first.");
      await ed.edit((b) => {
        if (m.type === "replace" && !ed.selection.isEmpty) b.replace(ed.selection, m.code);
        else b.insert(ed.selection.active, m.code);
      });
    } else if (m.type === "copy") {
      await vscode.env.clipboard.writeText(m.code);
      vscode.window.setStatusBarMessage("Copied", 2000);
    } else if (m.type === "retry") {
      this.checkHealth();
    }
  }

  reset() {
    if (this.abort) this.abort.abort();
    this.messages = [];
    this.post({ type: "clear" });
  }

  /** Show the chat, and ask a question in it. */
  async askFromCommand(text, ctx) {
    await vscode.commands.executeCommand("aiWorkbench.chat.focus");
    if (!this.view) {
      this.queue.push({ text, ctx });
      return;
    }
    this.ask(text, ctx);
  }

  async ask(text, ctx, expert) {
    if (!text || this.abort) return;
    expert = expert || vscode.workspace.getConfiguration("aiWorkbench").get("expert", "auto");
    this.messages.push({ role: "user", content: text });
    const where = ctx.selection ? `selection in ${ctx.file}` : ctx.text ? ctx.file : "";
    this.post({ type: "user", text, where });
    this.abort = new AbortController();
    this.post({ type: "busy", busy: true });
    let answer = "";
    try {
      const r = await fetch(`${base()}/api/chat`, {
        method: "POST", headers: headers(), signal: this.abort.signal,
        body: JSON.stringify({ messages: this.messages, context: ctx, expert }),
      });
      if (!r.ok) throw new Error(`AI Workbench answered ${r.status}: ${await r.text()}`);
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          const ev = JSON.parse(line);
          if (ev.type === "meta") this.post({ type: "start", who: `${ev.expert} (${ev.model})`, why: ev.why });
          else if (ev.type === "text") { answer += ev.text; this.post({ type: "text", text: answer }); }
          else if (ev.type === "done") this.post({ type: "done", by: ev.by, tokens: ev.tokens });
          else if (ev.type === "error") throw new Error(ev.error);
        }
      }
    } catch (err) {
      if (err.name === "AbortError") this.post({ type: "done", by: "stopped" });
      else this.post({ type: "error", text: friendly(err) });
    } finally {
      if (answer) this.messages.push({ role: "assistant", content: answer });
      else this.messages.pop();
      this.abort = undefined;
      this.post({ type: "busy", busy: false });
    }
  }
}

/** Change the selection (or the whole file) by an instruction, shown as a diff before it is applied. */
async function editCode(proposals) {
  const ed = vscode.window.activeTextEditor;
  if (!ed) return vscode.window.showWarningMessage("Open a file first.");
  const doc = ed.document;
  const range = ed.selection.isEmpty
    ? new vscode.Range(doc.positionAt(0), doc.positionAt(doc.getText().length))
    : new vscode.Range(ed.selection.start, ed.selection.end);
  const instruction = await vscode.window.showInputBox({
    title: "AI Workbench: change this code",
    prompt: ed.selection.isEmpty ? "What should change in the whole file?" : "What should change in the selection?",
    placeHolder: "e.g. add error handling, rename x to count, make it async",
  });
  if (!instruction) return;
  const original = doc.getText(range);
  let result;
  try {
    result = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "AI Workbench's code expert is working…",
        cancellable: false },
      async () => {
        const r = await fetch(`${base()}/api/edit`, {
          method: "POST", headers: headers(),
          body: JSON.stringify({ instruction, text: original, language: doc.languageId,
            file: vscode.workspace.asRelativePath(doc.uri) }),
        });
        if (!r.ok) throw new Error(`AI Workbench answered ${r.status}: ${await r.text()}`);
        return r.json();
      });
  } catch (err) {
    return vscode.window.showErrorMessage(friendly(err));
  }
  // The expert's code ends with a newline; keep the original's ending instead
  const code = result.code.replace(/\n$/, original.endsWith("\n") ? "\n" : "");
  const id = String(Date.now());
  const before = doc.getText().slice(0, doc.offsetAt(range.start));
  const after = doc.getText().slice(doc.offsetAt(range.end));
  proposals.set(id, before + code + after);
  const proposed = vscode.Uri.from({ scheme: PROPOSAL, path: `/${path.basename(doc.fileName)}`, query: id });
  await vscode.commands.executeCommand("vscode.diff", doc.uri, proposed,
    `${path.basename(doc.fileName)} ↔ AI Workbench's change`);
  const pick = await vscode.window.showInformationMessage(
    `${result.by} proposes this change. Apply it?`, { modal: false }, "Apply", "Discard");
  if (pick === "Apply") {
    await ed.edit((b) => b.replace(range, code));
    vscode.window.setStatusBarMessage("AI Workbench: change applied (Ctrl+Z undoes it)", 4000);
  }
  proposals.delete(id);
}

function activate(context) {
  const chat = new ChatView(context);
  const proposals = new Map();
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("aiWorkbench.chat", chat, {
      webviewOptions: { retainContextWhenHidden: true } }),
    vscode.workspace.registerTextDocumentContentProvider(PROPOSAL, {
      provideTextDocumentContent: (uri) => proposals.get(uri.query) || "" }),
    vscode.commands.registerCommand("aiWorkbench.open", () => vscode.commands.executeCommand("aiWorkbench.chat.focus")),
    vscode.commands.registerCommand("aiWorkbench.newChat", () => chat.reset()),
    vscode.commands.registerCommand("aiWorkbench.explain", () =>
      chat.askFromCommand("Explain what this code does, step by step.", editorContext(false))),
    vscode.commands.registerCommand("aiWorkbench.fix", () =>
      chat.askFromCommand("Find bugs and problems in this code and show the fixed code.", editorContext(false))),
    vscode.commands.registerCommand("aiWorkbench.tests", () =>
      chat.askFromCommand("Write unit tests for this code, using the usual test framework for its language.",
        editorContext(false))),
    vscode.commands.registerCommand("aiWorkbench.edit", () => editCode(proposals)),
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
