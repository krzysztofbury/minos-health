ALTER TABLE schema_migration RENAME TO previous_schema_migration;
CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY CHECK (version IN (1, 2, 3, 4, 5)),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
) STRICT;
INSERT INTO schema_migration SELECT version, sha256 FROM previous_schema_migration;
DROP TABLE previous_schema_migration;

-- Reference correspondence is separate from executable service-date binding.
CREATE TABLE characteristics_release (
    id TEXT PRIMARY KEY,
    manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json)),
    valid_from TEXT NOT NULL CHECK (valid_from = date(valid_from)),
    valid_to TEXT CHECK (valid_to = date(valid_to)),
    published_on TEXT NOT NULL CHECK (published_on = date(published_on)),
    recorded_at TEXT NOT NULL,
    applicability_evidence TEXT NOT NULL,
    icd9_release_id TEXT NOT NULL REFERENCES source_release(id),
    icd10_release_id TEXT NOT NULL REFERENCES source_release(id),
    catalog_release_id TEXT REFERENCES catalog_release(id),
    supersedes TEXT REFERENCES characteristics_release(id),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
) STRICT;
CREATE TABLE characteristics_knowledge (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    as_of TEXT NOT NULL CHECK (as_of = date(as_of)),
    evidence TEXT NOT NULL
) STRICT;
CREATE TABLE characteristics_sheet (
    release_id TEXT NOT NULL REFERENCES characteristics_release(id),
    catalog TEXT NOT NULL CHECK (catalog IN ('1a', '1ae')),
    worksheet TEXT NOT NULL,
    PRIMARY KEY (release_id, catalog)
) STRICT;
CREATE TABLE characteristics_evidence (
    release_id TEXT NOT NULL,
    catalog TEXT NOT NULL,
    row INTEGER NOT NULL CHECK (row BETWEEN 1 AND 50000),
    section TEXT,
    context TEXT,
    source_text TEXT NOT NULL CHECK (length(source_text) <= 8192),
    category TEXT NOT NULL CHECK (category IN ('section', 'title', 'list_heading', 'marker', 'member', 'unbound_member', 'label', 'connector')),
    PRIMARY KEY (release_id, catalog, row),
    FOREIGN KEY (release_id, catalog) REFERENCES characteristics_sheet(release_id, catalog)
) STRICT;
CREATE TABLE characteristics_group (
    release_id TEXT NOT NULL,
    catalog TEXT NOT NULL,
    code TEXT NOT NULL,
    title_row INTEGER NOT NULL,
    roots_json TEXT NOT NULL CHECK (json_valid(roots_json) AND length(roots_json) <= 65536),
    PRIMARY KEY (release_id, catalog, code),
    FOREIGN KEY (release_id, catalog, title_row) REFERENCES characteristics_evidence(release_id, catalog, row)
) STRICT;
CREATE TABLE directional_list (
    release_id TEXT NOT NULL,
    catalog TEXT NOT NULL,
    id INTEGER NOT NULL CHECK (id BETWEEN 1 AND 3000),
    label TEXT NOT NULL,
    system TEXT NOT NULL CHECK (system IN ('icd9-pl', 'icd10-pl')),
    context TEXT NOT NULL,
    marker_row INTEGER NOT NULL,
    naming_rows_json TEXT NOT NULL CHECK (json_valid(naming_rows_json) AND length(naming_rows_json) <= 65536),
    PRIMARY KEY (release_id, catalog, id),
    FOREIGN KEY (release_id, catalog, marker_row) REFERENCES characteristics_evidence(release_id, catalog, row)
) STRICT;
CREATE TABLE characteristics_group_catalog (
    release_id TEXT NOT NULL,
    catalog TEXT NOT NULL,
    code TEXT NOT NULL,
    catalog_release_id TEXT REFERENCES catalog_release(id),
    correspondence TEXT NOT NULL CHECK (correspondence IN ('matched', 'not_in_catalog', 'catalog_unbound')),
    CHECK ((catalog_release_id IS NULL) = (correspondence = 'catalog_unbound')),
    PRIMARY KEY (release_id, catalog, code),
    FOREIGN KEY (release_id, catalog, code) REFERENCES characteristics_group(release_id, catalog, code)
) STRICT;
CREATE TABLE characteristics_catalog_row (
    release_id TEXT NOT NULL,
    catalog TEXT NOT NULL,
    code TEXT NOT NULL,
    catalog_release_id TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    PRIMARY KEY (release_id, catalog, code, source_row),
    FOREIGN KEY (release_id, catalog, code) REFERENCES characteristics_group_catalog(release_id, catalog, code),
    FOREIGN KEY (catalog_release_id, catalog, source_row) REFERENCES group_definition(release_id, catalog, source_row)
) STRICT;
CREATE TABLE characteristics_code (
    release_id TEXT NOT NULL,
    catalog TEXT NOT NULL,
    system TEXT NOT NULL CHECK (system IN ('icd9-pl', 'icd10-pl')),
    code TEXT NOT NULL,
    reference_release_id TEXT NOT NULL REFERENCES source_release(id),
    reference_node INTEGER,
    correspondence TEXT NOT NULL CHECK (correspondence IN ('matched', 'not_in_reference')),
    CHECK ((reference_node IS NOT NULL) = (correspondence = 'matched')),
    PRIMARY KEY (release_id, catalog, system, code),
    FOREIGN KEY (release_id, catalog) REFERENCES characteristics_sheet(release_id, catalog),
    FOREIGN KEY (reference_release_id, reference_node) REFERENCES concept(source_release_id, node_ordinal)
) STRICT;
CREATE TABLE characteristics_code_evidence (
    release_id TEXT NOT NULL,
    catalog TEXT NOT NULL,
    system TEXT NOT NULL,
    code TEXT NOT NULL,
    row INTEGER NOT NULL,
    PRIMARY KEY (release_id, catalog, system, code, row),
    FOREIGN KEY (release_id, catalog, system, code) REFERENCES characteristics_code(release_id, catalog, system, code),
    FOREIGN KEY (release_id, catalog, row) REFERENCES characteristics_evidence(release_id, catalog, row)
) STRICT;
CREATE TABLE directional_member (
    release_id TEXT NOT NULL,
    catalog TEXT NOT NULL,
    list_id INTEGER NOT NULL,
    row INTEGER NOT NULL,
    system TEXT NOT NULL,
    code TEXT NOT NULL,
    marks_json TEXT NOT NULL CHECK (json_valid(marks_json) AND length(marks_json) <= 1024),
    PRIMARY KEY (release_id, catalog, row),
    FOREIGN KEY (release_id, catalog, list_id) REFERENCES directional_list(release_id, catalog, id),
    FOREIGN KEY (release_id, catalog, system, code) REFERENCES characteristics_code(release_id, catalog, system, code),
    FOREIGN KEY (release_id, catalog, row) REFERENCES characteristics_evidence(release_id, catalog, row)
) STRICT;
CREATE TABLE condition_selector (
    release_id TEXT NOT NULL,
    catalog TEXT NOT NULL,
    id INTEGER NOT NULL CHECK (id BETWEEN 1 AND 20000),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json) AND length(payload_json) <= 65536),
    PRIMARY KEY (release_id, catalog, id),
    FOREIGN KEY (release_id, catalog) REFERENCES characteristics_sheet(release_id, catalog)
) STRICT;
CREATE TABLE group_condition (
    release_id TEXT NOT NULL,
    catalog TEXT NOT NULL,
    id INTEGER NOT NULL CHECK (id BETWEEN 1 AND 20000),
    source_order INTEGER NOT NULL CHECK (source_order BETWEEN 1 AND 20000),
    group_code TEXT NOT NULL,
    kind TEXT,
    interpretation_status TEXT NOT NULL CHECK (interpretation_status IN ('proposed', 'unsupported')),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json) AND length(payload_json) <= 65536),
    PRIMARY KEY (release_id, catalog, id),
    UNIQUE (release_id, catalog, source_order),
    FOREIGN KEY (release_id, catalog, group_code) REFERENCES characteristics_group(release_id, catalog, code)
) STRICT;
