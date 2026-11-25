The client's WoW addon RollTracker (WoW: Forever, Lua 5.1) records `/roll` results from the game's system messages
(`Name rolls 57 (1-100)`) so a raid leader can hand out loot. Its code is in `RollTracker.lua`;
`luajit tests/run.lua` runs its tests outside the game. The client wants:

1. **Bug:** players with accented letters in their name (`Lûnamal`) or with a realm (`Lunamal-Stormscale`) are
   never recorded. Record them, with the name exactly as the message has it.
2. Only rolls of 1-100 count. Other ranges (`(1-50)`, `(1-1000)`) are ignored.
3. A player's first roll counts. Later rolls by the same player are ignored until the list is cleared.
4. `/rolls` prints `Rolls:` and then one line per player, highest roll first, written `1. Name 57`. Equal rolls
   are listed by name in alphabetical order, ignoring upper/lower case. With no rolls it prints `No rolls yet.`
5. `/rolls clear` empties the list and prints `Rolls cleared.`
6. `/rolls winner` prints `Winner: Name (57)`. When two or more players share the highest roll it prints
   `Tie: Name1, Name2 (57)` with the names in alphabetical order (ignoring case). With no rolls it prints
   `No rolls yet.`

Commands are not case-sensitive (`/rolls CLEAR` works) and spaces around them are ignored. Keep the existing tests
working.
