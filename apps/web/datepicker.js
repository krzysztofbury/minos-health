/* Polish dates whatever the browser language, shared by the grouper and the JGP
   pages: typed as DD.MM.RRRR (months MM.RRRR), picked from a Polish calendar,
   sent as ISO. No requests, storage or logging. */
(function () {
  "use strict";
  var MONTHS = ["styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec", "lipiec", "sierpień", "wrzesień", "październik", "listopad", "grudzień"];
  var MONTHS_OF = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia", "września", "października", "listopada", "grudnia"];
  var WEEKDAYS = ["pn", "wt", "śr", "cz", "pt", "so", "nd"];

  function el(tag, text, cls) { var node = document.createElement(tag); if (text !== undefined && text !== null) node.textContent = text; if (cls) node.className = cls; return node; }
  function iso(text) {
    var m = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec((text || "").trim()); if (!m) return "";
    var d = new Date(Date.UTC(+m[3], +m[2] - 1, +m[1]));
    return d.getUTCFullYear() === +m[3] && d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[1] ? m[3] + "-" + m[2] + "-" + m[1] : "";
  }
  function isoMonth(text) { var m = /^(0[1-9]|1[0-2])\.(\d{4})$/.exec((text || "").trim()); return m ? m[2] + "-" + m[1] : ""; }
  function pl(isoDate) { var p = (isoDate || "").split("-"); return p.length === 3 ? p[2] + "." + p[1] + "." + p[0] : ""; }
  function plMonth(isoDate) { var p = (isoDate || "").split("-"); return p.length >= 2 ? p[1] + "." + p[0] : ""; }
  function mask(input, groups) {
    var digits = input.value.replace(/\D/g, "").slice(0, groups.reduce(function (a, b) { return a + b; }, 0)), parts = [], at = 0;
    groups.forEach(function (size) { if (digits.length > at) parts.push(digits.slice(at, at + size)); at += size; });
    input.value = parts.join(".");
  }
  function isoOf(y, m, d) { var date = new Date(Date.UTC(y, m, d)); return date.toISOString().slice(0, 10); }
  function todayIso() { var now = new Date(); return isoOf(now.getFullYear(), now.getMonth(), now.getDate()); }

  /* One calendar per page. `fallback` names the month to open on for an empty
     field; `beforeOpen` lets the page close its own overlays first. */
  function attach(options) {
    options = options || {};
    var picker = document.getElementById("picker");
    if (!picker) { picker = el("div", undefined, "picker"); picker.id = "picker"; picker.setAttribute("role", "dialog"); picker.setAttribute("aria-label", "Kalendarz"); picker.hidden = true; document.body.append(picker); }
    var pickerInput = null, pickerKind = "date", view = { year: 2026, month: 0 }, cursor = "";
    function openPicker(button) {
      if (options.beforeOpen) options.beforeOpen();
      pickerInput = button.parentElement.querySelector("input"); pickerKind = button.dataset.picker;
      var chosen = pickerKind === "date" ? iso(pickerInput.value) : (isoMonth(pickerInput.value) ? isoMonth(pickerInput.value) + "-01" : "");
      cursor = chosen || (options.fallback && options.fallback()) || todayIso();
      view = { year: +cursor.slice(0, 4), month: +cursor.slice(5, 7) - 1 };
      renderPicker(); picker.hidden = false;
      var r = pickerInput.parentElement.getBoundingClientRect(), width = picker.offsetWidth;
      picker.style.left = Math.max(16, Math.min(r.left, document.documentElement.clientWidth - width - 16)) + window.scrollX + "px";
      picker.style.top = r.bottom + window.scrollY + 6 + "px";
      var focus = picker.querySelector("[data-value='" + (pickerKind === "date" ? cursor : cursor.slice(0, 7)) + "']"); if (focus) focus.focus();
    }
    function closePicker(restore) { if (picker.hidden) return; picker.hidden = true; picker.replaceChildren(); if (restore && pickerInput) pickerInput.focus(); }
    function choosePicked(valueIso) {
      pickerInput.value = pickerKind === "date" ? pl(valueIso) : plMonth(valueIso);
      pickerInput.dispatchEvent(new Event("input", { bubbles: true }));
      pickerInput.dispatchEvent(new CustomEvent("minos:picked", { bubbles: true }));
      closePicker(true);
    }
    function renderPicker() {
      picker.replaceChildren();
      var head = el("div", undefined, "picker-head"), title = el("p", pickerKind === "date" ? MONTHS[view.month] + " " + view.year : String(view.year), "picker-title");
      title.setAttribute("aria-live", "polite");
      var prev = el("button", "‹", "picker-nav"), nextButton = el("button", "›", "picker-nav");
      prev.type = nextButton.type = "button";
      prev.setAttribute("aria-label", pickerKind === "date" ? "Poprzedni miesiąc" : "Poprzedni rok"); nextButton.setAttribute("aria-label", pickerKind === "date" ? "Następny miesiąc" : "Następny rok");
      prev.addEventListener("click", function () { shiftView(-1); }); nextButton.addEventListener("click", function () { shiftView(1); });
      head.append(prev, title, nextButton); picker.append(head);
      var grid = el("div", undefined, pickerKind === "date" ? "picker-days" : "picker-months"); grid.setAttribute("role", "grid");
      var today = todayIso(), selected = pickerKind === "date" ? iso(pickerInput.value) : isoMonth(pickerInput.value);
      if (pickerKind === "date") {
        WEEKDAYS.forEach(function (name) { var cell = el("span", name, "picker-weekday"); cell.setAttribute("aria-hidden", "true"); grid.append(cell); });
        var first = new Date(Date.UTC(view.year, view.month, 1)), offset = (first.getUTCDay() + 6) % 7, days = new Date(Date.UTC(view.year, view.month + 1, 0)).getUTCDate();
        for (var blank = 0; blank < offset; blank++) grid.append(el("span", "", "picker-blank"));
        for (var day = 1; day <= days; day++) {
          var at = isoOf(view.year, view.month, day), cell = el("button", String(day), "picker-cell");
          cell.type = "button"; cell.dataset.value = at; cell.tabIndex = at === cursor ? 0 : -1;
          cell.setAttribute("aria-label", day + " " + MONTHS_OF[view.month] + " " + view.year);
          if (at === today) cell.classList.add("is-today"); if (at === selected) { cell.classList.add("is-selected"); cell.setAttribute("aria-pressed", "true"); }
          grid.append(cell);
        }
      } else {
        MONTHS.forEach(function (name, index) {
          var at = view.year + "-" + String(index + 1).padStart(2, "0"), cell = el("button", name, "picker-cell");
          cell.type = "button"; cell.dataset.value = at; cell.tabIndex = at === cursor.slice(0, 7) ? 0 : -1;
          cell.setAttribute("aria-label", name + " " + view.year);
          if (at === today.slice(0, 7)) cell.classList.add("is-today"); if (at === selected) { cell.classList.add("is-selected"); cell.setAttribute("aria-pressed", "true"); }
          grid.append(cell);
        });
      }
      picker.append(grid);
    }
    function shiftView(step) {
      if (pickerKind === "date") { var d = new Date(Date.UTC(view.year, view.month + step, 1)); view = { year: d.getUTCFullYear(), month: d.getUTCMonth() }; cursor = isoOf(view.year, view.month, Math.min(+cursor.slice(8, 10) || 1, new Date(Date.UTC(view.year, view.month + 1, 0)).getUTCDate())); }
      else { view = { year: view.year + step, month: view.month }; cursor = view.year + cursor.slice(4); }
      renderPicker();
    }
    function moveCursor(delta) {
      if (pickerKind === "date") { var d = new Date(cursor + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + delta); cursor = d.toISOString().slice(0, 10); }
      else { var month = +cursor.slice(5, 7) - 1 + delta, year = +cursor.slice(0, 4) + Math.floor(month / 12); month = ((month % 12) + 12) % 12; cursor = year + "-" + String(month + 1).padStart(2, "0") + "-01"; }
      view = { year: +cursor.slice(0, 4), month: +cursor.slice(5, 7) - 1 };
      renderPicker();
      var focus = picker.querySelector("[data-value='" + (pickerKind === "date" ? cursor : cursor.slice(0, 7)) + "']"); if (focus) focus.focus();
    }
    picker.addEventListener("click", function (event) { var cell = event.target.closest && event.target.closest(".picker-cell"); if (cell) choosePicked(cell.dataset.value); });
    picker.addEventListener("keydown", function (event) {
      var cell = event.target.classList && event.target.classList.contains("picker-cell"), date = pickerKind === "date";
      var moves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: date ? -7 : -3, ArrowDown: date ? 7 : 3 };
      if (event.key === "Escape") { event.preventDefault(); closePicker(true); }
      else if (cell && moves[event.key] !== undefined) { event.preventDefault(); moveCursor(moves[event.key]); }
      else if (cell && (event.key === "PageUp" || event.key === "PageDown")) { event.preventDefault(); shiftView(event.key === "PageUp" ? -1 : 1); var focus = picker.querySelector("[data-value='" + (date ? cursor : cursor.slice(0, 7)) + "']"); if (focus) focus.focus(); }
    });
    document.addEventListener("click", function (event) {
      var opener = event.target.closest && event.target.closest(".picker-button");
      if (opener) { if (!picker.hidden && pickerInput === opener.parentElement.querySelector("input")) closePicker(true); else openPicker(opener); return; }
      if (!picker.hidden && !(event.target.closest && event.target.closest(".picker"))) closePicker(false);
    });
    if (options.mask) {
      document.addEventListener("input", function (event) {
        var target = event.target;
        if (!target.classList || (event.inputType || "").startsWith("delete")) return;
        if (target.classList.contains("date-pl")) mask(target, [2, 2, 4]); else if (target.classList.contains("month-pl")) mask(target, [2, 4]);
      });
    }
    return { close: closePicker };
  }

  window.MinosDates = { iso: iso, isoMonth: isoMonth, pl: pl, plMonth: plMonth, mask: mask, attach: attach };
})();
