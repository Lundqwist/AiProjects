"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const path = require("path");
const { load } = require("./load");

const K = { LEFT: 37, UP: 38, RIGHT: 39, DOWN: 40, OK: 13, BACK: 10009, RED: 403 };

// 8 panels in rows of 3:   0 1 2 / 3 4 5 / 6 7
function moves() {
  const { app } = load();
  return (from, key) => app.Remote.move(from, key, 8, 3);
}

test("golden: left and right stay within the row", () => {
  const m = moves();
  assert.strictEqual(m(2, K.RIGHT), 2);
  assert.strictEqual(m(3, K.LEFT), 3);
  assert.strictEqual(m(0, K.LEFT), 0);
  assert.strictEqual(m(4, K.RIGHT), 5);
  assert.strictEqual(m(7, K.RIGHT), 7);
  assert.strictEqual(m(7, K.LEFT), 6);
});

test("golden: up and down move a row, and never off the grid", () => {
  const m = moves();
  assert.strictEqual(m(1, K.UP), 1);
  assert.strictEqual(m(4, K.UP), 1);
  assert.strictEqual(m(1, K.DOWN), 4);
  assert.strictEqual(m(4, K.DOWN), 7);
  assert.strictEqual(m(6, K.DOWN), 6);
  assert.strictEqual(m(7, K.UP), 4);
});

test("golden: down into a shorter last row goes to its last panel", () => {
  const m = moves();
  assert.strictEqual(m(5, K.DOWN), 7);
  const { app } = load();
  assert.strictEqual(app.Remote.move(2, K.DOWN, 4, 3), 3);   // 0 1 2 / 3
});

test("golden: other keys don't move the focus", () => {
  const m = moves();
  assert.strictEqual(m(4, K.OK), 4);
  assert.strictEqual(m(4, 999), 4);
});

test("golden: arrows do nothing while a panel is open", () => {
  const { app, press } = load();
  app.App.init(8, 3);
  press(K.RIGHT);
  press(K.OK);
  press(K.RIGHT);
  press(K.DOWN);
  assert.strictEqual(app.App.state.focus, 1);
  assert.strictEqual(app.App.state.open, true);
});

test("golden: Back closes the panel, then exits the app", () => {
  const { app, press, calls } = load();
  app.App.init(8, 3);
  press(K.OK);
  press(K.BACK);
  assert.strictEqual(app.App.state.open, false);
  assert.strictEqual(calls.exits, 0);
  press(K.BACK);
  assert.strictEqual(calls.exits, 1);
});

test("golden: the red button reloads, and is registered at start", () => {
  const { app, press, calls } = load();
  app.App.init(8, 3);
  assert.deepStrictEqual(calls.registered, ["ColorF0Red"]);
  press(K.RED);
  assert.strictEqual(calls.reloads, 1);
  assert.strictEqual(app.App.state.focus, 0);
});

test("golden: without tizen (a desktop browser) nothing breaks", () => {
  const { app, press, calls } = load({ tizen: false });
  app.App.init(8, 3);
  press(K.RED);
  press(K.BACK);
  assert.strictEqual(calls.reloads, 1);
});

test("golden: config.xml has the remote control privilege", () => {
  const cfg = fs.readFileSync(path.join(__dirname, "..", "config.xml"), "utf8");
  assert.match(cfg, /<tizen:privilege\s+name="http:\/\/tizen\.org\/privilege\/tv\.inputdevice"\s*\/>/);
  assert.match(cfg, /privilege\/internet/);
});
