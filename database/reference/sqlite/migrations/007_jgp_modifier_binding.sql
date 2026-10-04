ALTER TABLE schema_migration RENAME TO previous_schema_migration;
CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY CHECK (version BETWEEN 1 AND 7),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
) STRICT;

INSERT INTO schema_migration SELECT version, sha256 FROM previous_schema_migration;
DROP TABLE previous_schema_migration;

CREATE TABLE modifier_binding (
    catalog_release_id TEXT PRIMARY KEY REFERENCES catalog_release(id),
    modifier_release_id TEXT NOT NULL REFERENCES modifier_release(id),
    overlay_sha256 TEXT NOT NULL CHECK (length(overlay_sha256) = 64),
    overlay_json TEXT NOT NULL CHECK (json_valid(overlay_json) AND length(overlay_json) <= 33554432),
    reviewed_on TEXT NOT NULL CHECK (length(reviewed_on) = 10),
    knowledge_from TEXT NOT NULL CHECK (length(knowledge_from) = 10),
    reviewer TEXT NOT NULL CHECK (length(reviewer) BETWEEN 1 AND 2048),
    evidence TEXT NOT NULL CHECK (length(evidence) BETWEEN 1 AND 2048)
) STRICT;

CREATE TABLE modifier_binding_window (
    catalog_release_id TEXT NOT NULL REFERENCES modifier_binding(catalog_release_id),
    service_from TEXT NOT NULL CHECK (length(service_from) = 10),
    service_to TEXT NOT NULL CHECK (length(service_to) = 10 AND service_to >= service_from),
    PRIMARY KEY (catalog_release_id, service_from, service_to)
) STRICT;
