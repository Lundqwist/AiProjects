// The news panel: shows one headline at a time and moves on to the next every few seconds.
var Ticker = (function () {
  // items: [{ title, url, published }], published = a time in milliseconds
  function create(items) {
    var list = items.slice();
    var pos = 0;
    return {
      current: function () { return list[pos]; },
      next: function () {
        pos = pos + 1;
        return list[pos];
      },
      size: function () { return list.length; }
    };
  }

  // How long ago a headline was published, for the line under it.
  function timeAgo(published, now) {
    var minutes = Math.round((now - published) / 60000);
    return minutes + " min ago";
  }

  return { create: create, timeAgo: timeAgo };
})();
