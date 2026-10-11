/* JGP catalog pages (#26) are complete without this script. It adds date
   navigation (forms are off, so no query strings) and a local filter whose
   text never leaves the browser: no requests, no URL, no storage. */
(function () {
  "use strict";

  var DATE = /^20\d{2}-\d{2}-\d{2}$/;

  var comparison = document.querySelector(".compare-controls");
  if (comparison) {
    var before = document.getElementById("compare-before");
    var after = document.getElementById("compare-after");
    var beforeRelease = document.getElementById("compare-before-release");
    var afterRelease = document.getElementById("compare-after-release");
    var scope = document.getElementById("compare-scope");
    var group = document.getElementById("compare-group");
    var button = document.getElementById("compare-go");
    var compare = function () {
      if (!before.checkValidity() || !after.checkValidity() || !DATE.test(before.value) || !DATE.test(after.value)) return;
      window.location.assign("/jgp/porownaj/" + comparison.dataset.dataset + "/" + before.value + "/" + beforeRelease.value + "/" + after.value + "/" + afterRelease.value + "/" + scope.value + "/" + group.value);
    };
    button.hidden = false;
    button.addEventListener("click", compare);
    scope.addEventListener("change", compare);
    group.addEventListener("change", compare);
    comparison.addEventListener("keydown", function (event) {
      if (event.key === "Enter") { event.preventDefault(); compare(); }
    });
  }

  var dateInput = document.getElementById("jgp-date");
  if (dateInput instanceof HTMLInputElement) {
    var current = dateInput.value;
    // Keyboard edits pass through valid intermediate dates (typing "15" is
    // "1" first), so they navigate on Enter or blur; a picker choice at once.
    var typing = false;
    var go = function () {
      typing = false;
      if (!DATE.test(dateInput.value) || dateInput.value === current) return;
      window.location.assign((dateInput.dataset.prefix || "/jgp/") + dateInput.value + (dateInput.dataset.suffix || ""));
    };
    dateInput.addEventListener("keydown", function (event) {
      if (event.key === "Enter") go(); else typing = true;
    });
    dateInput.addEventListener("change", function () { if (!typing) go(); });
    dateInput.addEventListener("blur", function () { if (typing) go(); });
  }

  var box = document.querySelector(".jgp-filter");
  var input = document.getElementById("jgp-filter");
  var status = document.querySelector(".jgp-filter-status");
  // Escape closes the zakres picker, which covers the tree while open.
  var scopes = document.querySelector(".jgp-scopes");
  if (scopes instanceof HTMLDetailsElement) {
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || !scopes.open) return;
      scopes.open = false;
      var summary = scopes.querySelector("summary");
      if (summary) summary.focus();
    });
  }

  if (!(box instanceof HTMLElement) || !(input instanceof HTMLInputElement)) return;
  var rows = Array.prototype.slice.call(document.querySelectorAll(".jgp-list li[data-search]"));
  var chapters = Array.prototype.slice.call(document.querySelectorAll(".jgp-chapter"));
  var parts = Array.prototype.slice.call(document.querySelectorAll(".jgp-catalog-part"));
  var branches = parts.map(function (part) { return part.querySelector("details"); });

  function fold(text) {
    // Case and Polish diacritics are ignored: "zlamanie" finds "złamanie".
    return text
      .toLocaleLowerCase("pl")
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/ł/g, "l");
  }

  // The server's count line comes back when the filter clears.
  var hint = status ? status.textContent : "";
  var keys = rows.map(function (row) { return fold(row.getAttribute("data-search") || ""); });
  // Chapters the user opened before filtering come back when the filter clears.
  var opened = null;
  var catalogsOpened = null;
  if (rows.length === 0) return; // nothing to filter; the status says so
  box.hidden = false;

  input.addEventListener("input", function () {
    var query = fold(input.value.trim());
    var shown = 0;
    if (query && opened === null) {
      opened = chapters.map(function (chapter) { return chapter.open; });
      catalogsOpened = branches.map(function (branch) { return branch.open; });
    }
    rows.forEach(function (row, index) {
      var match = !query || keys[index].indexOf(query) !== -1;
      row.hidden = !match;
      if (match) shown += 1;
    });
    chapters.forEach(function (chapter, index) {
      var visible = chapter.querySelector(".jgp-list li[data-search]:not([hidden])") !== null;
      chapter.hidden = !visible;
      if (query) chapter.open = visible;
      else if (opened !== null) chapter.open = opened[index];
    });
    if (!query) opened = null;
    parts.forEach(function (part, index) {
      part.hidden = part.querySelector(".jgp-chapter:not([hidden])") === null;
      // A catalog the user collapsed must not hide matches while filtering.
      if (query) branches[index].open = true;
      else if (catalogsOpened !== null) branches[index].open = catalogsOpened[index];
    });
    if (!query) catalogsOpened = null;
    if (status) status.textContent = query ? "Pasuje: " + shown + " z " + rows.length + " grup" : hint;
  });
})();
