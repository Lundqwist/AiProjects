// The chat panel inside VS Code. Talks only to the extension (postMessage); the extension talks to AI Workbench.
(function () {
  const vscode = acquireVsCodeApi();
  const log = document.getElementById("log");
  const input = document.getElementById("input");
  const send = document.getElementById("send");
  const stop = document.getElementById("stop");
  const status = document.getElementById("status");
  const useFile = document.getElementById("useFile");
  const expert = document.getElementById("expert");
  let current = null; // the answer being written

  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // A small Markdown renderer: fenced code (with buttons), inline code, bold, italics, headings, lists
  function render(md) {
    const out = [];
    const parts = md.split(/```/);
    parts.forEach((part, i) => {
      if (i % 2 === 1) {
        const nl = part.indexOf("\n");
        const lang = nl >= 0 ? part.slice(0, nl).trim() : "";
        const code = nl >= 0 ? part.slice(nl + 1) : part;
        const done = i < parts.length - 1; // a block still being written gets no buttons yet
        out.push(`<div class="code"><div class="codebar"><span>${esc(lang)}</span>` +
          (done ? `<button data-act="insert">Insert</button><button data-act="replace">Replace selection</button>` +
                  `<button data-act="copy">Copy</button>` : "") +
          `</div><pre><code>${esc(code.replace(/\n$/, ""))}</code></pre></div>`);
      } else {
        let h = esc(part)
          .replace(/`([^`\n]+)`/g, "<code>$1</code>")
          .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
          .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<i>$2</i>")
          .replace(/^#{1,6} (.*)$/gm, "<b>$1</b>")
          .replace(/^\s*[-*] (.*)$/gm, "• $1");
        h = h.replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br>");
        if (h.trim()) out.push(`<p>${h}</p>`);
      }
    });
    return out.join("");
  }

  function add(cls, html) {
    const div = document.createElement("div");
    div.className = "msg " + cls;
    div.innerHTML = html;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  function ask() {
    const text = input.value.trim();
    if (!text || send.disabled) return;
    input.value = "";
    vscode.postMessage({ type: "ask", text, useFile: useFile.checked, expert: expert.value });
  }

  send.addEventListener("click", ask);
  stop.addEventListener("click", () => vscode.postMessage({ type: "stop" }));
  status.addEventListener("click", () => vscode.postMessage({ type: "retry" }));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      ask();
    }
  });
  log.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const code = btn.closest(".code").querySelector("code").textContent;
    vscode.postMessage({ type: btn.dataset.act, code });
  });

  window.addEventListener("message", (e) => {
    const m = e.data;
    if (m.type === "init") {
      expert.value = m.expert;
      useFile.checked = m.sendFile;
    } else if (m.type === "status") {
      status.textContent = m.text + (m.ok ? "" : " (click to try again)");
      status.className = m.ok ? "ok" : "bad";
    } else if (m.type === "user") {
      add("user", render(m.text) + (m.where ? `<div class="meta">with the ${esc(m.where)}</div>` : ""));
    } else if (m.type === "start") {
      current = add("bot", `<div class="who">${esc(m.who)} <span class="meta">· ${esc(m.why)}</span></div>` +
        `<div class="body"><i>Thinking…</i></div>`);
    } else if (m.type === "text") {
      if (!current) current = add("bot", `<div class="body"></div>`);
      current.querySelector(".body").innerHTML = render(m.text);
      log.scrollTop = log.scrollHeight;
    } else if (m.type === "done") {
      if (current) {
        const t = m.tokens && (m.tokens[0] || m.tokens[1]) ? ` · ${m.tokens[0]} tokens in, ${m.tokens[1]} out` : "";
        current.insertAdjacentHTML("beforeend", `<div class="meta">${esc(m.by || "")}${t}</div>`);
      }
      current = null;
    } else if (m.type === "error") {
      add("error", esc(m.text));
      current = null;
    } else if (m.type === "busy") {
      send.disabled = m.busy;
      stop.disabled = !m.busy;
    } else if (m.type === "clear") {
      log.innerHTML = "";
      current = null;
    }
  });

})();
