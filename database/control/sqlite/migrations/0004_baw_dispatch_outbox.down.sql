CREATE TABLE migration_0004_down_guard (
    unrepresentable_state INTEGER NOT NULL,
    CHECK (unrepresentable_state = 0)
) STRICT;

INSERT INTO migration_0004_down_guard (unrepresentable_state)
SELECT 1 WHERE EXISTS (SELECT 1 FROM baw_dispatch_outbox);

DROP TABLE migration_0004_down_guard;
DROP INDEX baw_dispatch_pending;
DROP TABLE baw_dispatch_outbox;

UPDATE control_state SET schema_version = 3 WHERE singleton_id = 1;
PRAGMA user_version = 3;
