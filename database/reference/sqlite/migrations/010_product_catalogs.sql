ALTER TABLE schema_migration RENAME TO previous_schema_migration;
CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY CHECK (version BETWEEN 1 AND 10),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
) STRICT;

CREATE TABLE product_catalog_release (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 120),
    catalog_release_id TEXT NOT NULL UNIQUE REFERENCES catalog_release(id),
    manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json) AND length(manifest_json) <= 262144)
) STRICT;

CREATE TABLE product_catalog_member (
    release_id TEXT NOT NULL REFERENCES product_catalog_release(id),
    sheet TEXT NOT NULL CHECK (sheet IN ('1b', '1c', '1d', '1ows', '1ts', '1w')),
    code TEXT NOT NULL CHECK (length(code) = 15 AND code GLOB '5.[0-9][0-9].[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
    name TEXT NOT NULL CHECK (length(name) <= 4096),
    source_row INTEGER NOT NULL CHECK (source_row BETWEEN 1 AND 20000),
    PRIMARY KEY (release_id, sheet, code)
) STRICT;

INSERT INTO schema_migration SELECT version, sha256 FROM previous_schema_migration;
DROP TABLE previous_schema_migration;
