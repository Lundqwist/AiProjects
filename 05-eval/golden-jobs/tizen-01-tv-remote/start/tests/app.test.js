"use strict";
const test = require("node:test");
const assert = require("node:assert");
const { load } = require("./load");

test("OK opens the focused panel", () => {
  const { app, press } = load();
  app.App.init(6, 3);
  press(13);
  assert.strictEqual(app.App.state.open, true);
});

test("right moves to the next panel in the row", () => {
  const { app, press } = load();
  app.App.init(6, 3);
  press(39);
  assert.strictEqual(app.App.state.focus, 1);
});
