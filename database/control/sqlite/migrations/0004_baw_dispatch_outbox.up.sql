CREATE TABLE baw_dispatch_outbox (
    discovery_source_id TEXT NOT NULL,
    document_id INTEGER NOT NULL,
    discovery_run_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    observation_sha256 TEXT NOT NULL,
    first_seen_at_ms INTEGER NOT NULL,
    delivered_run_id TEXT,
    delivered_observation_id TEXT,
    delivered_at_ms INTEGER,
    retained_root INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (discovery_source_id, document_id),
    FOREIGN KEY (discovery_source_id) REFERENCES sources (source_id) ON DELETE RESTRICT,
    FOREIGN KEY (observation_id, discovery_source_id, discovery_run_id)
        REFERENCES observations (observation_id, source_id, run_id) ON DELETE RESTRICT,
    FOREIGN KEY (observation_sha256) REFERENCES blobs (sha256) ON DELETE RESTRICT,
    FOREIGN KEY (delivered_run_id) REFERENCES watcher_runs (run_id) ON DELETE RESTRICT,
    FOREIGN KEY (delivered_observation_id) REFERENCES observations (observation_id)
        ON DELETE RESTRICT,
    CHECK (length(discovery_source_id) BETWEEN 1 AND 64),
    CHECK (document_id BETWEEN 1 AND 2147483647),
    CHECK (length(source_url) BETWEEN 1 AND 256),
    CHECK (length(observation_sha256) = 64 AND observation_sha256 NOT GLOB '*[^0-9a-f]*'),
    CHECK (first_seen_at_ms BETWEEN 1 AND 253402300799999),
    CHECK (delivered_at_ms IS NULL OR delivered_at_ms BETWEEN first_seen_at_ms AND 253402300799999),
    CHECK (retained_root IN (0, 1)),
    CHECK (retained_root = 0 OR delivered_run_id IS NOT NULL),
    CHECK (
        (delivered_run_id IS NULL AND delivered_observation_id IS NULL AND delivered_at_ms IS NULL)
        OR (delivered_run_id IS NOT NULL AND delivered_observation_id IS NOT NULL AND delivered_at_ms IS NOT NULL)
    )
) STRICT;

CREATE INDEX baw_dispatch_pending
ON baw_dispatch_outbox (first_seen_at_ms, discovery_source_id, document_id)
WHERE delivered_run_id IS NULL;

UPDATE control_state SET schema_version = 4 WHERE singleton_id = 1;
PRAGMA user_version = 4;
