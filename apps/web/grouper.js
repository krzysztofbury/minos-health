/* Stepped stateless encounter builder. No URL state, storage, analytics or input logging. */
(function () {
  "use strict";
  var NAMES = ["Hospitalizacja", "Pobyty na oddziałach", "Rozpoznania", "Procedury i produkty", "Wynik"];
  var RESULT = 4, CODE = /^[A-Z0-9][A-Z0-9.\-]{0,15}$/, PRODUCT = /^\d\.\d{2}\.\d{2}\.\d{7}$/;
  var form = document.getElementById("group-form"), fields = document.getElementById("group-fields");
  var answer = document.getElementById("group-answer"), status = document.getElementById("group-status");
  var errorsBox = document.getElementById("step-errors"), summary = document.getElementById("case-summary");
  var back = document.getElementById("step-back"), next = document.getElementById("step-next");
  var current = 0, reached = 0, releases = {}, sequence = 0, controller = null, nextId = 0;
  var wards = [], secondary = [], procedures = [], products = [], principal = null, declared = [];
  var MARK = { met: "✓", not_met: "✗", undetermined: "?" };
  var SPOKEN = { met: "spełniony: ", not_met: "niespełniony: ", undetermined: "nierozstrzygnięty: " };
  var CMP = { lt: "<", le: "≤", gt: ">", ge: "≥" };
  var KINDS = { diagnosis_presence: 1, procedure_presence: 1, procedure_count: 1, patient_age: 1, episode_duration: 1, discharge_mode: 1, all: 1, any: 1 };
  var FACT = { completed_years: "wiek", duration_days: "czas pobytu", discharge_mode: "tryb wypisu" };
  var MODES = { hospitalization: "hospitalizacja", planned: "hospitalizacja planowa", one_day: "leczenie jednego dnia" };
  var REPORTS = { empty: "Kompletny raport bez produktów", unknown: "Raport niepełny lub nieznany", present: "Kompletny raport z produktami" };
  var PRODUCT_CLASS = { W: "wyłączony z grupowania (katalog 1w): pominięto procedury z jego okresu", Wpartial: "wyłączony z grupowania (katalog 1w), bez obu dat: procedury z możliwego okresu są nierozstrzygnięte", neutral: "nie wpływa na grupowanie", unknown: "nie ustalono w odczytanych katalogach aktu: wpływ nierozstrzygnięty" };

  function el(tag, text, cls) { var node = document.createElement(tag); if (text !== undefined && text !== null) node.textContent = text; if (cls) node.className = cls; return node; }
  function byId(id) { return document.getElementById(id); }
  function value(id) { return byId(id).value.trim(); }
  function radio(name) { var checked = form.querySelector("input[name='" + name + "']:checked"); return checked ? checked.value : ""; }
  function plural(n, one, few, many) { var tens = n % 100, units = n % 10; return n === 1 ? one : units >= 2 && units <= 4 && (tens < 12 || tens > 14) ? few : many; }
  function points(n) { return n === null || n === undefined ? "nieustalone" : (n / 100).toLocaleString("pl-PL", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " pkt"; }
  function button(parent, text, cls, callback) { var node = el("button", text, cls); node.type = "button"; node.addEventListener("click", callback); parent.append(node); return node; }
  function chip(text, cls) { return el("span", text, "code-chip" + (cls ? " " + cls : "")); }

  /* Dates are typed as DD.MM.RRRR whatever the browser language; the API receives ISO. */
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
  /* One field anatomy everywhere: a label row (label, optional marker, ? tip) above
     the control, so controls in a row always start at the same height. */
  function field(parent, title, options) {
    options = options || {};
    var wrap = el("div", undefined, "field"), head = el("div", undefined, "field-label"), label = el("label", title), id = "field-" + (++nextId);
    if (options.optional) { label.append(" "); label.append(el("span", "(opcjonalnie)", "optional")); }
    head.append(label); if (options.tip) head.append(tipButton(options.tip, title));
    var control = el("div", undefined, "control" + (options.control ? " " + options.control : ""));
    label.htmlFor = id; wrap.append(head, control); parent.append(wrap);
    return { wrap: wrap, label: label, control: control, id: id };
  }
  function tipButton(text, about) { var b = el("button", "?", "tip"); b.type = "button"; b.dataset.tip = text; b.setAttribute("aria-label", "Wyjaśnienie: " + about); b.setAttribute("aria-expanded", "false"); return b; }
  function calendarButton(kind, label) {
    var NS = "http://www.w3.org/2000/svg", b = el("button", undefined, "picker-button"), svg = document.createElementNS(NS, "svg"), rect = document.createElementNS(NS, "rect"), path = document.createElementNS(NS, "path");
    b.type = "button"; b.dataset.picker = kind; b.setAttribute("aria-label", label);
    svg.setAttribute("viewBox", "0 0 20 20"); svg.setAttribute("aria-hidden", "true"); svg.setAttribute("focusable", "false");
    [["x", "2.5"], ["y", "4"], ["width", "15"], ["height", "13.5"], ["rx", "2"]].forEach(function (a) { rect.setAttribute(a[0], a[1]); });
    path.setAttribute("d", "M2.5 8h15M6.5 2v4M13.5 2v4");
    [rect, path].forEach(function (shape) { shape.setAttribute("fill", "none"); shape.setAttribute("stroke", "currentColor"); shape.setAttribute("stroke-width", "1.6"); shape.setAttribute("stroke-linecap", "round"); });
    svg.append(rect, path); b.append(svg); return b;
  }
  function dateField(parent, title, required, optional) {
    var f = field(parent, title, { optional: optional, control: "control-picker" }), input = el("input");
    input.type = "text"; input.className = "date-pl"; input.inputMode = "numeric"; input.maxLength = 10; input.placeholder = "DD.MM.RRRR"; input.required = !!required; input.id = f.id;
    f.control.append(input, calendarButton("date", title + ": wybierz z kalendarza")); return input;
  }

  /* Tooltips: tap or click pins, hover and focus preview, Escape closes. */
  var tooltip = byId("tooltip"), tipOwner = null, tipPinned = false;
  function showTip(owner, pinned) {
    if (tipOwner && tipOwner !== owner) hideTip(true);
    tipOwner = owner; tipPinned = tipPinned || !!pinned; tooltip.textContent = owner.dataset.tip; tooltip.hidden = false;
    owner.setAttribute("aria-describedby", "tooltip"); owner.setAttribute("aria-expanded", "true");
    var r = owner.getBoundingClientRect(), width = Math.min(320, document.documentElement.clientWidth - 32);
    tooltip.style.width = width + "px";
    tooltip.style.left = Math.max(16, Math.min(r.left + r.width / 2 - width / 2, document.documentElement.clientWidth - width - 16)) + window.scrollX + "px";
    tooltip.style.top = r.bottom + window.scrollY + 8 + "px";
  }
  function hideTip(force) {
    if (!tipOwner || (tipPinned && !force)) return;
    tipOwner.removeAttribute("aria-describedby"); tipOwner.setAttribute("aria-expanded", "false"); tipOwner = null; tipPinned = false; tooltip.hidden = true;
  }
  document.addEventListener("click", function (event) {
    var tip = event.target.closest && event.target.closest(".tip");
    if (tip) { if (tipOwner === tip && tipPinned) hideTip(true); else showTip(tip, true); return; }
    if (!event.target.closest || !event.target.closest(".tooltip")) hideTip(true);
  });
  document.addEventListener("mouseover", function (event) { var tip = event.target.closest && event.target.closest(".tip"); if (tip && !tipPinned) showTip(tip, false); });
  document.addEventListener("mouseout", function (event) { var tip = event.target.closest && event.target.closest(".tip"); if (tip) hideTip(false); });
  document.addEventListener("focusin", function (event) { if (event.target.classList && event.target.classList.contains("tip") && !tipPinned) showTip(event.target, false); });
  document.addEventListener("focusout", function (event) { if (event.target.classList && event.target.classList.contains("tip")) hideTip(true); });

  /* Calendar in Polish whatever the browser language; typing stays the fast path. */
  var picker = byId("picker"), pickerInput = null, pickerKind = "date", view = { year: 2026, month: 0 }, cursor = "";
  var MONTHS = ["styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec", "lipiec", "sierpień", "wrzesień", "październik", "listopad", "grudzień"];
  var MONTHS_OF = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia", "września", "października", "listopada", "grudnia"];
  var WEEKDAYS = ["pn", "wt", "śr", "cz", "pt", "so", "nd"];
  function isoOf(y, m, d) { var date = new Date(Date.UTC(y, m, d)); return date.toISOString().slice(0, 10); }
  function todayIso() { var now = new Date(); return isoOf(now.getFullYear(), now.getMonth(), now.getDate()); }
  function openPicker(button) {
    hideTip(true);
    pickerInput = button.parentElement.querySelector("input"); pickerKind = button.dataset.picker;
    var chosen = pickerKind === "date" ? iso(pickerInput.value) : (isoMonth(pickerInput.value) ? isoMonth(pickerInput.value) + "-01" : "");
    cursor = chosen || iso(value("discharged")) || iso(value("admitted")) || todayIso();
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
    else if (cell && (event.key === "PageUp" || event.key === "PageDown")) { event.preventDefault(); if (date) { shiftView(event.key === "PageUp" ? -1 : 1); } else { shiftView(event.key === "PageUp" ? -1 : 1); } var focus = picker.querySelector("[data-value='" + (date ? cursor : cursor.slice(0, 7)) + "']"); if (focus) focus.focus(); }
  });
  document.addEventListener("click", function (event) {
    var opener = event.target.closest && event.target.closest(".picker-button");
    if (opener) { if (!picker.hidden && pickerInput === opener.parentElement.querySelector("input")) closePicker(true); else openPicker(opener); return; }
    if (!picker.hidden && !(event.target.closest && event.target.closest(".picker"))) closePicker(false);
  });

  function serviceDate() { return radio("service-mode") === "discharge" ? iso(value("discharged")) : radio("service-mode") === "other" ? iso(value("service-date")) : ""; }
  function reportingMonth() { var d = iso(value("discharged")); return radio("month-mode") === "discharge" ? (d ? d.slice(0, 7) : "") : radio("month-mode") === "other" ? isoMonth(value("reporting-month")) : ""; }
  function dischargeMode() { var s = byId("discharge-select").value; return s === "other" ? value("discharge-mode") : s; }
  function syncChoices() {
    var d = iso(value("discharged"));
    byId("service-discharge-label").textContent = d ? "Dzień wypisu (" + pl(d) + ")" : "Dzień wypisu";
    byId("month-discharge-label").textContent = d ? "Miesiąc wypisu (" + plMonth(d) + ")" : "Miesiąc wypisu";
    byId("service-date").parentElement.hidden = radio("service-mode") !== "other";
    byId("reporting-month").parentElement.hidden = radio("month-mode") !== "other";
    byId("discharge-other").hidden = byId("discharge-select").value !== "other";
    byId("product-list").hidden = radio("product-report") !== "present";
  }

  /* Any edit makes an earlier answer obsolete; an in-flight response is refused by sequence. */
  function invalidate() { sequence++; if (controller) controller.abort(); answer.replaceChildren(); status.textContent = ""; syncChoices(); renderSummary(); }

  function showStep(index, focus) {
    current = index; reached = Math.max(reached, index);
    form.querySelectorAll(".wizard-step").forEach(function (section) { section.hidden = Number(section.dataset.step) !== index; });
    document.querySelectorAll("[data-goto]").forEach(function (item) {
      var target = Number(item.dataset.goto);
      item.disabled = target > reached; item.parentElement.className = target === index ? "is-current" : target <= reached ? "is-done" : "";
      if (target === index) item.setAttribute("aria-current", "step"); else item.removeAttribute("aria-current");
    });
    byId("wizard-progress").textContent = "Krok " + (index + 1) + " z " + NAMES.length + ": " + NAMES[index];
    back.hidden = index === 0; next.hidden = index === RESULT;
    next.textContent = index === RESULT - 1 ? "Sprawdź grupowanie" : "Dalej: " + NAMES[index + 1];
    form.classList.toggle("is-result", index === RESULT);
    clearErrors();
    if (index === 1) prefillWard();
    renderSummary();
    if (focus) { var wizard = document.querySelector(".wizard"); byId("step-" + index + "-title").focus({ preventScroll: true }); if (wizard.getBoundingClientRect().top < 0) wizard.scrollIntoView({ block: "start" }); }
    if (index === RESULT) evaluateCase();
  }

  /* Validation: problems are listed above the step and point to the field to fix. */
  function clearErrors() { errorsBox.hidden = true; errorsBox.replaceChildren(); form.querySelectorAll("[aria-invalid]").forEach(function (input) { input.removeAttribute("aria-invalid"); }); form.querySelectorAll(".choice.is-invalid").forEach(function (group) { group.classList.remove("is-invalid"); }); }
  // A corrected field drops its error mark at once; the list refreshes on the next step change.
  function clearMark(target) { if (target.type === "radio") { var group = target.closest(".choice"); if (group) group.classList.remove("is-invalid"); } else if (target.removeAttribute) target.removeAttribute("aria-invalid"); }
  function problems(index) {
    var found = [];
    function add(input, message) { found.push({ input: input, message: message }); }
    var admitted = iso(value("admitted")), discharged = iso(value("discharged"));
    function inEpisode(date) { return !admitted || !discharged || (date >= admitted && date <= discharged); }
    if (index === 0) {
      if (!admitted) add(byId("admitted"), "Podaj datę przyjęcia jako DD.MM.RRRR.");
      if (!discharged) add(byId("discharged"), "Podaj datę wypisu jako DD.MM.RRRR.");
      else if (admitted && discharged < admitted) add(byId("discharged"), "Wypis nie może być wcześniejszy niż przyjęcie.");
      if (!/^\d{1,3}$/.test(value("age")) || Number(value("age")) > 200) add(byId("age"), "Podaj wiek w ukończonych latach (0-200).");
      if (byId("discharge-select").value === "other" && !(/^\d{1,2}$/.test(value("discharge-mode")) && Number(value("discharge-mode")) >= 1)) add(byId("discharge-mode"), "Kod trybu wypisu to liczba od 1 do 99.");
      if (!radio("service-mode")) add(form.querySelector("input[name=service-mode]"), "Wybierz dzień świadczenia: dzień wypisu albo inny dzień.");
      else if (radio("service-mode") === "other" && !iso(value("service-date"))) add(byId("service-date"), "Podaj dzień świadczenia jako DD.MM.RRRR.");
      else if (serviceDate() && !inEpisode(serviceDate())) add(byId("service-date"), "Dzień świadczenia musi mieścić się między przyjęciem a wypisem.");
      if (!radio("month-mode")) add(form.querySelector("input[name=month-mode]"), "Wybierz miesiąc sprawozdawczy: miesiąc wypisu albo inny miesiąc.");
      else if (radio("month-mode") === "other" && !isoMonth(value("reporting-month"))) add(byId("reporting-month"), "Podaj miesiąc sprawozdawczy jako MM.RRRR.");
    }
    if (index === 1) {
      if (!wards.length) add(byId("add-ward"), "Dodaj co najmniej jeden pobyt.");
      wards.forEach(function (ward, i) {
        var name = "Pobyt " + (i + 1) + ": ", start = iso(ward.start.value), end = iso(ward.end.value);
        if (!/^[0-9]{4}$/.test(ward.department.value.trim())) add(ward.department, name + "wybierz specjalność z listy albo wpisz 4-cyfrowy kod.");
        if (!start || !end) add(start ? ward.end : ward.start, name + "podaj obie daty jako DD.MM.RRRR.");
        else if (end < start) add(ward.end, name + "wypis nie może być wcześniejszy niż przyjęcie.");
        else if (!inEpisode(start) || !inEpisode(end)) add(ward.start, name + "daty muszą mieścić się w hospitalizacji.");
      });
    }
    if (index === 2) {
      if (!CODE.test(code(principal))) add(principal.search.input, "Rozpoznanie zasadnicze: wybierz podpowiedź albo wpisz dokładny kod ICD-10, np. K40.9.");
      secondary.forEach(function (item, i) { if (!CODE.test(code(item))) add(item.search.input, "Rozpoznanie współistniejące " + (i + 1) + ": wybierz kod albo usuń wiersz."); });
    }
    if (index === 3) {
      procedures.forEach(function (item, i) {
        var name = "Procedura " + (i + 1) + ": ", when = item.date.value.trim();
        if (!CODE.test(code(item))) add(item.search.input, name + "wybierz kod ICD-9 albo usuń wiersz.");
        if (when && !iso(when)) add(item.date, name + "podaj datę jako DD.MM.RRRR albo zostaw puste pole.");
        else if (when && !inEpisode(iso(when))) add(item.date, name + "data musi mieścić się w hospitalizacji.");
      });
      if (radio("product-report") === "present") {
        if (!products.length) add(byId("add-product"), "Dodaj produkty z raportu albo wybierz raport bez produktów.");
        products.forEach(function (item, i) {
          var name = "Produkt " + (i + 1) + ": ", start = item.start.value.trim(), end = item.end.value.trim();
          if (!PRODUCT.test(item.code.value.trim())) add(item.code, name + "wpisz kod produktu rozliczeniowego, np. 5.52.01.0001363.");
          [[item.start, start], [item.end, end]].forEach(function (pair) {
            if (pair[1] && !iso(pair[1])) add(pair[0], name + "podaj datę jako DD.MM.RRRR albo zostaw puste pole.");
            else if (pair[1] && !inEpisode(iso(pair[1]))) add(pair[0], name + "data musi mieścić się w hospitalizacji.");
          });
          if (iso(start) && iso(end) && iso(end) < iso(start)) add(item.end, name + "koniec nie może być wcześniejszy niż początek.");
        });
      }
    }
    return found;
  }
  function showProblems(found) {
    clearErrors();
    if (!found.length) return true;
    errorsBox.append(el("p", "Popraw przed przejściem dalej:"));
    var list = el("ul");
    found.forEach(function (item) {
      if (item.input.type === "radio") item.input.closest(".choice").classList.add("is-invalid"); else item.input.setAttribute("aria-invalid", "true");
      var entry = el("li"); button(entry, item.message, "link-button", function () { item.input.focus(); }); list.append(entry);
    });
    errorsBox.append(list); errorsBox.hidden = false; found[0].input.focus();
    return false;
  }
  function firstInvalid() { for (var i = 0; i < RESULT; i++) { var found = problems(i); if (found.length) return { step: i, found: found }; } return null; }

  /* Stays, diagnoses and procedures keep every occurrence; attribution stays explicit. */
  function prefillWard() {
    var first = wards[0];
    if (first && !first.start.value && !first.end.value && iso(value("admitted")) && iso(value("discharged"))) { first.start.value = value("admitted"); first.end.value = value("discharged"); renderSummary(); }
  }
  function refreshAttributions() {
    form.querySelectorAll("select[data-diagnosis]").forEach(function (select) {
      var old = select.value; select.replaceChildren(new Option("Nie określono", ""));
      if (select.dataset.diagnosis === "yes") select.append(new Option("Cała hospitalizacja", "episode"));
      wards.forEach(function (ward, index) { select.append(new Option("Pobyt " + (index + 1) + (ward.department.value ? " (" + ward.department.value + ")" : ""), ward.id)); });
      select.value = old; if (select.selectedIndex < 0) select.value = "";
    });
  }
  function addWard() {
    if (wards.length >= 100) return;
    var row = el("div", undefined, "fact-row"), head = el("div", undefined, "fact-head"), title = el("h3", "Pobyt " + (wards.length + 1));
    head.append(title); row.append(head);
    var search = combobox(row, "Specjalność oddziału", Object.assign(specialtySearch(), { after: refreshAttributions }));
    var grid = el("div", undefined, "field-grid fact-details"); row.append(grid);
    var start = dateField(grid, "Przyjęcie na oddział", true), end = dateField(grid, "Wypis z oddziału", true);
    var ward = { id: "ward" + (++nextId), row: row, title: title, department: search.input, search: search, start: start, end: end };
    button(head, "Usuń", "link-button", function () {
      if (wards.length === 1) return;
      search.close(); wards.splice(wards.indexOf(ward), 1); row.remove(); wards.forEach(function (w, i) { w.title.textContent = "Pobyt " + (i + 1); }); refreshAttributions(); invalidate();
    });
    search.input.addEventListener("input", refreshAttributions);
    wards.push(ward); byId("wards").append(row); refreshAttributions();
  }
  /* Products keep every occurrence; a missing date stays unknown, never assumed. */
  function addProduct() {
    if (products.length >= 1000) return null;
    var row = el("div", undefined, "fact-row"), head = el("div", undefined, "fact-head"), title = el("h3");
    head.append(title); row.append(head);
    var grid = el("div", undefined, "field-grid field-grid-3 fact-details"); row.append(grid);
    var f = field(grid, "Kod produktu", { tip: "Kod produktu rozliczeniowego z raportu, np. 5.52.01.0001363. Rodzaj produktu ustalamy według katalogów aktu obowiązującego w dniu świadczenia." });
    var input = el("input"); input.type = "text"; input.id = f.id; input.inputMode = "decimal"; input.maxLength = 15; input.placeholder = "5.00.00.0000000"; input.autocomplete = "off"; input.spellcheck = false; f.control.append(input);
    var item = { row: row, title: title, code: input, start: dateField(grid, "Początek", false, true), end: dateField(grid, "Koniec", false, true) };
    button(head, "Usuń", "link-button", function () { products.splice(products.indexOf(item), 1); row.remove(); renumberProducts(); invalidate(); });
    products.push(item); renumberProducts(); byId("products").append(row);
    return item;
  }
  function renumberProducts() { products.forEach(function (item, i) { item.title.textContent = "Produkt " + (i + 1); }); }

  /* One combobox for ICD codes (server search) and specialties (local dictionary). */
  function combobox(parent, title, settings) {
    var f = field(parent, title, { tip: settings.tip, control: "search-box" }), input = el("input"), list = el("ul", undefined, "suggestions");
    var hint = el("p", settings.idle, "field-hint"); hint.setAttribute("aria-live", "polite");
    var id = "list-" + (++nextId), active = -1, options = [], request = null, delay = 0, generation = 0, state = { label: "" };
    input.type = "text"; input.required = true; input.maxLength = 120; input.spellcheck = false; input.id = f.id; input.placeholder = settings.placeholder || "";
    input.setAttribute("role", "combobox"); input.setAttribute("aria-autocomplete", "list"); input.setAttribute("aria-expanded", "false"); input.setAttribute("aria-controls", id); list.id = id; list.setAttribute("role", "listbox"); list.hidden = true;
    f.control.append(input, list); f.wrap.append(hint);
    function close() { generation++; clearTimeout(delay); if (request) request.abort(); request = null; list.hidden = true; list.replaceChildren(); options = []; active = -1; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); }
    function choose(hit) { var picked = settings.pick(hit); input.value = picked.value; state.label = picked.label; hint.textContent = picked.hint || picked.label; close(); if (settings.after) settings.after(); invalidate(); }
    function show(hits) {
      options = hits; list.replaceChildren();
      hits.forEach(function (hit, index) { var item = el("li", undefined, "suggestion"); item.id = id + "-" + index; item.setAttribute("role", "option"); item.setAttribute("aria-selected", "false"); settings.render(hit).forEach(function (node) { item.append(node); }); item.addEventListener("pointerdown", function (event) { event.preventDefault(); }); item.addEventListener("click", function () { choose(hit); }); list.append(item); });
      list.hidden = !hits.length; input.setAttribute("aria-expanded", String(!!hits.length));
      if (!state.label) hint.textContent = hits.length ? "Strzałki i Enter wybierają podpowiedź." : settings.empty;
    }
    input.addEventListener("input", function () {
      close(); state.label = ""; hint.textContent = settings.idle;
      var query = input.value.trim(), version = generation;
      if (!query) return;
      var exact = settings.exact ? settings.exact(query) : null;
      if (exact) { state.label = exact.label; hint.textContent = exact.hint; }
      delay = setTimeout(function () {
        request = settings.lookup(query, function (hits) { if (generation === version && input.isConnected) show(hits); }, function () { if (generation === version) hint.textContent = settings.failed; });
      }, settings.delay);
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { close(); return; }
      if (!options.length) return;
      if (event.key === "Enter" && active >= 0) { event.preventDefault(); choose(options[active]); }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); active = (active + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length; Array.from(list.children).forEach(function (node, i) { node.setAttribute("aria-selected", String(i === active)); }); input.setAttribute("aria-activedescendant", list.children[active].id); list.children[active].scrollIntoView({ block: "nearest" }); }
    });
    input.addEventListener("blur", close);
    return { input: input, close: close, state: state, hint: hint };
  }
  function icdSearch(system) {
    return {
      idle: "Wpisz kod lub opis.", empty: "Brak podpowiedzi. Możesz wpisać dokładny kod z dokumentacji.", failed: "Podpowiedzi są chwilowo niedostępne. Możesz wpisać dokładny kod.", delay: 220,
      lookup: function (query, done, fail) {
        var release = releases[system], controller = new AbortController();
        if (!release) return null;
        window.MinosRequest("/api/v1/search", { method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store", credentials: "omit", signal: controller.signal,
          body: JSON.stringify({ system: system, release_id: release, query: query, limit: 6 }) }).then(function (data) { done(data.results || []); }).catch(fail);
        return controller;
      },
      render: function (hit) { return [chip(hit.code), el("span", hit.label, "suggestion-label")]; },
      pick: function (hit) { return { value: hit.code, label: hit.label }; }
    };
  }
  /* Specialties: official names, hospital wards first, derived children's codes marked. */
  var specialties = [], specialtyByCode = {};
  function fold(text) { return text.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/ł/g, "l"); }
  function specialtyName(entry) { return entry.name + (entry.derived ? ", dla dzieci" : ""); }
  function specialtySearch() {
    return {
      tip: "Kod specjalności komórki organizacyjnej (część VIII kodu resortowego), ten sam co w sprawozdaniu świadczenia. Szukaj nazwą oddziału albo wpisz 4 cyfry. Kod decyduje o wyłączeniu pobytu z grupowania.",
      placeholder: "np. chirurgiczny albo 4500", idle: "Szukaj nazwą oddziału albo wpisz kod.", empty: "Brak takiej specjalności w załączniku nr 2. Wpisz 4-cyfrowy kod z dokumentacji.", failed: "", delay: 60,
      lookup: function (query, done) {
        var q = fold(query), digits = /^\d+$/.test(q);
        var hits = specialties.filter(function (entry) { return digits ? entry.code.indexOf(q) === 0 : entry.folded.indexOf(q) >= 0; });
        hits.sort(function (a, b) { return a.rank - b.rank || (a.folded.indexOf(q) === 0 ? 0 : 1) - (b.folded.indexOf(q) === 0 ? 0 : 1) || a.position - b.position; });
        done(hits.slice(0, 8)); return null;
      },
      render: function (entry) {
        var text = el("span", undefined, "suggestion-label");
        text.append(el("span", entry.name), el("span", entry.derived ? "dla dzieci, § 9 ust. 4" : entry.section, "suggestion-meta"));
        return [chip(entry.code), text];
      },
      pick: function (entry) { return { value: entry.code, label: specialtyName(entry), hint: specialtyName(entry) + (entry.derived ? " (kod wyprowadzony z § 9 ust. 4)" : "") }; },
      exact: function (query) {
        if (!/^\d{4}$/.test(query)) return null;
        var entry = specialtyByCode[query];
        return entry ? { label: specialtyName(entry), hint: specialtyName(entry) } : { label: "", hint: specialties.length ? "Kod spoza załącznika nr 2; zostanie przekazany bez zmian." : "Kod zostanie przekazany bez zmian." };
      }
    };
  }
  /* Official names for specialties and discharge modes; older bundles keep the interim choices. */
  function dischargeOptions(modes) {
    var select = byId("discharge-select"), old = select.value;
    select.replaceChildren(new Option("Nie podano", ""));
    if (modes) modes.forEach(function (mode) { select.append(new Option(mode.code + ": " + mode.name, String(mode.code))); });
    else { select.append(new Option("9: zgon pacjenta", "9")); select.append(new Option("Inny kod", "other")); }
    select.value = old; if (select.selectedIndex < 0) select.value = "";
  }
  function loadDictionaries() {
    window.MinosRequest("/api/v1/grouping/dictionaries", { cache: "default", credentials: "omit" }).then(function (data) {
      specialties = data.specialties.entries.map(function (entry) {
        return { code: entry.code, name: entry.name, section: entry.section, derived: entry.derived, position: specialties.length, folded: fold(entry.code + " " + entry.name), rank: (entry.section.indexOf("Oddziały szpitalne") === 0 ? 0 : 2) + (entry.derived ? 1 : 0) };
      });
      specialties.forEach(function (entry, index) { entry.position = index; specialtyByCode[entry.code] = entry; });
      dischargeOptions(data.discharge_modes.entries);
    }).catch(function () { dischargeOptions(null); });
  }
  function addCode(kind) {
    var diagnosis = kind !== "procedure", array = kind === "secondary" ? secondary : procedures;
    if ((kind === "secondary" && array.length >= 100) || (kind === "procedure" && array.length >= 1000)) return null;
    var row = el("div", undefined, "fact-row"), head = el("div", undefined, "fact-head");
    var label = kind === "principal" ? "Rozpoznanie zasadnicze (ICD-10)" : diagnosis ? "Kod ICD-10 lub opis" : "Kod ICD-9 lub opis";
    var title = kind === "principal" ? null : el("h3"); if (title) head.append(title);
    row.append(head);
    // The search owns a full row so suggestions never squeeze into a narrow column.
    var search = combobox(row, label, icdSearch(diagnosis ? "icd10-pl" : "icd9-pl"));
    var grid = el("div", undefined, "field-grid fact-details"); row.append(grid);
    var where = field(grid, "Poziom zgłoszenia", { tip: diagnosis ? "Cała hospitalizacja: rozpoznanie zgłoszone na poziomie hospitalizacji. Pobyt: rozpoznanie przypisane do oddziału. Nie określono, gdy dokumentacja tego nie podaje." : "Pobyt, na którym wykonano procedurę. Nie określono, gdy dokumentacja tego nie podaje." });
    var select = el("select"); select.id = where.id; select.dataset.diagnosis = diagnosis ? "yes" : "no"; where.control.append(select);
    var item = { row: row, search: search, attribution: select, title: title, date: diagnosis ? null : dateField(grid, "Data wykonania", false, true) };
    if (kind === "principal") principal = item;
    else { array.push(item); button(head, "Usuń", "link-button", function () { search.close(); array.splice(array.indexOf(item), 1); row.remove(); renumber(array, diagnosis); invalidate(); }); renumber(array, diagnosis); }
    byId(kind === "procedure" ? "procedures" : kind).append(row); refreshAttributions();
    return item;
  }
  function renumber(array, diagnosis) { array.forEach(function (item, i) { item.title.textContent = (diagnosis ? "Rozpoznanie współistniejące " : "Procedura ") + (i + 1); }); }
  function code(item) { return item ? item.search.input.value.trim().toUpperCase() : ""; }
  function codeFact(item, diagnosis) {
    var result = { code: code(item) }, where = item.attribution.value;
    if (where === "episode" && diagnosis) result.hospitalization_level = true;
    else if (where) result.stay_id = where;
    if (item.date && iso(item.date.value)) result.performed_on = iso(item.date.value);
    return result;
  }
  function payload() {
    var data = { admitted_on: iso(value("admitted")), discharged_on: iso(value("discharged")), age_completed_years: Number(value("age")), hospitalization_type: radio("hospitalization"),
      principal_diagnosis: codeFact(principal, true), secondary_diagnoses: secondary.map(function (r) { return codeFact(r, true); }), procedures: procedures.map(function (r) { return codeFact(r, false); }),
      stays: wards.map(function (w) { return { identifier: w.id, department: w.department.value.trim(), admitted_on: iso(w.start.value), discharged_on: iso(w.end.value) }; }),
      product_reporting: radio("product-report") === "unknown" ? "unknown" : "complete",
      settlement_products: radio("product-report") === "present" ? products.map(function (item) {
        var fact = { code: item.code.value.trim() };
        if (iso(item.start.value)) fact.started_on = iso(item.start.value);
        if (iso(item.end.value)) fact.ended_on = iso(item.end.value);
        return fact;
      }) : [] };
    if (dischargeMode()) data.discharge_mode = Number(dischargeMode());
    return { schema_version: 1, encounter: data, service_date: serviceDate(), reporting_month: reportingMonth() + "-01", declared_modifiers: declared.slice() };
  }

  /* Case card: what will be evaluated, step by step, with a way back to each step. */
  function codeLine(item, role) {
    var line = el("p", undefined, "case-code"), c = code(item);
    if (!CODE.test(c)) { line.append(el("span", "Nie wybrano kodu", "is-empty")); return line; }
    line.append(chip(c)); if (item.search.state.label) line.append(el("span", item.search.state.label)); if (role) line.append(el("span", role, "case-role"));
    return line;
  }
  function renderSummary() {
    var admitted = iso(value("admitted")), discharged = iso(value("discharged")), sections = [];
    var hosp = [];
    if (admitted || discharged) hosp.push(el("p", (pl(admitted) || "?") + " - " + (pl(discharged) || "?"), "case-strong"));
    var facts = [/^\d{1,3}$/.test(value("age")) ? value("age") + " " + plural(Number(value("age")), "rok", "lata", "lat") : "", MODES[radio("hospitalization")], dischargeMode() ? "tryb wypisu " + dischargeMode() : ""].filter(Boolean);
    if (admitted || value("age")) hosp.push(el("p", facts.join(", ")));
    if (serviceDate() || reportingMonth()) hosp.push(el("p", [serviceDate() ? "świadczenie " + pl(serviceDate()) : "", reportingMonth() ? "sprawozdanie " + plMonth(reportingMonth()) : ""].filter(Boolean).join(", "), "case-muted"));
    sections.push(hosp);
    sections.push(wards.filter(function (w) { return /^[0-9]{4}$/.test(w.department.value.trim()); }).map(function (w) {
      var line = el("p", undefined, "case-code"); line.append(chip(w.department.value.trim()));
      if (w.search.state.label) line.append(el("span", w.search.state.label));
      line.append(el("span", iso(w.start.value) && iso(w.end.value) ? pl(iso(w.start.value)) + " - " + pl(iso(w.end.value)) : "daty do uzupełnienia", "case-role"));
      return line;
    }));
    var dx = []; if (principal && (code(principal) || reached > 2)) dx.push(codeLine(principal, "zasadnicze"));
    secondary.forEach(function (item) { dx.push(codeLine(item)); });
    sections.push(dx);
    var px = procedures.map(function (item) { var line = codeLine(item); if (item.date && iso(item.date.value)) line.append(el("span", pl(iso(item.date.value)), "case-role")); return line; });
    if (!px.length && reached > 3) px.push(el("p", "Bez procedur", "case-muted"));
    if (reached > 3) px.push(el("p", REPORTS[radio("product-report")], "case-muted"));
    if (radio("product-report") === "present") products.forEach(function (item) {
      var line = el("p", undefined, "case-code"), c = item.code.value.trim(), start = iso(item.start.value), end = iso(item.end.value);
      if (!PRODUCT.test(c)) { line.append(el("span", "Nie podano kodu produktu", "is-empty")); px.push(line); return; }
      line.append(chip(c), el("span", start || end ? (pl(start) || "?") + " - " + (pl(end) || "?") : "bez dat", "case-role")); px.push(line);
    });
    sections.push(px);
    sections.push(declared.length ? [el("p", "Potwierdzone modyfikatory: " + declared.join(", "), "case-muted")] : []);
    summary.replaceChildren();
    // Progress belongs to the stepper; the card lists only what was entered.
    sections.forEach(function (content, index) {
      if (!content.length) return;
      var li = el("li", undefined, "case-section"), head = el("div", undefined, "case-head");
      head.append(el("h3", NAMES[index]));
      if (index <= reached && index !== current) { var change = button(head, "Zmień", "link-button", function () { showStep(index, true); }); change.setAttribute("aria-label", "Zmień: " + NAMES[index]); }
      var body = el("div", undefined, "case-body");
      content.forEach(function (node) { body.append(node); });
      li.append(head, body);
      summary.append(li);
    });
    if (!summary.childNodes.length) summary.append(el("li", "Tu pojawią się wpisane dane.", "case-section is-empty"));
  }

  /* Plain-language reading of one consulted condition, in catalog vernacular. */
  function lists(event) {
    var s = event.selector; if (!s) return "";
    var labels = (s.list_labels || []).filter(Boolean);
    if (labels.length) return (labels.length > 1 ? "z list " : "z listy ") + labels.join(", ");
    if (s.explicit_codes && s.explicit_codes.length) return "spośród kodów " + s.explicit_codes.slice(0, 6).join(", ") + (s.explicit_codes.length > 6 ? " i innych" : "");
    if (s.families && s.families.length) return "z rodzin " + s.families.join(", ");
    return "z listy źródłowej";
  }
  function describe(event) {
    var p = event.parameters || {};
    switch (event.kind) {
      case "diagnosis_presence": return (p.role === "principal" ? "Rozpoznanie zasadnicze " : p.role === "secondary" ? "Rozpoznanie współistniejące " : "Rozpoznanie ") + lists(event);
      case "procedure_presence": return "Procedura " + lists(event);
      case "procedure_count": return "Liczba procedur " + lists(event) + " " + (CMP[p.comparison] || "") + " " + p.required_count;
      case "patient_age": return p.unit === "completed_years" ? "Wiek " + CMP[p.comparison] + " " + p.threshold + " r.ż." : "Wiek w nieobsługiwanej jednostce";
      case "episode_duration": return "Czas pobytu " + (p.bounds || []).map(function (b) { return CMP[b.comparison] + " " + b.days + " " + plural(b.days, "dzień", "dni", "dni"); }).join(" i ");
      case "discharge_mode": return "Tryb wypisu " + (p.allowed_reported_codes || []).join(" lub ");
      case "all": return "Wszystkie poniższe warunki";
      case "any": return "Co najmniej jeden z poniższych warunków";
      default: return "Warunek bez obsługiwanej interpretacji";
    }
  }
  function observed(event) {
    if (event.matched_codes && event.matched_codes.length) return "w przypadku: " + event.matched_codes.join(", ");
    var o = event.observed || {}, key = Object.keys(o)[0];
    if (key) return o[key] === null ? "w przypadku: brak danych" : "w przypadku: " + o[key] + (key === "completed_years" ? " " + plural(o[key], "rok", "lata", "lat") : key === "duration_days" ? " " + plural(o[key], "dzień", "dni", "dni") : "");
    if (!KINDS[event.kind]) return "";
    if (event.verdict === "not_met" && (event.kind === "diagnosis_presence" || event.kind === "procedure_presence")) return "brak w przypadku";
    return event.verdict === "met" || event.kind === "all" || event.kind === "any" ? "" : event.explanation;
  }
  function ruleTree(group) {
    var events = {}; group.trace.forEach(function (event) { events[event.node] = event; });
    function item(id, depth) {
      var event = events[id]; if (!event || depth > 16) return null;
      var li = el("li", undefined, "rule rule-" + event.verdict), line = el("div", undefined, "rule-line");
      var mark = el("span", MARK[event.verdict] || "?", "rule-mark"); mark.setAttribute("aria-hidden", "true");
      line.append(mark, el("span", SPOKEN[event.verdict] || "", "visually-hidden"), el("span", describe(event), "rule-text"));
      var detail = observed(event); if (detail) line.append(el("span", detail, "rule-detail"));
      li.append(line);
      var children = (event.parameters || {}).children || [];
      if (children.length) { var nested = el("ul"); children.forEach(function (child) { var x = item(child, depth + 1); if (x) nested.append(x); }); li.append(nested); }
      return li;
    }
    var tree = el("ul", undefined, "rule-tree");
    var roots = group.roots.length ? group.roots : group.trace.map(function (event) { return event.node; });
    if (group.roots.length > 1) {
      var either = el("li", undefined, "rule rule-" + (group.conditions || "undetermined")), line = el("div", undefined, "rule-line"), nested = el("ul");
      line.append(el("span", MARK[group.conditions] || "?", "rule-mark"), el("span", "Jedna z alternatyw w źródle", "rule-text")); either.append(line);
      roots.forEach(function (root) { var x = item(root, 1); if (x) nested.append(x); }); either.append(nested); tree.append(either);
    } else roots.forEach(function (root) { var x = item(root, 0); if (x) tree.append(x); });
    return tree;
  }
  function explain(parent, group, open) {
    var box = el("details", undefined, "explain"); box.open = !!open;
    box.append(el("summary", group.status === "valid" ? "Dlaczego ta grupa?" : "Pokaż warunki"));
    if (group.trace.length) box.append(ruleTree(group)); else box.append(el("p", "Brak warunków do oceny w źródle.", "group-help"));
    if (group.routing_trace && group.routing_trace.reason !== "routing_not_restricted") box.append(el("p", "Ścieżka grupowania: " + (group.explanations[group.explanations.length - 1] || ""), "group-help"));
    var base = group.base_points;
    box.append(el("p", "Punkty bazowe: " + points(base.total_hundredths) + ". " + base.explanation + (group.catalog_row ? " Katalog " + group.catalog + ", wiersz " + group.catalog_row + "." : ""), "group-help"));
    parent.append(box);
  }
  /* Legal supplements carry an internal symbol; coders know them by paragraph. */
  function modifierName(m) {
    var section = /section\s+(\d+)\((\w+)\)/.exec(m.source_citation || "");
    return m.symbol.indexOf("manual-") === 0 && section ? "dopłata z § " + section[1] + " ust. " + section[2] : "modyfikator " + m.symbol;
  }
  /* Modifiers are confirmed per group, from the possibilities the rules return. */
  function modifierBox(parent, group) {
    var list = group.final_points ? group.final_points.modifiers.filter(function (m) { return m.outcome === "possible" || m.outcome === "applied"; }) : [];
    if (!list.length) return;
    var box = el("fieldset", undefined, "modifier-box");
    box.append(el("legend", "Modyfikatory dla tej grupy"), el("p", "Zaznacz tylko, jeśli dokumentacja i umowa potwierdzają wszystkie warunki. Zaznaczenie przelicza wynik.", "group-help"));
    list.forEach(function (m) {
      var label = el("label", undefined, "modifier"), input = el("input"); input.type = "checkbox"; input.checked = m.outcome === "applied";
      // A confirmation re-evaluates on its own; the form-level edit handler must not cancel it.
      input.addEventListener("input", function (event) { event.stopPropagation(); });
      input.addEventListener("change", function (event) {
        event.stopPropagation();
        declared = declared.filter(function (s) { return s !== m.symbol; }); if (input.checked) declared.push(m.symbol);
        declared.sort(); renderSummary(); evaluateCase(true);
      });
      var text = el("span"); text.append(el("span", "Potwierdzam warunki: " + modifierName(m), "modifier-name"), el("span", m.outcome === "applied" ? "zastosowano" : "wartość +" + points(m.would_add_hundredths), "group-help"));
      label.append(input, text); box.append(label);
    });
    parent.append(box);
  }
  function firstReason(group) {
    var leaf = group.trace.filter(function (e) { return e.kind !== "all" && e.kind !== "any"; });
    var failed = leaf.filter(function (e) { return e.verdict === (group.status === "invalid" ? "not_met" : "undetermined"); })[0];
    if (failed) { var detail = observed(failed); return describe(failed) + (detail ? " (" + detail + ")" : ""); }
    var reasons = group.reasons.map(function (reason, i) { return [reason, group.explanations[i]]; }).filter(function (pair) { return pair[0] !== "routing_not_restricted" && pair[0] !== "conservative_allowed"; });
    return reasons.length ? reasons[0][1] : "Część warunków nie ma obsługiwanej interpretacji.";
  }

  function render(data, quiet) {
    answer.replaceChildren();
    if (data.status !== "evaluated") {
      var why = data.status === "product_catalog_unavailable" ? "Zestaw reguł nie zawiera katalogów produktów aktu obowiązującego w dniu świadczenia, więc nie można ustalić, które procedury wyłączają podane produkty. Kwalifikacja nie została oceniona." : "Raport produktów jest niepełny, więc kwalifikacja nie została oceniona: brakujący produkt mógłby wyłączyć procedury. Podaj wszystkie produkty z raportu albo potwierdź raport bez produktów.";
      answer.append(el("p", why, "group-notice")); status.className = "visually-hidden"; status.textContent = "Brak oceny dla tego raportu produktów."; return;
    }
    var order = data.ranking.map(function (r) { return r.catalog + "/" + r.group; });
    var groups = data.groups.slice(), valid = order.map(function (key) { return groups.filter(function (g) { return g.catalog + "/" + g.group === key; })[0]; }).filter(Boolean);
    var n = valid.length;
    var verdict = el("div", undefined, "verdict");
    verdict.append(el("h3", n ? "Obsługiwane warunki " + plural(n, "spełnia", "spełniają", "spełnia") + " " + n + " " + plural(n, "grupa", "grupy", "grup") : "Żadna grupa nie spełnia obsługiwanych warunków", "verdict-title"));
    verdict.append(el("p", n ? "Wybór grupy należy do świadczeniodawcy. Kolejność według " + (data.tariff_status === "bound" ? "punktów z potwierdzonymi modyfikatorami." : "punktów bazowych; brak powiązania taryfy dla tej daty.") : "Sprawdź pozostałych kandydatów i nierozstrzygnięte warunki poniżej. Brak grupy w tej ocenie nie dowodzi braku możliwej grupy.", "group-help"));
    status.className = "visually-hidden";
    status.textContent = n ? "Gotowe: " + n + " " + plural(n, "grupa spełnia", "grupy spełniają", "grup spełnia") + " obsługiwane warunki." : "Gotowe: żadna grupa nie spełnia obsługiwanych warunków.";
    answer.append(verdict);
    if (data.preparation.products.length) {
      var reported = el("section", undefined, "product-effects"), plist = el("ul");
      reported.append(el("h3", "Produkty w raporcie"));
      data.preparation.products.forEach(function (product) {
        var item = el("li", undefined, "product-" + product["class"]), line = el("p");
        var kind = product["class"] === "W" && !(product.started_on && product.ended_on) ? "Wpartial" : product["class"];
        line.append(chip(product.code), el("span", " " + (product.name || (product["class"] === "unknown" ? "Kod nieustalony w katalogach aktu" : "Bez nazwy w katalogu"))));
        item.append(line, el("p", (product.started_on || product.ended_on ? (pl(product.started_on) || "?") + " - " + (pl(product.ended_on) || "?") + ": " : "") + (PRODUCT_CLASS[kind] || ""), "group-help"));
        plist.append(item);
      });
      reported.append(plist);
      // Trace ordinals follow the canonical procedure order of the preparation.
      [["product_w_interval", "Pominięte procedury: "], ["product_interval_possible", "Procedury nierozstrzygnięte przez produkty: "]].forEach(function (pair) {
        var codes = data.preparation.trace.filter(function (d) { return d.phase === "procedure_product" && d.reason === pair[0]; }).map(function (d) { return (data.preparation.procedures[d.ordinal - 1] || {}).code; }).filter(Boolean);
        if (codes.length) reported.append(el("p", pair[1] + codes.join(", "), "group-help"));
      });
      if (data.preparation.ward_adjusted_days === null) reported.append(el("p", "Czas pobytu po wyłączeniach pozostaje nieustalony, więc warunki czasu pobytu są nierozstrzygnięte.", "group-help"));
      answer.append(reported);
    }
    valid.forEach(function (group, index) {
      var card = el("article", undefined, "verdict-group group-valid" + (index === 0 ? " is-top" : "") + (quiet ? " is-quiet" : "")); card.dataset.group = group.group;
      var head = el("div", undefined, "verdict-head"), name = el("div", undefined, "verdict-name"), worth = el("div", undefined, "verdict-points");
      name.append(el("span", group.group, "group-code"), el("span", group.title, "group-title"), el("span", "Katalog " + group.catalog + (group.product_code ? ", produkt " + group.product_code : ""), "group-help"));
      var shown = group.final_points ? group.final_points.total_hundredths : group.base_points.total_hundredths;
      worth.append(el("strong", points(shown)), el("span", group.final_points ? (declared.length ? "z potwierdzonymi modyfikatorami" : "bez modyfikatorów") : "punkty bazowe", "group-help"));
      if (group.final_points && group.final_points.total_hundredths !== group.base_points.total_hundredths) worth.append(el("span", "bazowe " + points(group.base_points.total_hundredths), "group-help"));
      head.append(name, worth); card.append(head);
      if (index === 0 && n > 1) card.append(el("p", "Najwyższa wartość wśród spełniających grup", "badge"));
      if (group.final_points) group.final_points.notes.forEach(function (note) { card.append(el("p", note, "group-help")); });
      modifierBox(card, group);
      explain(card, group, index === 0);
      answer.append(card);
    });
    if (data.near_misses.length) {
      var checks = el("section", undefined, "doc-checks");
      checks.append(el("h3", "Do sprawdzenia w dokumentacji"), el("p", data.near_misses[0].explanation, "group-help"));
      var list = el("ul");
      data.near_misses.forEach(function (miss) {
        var group = groups.filter(function (g) { return g.group === miss.group && g.catalog === miss.catalog; })[0];
        var rule = group && group.trace.filter(function (e) { return e.node === miss.node; })[0], seen = rule ? observed(rule) : "";
        list.append(el("li", miss.group + (group ? " (" + group.title + ")" : "") + ": sprawdź " + FACT[miss.fact] + "." + (rule ? " Warunek grupy: " + describe(rule) + (seen ? "; " + seen : "") + "." : "")));
      });
      checks.append(list); answer.append(checks);
    }
    var rest = groups.filter(function (g) { return g.status !== "valid"; });
    if (rest.length) {
      var others = el("details", undefined, "other-candidates"); others.open = !n;
      others.append(el("summary", "Pozostali kandydaci (" + rest.length + ")"));
      [["invalid", "Nie spełniają warunków"], ["undetermined", "Nierozstrzygnięte"]].forEach(function (pair) {
        var part = rest.filter(function (g) { return g.status === pair[0]; }); if (!part.length) return;
        others.append(el("h4", pair[1] + " (" + part.length + ")"));
        var list = el("ul", undefined, "candidate-list");
        part.forEach(function (group) {
          var item = el("li", undefined, "candidate group-" + group.status); item.dataset.group = group.group;
          var line = el("p"); line.append(chip(group.group), el("span", " " + group.title));
          item.append(line, el("p", firstReason(group), "group-help")); explain(item, group, false); list.append(item);
        });
        others.append(list);
      });
      answer.append(others);
    }
    if (!groups.length) answer.append(el("p", "Żaden kod nie trafił na listy kierunkowe. To nie dowodzi braku możliwej grupy.", "group-help"));
    var about = el("details", undefined, "about-result"); about.append(el("summary", "Zakres oceny i założenia"), el("p", data.disclaimer));
    data.limitations.forEach(function (text) { about.append(el("p", text, "group-help")); }); answer.append(about);
    var tech = el("details", undefined, "result-technical"); tech.append(el("summary", "Szczegóły techniczne"));
    var prep = el("details"); prep.append(el("summary", "Przygotowanie faktów i wyłączenia"));
    data.preparation.trace.forEach(function (event) { prep.append(el("p", event.explanation, "group-help")); }); tech.append(prep);
    var skippedCount = data.not_evaluated.reduce(function (sum, entry) { return sum + Object.keys(entry.groups).reduce(function (k, catalog) { return k + entry.groups[catalog].length; }, 0); }, 0);
    var skipped = el("details"); skipped.append(el("summary", "Grupy poza oceną (" + skippedCount + ")"));
    data.not_evaluated.forEach(function (entry) { skipped.append(el("p", entry.explanation)); Object.keys(entry.groups).forEach(function (catalog) { skipped.append(el("p", catalog + ": " + entry.groups[catalog].join(", "), "group-help")); }); });
    tech.append(skipped);
    var identity = el("details"); identity.append(el("summary", "Tożsamość zestawu reguł"), el("pre", JSON.stringify({ dataset_id: data.dataset_id, database_sha256: data.database_sha256, sources: data.sources }, null, 2))); tech.append(identity);
    var raw = el("details"); raw.append(el("summary", "Pełny wynik JSON v1"), el("pre", JSON.stringify(data, null, 2))); tech.append(raw);
    answer.append(tech);
    var again = el("div", undefined, "result-actions"); button(again, "Nowy przypadek", "button button-quiet", function () { clearCase(); }); answer.append(again);
  }

  async function evaluateCase(quiet) {
    var invalid = firstInvalid();
    if (invalid) { showStep(invalid.step, true); showProblems(invalid.found); return; }
    if (radio("product-report") === "unknown") { render({ status: "unsupported_product_report" }); return; }
    var version = ++sequence; if (controller) controller.abort();
    controller = new AbortController(); status.className = ""; status.textContent = "Sprawdzamy warunki grup...";
    try {
      var data = await window.MinosRequest("/api/v1/group", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload()), cache: "no-store", credentials: "omit", signal: controller.signal });
      if (version !== sequence || current !== RESULT) return;
      if (data.schema !== "minos.grouping.v1") throw new Error("schema");
      render(data, quiet);
    } catch (error) {
      if (version !== sequence || current !== RESULT) return;
      answer.replaceChildren(); status.className = "visually-hidden";
      var message = error.code === "invalid_request" ? "Serwer nie przyjął danych przypadku. Sprawdź daty, pobyty i kody." : error.code === "busy" ? "Gruper jest chwilowo zajęty. Spróbuj ponownie za kilka sekund." : "Nie udało się ocenić przypadku w limicie czasu. To nie oznacza braku grupy.";
      answer.append(el("p", message, "group-notice"));
      var retry = el("div", undefined, "result-actions"); button(retry, "Spróbuj ponownie", "button", function () { evaluateCase(); }); answer.append(retry);
      status.textContent = message;
    }
  }

  function clearCase() {
    sequence++; if (controller) controller.abort(); answer.replaceChildren(); status.textContent = "";
    [principal].concat(secondary, procedures).filter(Boolean).forEach(function (r) { r.search.close(); });
    form.reset(); wards = []; secondary = []; procedures = []; products = []; principal = null; declared = []; reached = 0;
    ["wards", "principal", "secondary", "procedures", "products"].forEach(function (id) { byId(id).replaceChildren(); });
    hideTip(true); closePicker(false);
    addWard(); addCode("principal"); syncChoices(); showStep(0, false);
  }

  form.addEventListener("input", function (event) {
    var target = event.target;
    clearMark(target);
    if (target.classList && !(event.inputType || "").startsWith("delete")) { if (target.classList.contains("date-pl")) mask(target, [2, 2, 4]); else if (target.classList.contains("month-pl")) mask(target, [2, 4]); else if (target.id === "age" || target.id === "discharge-mode") target.value = target.value.replace(/\D/g, ""); }
    invalidate();
  });
  form.addEventListener("change", function (event) { clearMark(event.target); invalidate(); });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (current >= RESULT) return;
    if (!showProblems(problems(current))) return;
    showStep(current + 1, true);
  });
  back.addEventListener("click", function () { if (current > 0) showStep(current - 1, true); });
  document.querySelectorAll("[data-goto]").forEach(function (item) { item.addEventListener("click", function () { var target = Number(item.dataset.goto); if (target <= reached) showStep(target, true); }); });
  byId("add-ward").addEventListener("click", function () { addWard(); invalidate(); wards[wards.length - 1].department.focus(); });
  byId("add-secondary").addEventListener("click", function () { var item = addCode("secondary"); invalidate(); if (item) item.search.input.focus(); });
  byId("add-procedure").addEventListener("click", function () { var item = addCode("procedure"); invalidate(); if (item) item.search.input.focus(); });
  byId("add-product").addEventListener("click", function () { var item = addProduct(); invalidate(); if (item) item.code.focus(); });
  // Choosing a report with products opens its first row, ready to type.
  form.querySelectorAll("input[name=product-report]").forEach(function (input) { input.addEventListener("change", function () { if (input.value === "present" && !products.length) { addProduct(); syncChoices(); products[0].code.focus(); } }); });
  byId("clear-case").addEventListener("click", clearCase);
  window.addEventListener("pagehide", clearCase);
  if (window.matchMedia("(max-width: 55rem)").matches) byId("case-card-details").open = false;
  clearCase();
  // The builder stays hidden without JavaScript, where its controls cannot work.
  document.querySelector(".wizard").hidden = false;
  byId("group-availability").textContent = "Sprawdzamy dostępność zestawu reguł...";
  window.MinosRequest("/api/v1/releases", { cache: "no-store", credentials: "omit" }).then(function (data) {
    data.releases.forEach(function (release) { if (!releases[release.system] || release.origin === "czs") releases[release.system] = release.release_id; });
    var available = data.grouping && data.grouping.available;
    fields.disabled = !available;
    if (available) loadDictionaries();
    // The scope decides whether a result can be trusted, so it is shown above the
    // form and repeated with the result.
    var scope = "Ocena obejmuje zatwierdzony podzbiór reguł. Warunki spoza niego pozostają nierozstrzygnięte.";
    byId("group-availability").className = available ? "group-callout" : "group-notice";
    byId("group-availability").textContent = available ? scope : "Ten zestaw danych nie zawiera źródeł wymaganych do grupowania.";
    byId("result-scope").textContent = scope;
    byId("result-scope").hidden = !available;
  }).catch(function () { byId("group-availability").textContent = "Nie udało się pobrać zestawu reguł. Odśwież stronę, aby spróbować ponownie."; });
})();
