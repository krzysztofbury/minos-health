window.plausible = window.plausible || function () {
  (window.plausible.q = window.plausible.q || []).push(arguments);
};
window.plausible.init = window.plausible.init || function (options) {
  window.plausible.o = options || {};
};

var plausiblePagePaths = ["/", "/polityka-prywatnosci", "/regulamin", "/zrodla"];

window.plausible.init({
  autoCapturePageviews: true,
  captureOnLocalhost: false,
  fileDownloads: false,
  formSubmissions: false,
  hashBasedRouting: false,
  logging: false,
  outboundLinks: false,
  transformRequest: function (payload) {
    if (
      payload.n !== "pageview" ||
      plausiblePagePaths.indexOf(window.location.pathname) === -1
    ) {
      return null;
    }

    payload.u = "https://minoshealth.pl" + window.location.pathname;
    payload.r = null;
    delete payload.p;
    delete payload.$;
    return payload;
  },
});
