// The dashboard: panels in a grid, one has focus, OK opens it big, Back closes it.
var App = (function () {
  var state = { focus: 0, open: false, count: 0, columns: 3 };

  function onKey(e) {
    if (e.keyCode === Remote.KEY.ENTER) {
      state.open = true;
      return;
    }
    if (e.keyCode === Remote.KEY.BACK) {
      state.open = false;
      return;
    }
    state.focus = Remote.move(state.focus, e.keyCode, state.count, state.columns);
  }

  function init(count, columns) {
    state.focus = 0;
    state.open = false;
    state.count = count;
    state.columns = columns;
    document.addEventListener("keydown", onKey);
  }

  return { init: init, onKey: onKey, state: state };
})();
