// Loads the panels' data from the home lab's dashboard feed.
var Data = (function () {
  var FEED = "http://192.168.50.3:3010/api/state";
  var last = null;

  function reload() {
    return fetch(FEED).then(function (r) { return r.json(); }).then(function (json) {
      last = json;
      return json;
    });
  }

  return { reload: reload, last: function () { return last; } };
})();
