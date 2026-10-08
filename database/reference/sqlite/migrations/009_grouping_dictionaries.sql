ALTER TABLE schema_migration RENAME TO previous_schema_migration;
CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY CHECK (version BETWEEN 1 AND 9),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
) STRICT;

CREATE TABLE grouping_dictionary_release (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 120),
    kind TEXT NOT NULL CHECK (kind IN ('specialty', 'discharge_mode')),
    manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json) AND length(manifest_json) <= 262144)
) STRICT;

CREATE TABLE specialty_code (
    release_id TEXT NOT NULL REFERENCES grouping_dictionary_release(id),
    code TEXT NOT NULL CHECK (length(code) = 4 AND code GLOB '[0-9][0-9][0-9][0-9]'),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 512),
    section TEXT NOT NULL CHECK (length(section) BETWEEN 1 AND 512),
    derivation TEXT NOT NULL CHECK (derivation IN ('listed', 'children_rule')),
    position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 5000),
    PRIMARY KEY (release_id, code)
) STRICT;

CREATE TABLE discharge_mode (
    release_id TEXT NOT NULL REFERENCES grouping_dictionary_release(id),
    code INTEGER NOT NULL CHECK (code BETWEEN 1 AND 99),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 1024),
    point INTEGER NOT NULL CHECK (point BETWEEN 1 AND 99),
    PRIMARY KEY (release_id, code)
) STRICT;

INSERT INTO schema_migration SELECT version, sha256 FROM previous_schema_migration;
DROP TABLE previous_schema_migration;
