(function () {
  "use strict";

  var RSK_PATH = "/api/v1/provenance/rsk";
  var BAW_PATH = "/api/v1/provenance/baw";
  var MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
  var MAX_RESPONSE_CHUNKS = 65536;
  var MAX_ENTRIES = 8192;
  var MAX_REVISIONS = 512;
  var MAX_NOTICES = 128;
  var MAX_TEXT_LENGTH = 512;
  var MAX_FILE_BYTES = 512 * 1024 * 1024;
  var OFFICIAL_HOST = "rsk3.ezdrowie.gov.pl";
  var OFFICIAL_PATH = "/api/rsk/file/get/";
  var DIGEST_PATTERN = /^[0-9a-f]{64}$/;
  var HCD_PATTERN = /^[0-9A-Z]{6}$/;
  var UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  var EXTENSION_PATTERN = /^[a-z0-9][a-z0-9._+-]{0,31}$/;
  var TIMESTAMP_PATTERN = /^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?Z$/;
  var CONTROL_PATTERN = /\p{C}/u;
  var state = { rsk: null, baw: null, source: "all", query: "", errors: [] };

  function fail(label) { throw new Error("Nieprawidłowe dane: " + label + "."); }
  function isObject(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
  function expectKeys(value, expected, label) {
    if (!isObject(value)) fail(label);
    var actual = Object.keys(value).sort();
    var wanted = expected.slice().sort();
    if (actual.length !== wanted.length) fail(label);
    for (var index = 0; index < wanted.length; index += 1) {
      if (actual[index] !== wanted[index]) fail(label);
    }
  }
  function expectString(value, label, maximum) {
    if (typeof value !== "string" || value.length < 1 || value.length > maximum || CONTROL_PATTERN.test(value)) fail(label);
    return value;
  }
  function expectNullableString(value, label, maximum) {
    return value === null ? null : expectString(value, label, maximum);
  }
  function expectInteger(value, label, minimum, maximum) {
    if (!Number.isInteger(value) || value < minimum || value > maximum) fail(label);
    return value;
  }
  function expectArray(value, label, minimum, maximum) {
    if (!Array.isArray(value) || value.length < minimum || value.length > maximum) fail(label);
    return value;
  }
  function expectTimestamp(value, label) {
    expectString(value, label, 32);
    var match = TIMESTAMP_PATTERN.exec(value);
    if (match === null) fail(label);
    var date = new Date(0);
    date.setUTCHours(Number(match[4]), Number(match[5]), Number(match[6]), 0);
    date.setUTCFullYear(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    var parts = [date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate(), date.getUTCHours(), date.getUTCMinutes(), date.getUTCSeconds()];
    for (var index = 0; index < parts.length; index += 1) {
      if (parts[index] !== Number(match[index + 1])) fail(label);
    }
    if (Number(match[1]) < 1) fail(label);
    var fraction = (match[7] || "").padEnd(6, "0");
    return value.slice(0, 19) + "." + fraction + "Z";
  }
  function expectNullableTimestamp(value, label) {
    return value === null ? null : expectTimestamp(value, label);
  }
  function expectPattern(value, pattern, label, maximum) {
    expectString(value, label, maximum);
    if (!pattern.test(value)) fail(label);
    return value;
  }
  function expectNotices(value, label) {
    expectArray(value, label, 0, 16);
    return value.map(function (notice, index) { return expectString(notice, label + "[" + index + "]", MAX_TEXT_LENGTH); });
  }
  function officialOriginUrl(fileUuid) {
    return "https://" + OFFICIAL_HOST + OFFICIAL_PATH + fileUuid;
  }
  function expectOrigin(value, fileUuid) {
    expectString(value, "adres źródłowy", 256);
    var parsed;
    try { parsed = new URL(value); } catch (_error) { fail("adres źródłowy"); }
    if (parsed.protocol !== "https:" || parsed.hostname !== OFFICIAL_HOST || parsed.port !== "" || parsed.username !== "" || parsed.password !== "" || parsed.pathname !== OFFICIAL_PATH + fileUuid || parsed.search !== "" || parsed.hash !== "" || value !== officialOriginUrl(fileUuid)) fail("adres źródłowy");
    return value;
  }
  function validateEntry(value, index) {
    var label = "wpis " + index;
    expectKeys(value, [
      "archive_status", "download_url", "downloadable", "extension", "file_created_at", "file_name", "file_uuid",
      "first_observed_at", "hcd", "last_observed_at", "metadata_observation_sha256", "notices", "origin_url",
      "product_sha256", "publisher_name", "publisher_short_name", "resource_name", "resource_short_name",
      "resource_type", "resource_version", "size_bytes", "source_archived_at", "status", "type_label", "valid_from", "valid_to",
    ], label);
    var fileUuid = expectPattern(value.file_uuid, UUID_PATTERN, label + " UUID", 36);
    expectPattern(value.hcd, HCD_PATTERN, label + " HCD", 6);
    ["resource_name", "resource_short_name", "resource_version"].forEach(function (key) { expectString(value[key], label + " " + key, MAX_TEXT_LENGTH); });
    ["publisher_name", "publisher_short_name", "resource_type", "file_name", "type_label"].forEach(function (key) { expectNullableString(value[key], label + " " + key, MAX_TEXT_LENGTH); });
    if (value.extension !== null) expectPattern(value.extension, EXTENSION_PATTERN, label + " rozszerzenie", 32);
    if (value.size_bytes !== null) expectInteger(value.size_bytes, label + " rozmiar", 0, MAX_FILE_BYTES);
    ["valid_from", "first_observed_at", "last_observed_at"].forEach(function (key) { value[key] = expectTimestamp(value[key], label + " " + key); });
    ["valid_to", "file_created_at", "source_archived_at"].forEach(function (key) { value[key] = expectNullableTimestamp(value[key], label + " " + key); });
    if (value.valid_to !== null && value.valid_from > value.valid_to) fail(label + " ważność");
    if (value.first_observed_at > value.last_observed_at) fail(label + " obserwacja");
    expectPattern(value.metadata_observation_sha256, DIGEST_PATTERN, label + " SHA-256 metadanych", 64);
    if (value.product_sha256 !== null) expectPattern(value.product_sha256, DIGEST_PATTERN, label + " SHA-256 produktu", 64);
    if (!["current", "historical", "missing"].includes(value.status)) fail(label + " status");
    if (!["metadata_only", "privately_archived_verified"].includes(value.archive_status)) fail(label + " archiwizacja");
    if ((value.archive_status === "privately_archived_verified") !== (value.product_sha256 !== null)) fail(label + " suma produktu");
    if (value.downloadable !== false || value.download_url !== null) fail(label + " pobieranie");
    expectOrigin(value.origin_url, fileUuid);
    value.notices = expectNotices(value.notices, label + " komunikaty");
    return value;
  }
  function validateProjection(value) {
    expectKeys(value, ["entries", "history_truncated", "included_revisions", "notices", "projected_at", "redistribution", "schema_version", "source", "through_state_revision"], "projekcja");
    if (value.schema_version !== 1 || value.source !== "RSK") fail("wersja projekcji");
    expectInteger(value.through_state_revision, "rewizja", 1, Number.MAX_SAFE_INTEGER);
    value.projected_at = expectTimestamp(value.projected_at, "czas projekcji");
    if (typeof value.history_truncated !== "boolean") fail("zakres historii");
    expectArray(value.included_revisions, "rewizje", 1, MAX_REVISIONS);
    var previous = 0;
    value.included_revisions.forEach(function (revision) {
      expectInteger(revision, "rewizja historii", 1, Number.MAX_SAFE_INTEGER);
      if (revision <= previous) fail("kolejność rewizji");
      previous = revision;
    });
    if (previous !== value.through_state_revision) fail("ogrodzenie rewizji");
    expectKeys(value.redistribution, ["artifacts", "schema_version"], "redystrybucja");
    if (value.redistribution.schema_version !== 1) fail("wersja redystrybucji");
    expectArray(value.redistribution.artifacts, "artefakty", 0, 0);
    value.notices = expectNotices(value.notices, "komunikaty projekcji");
    expectArray(value.entries, "wpisy", 0, MAX_ENTRIES);
    value.entries = value.entries.map(validateEntry);
    var uniqueNotices = new Set(value.notices);
    value.entries.forEach(function (entry) {
      entry.notices.forEach(function (notice) { uniqueNotices.add(notice); });
      if (uniqueNotices.size > MAX_NOTICES) fail("liczba unikalnych komunikatów");
    });
    return value;
  }

  function expectBawDate(value, label) {
    if (value === null) return null;
    expectPattern(value, /^\d{4}-\d{2}-\d{2}$/, label, 10);
    var date = new Date(value + "T00:00:00Z");
    if (!Number.isFinite(date.valueOf()) || date.toISOString().slice(0, 10) !== value) fail(label);
    return value;
  }
  function validateBawProjection(value) {
    expectKeys(value, ["schema_version", "source", "through_state_revision", "observed_at", "documents", "redistribution"], "BAW");
    if (value.schema_version !== 1 || value.source !== "BAW") fail("wersja BAW");
    expectInteger(value.through_state_revision, "rewizja BAW", 1, Number.MAX_SAFE_INTEGER);
    value.observed_at = expectTimestamp(value.observed_at, "odczyt BAW");
    expectKeys(value.redistribution, ["schema_version", "artifacts"], "redystrybucja BAW");
    if (value.redistribution.schema_version !== 1) fail("wersja redystrybucji BAW");
    expectArray(value.redistribution.artifacts, "artefakty BAW", 0, 0);
    expectArray(value.documents, "dokumenty BAW", 1, 32);
    var ids = new Set();
    value.documents.forEach(function (doc) {
      expectKeys(doc, ["document_id", "act_number", "act_date", "published_date", "entry_into_force", "service_applicability_date", "valid_to", "legal_status", "observed_at", "metadata_sha256", "source_url", "files"], "dokument BAW");
      expectInteger(doc.document_id, "ID BAW", 1, Number.MAX_SAFE_INTEGER);
      if (ids.has(doc.document_id)) fail("powtórzony ID BAW");
      ids.add(doc.document_id);
      expectString(doc.act_number, "numer aktu", MAX_TEXT_LENGTH);
      expectString(doc.legal_status, "status aktu", MAX_TEXT_LENGTH);
      ["act_date", "published_date", "entry_into_force", "service_applicability_date", "valid_to"].forEach(function (name) { expectBawDate(doc[name], name); });
      doc.observed_at = expectTimestamp(doc.observed_at, "odczyt dokumentu");
      expectPattern(doc.metadata_sha256, DIGEST_PATTERN, "suma metadanych", 64);
      var officialUrl = "https://baw.nfz.gov.pl/NFZ/document/" + doc.document_id + "/Zarzadzenie-" + doc.act_number.replace(/\//g, "_");
      if (doc.source_url !== null && (!/^[1-9]\d{0,3}\/20\d{2}\/DSOZ$/.test(doc.act_number) || doc.source_url !== officialUrl)) fail("adres BAW");
      expectArray(doc.files, "pliki BAW", 0, 128);
      var files = new Set();
      doc.files.forEach(function (file) {
        expectKeys(file, ["file_id", "name", "size_bytes", "sha256"], "plik BAW");
        expectInteger(file.file_id, "ID pliku", 1, Number.MAX_SAFE_INTEGER);
        if (files.has(file.file_id)) fail("powtórzony plik BAW");
        files.add(file.file_id);
        expectString(file.name, "nazwa pliku", MAX_TEXT_LENGTH);
        expectInteger(file.size_bytes, "rozmiar pliku", 1, MAX_FILE_BYTES);
        expectPattern(file.sha256, DIGEST_PATTERN, "SHA-256 pliku", 64);
      });
    });
    return value;
  }

  async function readBoundedJson(response) {
    var length = response.headers.get("content-length");
    if (length !== null) {
      if (!/^[0-9]+$/.test(length)) fail("rozmiar odpowiedzi");
      expectInteger(Number(length), "rozmiar odpowiedzi", 1, MAX_RESPONSE_BYTES);
    }
    if (response.body === null) fail("treść odpowiedzi");
    var reader = response.body.getReader();
    var decoder = new TextDecoder("utf-8", { fatal: true });
    var parts = [];
    var received = 0;
    var complete = false;
    try {
      for (var chunk = 0; chunk < MAX_RESPONSE_CHUNKS; chunk += 1) {
        var result = await reader.read();
        if (result.done) { complete = true; break; }
        received += result.value.byteLength;
        if (received > MAX_RESPONSE_BYTES) fail("rozmiar odpowiedzi");
        parts.push(decoder.decode(result.value, { stream: true }));
      }
      if (!complete) fail("liczba części odpowiedzi");
      parts.push(decoder.decode());
    } catch (error) {
      if (!complete) await reader.cancel();
      throw error;
    }
    if (length !== null && Number(length) !== received) fail("rozmiar odpowiedzi");
    try { return JSON.parse(parts.join("")); } catch (_error) { fail("JSON"); }
  }

  function normalizeSearch(value) {
    return value.toLocaleLowerCase("pl-PL").normalize("NFD").replace(/\p{M}/gu, "").replace(/ł/g, "l").replace(/[^a-z0-9]+/g, "");
  }
  function formatDate(value) {
    if (value === null) return "Nie podano";
    return new Intl.DateTimeFormat("pl-PL", { dateStyle: "medium", timeZone: "Europe/Warsaw" }).format(new Date(value.length === 10 ? value + "T12:00:00Z" : value));
  }
  function formatSize(value) {
    if (value === null) return "Nie podano";
    return value < 1024 * 1024 ? Math.round(value / 1024) + " KB" : new Intl.NumberFormat("pl-PL", { maximumFractionDigits: 1 }).format(value / 1024 / 1024) + " MB";
  }
  function countLabel(count) {
    if (count === 1) return "1 zestaw";
    var ending = count % 10;
    return count + (ending >= 2 && ending <= 4 && (count % 100 < 12 || count % 100 > 14) ? " zestawy" : " zestawów");
  }
  function element(name, className, text) {
    var node = document.createElement(name);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function fact(list, label, value, className) {
    var wrapper = element("div");
    wrapper.append(element("dt", "", label), element("dd", className || "", value));
    list.append(wrapper);
  }
  function rskDatasets(projection) {
    var grouped = new Map();
    projection.entries.forEach(function (entry) {
      if (!grouped.has(entry.hcd)) grouped.set(entry.hcd, []);
      grouped.get(entry.hcd).push(entry);
    });
    return Array.from(grouped, function (pair) {
      var entries = pair[1].slice().sort(function (left, right) { return right.last_observed_at.localeCompare(left.last_observed_at); });
      var latest = entries[0];
      var byUuid = new Map();
      entries.forEach(function (entry) {
        if (!byUuid.has(entry.file_uuid)) byUuid.set(entry.file_uuid, entry);
      });
      var files = Array.from(byUuid.values());
      var notices = Array.from(new Set(entries.flatMap(function (entry) { return entry.notices; })));
      return {
        source: "rsk", id: latest.hcd, title: latest.resource_name,
        subtitle: latest.publisher_name || "Wydawca niepodany w RSK",
        version: latest.resource_version, observed: latest.last_observed_at,
        validity: latest.valid_to, status: latest.status, files: files,
        metadataSha256: latest.metadata_observation_sha256, notices: notices,
        search: normalizeSearch([latest.hcd, latest.resource_name, latest.resource_short_name, latest.publisher_name, latest.publisher_short_name, latest.resource_version, files.map(function (file) { return [file.file_name, file.extension, file.type_label].join(" "); }).join(" ")].join(" ")),
      };
    });
  }
  function bawDatasets(projection) {
    return projection.documents.map(function (doc) {
      return {
        source: "baw", id: String(doc.document_id), title: "Zarządzenie " + doc.act_number,
        subtitle: "Baza Aktów Własnych / Narodowy Fundusz Zdrowia",
        version: doc.legal_status, observed: doc.observed_at,
        validity: doc.valid_to, status: "observed", files: doc.files, document: doc,
        metadataSha256: doc.metadata_sha256,
        search: normalizeSearch([doc.document_id, doc.act_number, doc.legal_status, "BAW NFZ Narodowy Fundusz Zdrowia", doc.files.map(function (file) { return file.name; }).join(" ")].join(" ")),
      };
    });
  }
  function fileList(card) {
    var list = element("ul", "file-list");
    card.files.forEach(function (file) {
      var row = element("li");
      row.append(element("strong", "", file.file_name || file.name || "Plik bez nazwy"));
      row.append(element("small", "", formatSize(file.size_bytes) + " · " + (card.source === "rsk" ? (file.extension || "format niepodany") : "zapisany prywatnie")));
      var digest = card.source === "rsk" ? file.product_sha256 : file.sha256;
      if (digest) row.append(element("span", "technical", "SHA-256: " + digest));
      if (card.source === "rsk") {
        var link = element("a", "card-link", "Otwórz " + (file.file_name || "plik") + " w RSK");
        link.href = officialOriginUrl(file.file_uuid);
        link.rel = "noopener noreferrer";
        link.target = "_blank";
        row.append(link);
      }
      list.append(row);
    });
    return list;
  }
  function renderCard(card) {
    var article = element("article", "source-card");
    var overline = element("div", "card-overline");
    overline.append(element("span", "source-tag", card.source.toUpperCase()), element("span", "", card.source === "rsk" ? "Zasób " + card.id : "Dokument " + card.id));
    article.append(overline, element("h3", "", card.title), element("p", "card-lead", card.subtitle));
    var facts = element("dl", "card-facts");
    fact(facts, card.source === "rsk" ? "Wersja według RSK" : "Stan aktu w BAW", card.version);
    fact(facts, "Ostatni odczyt Minos", formatDate(card.observed));
    fact(facts, "Obecność w obserwacji", card.status === "current" || card.status === "observed" ? "W ostatnim odczycie" : card.status === "missing" ? "Nieobecny w ostatnim odczycie" : "Historyczny");
    fact(facts, "Ważność według wydawcy", card.validity === null ? "Daty końcowej nie podano" : "Do " + formatDate(card.validity), card.validity !== null && Date.parse(card.validity) < Date.now() ? "is-expired" : "");
    article.append(facts);
    if (card.source === "baw") {
      article.append(element("p", "card-notice", "Okres stosowania do świadczeń nie został jeszcze niezależnie ustalony. Archiwum Minos nie udostępnia tych plików do pobrania."));
    }
    var details = element("details");
    details.append(element("summary", "", "Pliki (" + card.files.length + ") i pochodzenie"));
    if (card.files.length) details.append(fileList(card));
    else details.append(element("p", "card-notice", "Brak osobno archiwizowanych plików dla tego dokumentu."));
    if (card.notices && card.notices.length) {
      var notices = element("ul", "file-list");
      card.notices.forEach(function (notice) { notices.append(element("li", "", notice)); });
      details.append(element("p", "card-notice", "Komunikaty źródła:"), notices);
    }
    details.append(element("span", "technical", "Odczyt (UTC): " + card.observed + " · SHA-256 odpowiedzi metadanych: " + card.metadataSha256));
    article.append(details);
    if (card.source === "baw" && card.document.source_url !== null) {
      var sourceLink = element("a", "card-link", "Otwórz dokument w BAW NFZ");
      sourceLink.href = card.document.source_url;
      sourceLink.rel = "noopener noreferrer";
      sourceLink.target = "_blank";
      article.append(sourceLink);
    }
    return article;
  }
  function allCards() {
    var cards = [];
    if (state.rsk) cards.push.apply(cards, rskDatasets(state.rsk));
    if (state.baw) cards.push.apply(cards, bawDatasets(state.baw));
    return cards.sort(function (left, right) { return left.title.localeCompare(right.title, "pl-PL"); });
  }
  function render() {
    var cards = allCards().filter(function (card) {
      return (state.source === "all" || state.source === card.source) && card.search.includes(state.query);
    });
    var result = document.getElementById("catalog-results");
    result.replaceChildren();
    cards.forEach(function (card) { result.append(renderCard(card)); });
    document.getElementById("result-count").textContent = countLabel(cards.length);
    document.getElementById("empty-state").hidden = cards.length !== 0;
    var summary = document.getElementById("source-summary");
    summary.replaceChildren();
    [["RSK", state.rsk], ["BAW NFZ", state.baw]].forEach(function (pair) {
      var text = pair[1] ? "Odczyt " + formatDate(pair[1].projected_at || pair[1].observed_at) : "Brak dostępnej obserwacji";
      var node = element("p");
      node.append(element("strong", "", pair[0] + ": "), document.createTextNode(text));
      summary.append(node);
    });
  }
  function setLoadState(kind, text) {
    var node = document.getElementById("load-state");
    node.className = "load-state is-" + kind;
    node.textContent = text;
    node.setAttribute("role", kind === "error" ? "alert" : "status");
  }
  async function loadOne(path, validator) {
    var response = await fetch(path, {
      credentials: "same-origin", headers: { Accept: "application/json" },
      method: "GET", redirect: "error", signal: AbortSignal.timeout(15000),
    });
    if (!response.ok || !(response.headers.get("content-type") || "").toLowerCase().startsWith("application/json")) fail("odpowiedź źródła");
    return validator(await readBoundedJson(response));
  }
  async function loadAll() {
    setLoadState("loading", "Pobieranie obserwacji źródeł...");
    var responses = await Promise.allSettled([
      loadOne(RSK_PATH, validateProjection),
      loadOne(BAW_PATH, validateBawProjection),
    ]);
    state.rsk = responses[0].status === "fulfilled" ? responses[0].value : null;
    state.baw = responses[1].status === "fulfilled" ? responses[1].value : null;
    state.errors = responses.map(function (response, index) { return response.status === "rejected" ? (index === 0 ? "RSK" : "BAW") : null; }).filter(Boolean);
    document.getElementById("retry-button").hidden = state.errors.length === 0;
    if (state.errors.length) {
      setLoadState("error", "Nie udało się wczytać: " + state.errors.join(", ") + ". Pozostałe źródła nadal są dostępne.");
    } else {
      setLoadState("ready", "Wczytano publiczne obserwacje RSK i BAW. Sumy i metadane nie oznaczają zatwierdzenia treści.");
    }
    render();
  }
  function selectTab(button) {
    document.querySelectorAll("[data-source]").forEach(function (tab) {
      var selected = tab === button;
      tab.setAttribute("aria-selected", selected ? "true" : "false");
      tab.tabIndex = selected ? 0 : -1;
    });
    state.source = button.getAttribute("data-source");
    document.getElementById("catalog-results").setAttribute("aria-labelledby", button.id);
    render();
  }
  function bind() {
    var tabs = Array.from(document.querySelectorAll("[data-source]"));
    tabs.forEach(function (button) {
      button.id = "tab-" + button.getAttribute("data-source");
      button.addEventListener("click", function () { selectTab(button); });
      button.addEventListener("keydown", function (event) {
        var index = tabs.indexOf(button);
        var next = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : -1;
        if (next < 0) return;
        event.preventDefault();
        selectTab(tabs[next]);
        tabs[next].focus();
      });
    });
    selectTab(tabs[0]);
    document.getElementById("source-search").addEventListener("input", function (event) {
      state.query = normalizeSearch(event.target.value.trim());
      render();
    });
    document.getElementById("retry-button").addEventListener("click", loadAll);
    loadAll();
  }
  function classifyFreshness(timestamp, nowMs) {
    var ageMs = nowMs - Date.parse(timestamp);
    if (ageMs < -60 * 60 * 1000) return { kind: "future" };
    if (ageMs <= 36 * 60 * 60 * 1000) return { kind: "current" };
    if (ageMs <= 72 * 60 * 60 * 1000) return { kind: "stale" };
    return { kind: "severe" };
  }
  if (typeof document !== "undefined") bind();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      classifyFreshness: classifyFreshness,
      officialOriginUrl: officialOriginUrl,
      validateProjection: validateProjection,
      validateBawProjection: validateBawProjection,
      normalizeSearch: normalizeSearch,
    };
  }
}());
