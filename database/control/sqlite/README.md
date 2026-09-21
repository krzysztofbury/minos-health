# Control SQLite Schema

The watcher control database uses Python's `sqlite3` module and raw SQL. It has
no ORM and no library-owned migration state.

## Files

- `schema.sql` is the canonical, readable definition of the current complete
  schema.
- `migrations/NNNN_name.up.sql` upgrades an existing database by one version.
- `migrations/NNNN_name.down.sql` exists for local and automated rollback tests.

`up` and `down` are standard migration terms for applying and reversing a
schema change. Their presence does not change the production rollback policy
below, which restores a verified recovery image.

Runtime initialization always applies numbered `up` migrations. `schema.sql` is
not a separate runtime bootstrap path. Tests apply both paths to empty databases
and require equivalent schemas, migration checksums, and singleton control
state.

Run migrations on a dedicated connection before installing any long-lived
SQLite authorizer. The migration runner installs a temporary restrictive
authorizer and clears it after each script; Python does not expose the previous
callback for restoration.

## Table Purpose Map

| Table | Purpose |
| --- | --- |
| `control_schema_migrations` | Records applied migration identities, exact checksums, and reader compatibility. |
| `control_state` | Holds the singleton schema version and monotonic state revision. |
| `sources` | Defines watcher sources, enablement, active configuration, and fencing epoch. |
| `source_configuration_revisions` | Stores immutable, checksummed configuration revisions per source. |
| `source_schedules` | Stores UTC cron policy, durable due cursors, and bounded retry settings. |
| `watcher_runs` | Tracks fenced scheduled, catch-up, verification, and manual run lifecycles. |
| `blobs` | Catalogs content-addressed archived bytes by SHA-256, size, and media type. |
| `observations` | Records source observations committed by a run and their response blob. |
| `observation_blobs` | Links each observation to its ordered supporting blobs by role. |
| `source_cursors` | Stores the latest durable, source-specific comparison cursor. |
| `source_changes` | Records bounded changes derived from committed observations. |
| `incidents` | Records global, source, or run incidents, including failures and drift, with their resolution state. |
| `audit_events` | Appends revisioned control-plane mutation and transition evidence. |

## Runtime Writer

`ControlStore.open()` owns the sole runtime writer connection. It validates the
complete migration history, explicit transaction mode, foreign keys, WAL,
`synchronous=FULL`, a bounded busy timeout, untrusted schema handling, and
SQLite defensive mode before exposing transaction methods.

Configuration documents use canonical JSON and reject secret-like keys. Every
restorable mutation advances one monotonic state revision and appends an audit
event in the same `BEGIN IMMEDIATE` transaction. Scheduled claims compare and
advance the durable due cursor; manual claims leave it untouched. Collector
commits require a live token-and-epoch fenced claim and structurally validated,
digest-derived receipts emitted by the archive boundary, then atomically write
observations, cursor, changes, incidents, audit, and the committed run state.
The archive implementation remains responsible for native checksum and byte
verification. Reconciliation accepts a recovery receipt only when its image
revision equals the current database revision and records its immutable key and
digest before moving a run to `heartbeat_pending`.

## Changing The Schema

Every schema change must:

1. Add the next contiguous `up` and `down` migration pair.
2. Update `schema.sql` to show the complete new end state.
3. Add the migration to the explicit manifest in `migrations.py`.
4. Mark the lowest schema version that can safely read the new state.
5. Pass fresh-apply, upgrade, rollback, checksum, and DDL-equivalence tests.

Migration checksums cover the exact file bytes. Once a migration is applied in
an accepted environment, do not edit or reorder it. Correct mistakes with a new
migration.

## Rollback Policy

Down migrations are destructive test tools. Production rollback restores the
verified pre-deploy SQLite recovery image while watcher dispatch is stopped. It
does not execute down migrations against the live database.

Retained older watcher binaries may open a newer database only when every
migration they know has its exact checksum and every unknown higher migration
declares that older schema version as a compatible reader. Breaking migrations
must raise the minimum reader version.
