/* Bounded recovery for idempotent lookups. Queries stay in POST bodies. */
(function () {
  "use strict";
  function pause(milliseconds, signal) {
    return new Promise(function (resolve, reject) {
      if (signal.aborted) { reject(new DOMException("Aborted", "AbortError")); return; }
      var timer = window.setTimeout(function () { signal.removeEventListener("abort", abort); resolve(); }, milliseconds);
      function abort() { window.clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); }
      signal.addEventListener("abort", abort, { once: true });
    });
  }
  window.MinosRequest = function (url, options, state) {
    options = options || {};
    state = state || {};
    var parent = options.signal;
    var controller = new AbortController();
    var timedOut = false;
    function abort() { controller.abort(); }
    if (parent) {
      if (parent.aborted) controller.abort();
      else parent.addEventListener("abort", abort, { once: true });
    }
    var timer = window.setTimeout(function () { timedOut = true; controller.abort(); }, 12000);
    function attempt(number) {
      return fetch(url, Object.assign({ cache: "no-store", credentials: "omit" }, options, { signal: controller.signal }))
        .then(function (response) {
          return response.json().catch(function () { return {}; }).then(function (body) {
            if (response.ok) return body;
            var code = body && body.error && body.error.code;
            var busy = response.status === 429 || (response.status === 503 && !code);
            var retryAfter = response.headers.get("Retry-After");
            var seconds = retryAfter === null ? 1 : Number(retryAfter);
            if (!Number.isFinite(seconds)) seconds = (Date.parse(retryAfter) - Date.now()) / 1000;
            if (!Number.isFinite(seconds) || seconds < 0) seconds = 1;
            if (busy && seconds <= 3 && number === 0 && state.retry !== false) {
              if (state.onRetry) state.onRetry();
              return pause(Math.max(0.1, seconds) * 1000, controller.signal).then(function () { return attempt(1); });
            }
            var error = new Error("lookup failed");
            error.code = code || (busy ? "busy" : response.status === 504 ? "query_timeout" : response.status === 408 ? "request_timeout" : response.status >= 500 ? "unavailable" : "invalid_request");
            throw error;
          });
        });
    }
    return attempt(0).catch(function (error) {
      if (timedOut) { var timeout = new Error("lookup timed out"); timeout.code = "query_timeout"; throw timeout; }
      throw error;
    }).finally(function () {
      window.clearTimeout(timer);
      if (parent) parent.removeEventListener("abort", abort);
    });
  };
})();
