perf(task-center): set WAL journal mode once per database

Every TaskStore call opened a connection and re-ran PRAGMA journal_mode =
WAL, which takes a lock round-trip although the mode is persisted in the
database file. The pragma now runs only when the schema identity check
sees a new or replaced database, right before schema initialization.

Refs: B-09
