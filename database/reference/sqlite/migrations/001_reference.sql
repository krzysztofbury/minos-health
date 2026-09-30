CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY CHECK (version = 1),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
) STRICT;

CREATE TABLE build_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE source_release (
    id TEXT PRIMARY KEY,
    system TEXT NOT NULL,
    origin TEXT NOT NULL,
    source_version TEXT NOT NULL,
    source_file_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    publisher TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    source_created_at TEXT,
    valid_from TEXT,
    valid_to TEXT,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    license TEXT NOT NULL,
    notices_json TEXT NOT NULL CHECK (json_valid(notices_json)),
    header_json TEXT NOT NULL CHECK (json_valid(header_json)),
    UNIQUE (system, id)
) STRICT;

CREATE TABLE reference_node (
    source_release_id TEXT NOT NULL REFERENCES source_release(id),
    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
    source_code TEXT NOT NULL,
    source_text TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('classification', 'range', 'modifier', 'text', 'container')),
    PRIMARY KEY (source_release_id, ordinal)
) STRICT;

CREATE TABLE reference_node_attribute (
    source_release_id TEXT NOT NULL,
    node_ordinal INTEGER NOT NULL,
    source_order INTEGER NOT NULL CHECK (source_order > 0),
    name TEXT NOT NULL CHECK (length(name) > 0),
    source_text TEXT NOT NULL,
    PRIMARY KEY (source_release_id, node_ordinal, name),
    UNIQUE (source_release_id, node_ordinal, source_order),
    FOREIGN KEY (source_release_id, node_ordinal)
        REFERENCES reference_node(source_release_id, ordinal) DEFERRABLE INITIALLY DEFERRED
) STRICT;

CREATE TABLE reference_relation (
    source_release_id TEXT NOT NULL,
    parent_ordinal INTEGER NOT NULL,
    child_ordinal INTEGER NOT NULL,
    relation_type TEXT NOT NULL CHECK (relation_type IN ('contains', 'is_a')),
    CHECK (parent_ordinal <> child_ordinal),
    PRIMARY KEY (source_release_id, parent_ordinal, child_ordinal, relation_type),
    FOREIGN KEY (source_release_id, parent_ordinal)
        REFERENCES reference_node(source_release_id, ordinal) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (source_release_id, child_ordinal)
        REFERENCES reference_node(source_release_id, ordinal) DEFERRABLE INITIALLY DEFERRED
) STRICT;

CREATE INDEX reference_relation_child ON reference_relation (
    source_release_id, child_ordinal, relation_type, parent_ordinal
);

CREATE TABLE concept (
    source_release_id TEXT NOT NULL,
    node_ordinal INTEGER NOT NULL,
    lookup_code TEXT NOT NULL CHECK (length(lookup_code) > 0),
    PRIMARY KEY (source_release_id, node_ordinal),
    UNIQUE (source_release_id, lookup_code),
    FOREIGN KEY (source_release_id, node_ordinal)
        REFERENCES reference_node(source_release_id, ordinal) DEFERRABLE INITIALLY DEFERRED
) STRICT;

CREATE TABLE concept_description (
    id INTEGER PRIMARY KEY,
    source_release_id TEXT NOT NULL,
    node_ordinal INTEGER NOT NULL,
    language TEXT NOT NULL CHECK (length(language) BETWEEN 2 AND 35),
    description_type TEXT NOT NULL CHECK (description_type IN ('preferred', 'translation', 'alternate')),
    source_text TEXT NOT NULL CHECK (length(trim(source_text)) > 0),
    normalized_text TEXT NOT NULL CHECK (length(normalized_text) > 0),
    UNIQUE (source_release_id, node_ordinal, language, description_type),
    FOREIGN KEY (source_release_id, node_ordinal)
        REFERENCES concept(source_release_id, node_ordinal) DEFERRABLE INITIALLY DEFERRED
) STRICT;

CREATE INDEX concept_description_normalized ON concept_description (
    source_release_id, normalized_text, node_ordinal
);

CREATE VIRTUAL TABLE concept_description_fts USING fts5(
    normalized_text,
    content='concept_description',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 0'
);
