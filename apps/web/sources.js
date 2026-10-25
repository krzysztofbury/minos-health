(function () {
  "use strict";
  var MiniSearch = typeof module !== "undefined" && module.exports ? module.exports : globalThis.MiniSearch;

  var RSK_PATH = "/api/v1/provenance/rsk";
  var BAW_PATH = "/api/v1/provenance/baw";
  var NFZ_PATH = "/api/v1/provenance/nfz";
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
  var state = { rsk: null, baw: null, nfz: null, source: "all", publisher: "all", format: "all", sort: "relevance", page: 1, query: "", errors: [], cards: [], catalog: null, sourcePages: false };
  var PUBLISHERS = { nfz: "Narodowy Fundusz Zdrowia", "who-pl": "Biuro Światowej Organizacji Zdrowia w Polsce", who: "World Health Organization", ec: "Komisja Europejska", snomed: "SNOMED International", aotmit: "AOTMiT", other: "Pozostali / niepodani" };
  var FORMATS = ["xml", "xlsx", "xls", "pdf", "docx", "doc", "zip", "zipx", "html", "other"];
  var PAGE_SIZE = 50;
  var NFZ_CHANNELS = { order: "Publikacja zarządzenia NFZ", news: "Aktualności NFZ", project: "Projekt zarządzenia", jgp: "Komunikaty JGP", czs: "Słowniki CZS NFZ", icd9_companion: "Plik ICD-9 PL NFZ", aotmit: "AOTMiT - sygnał taryfikacji" };
  var BAW_ROLES = { 1: "Akt pierwotny", 3: "Tekst po zmianach", 4: "Porównanie zmian", 5: "Skrócone porównanie zmian", 6: "Akt zmieniający" };

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
    if (![1, 2].includes(value.schema_version) || value.source !== "BAW") fail("wersja BAW");
    expectInteger(value.through_state_revision, "rewizja BAW", 1, Number.MAX_SAFE_INTEGER);
    value.observed_at = expectTimestamp(value.observed_at, "odczyt BAW");
    expectKeys(value.redistribution, ["schema_version", "artifacts"], "redystrybucja BAW");
    if (value.redistribution.schema_version !== 1) fail("wersja redystrybucji BAW");
    expectArray(value.redistribution.artifacts, "artefakty BAW", 0, 0);
    expectArray(value.documents, "dokumenty BAW", 1, 32);
    var ids = new Set();
    value.documents.forEach(function (doc) {
      var expectedKeys = ["document_id", "act_number", "act_date", "published_date", "entry_into_force", "service_applicability_date", "valid_to", "legal_status", "observed_at", "metadata_sha256", "source_url", "files"];
      if (value.schema_version === 2) expectedKeys.push("lineage");
      if (Object.hasOwn(doc, "service_applicability_evidence")) expectedKeys.push("service_applicability_evidence");
      expectKeys(doc, expectedKeys, "dokument BAW");
      expectInteger(doc.document_id, "ID BAW", 1, Number.MAX_SAFE_INTEGER);
      if (ids.has(doc.document_id)) fail("powtórzony ID BAW");
      ids.add(doc.document_id);
      expectString(doc.act_number, "numer aktu", MAX_TEXT_LENGTH);
      expectString(doc.legal_status, "status aktu", MAX_TEXT_LENGTH);
      ["act_date", "published_date", "entry_into_force", "service_applicability_date", "valid_to"].forEach(function (name) { expectBawDate(doc[name], name); });
      if ((doc.service_applicability_date === null) !== !Object.hasOwn(doc, "service_applicability_evidence")) fail("dowód okresu stosowania");
      if (doc.service_applicability_date !== null) expectString(doc.service_applicability_evidence, "dowód okresu stosowania", MAX_TEXT_LENGTH);
      doc.observed_at = expectTimestamp(doc.observed_at, "odczyt dokumentu");
      expectPattern(doc.metadata_sha256, DIGEST_PATTERN, "suma metadanych", 64);
      var officialUrl = "https://baw.nfz.gov.pl/NFZ/document/" + doc.document_id + "/Zarzadzenie-" + doc.act_number.replace(/\//g, "_");
      var legacyUrl = "https://baw.nfz.gov.pl/NFZ/document/" + doc.document_id;
      if (doc.source_url !== null && doc.source_url !== legacyUrl && (!/^[1-9]\d{0,3}\/20\d{2}\/DSOZ$/.test(doc.act_number) || doc.source_url !== officialUrl)) fail("adres BAW");
      if (doc.source_url === legacyUrl) doc.source_url = null;
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
    if (value.schema_version === 2) validateBawLineage(value.documents);
    return value;
  }

  function validateBawLineage(documents) {
    var byId = new Map(documents.map(function (doc) { return [doc.document_id, doc]; }));
    documents.forEach(function (doc) {
      var link = doc.lineage;
      expectKeys(link, ["automatic_type", "source_document_id", "introductory_document_id"], "relacje BAW");
      if (![1, 3, 4, 5, 6].includes(link.automatic_type)) fail("rodzaj dokumentu BAW");
      [link.source_document_id, link.introductory_document_id].forEach(function (id) {
        if (id !== null) expectInteger(id, "relacja BAW", 1, Number.MAX_SAFE_INTEGER);
      });
      if (link.automatic_type === 1) {
        if (link.source_document_id !== null || link.introductory_document_id !== null) fail("akt pierwotny BAW");
        return;
      }
      var source = byId.get(link.source_document_id);
      if (link.source_document_id !== null && (!source || source.lineage.automatic_type !== 1)) fail("akt źródłowy BAW");
      if (link.automatic_type === 6) {
        if (link.introductory_document_id !== null) fail("akt zmieniający BAW");
        return;
      }
      var introductory = byId.get(link.introductory_document_id);
      if (!source || doc.act_number !== source.act_number || !introductory || introductory.lineage.automatic_type !== 6 || introductory.lineage.source_document_id !== source.document_id) fail("historia BAW");
    });
  }

  function expectNfzUrl(value, channel) {
    expectString(value, "adres NFZ", 1024);
    var paths = {
      order: "zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/",
      news: "aktualnosci/aktualnosci-centrali/",
      project: "zarzadzenia-prezesa/projekty-zarzadzen/",
      jgp: "dla-swiadczeniodawcy/jednorodne-grupy-pacjentow/komunikaty-jgp/",
    };
    var pattern = paths[channel] ? new RegExp("^https://www\\.nfz\\.gov\\.pl/" + paths[channel] + "[a-z0-9-]+,[1-9][0-9]{0,9}\\.html$") : {
      jgp_file: /^https:\/\/www\.nfz\.gov\.pl\/download\/gfx\/nfz\/pl\/defaultaktualnosci\/354\/[1-9][0-9]{0,9}\/[1-9][0-9]{0,2}\/[a-zA-Z0-9_.-]{3,160}\.(docx|xlsx)$/,
      icd9_companion: /^https:\/\/www\.nfz\.gov\.pl\/download\/gfx\/nfz\/pl\/defaultstronaopisowa\/782\/1\/2\/[a-zA-Z0-9_.-]{3,140}\.xlsx$/,
      czs: /^https:\/\/slowniki\.nfz\.gov\.pl\/WersjeSlownikow\/PobierzXml\?kodSlownika=(ICD9|MODTAR)&nrGen=[1-9][0-9]{0,5}$/,
      display: /^https:\/\/slowniki\.nfz\.gov\.pl\/WersjeSlownikow\/Index\/[1-9][0-9]{0,9}\?kod=MODTAR$/,
      aotmit: /^https:\/\/bip\.aotm\.gov\.pl\/assets\/files\/taryfikacja\/taryfy\/obwieszczenia\/20[0-9]{2}\/[\p{L}\p{N}_ .%-]{3,180}\.pdf$/u,
    }[channel];
    if (!pattern || !pattern.test(value)) fail("adres NFZ");
    var filename;
    try { filename = decodeURIComponent(value.split("?")[0].split("/").pop()); } catch (_error) { fail("adres NFZ"); }
    if (/\.\.|[\\/%?#]/.test(filename) || CONTROL_PATTERN.test(filename)) fail("ścieżka NFZ");
  }
  function expectNfzEvidence(value, channel, fence) {
    expectNfzUrl(value.source_url, channel);
    expectPattern(value.sha256, DIGEST_PATTERN, "SHA-256 NFZ", 64);
    expectPattern(value.observed_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/, "odczyt NFZ", 24);
    expectTimestamp(value.observed_at, "odczyt NFZ");
    if (Date.parse(value.observed_at) > Date.parse(fence)) fail("ogrodzenie NFZ");
  }
  function validateNfzProjection(value) {
    expectKeys(value, ["schema_version", "source", "through_state_revision", "observed_at", "coverage", "entries", "redistribution"], "NFZ");
    if (value.schema_version !== 1 || value.source !== "NFZ" || value.coverage !== "latest_reconciled_observation_only") fail("wersja NFZ");
    expectInteger(value.through_state_revision, "rewizja NFZ", 1, Number.MAX_SAFE_INTEGER);
    expectTimestamp(value.observed_at, "odczyt NFZ");
    expectKeys(value.redistribution, ["schema_version", "artifacts"], "redystrybucja NFZ");
    if (value.redistribution.schema_version !== 1) fail("redystrybucja NFZ");
    expectArray(value.redistribution.artifacts, "artefakty NFZ", 0, 0);
    expectArray(value.entries, "publikacje NFZ", 0, 256);
    var fields = { order: ["act_number", "baw_document_id", "published_date"], news: ["item_id", "published_date", "files"], project: ["item_id", "published_date", "files"], jgp: ["item_id", "published_date", "files"], czs: ["code", "generation", "generated_at", "year", "grouper_type", "version", "published_date", "publisher_valid_from", "display_evidence"], icd9_companion: ["version", "czs_xml_sha256"], aotmit: ["meaning", "published_date"] };
    var counts = {}, ids = new Set(), codes = new Set();
    value.entries.forEach(function (entry) {
      if (!Object.hasOwn(fields, entry.channel)) fail("kanał NFZ");
      expectKeys(entry, ["channel", "title", "source_url", "sha256", "observed_at", "service_applicability"].concat(fields[entry.channel]), "publikacja NFZ");
      expectNfzEvidence(entry, entry.channel, value.observed_at);
      expectString(entry.title, "tytuł NFZ", MAX_TEXT_LENGTH);
      if (entry.service_applicability !== "not_reviewed") fail("stosowanie NFZ");
      var id = entry.channel + ":" + entry.source_url;
      if (ids.has(id)) fail("powtórzona publikacja NFZ");
      ids.add(id); counts[entry.channel] = (counts[entry.channel] || 0) + 1;
      if (Object.hasOwn(entry, "published_date")) {
        if (entry.channel !== "czs" && entry.published_date === null) fail("data publikacji NFZ");
        expectBawDate(entry.published_date, "data publikacji NFZ");
      }
      if (entry.channel === "order") {
        expectPattern(entry.act_number, /^[1-9][0-9]{0,3}\/20[0-9]{2}\/DSOZ$/, "numer aktu", 16);
        expectInteger(entry.baw_document_id, "ID BAW", 1, 2147483647);
      } else if (["news", "project", "jgp"].includes(entry.channel)) {
        expectInteger(entry.item_id, "ID publikacji", 1, 2147483647);
        if (!entry.source_url.endsWith("," + entry.item_id + ".html")) fail("ID publikacji");
        expectArray(entry.files, "pliki JGP", 0, entry.channel === "jgp" ? 8 : 0);
        if (entry.channel === "jgp" && entry.files.length === 0) fail("brak plików JGP");
        var files = new Set();
        entry.files.forEach(function (file) {
          expectKeys(file, ["source_url", "sha256", "observed_at"], "plik JGP");
          expectNfzEvidence(file, "jgp_file", value.observed_at);
          if (!file.source_url.includes("/354/" + entry.item_id + "/") || files.has(file.source_url)) fail("powiązanie pliku JGP");
          files.add(file.source_url);
        });
      } else if (entry.channel === "czs") {
        if (!["ICD9", "MODTAR"].includes(entry.code) || codes.has(entry.code)) fail("słownik CZS");
        codes.add(entry.code);
        expectInteger(entry.generation, "generacja CZS", 1, 999999);
        if (entry.source_url !== "https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml?kodSlownika=" + entry.code + "&nrGen=" + entry.generation) fail("generacja CZS");
        expectNullableString(entry.version, "wersja CZS", MAX_TEXT_LENGTH);
        expectBawDate(entry.publisher_valid_from, "ważność wg CZS");
        expectPattern(entry.generated_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})?$/, "czas generacji", 32);
        expectTimestamp(entry.generated_at.replace(/(Z|[+-]\d{2}:\d{2})$/, "") + "Z", "czas generacji");
        if (/[+-]\d{2}:\d{2}$/.test(entry.generated_at) && !Number.isFinite(Date.parse(entry.generated_at))) fail("strefa generacji");
        if (entry.code === "ICD9") {
          if ([entry.year, entry.grouper_type, entry.display_evidence, entry.published_date, entry.publisher_valid_from].some(function (item) { return item !== null; })) fail("zakres ICD9");
          if (entry.version !== null) expectPattern(entry.version, /^[1-9][0-9]{0,2}\.[0-9]{1,3}$/, "wersja ICD9", 7);
        } else {
          expectInteger(entry.year, "rok MODTAR", 2020, 2100);
          if (entry.grouper_type !== "SZP") fail("zakres MODTAR");
          if (entry.display_evidence === null && [entry.version, entry.published_date, entry.publisher_valid_from].some(function (item) { return item !== null; })) fail("dowód wersji MODTAR");
        }
        if (entry.display_evidence !== null) {
          expectKeys(entry.display_evidence, ["source_url", "sha256", "observed_at"], "dowód MODTAR");
          expectNfzEvidence(entry.display_evidence, "display", value.observed_at);
        }
      } else if (entry.channel === "icd9_companion") {
        expectPattern(entry.version, /^[1-9][0-9]{0,2}\.[0-9]{1,3}$/, "wersja ICD9", 7);
        expectPattern(entry.czs_xml_sha256, DIGEST_PATTERN, "suma XML CZS", 64);
      } else if (entry.meaning !== "leading_indicator") fail("znaczenie AOTMiT");
    });
    Object.entries({ order: 32, news: 64, project: 64, jgp: 4, czs: 2, icd9_companion: 1, aotmit: 12 }).forEach(function (pair) { if ((counts[pair[0]] || 0) > pair[1]) fail("liczba publikacji NFZ"); });
    value.entries.filter(function (entry) { return entry.channel === "icd9_companion"; }).forEach(function (entry) {
      var dictionary = value.entries.find(function (item) { return item.channel === "czs" && item.code === "ICD9"; });
      if (!dictionary || dictionary.sha256 !== entry.czs_xml_sha256 || dictionary.version !== entry.version) fail("powiązanie ICD9");
    });
    return value;
  }

  async function readBoundedJson(response, maximum) {
    maximum = maximum || MAX_RESPONSE_BYTES;
    var length = response.headers.get("content-length");
    if (length !== null) {
      if (!/^[0-9]+$/.test(length)) fail("rozmiar odpowiedzi");
      expectInteger(Number(length), "rozmiar odpowiedzi", 1, maximum);
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
        if (received > maximum) fail("rozmiar odpowiedzi");
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
    return value.toLocaleLowerCase("pl-PL").normalize("NFD").replace(/\p{M}/gu, "").replace(/ł/g, "l").replace(/[^a-z0-9]+/g, " ").trim().replace(/\s+/g, " ");
  }
  function compactIdentifier(value) {
    return normalizeSearch(value).replace(/ /g, "");
  }
  function searchTokens(value) {
    return normalizeSearch(value).replace(/([a-z])([0-9])/g, "$1 $2").replace(/([0-9])([a-z])/g, "$1 $2").split(" ").filter(Boolean);
  }
  function catalogRecord(card, suffix, fields, label, identifiers, primary) {
    return Object.assign({ id: card.source + ":" + card.id + ":" + suffix, cardKey: card.source + ":" + card.id,
      source: card.source, title: card.title, publisher: card.subtitle, version: card.version,
      files: "", roles: "", identifiers: identifiers.join(" "), keys: identifiers.map(compactIdentifier),
      label: label, primary: primary }, fields);
  }
  function catalogDocuments(card) {
    var records = [catalogRecord(card, "card", {}, card.title, [card.id], true)];
    if (card.source === "nfz") {
      records[0].identifiers += " " + [card.entry.code, card.entry.act_number, card.entry.item_id, card.version].filter(Boolean).join(" ");
      records[0].keys.push.apply(records[0].keys, [card.entry.code, card.entry.act_number, card.entry.item_id].filter(Boolean).map(function (value) { return compactIdentifier(String(value)); }));
      card.files.forEach(function (file, index) {
        var name = decodeURIComponent(file.source_url.split("/").pop());
        records.push(catalogRecord(card, "file-" + index, { files: name }, "Plik NFZ: " + name, [], false));
      });
      return records;
    }
    if (card.source === "rsk") {
      records[0].identifiers += " " + card.aliases.join(" ");
      records[0].keys.push.apply(records[0].keys, card.aliases.map(compactIdentifier));
      card.entries.forEach(function (file, index) {
        records.push(catalogRecord(card, file.file_uuid + ":" + index, { title: file.resource_name, version: file.resource_version,
          identifiers: [card.id, file.resource_short_name, file.publisher_short_name].join(" "),
          publisher: file.publisher_name || "", files: file.file_name || "", roles: [file.extension, file.type_label].join(" ") },
          "Plik RSK: " + (file.file_name || "Nazwa niepodana") + " · Wersja " + file.resource_version + " · " + file.resource_name,
          [card.id, file.resource_short_name], false));
      });
      return records;
    }
    records[0].keys.push(compactIdentifier(card.original.act_number));
    card.documents.forEach(function (doc) {
      var role = doc.lineage ? BAW_ROLES[doc.lineage.automatic_type] : "Dokument BAW";
      var label = role + " · " + doc.act_number + " · " + formatDate(doc.act_date);
      var fields = { title: "Zarządzenie " + doc.act_number, publisher: "BAW NFZ Narodowy Fundusz Zdrowia", version: doc.legal_status, roles: role };
      records.push(catalogRecord(card, "doc-" + doc.document_id, fields, label, [String(doc.document_id), doc.act_number], false));
      doc.files.forEach(function (file) {
        records.push(catalogRecord(card, "file-" + doc.document_id + "-" + file.file_id,
          Object.assign({}, fields, { files: file.name }), "Plik: " + file.name + " · " + label,
          [String(doc.document_id), doc.act_number], false));
      });
    });
    return records;
  }
  function buildCatalogIndex(cards) {
    var records = cards.flatMap(catalogDocuments);
    if (records.length > 24576) fail("liczba dokumentów wyszukiwania");
    var engine = new MiniSearch({ fields: ["identifiers", "title", "publisher", "version", "files", "roles"],
      storeFields: ["cardKey", "source", "label", "keys", "title", "primary"],
      tokenize: searchTokens,
      processTerm: function (term) { return term; },
      searchOptions: { combineWith: "AND", fuzzy: false, prefix: function (term) { return term.length >= 3 && !/\d/.test(term); },
        boost: { identifiers: 4, title: 3, files: 2 } } });
    engine.addAll(records);
    return { engine: engine, cards: cards, records: records };
  }
  function searchCatalog(catalog, text, source) {
    if (text.length > 80 || CONTROL_PATTERN.test(text)) return { hits: [], suggestions: [], invalid: true };
    var query = normalizeSearch(text);
    var terms = searchTokens(text);
    if (terms.length > 8 || terms.some(function (term) { return term.length > 64; }) || (text.trim() && !query)) return { hits: [], suggestions: [], invalid: true };
    var cards = catalog.cards.filter(function (card) { return source === "all" || card.source === source; });
    if (!query) return { hits: cards.map(function (card) { return { card: card, evidence: [] }; }), suggestions: [], invalid: false };
    var filter = function (result) { return source === "all" || result.source === source; };
    var results = catalog.engine.search(query, { filter: filter });
    var compact = compactIdentifier(text);
    var exact = catalog.records.filter(function (record) {
      return filter(record) && (record.keys.includes(compact) || normalizeSearch(record.title) === query);
    });
    var found = new Map();
    exact.concat(results).forEach(function (result) {
      var exactKey = result.keys.includes(compact);
      var rank = exactKey ? (result.primary ? 0 : 1) : normalizeSearch(result.title) === query ? 2 : 3;
      var existing = found.get(result.cardKey);
      if (!existing) found.set(result.cardKey, { rank: rank, score: result.score || 0, evidence: [result.label] });
      else {
        existing.rank = Math.min(existing.rank, rank);
        existing.score = Math.max(existing.score, result.score || 0);
        if (existing.evidence.length < 3 && !existing.evidence.includes(result.label)) existing.evidence.push(result.label);
      }
    });
    var hits = cards.map(function (card, index) { return { card: card, order: index, match: found.get(card.source + ":" + card.id) }; })
      .filter(function (hit) { return Boolean(hit.match); })
      .sort(function (left, right) { return left.match.rank - right.match.rank || right.match.score - left.match.score || left.order - right.order; })
      .map(function (hit) { return { card: hit.card, evidence: hit.match.evidence }; });
    var suggestions = hits.length ? [] : catalog.engine.autoSuggest(query, { filter: filter, prefix: false, combineWith: "AND",
      fuzzy: function (term) { return term.length >= 4 && !/\d/.test(term) ? 1 : false; } })
      .filter(function (item) { return item.suggestion.length <= 80 && item.suggestion !== query; })
      .sort(function (left, right) { return right.score - left.score || left.suggestion.localeCompare(right.suggestion, "pl-PL"); })
      .slice(0, 3).map(function (item) { return item.suggestion; });
    return { hits: hits, suggestions: suggestions, invalid: false };
  }
  function formatDate(value) {
    if (value === null) return "Nie podano";
    return new Intl.DateTimeFormat("pl-PL", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "Europe/Warsaw" }).format(new Date(value.length === 10 ? value + "T12:00:00Z" : value));
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
         validity: latest.valid_to, validFrom: latest.valid_from, status: latest.status, files: files,
        metadataSha256: latest.metadata_observation_sha256, notices: notices,
         aliases: [latest.resource_short_name, latest.publisher_short_name].filter(Boolean), entries: entries,
      };
    });
  }
  function bawDatasets(projection) {
    var groups = new Map();
    var byId = new Map(projection.documents.map(function (doc) { return [doc.document_id, doc]; }));
    projection.documents.forEach(function (doc) {
      var id = doc.lineage && [3, 4, 5].includes(doc.lineage.automatic_type) ? doc.lineage.source_document_id : doc.document_id;
      if (!groups.has(id)) groups.set(id, []);
      groups.get(id).push(doc);
    });
    return Array.from(groups, function (pair) {
      var original = byId.get(pair[0]);
      var documents = pair[1].slice().sort(function (left, right) {
        return (right.act_date || "").localeCompare(left.act_date || "") || left.document_id - right.document_id;
      });
      var doc = documents.find(function (item) { return item.lineage && item.lineage.automatic_type === 3; }) || original;
      var parent = original.lineage ? byId.get(original.lineage.source_document_id) : null;
      return {
        source: "baw", id: String(original.document_id), title: "Zarządzenie " + original.act_number,
        subtitle: parent ? "Zmienia zarządzenie " + parent.act_number : "Narodowy Fundusz Zdrowia",
        version: doc.legal_status, observed: doc.observed_at,
        validity: doc.valid_to, status: "observed", files: doc.files, document: doc,
        original: original, documents: documents, byId: byId,
        metadataSha256: doc.metadata_sha256,
      };
    });
  }
  function externalLink(url, label, className) {
    var link = element("a", className || "card-link", label);
    link.href = url;
    link.rel = "noopener noreferrer";
    link.target = "_blank";
    return link;
  }
  function nfzDatasets(projection) {
    return projection.entries.map(function (entry) {
      return { source: "nfz", id: entry.source_url, title: entry.title, subtitle: NFZ_CHANNELS[entry.channel],
        version: entry.version || "Nie ustalono", observed: entry.observed_at, files: entry.files || [], entry: entry };
    });
  }
  function renderNfzCard(card) {
    var entry = card.entry;
    var article = element("article", "source-card");
    article.append(element("span", "source-tag", card.subtitle), element("h3", "", card.title));
    if (state.sourcePages) {
      var sourceLink = element("a", "card-link", "Wszystkie publikacje tego źródła");
      sourceLink.href = "/zrodla/nfz";
      article.append(sourceLink);
    }
    var facts = element("dl", "card-facts");
    fact(facts, "Ostatnio sprawdzono", formatDate(entry.observed_at));
    if (Object.hasOwn(entry, "version")) fact(facts, "Wersja wg wydawcy", card.version);
    if (Object.hasOwn(entry, "published_date")) fact(facts, "Data publikacji", formatDate(entry.published_date));
    if (entry.channel === "czs") {
      fact(facts, "Generacja techniczna", String(entry.generation));
      fact(facts, "Czas generacji wg źródła", entry.generated_at + (/Z$|[+-]\d{2}:\d{2}$/.test(entry.generated_at) ? "" : " (strefa niepodana)"));
      if (entry.code === "MODTAR") {
        fact(facts, "Zakres słownika", entry.year + " / " + entry.grouper_type);
        fact(facts, "Ważny od wg wydawcy", formatDate(entry.publisher_valid_from));
      }
    }
    article.append(facts);
    var notice = entry.channel === "project" ? "Projekt, nie obowiązująca reguła rozliczeniowa." : entry.channel === "aotmit" ? "Sygnał taryfikacji AOTMiT, nie reguła rozliczeniowa NFZ." : "Obserwacja publikacji. Okres stosowania do rozliczeń nie został tu ustalony.";
    article.append(element("p", "card-notice", notice));
    var links = element("div", "nfz-links");
    links.append(externalLink(entry.source_url, "Otwórz w oficjalnym źródle ↗"));
    article.append(links);
    if (entry.channel === "order") article.append(element("p", "card-notice", "Dokument BAW: " + entry.baw_document_id + ". Historię aktu znajdziesz w zakładce Zarządzenia NFZ."));
    if (entry.display_evidence) links.append(externalLink(entry.display_evidence.source_url, "Informacja o wersji CZS ↗"));
    var details = element("details", "technical-details");
    details.append(element("summary", "", "Źródła i sumy kontrolne"));
    details.append(element("span", "technical", "Odczyt (UTC): " + entry.observed_at + " · SHA-256 odpowiedzi źródła: " + entry.sha256));
    if (entry.display_evidence) details.append(element("span", "technical", "SHA-256 informacji o wersji CZS: " + entry.display_evidence.sha256));
    if (entry.czs_xml_sha256) details.append(element("span", "technical", "SHA-256 powiązanego XML CZS: " + entry.czs_xml_sha256));
    card.files.forEach(function (file) {
      var name = decodeURIComponent(file.source_url.split("/").pop());
      details.append(externalLink(file.source_url, name + " ↗"), element("span", "technical", "Odczyt (UTC): " + file.observed_at + " · SHA-256: " + file.sha256));
    });
    article.append(details);
    return article;
  }
  function fileList(card) {
    var list = element("ul", "file-list");
    card.files.forEach(function (file) {
      var row = element("li", "file-row");
      var name = file.file_name || file.name || "Plik bez nazwy";
      row.append(element("strong", "file-name", name));
      if (card.source === "rsk") {
        var action = externalLink(officialOriginUrl(file.file_uuid), "↗", "file-action");
        action.setAttribute("aria-label", "Otwórz " + name + " w RSK (nowa karta)");
        action.title = "Otwórz w RSK (nowa karta)";
        row.append(action);
      }
      row.append(element("small", "file-size", formatSize(file.size_bytes) + " · " + (card.source === "rsk" ? (file.extension || "format niepodany") : name.split(".").pop().toLowerCase())));
      list.append(row);
    });
    return list;
  }
  function technicalDetails(doc, files, source) {
    var details = element("details", "technical-details");
    details.append(element("summary", "", "Szczegóły techniczne"));
    if (doc.document_id) details.append(element("span", "technical", "Identyfikator BAW: " + doc.document_id));
    details.append(element("span", "technical", "Sprawdzono (UTC): " + (doc.observed_at || doc.observed) + " · SHA-256 metadanych: " + (doc.metadata_sha256 || doc.metadataSha256)));
    files.forEach(function (file) {
      var digest = source === "rsk" ? file.product_sha256 : file.sha256;
      if (digest) details.append(element("span", "technical", (file.file_name || file.name) + " · SHA-256: " + digest));
    });
    if (doc.service_applicability_evidence) details.append(element("span", "technical", "Podstawa daty stosowania: " + doc.service_applicability_evidence));
    return details;
  }
  function bawHistory(card) {
    var history = element("details", "act-history");
    var summary = element("summary");
    history.append(summary);
    if (!card.original.lineage) history.append(element("p", "card-notice", "Starszy zapis bez rozpoznanego rodzaju dokumentu. Historia będzie dostępna po kolejnym sprawdzeniu źródła."));
    var states = new Map();
    card.documents.forEach(function (doc) {
      var id = doc.lineage ? (doc.lineage.introductory_document_id || doc.document_id) : doc.document_id;
      if (!states.has(id)) states.set(id, []);
      states.get(id).push(doc);
    });
    var count = states.size;
    summary.textContent = "Historia (" + count + (count === 1 ? " wersja" : count % 10 >= 2 && count % 10 <= 4 && (count % 100 < 12 || count % 100 > 14) ? " wersje" : " wersji") + ")";
    states.forEach(function (documents, id) {
      var amendment = card.byId.get(id);
      var section = element("section", "act-state");
      var isAmendment = amendment && amendment.lineage && amendment.lineage.automatic_type === 6 && id !== card.original.document_id;
      section.append(element("h4", "", isAmendment ? "Po zmianie " + amendment.act_number + " z " + formatDate(amendment.act_date) : "Dokument pierwotny z " + formatDate(card.original.act_date)));
      if (isAmendment && amendment.source_url) section.append(externalLink(amendment.source_url, "Zarządzenie zmieniające ↗"));
      documents.sort(function (left, right) { return (left.lineage ? left.lineage.automatic_type : 0) - (right.lineage ? right.lineage.automatic_type : 0); });
      documents.forEach(function (doc) { section.append(bawVariant(doc)); });
      history.append(section);
    });
    var otherAmendments = Array.from(card.byId.values()).filter(function (doc) {
      return doc.lineage && doc.lineage.automatic_type === 6 && doc.lineage.source_document_id === card.original.document_id && !states.has(doc.document_id);
    });
    if (otherAmendments.length) {
      var additional = element("section", "additional-amendments");
      additional.append(element("h4", "", "Pozostałe zarządzenia zmieniające"), element("p", "card-notice", "W tym zapisie BAW nie ma osobnego tekstu po tych zmianach. Sprawdź treść zarządzenia zmieniającego."));
      otherAmendments.sort(function (left, right) { return (right.act_date || "").localeCompare(left.act_date || "") || left.document_id - right.document_id; });
      otherAmendments.forEach(function (doc) {
        if (doc.source_url) additional.append(externalLink(doc.source_url, doc.act_number + " z " + formatDate(doc.act_date) + " ↗"));
        else additional.append(element("p", "card-notice", doc.act_number + " z " + formatDate(doc.act_date)));
      });
      history.append(additional);
    }
    return history;
  }
  function bawVariant(doc) {
    var details = element("details", "act-variant");
    details.append(element("summary", "", doc.lineage ? BAW_ROLES[doc.lineage.automatic_type] : "Dokument BAW " + doc.document_id));
    var facts = element("dl", "card-facts");
    fact(facts, "Data dokumentu", formatDate(doc.act_date));
    fact(facts, "Publikacja w BAW", formatDate(doc.published_date));
    fact(facts, "Wejście w życie wg BAW", formatDate(doc.entry_into_force));
    fact(facts, "Status w BAW", doc.legal_status);
    if (doc.service_applicability_date) fact(facts, "Dotyczy świadczeń od", formatDate(doc.service_applicability_date));
    details.append(facts);
    details.append(element("p", "card-notice", doc.files.length ? "Kopia dokumentu zachowana przez Minos. Pliki otworzysz w oficjalnej bazie NFZ." : "Minos nie ma kopii plików z tego zapisu. Sprawdź ich dostępność w bazie NFZ."));
    if (doc.files.length) details.append(fileList({ source: "baw", files: doc.files }));
    if (doc.source_url) details.append(externalLink(doc.source_url, "Otwórz w bazie NFZ ↗"));
    details.append(technicalDetails(doc, doc.files, "baw"));
    return details;
  }
  // One badge per source, as the server pages name them (source_pages.NAMES);
  // BAW orders are not the NFZ / CZS / AOTMiT publications (#234).
  var SOURCE_TAGS = { rsk: "RSK", baw: "BAW NFZ" };
  function sourceTag(source) {
    return SOURCE_TAGS[source] || "NFZ";
  }
  function renderCard(card, evidence) {
    if (card.source === "nfz") return renderNfzCard(card);
    var article = element("article", "source-card");
    var overline = element("div", "card-overline");
    overline.append(element("span", "source-tag", sourceTag(card.source)));
    article.append(overline, element("h3", "", card.title), element("p", "card-lead", card.subtitle));
    if (state.sourcePages) {
      var datasetLink = element("a", "card-link", "Zestaw i obserwacje w Minos");
      datasetLink.href = "/zrodla/" + card.source + "/" + card.id;
      article.append(datasetLink);
    }
    if (evidence && evidence.length) {
      var matches = element("ul", "match-evidence");
      evidence.forEach(function (label) { matches.append(element("li", "", label)); });
      article.append(element("p", "match-label", "Dopasowanie w metadanych:"), matches);
    }
    var facts = element("dl", "card-facts");
    fact(facts, card.source === "rsk" ? "Wersja w RSK" : "Status w bazie NFZ", card.version);
    fact(facts, "Ostatnio sprawdzono", formatDate(card.observed));
    if (card.source === "rsk") fact(facts, "Wersja ważna od wg RSK", formatDate(card.validFrom));
    else fact(facts, "Data aktu pierwotnego", formatDate(card.original.act_date));
    fact(facts, "Ważność wg źródła", card.validity === null ? "Nie podano daty końcowej" : "Do " + formatDate(card.validity), card.validity !== null && Date.parse(card.validity) < Date.now() ? "is-expired" : "");
    if (card.source === "baw" && card.document.service_applicability_date !== null) {
      fact(facts, "Dotyczy świadczeń od", formatDate(card.document.service_applicability_date));
    }
    article.append(facts);
    if (card.source === "baw") {
      if (card.document.service_applicability_date === null) article.append(element("p", "card-notice", "Okres stosowania do rozliczeń sprawdź w treści zarządzenia."));
      article.append(bawHistory(card));
      if (card.document.source_url) article.append(externalLink(card.document.source_url, card.document.lineage && card.document.lineage.automatic_type === 3 ? "Najnowszy tekst po zmianach ↗" : "Otwórz zarządzenie w bazie NFZ ↗"));
      return article;
    }
    if (card.status === "missing") article.append(element("p", "card-notice", "Nie znaleziono w ostatnim sprawdzeniu RSK. Pokazujemy poprzedni zapis."));
    var details = element("details");
    details.append(element("summary", "", "Pliki w RSK (" + card.files.length + ")"));
    if (card.files.length) details.append(fileList(card));
    else details.append(element("p", "card-notice", "Źródło nie podało plików."));
    if (card.notices && card.notices.length) {
      var notices = element("ul", "file-list");
      card.notices.forEach(function (notice) { notices.append(element("li", "", notice)); });
      details.append(element("p", "card-notice", "Komunikaty źródła:"), notices);
    }
    details.append(technicalDetails(card, card.files, "rsk"));
    article.append(details);
    return article;
  }
  function allCards(rsk, baw, nfz) {
    var cards = [];
    if (rsk) cards.push.apply(cards, rskDatasets(rsk));
    if (baw) cards.push.apply(cards, bawDatasets(baw));
    if (nfz) cards.push.apply(cards, nfzDatasets(nfz));
    function priority(card) {
      if (card.id === "00CD10") return 0;
      if (card.id === "00CD09") return 1;
      if (card.source === "baw") return card.original.lineage && card.original.lineage.automatic_type === 6 ? 3 : 2;
      return 4;
    }
    return cards.sort(function (left, right) { return priority(left) - priority(right) || left.title.localeCompare(right.title, "pl-PL"); });
  }
  function cardPublisher(card) {
    if (card.source === "baw") return "nfz";
    if (card.source === "nfz") return card.entry.channel === "aotmit" ? "aotmit" : "nfz";
    return Object.keys(PUBLISHERS).find(function (key) { return PUBLISHERS[key] === card.subtitle; }) || "other";
  }
  function cardFormats(card) {
    var names = card.source === "baw" ? card.documents.flatMap(function (doc) { return doc.files.map(function (file) { return file.name; }); }) :
      card.source === "rsk" ? card.entries.map(function (file) { return "file." + file.extension; }) :
      card.files.map(function (file) { return new URL(file.source_url).pathname; });
    if (card.source === "nfz") names.push(card.entry.channel === "czs" ? "file.xml" : new URL(card.entry.source_url).pathname);
    return Array.from(new Set(names.map(function (name) {
      var extension = name.split(".").pop().toLowerCase();
      return FORMATS.includes(extension) ? extension : "other";
    })));
  }
  function catalogSelection(hits, selection) {
    var result = hits.filter(function (hit) {
      return (selection.publisher === "all" || cardPublisher(hit.card) === selection.publisher) &&
        (selection.format === "all" || cardFormats(hit.card).includes(selection.format));
    });
    if (selection.sort !== "relevance") result.sort(function (left, right) {
      var order = selection.sort === "observed" ? Date.parse(right.card.observed) - Date.parse(left.card.observed) : left.card.title.localeCompare(right.card.title, "pl-PL");
      return order || (left.card.source + ":" + left.card.id).localeCompare(right.card.source + ":" + right.card.id, "en");
    });
    return result;
  }
  function readSelection(fragment) {
    var defaults = { source: "all", publisher: "all", format: "all", sort: "relevance", page: 1 };
    if (!fragment || fragment.length > 160 || !fragment.startsWith("#katalog=1&")) return defaults;
    var params = new URLSearchParams(fragment.slice(1));
    var keys = Array.from(params.keys());
    if (new Set(keys).size !== keys.length || keys.some(function (key) { return !["katalog", "source", "publisher", "format", "sort", "page"].includes(key); })) return defaults;
    var selected = { source: params.get("source"), publisher: params.get("publisher"), format: params.get("format"), sort: params.get("sort"), page: Number(params.get("page")) };
    if (!["all", "rsk", "baw", "nfz"].includes(selected.source) || !["all"].concat(Object.keys(PUBLISHERS)).includes(selected.publisher) ||
      !["all"].concat(FORMATS).includes(selected.format) || !["relevance", "title", "observed"].includes(selected.sort) ||
      !/^[1-9][0-9]{0,3}$/.test(params.get("page") || "") || selected.page > 1000) return defaults;
    return selected;
  }
  function selectionHash(selection) {
    if (selection.source === "all" && selection.publisher === "all" && selection.format === "all" && selection.sort === "relevance" && selection.page === 1) return "";
    var params = new URLSearchParams({ katalog: "1", source: selection.source, publisher: selection.publisher, format: selection.format, sort: selection.sort, page: String(selection.query ? 1 : selection.page) });
    return "#" + params.toString();
  }
  function syncSelection() {
    document.querySelectorAll("[data-source]").forEach(function (button) {
      var selected = button.getAttribute("data-source") === state.source;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    ["publisher", "format", "sort"].forEach(function (name) { document.getElementById("catalog-" + name).value = state[name]; });
    // A shared link with non-default facets must show them; never close it for the user.
    if (state.publisher !== "all" || state.format !== "all" || state.sort !== "relevance") document.getElementById("catalog-more").open = true;
    document.getElementById("catalog-results").setAttribute("aria-labelledby", "tab-" + state.source);
    var fragment = selectionHash(state);
    document.getElementById("share-catalog").href = location.pathname + fragment;
    if (location.hash !== fragment) history.replaceState(null, "", location.pathname + fragment);
  }
  function render() {
    document.querySelectorAll("[data-fallback-source]").forEach(function (section) {
      var source = section.getAttribute("data-fallback-source");
      section.hidden = state[source] !== null || (state.source !== "all" && state.source !== source);
      section.querySelector("[data-fallback-note]").hidden = section.hidden || !(state.query.trim() || state.publisher !== "all" || state.format !== "all");
    });
    var answer = state.catalog ? searchCatalog(state.catalog, state.query, state.source) : { hits: [], suggestions: [], invalid: false };
    answer.hits = catalogSelection(answer.hits, state);
    var pages = Math.max(1, Math.ceil(answer.hits.length / PAGE_SIZE));
    if (state.catalog) state.page = Math.min(state.page, pages);
    syncSelection();
    var result = document.getElementById("catalog-results");
    result.replaceChildren();
    answer.hits.slice((state.page - 1) * PAGE_SIZE, state.page * PAGE_SIZE).forEach(function (hit) { result.append(renderCard(hit.card, hit.evidence)); });
    document.getElementById("result-count").textContent = countLabel(answer.hits.length);
    document.getElementById("empty-state").hidden = answer.hits.length !== 0 || answer.invalid;
    document.getElementById("source-search").setAttribute("aria-invalid", answer.invalid ? "true" : "false");
    document.getElementById("search-feedback").textContent = answer.invalid ? "Wpisz maksymalnie 80 znaków i 8 słów, bez znaków sterujących." : pages > 1 ? "Strona " + state.page + " z " + pages + ". Do " + PAGE_SIZE + " zestawów na stronie." : "";
    document.getElementById("more-results").hidden = state.page >= pages;
    document.getElementById("previous-results").hidden = state.page <= 1;
    var suggestions = document.getElementById("search-suggestions");
    suggestions.replaceChildren();
    if (answer.suggestions.length) suggestions.append(element("span", "", "Czy chodziło o: "));
    answer.suggestions.forEach(function (query) {
      var button = element("button", "suggestion-button", query);
      button.type = "button";
      button.addEventListener("click", function () { setQuery(query); document.getElementById("source-search").focus(); });
      suggestions.append(button);
    });
    var summary = document.getElementById("source-summary");
    summary.replaceChildren();
    [["RSK", state.rsk, "RSK"], ["BAW NFZ", state.baw, "BAW"], ["Publikacje NFZ / CZS / AOTMiT", state.nfz, "NFZ"]].forEach(function (pair) {
      var unavailable = state.errors.includes(pair[2]);
      var text = pair[1] ? (unavailable ? "Poprzedni zapis (może być nieaktualny): " : "Sprawdzono ") + formatDate(pair[1].projected_at || pair[1].observed_at) : "Dane niedostępne";
      if (pair[2] === "NFZ" && pair[1]) {
        if (classifyFreshness(pair[1].observed_at, Date.now()).kind !== "current") text += ". Obserwacja wymaga odświeżenia";
        text += pair[1].entries.length ? ". Tylko ostatnie sprawdzenie; niepełna historia publikacji." : ". Brak publikacji w tym zapisie.";
      }
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
    return validator(await readBoundedJson(response, path === NFZ_PATH ? 1024 * 1024 : MAX_RESPONSE_BYTES));
  }
  async function loadAll() {
    setLoadState("loading", "Wczytywanie słowników i zarządzeń...");
    document.getElementById("retry-button").disabled = true;
    var responses = await Promise.allSettled([
      loadOne(RSK_PATH, validateProjection),
      loadOne(BAW_PATH, validateBawProjection),
      loadOne(NFZ_PATH, validateNfzProjection),
    ]);
    var rsk = responses[0].status === "fulfilled" ? responses[0].value : state.rsk;
    var baw = responses[1].status === "fulfilled" ? responses[1].value : state.baw;
    var nfz = responses[2].status === "fulfilled" ? responses[2].value : state.nfz;
    try {
      var cards = allCards(rsk, baw, nfz);
      var catalog = buildCatalogIndex(cards);
      state.rsk = rsk;
      state.baw = baw;
      state.nfz = nfz;
      state.cards = cards;
      state.catalog = catalog;
    } catch (_error) {
      state.errors = ["RSK", "BAW", "NFZ"];
      document.getElementById("retry-button").disabled = false;
      document.getElementById("retry-button").hidden = false;
      setLoadState("error", "Nie udało się przygotować wyszukiwania. Poprzednio wczytane dane mogą być nieaktualne. Spróbuj ponownie.");
      render();
      return;
    }
    document.getElementById("retry-button").disabled = false;
    state.errors = responses.map(function (response, index) { return response.status === "rejected" ? ["RSK", "BAW", "NFZ"][index] : null; }).filter(Boolean);
    document.getElementById("retry-button").hidden = state.errors.length === 0;
    if (state.errors.length) {
      setLoadState("error", "Nie udało się wczytać: " + state.errors.join(", ") + ". Poprzednio wczytane dane mogą być nieaktualne; pozostałe źródła nadal są dostępne.");
    } else {
      setLoadState("ready", "Katalog gotowy. Pliki otworzysz w oficjalnym źródle.");
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
    state.page = 1;
    document.getElementById("catalog-results").setAttribute("aria-labelledby", button.id);
    render();
  }
  function bind() {
    state.sourcePages = document.querySelector("[data-fallback-source]") !== null;
    document.getElementById("catalog-controls").hidden = false;
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
    Object.keys(PUBLISHERS).forEach(function (key) {
      var option = element("option", "", PUBLISHERS[key]); option.value = key;
      document.getElementById("catalog-publisher").append(option);
    });
    FORMATS.forEach(function (format) {
      var option = element("option", "", format === "other" ? "Inny / niepodany" : format.toUpperCase()); option.value = format;
      document.getElementById("catalog-format").append(option);
    });
    Object.assign(state, readSelection(location.hash));
    render();
    ["publisher", "format", "sort"].forEach(function (name) {
      document.getElementById("catalog-" + name).addEventListener("change", function (event) { state[name] = event.target.value; state.page = 1; render(); });
    });
    window.addEventListener("hashchange", function () {
      Object.assign(state, readSelection(location.hash)); state.query = "";
      document.getElementById("source-search").value = ""; render();
    });
    document.getElementById("source-search").addEventListener("input", function (event) {
      state.query = event.target.value;
      state.page = 1;
      render();
    });
    document.getElementById("reset-search").addEventListener("click", function () { setQuery(""); document.getElementById("source-search").focus(); });
    document.getElementById("reset-catalog").addEventListener("click", function () { Object.assign(state, readSelection("")); setQuery(""); document.getElementById("source-search").focus(); });
    document.getElementById("more-results").addEventListener("click", function () { state.page += 1; render(); document.getElementById("catalog-title").focus(); });
    document.getElementById("previous-results").addEventListener("click", function () { state.page -= 1; render(); document.getElementById("catalog-title").focus(); });
    document.getElementById("retry-button").addEventListener("click", loadAll);
    loadAll();
  }
  function setQuery(query) {
    document.getElementById("source-search").value = query;
    state.query = query;
    state.page = 1;
    render();
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
      validateNfzProjection: validateNfzProjection,
      nfzDatasets: nfzDatasets,
      normalizeSearch: normalizeSearch,
      buildCatalogIndex: buildCatalogIndex,
      searchCatalog: searchCatalog,
      rskDatasets: rskDatasets,
      bawDatasets: bawDatasets,
      readSelection: readSelection,
      selectionHash: selectionHash,
      catalogSelection: catalogSelection,
      sourceTag: sourceTag,
    };
  }
}());
