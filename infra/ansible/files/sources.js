(function () {
  "use strict";

  var PROVENANCE_PATH = "/api/v1/provenance/rsk";
  var MAX_RESPONSE_BYTES = 8 * 1024 * 1024;
  var MAX_RESPONSE_CHUNKS = 65536;
  var MAX_ENTRIES = 8192;
  var MAX_REVISIONS = 512;
  var MAX_NOTICES = 128;
  var PAGE_SIZE = 75;
  var FILTER_DELAY_MS = 150;
  var CURRENT_MAX_AGE_MS = 36 * 60 * 60 * 1000;
  var SEVERE_MAX_AGE_MS = 72 * 60 * 60 * 1000;
  var FUTURE_CLOCK_SKEW_MS = 60 * 60 * 1000;
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
  var STATUS_LABELS = {
    current: "Bieżący",
    historical: "Historyczny",
    missing: "Brakujący",
  };
  var ENTRY_KEYS = [
    "archive_status", "download_url", "downloadable", "extension",
    "file_created_at", "file_name", "file_uuid", "first_observed_at", "hcd",
    "last_observed_at", "metadata_observation_sha256", "notices", "origin_url",
    "product_sha256", "publisher_name", "publisher_short_name", "resource_name",
    "resource_short_name", "resource_type", "resource_version", "size_bytes",
    "source_archived_at", "status", "type_label", "valid_from", "valid_to",
  ];
  var state = { entries: [], status: "all", query: "", page: 1, filterTimer: null };

  function fail(label) {
    throw new Error("Nieprawidłowe dane: " + label + ".");
  }

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

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
    if (
      typeof value !== "string" || value.length < 1 || value.length > maximum ||
      CONTROL_PATTERN.test(value)
    ) fail(label);
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
    if (!Array.isArray(value) || value.length < minimum || value.length > maximum) {
      fail(label);
    }
    return value;
  }

  function expectTimestamp(value, label) {
    expectString(value, label, 32);
    var match = TIMESTAMP_PATTERN.exec(value);
    if (match === null) fail(label);
    var date = new Date(0);
    date.setUTCHours(Number(match[4]), Number(match[5]), Number(match[6]), 0);
    date.setUTCFullYear(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    var parts = [
      date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate(),
      date.getUTCHours(), date.getUTCMinutes(), date.getUTCSeconds(),
    ];
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
    return value.map(function (notice, index) {
      return expectString(notice, label + "[" + index + "]", MAX_TEXT_LENGTH);
    });
  }

  function officialOriginUrl(fileUuid) {
    return "https://" + OFFICIAL_HOST + OFFICIAL_PATH + fileUuid;
  }

  function expectOrigin(value, fileUuid) {
    expectString(value, "adres źródłowy", 256);
    var parsed;
    try {
      parsed = new URL(value);
    } catch (_error) {
      fail("adres źródłowy");
    }
    if (
      parsed.protocol !== "https:" || parsed.hostname !== OFFICIAL_HOST ||
      parsed.port !== "" || parsed.username !== "" || parsed.password !== "" ||
      parsed.pathname !== OFFICIAL_PATH + fileUuid || parsed.search !== "" ||
      parsed.hash !== "" || value !== officialOriginUrl(fileUuid)
    ) fail("adres źródłowy");
    return value;
  }

  function validateEntry(value, index) {
    var label = "wpis " + index;
    expectKeys(value, ENTRY_KEYS, label);
    var fileUuid = expectPattern(value.file_uuid, UUID_PATTERN, label + " UUID", 36);
    expectPattern(value.hcd, HCD_PATTERN, label + " HCD", 6);
    ["resource_name", "resource_short_name", "resource_version"].forEach(function (key) {
      expectString(value[key], label + " " + key, MAX_TEXT_LENGTH);
    });
    ["publisher_name", "publisher_short_name", "resource_type", "file_name", "type_label"].forEach(function (key) {
      expectNullableString(value[key], label + " " + key, MAX_TEXT_LENGTH);
    });
    if (value.extension !== null) {
      expectPattern(value.extension, EXTENSION_PATTERN, label + " rozszerzenie", 32);
    }
    if (value.size_bytes !== null) {
      expectInteger(value.size_bytes, label + " rozmiar", 0, MAX_FILE_BYTES);
    }
    ["valid_from", "first_observed_at", "last_observed_at"].forEach(function (key) {
      value[key] = expectTimestamp(value[key], label + " " + key);
    });
    ["valid_to", "file_created_at", "source_archived_at"].forEach(function (key) {
      value[key] = expectNullableTimestamp(value[key], label + " " + key);
    });
    if (value.valid_to !== null && value.valid_from > value.valid_to) fail(label + " ważność");
    if (value.first_observed_at > value.last_observed_at) fail(label + " obserwacja");
    expectPattern(value.metadata_observation_sha256, DIGEST_PATTERN, label + " SHA-256 metadanych", 64);
    if (value.product_sha256 !== null) {
      expectPattern(value.product_sha256, DIGEST_PATTERN, label + " SHA-256 produktu", 64);
    }
    if (!["current", "historical", "missing"].includes(value.status)) fail(label + " status");
    if (!["metadata_only", "privately_archived_verified"].includes(value.archive_status)) fail(label + " archiwizacja");
    var isArchived = value.archive_status === "privately_archived_verified";
    if (isArchived !== (value.product_sha256 !== null)) fail(label + " suma produktu");
    if (value.downloadable !== false || value.download_url !== null) fail(label + " pobieranie");
    expectOrigin(value.origin_url, fileUuid);
    value.notices = expectNotices(value.notices, label + " komunikaty");
    return value;
  }

  function validateProjection(value) {
    expectKeys(value, [
      "entries", "history_truncated", "included_revisions", "notices", "projected_at",
      "redistribution", "schema_version", "source", "through_state_revision",
    ], "projekcja");
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

  async function readBoundedJson(response) {
    var contentLength = response.headers.get("content-length");
    if (contentLength !== null) {
      if (!/^[0-9]+$/.test(contentLength)) fail("rozmiar odpowiedzi");
      expectInteger(Number(contentLength), "rozmiar odpowiedzi", 1, MAX_RESPONSE_BYTES);
    }
    if (response.body === null) fail("treść odpowiedzi");
    var reader = response.body.getReader();
    var decoder = new TextDecoder("utf-8", { fatal: true });
    var parts = [];
    var receivedBytes = 0;
    var complete = false;
    try {
      for (var chunk = 0; chunk < MAX_RESPONSE_CHUNKS; chunk += 1) {
        var result = await reader.read();
        if (result.done) {
          complete = true;
          break;
        }
        receivedBytes += result.value.byteLength;
        if (receivedBytes > MAX_RESPONSE_BYTES) fail("rozmiar odpowiedzi");
        parts.push(decoder.decode(result.value, { stream: true }));
      }
      if (!complete) fail("liczba części odpowiedzi");
      parts.push(decoder.decode());
    } catch (error) {
      if (!complete) {
        await reader.cancel();
      }
      throw error;
    }
    try {
      return JSON.parse(parts.join(""));
    } catch (_error) {
      fail("JSON");
    }
  }

  function element(name, className, text) {
    var node = document.createElement(name);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function addDetail(list, term, description, className) {
    var wrapper = element("div");
    wrapper.append(element("dt", "", term));
    wrapper.append(element("dd", className || "", description));
    list.append(wrapper);
  }

  function formatDate(value) {
    if (value === null) return "Nie podano";
    return new Intl.DateTimeFormat("pl-PL", {
      dateStyle: "medium", timeStyle: "medium", timeZone: "Europe/Warsaw",
    }).format(new Date(value));
  }

  function formatBytes(value) {
    if (value === null) return "Nie podano";
    if (value < 1024) return value + " B";
    var units = ["KB", "MB", "GB"];
    var size = value;
    var unit = -1;
    do {
      size /= 1024;
      unit += 1;
    } while (size >= 1024 && unit < units.length - 1);
    return new Intl.NumberFormat("pl-PL", { maximumFractionDigits: 2 }).format(size) + " " + units[unit];
  }

  function tableCell(label) {
    var cell = element("td");
    cell.setAttribute("data-label", label);
    return cell;
  }

  function resourceCell(entry) {
    var cell = tableCell("Zasób");
    cell.append(element("span", "hcd", entry.hcd));
    cell.append(element("p", "primary-value", entry.resource_name));
    var details = element("dl", "detail-list");
    addDetail(details, "Nazwa skrócona", entry.resource_short_name);
    addDetail(details, "Wersja", entry.resource_version, "mono");
    addDetail(details, "Typ", entry.resource_type || "Nie podano");
    var publisher = entry.publisher_name || "Nie podano";
    if (entry.publisher_short_name !== null) publisher += " (" + entry.publisher_short_name + ")";
    addDetail(details, "Wydawca", publisher);
    cell.append(details);
    return cell;
  }

  function fileCell(entry) {
    var cell = tableCell("Plik");
    cell.append(element("p", "primary-value", entry.file_name || "Plik bez nazwy"));
    var details = element("dl", "detail-list");
    addDetail(details, "UUID", entry.file_uuid, "mono");
    addDetail(details, "Typ", entry.type_label || "Nie podano");
    addDetail(details, "Rozszerzenie", entry.extension || "Nie podano", "mono");
    addDetail(details, "Rozmiar", formatBytes(entry.size_bytes));
    cell.append(details);
    return cell;
  }

  function datesCell(entry) {
    var cell = tableCell("Daty");
    var details = element("dl", "detail-list");
    addDetail(details, "Ważny od", formatDate(entry.valid_from));
    addDetail(details, "Ważny do", formatDate(entry.valid_to));
    addDetail(details, "Utworzenie pliku w RSK", formatDate(entry.file_created_at));
    addDetail(details, "Pierwsza obserwacja Minos", formatDate(entry.first_observed_at));
    addDetail(details, "Ostatnia obserwacja Minos", formatDate(entry.last_observed_at));
    if (entry.source_archived_at !== null) {
      addDetail(details, "Archiwizacja wskazana przez RSK", formatDate(entry.source_archived_at));
    }
    cell.append(details);
    return cell;
  }

  function integrityCell(entry) {
    var cell = tableCell("Pochodzenie i integralność");
    var details = element("dl", "detail-list");
    addDetail(details, "SHA-256 odpowiedzi metadanych", entry.metadata_observation_sha256, "digest");
    addDetail(details, "Archiwum Minos", entry.archive_status === "privately_archived_verified" ? "Prywatnie zarchiwizowane i zweryfikowane" : "Tylko metadane");
    if (entry.product_sha256 !== null) {
      addDetail(details, "SHA-256 produktu", entry.product_sha256, "digest");
    }
    cell.append(details);
    var link = element("a", "origin-link", "Otwórz plik na stronie wydawcy RSK");
    link.href = officialOriginUrl(entry.file_uuid);
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    cell.append(link);
    return cell;
  }

  function timelineRow(entry) {
    var row = element("tr");
    var statusCell = tableCell("Stan");
    statusCell.append(element("span", "status-badge status-" + entry.status, STATUS_LABELS[entry.status]));
    row.append(statusCell, resourceCell(entry), fileCell(entry), datesCell(entry), integrityCell(entry));
    return row;
  }

  function compareEntries(left, right) {
    var rank = { current: 0, historical: 1, missing: 2 };
    if (rank[left.status] !== rank[right.status]) return rank[left.status] - rank[right.status];
    if (left.last_observed_at !== right.last_observed_at) {
      return left.last_observed_at > right.last_observed_at ? -1 : 1;
    }
    var keys = ["hcd", "file_uuid", "metadata_observation_sha256"];
    for (var index = 0; index < keys.length; index += 1) {
      if (left[keys[index]] < right[keys[index]]) return -1;
      if (left[keys[index]] > right[keys[index]]) return 1;
    }
    return 0;
  }

  function visibleEntries() {
    return state.entries.filter(function (entry) {
      var matchesStatus = state.status === "all" || entry.status === state.status;
      var haystack = [entry.hcd, entry.resource_name, entry.resource_short_name]
        .join(" ").toLocaleLowerCase("pl-PL");
      return matchesStatus && haystack.includes(state.query);
    });
  }

  function renderTimeline() {
    var renderStartedAt = performance.now();
    var entries = visibleEntries();
    var pageCount = Math.max(1, Math.ceil(entries.length / PAGE_SIZE));
    state.page = Math.min(state.page, pageCount);
    var firstIndex = (state.page - 1) * PAGE_SIZE;
    var pageEntries = entries.slice(firstIndex, firstIndex + PAGE_SIZE);
    var body = document.getElementById("timeline-body");
    body.replaceChildren();
    pageEntries.forEach(function (entry) { body.append(timelineRow(entry)); });
    var count = entries.length === 1 ? "1 wynik" : entries.length + " wyników";
    document.getElementById("result-count").textContent = entries.length === 0
      ? count
      : count + ", pokazano " + (firstIndex + 1) + "-" + (firstIndex + pageEntries.length);
    var pagination = document.getElementById("pagination");
    pagination.hidden = entries.length <= PAGE_SIZE;
    document.getElementById("page-status").textContent = "Strona " + state.page + " z " + pageCount;
    document.getElementById("previous-page").disabled = state.page === 1;
    document.getElementById("next-page").disabled = state.page === pageCount;
    var filteredEmpty = entries.length === 0;
    document.getElementById("table-region").hidden = filteredEmpty;
    document.getElementById("empty-state").hidden = !filteredEmpty;
    if (filteredEmpty && state.entries.length > 0) {
      document.getElementById("empty-title").textContent = "Brak wyników dla tych filtrów.";
      document.getElementById("empty-detail").textContent = "Zmień status lub wyszukiwaną nazwę zasobu.";
    }
    document.getElementById("timeline").dataset.renderDurationMs = String(
      Math.ceil(performance.now() - renderStartedAt),
    );
  }

  function relativeAgeLabel(ageMs) {
    var ageDays = Math.floor(Math.max(0, ageMs) / 86400000);
    if (ageDays === 0) return "Dzisiaj";
    if (ageDays === 1) return "1 dzień temu";
    return ageDays + " dni temu";
  }

  function classifyFreshness(timestamp, nowMs) {
    var ageMs = nowMs - Date.parse(timestamp);
    if (ageMs < -FUTURE_CLOCK_SKEW_MS) {
      return { kind: "future", label: "Czas projekcji wyprzedza zegar urządzenia o ponad godzinę" };
    }
    if (ageMs <= CURRENT_MAX_AGE_MS) {
      return { kind: "current", label: "Aktualna - " + relativeAgeLabel(ageMs) };
    }
    if (ageMs <= SEVERE_MAX_AGE_MS) {
      return { kind: "stale", label: "Opóźniona o ponad 36 godzin - " + relativeAgeLabel(ageMs) };
    }
    return { kind: "severe", label: "Poważnie nieaktualna - " + relativeAgeLabel(ageMs) };
  }

  function renderNotices(projection) {
    var unique = new Set(projection.notices);
    projection.entries.forEach(function (entry) {
      entry.notices.forEach(function (notice) { unique.add(notice); });
    });
    var region = document.getElementById("public-notices");
    var list = document.getElementById("public-notice-list");
    list.replaceChildren();
    unique.forEach(function (notice) { list.append(element("li", "", notice)); });
    region.hidden = unique.size === 0;
  }

  function renderProjection(projection) {
    state.entries = projection.entries.slice().sort(compareEntries);
    state.page = 1;
    document.getElementById("revision-value").textContent = String(projection.through_state_revision);
    document.getElementById("projected-value").textContent = formatDate(projection.projected_at);
    var freshness = classifyFreshness(projection.projected_at, Date.now());
    var freshnessNode = document.getElementById("freshness-value");
    freshnessNode.className = "freshness freshness-" + freshness.kind;
    freshnessNode.textContent = freshness.label;
    document.getElementById("history-value").textContent = projection.history_truncated
      ? "Historia ograniczona do najnowszych pełnych rewizji"
      : "Pełny dostępny zakres: " + projection.included_revisions.length + " rewizji";
    document.getElementById("projection-facts").hidden = false;
    document.getElementById("controls").hidden = state.entries.length === 0;
    renderNotices(projection);
    renderTimeline();
  }

  function setLoadState(kind, message) {
    var node = document.getElementById("load-state");
    node.className = "load-state is-" + kind;
    node.textContent = message;
    node.setAttribute("role", kind === "error" ? "alert" : "status");
  }

  async function loadProjection() {
    setLoadState("loading", "Pobieranie publicznego rejestru pochodzenia danych...");
    document.getElementById("retry-button").hidden = true;
    var response;
    try {
      response = await fetch(PROVENANCE_PATH, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        method: "GET",
        redirect: "error",
        signal: AbortSignal.timeout(15000),
      });
    } catch (_error) {
      setLoadState("error", "Rejestr jest teraz niedostępny. Spróbuj ponownie później.");
      document.getElementById("retry-button").hidden = false;
      return;
    }
    if (!response.ok) {
      setLoadState("error", "Rejestr jest teraz niedostępny. Spróbuj ponownie później.");
      document.getElementById("retry-button").hidden = false;
      return;
    }
    try {
      var contentType = response.headers.get("content-type") || "";
      if (!contentType.toLocaleLowerCase("en-US").startsWith("application/json")) {
        fail("typ odpowiedzi");
      }
      var projection = validateProjection(await readBoundedJson(response));
      renderProjection(projection);
      setLoadState("ready", projection.entries.length === 0
        ? "Rejestr jest dostępny, ale nie zawiera jeszcze wpisów."
        : "Publiczny rejestr został poprawnie zweryfikowany.");
    } catch (_error) {
      setLoadState("error", "Odebrana projekcja ma nieprawidłowy format i nie została wyświetlona.");
      document.getElementById("retry-button").hidden = false;
    }
  }

  function bindControls() {
    function scheduleFilteredRender() {
      clearTimeout(state.filterTimer);
      state.page = 1;
      state.filterTimer = setTimeout(renderTimeline, FILTER_DELAY_MS);
    }
    document.getElementById("source-search").addEventListener("input", function (event) {
      state.query = event.target.value.trim().toLocaleLowerCase("pl-PL");
      scheduleFilteredRender();
    });
    document.querySelectorAll("[data-status]").forEach(function (button) {
      button.addEventListener("click", function () {
        document.querySelectorAll("[data-status]").forEach(function (candidate) {
          candidate.setAttribute("aria-pressed", candidate === button ? "true" : "false");
        });
        state.status = button.getAttribute("data-status");
        scheduleFilteredRender();
      });
    });
    document.getElementById("previous-page").addEventListener("click", function () {
      if (state.page > 1) state.page -= 1;
      renderTimeline();
    });
    document.getElementById("next-page").addEventListener("click", function () {
      state.page += 1;
      renderTimeline();
    });
    document.getElementById("retry-button").addEventListener("click", loadProjection);
  }

  if (typeof document !== "undefined") {
    bindControls();
    loadProjection();
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      classifyFreshness: classifyFreshness,
      officialOriginUrl: officialOriginUrl,
      validateProjection: validateProjection,
    };
  }
}());
