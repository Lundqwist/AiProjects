// Remote control for the dashboard: which panel gets focus after a key.
var Remote = (function () {
  var KEY = { LEFT: 37, UP: 38, RIGHT: 39, DOWN: 40, ENTER: 13, BACK: 10009 };

  // The panels are laid out in rows of `columns`, `count` panels in all. Returns the index that has focus next.
  function move(index, keyCode, count, columns) {
    if (keyCode === KEY.RIGHT) return index + 1;
    if (keyCode === KEY.LEFT) return index - 1;
    if (keyCode === KEY.DOWN) return index + columns;
    if (keyCode === KEY.UP) return index - columns;
    return index;
  }

  return { KEY: KEY, move: move };
})();
