/* Stateless encounter builder. No URL state, storage, analytics or input logging. */
(function () {
  "use strict";
  var form = document.getElementById("group-form"), answer = document.getElementById("group-answer");
  var status = document.getElementById("group-status"), releases = {}, sequence = 0, timer = 0, controller = null, nextId = 0;
  var wards = [], secondary = [], procedures = [], principal;
  var labels = { valid: "Spełnia obsługiwane warunki", invalid: "Nie spełnia warunków", undetermined: "Nierozstrzygnięte", met: "Spełniony", not_met: "Niespełniony" };
  function el(tag, text, cls) { var node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; }
  function value(id) { return document.getElementById(id).value; }
  function field(parent, title, type, required) { var label = el("label", title), input = el("input"); input.type = type; input.required = !!required; label.append(input); parent.append(label); return input; }
  function button(parent, text, callback) { var node = el("button", text, "button button-quiet"); node.type = "button"; node.addEventListener("click", callback); parent.append(node); return node; }
  function invalidate() { sequence++; clearTimeout(timer); if (controller) controller.abort(); answer.replaceChildren(); status.textContent = "Dane zmienione. Uzupełnij wymagane pola, aby przeliczyć."; }
  function changed() { invalidate(); timer = setTimeout(calculate, 450); }
  function attribution(parent, diagnosis) {
    var label = el("label", "Poziom zgłoszenia"), select = el("select"); select.dataset.diagnosis = diagnosis ? "yes" : "no";
    label.append(select); parent.append(label); return select;
  }
  function refreshAttributions() {
    form.querySelectorAll("select[data-diagnosis]").forEach(function (select) {
      var old = select.value; select.replaceChildren(new Option("Nieznany", ""));
      if (select.dataset.diagnosis === "yes") select.append(new Option("Cała hospitalizacja", "episode"));
      wards.forEach(function (ward, index) { select.append(new Option("Pobyt " + (index + 1), ward.id)); });
      select.value = old; if (select.selectedIndex < 0) select.value = "";
    });
  }
  function addWard() {
    if (wards.length >= 100) return;
    var row = el("div", undefined, "group-row"), title = el("h3", "Pobyt " + (wards.length + 1)); row.append(title);
    var grid = el("div", undefined, "group-grid"); row.append(grid);
    var department = field(grid, "Specjalność (4 cyfry)", "text", true); department.pattern = "[0-9]{4}"; department.maxLength = 4; department.inputMode = "numeric";
    var start = field(grid, "Przyjęcie na oddział", "date", true), end = field(grid, "Wypis z oddziału", "date", true);
    var ward = { id: "ward" + (++nextId), row: row, title: title, department: department, start: start, end: end };
    button(row, "Usuń pobyt", function () { if (wards.length === 1) { status.textContent = "Wymagany jest co najmniej jeden pobyt."; return; } wards.splice(wards.indexOf(ward), 1); row.remove(); wards.forEach(function (w, i) { w.title.textContent = "Pobyt " + (i + 1); }); refreshAttributions(); changed(); });
    wards.push(ward); document.getElementById("wards").append(row); refreshAttributions();
  }
  function codeSearch(parent, system, title) {
    var box = el("div", undefined, "search-box"), input = field(box, title, "text", true), list = el("ul", undefined, "suggestions");
    var hint = el("p", "Wpisz kod lub wybierz podpowiedź.", "group-help"); hint.setAttribute("role", "status");
    var id = "code-" + (++nextId), active = -1, options = [], request = null, delay = 0, generation = 0;
    input.maxLength = 120; input.spellcheck = false; input.setAttribute("role", "combobox"); input.setAttribute("aria-autocomplete", "list"); input.setAttribute("aria-expanded", "false"); input.setAttribute("aria-controls", id); list.id = id; list.setAttribute("role", "listbox"); list.hidden = true;
    box.append(list, hint); parent.append(box);
    function close() { generation++; clearTimeout(delay); if (request) request.abort(); list.hidden = true; list.replaceChildren(); options = []; active = -1; input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); }
    function choose(hit) { input.value = hit.code; hint.textContent = hit.code + " - " + hit.label; close(); changed(); }
    input.addEventListener("input", function () {
      close(); hint.textContent = "Wpisz kod lub wybierz podpowiedź.";
      var query = input.value.trim(), release = releases[system], version = generation;
      if (!release || !query) return;
      delay = setTimeout(function () {
        request = new AbortController();
        window.MinosRequest("/api/v1/search", { method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store", credentials: "omit", signal: request.signal,
          body: JSON.stringify({ system: system, release_id: release, query: query, limit: 4 }) }).then(function (data) {
          if (generation !== version || !input.isConnected) return;
          options = data.results || []; list.replaceChildren();
          options.forEach(function (hit, index) { var item = el("li", undefined, "suggestion"); item.id = id + "-" + index; item.setAttribute("role", "option"); item.setAttribute("aria-selected", "false"); item.append(el("span", hit.code, "code-chip"), el("span", hit.label, "suggestion-label")); item.addEventListener("pointerdown", function (event) { event.preventDefault(); }); item.addEventListener("click", function () { choose(hit); }); list.append(item); });
          list.hidden = !options.length; input.setAttribute("aria-expanded", String(!!options.length)); hint.textContent = options.length ? "Wybierz strzałkami i Enterem lub wpisz dokładny kod." : "Brak podpowiedzi. Możesz podać dokładny kod z dokumentacji.";
        }).catch(function () { if (generation === version) hint.textContent = "Podpowiedzi niedostępne. Możesz podać dokładny kod."; });
      }, 220);
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { close(); return; }
      if (!options.length) return;
      if (event.key === "Enter" && active >= 0) { event.preventDefault(); choose(options[active]); }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); active = (active + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length; Array.from(list.children).forEach(function (node, i) { node.setAttribute("aria-selected", String(i === active)); }); input.setAttribute("aria-activedescendant", list.children[active].id); }
    });
    input.addEventListener("blur", close);
    return { input: input, close: close };
  }
  function addCode(kind) {
    var diagnosis = kind !== "procedure", array = kind === "secondary" ? secondary : procedures;
    if ((kind === "secondary" && array.length >= 100) || (kind === "procedure" && array.length >= 1000)) return;
    var row = el("div", undefined, "group-row"), search = codeSearch(row, diagnosis ? "icd10-pl" : "icd9-pl", kind === "principal" ? "Główne rozpoznanie ICD-10" : diagnosis ? "Współistniejące ICD-10" : "Procedura ICD-9");
    var grid = el("div", undefined, "group-grid"); row.append(grid);
    var item = { row: row, search: search, attribution: attribution(grid, diagnosis), date: diagnosis ? null : field(grid, "Data wykonania (opcjonalnie)", "date", false) };
    if (kind === "principal") principal = item;
    else { array.push(item); button(row, "Usuń", function () { search.close(); array.splice(array.indexOf(item), 1); row.remove(); changed(); }); }
    document.getElementById(kind === "procedure" ? "procedures" : kind).append(row); refreshAttributions();
  }
  function codeFact(item, diagnosis) {
    var code = item.search.input.value.trim().toUpperCase();
    if (!/^[A-Z0-9][A-Z0-9.\-]{0,15}$/.test(code)) throw new Error("Wybierz podpowiedź albo wpisz dokładny kod zamiast opisu.");
    var result = { code: code }, where = item.attribution.value;
    if (where === "episode" && diagnosis) result.hospitalization_level = true;
    else if (where) result.stay_id = where;
    if (item.date && item.date.value) result.performed_on = item.date.value;
    return result;
  }
  function payload() {
    var data = { admitted_on: value("admitted"), discharged_on: value("discharged"), age_completed_years: Number(value("age")), hospitalization_type: value("hospitalization"),
      principal_diagnosis: codeFact(principal, true), secondary_diagnoses: secondary.map(function (r) { return codeFact(r, true); }), procedures: procedures.map(function (r) { return codeFact(r, false); }),
      stays: wards.map(function (w) { return { identifier: w.id, department: w.department.value, admitted_on: w.start.value, discharged_on: w.end.value }; }),
      product_reporting: value("product-report") === "empty" ? "complete" : "unknown", settlement_products: [] };
    if (value("discharge-mode")) data.discharge_mode = Number(value("discharge-mode"));
    return { schema_version: 1, encounter: data, service_date: value("service-date"), reporting_month: value("reporting-month") + "-01", declared_modifiers: value("modifiers").split(/[,\s]+/).filter(Boolean) };
  }
  function details(parent, title) { var box = el("details"); box.append(el("summary", title)); parent.append(box); return box; }
  function points(n) { return n === null || n === undefined ? "nieustalone" : (n / 100).toLocaleString("pl-PL", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " pkt"; }
  function pretty(value) { return JSON.stringify(value, null, 2); }
  function render(data) {
    answer.replaceChildren(); answer.append(el("p", data.disclaimer, "group-notice"));
    var limits = details(answer, "Zakres oceny i przyjęte założenia"); (data.limitations || []).forEach(function (text) { limits.append(el("p", text)); });
    if (data.status !== "evaluated") { limits.open = true; status.textContent = "Brak oceny: ten raport produktów nie jest obsługiwany."; return; }
    var ranked = data.ranking.map(function (r) { return r.catalog + "/" + r.group; });
    status.textContent = "Kwalifikujące grupy: " + ranked.length + ". Wybór należy do świadczeniodawcy.";
    answer.append(el("p", data.tariff_status === "bound" ? "Kolejność według punktów z zadeklarowanymi modyfikatorami. Pozostałe modyfikatory nie zostały zastosowane." : "Brak powiązania taryfy dla tej daty. Pokazujemy wyłącznie punkty bazowe.", "group-help"));
    var groups = data.groups.slice().sort(function (a, b) { var ai = ranked.indexOf(a.catalog + "/" + a.group), bi = ranked.indexOf(b.catalog + "/" + b.group); return (ai < 0 ? 99999 : ai) - (bi < 0 ? 99999 : bi); });
    var others = el("details", undefined, "group-other-candidates"); others.append(el("summary", "Pozostali kandydaci: " + groups.filter(function (g) { return g.status !== "valid"; }).length));
    groups.forEach(function (group) {
      var card = el("article", undefined, "group-card group-" + group.status); card.dataset.group = group.group;
      card.append(el("h3", group.group + " / " + group.catalog + " - " + group.title), el("p", labels[group.status], "group-verdict"));
      if (group.status === "valid") card.append(el("p", "Punkty bazowe: " + points(group.base_points.total_hundredths)));
      if (group.final_points) { card.append(el("p", "Z zadeklarowanymi modyfikatorami: " + points(group.final_points.total_hundredths))); group.final_points.notes.forEach(function (note) { card.append(el("p", note, "group-notice")); }); }
      if (group.unapplied_declared_modifiers.length) card.append(el("p", "Niezastosowane deklaracje: " + group.unapplied_declared_modifiers.join(", ") + ". Sprawdź warunki i ślad modyfikatorów.", "group-help"));
      group.explanations.forEach(function (text) { card.append(el("p", text, "group-help")); });
      var trace = details(card, "Dlaczego? Warunki i źródła (" + group.trace.length + ")");
      group.trace.forEach(function (event) {
        var rule = el("div", undefined, "group-rule"); rule.append(el("strong", "#" + event.node + " / " + (labels[event.verdict] || "Nierozstrzygnięty")), el("p", event.explanation));
        if (event.matched_codes.length) rule.append(el("p", "Pasujące kody: " + event.matched_codes.join(", ")));
        rule.append(el("p", "Załącznik 9, wiersze: " + event.source_rows.join(", "), "group-help"));
        var parameters = details(rule, "Parametry reguły i porównane wartości"); parameters.append(el("pre", pretty({ rule: event.parameters, observed: event.observed })));
        trace.append(rule);
      });
      trace.append(el("p", "Katalog, wiersz: " + group.catalog_row + ". " + group.base_points.explanation));
      if (group.final_points) group.final_points.modifiers.forEach(function (m) { trace.append(el("p", m.symbol + ": " + m.explanation + " Źródło: " + m.source_citation)); });
      (data.near_misses || []).filter(function (m) { return m.group === group.group && m.catalog === group.catalog; }).forEach(function (m) { card.append(el("p", "Sprawdzenie dokumentacji, warunek #" + m.node + ": " + m.explanation, "group-notice")); });
      if (group.status === "valid") answer.append(card); else others.append(card);
    });
    if (groups.some(function (g) { return g.status !== "valid"; })) { others.open = !ranked.length; answer.append(others); }
    if (!groups.length) answer.append(el("p", "Nie wyznaczono kandydatów z list kierunkowych. To nie dowodzi braku możliwej grupy."));
    var prep = details(answer, "Przygotowanie faktów i reguły wyłączeń");
    data.preparation.trace.forEach(function (event) { prep.append(el("p", event.phase + " #" + event.ordinal + ": " + event.explanation)); });
    var skipped = details(answer, "Grupy poza oceną (" + data.not_evaluated.length + ")");
    data.not_evaluated.forEach(function (group) { skipped.append(el("p", group.catalog + "/" + group.group + ": " + group.explanation)); });
    var sources = details(answer, "Tożsamość zestawu reguł"); sources.append(el("pre", pretty({ dataset_id: data.dataset_id, database_sha256: data.database_sha256, sources: data.sources })));
    var raw = details(answer, "Pełny wynik JSON v1"); raw.append(el("pre", pretty(data)));
  }
  async function calculate() {
    var version = sequence;
    if (!form.checkValidity()) return;
    if (value("product-report") !== "empty") { status.textContent = "Brak oceny: wymagany jest potwierdzony kompletny raport bez produktów. Wyłączenia produktów nie są jeszcze obsługiwane."; return; }
    var body; try { body = payload(); } catch (error) { status.textContent = error.message; return; }
    controller = new AbortController(); status.textContent = "Oceniamy warunki...";
    try {
      var data = await window.MinosRequest("/api/v1/group", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), cache: "no-store", credentials: "omit", signal: controller.signal });
      if (version !== sequence) return;
      if (data.schema !== "minos.grouping.v1") throw new Error("schema"); render(data);
    } catch (error) {
      if (version !== sequence) return;
      status.textContent = error.code === "invalid_request" ? "Sprawdź daty, przypisanie do pobytów, kody i symbole modyfikatorów. Nie udało się ocenić przypadku." : "Nie udało się ocenić przypadku w tym zestawie reguł lub w limicie czasu. To nie oznacza braku grupy. Możesz ponowić przeliczenie.";
    }
  }
  form.addEventListener("input", changed);
  form.addEventListener("change", changed);
  form.addEventListener("submit", function (event) { event.preventDefault(); invalidate(); if (form.reportValidity()) calculate(); });
  document.getElementById("add-ward").addEventListener("click", function () { addWard(); changed(); });
  document.getElementById("add-secondary").addEventListener("click", function () { addCode("secondary"); changed(); });
  document.getElementById("add-procedure").addEventListener("click", function () { addCode("procedure"); changed(); });
  function clearCase() { invalidate(); [principal].concat(secondary, procedures).filter(Boolean).forEach(function (r) { r.search.close(); }); form.reset(); wards = []; secondary = []; procedures = []; ["wards", "principal", "secondary", "procedures"].forEach(function (id) { document.getElementById(id).replaceChildren(); }); addWard(); addCode("principal"); status.textContent = "Uzupełnij fakty przypadku, aby zobaczyć wynik."; }
  document.getElementById("clear-case").addEventListener("click", clearCase);
  window.addEventListener("pagehide", clearCase);
  clearCase();
  window.MinosRequest("/api/v1/releases", { cache: "no-store", credentials: "omit" }).then(function (data) {
    data.releases.forEach(function (release) { if (!releases[release.system] || release.origin === "czs") releases[release.system] = release.release_id; });
    var available = data.grouping && data.grouping.available;
    document.getElementById("group-fields").disabled = !available;
    document.getElementById("group-availability").textContent = available ? "Ocena ograniczona do obsługiwanych reguł. Sprawdź wersje źródeł przy wyniku; nieznane warunki pozostają nierozstrzygnięte." : "Ten zestaw danych nie zawiera źródeł wymaganych do grupowania. Formularz zostanie udostępniony po aktualizacji zestawu.";
  }).catch(function () { document.getElementById("group-availability").textContent = "Nie udało się pobrać zestawu reguł. Odśwież stronę, aby spróbować ponownie."; });
})();
