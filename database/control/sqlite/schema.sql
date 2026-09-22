PRAGMA user_version = 3;

CREATE TABLE control_schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    up_sha256 TEXT NOT NULL,
    down_sha256 TEXT NOT NULL,
    minimum_reader_version INTEGER NOT NULL,
    applied_at_ms INTEGER NOT NULL,
    CHECK (version > 0),
    CHECK (length(name) BETWEEN 1 AND 100),
    CHECK (length(up_sha256) = 64 AND up_sha256 NOT GLOB '*[^0-9a-f]*'),
    CHECK (length(down_sha256) = 64 AND down_sha256 NOT GLOB '*[^0-9a-f]*'),
    CHECK (minimum_reader_version BETWEEN 1 AND version),
    CHECK (applied_at_ms BETWEEN 1 AND 253402300799999)
) STRICT;

CREATE TABLE control_state (
    singleton_id INTEGER PRIMARY KEY,
    state_revision INTEGER NOT NULL,
    schema_version INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    CHECK (singleton_id = 1),
    CHECK (state_revision >= 0),
    CHECK (schema_version > 0),
    CHECK (updated_at_ms BETWEEN 1 AND 253402300799999)
) STRICT;

CREATE TABLE sources (
    source_id TEXT PRIMARY KEY,
    watcher_type TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    current_configuration_revision INTEGER,
    fencing_epoch INTEGER NOT NULL,
    created_at_ms INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    CHECK (length(source_id) BETWEEN 1 AND 64),
    CHECK (length(watcher_type) BETWEEN 1 AND 64),
    CHECK (enabled IN (0, 1)),
    CHECK (enabled = 0 OR current_configuration_revision IS NOT NULL),
    CHECK (current_configuration_revision IS NULL OR current_configuration_revision > 0),
    CHECK (fencing_epoch >= 0),
    CHECK (created_at_ms BETWEEN 1 AND 253402300799999),
    CHECK (updated_at_ms BETWEEN created_at_ms AND 253402300799999),
    FOREIGN KEY (source_id, current_configuration_revision)
        REFERENCES source_configuration_revisions (source_id, configuration_revision)
        DEFERRABLE INITIALLY DEFERRED
) STRICT;

CREATE TABLE source_configuration_revisions (
    source_id TEXT NOT NULL,
    configuration_revision INTEGER NOT NULL,
    configuration_sha256 TEXT NOT NULL,
    configuration_json TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    PRIMARY KEY (source_id, configuration_revision),
    FOREIGN KEY (source_id) REFERENCES sources (source_id) ON DELETE RESTRICT,
    CHECK (configuration_revision > 0),
    CHECK (
        length(configuration_sha256) = 64
        AND configuration_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    CHECK (length(configuration_json) BETWEEN 2 AND 65536),
    CHECK (json_valid(configuration_json)),
    CHECK (json_type(configuration_json) = 'object'),
    CHECK (created_at_ms BETWEEN 1 AND 253402300799999)
) STRICT;

CREATE TABLE source_schedules (
    source_id TEXT NOT NULL,
    run_mode TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    next_due_at_ms INTEGER NOT NULL,
    misfire_grace_seconds INTEGER NOT NULL,
    retry_max_attempts INTEGER NOT NULL,
    retry_base_seconds REAL NOT NULL,
    retry_max_seconds REAL NOT NULL,
    pending_scheduled_at_ms INTEGER,
    pending_run_mode TEXT,
    retry_not_before_ms INTEGER,
    retry_failure_count INTEGER NOT NULL DEFAULT 0,
    updated_at_ms INTEGER NOT NULL,
    PRIMARY KEY (source_id, run_mode),
    FOREIGN KEY (source_id) REFERENCES sources (source_id) ON DELETE RESTRICT,
    CHECK (run_mode IN ('scheduled', 'weekly_verification')),
    CHECK (length(cron_expression) BETWEEN 9 AND 256),
    CHECK (next_due_at_ms BETWEEN 1 AND 253402300799999),
    CHECK (misfire_grace_seconds BETWEEN 1 AND 86400),
    CHECK (retry_max_attempts BETWEEN 1 AND 10),
    CHECK (retry_base_seconds > 0 AND retry_base_seconds <= 300),
    CHECK (retry_max_seconds >= retry_base_seconds AND retry_max_seconds <= 3600),
    CHECK (
        pending_scheduled_at_ms IS NULL
        OR pending_scheduled_at_ms BETWEEN 1 AND 253402300799999
    ),
    CHECK (
        pending_run_mode IS NULL
        OR pending_run_mode IN ('scheduled', 'weekly_verification', 'catch_up')
    ),
    CHECK (
        retry_not_before_ms IS NULL
        OR retry_not_before_ms BETWEEN 1 AND 253402300799999
    ),
    CHECK (retry_failure_count BETWEEN 0 AND 31),
    CHECK (
        (pending_scheduled_at_ms IS NULL AND pending_run_mode IS NULL
            AND retry_not_before_ms IS NULL AND retry_failure_count = 0)
        OR (pending_scheduled_at_ms IS NOT NULL AND pending_run_mode IS NOT NULL)
    ),
    CHECK (
        (run_mode = 'scheduled' AND pending_run_mode IN ('scheduled', 'catch_up'))
        OR (run_mode = 'weekly_verification'
            AND pending_run_mode = 'weekly_verification')
        OR pending_run_mode IS NULL
    ),
    CHECK (updated_at_ms BETWEEN 1 AND 253402300799999)
) STRICT;

CREATE TABLE watcher_runs (
    run_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    run_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    claim_token TEXT NOT NULL UNIQUE,
    fencing_epoch INTEGER NOT NULL,
    configuration_revision INTEGER NOT NULL,
    scheduled_at_ms INTEGER NOT NULL,
    started_at_ms INTEGER NOT NULL,
    deadline_at_ms INTEGER NOT NULL,
    committed_at_ms INTEGER,
    finished_at_ms INTEGER,
    committed_state_revision INTEGER,
    error_category TEXT,
    UNIQUE (run_id, source_id),
    FOREIGN KEY (source_id) REFERENCES sources (source_id) ON DELETE RESTRICT,
    FOREIGN KEY (source_id, configuration_revision)
        REFERENCES source_configuration_revisions (source_id, configuration_revision)
        ON DELETE RESTRICT,
    CHECK (length(run_id) BETWEEN 16 AND 128),
    CHECK (run_mode IN ('scheduled', 'weekly_verification', 'manual', 'catch_up')),
    CHECK (status IN ('running', 'committed', 'heartbeat_pending', 'successful', 'failed', 'expired')),
    CHECK (length(claim_token) BETWEEN 32 AND 128),
    CHECK (fencing_epoch > 0),
    CHECK (configuration_revision > 0),
    CHECK (scheduled_at_ms BETWEEN 1 AND started_at_ms),
    CHECK (started_at_ms BETWEEN 1 AND 253402300799998),
    CHECK (deadline_at_ms BETWEEN started_at_ms + 1 AND 253402300799999),
    CHECK (committed_at_ms IS NULL OR committed_at_ms BETWEEN started_at_ms AND deadline_at_ms - 1),
    CHECK (finished_at_ms IS NULL OR finished_at_ms BETWEEN started_at_ms AND 253402300799999),
    CHECK (committed_state_revision IS NULL OR committed_state_revision > 0),
    CHECK (error_category IS NULL OR length(error_category) BETWEEN 1 AND 64),
    CHECK (
        (status = 'running' AND committed_at_ms IS NULL AND finished_at_ms IS NULL
            AND committed_state_revision IS NULL AND error_category IS NULL)
        OR (status IN ('committed', 'heartbeat_pending') AND committed_at_ms IS NOT NULL
            AND finished_at_ms IS NULL AND committed_state_revision IS NOT NULL
            AND error_category IS NULL)
        OR (status = 'successful' AND committed_at_ms IS NOT NULL
            AND finished_at_ms >= committed_at_ms AND committed_state_revision IS NOT NULL
            AND error_category IS NULL)
        OR (status = 'failed' AND committed_at_ms IS NULL
            AND finished_at_ms IS NOT NULL AND committed_state_revision IS NULL
            AND error_category IS NOT NULL)
        OR (status = 'expired' AND committed_at_ms IS NULL
            AND finished_at_ms >= deadline_at_ms AND committed_state_revision IS NULL
            AND error_category IS NOT NULL)
    )
) STRICT;

CREATE UNIQUE INDEX watcher_runs_one_blocking_per_source
ON watcher_runs (source_id)
WHERE status IN ('running', 'committed', 'heartbeat_pending');

CREATE INDEX watcher_runs_source_started
ON watcher_runs (source_id, started_at_ms);

CREATE INDEX watcher_runs_status_started
ON watcher_runs (status, started_at_ms);

CREATE TABLE blobs (
    sha256 TEXT PRIMARY KEY,
    size_bytes INTEGER NOT NULL,
    media_type TEXT,
    created_at_ms INTEGER NOT NULL,
    CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    CHECK (size_bytes >= 0),
    CHECK (media_type IS NULL OR length(media_type) BETWEEN 1 AND 255),
    CHECK (created_at_ms BETWEEN 1 AND 253402300799999)
) STRICT;

CREATE TABLE observations (
    observation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    observation_kind TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    response_sha256 TEXT NOT NULL,
    observed_at_ms INTEGER NOT NULL,
    metadata_json TEXT NOT NULL,
    state_revision INTEGER NOT NULL,
    UNIQUE (observation_id, source_id, run_id),
    FOREIGN KEY (run_id, source_id) REFERENCES watcher_runs (run_id, source_id) ON DELETE RESTRICT,
    FOREIGN KEY (source_id) REFERENCES sources (source_id) ON DELETE RESTRICT,
    FOREIGN KEY (response_sha256) REFERENCES blobs (sha256) ON DELETE RESTRICT,
    CHECK (length(observation_id) BETWEEN 16 AND 128),
    CHECK (length(observation_kind) BETWEEN 1 AND 64),
    CHECK (length(source_identity) BETWEEN 1 AND 512),
    CHECK (observed_at_ms BETWEEN 1 AND 253402300799999),
    CHECK (length(metadata_json) BETWEEN 2 AND 262144),
    CHECK (json_valid(metadata_json)),
    CHECK (json_type(metadata_json) = 'object'),
    CHECK (state_revision > 0)
) STRICT;

CREATE INDEX observations_source_observed
ON observations (source_id, observed_at_ms);

CREATE INDEX observations_run
ON observations (run_id);

CREATE TABLE observation_blobs (
    observation_id TEXT NOT NULL,
    blob_role TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (observation_id, blob_role, ordinal),
    FOREIGN KEY (observation_id) REFERENCES observations (observation_id) ON DELETE RESTRICT,
    FOREIGN KEY (sha256) REFERENCES blobs (sha256) ON DELETE RESTRICT,
    CHECK (length(blob_role) BETWEEN 1 AND 64),
    CHECK (ordinal >= 0)
) STRICT;

CREATE INDEX observation_blobs_sha256
ON observation_blobs (sha256);

CREATE TABLE source_cursors (
    source_id TEXT PRIMARY KEY,
    cursor_json TEXT NOT NULL,
    state_revision INTEGER NOT NULL,
    updated_at_ms INTEGER NOT NULL,
    FOREIGN KEY (source_id) REFERENCES sources (source_id) ON DELETE RESTRICT,
    CHECK (length(cursor_json) BETWEEN 2 AND 262144),
    CHECK (json_valid(cursor_json)),
    CHECK (json_type(cursor_json) = 'object'),
    CHECK (state_revision > 0),
    CHECK (updated_at_ms BETWEEN 1 AND 253402300799999)
) STRICT;

CREATE TABLE source_changes (
    change_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    change_category TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    state_revision INTEGER NOT NULL,
    created_at_ms INTEGER NOT NULL,
    FOREIGN KEY (run_id, source_id) REFERENCES watcher_runs (run_id, source_id) ON DELETE RESTRICT,
    FOREIGN KEY (observation_id, source_id, run_id)
        REFERENCES observations (observation_id, source_id, run_id) ON DELETE RESTRICT,
    CHECK (length(change_id) BETWEEN 16 AND 128),
    CHECK (length(change_category) BETWEEN 1 AND 64),
    CHECK (length(summary_json) BETWEEN 2 AND 65536),
    CHECK (json_valid(summary_json)),
    CHECK (json_type(summary_json) = 'object'),
    CHECK (state_revision > 0),
    CHECK (created_at_ms BETWEEN 1 AND 253402300799999)
) STRICT;

CREATE INDEX source_changes_source_created
ON source_changes (source_id, created_at_ms);

CREATE TABLE incidents (
    incident_id TEXT PRIMARY KEY,
    source_id TEXT,
    run_id TEXT,
    incident_category TEXT NOT NULL,
    details_json TEXT NOT NULL,
    state_revision INTEGER NOT NULL,
    created_at_ms INTEGER NOT NULL,
    resolved_at_ms INTEGER,
    FOREIGN KEY (source_id) REFERENCES sources (source_id) ON DELETE RESTRICT,
    FOREIGN KEY (run_id, source_id) REFERENCES watcher_runs (run_id, source_id) ON DELETE RESTRICT,
    CHECK (run_id IS NULL OR source_id IS NOT NULL),
    CHECK (length(incident_id) BETWEEN 16 AND 128),
    CHECK (length(incident_category) BETWEEN 1 AND 64),
    CHECK (length(details_json) BETWEEN 2 AND 65536),
    CHECK (json_valid(details_json)),
    CHECK (json_type(details_json) = 'object'),
    CHECK (state_revision > 0),
    CHECK (created_at_ms BETWEEN 1 AND 253402300799999),
    CHECK (resolved_at_ms IS NULL OR resolved_at_ms BETWEEN created_at_ms AND 253402300799999)
) STRICT;

CREATE INDEX incidents_open_created
ON incidents (created_at_ms)
WHERE resolved_at_ms IS NULL;

CREATE INDEX incidents_source_created
ON incidents (source_id, created_at_ms);

CREATE TABLE audit_events (
    audit_event_id TEXT PRIMARY KEY,
    source_id TEXT,
    run_id TEXT,
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL,
    state_revision INTEGER NOT NULL,
    created_at_ms INTEGER NOT NULL,
    FOREIGN KEY (source_id) REFERENCES sources (source_id) ON DELETE RESTRICT,
    FOREIGN KEY (run_id, source_id) REFERENCES watcher_runs (run_id, source_id) ON DELETE RESTRICT,
    CHECK (run_id IS NULL OR source_id IS NOT NULL),
    CHECK (length(audit_event_id) BETWEEN 16 AND 128),
    CHECK (length(event_type) BETWEEN 1 AND 64),
    CHECK (length(details_json) BETWEEN 2 AND 65536),
    CHECK (json_valid(details_json)),
    CHECK (json_type(details_json) = 'object'),
    CHECK (state_revision > 0),
    CHECK (created_at_ms BETWEEN 1 AND 253402300799999)
) STRICT;

CREATE INDEX audit_events_source_created
ON audit_events (source_id, created_at_ms);

INSERT INTO control_state (
    singleton_id,
    state_revision,
    schema_version,
    updated_at_ms
) VALUES (
    1,
    0,
    3,
    CAST(strftime('%s', 'now') AS INTEGER) * 1000
);

INSERT INTO control_schema_migrations (
    version,
    name,
    up_sha256,
    down_sha256,
    minimum_reader_version,
    applied_at_ms
) VALUES (
    1,
    'initial_control_schema',
    'be2adaa27b180d1b6f5677887b5e649a536cdd81b13a252018ab43b8cb63832b',
    '936380d5b230f7521bd409d18022af96d83b1befccde676d52cee404f8ba116c',
    1,
    CAST(strftime('%s', 'now') AS INTEGER) * 1000
);

INSERT INTO control_schema_migrations (
    version,
    name,
    up_sha256,
    down_sha256,
    minimum_reader_version,
    applied_at_ms
) VALUES (
    2,
    'durable_schedule_obligations',
    'f965414badfdc0f8d2be1366cc8ef1f1f9ab11c0f5347e99689bd7de4a32b402',
    '4ede51a550339ddca1201c0cd5a045e6de4a33f9745ecdf26a9df5ff4b56bf90',
    2,
    CAST(strftime('%s', 'now') AS INTEGER) * 1000
);

INSERT INTO control_schema_migrations (
    version,
    name,
    up_sha256,
    down_sha256,
    minimum_reader_version,
    applied_at_ms
) VALUES (
    3,
    'retryable_schedule_obligations',
    '05acc0a63711bc39b2921d30e882548cca3a5c42c6943886fa3ff3ce6a57a075',
    '9431ca930c74c5d8a61652fde7b93eee46f50b2000857d99cbe3db2a09ef4a90',
    3,
    CAST(strftime('%s', 'now') AS INTEGER) * 1000
);
