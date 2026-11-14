# SQLite backups, recovery and lifecycle

Evlet keeps the four SQLite stores. Records, audits and business invariants remain in the main
file; session, log and Eva writes retain their independent writer lanes. No database server or
additional service is required.

## Backups

Controls field changes, `evlet backup` and installation backups use the same complete-set copier.
For a split instance, keep the main backup, its `-sessions`, `-logs` and `-eva` files, and the
adjacent `.sqlite3.manifest.json` together. The manifest checks every member's SHA-256 digest.
An old single-file backup remains supported; a partially completed split is backed up as a
complete set, including tables that have already moved.

A brief SQLite `BEGIN IMMEDIATE` barrier excludes writers across processes while a separate
reader pins all four snapshots. The barrier is released before the online copies. Busy writers
can make the command fail with the normal SQLite timeout; retry the backup after they finish.
This does not make ordinary multi-file WAL transactions crash-atomic.

Back up `secret.key`, configuration, app sources and any external object/file storage separately.
The database set is not a substitute for those operator backups. A restore validates all members
before changing live data and preserves a safety backup of the current database set.

## Interrupted installation or restore

An instance must be stopped for replacement. Before publishing a generation, Evlet fsyncs its
staged files and writes `.install-journal.json`. It retains those files until all live database
members and `apps.lock` are durable and the journal deletion has been fsynced.

Startup refuses an instance with a pending journal. Run `evlet migrate INSTANCE` to replay the
retained generation; replay can itself be interrupted and retried. Keep the journal and its
`data/.publish-*` directory together until recovery succeeds. Do not remove a lock or journal
to force startup.

Old-format journals have only `before`/`after` main-file metadata. They cannot prove which side
files survived an interruption, even when the main file appears new. Restore an explicitly
selected complete backup with `evlet restore BACKUP --instance INSTANCE --yes INSTANCE` instead
of accepting that ambiguous generation. Pre-split single-file backups still restore; run migrate
afterward to move their side tables into the current layout.

## Connections, logs and key rotation

The idle pool keeps at most two attached connections per Database and eight across the process,
independent of worker identities. Idle entries older than 60 seconds are pruned at the next pool
operation. Active connections belong to their admitted requests and are never evicted. Closing a
Database invalidates older checkouts so their return cannot refill its idle pool.

Shutdown drains requests before closing databases. Database close joins an in-flight log writer
and drains accepted queued writes. Failed batches remain queued and close reports an error;
it no longer silently reports success while an earlier batch is still writing.
The retry backlog is capped at 4,096 best-effort metadata writes. A prolonged storage failure
discards the oldest excess metadata, increments `database.logs.dropped`, and emits a rate-limited
warning. Business records and their audit entries use synchronous transactions and are unaffected.

Key rotations hold an interprocess lock from keyring read through commit and pruning. The new
key is published only after all database writer locks are held, and old keys remain available
until the re-encrypted values commit. A competing rotation fails clearly and can be retried.
The CLI also holds the instance's shared lifecycle lease, so restoration cannot replace its
files during rotation. Key-file replacement and its directory are fsynced.

## Project upgrades

`evlet upgrade INSTANCE` copies the project environment into a temporary staging directory,
installs the new release there, and plans against staged database copies. Declining the plan or
failing installation or planning leaves the live environment and services unchanged.

If other installed instances share `.venv`, use `--all-instances`. Evlet validates every service,
stops them, captures final rollback backups including the last accepted writes, checks that
their lifecycle leases were released, and only then publishes the prepared environment.
A backup failure keeps the original environment and restarts its services.
Evlet migrates every instance before restarting any of
them. `--service` overrides the selected instance's unit; siblings use `evlet-NAME` units.

A failed publication restores the original environment when possible. After publication, a
failed migration or restart retains the previous environment and every database backup, and
prints exact rollback commands. The same commands are recorded in `.evlet-upgrade.json`;
another upgrade refuses to proceed until that interrupted upgrade is recovered. Follow the
recorded stop, environment restore, database restore and restart sequence for the entire group.
After a successful health-checked restart, the temporary previous environment is removed;
database backups remain under each instance's configured backup directory.

Preparing an environment needs free space for one copy of `.venv`; database backup and migration
space checks remain separate. A project-level interprocess lock excludes concurrent upgrades.
