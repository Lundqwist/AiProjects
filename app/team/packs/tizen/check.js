// The tizen pack's build check for a Samsung TV web app (Tizen 5.5, its browser is about Chrome 69):
//
//   node check.js <the app's folder (where config.xml is)>
//
//   1. every .js file parses as ES2018 (Chrome 69): no ?., ??, class fields, numeric separators, ...
//   2. no built-ins Chrome 69 doesn't have (Object.fromEntries, replaceAll, .at(), globalThis, ...)
//   3. config.xml: the start page exists, and the privileges the code needs are there
//      (tizen.tvinputdevice -> tv.inputdevice; network calls -> internet)
//   4. index.html loads only scripts that exist
//
// Prints "file:line: message" per problem and exits 1 when there is any. Uses acorn (vendor/acorn, MIT),
// copied in so nothing is downloaded.
"use strict";
const fs = require("fs");
const path = require("path");
const acorn = require("./vendor/acorn/acorn.js");

const dir = path.resolve(process.argv[2] || ".");
const problems = [];
const problem = (file, line, msg) => problems.push(`${file}:${line || "?"}: ${msg}`);
const SKIP = new Set(["node_modules", ".git", ".buildResult", ".sign", "tests", "test", "tools", "dist", "build"]);

function jsFiles(d, rel = "") {
  let out = [];
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    if (e.isDirectory()) {
      if (!SKIP.has(e.name) && !e.name.startsWith(".")) out = out.concat(jsFiles(path.join(d, e.name), rel + e.name + "/"));
    } else if (e.name.endsWith(".js")) {
      out.push(rel + e.name);
    }
  }
  return out;
}

// built-ins newer than Chrome 69 (member name -> what it is)
const NEWER_MEMBERS = Object.assign(Object.create(null), {
  fromEntries: "Object.fromEntries is Chrome 73+", matchAll: "String.matchAll is Chrome 73+",
  replaceAll: "String.replaceAll is Chrome 85+", allSettled: "Promise.allSettled is Chrome 76+",
  any: null, at: "Array/String .at() is Chrome 92+", hasOwn: "Object.hasOwn is Chrome 93+",
  findLast: "findLast is Chrome 97+", findLastIndex: "findLastIndex is Chrome 97+",
  toSorted: "toSorted is Chrome 110+", toReversed: "toReversed is Chrome 110+", groupBy: "groupBy is Chrome 117+",
  trimStart: null, trimEnd: null,
});
const NEWER_GLOBALS = Object.assign(Object.create(null), { globalThis: "globalThis is Chrome 71+", structuredClone: "structuredClone is Chrome 98+",
                        queueMicrotask: "queueMicrotask is Chrome 71+", AbortController: "AbortController is Chrome 66+ (ok)" });
delete NEWER_GLOBALS.AbortController;

function walk(node, visit) {
  if (!node || typeof node.type !== "string") return;
  visit(node);
  for (const key of Object.keys(node)) {
    const v = node[key];
    if (Array.isArray(v)) v.forEach((c) => c && typeof c.type === "string" && walk(c, visit));
    else if (v && typeof v.type === "string" && key !== "loc") walk(v, visit);
  }
}

const uses = { tvinput: null, network: null };
for (const rel of jsFiles(dir)) {
  const src = fs.readFileSync(path.join(dir, rel), "utf8");
  let ast;
  try {
    ast = acorn.parse(src, { ecmaVersion: 2018, sourceType: "script", locations: true, allowHashBang: true });
  } catch (e) {
    let what = e.message.replace(/\s*\(\d+:\d+\)$/, "");
    const at = e.loc ? src.split("\n")[e.loc.line - 1] || "" : "";
    if (/\?\.|\?\?/.test(at)) what += " (?. and ?? don't exist in Chrome 69: write a && a.b, or a != null ? a : b)";
    problem(rel, e.loc && e.loc.line, `the TV's browser (Chrome 69, ES2018) can't run this: ${what}`);
    continue;
  }
  walk(ast, (n) => {
    if (n.type === "MemberExpression" && !n.computed && n.property.type === "Identifier") {
      const msg = NEWER_MEMBERS[n.property.name];
      if (msg) problem(rel, n.loc.start.line, `${msg}: the TV's browser (Chrome 69) doesn't have it`);
      const obj = n.object;
      if (obj.type === "MemberExpression" && !obj.computed && obj.object.type === "Identifier" && obj.object.name === "tizen"
          && obj.property.name === "tvinputdevice") uses.tvinput = uses.tvinput || [rel, n.loc.start.line];
      if (obj.type === "Identifier" && obj.name === "tizen" && n.property.name === "tvinputdevice")
        uses.tvinput = uses.tvinput || [rel, n.loc.start.line];
    }
    if (n.type === "Identifier" && NEWER_GLOBALS[n.name]) problem(rel, n.loc.start.line, `${NEWER_GLOBALS[n.name]}: the TV's browser (Chrome 69) doesn't have it`);
    if ((n.type === "CallExpression" && n.callee.type === "Identifier" && n.callee.name === "fetch")
        || (n.type === "NewExpression" && n.callee.type === "Identifier" && /^(XMLHttpRequest|WebSocket|EventSource)$/.test(n.callee.name)))
      uses.network = uses.network || [rel, n.loc.start.line];
  });
}

// config.xml
const cfgPath = path.join(dir, "config.xml");
const cfg = fs.existsSync(cfgPath) ? fs.readFileSync(cfgPath, "utf8") : "";
if (!cfg) problem("config.xml", 1, "missing");
else {
  const content = (cfg.match(/<content\s+src="([^"]+)"/) || [])[1];
  if (!content) problem("config.xml", 1, "no <content src=\"...\"/>: the TV doesn't know which page to open");
  else if (!fs.existsSync(path.join(dir, content))) problem("config.xml", 1, `<content src="${content}"> doesn't exist`);
  const privileges = new Set([...cfg.matchAll(/<tizen:privilege\s+name="([^"]+)"/g)].map((m) => m[1]));
  if (uses.tvinput && !privileges.has("http://tizen.org/privilege/tv.inputdevice"))
    problem(uses.tvinput[0], uses.tvinput[1], "uses tizen.tvinputdevice, but config.xml lacks "
      + "<tizen:privilege name=\"http://tizen.org/privilege/tv.inputdevice\"/>: registerKey fails on the TV");
  if (uses.network && !privileges.has("http://tizen.org/privilege/internet"))
    problem(uses.network[0], uses.network[1], "makes network calls, but config.xml lacks "
      + "<tizen:privilege name=\"http://tizen.org/privilege/internet\"/>");
  if (content && fs.existsSync(path.join(dir, content))) {
    const html = fs.readFileSync(path.join(dir, content), "utf8");
    for (const m of html.matchAll(/<script[^>]*\ssrc="([^"]+)"/g)) {
      const s = m[1];
      if (/^(https?:)?\/\//.test(s) || s.startsWith("$WEBAPIS")) continue;
      if (!fs.existsSync(path.join(dir, path.dirname(content), s)))
        problem(content, html.slice(0, m.index).split("\n").length, `loads ${s}, which doesn't exist`);
    }
  }
}

if (problems.length) {
  problems.forEach((p) => console.log("error: " + p));
  console.log(`${problems.length} problem(s)`);
  process.exit(1);
}
console.log(`ok: ${jsFiles(dir).length} script(s) checked for the TV (ES2018 / Chrome 69, config.xml)`);
