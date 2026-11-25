// Hidden checks for the golden job: copied in only when the job is checked, never shown to the model.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as shop from '../src/cart.js';

const { createCart, addItem, summary } = shop;

function cartWith(price, quantity = 1) {
  const cart = createCart();
  addItem(cart, { sku: 'x', name: 'Thing', price }, quantity);
  return cart;
}

test('amounts are rounded to 2 decimals', () => {
  const s = summary(cartWith(0.1, 3));
  assert.equal(s.subtotal, 0.3);
  assert.equal(s.total, 49.3);
});

test('no code: discount 0 and code null', () => {
  const s = summary(cartWith(120));
  assert.equal(s.discount, 0);
  assert.equal(s.code, null);
  assert.equal(s.total, 169);
});

test('applyCode exists', () => {
  assert.equal(typeof shop.applyCode, 'function');
});

test('SAVE10 under the shipping limit', () => {
  const cart = cartWith(450);
  shop.applyCode(cart, 'SAVE10');
  assert.deepEqual(summary(cart), { subtotal: 450, discount: 45, shipping: 49, total: 454, code: 'SAVE10' });
});

test('the shipping limit is checked after the discount', () => {
  const cart = cartWith(540);
  shop.applyCode(cart, 'SAVE10');
  const s = summary(cart);
  assert.equal(s.discount, 54);
  assert.equal(s.shipping, 49);
  assert.equal(s.total, 535);
});

test('SAVE10 above the limit keeps free shipping', () => {
  const cart = cartWith(600);
  shop.applyCode(cart, 'SAVE10');
  assert.equal(summary(cart).shipping, 0);
  assert.equal(summary(cart).total, 540);
});

test('SAVE10 discount is rounded', () => {
  const cart = cartWith(33.33);
  shop.applyCode(cart, 'SAVE10');
  const s = summary(cart);
  assert.equal(s.discount, 3.33);
  assert.equal(s.total, 79);
});

test('FREESHIP makes shipping 0', () => {
  const cart = cartWith(120);
  shop.applyCode(cart, 'FREESHIP');
  assert.deepEqual(summary(cart), { subtotal: 120, discount: 0, shipping: 0, total: 120, code: 'FREESHIP' });
});

test('codes are case-insensitive and trimmed', () => {
  const cart = cartWith(200);
  shop.applyCode(cart, '  save10 ');
  assert.equal(summary(cart).code, 'SAVE10');
  assert.equal(summary(cart).discount, 20);
});

test('unknown code throws and keeps the old code', () => {
  const cart = cartWith(200);
  shop.applyCode(cart, 'SAVE10');
  assert.throws(() => shop.applyCode(cart, 'NOPE'), { message: 'Unknown code' });
  assert.equal(summary(cart).code, 'SAVE10');
});

test('a new code replaces the old one', () => {
  const cart = cartWith(200);
  shop.applyCode(cart, 'SAVE10');
  shop.applyCode(cart, 'FREESHIP');
  const s = summary(cart);
  assert.equal(s.code, 'FREESHIP');
  assert.equal(s.discount, 0);
  assert.equal(s.shipping, 0);
});

test('the cart still survives JSON', () => {
  const cart = cartWith(450);
  shop.applyCode(cart, 'SAVE10');
  const back = JSON.parse(JSON.stringify(cart));
  assert.equal(summary(back).total, 454);
});
