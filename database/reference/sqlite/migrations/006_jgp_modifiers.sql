ALTER TABLE schema_migration RENAME TO previous_schema_migration;
CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY CHECK (version IN (1, 2, 3, 4, 5, 6)),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
) STRICT;
INSERT INTO schema_migration SELECT version, sha256 FROM previous_schema_migration;
DROP TABLE previous_schema_migration;

CREATE TABLE modifier_release (
    id TEXT PRIMARY KEY,
    manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json)),
    source_json TEXT NOT NULL CHECK (json_valid(source_json) AND length(source_json) <= 8388608),
    year INTEGER NOT NULL CHECK (year BETWEEN 2000 AND 2099),
    generation INTEGER NOT NULL CHECK (generation BETWEEN 1 AND 999999),
    grouper_type TEXT NOT NULL CHECK (grouper_type = 'SZP'),
    source_sha256 TEXT NOT NULL CHECK (length(source_sha256) = 64)
) STRICT;
CREATE TABLE tariff_modifier (
    release_id TEXT NOT NULL REFERENCES modifier_release(id),
    source_order INTEGER NOT NULL CHECK (source_order BETWEEN 1 AND 10000),
    symbol TEXT NOT NULL CHECK (length(symbol) BETWEEN 1 AND 80),
    description TEXT NOT NULL CHECK (length(description) BETWEEN 1 AND 8192),
    status TEXT NOT NULL CHECK (status IN ('A', 'N')),
    PRIMARY KEY (release_id, source_order)
) STRICT;
CREATE TABLE modifier_period (
    release_id TEXT NOT NULL,
    modifier_order INTEGER NOT NULL,
    source_order INTEGER NOT NULL CHECK (source_order BETWEEN 1 AND 24),
    month_from INTEGER NOT NULL CHECK (month_from BETWEEN 1 AND 12),
    month_to INTEGER NOT NULL CHECK (month_to BETWEEN month_from AND 12),
    coefficient_text TEXT NOT NULL,
    coefficient_millionths INTEGER NOT NULL CHECK (coefficient_millionths BETWEEN 0 AND 999999999),
    PRIMARY KEY (release_id, modifier_order, source_order),
    FOREIGN KEY (release_id, modifier_order) REFERENCES tariff_modifier(release_id, source_order)
) STRICT;
CREATE TABLE modifier_group (
    release_id TEXT NOT NULL,
    modifier_order INTEGER NOT NULL,
    period_order INTEGER NOT NULL,
    source_order INTEGER NOT NULL CHECK (source_order BETWEEN 1 AND 2000),
    code TEXT NOT NULL,
    PRIMARY KEY (release_id, modifier_order, period_order, source_order),
    FOREIGN KEY (release_id, modifier_order, period_order) REFERENCES modifier_period(release_id, modifier_order, source_order)
) STRICT;
CREATE TABLE modifier_candidate (
    catalog_release_id TEXT PRIMARY KEY REFERENCES catalog_release(id),
    modifier_release_id TEXT NOT NULL REFERENCES modifier_release(id),
    status TEXT NOT NULL CHECK (status = 'pending_legal_review'),
    evidence TEXT NOT NULL
) STRICT;

CREATE INDEX directional_member_code ON directional_member(release_id, catalog, system, code, list_id, row);
CREATE INDEX directional_member_list ON directional_member(release_id, catalog, list_id, row);
CREATE INDEX characteristics_group_evidence ON characteristics_evidence(release_id, catalog, context, row);
CREATE INDEX characteristics_group_nodes ON group_condition(release_id, catalog, group_code, source_order);
