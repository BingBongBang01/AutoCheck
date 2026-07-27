# Migration Guide

## What migrates automatically

On every `gui_web.py` startup, `engine.migration_manager.migrate_if_needed()`
runs before the pywebview window is created. It is a no-op after the first
successful run (gated by `data/.workspace_version.json`'s
`migration_version`), and a no-op if no legacy data is present at all.

| Legacy source | Destination |
|---|---|
| `labs/<lab>/device_inventory.yaml` | `data/legacy_import/<lab>/inventory/device_inventory.yaml` |
| `labs/<lab>/commands_catalog.yaml` | `data/legacy_import/<lab>/commands/commands_catalog.yaml` |
| `labs/<lab>/target_state.yaml` | `data/legacy_import/<lab>/baselines/target_state.yaml` |
| `labs/<lab>/{stages,lab_meta,project_meta,ip_allocation}.yaml` | `data/legacy_import/<lab>/archive/legacy_lab_meta/<file>` |
| `labs/<lab>/terminal_sessions/*.txt` | `data/legacy_import/<lab>/history/legacy_terminal_sessions/<file>` |
| `history/<lab>/*.json` | `data/legacy_import/<lab>/history/legacy_grading_history/<file>` |
| `config_snapshots/**`, `raw_logs/**` | `data/legacy_import/_unclassified_logs/cache/{config_snapshots,raw_logs}/**` (bulk copy — see below) |
| `config/*.yaml` | `<app_root>/_workspace_global/legacy_config/<file>` |

## Safety guarantees

- **Backup before anything is touched.** `_backup_legacy_trees()` copies the
  entire `labs/`, `history/`, `config/`, `config_snapshots/`, `raw_logs/`
  trees into `migration_backup/<timestamp>/` before any destination write
  happens.
- **Copy, never move.** Every migration step reads from the legacy location
  and writes to the new one; legacy originals are left in place untouched.
- **Never overwrite.** `_copy_if_missing()` skips (and records in
  `skipped_files`) any destination that already exists — a user's own data
  in the new workspace always wins over a migrated legacy copy.
- **Idempotent.** `migration_version` in `data/.workspace_version.json`
  prevents re-running once caught up; safe to leave `migrate_if_needed()` in
  the startup path permanently.
- **Rollback.** `engine.migration_manager.rollback_migration()` deletes only
  the files/folders it created (per `migrated_files` in the report) and
  restores the previous `migration_version` — it never touches legacy
  originals, since they were never modified.
- **Startup never blocks on failure.** `gui_web.py` wraps the migration call
  in `try/except`; a migration error is logged via `log_event` and the app
  still starts.

## Known limitation: `config_snapshots/` and `raw_logs/`

Session folder names under `config_snapshots/` and `raw_logs/` (e.g.
`0723_20260723091817`, `asdf`) do not map 1:1 to a lab/profile name. Rather
than guess a possibly-wrong mapping, migration copies these trees wholesale
into a shared `legacy_import/_unclassified_logs` profile and adds a warning
to `migration_report.json`. **If you have logs you need attributed to a
specific customer/profile, move them manually after migration** — do not
rely on automatic per-lab sorting for these two folders.

## Known legacy bypasses (found during the production-readiness review)

These modules still read legacy locations (`labs/...`, `raw_logs/...`)
**live**, independent of migration. After migration runs, the legacy
originals are still present (migration only copies), so these continue to
work — but they are reading from the pre-migration location, not the
migrated copy, which means:

- `api/report_api.py` (`_latest_terminal_logs_by_device`, and report/export
  path construction)
- `api/log_file_browser_api.py`
- `api/terminal_inspection_api.py`
- `engine/collector.py`'s fallback branch (when `customer_name`/
  `profile_name` are not passed to `collect_all()`)

None of these have been rewired to the new workspace in this pass — doing so
safely requires updating each call site's callers to always pass
`customer_name`/`profile_name`, which is a larger, higher-risk change than
this review's scope. They are tracked in ARCHITECTURE.md "Known follow-ups."
**Practically: don't delete `labs/`, `raw_logs/`, or `config_snapshots/`
after migration** — these modules still depend on them being present.

## Manually forcing a re-migration (support/debugging only)

Delete `data/.workspace_version.json` and restart the app. This is safe
because migration only ever copies (never overwrites existing destination
files), so a forced re-run just re-checks everything and fills in anything
still missing — it will not duplicate or corrupt existing migrated data.

## Future schema upgrades

Do not hand-write one-off migration scripts. Add a function to
`engine/migration_manager.py:UPGRADE_FUNCS` keyed by the version it upgrades
*from*, and bump `MIGRATION_VERSION` (and `SCHEMA_VERSION`/
`WORKSPACE_VERSION`/`PROFILE_VERSION` as appropriate). `apply_upgrades()`
runs every registered function between the stored version and the current
one, in order, on the next startup.
