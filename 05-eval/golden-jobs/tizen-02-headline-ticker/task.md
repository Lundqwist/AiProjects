The client's Samsung TV app (Tizen 5.5, its browser is about Chrome 69) has a news panel that shows one headline
at a time. The code is in `js\ticker.js`; `node --test` runs its tests outside the TV. Three changes:

1. **Bug:** after the last headline, `next()` returns `undefined` and the panel goes blank. After the last one it
   must start over at the first (and return it).
2. `Ticker.create(items)` (items are `{ title, url, published }`, `published` a time in milliseconds) must:
   - leave out items whose title is empty or only spaces;
   - leave out repeats: items with the same `url` as an earlier one in the list (the earlier one stays);
   - show the newest first (highest `published`);
   - keep at most 10 (the 10 newest).
   With no items left, `current()` and `next()` return `null`.
3. `Ticker.timeAgo(published, now)` (both in milliseconds) returns:
   - under a minute: `just now` (a time in the future counts as just now too);
   - under an hour: `N min ago`, whole minutes, rounded down;
   - under 24 hours: `N h ago`, whole hours, rounded down;
   - from 24 hours up to (not including) 48 hours: `yesterday`;
   - older: the day and the short English month of `published` in UTC, like `3 Nov` (no year, no leading zero).

Keep the existing tests working.
