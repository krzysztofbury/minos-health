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
      source_url: "https://baw.nfz.gov.pl/NFZ/document/43943",
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
    { ...valid.documents[0], source_url: "https://evil.invalid/NFZ/document/43943" },
    { ...valid.documents[0], entry_into_force: "2026-02-30" },
    { ...valid.documents[0], files: [valid.documents[0].files[0], valid.documents[0].files[0]] },
  ]) {
    assert.throws(() => sources.validateBawProjection(bawProjection({ documents: [mutation] })));
  }
  assert.throws(() => sources.validateBawProjection(bawProjection({
    redistribution: { schema_version: 1, artifacts: ["public-baw.xlsx"] },
  })));
});

test("local catalog search normalizes ICD punctuation and Polish diacritics", () => {
  assert.equal(sources.normalizeSearch("ICD-10"), sources.normalizeSearch("ICD10"));
  assert.equal(sources.normalizeSearch("państwa"), sources.normalizeSearch("panstwa"));
  assert.equal(sources.normalizeSearch("Załącznik 9"), "zalacznik9");
});
