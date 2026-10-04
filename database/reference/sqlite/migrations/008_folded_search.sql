ALTER TABLE schema_migration RENAME TO previous_schema_migration;
CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY CHECK (version BETWEEN 1 AND 8),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
) STRICT;

CREATE TABLE search_jgp_member (
    release_id TEXT NOT NULL,
    catalog TEXT NOT NULL,
    system TEXT NOT NULL,
    code TEXT NOT NULL,
    group_code TEXT NOT NULL,
    PRIMARY KEY (release_id,catalog,system,code,group_code),
    FOREIGN KEY (release_id,catalog,group_code) REFERENCES characteristics_group(release_id,catalog,code)
) STRICT;

CREATE INDEX search_jgp_group ON search_jgp_member(release_id,catalog,group_code,system,code);

CREATE TABLE search_jgp_count (
    release_id TEXT NOT NULL,
    catalog TEXT NOT NULL,
    system TEXT NOT NULL,
    code TEXT NOT NULL,
    group_count INTEGER NOT NULL CHECK (group_count BETWEEN 1 AND 2000),
    PRIMARY KEY (release_id,catalog,system,code)
) STRICT;
INSERT INTO schema_migration SELECT version, sha256 FROM previous_schema_migration;
DROP TABLE previous_schema_migration;

ALTER TABLE search_document ADD COLUMN folded_text TEXT NOT NULL DEFAULT '';

CREATE TABLE search_synonym (
    id TEXT PRIMARY KEY,
    system TEXT NOT NULL,
    term TEXT NOT NULL,
    source_wording TEXT NOT NULL,
    entry_json TEXT NOT NULL CHECK (json_valid(entry_json)),
    UNIQUE (system,term)
) STRICT;

CREATE TABLE search_word_folded (
    source_release_id TEXT NOT NULL,
    language TEXT NOT NULL,
    folded TEXT NOT NULL CHECK (length(folded) BETWEEN 1 AND 64),
    word TEXT NOT NULL,
    PRIMARY KEY (source_release_id, language, folded, word),
    FOREIGN KEY (source_release_id, language, word)
        REFERENCES search_word(source_release_id, language, word)
) STRICT;
