"use strict";
const test = require("node:test");
const assert = require("node:assert");
const { load } = require("./load");

const T = () => load().app.Ticker;
const item = (title, url, published) => ({ title, url, published });
const titles = (t) => {
  const out = [t.current() && t.current().title];
  for (let i = 1; i < t.size(); i++) out.push(t.next().title);
  return out;
};

test("golden: after the last headline it starts over", () => {
  const t = T().create([item("A", "a", 3), item("B", "b", 2)]);
  assert.strictEqual(t.current().title, "A");
  assert.strictEqual(t.next().title, "B");
  assert.strictEqual(t.next().title, "A");
  assert.strictEqual(t.current().title, "A");
  assert.strictEqual(t.next().title, "B");
});

test("golden: a single headline stays", () => {
  const t = T().create([item("Only", "o", 1)]);
  assert.strictEqual(t.next().title, "Only");
  assert.strictEqual(t.next().title, "Only");
});

test("golden: empty and blank titles are left out", () => {
  const t = T().create([item("", "a", 5), item("   ", "b", 4), item("Real", "c", 3)]);
  assert.strictEqual(t.size(), 1);
  assert.strictEqual(t.current().title, "Real");
});

test("golden: repeats by url are left out, the earlier one stays", () => {
  const t = T().create([item("First", "same", 1), item("Other", "x", 2), item("Second", "same", 9)]);
  assert.strictEqual(t.size(), 2);
  assert.deepStrictEqual(titles(t), ["Other", "First"]);
});

test("golden: newest first", () => {
  const t = T().create([item("Old", "1", 100), item("New", "2", 300), item("Mid", "3", 200)]);
  assert.deepStrictEqual(titles(t), ["New", "Mid", "Old"]);
});

test("golden: at most 10, the newest", () => {
  const items = [];
  for (let i = 1; i <= 13; i++) items.push(item("H" + i, "u" + i, i * 1000));
  const t = T().create(items);
  assert.strictEqual(t.size(), 10);
  const got = titles(t);
  assert.strictEqual(got[0], "H13");
  assert.strictEqual(got[9], "H4");
});

test("golden: the input list is not changed", () => {
  const items = [item("B", "b", 1), item("A", "a", 2)];
  T().create(items);
  assert.deepStrictEqual(items.map((i) => i.title), ["B", "A"]);
});

test("golden: nothing left gives null", () => {
  const t = T().create([item(" ", "a", 1)]);
  assert.strictEqual(t.current(), null);
  assert.strictEqual(t.next(), null);
  const e = T().create([]);
  assert.strictEqual(e.current(), null);
});

const MIN = 60000, H = 60 * MIN;
const NOW = Date.UTC(2026, 10, 25, 12, 0, 0);

test("golden: just now", () => {
  const ago = T().timeAgo;
  assert.strictEqual(ago(NOW, NOW), "just now");
  assert.strictEqual(ago(NOW - 59999, NOW), "just now");
  assert.strictEqual(ago(NOW + 5 * MIN, NOW), "just now");
});

test("golden: minutes, rounded down", () => {
  const ago = T().timeAgo;
  assert.strictEqual(ago(NOW - MIN, NOW), "1 min ago");
  assert.strictEqual(ago(NOW - (5 * MIN + 59000), NOW), "5 min ago");
  assert.strictEqual(ago(NOW - (H - 1), NOW), "59 min ago");
});

test("golden: hours, rounded down", () => {
  const ago = T().timeAgo;
  assert.strictEqual(ago(NOW - H, NOW), "1 h ago");
  assert.strictEqual(ago(NOW - (3 * H + 59 * MIN), NOW), "3 h ago");
  assert.strictEqual(ago(NOW - (24 * H - 1), NOW), "23 h ago");
});

test("golden: yesterday", () => {
  const ago = T().timeAgo;
  assert.strictEqual(ago(NOW - 24 * H, NOW), "yesterday");
  assert.strictEqual(ago(NOW - (48 * H - 1), NOW), "yesterday");
});

test("golden: older is day and month in UTC", () => {
  const ago = T().timeAgo;
  assert.strictEqual(ago(NOW - 48 * H, NOW), "23 Nov");
  assert.strictEqual(ago(Date.UTC(2026, 10, 3, 23, 30), NOW), "3 Nov");
  assert.strictEqual(ago(Date.UTC(2026, 0, 1, 0, 5), NOW), "1 Jan");
  assert.strictEqual(ago(Date.UTC(2025, 11, 31, 23, 59), NOW), "31 Dec");
});
