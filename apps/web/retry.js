/* A busy page tries again by itself, a few times and not in step with other
   tabs. It stops at the page's own button; nothing here needs to succeed. */
(function () {
  "use strict";
  var key = "minos.retry." + window.location.pathname;
  try {
    var tries = Number(window.sessionStorage.getItem(key)) || 0;
    if (tries >= 3) { window.sessionStorage.removeItem(key); return; }
    window.sessionStorage.setItem(key, String(tries + 1));
    window.setTimeout(function () { window.location.reload(); }, (Math.pow(2, tries) + Math.random()) * 1000);
  } catch (error) { /* Without storage the button remains. */ }
})();
