ALTER TABLE schema_migration RENAME TO previous_schema_migration;
CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY CHECK (version BETWEEN 1 AND 11),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
) STRICT;

CREATE TABLE icd11_map_release (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 120),
    icd11_release_id TEXT NOT NULL REFERENCES source_release(id),
    manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json) AND length(manifest_json) <= 65536)
) STRICT;

-- One WHO table row as published, with absent cells kept NULL.
CREATE TABLE icd11_map_row (
    release_id TEXT NOT NULL REFERENCES icd11_map_release(id),
    kind TEXT NOT NULL CHECK (kind IN ('10to11_one', '10to11_many', '11to10_one')),
    source_row INTEGER NOT NULL CHECK (source_row BETWEEN 2 AND 40001),
    icd10_code TEXT CHECK (icd10_code IS NULL OR length(icd10_code) BETWEEN 1 AND 16),
    icd10_title TEXT CHECK (icd10_title IS NULL OR length(icd10_title) <= 4096),
    icd11_code TEXT CHECK (icd11_code IS NULL OR length(icd11_code) BETWEEN 1 AND 64),
    icd11_uri TEXT CHECK (icd11_uri IS NULL OR length(icd11_uri) <= 256),
    icd11_title TEXT CHECK (icd11_title IS NULL OR length(icd11_title) <= 4096),
    PRIMARY KEY (release_id, kind, source_row)
) STRICT;
CREATE INDEX icd11_map_by_icd10 ON icd11_map_row (release_id, kind, icd10_code);
CREATE INDEX icd11_map_by_icd11 ON icd11_map_row (release_id, kind, icd11_code);

INSERT INTO schema_migration SELECT version, sha256 FROM previous_schema_migration;
DROP TABLE previous_schema_migration;
