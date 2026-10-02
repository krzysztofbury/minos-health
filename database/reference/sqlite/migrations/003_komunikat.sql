ALTER TABLE schema_migration RENAME TO previous_schema_migration;

CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY CHECK (version IN (1, 2, 3)),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
) STRICT;

INSERT INTO schema_migration SELECT version, sha256 FROM previous_schema_migration;
DROP TABLE previous_schema_migration;

CREATE TABLE algorithm_release (
    id TEXT PRIMARY KEY,
    icd9_release_id TEXT NOT NULL REFERENCES source_release(id),
    algorithm_version TEXT NOT NULL,
    reporting_from TEXT NOT NULL,
    version_text TEXT NOT NULL,
    version_paragraph INTEGER NOT NULL CHECK (version_paragraph BETWEEN 1 AND 10000),
    pairing TEXT NOT NULL CHECK (pairing = 'reference-only'),
    manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json))
) STRICT;

CREATE UNIQUE INDEX algorithm_release_reference ON algorithm_release(icd9_release_id);

CREATE TABLE procedure_rank (
    release_id TEXT NOT NULL REFERENCES algorithm_release(id),
    grouper_type TEXT NOT NULL CHECK (grouper_type IN ('SZP', 'AOS')),
    code TEXT NOT NULL,
    source_title TEXT NOT NULL,
    rank INTEGER NOT NULL CHECK (rank BETWEEN 0 AND 4),
    worksheet TEXT NOT NULL,
    source_row INTEGER NOT NULL CHECK (source_row BETWEEN 4 AND 20000),
    PRIMARY KEY (release_id, grouper_type, code)
) STRICT;

CREATE TABLE department_specialty (
    release_id TEXT NOT NULL REFERENCES algorithm_release(id),
    grouper_type TEXT NOT NULL CHECK (grouper_type IN ('SZP', 'AOS', 'REH')),
    code TEXT NOT NULL,
    service_codes TEXT NOT NULL,
    icd_codes TEXT NOT NULL,
    worksheet TEXT NOT NULL,
    source_row INTEGER NOT NULL CHECK (source_row BETWEEN 4 AND 20000),
    PRIMARY KEY (release_id, grouper_type, code)
) STRICT;
