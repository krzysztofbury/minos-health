/* Home search: grouped results and suggestions while typing.
   Queries travel only in POST bodies, never in URLs, storage or analytics. */
(function () {
  "use strict";

  var SYSTEMS = {
    "icd10-pl": { name: "ICD-10", group: "Rozpoznania (ICD-10)", empty: "Brak wyników w ICD-10.", path: "/slowniki/icd-10" },
    "icd9-pl": { name: "ICD-9", group: "Procedury (ICD-9)", empty: "Brak wyników w ICD-9.", path: "/slowniki/icd-9" },
    "jgp": { name: "JGP", group: "JGP grupy", empty: "Brak pasujących grup JGP.", path: "/jgp" },
    "icd11-pl": { name: "ICD-11", group: "ICD-11 (wersja przedostateczna, nie do rozliczeń NFZ)", empty: "Brak wyników w ICD-11.", path: "/slowniki/icd-11" }
  };
  var ORDER = ["icd10-pl", "icd9-pl", "jgp", "icd11-pl"];
  // ICD-11 PL is pre-released and not in force, so it is searched only on request.
  var DEFAULT_SYSTEMS = ["icd10-pl", "icd9-pl", "jgp"];
  var FIRST_PAGE = 10;
  var MAX_RESULTS = 20;
  var SUGGEST_LIMIT = 4;
  var SUGGEST_DELAY_MS = 220;
  var HANDOFF_KEY = "minos.dictionary.query";
  var FILTER_KEY = "minos.search.filters.v1";
  var FILTER_TTL_MS = 90 * 24 * 60 * 60 * 1000;

  var form = document.getElementById("search-form");
  var input = document.getElementById("query");
  var submit = document.getElementById("search-submit");
  var list = document.getElementById("suggestions");
  var help = document.getElementById("search-help");
  var results = document.getElementById("results");
  var statusLine = document.getElementById("results-status");
  var groups = document.getElementById("result-groups");
  var start = document.getElementById("start");
  var boxes = Array.prototype.slice.call(form.querySelectorAll('input[name="system"]'));
  var preferenceInfo = document.getElementById("filter-preferences");
  var preferenceStatus = document.getElementById("filter-preferences-status");
  var resetFilters = document.getElementById("reset-filters");
  // Keep user intent separate from availability; a missing release is temporary.
  var preferredSystems = DEFAULT_SYSTEMS.slice();

  var releases = {};
  var suggestTimer = 0;
  var suggestController = null;
  var searchController = null;
  var options = [];
  var active = -1;

  function preferenceFailure(message) {
    preferenceStatus.textContent = message;
  }

  function removePreference() {
    try {
      window.localStorage.removeItem(FILTER_KEY);
      preferenceStatus.textContent = "";
      return true;
    } catch (error) {
      preferenceFailure("Nie udało się usunąć zapamiętanych filtrów. Możesz usunąć dane tej strony w ustawieniach przeglądarki.");
      return false;
    }
  }

  function validPreference(value, now_ms) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    if (Object.keys(value).sort().join(",") !== "expires_at_ms,systems,version") return false;
    if (value.version !== 1 || !Array.isArray(value.systems) || value.systems.length > ORDER.length) return false;
    var previous = -1;
    if (!value.systems.every(function (system) {
      var position = ORDER.indexOf(system);
      if (position <= previous) return false;
      previous = position;
      return true;
    })) return false;
    return Number.isSafeInteger(value.expires_at_ms) && value.expires_at_ms > now_ms && value.expires_at_ms <= now_ms + FILTER_TTL_MS;
  }

  function restorePreference() {
    var raw;
    try {
      raw = window.localStorage.getItem(FILTER_KEY);
    } catch (error) {
      preferenceFailure("Nie udało się odczytać zapamiętanych filtrów. Włączamy domyślne źródła.");
      return;
    }
    if (raw === null) return;
    if (raw.length <= 256) {
      try {
        var value = JSON.parse(raw);
        if (validPreference(value, Date.now())) {
          preferredSystems = value.systems.slice();
          return;
        }
      } catch (error) { /* Malformed data is not a preference. */ }
    }
    removePreference();
  }

  function savePreference() {
    var value = { version: 1, systems: preferredSystems.slice(), expires_at_ms: Date.now() + FILTER_TTL_MS };
    if (!validPreference(value, Date.now())) return;
    try {
      window.localStorage.setItem(FILTER_KEY, JSON.stringify(value));
      preferenceStatus.textContent = "";
    } catch (error) {
      preferenceFailure("Nie udało się zapamiętać filtrów. Twój wybór działa do opuszczenia tej strony.");
    }
  }

  function applyPreference() {
    boxes.forEach(function (box) {
      box.disabled = !releases[box.value];
      box.checked = !box.disabled && preferredSystems.indexOf(box.value) >= 0;
    });
    syncReset();
  }

  // Reset only offers something while an available source differs from the default.
  function syncReset() {
    resetFilters.hidden = boxes.every(function (box) {
      return box.disabled || box.checked === (DEFAULT_SYSTEMS.indexOf(box.value) >= 0);
    });
  }

  function filtersChanged() {
    window.clearTimeout(suggestTimer);
    if (suggestController) suggestController.abort();
    closeSuggestions();
    if (!results.hidden && normalize(input.value)) runSearch();
  }

  restorePreference();

  // Visible dates read DD.MM.RRRR; requests and links keep ISO.
  function plDate(value) {
    var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
    return match ? match[3] + "." + match[2] + "." + match[1] : value;
  }

  function element(name, className, text) {
    var node = document.createElement(name);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function plural(count, one, few, many) {
    var tens = count % 100;
    var units = count % 10;
    if (count === 1) return one;
    if (units >= 2 && units <= 4 && (tens < 12 || tens > 14)) return few;
    return many;
  }

  function normalize(text) {
    return text.replace(/\s+/g, " ").trim();
  }

  function validate(text) {
    if (!text) return "Wpisz kod albo kilka słów opisu.";
    if (text.length > 120) return "Wpisz najwyżej 120 znaków.";
    if (text.split(" ").length > 8) return "Wpisz najwyżej 8 słów.";
    if (/[\u0000-\u001f\u007f]/.test(text)) return "Usuń znaki sterujące z zapytania.";
    return "";
  }

  function selectedSystems() {
    return boxes.filter(function (box) { return box.checked && releases[box.value]; })
      .map(function (box) { return box.value; });
  }

  function dictionaryHref(system, code) {
    // Each code has a stable, citable page; the dictionary tree is one click away.
    return SYSTEMS[system].path + (code ? "/" + encodeURIComponent(code) : "");
  }

  function failureMessage(code) {
    if (code === "query_timeout" || code === "request_timeout") return "Wyszukiwanie trwało zbyt długo. To nie oznacza braku dopasowań. Możesz spróbować ponownie.";
    if (code === "limit_exceeded") return "Zapytanie przekroczyło bezpieczny limit przetwarzania. Wpisz dokładniejszy opis albo kod.";
    if (code === "busy") return "Wyszukiwarka jest teraz zajęta. Spróbuj ponownie za chwilę.";
    if (code === "invalid_request") return "Tego zapytania nie da się wyszukać. Zmień frazę.";
    return "Słownik jest chwilowo niedostępny. Spróbuj ponownie za chwilę.";
  }

  function lookup(system, query, limit, signal, state) {
    var release = releases[system];
    return window.MinosRequest("/api/v1/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ system: system, release_id: system === "jgp" ? undefined : release.release_id, query: query, limit: limit }),
      cache: "no-store",
      credentials: "omit",
      signal: signal
    }, state).then(function (body) {
      if (Array.isArray(body.results)) return body;
      throw new Error("invalid lookup response");
    });
  }

  /* Suggestions while typing (WAI-ARIA combobox with listbox popup). */
  function closeSuggestions() {
    list.hidden = true;
    list.replaceChildren();
    options = [];
    active = -1;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  function highlight(index) {
    options.forEach(function (option, position) {
      option.node.setAttribute("aria-selected", position === index ? "true" : "false");
    });
    active = index;
    if (index >= 0) {
      input.setAttribute("aria-activedescendant", options[index].node.id);
      options[index].node.scrollIntoView({ block: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function openOption(option) {
    window.location.assign(option.href || dictionaryHref(option.system, option.code));
  }

  function showSuggestions(items) {
    list.replaceChildren();
    options = [];
    items.forEach(function (item, index) {
      var node = element("li", "suggestion");
      node.id = "suggestion-" + index;
      node.setAttribute("role", "option");
      node.setAttribute("aria-selected", "false");
      node.append(
        element("span", "code-chip", item.code),
        element("span", "suggestion-label", normalize(item.label)),
        element("span", "suggestion-system", SYSTEMS[item.system].name)
      );
      node.addEventListener("mousedown", function (event) { event.preventDefault(); });
      node.addEventListener("click", function () { openOption(item); });
      options.push({ node: node, system: item.system, code: item.code, href: item.href });
      list.append(node);
    });
    active = -1;
    list.hidden = items.length === 0;
    input.setAttribute("aria-expanded", items.length ? "true" : "false");
  }

  function suggest() {
    var query = normalize(input.value);
    if (suggestController) suggestController.abort();
    if (query.length < 2 || validate(query)) { closeSuggestions(); return; }
    var systems = selectedSystems();
    if (!systems.length) { closeSuggestions(); return; }
    suggestController = new AbortController();
    var signal = suggestController.signal;
    Promise.all(systems.map(function (system) {
      return lookup(system, query, SUGGEST_LIMIT, signal, { retry: false })
        .then(function (answer) { return answer.results.map(function (hit) { return { system: system, code: hit.code, label: hit.label, href: system === "jgp" ? hit.href : undefined }; }); })
        .catch(function () { return []; });
    })).then(function (perSystem) {
      if (signal.aborted || normalize(input.value) !== query) return;
      var merged = [];
      for (var row = 0; row < SUGGEST_LIMIT; row++) {
        perSystem.forEach(function (items) { if (items[row] && items[row].code) merged.push(items[row]); });
      }
      showSuggestions(merged.slice(0, 8));
    });
  }

  /* Grouped results. */
  function badgeFor(hit, system) {
    var status = hit.source_attributes && hit.source_attributes.status;
    if (system === "icd9-pl" && status && status !== "A") return element("span", "badge badge-warn", "Status " + status + " w CZS" + (hit.match_type === "folded_tokens" ? "; bez polskich znaków" : ""));
    if (hit.match_type === "morphology_tokens") return element("span", "badge badge-muted", "inna forma słowa");
    if (hit.match_type === "folded_tokens") return element("span", "badge badge-muted", "bez polskich znaków");
    if (hit.match_type === "synonym_tokens") return element("span", "badge badge-muted", "synonim: " + hit.synonym_source_wording);
    if (hit.match_type === "related_tokens") return element("span", "badge badge-muted", "powiązana procedura: " + hit.synonym_source_wording);
    if (hit.match_type === "substring") return element("span", "badge badge-muted", "fragment słowa");
    return null;
  }

  function renderHits(system, answer, listNode) {
    listNode.replaceChildren();
    answer.results.forEach(function (hit) {
      var item = element("li");
      var link = element("a", "result");
      link.href = system === "jgp" ? hit.href : dictionaryHref(system, hit.code) + (hit.jgp_membership && hit.jgp_membership.service_date ? "/jgp/" + encodeURIComponent(hit.jgp_membership.service_date) : "");
      link.append(element("span", "code-chip", hit.code || "-"), element("span", "result-label", normalize(hit.label || "")));
      var matched = normalize((hit.match && hit.match.text) || "");
      if (matched && matched.toLowerCase() !== normalize(hit.label || "").toLowerCase()) {
        link.append(element("span", "result-why", "Pasuje do: „" + matched + "”"));
      }
      var badge = badgeFor(hit, system);
      var meta = element("span", "result-meta");
      if (system === "jgp") meta.append(element("span", "badge badge-muted", hit.catalog), element("span", "", hit.act_number));
      if (hit.jgp_membership && hit.jgp_membership.group_count) {
        var membership = element("span", "badge badge-muted", "występuje w " + hit.jgp_membership.group_count + " " + plural(hit.jgp_membership.group_count, "grupie", "grupach", "grupach") + " JGP");
        membership.title = "Obecność na jawnych listach załącznika 9 na " + plDate(hit.jgp_membership.service_date) + "; nie oznacza kwalifikacji ani typowego leczenia";
        meta.append(membership);
      }
      if (badge) {
        meta.append(badge);
      }
      if (meta.childNodes.length) link.append(meta);
      item.append(link);
      listNode.append(item);
    });
  }

  function groupSection(system) {
    var section = element("section", "result-group");
    section.setAttribute("aria-labelledby", "group-" + system);
    var heading = element("h2", "result-group-head");
    var toggle = element("button", "result-toggle");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "true");
    toggle.setAttribute("aria-controls", "results-" + system);
    var title = element("span", "result-title", SYSTEMS[system].group);
    title.id = "group-" + system;
    var count = element("span", "result-count", "Szukam…");
    var chevron = element("span", "chevron");
    chevron.setAttribute("aria-hidden", "true");
    toggle.append(title, count, chevron);
    heading.append(toggle);
    var body = element("ul", "result-list");
    var foot = element("div", "result-group-foot");
    foot.hidden = true;
    var content = element("div");
    content.id = "results-" + system;
    content.append(body, foot);
    // Sections fold from their headers, so a long list never hides the next one.
    function show(open) {
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      content.hidden = !open;
    }
    toggle.addEventListener("click", function () { show(toggle.getAttribute("aria-expanded") !== "true"); });
    section.append(heading, content);
    return { section: section, title: title, count: count, body: body, foot: foot, show: show };
  }

  function handOff(system, query) {
    try { window.sessionStorage.setItem(HANDOFF_KEY, JSON.stringify({ system: system, query: query })); } catch (error) { /* The dictionary still opens. */ }
    window.location.assign(SYSTEMS[system].path);
  }

  function fillGroup(group, system, query, answer, limit) {
    var shown = answer.results.length;
    group.foot.replaceChildren();
    if (system === "jgp") {
      group.title.textContent = SYSTEMS[system].group + " - stan na " + plDate(answer.service_date);
      var advanced = element("a", "button button-quiet", "Więcej opcji");
      advanced.href = "/jgp/" + encodeURIComponent(answer.service_date);
      group.foot.append(advanced);
      if (answer.ruleset && answer.ruleset.service_date_after_knowledge) {
        group.foot.append(element("p", "group-message", "Według źródeł sprawdzonych do " + plDate(answer.ruleset.knowledge_as_of) + "."));
      }
    }
    if (shown === 0) {
      group.count.textContent = "0 wyników";
      group.body.replaceChildren(element("li", "group-message", answer.status === "not_covered" ? "Brak katalogu JGP obejmującego ten dzień." : SYSTEMS[system].empty));
      if (Array.isArray(answer.suggestions) && answer.suggestions.length) {
        group.foot.append(element("span", "", "Czy chodziło o:"));
        answer.suggestions.slice(0, 3).forEach(function (suggestion) {
          var text = typeof suggestion === "string" ? suggestion : suggestion && suggestion.text;
          if (!text) return;
          var button = element("button", "button button-quiet", text);
          button.type = "button";
          button.addEventListener("click", function () { input.value = text; runSearch(); });
          group.foot.append(button);
        });
        group.foot.hidden = group.foot.childElementCount <= 1;
      } else {
        group.foot.hidden = group.foot.childElementCount === 0;
      }
      return;
    }
    renderHits(system, answer, group.body);
    var datedMembership = answer.results.find(function (hit) { return hit.jgp_membership && hit.jgp_membership.group_count; });
    if (datedMembership) group.foot.append(element("p", "group-message", "Obecność na listach załącznika 9 na " + plDate(datedMembership.jgp_membership.service_date) + ". Nie oznacza typowego leczenia ani kwalifikacji hospitalizacji."));
    group.count.textContent = answer.truncated
      ? shown + " najlepszych dopasowań"
      : shown + " " + plural(shown, "wynik", "wyniki", "wyników");
    if (answer.truncated && limit < MAX_RESULTS) {
      var more = element("button", "button button-quiet", "Pokaż więcej");
      more.type = "button";
      more.addEventListener("click", function () {
        more.disabled = true;
        lookup(system, query, MAX_RESULTS).then(function (next) {
          fillGroup(group, system, query, next, MAX_RESULTS);
        }).catch(function (error) {
          more.disabled = false;
          var previous = group.foot.querySelector(".is-error");
          if (previous) previous.remove();
          group.foot.prepend(element("p", "group-message is-error", failureMessage(error.code)));
          group.show(true);
        });
      });
      group.foot.append(more);
    }
    if (answer.truncated && system !== "jgp") {
      var open = element("button", "button button-quiet", "Szukaj w pełnym słowniku " + SYSTEMS[system].name);
      open.type = "button";
      open.addEventListener("click", function () { handOff(system, query); });
      group.foot.append(open);
    }
    group.foot.hidden = group.foot.childElementCount === 0;
  }

  function failGroup(group, system, query, code) {
    group.count.textContent = "";
    // An error and its retry must not stay inside a section folded meanwhile.
    group.show(true);
    group.body.replaceChildren(element("li", "group-message is-error", failureMessage(code)));
    group.foot.replaceChildren();
    var retry = element("button", "button button-quiet", "Spróbuj ponownie");
    retry.type = "button";
    retry.addEventListener("click", function () { loadGroup(group, system, query).catch(function () { /* Replaced search. */ }); });
    group.foot.append(retry);
    group.foot.hidden = false;
  }

  function loadGroup(group, system, query) {
    group.count.textContent = "Szukam…";
    var signal = searchController ? searchController.signal : undefined;
    group.section.setAttribute("aria-busy", "true");
    group.section.dataset.searchState = "pending";
    group.body.replaceChildren();
    group.foot.hidden = true;
    var waiting = window.setTimeout(function () {
      if (!signal || !signal.aborted) group.count.textContent = "Wciąż szukam. Przygotowanie wyników trwa dłużej niż zwykle…";
    }, 500);
    return lookup(system, query, FIRST_PAGE, signal, { onRetry: function () {
      window.clearTimeout(waiting);
      if (!signal || !signal.aborted) group.count.textContent = "Serwis jest zajęty. Ponawiam za chwilę…";
    } }).then(function (answer) {
      if (signal && signal.aborted) throw new DOMException("Aborted", "AbortError");
      fillGroup(group, system, query, answer, FIRST_PAGE);
      group.section.dataset.searchState = answer.status === "not_covered" ? "uncovered" : "ready";
      group.section.dataset.resultCount = String(answer.results.length);
      return answer.results.length;
    }).catch(function (error) {
      if (error.name === "AbortError") throw error;
      failGroup(group, system, query, error.code);
      group.section.dataset.searchState = "failed";
      return null;
    }).finally(function () {
      window.clearTimeout(waiting);
      group.section.setAttribute("aria-busy", "false");
      if (!signal || !signal.aborted) summarizeResults();
    });
  }

  function summarizeResults() {
    var sections = Array.from(groups.children);
    if (sections.some(function (section) { return section.dataset.searchState === "pending"; })) return;
    var failures = sections.filter(function (section) { return section.dataset.searchState === "failed"; }).length;
    if (failures) {
      statusLine.textContent = failures === sections.length ? "Nie udało się zakończyć wyszukiwania. Powody i przyciski ponowienia znajdziesz poniżej." : "Część słowników nie odpowiedziała. Dostępne wyniki i opcje ponowienia znajdziesz poniżej.";
      return;
    }
    if (sections.some(function (section) { return section.dataset.searchState === "uncovered"; })) {
      statusLine.textContent = "Nie wszystkie katalogi obejmują wybrany dzień. Szczegóły znajdziesz poniżej.";
      return;
    }
    var total = sections.reduce(function (sum, section) { return sum + Number(section.dataset.resultCount || 0); }, 0);
    statusLine.textContent = total ? "Kliknij pozycję, aby zobaczyć jej opis i miejsce w słowniku." : "Nic nie znaleziono. Sprawdź pisownię albo wpisz kod.";
  }

  function runSearch() {
    window.clearTimeout(suggestTimer);
    if (suggestController) suggestController.abort();
    closeSuggestions();
    var query = normalize(input.value);
    var problem = validate(query);
    var systems = selectedSystems();
    if (searchController) searchController.abort();
    results.hidden = false;
    statusLine.classList.remove("is-error");
    if (problem || !systems.length) {
      groups.replaceChildren();
      statusLine.textContent = problem || "Zaznacz co najmniej jeden słownik.";
      statusLine.classList.add("is-error");
      input.focus();
      return;
    }
    searchController = new AbortController();
    start.hidden = true;
    groups.replaceChildren();
    statusLine.textContent = "Szukam w: " + systems.map(function (system) { return SYSTEMS[system].name; }).join(", ") + ".";
    Promise.all(systems.map(function (system) {
      var group = groupSection(system);
      groups.append(group.section);
      return loadGroup(group, system, query);
    })).catch(function () { /* A newer search replaced this one. */ });
  }

  function setUnavailable(message) {
    help.textContent = message;
    help.classList.add("is-unavailable");
    input.disabled = true;
    submit.disabled = true;
  }

  input.addEventListener("input", function () {
    if (suggestController) suggestController.abort();
    window.clearTimeout(suggestTimer);
    suggestTimer = window.setTimeout(suggest, SUGGEST_DELAY_MS);
  });
  input.addEventListener("keydown", function (event) {
    if (event.key === "ArrowDown" && options.length) {
      event.preventDefault();
      highlight((active + 1) % options.length);
    } else if (event.key === "ArrowUp" && options.length) {
      event.preventDefault();
      highlight(active <= 0 ? options.length - 1 : active - 1);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeSuggestions();
    } else if (event.key === "Enter" && active >= 0 && options[active]) {
      event.preventDefault();
      openOption(options[active]);
    }
  });
  input.addEventListener("blur", function () { window.setTimeout(closeSuggestions, 120); });
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    window.clearTimeout(suggestTimer);
    if (suggestController) suggestController.abort();
    runSearch();
  });
  boxes.forEach(function (box) {
    box.addEventListener("change", function () {
      if (box.disabled) return;
      preferredSystems = ORDER.filter(function (system) {
        var candidate = boxes.find(function (item) { return item.value === system; });
        return candidate && (candidate.disabled ? preferredSystems.indexOf(system) >= 0 : candidate.checked);
      });
      savePreference();
      syncReset();
      filtersChanged();
    });
  });
  resetFilters.addEventListener("click", function () {
    var focused = document.activeElement === resetFilters;
    preferredSystems = DEFAULT_SYSTEMS.slice();
    applyPreference();
    // The button hides itself; keep keyboard focus on the restored filters.
    var first = boxes.find(function (box) { return !box.disabled; });
    if (focused && first) first.focus();
    var removed = removePreference();
    filtersChanged();
    if (removed) preferenceStatus.textContent = "Przywrócono domyślne filtry i usunięto zapamiętany wybór.";
  });
  document.addEventListener("keydown", function (event) {
    var tag = document.activeElement && document.activeElement.tagName;
    if (event.key === "/" && tag !== "INPUT" && tag !== "TEXTAREA" && tag !== "SELECT") {
      event.preventDefault();
      input.focus();
    }
  });

  // A first submit must not race the exact release pins required by the API.
  input.disabled = true;
  submit.disabled = true;
  window.MinosRequest("/api/v1/releases")
    .then(function (answer) {
      if (!answer || !Array.isArray(answer.releases)) throw new Error("invalid releases");
      // The API lists the release in force today first; the grouper takes it too (#235).
      answer.releases.forEach(function (release) {
        if (SYSTEMS[release.system] && !releases[release.system]) releases[release.system] = release;
      });
      if (answer.jgp_search) releases.jgp = { system: "jgp" };
      applyPreference();
      preferenceInfo.hidden = false;
      resetFilters.disabled = false;
      if (!ORDER.some(function (system) { return releases[system]; })) {
        setUnavailable("Brak słowników dostępnych do wyszukania.");
        return;
      }
      input.disabled = false;
      submit.disabled = false;
    })
    .catch(function () {
      setUnavailable("Nie udało się połączyć z wyszukiwarką. Odśwież stronę za chwilę.");
    });
})();
