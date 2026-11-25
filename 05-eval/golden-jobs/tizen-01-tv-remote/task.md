The client's Samsung TV app (Tizen 5.5, its browser is about Chrome 69) shows home lab panels in a grid that you
move through with the remote. The code is in `js\remote.js` and `js\app.js`; `node --test` runs its tests outside
the TV. Four changes:

1. **Bug:** the focus wanders off the grid. Right on the last panel of a row jumps to the next row, and the arrows
   at the edges give panel numbers that don't exist. Fix `Remote.move(index, keyCode, count, columns)`: Left and
   Right stay within the row (no wrapping), Up and Down move one row. A move that would leave the grid keeps the
   focus where it is. Down into the last row, when that row is shorter and has no panel directly below, goes to
   the last panel.
2. While a panel is open, the arrow keys do nothing.
3. **Back** closes the open panel. When no panel is open, Back exits the app with
   `tizen.application.getCurrentApplication().exit()`.
4. The **red** button (keyCode 403) calls `Data.reload()`. Register the key in `App.init` with
   `tizen.tvinputdevice.registerKey("ColorF0Red")`, and add the privilege that needs to `config.xml`. Both tizen calls
   must be skipped when `window.tizen` doesn't exist (the app also runs in a desktop browser).

Keep the existing tests working.
