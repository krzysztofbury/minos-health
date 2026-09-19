# Control SQLite Schema

The watcher control database uses Python's `sqlite3` module and raw SQL. It has
no ORM and no library-owned migration state.

## Files

- `schema.sql` is the canonical, readable definition of the current complete
  schema.
- `migrations/NNNN_name.up.sql` upgrades an existing database by one version.
- `migrations/NNNN_name.down.sql` exists for local and automated rollback tests.

Runtime initialization always applies numbered `up` migrations. `schema.sql` is
not a separate runtime bootstrap path. Tests apply both paths to empty databases
and require equivalent schemas, migration checksums, and singleton control
state.

Run migrations on a dedicated connection before installing any long-lived
SQLite authorizer. The migration runner installs a temporary restrictive
authorizer and clears it after each script; Python does not expose the previous
callback for restoration.

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
