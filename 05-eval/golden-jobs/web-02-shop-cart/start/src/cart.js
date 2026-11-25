// The shopping cart. A cart is a plain object so it can be saved in localStorage as JSON.

export const FREE_SHIPPING_FROM = 500;
export const SHIPPING = 49;

export function createCart() {
  return { items: [] };
}

export function addItem(cart, { sku, name, price }, quantity = 1) {
  if (quantity <= 0) throw new Error('Quantity must be positive');
  const line = cart.items.find((i) => i.sku === sku);
  if (line) {
    line.quantity += quantity;
  } else {
    cart.items.push({ sku, name, price, quantity });
  }
  return cart;
}

export function removeItem(cart, sku) {
  cart.items = cart.items.filter((i) => i.sku !== sku);
  return cart;
}

export function subtotal(cart) {
  return cart.items.reduce((sum, i) => sum + i.price * i.quantity, 0);
}

export function shipping(cart) {
  const sub = subtotal(cart);
  if (sub === 0) return 0;
  return sub >= FREE_SHIPPING_FROM ? 0 : SHIPPING;
}

export function summary(cart) {
  const sub = subtotal(cart);
  const ship = shipping(cart);
  return { subtotal: sub, shipping: ship, total: sub + ship };
}
