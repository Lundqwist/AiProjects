The client's WoW addon JunkSeller (WoW: Forever, Lua 5.1) sells grey items when a merchant opens. Its code is in
`JunkSeller.lua`; `luajit tests/run.lua` runs its tests outside the game. Three changes:

1. **Bug:** opening a merchant with an empty bag slot gives the error
   `attempt to index local 'info' (a nil value)`, and nothing is sold. Empty slots must be skipped.

2. **Keep list:** `/junk keep <itemID>` puts an item on a keep list; items on it are never sold, even when grey.
   Typing it again with the same ID takes the item off the list. It prints `Keeping <itemID>` or
   `No longer keeping <itemID>`. `/junk keep` without a number (or with something that isn't a number) prints
   `Usage: /junk keep <itemID>`. Extra spaces don't matter (`/junk  keep  77 ` works). The list is saved in
   `JunkSellerDB.keep` (keys are item IDs as numbers).
   `JunkSellerDB` is already the addon's SavedVariable in the .toc; it is nil the first time the addon loads, so
   create it when `ADDON_LOADED` fires for this addon, and keep what is in it when it is already there.

3. **Summary:** after selling, print one line `Sold <n> items for <money>`: n is the number of stacks sold, money
   is the sum of each item's sell price (11th value of `GetItemInfo(itemID)`, in copper) times its stack count,
   written as gold, silver and copper like `1g 5s 20c`, leaving out the parts that are zero (`5s`, `2g 3c`).
   When nothing is sold, print nothing.

`/junk` without anything after it keeps working as now. Keep the existing tests working.
