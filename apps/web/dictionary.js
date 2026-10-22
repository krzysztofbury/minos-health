/* Dictionary tree: lazy source hierarchy, narrowed by search results.
   Tree requests carry only a release id and node number; typed text is sent
   only in POST search bodies and never placed in URLs. */
(function () {
  "use strict";

  var NAMES = { "icd10-pl": "ICD-10", "icd9-pl": "ICD-9", "icd11-pl": "ICD-11" };
  var HANDOFF_KEY = "minos.dictionary.query";
  var SEARCH_LIMIT = 20;
  var searchController;
  var NOTE_TITLES = {
    includes: "Obejmuje",
    excludes: "Nie obejmuje",
    term: "Terminy w źródle",
    note: "Uwagi",
    modifier: "Podział dodatkowy"
  };

  var root = document.getElementById("dictionary");
  var system = root.getAttribute("data-system");
  var tree = document.getElementById("tree");
  var statusLine = document.getElementById("tree-status");
  var detail = document.getElementById("detail");
  var form = document.getElementById("tree-search");
  var input = document.getElementById("tree-query");
  var reset = document.getElementById("tree-reset");
  var lead = document.getElementById("dictionary-source");
  var unplaced = document.getElementById("unplaced");
  var unplacedList = document.getElementById("unplaced-list");

  var release = null;
  var roots = null;
  var cache = new Map();
  var pending = new Map();
  var selected = null;
  var filtered = false;

  function element(name, className, text) {
    var node = document.createElement(name);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function clean(text) {
    return (text || "").replace(/\s+/g, " ").trim();
  }

  function setStatus(text, isError) {
    statusLine.textContent = text;
    statusLine.classList.toggle("is-error", Boolean(isError));
  }

  function chevron() {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 12 12");
    svg.setAttribute("aria-hidden", "true");
    var path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "M4 2l4 4-4 4");
    svg.append(path);
    return svg;
  }

  function fetchJson(url, options) {
    return window.MinosRequest(url, options, { onRetry: function () { setStatus("Słownik jest zajęty. Ponawiam za chwilę…", false); } });
  }

  function failure(code) {
    if (code === "query_timeout" || code === "request_timeout") return "Odczyt trwał zbyt długo. To nie oznacza braku danych. Możesz spróbować ponownie.";
    if (code === "limit_exceeded") return "Zapytanie przekroczyło limit przetwarzania. Wpisz dokładniejszą frazę albo kod.";
    if (code === "busy") return "Słownik jest teraz zajęty. Spróbuj ponownie za chwilę.";
    if (code === "not_found") return "Tej pozycji nie ma w słowniku.";
    return "Słownik jest chwilowo niedostępny. Odśwież stronę za chwilę.";
  }

  function node(ordinal) {
    if (cache.has(ordinal)) return Promise.resolve(cache.get(ordinal));
    if (pending.has(ordinal)) return pending.get(ordinal);
    var request = fetchJson("/api/v1/tree/" + encodeURIComponent(release.release_id) + "/" + ordinal)
      .then(function (answer) { cache.set(ordinal, answer); pending.delete(ordinal); return answer; })
      .catch(function (error) { pending.delete(ordinal); throw error; });
    pending.set(ordinal, request);
    return request;
  }

  /* Rendering. */
  function treeItem(child, level) {
    var item = element("li");
    item.setAttribute("role", "treeitem");
    item.setAttribute("aria-level", String(level));
    item.setAttribute("aria-selected", "false");
    item.tabIndex = -1;
    item.dataset.ordinal = String(child.ordinal);
    item.dataset.code = child.code || "";
    var row = element("div", "tree-row");
    if (child.has_children) {
      item.setAttribute("aria-expanded", "false");
      var toggle = element("button", "tree-toggle");
      toggle.type = "button";
      toggle.tabIndex = -1;
      toggle.setAttribute("aria-label", "Rozwiń " + (child.code || clean(child.label)));
      toggle.append(chevron());
      toggle.addEventListener("click", function (event) {
        event.stopPropagation();
        toggleItem(item);
      });
      row.append(toggle);
    } else {
      row.append(element("span", "tree-spacer"));
    }
    var text = element("span", "tree-text");
    if (child.code) text.append(element("span", "code-chip", child.code));
    text.append(element("span", "tree-label", clean(child.label)));
    if (child.canonical === false) text.append(element("span", "badge badge-muted", "tylko w skoroszycie NFZ"));
    row.append(text);
    row.addEventListener("click", function () { select(item, true); });
    item.append(row);
    return item;
  }

  function renderChildren(container, children, level) {
    container.replaceChildren();
    children.forEach(function (child) { container.append(treeItem(child, level)); });
  }

  function groupOf(item) {
    var group = item.querySelector(":scope > ul");
    if (!group) {
      group = element("ul");
      group.setAttribute("role", "group");
      item.append(group);
    }
    return group;
  }

  function expand(item) {
    if (!item.hasAttribute("aria-expanded")) return Promise.resolve();
    var ordinal = Number(item.dataset.ordinal);
    var group = groupOf(item);
    item.setAttribute("aria-expanded", "true");
    var toggle = item.querySelector(":scope > .tree-row .tree-toggle");
    if (toggle) toggle.setAttribute("aria-label", "Zwiń " + (item.dataset.code || "pozycję"));
    if (group.dataset.loaded === "true") { group.hidden = false; return Promise.resolve(); }
    group.replaceChildren(element("li", "tree-more", "Wczytywanie…"));
    return node(ordinal).then(function (answer) {
      renderChildren(group, answer.children, Number(item.getAttribute("aria-level")) + 1);
      if (answer.truncated) group.append(element("li", "tree-more", "Pokazano pierwsze pozycje. Użyj wyszukiwania, aby znaleźć pozostałe."));
      group.dataset.loaded = "true";
      group.hidden = false;
    }).catch(function (error) {
      group.replaceChildren(element("li", "tree-more", failure(error.code)));
      item.setAttribute("aria-expanded", "false");
    });
  }

  function collapse(item) {
    if (item.getAttribute("aria-expanded") !== "true") return;
    item.setAttribute("aria-expanded", "false");
    var group = item.querySelector(":scope > ul");
    if (group) group.hidden = true;
    var toggle = item.querySelector(":scope > .tree-row .tree-toggle");
    if (toggle) toggle.setAttribute("aria-label", "Rozwiń " + (item.dataset.code || "pozycję"));
  }

  function toggleItem(item) {
    if (item.getAttribute("aria-expanded") === "true") collapse(item);
    else expand(item);
  }

  function focusItem(item) {
    tree.querySelectorAll('[role="treeitem"][tabindex="0"]').forEach(function (other) { other.tabIndex = -1; });
    item.tabIndex = 0;
    item.focus();
  }

  function select(item, fromPointer) {
    if (selected) selected.setAttribute("aria-selected", "false");
    selected = item;
    item.setAttribute("aria-selected", "true");
    focusItem(item);
    if (item.dataset.code) history.replaceState(null, "", "#kod=" + encodeURIComponent(item.dataset.code));
    showDetail(Number(item.dataset.ordinal), fromPointer);
  }

  function visibleItems() {
    return Array.prototype.filter.call(tree.querySelectorAll('[role="treeitem"]'), function (item) {
      var parent = item.parentElement;
      while (parent && parent !== tree) {
        if (parent.hidden) return false;
        parent = parent.parentElement;
      }
      return true;
    });
  }

  tree.addEventListener("keydown", function (event) {
    var current = document.activeElement;
    if (!current || current.getAttribute("role") !== "treeitem") return;
    var items = visibleItems();
    var index = items.indexOf(current);
    var handled = true;
    if (event.key === "ArrowDown") { if (items[index + 1]) focusItem(items[index + 1]); }
    else if (event.key === "ArrowUp") { if (items[index - 1]) focusItem(items[index - 1]); }
    else if (event.key === "Home") { if (items[0]) focusItem(items[0]); }
    else if (event.key === "End") { if (items.length) focusItem(items[items.length - 1]); }
    else if (event.key === "ArrowRight") {
      if (current.getAttribute("aria-expanded") === "false") expand(current);
      else if (current.getAttribute("aria-expanded") === "true") {
        var first = current.querySelector(':scope > ul > [role="treeitem"]');
        if (first) focusItem(first);
      }
    } else if (event.key === "ArrowLeft") {
      if (current.getAttribute("aria-expanded") === "true") collapse(current);
      else {
        var parent = current.parentElement && current.parentElement.closest('[role="treeitem"]');
        if (parent) focusItem(parent);
      }
    } else if (event.key === "Enter" || event.key === " ") { select(current, false); }
    else handled = false;
    if (handled) event.preventDefault();
  });

  /* Detail panel. */
  function factList(attributes) {
    var list = element("dl", "detail-facts");
    attributes.forEach(function (attribute) {
      if (attribute.key === "evidence_kind" || !clean(attribute.value)) return;
      var wrapper = element("div");
      var value = attribute.key === "status" ? attribute.value + " (oznaczenie w CZS)" : clean(attribute.value);
      wrapper.append(element("dt", "", attribute.name), element("dd", "", value));
      list.append(wrapper);
    });
    return list.childElementCount ? list : null;
  }

  function noteBlocks(notes, supporting) {
    var wrapper = element("div", "detail-notes");
    ["includes", "excludes", "term", "modifier", "note"].forEach(function (kind) {
      var items = notes.filter(function (note) { return note.kind === kind; });
      if (!items.length) return;
      var title = NOTE_TITLES[kind] + (supporting && kind !== "modifier" ? " (skoroszyt NFZ)" : "");
      wrapper.append(element("h3", "", title));
      var list = element("ul");
      items.forEach(function (note) {
        var line = element("li");
        if (note.code) line.append(element("span", "code-chip", note.code), document.createTextNode(" "));
        line.append(document.createTextNode(clean(note.text)));
        list.append(line);
      });
      wrapper.append(list);
    });
    return wrapper.childElementCount ? wrapper : null;
  }

  function showDetail(ordinal, fromPointer) {
    detail.replaceChildren(element("p", "detail-empty", "Wczytywanie…"));
    node(ordinal).then(function (answer) {
      var info = answer.node;
      detail.replaceChildren();
      if (info.code) {
        var code = element("p", "detail-code");
        code.append(element("span", "code-chip", info.code));
        detail.append(code);
      }
      detail.append(element("h2", "", clean(info.label)));
      var attributes = info.attributes || [];
      var status = attributes.filter(function (attribute) { return attribute.key === "status"; })[0];
      if (status && status.value !== "A") detail.append(element("p", "badge badge-warn", "Status " + status.value + " w CZS"));
      if (info.canonical === false) detail.append(element("p", "badge badge-muted", "Pozycja tylko w skoroszycie NFZ, bez odpowiednika w CZS"));
      if (info.path && info.path.length) {
        var path = element("ol", "detail-path");
        path.setAttribute("aria-label", "Miejsce w słowniku");
        info.path.forEach(function (step) {
          var item = element("li");
          var button = element("button", "", step.code || clean(step.label));
          button.type = "button";
          button.title = clean(step.label);
          button.addEventListener("click", function () { reveal(info.path.slice(0, info.path.indexOf(step) + 1).map(function (part) { return part.ordinal; })); });
          item.append(button);
          path.append(item);
        });
        detail.append(path);
      }
      var facts = factList(attributes);
      if (facts) detail.append(facts);
      var notes = noteBlocks(info.notes || [], answer.supporting_hierarchy);
      if (notes) detail.append(notes);
      detail.append(element("p", "detail-source", answer.supporting_hierarchy
        ? "Nazwa, status i daty: CZS NFZ. Miejsce w hierarchii i uwagi oznaczone jako skoroszyt: skoroszyt NFZ tej samej wersji."
        : system === "icd11-pl"
          ? "Treść: ICD-11 PL z Rejestru Systemów Kodowania (RSK), wersja przedostateczna " + release.source_version + ", CC BY-ND 4.0. Tłumaczenie nie pochodzi od WHO; wiążąca jest wersja angielska. Nie do rozliczeń NFZ."
          : "Treść i układ: Rejestr Systemów Kodowania (RSK), " + NAMES[system] + " wersja " + release.source_version + "."));
      if (info.code) {
        var permalink = element("a", "detail-permalink", "Stały adres tego kodu");
        permalink.href = window.location.pathname + "/" + encodeURIComponent(info.code);
        detail.append(permalink);
      }
      var back = element("button", "button button-quiet detail-back", "Wróć do drzewa");
      back.type = "button";
      back.addEventListener("click", function () {
        var item = findItem(ordinal);
        if (!item) return;
        item.scrollIntoView({ block: "center" });
        focusItem(item);
      });
      detail.append(back);
      if (fromPointer && window.matchMedia("(max-width: 59.99rem)").matches) detail.scrollIntoView({ block: "start", behavior: "smooth" });
    }).catch(function (error) {
      detail.replaceChildren(element("p", "detail-empty", failure(error.code)));
    });
  }

  /* Full tree, deep links and narrowing. */
  function showRoots() {
    filtered = false;
    reset.hidden = true;
    renderChildren(tree, roots.children, 1);
    var first = tree.querySelector('[role="treeitem"]');
    if (first) first.tabIndex = 0;
    showUnplaced(roots.unplaced || [], null);
    setStatus(roots.children.length + " pozycji najwyższego poziomu. Rozwiń pozycję strzałką lub kliknięciem.");
  }

  function showUnplaced(items, matches) {
    var visible = matches ? items.filter(function (item) { return matches.has(item.code); }) : items;
    unplacedList.replaceChildren();
    visible.forEach(function (item) {
      var line = element("li", "tree-row");
      var text = element("span", "tree-text");
      text.append(element("span", "code-chip", item.code), element("span", "tree-label", clean(item.label)));
      var status = (item.attributes || []).filter(function (attribute) { return attribute.key === "status"; })[0];
      if (status && status.value !== "A") text.append(element("span", "badge badge-warn", "Status " + status.value + " w CZS"));
      line.append(element("span", "tree-spacer"), text);
      unplacedList.append(line);
    });
    unplaced.hidden = visible.length === 0;
  }

  function findItem(ordinal) {
    return tree.querySelector('[role="treeitem"][data-ordinal="' + ordinal + '"]');
  }

  function reveal(path) {
    if (filtered) showRoots();
    var chain = Promise.resolve();
    path.slice(0, -1).forEach(function (ordinal) {
      chain = chain.then(function () {
        var item = findItem(ordinal);
        if (!item) throw new Error("path not in tree");
        return expand(item);
      });
    });
    return chain.then(function () {
      var target = findItem(path[path.length - 1]);
      if (!target) throw new Error("target not in tree");
      select(target, false);
      target.scrollIntoView({ block: "center" });
    });
  }

  function pathOf(hit) {
    if (release.supporting_sources && release.supporting_sources.length) {
      var support = (hit.supporting_contexts || [])[0];
      if (!support) return null;
      return support.context.nodes.filter(treeLevel);
    }
    return (hit.context && hit.context.nodes || []).filter(treeLevel);
  }

  // Coded nodes and titled containers (ICD-11 blocks) are tree levels; untitled containers are not.
  function treeLevel(step) {
    return step.kind === "range" || step.kind === "classification" || (step.kind === "container" && clean(step.label || "") !== "");
  }

  function search(query, limit) {
    if (searchController) searchController.abort();
    searchController = new AbortController();
    var signal = searchController.signal;
    var waiting = window.setTimeout(function () {
      if (!signal.aborted) setStatus("Wciąż szukam. Przygotowanie wyników trwa dłużej niż zwykle…");
    }, 500);
    return fetchJson("/api/v1/search", {
      method: "POST",
      signal: signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ system: system, release_id: release.release_id, query: query, limit: limit })
    }).then(function (answer) {
      if (signal.aborted) throw new DOMException("Aborted", "AbortError");
      return answer;
    }).finally(function () { window.clearTimeout(waiting); });
  }

  function openCode(code) {
    setStatus("Wyszukiwanie kodu " + code + "…");
    return search(code, 5).then(function (answer) {
      var hit = answer.results.filter(function (candidate) { return candidate.code === code; })[0];
      var path = hit && pathOf(hit);
      if (!hit) { setStatus("Nie znaleziono kodu " + code + " w tym słowniku.", true); return; }
      if (!path || !path.length) {
        showUnplaced(roots.unplaced || [], new Set([code]));
        setStatus("Kod " + code + " jest w katalogu CZS, ale nie ma miejsca w hierarchii skoroszytu NFZ.");
        unplaced.scrollIntoView({ block: "start" });
        return;
      }
      return reveal(path.map(function (step) { return step.ordinal; })).then(function () {
        setStatus("Pokazano kod " + code + " w słowniku.");
      });
    }).catch(function (error) { if (error.name !== "AbortError") setStatus(failure(error.code), true); });
  }

  function narrow(query) {
    setStatus("Szukam…");
    return search(query, SEARCH_LIMIT).then(function (answer) {
      filtered = true;
      reset.hidden = false;
      tree.replaceChildren();
      var unplacedCodes = new Set();
      var items = new Map();
      var matches = 0;
      answer.results.forEach(function (hit) {
        var path = pathOf(hit);
        if (!path || !path.length) { unplacedCodes.add(hit.code); matches++; return; }
        matches++;
        var container = tree;
        path.forEach(function (step, index) {
          var key = String(step.ordinal);
          var item = items.get(key);
          var last = index === path.length - 1;
          if (!item) {
            item = treeItem({ ordinal: step.ordinal, code: step.code, label: last ? hit.label : step.label, has_children: true }, index + 1);
            container.append(item);
            items.set(key, item);
          }
          if (last) item.querySelector(".tree-label").classList.add("is-match");
          if (!last) {
            item.setAttribute("aria-expanded", "true");
            var group = groupOf(item);
            group.dataset.loaded = "false";
            container = group;
          } else {
            item.removeAttribute("aria-expanded");
            var toggle = item.querySelector(".tree-toggle");
            if (toggle) toggle.replaceWith(element("span", "tree-spacer"));
          }
        });
      });
      tree.querySelectorAll(".tree-label.is-match").forEach(function (label) {
        var mark = element("mark", "", label.textContent);
        label.replaceChildren(mark);
      });
      showUnplaced(roots.unplaced || [], unplacedCodes);
      var first = tree.querySelector('[role="treeitem"]');
      if (first) first.tabIndex = 0;
      if (!matches) {
        setStatus("Nic nie znaleziono. Sprawdź pisownię albo wpisz kod.");
      } else {
        setStatus((answer.truncated ? "Pokazano " + matches + " najlepszych dopasowań" : "Znaleziono " + matches + (matches === 1 ? " pozycję" : " pozycji")) + " w miejscu, w którym występują w słowniku.");
      }
    }).catch(function (error) { if (error.name !== "AbortError") setStatus(failure(error.code), true); });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var query = clean(input.value);
    if (!query) { input.focus(); return; }
    if (query.length > 120 || query.split(" ").length > 8) { setStatus("Wpisz najwyżej 8 słów i 120 znaków.", true); return; }
    narrow(query);
  });
  reset.addEventListener("click", function () {
    if (searchController) searchController.abort();
    input.value = "";
    showRoots();
    detail.replaceChildren(element("p", "detail-empty", "Wybierz pozycję, aby zobaczyć jej opis, uwagi i miejsce w słowniku."));
    history.replaceState(null, "", window.location.pathname);
    input.focus();
  });
  window.addEventListener("hashchange", function () {
    var code = codeFromHash();
    if (code && (!selected || selected.dataset.code !== code)) openCode(code);
  });

  function codeFromHash() {
    var match = /^#kod=(.{1,32})$/.exec(window.location.hash);
    if (!match) return "";
    try { return decodeURIComponent(match[1]); } catch (error) { return ""; }
  }

  function takeHandoff() {
    try {
      var raw = window.sessionStorage.getItem(HANDOFF_KEY);
      window.sessionStorage.removeItem(HANDOFF_KEY);
      var value = JSON.parse(raw || "null");
      return value && value.system === system && typeof value.query === "string" ? clean(value.query) : "";
    } catch (error) { return ""; }
  }

  fetchJson("/api/v1/releases")
    .then(function (answer) {
      release = (answer.releases || []).filter(function (candidate) { return candidate.system === system; })[0];
      if (!release) throw Object.assign(new Error("missing release"), { code: "unavailable" });
      lead.textContent = lead.textContent.replace(/\.$/, "") + ", wersja " + release.source_version + ". Wydawca: " + release.publisher + ".";
      return node(0);
    })
    .then(function (answer) {
      roots = answer;
      cache.delete(0);
      showRoots();
      var handoff = takeHandoff();
      var code = codeFromHash();
      if (handoff) { input.value = handoff; return narrow(handoff); }
      if (code) return openCode(code);
    })
    .catch(function (error) { setStatus(failure(error.code), true); });
})();
