/* Stepped stateless encounter builder. No URL state, storage, analytics or input logging. */
(function () {
  "use strict";
  var NAMES = ["Hospitalizacja", "Pobyty na oddziałach", "Rozpoznania", "Procedury", "Raport i modyfikatory", "Wynik"];
  var RESULT = 5, CODE = /^[A-Z0-9][A-Z0-9.\-]{0,15}$/, MODIFIER = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/;
  var form = document.getElementById("group-form"), fields = document.getElementById("group-fields");
  var answer = document.getElementById("group-answer"), status = document.getElementById("group-status");
  var errorsBox = document.getElementById("step-errors"), summary = document.getElementById("case-summary");
  var back = document.getElementById("step-back"), next = document.getElementById("step-next");
  var current = 0, reached = 0, releases = {}, sequence = 0, controller = null, nextId = 0;
  var wards = [], secondary = [], procedures = [], principal = null;
  var MARK = { met: "✓", not_met: "✗", undetermined: "?" };
  var SPOKEN = { met: "spełniony: ", not_met: "niespełniony: ", undetermined: "nierozstrzygnięty: " };
  var CMP = { lt: "<", le: "≤", gt: ">", ge: "≥" };
  var CMP_KINDS = { diagnosis_presence: 1, procedure_presence: 1, procedure_count: 1, patient_age: 1, episode_duration: 1, discharge_mode: 1, all: 1, any: 1 };
  var FACT = { completed_years: "wiek", duration_days: "czas pobytu", discharge_mode: "tryb wypisu" };

  function el(tag, text, cls) { var node = document.createElement(tag); if (text !== undefined && text !== null) node.textContent = text; if (cls) node.className = cls; return node; }
  function byId(id) { return document.getElementById(id); }
  function value(id) { return byId(id).value; }
  function radio(name) { var checked = form.querySelector("input[name='" + name + "']:checked"); return checked ? checked.value : ""; }
  function plural(n, one, few, many) { var tens = n % 100, units = n % 10; return n === 1 ? one : units >= 2 && units <= 4 && (tens < 12 || tens > 14) ? few : many; }
  function day(iso) { var p = (iso || "").split("-"); return p.length === 3 ? p[2] + "." + p[1] + "." + p[0] : ""; }
  function points(n) { return n === null || n === undefined ? "nieustalone" : (n / 100).toLocaleString("pl-PL", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " pkt"; }
  function field(parent, title, type, required, hint) {
    var label = el("label", title), input = el("input"); input.type = type; input.required = !!required;
    if (hint) label.append(el("span", hint, "field-hint"));
    label.append(input); parent.append(label); return input;
  }
  function button(parent, text, cls, callback) { var node = el("button", text, cls); node.type = "button"; node.addEventListener("click", callback); parent.append(node); return node; }

  /* Any edit makes an earlier answer obsolete; an in-flight response is refused by sequence. */
  function invalidate() { sequence++; if (controller) controller.abort(); answer.replaceChildren(); status.textContent = ""; renderSummary(); }

  function showStep(index, focus) {
    current = index; reached = Math.max(reached, index);
    form.querySelectorAll(".wizard-step").forEach(function (section) { section.hidden = Number(section.dataset.step) !== index; });
    document.querySelectorAll("[data-goto]").forEach(function (item) {
      var target = Number(item.dataset.goto), li = item.parentElement;
      item.disabled = target > reached; li.className = target === index ? "is-current" : target <= reached ? "is-done" : "";
      if (target === index) item.setAttribute("aria-current", "step"); else item.removeAttribute("aria-current");
    });
    byId("wizard-progress").textContent = "Krok " + (index + 1) + " z " + NAMES.length + ": " + NAMES[index];
    back.hidden = index === 0;
    next.hidden = index === RESULT;
    next.textContent = index === RESULT - 1 ? "Sprawdź grupowanie" : "Dalej: " + NAMES[index + 1];
    clearErrors();
    if (index === 1) prefillWard();
    form.classList.toggle("is-result", index === RESULT);
    renderSummary();
    if (focus) { var wizard = document.querySelector(".wizard"); byId("step-" + index + "-title").focus({ preventScroll: true }); if (wizard.getBoundingClientRect().top < 0) wizard.scrollIntoView({ block: "start" }); }
    if (index === RESULT) evaluateCase();
  }

  /* Validation: problems are listed above the step and point to the field to fix. */
  function clearErrors() { errorsBox.hidden = true; errorsBox.replaceChildren(); form.querySelectorAll("[aria-invalid]").forEach(function (input) { input.removeAttribute("aria-invalid"); }); }
  function problems(index) {
    var found = [];
    function add(input, message) { found.push({ input: input, message: message }); }
    var admitted = value("admitted"), discharged = value("discharged");
    function inEpisode(date) { return !admitted || !discharged || (date >= admitted && date <= discharged); }
    if (index === 0) {
      if (!admitted) add(byId("admitted"), "Podaj datę przyjęcia.");
      if (!discharged) add(byId("discharged"), "Podaj datę wypisu.");
      else if (admitted && discharged < admitted) add(byId("discharged"), "Wypis nie może być wcześniejszy niż przyjęcie.");
      if (!byId("age").checkValidity() || value("age") === "") add(byId("age"), "Podaj wiek w ukończonych latach (0-200).");
      if (!byId("discharge-mode").checkValidity()) add(byId("discharge-mode"), "Kod trybu wypisu to liczba od 1 do 99 albo puste pole.");
      var service = value("service-date");
      if (!service) add(byId("service-date"), "Podaj dzień świadczenia albo użyj przycisku dnia wypisu.");
      else if (!inEpisode(service)) add(byId("service-date"), "Dzień świadczenia musi mieścić się między przyjęciem a wypisem.");
      if (!/^[0-9]{4}-(0[1-9]|1[0-2])$/.test(value("reporting-month"))) add(byId("reporting-month"), "Podaj miesiąc sprawozdawczy w formacie RRRR-MM.");
    }
    if (index === 1) {
      if (!wards.length) add(byId("add-ward"), "Dodaj co najmniej jeden pobyt.");
      wards.forEach(function (ward, i) {
        var name = "Pobyt " + (i + 1) + ": ";
        if (!/^[0-9]{4}$/.test(ward.department.value)) add(ward.department, name + "podaj kod specjalności z 4 cyfr.");
        if (!ward.start.value || !ward.end.value) add(ward.start.value ? ward.end : ward.start, name + "podaj obie daty.");
        else if (ward.end.value < ward.start.value) add(ward.end, name + "wypis nie może być wcześniejszy niż przyjęcie.");
        else if (!inEpisode(ward.start.value) || !inEpisode(ward.end.value)) add(ward.start, name + "daty muszą mieścić się w hospitalizacji.");
      });
    }
    if (index === 2) {
      if (!CODE.test(code(principal))) add(principal.search.input, "Rozpoznanie zasadnicze: wybierz podpowiedź albo wpisz dokładny kod ICD-10, np. K40.9.");
      secondary.forEach(function (item, i) { if (!CODE.test(code(item))) add(item.search.input, "Rozpoznanie współistniejące " + (i + 1) + ": uzupełnij kod albo usuń wiersz."); });
    }
    if (index === 3) {
      procedures.forEach(function (item, i) {
        var name = "Procedura " + (i + 1) + ": ";
        if (!CODE.test(code(item))) add(item.search.input, name + "uzupełnij kod ICD-9 albo usuń wiersz.");
        if (item.date.value && !inEpisode(item.date.value)) add(item.date, name + "data musi mieścić się w hospitalizacji.");
      });
    }
    if (index === 4) {
      var symbols = modifiers();
      if (symbols.length > 64 || symbols.some(function (s) { return !MODIFIER.test(s); })) add(byId("modifiers"), "Symbole modyfikatorów: litery, cyfry, kropki i podkreślenia, do 64 pozycji.");
      else if (new Set(symbols).size !== symbols.length) add(byId("modifiers"), "Każdy modyfikator podaj tylko raz.");
    }
    return found;
  }
  function showProblems(found) {
    clearErrors();
    if (!found.length) return true;
    errorsBox.append(el("p", "Popraw przed przejściem dalej:"));
    var list = el("ul");
    found.forEach(function (item) {
      item.input.setAttribute("aria-invalid", "true");
      var entry = el("li"); button(entry, item.message, "link-button", function () { item.input.focus(); }); list.append(entry);
    });
    errorsBox.append(list); errorsBox.hidden = false; found[0].input.focus();
    return false;
  }
  function firstInvalid() { for (var i = 0; i < RESULT; i++) { var found = problems(i); if (found.length) return { step: i, found: found }; } return null; }

  /* Stays, diagnoses and procedures keep every occurrence; attribution stays explicit. */
  function prefillWard() {
    var first = wards[0];
    if (first && !first.start.value && !first.end.value && value("admitted") && value("discharged")) {
      first.start.value = value("admitted"); first.end.value = value("discharged"); byId("ward-prefill").hidden = false; renderSummary();
    }
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
    var grid = el("div", undefined, "field-grid field-grid-3"); row.append(grid);
    var department = field(grid, "Specjalność oddziału", "text", true, "4 cyfry, np. 4500"); department.maxLength = 4; department.inputMode = "numeric"; department.pattern = "[0-9]{4}";
    var start = field(grid, "Przyjęcie na oddział", "date", true), end = field(grid, "Wypis z oddziału", "date", true);
    var ward = { id: "ward" + (++nextId), row: row, title: title, department: department, start: start, end: end };
    button(head, "Usuń", "link-button", function () {
      if (wards.length === 1) return;
      wards.splice(wards.indexOf(ward), 1); row.remove(); wards.forEach(function (w, i) { w.title.textContent = "Pobyt " + (i + 1); }); refreshAttributions(); invalidate();
    });
    department.addEventListener("input", refreshAttributions);
    wards.push(ward); byId("wards").append(row); refreshAttributions();
  }
  function codeSearch(parent, system, title) {
    var box = el("div", undefined, "search-box"), input = field(box, title, "text", true), list = el("ul", undefined, "suggestions");
    var hint = el("p", "Wpisz kod lub opis.", "field-hint"); hint.setAttribute("aria-live", "polite");
    var id = "code-" + (++nextId), active = -1, options = [], request = null, delay = 0, generation = 0, state = { label: "" };
    input.maxLength = 120; input.spellcheck = false; input.setAttribute("role", "combobox"); input.setAttribute("aria-autocomplete", "list"); input.setAttribute("aria-expanded", "false"); input.setAttribute("aria-controls", id); list.id = id; list.setAttribute("role", "listbox"); list.hidden = true;
    box.append(list, hint); parent.append(box);
    function close() { generation++; clearTimeout(delay); if (request) request.abort(); list.hidden = true; list.replaceChildren(); options = []; active = -1; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); }
    function choose(hit) { input.value = hit.code; state.label = hit.label; hint.textContent = hit.label; close(); invalidate(); }
    input.addEventListener("input", function () {
      close(); state.label = ""; hint.textContent = "Wpisz kod lub opis.";
      var query = input.value.trim(), release = releases[system], version = generation;
      if (!release || !query) return;
      delay = setTimeout(function () {
        request = new AbortController();
        window.MinosRequest("/api/v1/search", { method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store", credentials: "omit", signal: request.signal,
          body: JSON.stringify({ system: system, release_id: release, query: query, limit: 5 }) }).then(function (data) {
          if (generation !== version || !input.isConnected) return;
          options = data.results || []; list.replaceChildren();
          options.forEach(function (hit, index) { var item = el("li", undefined, "suggestion"); item.id = id + "-" + index; item.setAttribute("role", "option"); item.setAttribute("aria-selected", "false"); item.append(el("span", hit.code, "code-chip"), el("span", hit.label, "suggestion-label")); item.addEventListener("pointerdown", function (event) { event.preventDefault(); }); item.addEventListener("click", function () { choose(hit); }); list.append(item); });
          list.hidden = !options.length; input.setAttribute("aria-expanded", String(!!options.length));
          hint.textContent = options.length ? "Strzałki i Enter wybierają podpowiedź." : "Brak podpowiedzi. Możesz wpisać dokładny kod z dokumentacji.";
        }).catch(function () { if (generation === version) hint.textContent = "Podpowiedzi są chwilowo niedostępne. Możesz wpisać dokładny kod."; });
      }, 220);
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { close(); return; }
      if (!options.length) return;
      if (event.key === "Enter" && active >= 0) { event.preventDefault(); choose(options[active]); }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); active = (active + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length; Array.from(list.children).forEach(function (node, i) { node.setAttribute("aria-selected", String(i === active)); }); input.setAttribute("aria-activedescendant", list.children[active].id); }
    });
    input.addEventListener("blur", close);
    return { input: input, close: close, state: state };
  }
  function addCode(kind) {
    var diagnosis = kind !== "procedure", array = kind === "secondary" ? secondary : procedures;
    if ((kind === "secondary" && array.length >= 100) || (kind === "procedure" && array.length >= 1000)) return null;
    var row = el("div", undefined, "fact-row"), head = el("div", undefined, "fact-head");
    var label = kind === "principal" ? "Rozpoznanie zasadnicze (ICD-10)" : diagnosis ? "Rozpoznanie współistniejące (ICD-10)" : "Procedura (ICD-9)";
    row.append(head);
    var grid = el("div", undefined, "field-grid " + (diagnosis ? "fact-grid" : "fact-grid-3")); row.append(grid);
    var search = codeSearch(grid, diagnosis ? "icd10-pl" : "icd9-pl", label);
    var where = el("label", "Poziom zgłoszenia"), select = el("select"); select.dataset.diagnosis = diagnosis ? "yes" : "no"; where.append(select); grid.append(where);
    var item = { row: row, search: search, attribution: select, date: diagnosis ? null : field(grid, "Data wykonania", "date", false, "opcjonalnie") };
    if (kind === "principal") principal = item;
    else { array.push(item); button(head, "Usuń", "link-button", function () { search.close(); array.splice(array.indexOf(item), 1); row.remove(); invalidate(); }); }
    byId(kind === "procedure" ? "procedures" : kind).append(row); refreshAttributions();
    return item;
  }
  function code(item) { return item.search.input.value.trim().toUpperCase(); }
  function codeFact(item, diagnosis) {
    var result = { code: code(item) }, where = item.attribution.value;
    if (where === "episode" && diagnosis) result.hospitalization_level = true;
    else if (where) result.stay_id = where;
    if (item.date && item.date.value) result.performed_on = item.date.value;
    return result;
  }
  function modifiers() { return value("modifiers").split(/[,\s]+/).filter(Boolean); }
  function payload() {
    var data = { admitted_on: value("admitted"), discharged_on: value("discharged"), age_completed_years: Number(value("age")), hospitalization_type: radio("hospitalization"),
      principal_diagnosis: codeFact(principal, true), secondary_diagnoses: secondary.map(function (r) { return codeFact(r, true); }), procedures: procedures.map(function (r) { return codeFact(r, false); }),
      stays: wards.map(function (w) { return { identifier: w.id, department: w.department.value, admitted_on: w.start.value, discharged_on: w.end.value }; }),
      product_reporting: radio("product-report") === "empty" ? "complete" : "unknown", settlement_products: [] };
    if (value("discharge-mode")) data.discharge_mode = Number(value("discharge-mode"));
    return { schema_version: 1, encounter: data, service_date: value("service-date"), reporting_month: value("reporting-month") + "-01", declared_modifiers: modifiers() };
  }

  /* Case card: what will be evaluated, with a way back to each step. */
  function renderSummary() {
    var rows = [];
    var years = value("age") === "" ? "" : value("age") + " " + plural(Number(value("age")), "rok", "lata", "lat");
    var stay = value("admitted") && value("discharged") ? day(value("admitted")) + " - " + day(value("discharged")) : "";
    var mode = { hospitalization: "hospitalizacja", planned: "hospitalizacja planowa", one_day: "leczenie jednego dnia" }[radio("hospitalization")];
    rows.push([0, [stay, years, mode].filter(Boolean).join(", "), value("service-date") ? "Dzień świadczenia " + day(value("service-date")) : ""]);
    rows.push([1, wards.filter(function (w) { return w.department.value; }).map(function (w) { return w.department.value + (w.start.value ? ": " + day(w.start.value) + " - " + day(w.end.value) : ""); }).join("; ")]);
    var main = principal && code(principal) ? code(principal) + (principal.search.state.label ? " " + principal.search.state.label : "") : "";
    var others = secondary.map(code).filter(Boolean);
    rows.push([2, main, others.length ? "Współistniejące: " + others.join(", ") : ""]);
    var done = procedures.map(code).filter(Boolean);
    rows.push([3, done.length ? done.join(", ") : reached > 3 ? "Bez procedur" : ""]);
    var report = { empty: "Kompletny raport bez produktów", unknown: "Raport niepełny lub nieznany", present: "Raport z produktami" }[radio("product-report")];
    rows.push([4, reached > 4 ? report : "", modifiers().length ? "Modyfikatory: " + modifiers().join(", ") : ""]);
    summary.replaceChildren();
    rows.forEach(function (row) {
      var term = el("dt", NAMES[row[0]]), detail = el("dd");
      detail.append(el("span", row[1] || "Nie uzupełniono", row[1] ? "" : "is-empty"));
      if (row[2]) detail.append(el("span", row[2], "case-extra"));
      if (row[0] <= reached && row[0] !== current) { var change = button(detail, "Zmień", "link-button", function () { showStep(row[0], true); }); change.setAttribute("aria-label", "Zmień: " + NAMES[row[0]]); }
      summary.append(term, detail);
    });
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
    if (!CMP_KINDS[event.kind]) return "";
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
    if (roots.length > 1 && group.roots.length > 1) {
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
    if (group.final_points) group.final_points.modifiers.forEach(function (m) {
      var name = modifierName(m);
      var text = m.outcome === "applied" ? "Zastosowano: " + name + "." : m.outcome === "possible" ? "Możliwe, niezadeklarowane: " + name + ". Wartość +" + points(m.would_add_hundredths) + ", jeśli dokumentacja i umowa potwierdzają warunki." : name + ": " + m.explanation;
      box.append(el("p", text, "group-help"));
    });
    parent.append(box);
  }
  /* Legal supplements carry an internal symbol; coders know them by paragraph. */
  function modifierName(m) {
    var section = /section\s+(\d+)\((\w+)\)/.exec(m.source_citation || "");
    return m.symbol.indexOf("manual-") === 0 && section ? "dopłata z § " + section[1] + " ust. " + section[2] : "modyfikator " + m.symbol;
  }
  function firstReason(group) {
    var leaf = group.trace.filter(function (e) { return e.kind !== "all" && e.kind !== "any"; });
    var failed = leaf.filter(function (e) { return e.verdict === (group.status === "invalid" ? "not_met" : "undetermined"); })[0];
    if (failed) { var detail = observed(failed); return describe(failed) + (detail ? " (" + detail + ")" : ""); }
    var reasons = group.reasons.map(function (reason, i) { return [reason, group.explanations[i]]; }).filter(function (pair) { return pair[0] !== "routing_not_restricted" && pair[0] !== "conservative_allowed"; });
    return reasons.length ? reasons[0][1] : "Część warunków nie ma obsługiwanej interpretacji.";
  }

  function render(data) {
    answer.replaceChildren();
    if (data.status !== "evaluated") { answer.append(el("p", "Ten raport produktów nie jest jeszcze obsługiwany, więc kwalifikacja nie została oceniona. Wybierz potwierdzony kompletny raport bez produktów, jeśli dokumentacja na to pozwala.", "group-notice")); status.textContent = "Brak oceny dla tego raportu produktów."; return; }
    var order = data.ranking.map(function (r) { return r.catalog + "/" + r.group; });
    var groups = data.groups.slice(), valid = order.map(function (key) { return groups.filter(function (g) { return g.catalog + "/" + g.group === key; })[0]; }).filter(Boolean);
    var n = valid.length;
    var verdict = el("div", undefined, "verdict");
    verdict.append(el("h3", n ? "Obsługiwane warunki " + plural(n, "spełnia", "spełniają", "spełnia") + " " + n + " " + plural(n, "grupa", "grupy", "grup") : "Żadna grupa nie spełnia obsługiwanych warunków", "verdict-title"));
    verdict.append(el("p", n ? "Wybór grupy należy do świadczeniodawcy. Kolejność według " + (data.tariff_status === "bound" ? "punktów z zadeklarowanymi modyfikatorami." : "punktów bazowych; brak powiązania taryfy dla tej daty.") : "Sprawdź pozostałych kandydatów i nierozstrzygnięte warunki poniżej. Brak grupy w tej ocenie nie dowodzi braku możliwej grupy.", "group-help"));
    status.className = "visually-hidden";
    status.textContent = n ? "Gotowe: " + n + " " + plural(n, "grupa spełnia", "grupy spełniają", "grup spełnia") + " obsługiwane warunki." : "Gotowe: żadna grupa nie spełnia obsługiwanych warunków.";
    answer.append(verdict);
    valid.forEach(function (group, index) {
      var card = el("article", undefined, "verdict-group" + (index === 0 ? " is-top" : "")); card.dataset.group = group.group; card.classList.add("group-valid");
      var head = el("div", undefined, "verdict-head"), name = el("div", undefined, "verdict-name"), value = el("div", undefined, "verdict-points");
      name.append(el("span", group.group, "group-code"), el("span", group.title, "group-title"), el("span", "Katalog " + group.catalog + (group.product_code ? ", produkt " + group.product_code : ""), "group-help"));
      var shown = group.final_points ? group.final_points.total_hundredths : group.base_points.total_hundredths;
      value.append(el("strong", points(shown)), el("span", group.final_points ? "z zadeklarowanymi modyfikatorami" : "punkty bazowe", "group-help"));
      if (group.final_points && group.final_points.total_hundredths !== group.base_points.total_hundredths) value.append(el("span", "bazowe " + points(group.base_points.total_hundredths), "group-help"));
      head.append(name, value); card.append(head);
      if (index === 0 && n > 1) card.append(el("p", "Najwyższa wartość wśród spełniających grup", "badge"));
      if (group.final_points) group.final_points.notes.forEach(function (note) { card.append(el("p", note, "group-help")); });
      if (group.unapplied_declared_modifiers.length) card.append(el("p", "Niezastosowane deklaracje: " + group.unapplied_declared_modifiers.join(", ") + ".", "group-help"));
      explain(card, group, index === 0);
      answer.append(card);
    });
    if (data.near_misses.length) {
      var checks = el("section", undefined, "doc-checks");
      checks.append(el("h3", "Do sprawdzenia w dokumentacji"), el("p", data.near_misses[0].explanation, "group-help"));
      var list = el("ul");
      data.near_misses.forEach(function (miss) {
        var group = groups.filter(function (g) { return g.group === miss.group && g.catalog === miss.catalog; })[0];
        var rule = group && group.trace.filter(function (e) { return e.node === miss.node; })[0];
        var seen = rule ? observed(rule) : "";
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
          var line = el("p"); line.append(el("span", group.group, "code-chip"), el("span", " " + group.title));
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

  async function evaluateCase() {
    var invalid = firstInvalid();
    if (invalid) { showStep(invalid.step, true); showProblems(invalid.found); return; }
    if (radio("product-report") !== "empty") { render({ status: "unsupported_product_report" }); return; }
    var version = ++sequence; if (controller) controller.abort();
    controller = new AbortController(); answer.replaceChildren(); status.className = ""; status.textContent = "Sprawdzamy warunki grup...";
    try {
      var data = await window.MinosRequest("/api/v1/group", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload()), cache: "no-store", credentials: "omit", signal: controller.signal });
      if (version !== sequence || current !== RESULT) return;
      if (data.schema !== "minos.grouping.v1") throw new Error("schema");
      render(data);
    } catch (error) {
      if (version !== sequence || current !== RESULT) return;
      answer.replaceChildren(); status.className = "visually-hidden";
      var message = error.code === "invalid_request" ? "Serwer nie przyjął danych przypadku. Sprawdź daty, pobyty, kody i symbole modyfikatorów." : error.code === "busy" ? "Gruper jest chwilowo zajęty. Spróbuj ponownie za kilka sekund." : "Nie udało się ocenić przypadku w limicie czasu. To nie oznacza braku grupy.";
      answer.append(el("p", message, "group-notice"));
      var retry = el("div", undefined, "result-actions"); button(retry, "Spróbuj ponownie", "button", function () { evaluateCase(); }); answer.append(retry);
      status.textContent = message;
    }
  }

  function clearCase() {
    invalidate(); [principal].concat(secondary, procedures).filter(Boolean).forEach(function (r) { r.search.close(); });
    form.reset(); wards = []; secondary = []; procedures = []; principal = null; reached = 0;
    ["wards", "principal", "secondary", "procedures"].forEach(function (id) { byId(id).replaceChildren(); });
    byId("ward-prefill").hidden = true; addWard(); addCode("principal"); showStep(0, false); renderSummary();
  }

  form.addEventListener("input", invalidate);
  form.addEventListener("change", invalidate);
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
  byId("fill-service-date").addEventListener("click", function () { if (value("discharged")) { byId("service-date").value = value("discharged"); invalidate(); } else byId("discharged").focus(); });
  byId("fill-reporting-month").addEventListener("click", function () { if (value("discharged")) { byId("reporting-month").value = value("discharged").slice(0, 7); invalidate(); } else byId("discharged").focus(); });
  byId("clear-case").addEventListener("click", clearCase);
  window.addEventListener("pagehide", clearCase);
  if (window.matchMedia("(max-width: 55rem)").matches) byId("case-card-details").open = false;
  clearCase();
  window.MinosRequest("/api/v1/releases", { cache: "no-store", credentials: "omit" }).then(function (data) {
    data.releases.forEach(function (release) { if (!releases[release.system] || release.origin === "czs") releases[release.system] = release.release_id; });
    var available = data.grouping && data.grouping.available;
    fields.disabled = !available;
    byId("group-availability").className = available ? "group-help" : "group-notice";
    byId("group-availability").textContent = available ? "Ocena obejmuje zatwierdzony podzbiór reguł. Warunki spoza niego pozostają nierozstrzygnięte." : "Ten zestaw danych nie zawiera źródeł wymaganych do grupowania.";
  }).catch(function () { byId("group-availability").textContent = "Nie udało się pobrać zestawu reguł. Odśwież stronę, aby spróbować ponownie."; });
})();
