ALTER TABLE schema_migration RENAME TO previous_schema_migration;

CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY CHECK (version IN (1, 2, 3, 4)),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
) STRICT;

INSERT INTO schema_migration SELECT version, sha256 FROM previous_schema_migration;
DROP TABLE previous_schema_migration;

-- Publication evidence for one attachment 1a workbook. Service-date
-- applicability is resolved separately and never by latest release.
CREATE TABLE catalog_release (
    id TEXT PRIMARY KEY,
    act_number TEXT NOT NULL,
    baw_document_id INTEGER NOT NULL UNIQUE CHECK (baw_document_id > 0),
    automatic_type INTEGER NOT NULL CHECK (automatic_type IN (1, 3)),
    act_date TEXT NOT NULL,
    entry_into_force TEXT,
    workbook_sha256 TEXT NOT NULL CHECK (length(workbook_sha256) = 64),
    manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json))
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
    notes TEXT,
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
