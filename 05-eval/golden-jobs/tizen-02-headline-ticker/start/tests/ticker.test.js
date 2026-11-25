"use strict";
const test = require("node:test");
const assert = require("node:assert");
const { load } = require("./load");

test("the first headline is shown first", () => {
  const { app } = load();
  const t = app.Ticker.create([{ title: "Snow in Uppsala", url: "u1", published: 1000 }]);
  assert.strictEqual(t.current().title, "Snow in Uppsala");
});

test("minutes ago", () => {
  const { app } = load();
  assert.strictEqual(app.Ticker.timeAgo(0, 5 * 60000), "5 min ago");
});
