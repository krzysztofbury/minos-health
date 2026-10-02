ALTER TABLE schema_migration RENAME TO previous_schema_migration;

CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY CHECK (version IN (1, 2, 3, 4)),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
) STRICT;

INSERT INTO schema_migration SELECT version, sha256 FROM previous_schema_migration;
DROP TABLE previous_schema_migration;

-- One attachment 1a workbook with its reviewed temporal evidence. Valid time
-- is service_from, which rows may narrow. Knowledge time is published_on.
-- recorded_at is when Minos fetched the bytes. A service date never falls
-- back to the latest release.
CREATE TABLE catalog_release (
    id TEXT PRIMARY KEY,
    act_number TEXT NOT NULL,
    baw_document_id INTEGER NOT NULL UNIQUE CHECK (baw_document_id > 0),
    automatic_type INTEGER NOT NULL CHECK (automatic_type IN (1, 3)),
    act_date TEXT NOT NULL CHECK (act_date = date(act_date)),
    entry_into_force TEXT CHECK (entry_into_force = date(entry_into_force)),
    published_on TEXT NOT NULL CHECK (
        published_on = date(published_on) AND published_on >= act_date
    ),
    service_from TEXT NOT NULL CHECK (service_from = date(service_from)),
    service_evidence TEXT NOT NULL,
    supersedes TEXT REFERENCES catalog_release(id),
    modifier_binding TEXT NOT NULL CHECK (modifier_binding = 'unbound'),
    recorded_at TEXT NOT NULL,
    workbook_sha256 TEXT NOT NULL CHECK (length(workbook_sha256) = 64),
    manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json))
) STRICT;

-- The reviewed date through which the catalog lineage is known complete.
CREATE TABLE catalog_knowledge (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    as_of TEXT NOT NULL CHECK (as_of = date(as_of)),
    evidence TEXT NOT NULL
) STRICT;

CREATE TABLE catalog_sheet (
    release_id TEXT NOT NULL REFERENCES catalog_release(id),
    catalog TEXT NOT NULL CHECK (catalog IN ('1a', '1ae')),
    layout TEXT NOT NULL,
    worksheet TEXT NOT NULL,
    title TEXT NOT NULL,
    PRIMARY KEY (release_id, catalog)
) STRICT;

CREATE TABLE catalog_scope (
    release_id TEXT NOT NULL,
    catalog TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position BETWEEN 1 AND 200),
    source_column TEXT NOT NULL,
    label TEXT NOT NULL,
    PRIMARY KEY (release_id, catalog, position),
    FOREIGN KEY (release_id, catalog) REFERENCES catalog_sheet(release_id, catalog)
) STRICT;

-- One row per source catalog row: a code may appear on two rows.
CREATE TABLE group_definition (
    release_id TEXT NOT NULL,
    catalog TEXT NOT NULL,
    source_row INTEGER NOT NULL CHECK (source_row BETWEEN 6 AND 20000),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    code TEXT NOT NULL,
    product_code TEXT NOT NULL,
    name TEXT NOT NULL,
    procedural INTEGER NOT NULL CHECK (procedural IN (0, 1)),
    chapter TEXT NOT NULL,
    chapter_position INTEGER NOT NULL CHECK (chapter_position BETWEEN 1 AND 200),
    notes TEXT,
    service_from TEXT NOT NULL CHECK (service_from = date(service_from)),
    service_to TEXT CHECK (
        service_to IS NULL OR (service_to = date(service_to) AND service_to >= service_from)
    ),
    applicability_evidence TEXT NOT NULL,
    PRIMARY KEY (release_id, catalog, source_row),
    FOREIGN KEY (release_id, catalog) REFERENCES catalog_sheet(release_id, catalog)
) STRICT;

CREATE INDEX group_definition_code ON group_definition(release_id, code);

-- Catalog base points without modifiers (hundredths) and financed days.
-- An empty source cell has no row here and is never stored as zero.
CREATE TABLE group_base_value (
    release_id TEXT NOT NULL,
    catalog TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    measure TEXT NOT NULL,
    unit TEXT NOT NULL CHECK (unit IN ('points_hundredths', 'days')),
    amount INTEGER NOT NULL CHECK (amount > 0),
    source_column TEXT NOT NULL,
    CHECK (
        (unit = 'days')
        = (measure IN ('financed_stay', 'financed_stay_excluding_day_stay'))
    ),
    PRIMARY KEY (release_id, catalog, source_row, measure),
    FOREIGN KEY (release_id, catalog, source_row)
        REFERENCES group_definition(release_id, catalog, source_row)
) STRICT;

CREATE TABLE group_scope (
    release_id TEXT NOT NULL,
    catalog TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    position INTEGER NOT NULL,
    marker INTEGER NOT NULL CHECK (marker IN (1, 2, 3)),
    PRIMARY KEY (release_id, catalog, source_row, position),
    FOREIGN KEY (release_id, catalog, source_row)
        REFERENCES group_definition(release_id, catalog, source_row),
    FOREIGN KEY (release_id, catalog, position)
        REFERENCES catalog_scope(release_id, catalog, position)
) STRICT;
