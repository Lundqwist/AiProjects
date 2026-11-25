The client's web shop keeps its cart logic in `src/cart.js` (plain JavaScript modules, tested with `node --test`).
Two changes:

1. **Bug:** the cart total sometimes shows values like `30.000000000000004`. Every amount the cart returns
   (`subtotal`, `discount`, `shipping`, `total`) must be rounded to 2 decimals.

2. **Discount codes:** add `applyCode(cart, code)` in `src/cart.js`.
   - `SAVE10` gives 10 % off the subtotal.
   - `FREESHIP` makes shipping 0.
   - Codes are case-insensitive (`save10` works) and surrounding spaces are ignored.
   - An unknown code throws an `Error` with the message `Unknown code`, and the cart keeps the code it had.
   - Only one code at a time: applying a new code replaces the old one.
   - `summary(cart)` returns `{ subtotal, discount, shipping, total, code }`: `subtotal` is before the discount,
     `total = subtotal - discount + shipping`, and `code` is the applied code in upper case, or `null`.
     Without a code, `discount` is 0.
   - Shipping stays as it is now: 49 under 500 kr, free from 500 kr. The 500 kr limit is checked on the
     subtotal **after** the discount.

Keep the existing functions and tests working. `npm test` must pass.
