"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const path = require("node:path");

const sources = require(path.join(
  __dirname,
  "../../infra/ansible/files/sources.js",
));

const UUID = "00000000-0000-4000-8000-000000000011";
const DIGEST = "a".repeat(64);
const nfzFixture = require("../fixtures/nfz_projection.json");

test("shareable catalog state retains only bounded non-sensitive selections", () => {
  const selected = { source: "nfz", publisher: "nfz", format: "xml", sort: "title", page: 2 };
  assert.deepEqual(sources.readSelection(sources.selectionHash(selected)), selected);
  const withQuery = sources.selectionHash({ ...selected, query: "PRIVATE_QUERY_CANARY" });
  assert.equal(withQuery.includes("PRIVATE_QUERY_CANARY"), false);
  assert.equal(sources.readSelection(withQuery).page, 1);
  for (const fragment of ["#query=PRIVATE_QUERY_CANARY", sources.selectionHash(selected) + "&source=baw", sources.selectionHash(selected) + "&query=canary",
    sources.selectionHash(selected).replace("page=2", "page=1001"), sources.selectionHash(selected).replace("publisher=nfz", "publisher=canary"), "#" + "a".repeat(200)]) {
    assert.deepEqual(sources.readSelection(fragment), { source: "all", publisher: "all", format: "all", sort: "relevance", page: 1 });
  }
});

test("publisher and format facets inspect source evidence including historical files", () => {
  const nfz = sources.nfzDatasets(sources.validateNfzProjection(cloned(nfzFixture)));
  const historical = { source: "baw", id: "43868", title: "Akt", observed: "2026-09-30T08:20:00Z",
    documents: [{ files: [{ name: "Akt.zipx" }] }, { files: [{ name: "Zalacznik9.xlsx" }] }] };
  const hits = [historical, ...nfz].map(card => ({ card, evidence: [] }));
  const selection = { publisher: "nfz", format: "xlsx", sort: "title" };
  assert.ok(sources.catalogSelection(hits, selection).some(hit => hit.card.id === "43868"));
  const aotmit = sources.catalogSelection(hits, { ...selection, publisher: "aotmit", format: "pdf" });
  assert.equal(aotmit.length, 1);
  assert.equal(aotmit[0].card.entry.channel, "aotmit");
  const eli = sources.catalogSelection(hits, { ...selection, publisher: "eli", format: "all" });
  assert.deepEqual(eli.map(hit => hit.card.entry.dictionary), ["discharge_modes"]);
  assert.equal(sources.catalogSelection(hits, { ...selection, publisher: "who" }).length, 0);
});

test("observed sort uses timestamps and stable identity ties, not input order", () => {
  const hits = ["b", "a"].map(id => ({ card: { source: "nfz", id, title: "Same", observed: "2026-10-01T00:00:00Z" } }));
  hits.push({ card: { source: "nfz", id: "newer", title: "Same", observed: "2026-10-01T00:00:00.001Z" } });
  assert.deepEqual(sources.catalogSelection(hits, { publisher: "all", format: "all", sort: "observed" }).map(hit => hit.card.id), ["newer", "a", "b"]);
});

test("NFZ channels retain source meaning and support bounded local search", () => {
  const value = sources.validateNfzProjection(cloned(nfzFixture));
  assert.equal(value.entries.length, 9);
  assert.equal(value.entries.find(entry => entry.code === "MODTAR").generated_at, "2026-09-24T10:58:10");
  const catalog = sources.buildCatalogIndex(sources.nfzDatasets(value));
  assert.equal(sources.searchCatalog(catalog, "MODTAR 202664", "nfz").hits[0].card.entry.code, "MODTAR");
  assert.equal(sources.searchCatalog(catalog, "opis algorytmu 2026 docx", "nfz").hits[0].card.entry.channel, "jgp");
  assert.equal(sources.searchCatalog(catalog, "MODTAR", "baw").hits.length, 0);
});

test("NFZ rejects private fields, unsafe links, mismatched versions and malformed dates", () => {
  const mutations = [
    v => { v.entries[0].archive_key = "private"; },
    v => { v.entries[0].published_date = "2026-02-30"; },
    v => { v.entries[0].source_url += "?token=secret"; },
    v => { v.entries[0].source_url = v.entries[0].source_url.replace("www.nfz.gov.pl", "user@www.nfz.gov.pl"); },
    v => { v.entries[4].generation = 94; },
    v => { v.entries[4].source_url += "&nrGen=94"; },
    v => { v.entries[5].display_evidence = null; },
    v => { v.entries[3].files[0].source_url = v.entries[3].files[0].source_url.replace("/54/", "/55/"); },
    v => { v.entries[7].meaning = "binding_rule"; },
    v => { v.entries[7].source_url = v.entries[7].source_url.replace("Synthetic%20taryfy", "%252e%252e"); },
    v => { v.entries[1].observed_at = "2026-10-02T10:00:00.000Z"; },
    v => { v.entries.push(v.entries[0]); },
    v => { v.redistribution.artifacts.push("private"); },
    v => { v.entries[8].findings = ["amendment:<script>"]; },
    v => { v.entries[8].eli = "DU/2025/1589"; },
    v => { v.entries[8].base_evidence.source_url = "https://api.sejm.gov.pl/eli/acts/DU/2019/1208"; },
    v => { v.entries[8].dictionary = "products"; },
    v => { v.entries.push({ ...v.entries[8], dictionary: "specialties", eli: "DU/2019/173", source_url: "https://api.sejm.gov.pl/eli/acts/DU/2019/173" }, { ...v.entries[8], eli: "DU/2019/174", source_url: "https://api.sejm.gov.pl/eli/acts/DU/2019/174" }); },
  ];
  for (const mutate of mutations) {
    const value = cloned(nfzFixture); mutate(value);
    assert.throws(() => sources.validateNfzProjection(value));
  }
  assert.equal(sources.validateNfzProjection({ ...cloned(nfzFixture), entries: [] }).entries.length, 0);
});

function entry(overrides = {}) {
  return {
    archive_status: "metadata_only",
    download_url: null,
    downloadable: false,
    extension: "xml",
    file_created_at: "2026-09-20T10:00:00Z",
    file_name: "resource.xml",
    file_uuid: UUID,
    first_observed_at: "2026-09-20T11:00:00Z",
    hcd: "00CD10",
    last_observed_at: "2026-09-21T11:00:00Z",
    metadata_observation_sha256: DIGEST,
    notices: [],
    origin_url: sources.officialOriginUrl(UUID),
    product_sha256: null,
    publisher_name: "Wydawca",
    publisher_short_name: "PUB",
    resource_name: "Zasób testowy",
    resource_short_name: "TEST",
    resource_type: "SLOWNIK",
    resource_version: "1",
    size_bytes: 123,
    source_archived_at: null,
    status: "current",
    type_label: "XML",
    valid_from: "2026-01-01T00:00:00Z",
    valid_to: null,
    ...overrides,
  };
}

function projection(overrides = {}) {
  return {
    entries: [entry()],
    history_truncated: false,
    included_revisions: [1],
    notices: ["Publiczny komunikat."],
    projected_at: "2026-09-21T12:00:00Z",
    redistribution: { artifacts: [], schema_version: 1 },
    schema_version: 1,
    source: "RSK",
    through_state_revision: 1,
    ...overrides,
  };
}

function cloned(value) {
  return JSON.parse(JSON.stringify(value));
}

test("accepts the exact bounded projection and hostile source text", () => {
  const value = projection({
    entries: [entry({ resource_name: '<img src=x onerror="alert(1)">' })],
  });

  const validated = sources.validateProjection(cloned(value));

  assert.equal(validated.entries[0].resource_name, value.entries[0].resource_name);
  assert.equal(
    validated.entries[0].origin_url,
    `https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/${UUID}`,
  );
});

test("rejects arbitrary or decorated origin URLs", () => {
  const origins = [
    `https://evil.invalid/api/rsk/file/get/${UUID}`,
    `https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/${UUID}?download=1`,
    `https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/${UUID}/extra`,
  ];

  for (const origin_url of origins) {
    assert.throws(() => sources.validateProjection(projection({
      entries: [entry({ origin_url })],
    })));
  }
});

test("rejects malformed digests, dates, enums, and unknown fields", () => {
  const mutations = [
    entry({ metadata_observation_sha256: "A".repeat(64) }),
    entry({ valid_from: "2026-02-31T00:00:00Z" }),
    entry({ status: "downloadable" }),
    { ...entry(), unexpected: "field" },
  ];

  for (const malformed of mutations) {
    assert.throws(() => sources.validateProjection(projection({ entries: [malformed] })));
  }
});

test("rejects inconsistent archive and download claims", () => {
  assert.throws(() => sources.validateProjection(projection({
    entries: [entry({ archive_status: "privately_archived_verified" })],
  })));
  assert.throws(() => sources.validateProjection(projection({
    entries: [entry({ downloadable: true })],
  })));
});

test("rejects revision fences and arrays outside their bounds", () => {
  assert.throws(() => sources.validateProjection(projection({
    included_revisions: [2],
  })));
  assert.throws(() => sources.validateProjection(projection({
    entries: new Array(8193).fill(entry()),
  })));
});

test("compares validity and observation intervals at microsecond precision", () => {
  assert.throws(() => sources.validateProjection(projection({
    entries: [entry({
      valid_from: "2026-01-01T00:00:00.000001Z",
      valid_to: "2026-01-01T00:00:00Z",
    })],
  })));
  assert.throws(() => sources.validateProjection(projection({
    entries: [entry({
      first_observed_at: "2026-01-01T00:00:00.000001Z",
      last_observed_at: "2026-01-01T00:00:00Z",
    })],
  })));
});

test("validates every timestamp field as a UTC calendar value", () => {
  const invalidTimestamp = "2026-02-30T00:00:00.1Z";
  const entryFields = [
    "valid_from", "valid_to", "file_created_at", "source_archived_at",
    "first_observed_at", "last_observed_at",
  ];
  for (const field of entryFields) {
    assert.throws(() => sources.validateProjection(projection({
      entries: [entry({ [field]: invalidTimestamp })],
    })));
  }
  assert.throws(() => sources.validateProjection(projection({
    projected_at: invalidTimestamp,
  })));
});

test("uses the producer UTC calendar and microsecond timestamp contract", () => {
  const accepted = [
    "0001-01-01T00:00:00Z",
    "2000-02-29T23:59:59.000001Z",
    "9999-12-31T23:59:59.999999Z",
  ];
  const rejected = [
    "0000-01-01T00:00:00Z",
    "1900-02-29T00:00:00Z",
    "2026-01-01T24:00:00Z",
    "2026-01-01T00:00:60Z",
    "2026-01-01T00:00:00.0000001Z",
    "2026-01-01T00:00:00+00:00",
    "2026-01-01T00:00:00z",
  ];
  for (const projected_at of accepted) {
    assert.doesNotThrow(() => sources.validateProjection(projection({ projected_at })));
  }
  for (const projected_at of rejected) {
    assert.throws(() => sources.validateProjection(projection({ projected_at })));
  }
});

test("bounds deduplicated notices across the complete projection", () => {
  const notices = Array.from({ length: 16 }, (_, index) => `Notice ${index}`);
  const repeated = Array.from({ length: 9 }, () => entry({ notices }));
  assert.doesNotThrow(() => sources.validateProjection(projection({ entries: repeated })));

  const unique = Array.from({ length: 9 }, (_, entryIndex) => entry({
    notices: Array.from(
      { length: 16 },
      (_, noticeIndex) => `Notice ${entryIndex * 16 + noticeIndex}`,
    ),
  }));
  assert.throws(() => sources.validateProjection(projection({ entries: unique })));
});

test("classifies current, stale, severe, and future-clock projections", () => {
  const now = Date.parse("2026-09-22T12:00:00Z");
  assert.equal(sources.classifyFreshness("2026-09-21T12:00:00Z", now).kind, "current");
  assert.equal(sources.classifyFreshness("2026-09-20T18:00:00Z", now).kind, "stale");
  assert.equal(sources.classifyFreshness("2026-09-18T12:00:00Z", now).kind, "severe");
  assert.equal(sources.classifyFreshness("2026-09-22T13:00:00.001Z", now).kind, "future");
});

function bawProjection(overrides = {}) {
  return {
    schema_version: 1,
    source: "BAW",
    through_state_revision: 2,
    observed_at: "2026-09-21T12:00:00Z",
    redistribution: { schema_version: 1, artifacts: [] },
    documents: [{
      document_id: 43943,
      act_number: "46/2026/DSOZ",
      act_date: "2026-07-23",
      published_date: null,
      entry_into_force: "2026-07-24",
      service_applicability_date: null,
      valid_to: null,
      legal_status: "Obowiązujący",
      observed_at: "2026-09-21T11:00:00Z",
      metadata_sha256: DIGEST,
      source_url: "https://baw.nfz.gov.pl/NFZ/document/43943/Zarzadzenie-46_2026_DSOZ",
      files: [{ file_id: 30983, name: "Zalacznik1a.xlsx", size_bytes: 304191, sha256: DIGEST }],
    }],
    ...overrides,
  };
}

test("accepts private BAW provenance but never a Minos download", () => {
  const verified = sources.validateBawProjection(bawProjection());
  assert.equal(verified.documents[0].files[0].name, "Zalacznik1a.xlsx");
  assert.deepEqual(verified.redistribution.artifacts, []);
});

test("rejects unrelated BAW hosts, invalid dates, duplicate files, and redistribution", () => {
  const valid = bawProjection();
  for (const mutation of [
    { ...valid.documents[0], source_url: "https://evil.invalid/NFZ/document/43943/Zarzadzenie-46_2026_DSOZ" },
    { ...valid.documents[0], source_url: "https://baw.nfz.gov.pl/NFZ/document/43943/not-the-title" },
    { ...valid.documents[0], entry_into_force: "2026-02-30" },
    { ...valid.documents[0], files: [valid.documents[0].files[0], valid.documents[0].files[0]] },
  ]) {
    assert.throws(() => sources.validateBawProjection(bawProjection({ documents: [mutation] })));
  }
  assert.throws(() => sources.validateBawProjection(bawProjection({
    redistribution: { schema_version: 1, artifacts: ["public-baw.xlsx"] },
  })));
  assert.equal(sources.validateBawProjection(bawProjection({
    documents: [{ ...valid.documents[0], source_url: null }],
  })).documents[0].source_url, null);
  assert.equal(sources.validateBawProjection(bawProjection({
    documents: [{ ...valid.documents[0], source_url: "https://baw.nfz.gov.pl/NFZ/document/43943" }],
  })).documents[0].source_url, null);
});

test("BAW applicability requires evidence and remains independent of entry into force", () => {
  const document = bawProjection().documents[0];
  const reviewed = { ...document, service_applicability_date: "2026-07-01", service_applicability_evidence: "BAW NFZ section 4" };
  assert.equal(sources.validateBawProjection(bawProjection({ documents: [reviewed] })).documents[0].entry_into_force, "2026-07-24");
  const { service_applicability_evidence: _evidence, ...missing } = reviewed;
  assert.throws(() => sources.validateBawProjection(bawProjection({ documents: [missing] })));
  assert.throws(() => sources.validateBawProjection(bawProjection({
    documents: [{ ...document, service_applicability_evidence: "BAW NFZ section 4" }],
  })));
});

test("local catalog search preserves word boundaries and folds Polish diacritics", () => {
  assert.equal(sources.normalizeSearch("ICD-10"), "icd 10");
  assert.equal(sources.normalizeSearch("państwa"), sources.normalizeSearch("panstwa"));
  assert.equal(sources.normalizeSearch("Załącznik 9"), "zalacznik 9");
  assert.notEqual(sources.normalizeSearch("ab c"), sources.normalizeSearch("a bc"));
});

function searchableCatalog() {
  const rsk = sources.rskDatasets(sources.validateProjection(projection({ entries: [entry({
    resource_name: "Klasyfikacja testowa", resource_short_name: "ICD-10", file_name: "Slownik.xml",
  })] })));
  const doc = bawProjection().documents[0];
  const baw = sources.bawDatasets(sources.validateBawProjection(bawProjection({ documents: [{ ...doc,
    files: [{ ...doc.files[0], name: "Załącznik 9.xlsx" }],
  }] })));
  return sources.buildCatalogIndex([...rsk, ...baw]);
}

test("catalog finds exact identifiers and multiword metadata in either order", () => {
  const catalog = searchableCatalog();
  for (const query of ["ICD10", "ICD-10", "00CD10"]) {
    assert.deepEqual(sources.searchCatalog(catalog, query, "all").hits.map(hit => hit.card.id), ["00CD10"]);
  }
  for (const query of ["NFZ załącznik 9", "9 zalacznik NFZ", "46/2026/DSOZ"]) {
    const result = sources.searchCatalog(catalog, query, "all");
    assert.equal(result.hits.length, 1);
    assert.equal(result.hits[0].card.source, "baw");
  }
  assert.equal(sources.searchCatalog(catalog, "NFZ załącznik 9", "rsk").hits.length, 0);
  assert.equal(sources.searchCatalog(catalog, "NFZ załącznik 99", "all").hits.length, 0);
  assert.equal(sources.searchCatalog(catalog, "NFZ załącznik 99", "all").suggestions.length, 0);
});

test("catalog returns filename and version evidence, not an implied contents match", () => {
  const result = sources.searchCatalog(searchableCatalog(), "NFZ załącznik 9", "all");
  assert.match(result.hits[0].evidence[0], /Plik: Załącznik 9.xlsx/);
  assert.match(result.hits[0].evidence[0], /46\/2026\/DSOZ/);
  assert.equal(sources.searchCatalog(searchableCatalog(), "niewydolność", "all").hits.length, 0);
});

test("catalog typos produce explicit suggestions without returning corrected matches", () => {
  const result = sources.searchCatalog(searchableCatalog(), "zalacznikx", "all");
  assert.equal(result.hits.length, 0);
  assert.ok(result.suggestions.includes("zalacznik"));
  assert.equal(sources.searchCatalog(searchableCatalog(), "zalacznikx", "rsk").suggestions.length, 0);
});

test("catalog rejects controls, oversized queries and excessive token counts", () => {
  for (const query of ["x".repeat(81), "word ".repeat(9), "x\u001btext", "***"]) {
    assert.equal(sources.searchCatalog(searchableCatalog(), query, "all").invalid, true);
  }
});

test("catalog matches previous RSK metadata even when a file UUID was reused", () => {
  const latest = entry({ resource_name: "Current catalog", file_name: "current.xml", resource_version: "013" });
  const old = { ...latest, resource_name: "Previous catalog", resource_short_name: "OLD-ID",
    file_name: "legacy-appendix.xml", resource_version: "012", status: "historical" };
  const cards = sources.rskDatasets(sources.validateProjection(projection({ entries: [latest, old] })));
  const catalog = sources.buildCatalogIndex(cards);
  for (const query of ["legacy appendix", "012", "OLDID"]) {
    const result = sources.searchCatalog(catalog, query, "rsk");
    assert.equal(result.hits.length, 1);
    assert.match(result.hits[0].evidence.join(" "), /legacy-appendix.xml.*012.*Previous catalog/);
    assert.equal(result.hits[0].card.title, "Current catalog");
  }
});

test("exact catalog names do not collapse boundaries like compact identifiers", () => {
  const cards = sources.rskDatasets(sources.validateProjection(projection({ entries: [entry({
    resource_name: "ab c", resource_short_name: "TEST",
  })] })));
  assert.equal(sources.searchCatalog(sources.buildCatalogIndex(cards), "a bc", "all").hits.length, 0);
});

test("exact catalog aliases and names outrank boosted prefix matches", () => {
  const entries = [
    entry({ hcd: "AA0001", resource_name: "Target catalog", resource_short_name: "TARGETS" }),
    entry({ hcd: "AA0002", resource_name: "Target", resource_short_name: "OTHER" }),
    entry({ hcd: "AA0003", resource_name: "Different catalog", resource_short_name: "TARGET" }),
  ];
  const catalog = sources.buildCatalogIndex(sources.rskDatasets(sources.validateProjection(projection({ entries }))));
  for (let repeat = 0; repeat < 2; repeat++) {
    assert.deepEqual(sources.searchCatalog(catalog, "target", "all").hits.map(hit => hit.card.id), ["AA0003", "AA0002", "AA0001"]);
  }
});

test("BAW lineage requires verified original and introductory acts, not shared names", () => {
  const base = bawProjection().documents[0];
  const original = { ...base, document_id: 43868, source_url: null,
    lineage: { automatic_type: 1, source_document_id: null, introductory_document_id: null } };
  const amendment = { ...base, document_id: 43942, act_number: "74/2026/DSOZ", source_url: null,
    lineage: { automatic_type: 6, source_document_id: 43868, introductory_document_id: null } };
  const variant = { ...base,
    lineage: { automatic_type: 4, source_document_id: 43868, introductory_document_id: 43942 } };
  const valid = bawProjection({ schema_version: 2, documents: [original, amendment, variant] });
  assert.doesNotThrow(() => sources.validateBawProjection(cloned(valid)));
  for (const change of [
    { automatic_type: 2 },
    { source_document_id: null },
    { source_document_id: 43943 },
    { source_document_id: 99999 },
    { introductory_document_id: null },
    { introductory_document_id: 43868 },
  ]) {
    const broken = cloned(valid);
    Object.assign(broken.documents[2].lineage, change);
    assert.throws(() => sources.validateBawProjection(broken));
  }
  const mismatch = cloned(valid);
  mismatch.documents[2].act_number = "101/2025/DSOZ";
  mismatch.documents[2].source_url = null;
  assert.throws(() => sources.validateBawProjection(mismatch));
  const missing = cloned(valid);
  delete missing.documents[2].lineage;
  assert.throws(() => sources.validateBawProjection(missing));
  assert.throws(() => sources.validateBawProjection({ ...valid, schema_version: 1 }));
});

test("BAW orders keep their own badge, distinct from NFZ publications", () => {
  assert.equal(sources.sourceTag("rsk"), "RSK");
  assert.equal(sources.sourceTag("baw"), "BAW NFZ");
  assert.notEqual(sources.sourceTag("baw"), "NFZ");
});
