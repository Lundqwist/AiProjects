import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createCart, addItem, removeItem, summary } from '../src/cart.js';
import { formatSEK } from '../src/money.js';

test('adding the same item twice adds to its quantity', () => {
  const cart = createCart();
  addItem(cart, { sku: 'mug', name: 'Mug', price: 120 });
  addItem(cart, { sku: 'mug', name: 'Mug', price: 120 }, 2);
  assert.equal(cart.items.length, 1);
  assert.equal(cart.items[0].quantity, 3);
});

test('shipping is 49 under 500 kr and free from 500 kr', () => {
  const cart = createCart();
  addItem(cart, { sku: 'mug', name: 'Mug', price: 120 });
  assert.equal(summary(cart).shipping, 49);
  addItem(cart, { sku: 'lamp', name: 'Lamp', price: 380 });
  assert.equal(summary(cart).shipping, 0);
});

test('removing an item', () => {
  const cart = createCart();
  addItem(cart, { sku: 'mug', name: 'Mug', price: 120 });
  removeItem(cart, 'mug');
  assert.equal(summary(cart).total, 0);
});

test('formatSEK uses two decimals', () => {
  assert.match(formatSEK(12), /^12,00\s?kr$/);
});
