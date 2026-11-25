// Loads the TV app's scripts the way index.html does, into a sandbox with stand-ins for the TV:
// a document that collects keydown listeners, and a tizen object that records registered keys and exits.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

function load(options) {
  const opts = options || {};
  const root = path.join(__dirname, "..");
  const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
  const scripts = [...html.matchAll(/<script[^>]*\ssrc="([^"]+)"/g)].map((m) => m[1]);
  const listeners = {};
  const calls = { registered: [], exits: 0, reloads: 0 };
  const document = {
    addEventListener: (type, fn) => (listeners[type] = listeners[type] || []).push(fn),
    getElementById: () => null,
    querySelectorAll: () => [],
  };
  const sandbox = { document, console, setTimeout, clearTimeout, fetch: () => Promise.resolve({ json: () => ({}) }) };
  sandbox.window = sandbox;
  if (opts.tizen !== false) {
    sandbox.tizen = {
      tvinputdevice: { registerKey: (name) => calls.registered.push(name) },
      application: { getCurrentApplication: () => ({ exit: () => { calls.exits += 1; } }) },
    };
  }
  vm.createContext(sandbox);
  for (const s of scripts) {
    vm.runInContext(fs.readFileSync(path.join(root, s), "utf8"), sandbox, { filename: s });
  }
  if (sandbox.Data) sandbox.Data.reload = () => { calls.reloads += 1; };
  const press = (keyCode) => (listeners.keydown || []).forEach((fn) => fn({ keyCode, preventDefault() {} }));
  return { app: sandbox, calls, press, listeners };
}

module.exports = { load };
