ALTER TABLE schema_migration RENAME TO schema_migration_v1;
CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY CHECK (version IN (1, 2)),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
) STRICT;
INSERT INTO schema_migration SELECT version, sha256 FROM schema_migration_v1;
DROP TABLE schema_migration_v1;

CREATE TABLE search_document (
    id INTEGER PRIMARY KEY,
    source_release_id TEXT NOT NULL,
    node_ordinal INTEGER NOT NULL,
    language TEXT NOT NULL,
    description_type TEXT NOT NULL CHECK (description_type IN ('preferred', 'alternate', 'translation', 'context')),
    description_id INTEGER REFERENCES concept_description(id),
    attribute_name TEXT,
    normalized_text TEXT NOT NULL CHECK (length(normalized_text) > 0),
    token_text TEXT NOT NULL,
    CHECK (description_id IS NULL OR attribute_name IS NULL),
    CHECK ((description_type = 'context') = (description_id IS NULL)),
    UNIQUE (source_release_id, id),
    FOREIGN KEY (source_release_id, node_ordinal) REFERENCES reference_node(source_release_id, ordinal)
) STRICT;
CREATE INDEX search_document_exact ON search_document (source_release_id, language, normalized_text, id);
CREATE INDEX search_document_node ON search_document (source_release_id, node_ordinal, id);

CREATE TABLE search_owner (
    source_release_id TEXT NOT NULL,
    document_id INTEGER NOT NULL,
    concept_ordinal INTEGER NOT NULL,
    PRIMARY KEY (source_release_id, document_id, concept_ordinal),
    FOREIGN KEY (source_release_id, document_id) REFERENCES search_document(source_release_id, id),
    FOREIGN KEY (source_release_id, concept_ordinal) REFERENCES concept(source_release_id, node_ordinal)
) STRICT;

CREATE TABLE search_word (
    source_release_id TEXT NOT NULL,
    language TEXT NOT NULL,
    word TEXT NOT NULL CHECK (length(word) BETWEEN 1 AND 64),
    characters INTEGER NOT NULL CHECK (characters = length(word)),
    PRIMARY KEY (source_release_id, language, word)
) STRICT;
CREATE INDEX search_word_length ON search_word (source_release_id, language, characters, word);

CREATE TABLE search_token (
    source_release_id TEXT NOT NULL,
    language TEXT NOT NULL,
    word TEXT NOT NULL,
    document_id INTEGER NOT NULL,
    PRIMARY KEY (source_release_id, language, word, document_id),
    FOREIGN KEY (source_release_id, language, word) REFERENCES search_word(source_release_id, language, word),
    FOREIGN KEY (source_release_id, document_id) REFERENCES search_document(source_release_id, id)
) STRICT;

CREATE TABLE search_word_lemma (
    source_release_id TEXT NOT NULL,
    language TEXT NOT NULL,
    lemma TEXT NOT NULL CHECK (length(lemma) BETWEEN 1 AND 64),
    word TEXT NOT NULL,
    PRIMARY KEY (source_release_id, language, lemma, word),
    FOREIGN KEY (source_release_id, language, word) REFERENCES search_word(source_release_id, language, word)
) STRICT;

CREATE VIRTUAL TABLE search_document_fts USING fts5(
    token_text, content='search_document', content_rowid='id',
    tokenize='unicode61 remove_diacritics 0', prefix='3 4'
);
