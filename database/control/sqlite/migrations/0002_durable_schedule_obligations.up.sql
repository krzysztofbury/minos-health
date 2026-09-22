ALTER TABLE source_schedules RENAME TO source_schedules_v1;

CREATE TABLE source_schedules (
    source_id TEXT NOT NULL,
    run_mode TEXT NOT NULL,
    cron_expression TEXT NOT NULL,
    next_due_at_ms INTEGER NOT NULL,
    misfire_grace_seconds INTEGER NOT NULL,
    retry_max_attempts INTEGER NOT NULL,
    retry_base_seconds REAL NOT NULL,
    retry_max_seconds REAL NOT NULL,
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
    CHECK (updated_at_ms BETWEEN 1 AND 253402300799999)
) STRICT;

INSERT INTO source_schedules (
    source_id,
    run_mode,
    cron_expression,
    next_due_at_ms,
    misfire_grace_seconds,
    retry_max_attempts,
    retry_base_seconds,
    retry_max_seconds,
    updated_at_ms
)
SELECT
    source_id,
    'scheduled',
    cron_expression,
    next_due_at_ms,
    misfire_grace_seconds,
    retry_max_attempts,
    retry_base_seconds,
    retry_max_seconds,
    updated_at_ms
FROM source_schedules_v1;

DROP TABLE source_schedules_v1;

UPDATE control_state SET schema_version = 2 WHERE singleton_id = 1;

PRAGMA user_version = 2;
