ALTER TABLE source_schedules RENAME TO source_schedules_v2;

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

INSERT INTO source_schedules (
    source_id,
    run_mode,
    cron_expression,
    next_due_at_ms,
    misfire_grace_seconds,
    retry_max_attempts,
    retry_base_seconds,
    retry_max_seconds,
    pending_scheduled_at_ms,
    pending_run_mode,
    updated_at_ms
)
SELECT
    ss.source_id,
    ss.run_mode,
    ss.cron_expression,
    ss.next_due_at_ms,
    ss.misfire_grace_seconds,
    ss.retry_max_attempts,
    ss.retry_base_seconds,
    ss.retry_max_seconds,
    r.scheduled_at_ms,
    r.run_mode,
    ss.updated_at_ms
FROM source_schedules_v2 AS ss
LEFT JOIN watcher_runs AS r
  ON r.source_id = ss.source_id
 AND r.status IN ('running', 'committed', 'heartbeat_pending')
 AND r.run_mode IN ('scheduled', 'weekly_verification', 'catch_up')
 AND ss.run_mode = CASE
     WHEN r.run_mode = 'weekly_verification' THEN 'weekly_verification'
     ELSE 'scheduled'
 END;

DROP TABLE source_schedules_v2;

UPDATE control_state SET schema_version = 3 WHERE singleton_id = 1;

PRAGMA user_version = 3;
