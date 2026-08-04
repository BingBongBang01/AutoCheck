# Workspace Structure

## On-disk layout

User Data Root: `Documents/AutoCheck/` (or `autocheck_data/` in portable mode)

```
<user_data_root>/
  data/
    .workspace_version.json          # schema/workspace/profile/migration version marker
    <customer>/
      _archived_profiles/            # profiles moved here by archive_profile()
      <profile>/
        profile/
          profile.json               # id, name, description, inspection_date, mode, status, timestamps
          customer.json               # {"name": <customer>}
          settings.json               # per-profile settings
          variables.json              # per-profile variables
          credential.json              # optional, per-profile default credentials
        inventory/                    # device inventory for this profile
        commands/                     # command catalog for this profile
        baselines/                    # target-state comparison snapshots
        runs/
          <run_id>/                   # run_id = YYYY-MM-DD_HHMMSS
            session.json               # RunSession
            metadata.json               # RunMetadata (progress counts, status)
            raw/       <device>.txt + .sha256      # 점검 원본 로그(= 옛 00_orignal_log)
            masked/    <device>.txt + .sha256      # 마스킹 결과(= 옛 02_masking_log)
            problem/   <name>_problems.txt         # 이상 징후 블록(= 옛 01_problem_log)
            parsed/    <device>.json
            analysis/  device_analysis.json, summary.json, comparison.json, health_score.json
            reports/   <name>.<ext>
            exports/   <file>, logs_<run_id>.zip, run_<run_id>.zip
        history/                     # cross-run accumulated records (e.g. running-config history)
        cache/                       # recomputable intermediates (pre/post-mask original logs)
        archive/                     # retired artifacts, not part of the active workspace
  migration_backup/<timestamp>/       # pre-migration copy of legacy trees (see MIGRATION_GUIDE.md)
  migration_report.json                # output of the most recent migration run
  _workspace_global/legacy_config/     # legacy app-wide config/*.yaml, kept outside data/
```

## Why `_workspace_global/` sits outside `data/`

`ProfileManager.list_customers()` simply lists every directory under
`data/`. Anything placed directly in `data/` is treated as a customer. Legacy
app-wide settings (`config/customers.yaml`, `scheduler.yaml`, `ui.yaml`,
`active_project.yaml`) are not customer data, so migration copies them to a
sibling folder instead of polluting the customer list.

## One inspection = one run

Every inspection (session-terminal inspection, SSH collection, CRT-log ingest)
creates its own `runs/<run_id>/` through `RunManager.create_run()` — nothing
appends to a shared per-profile log folder any more. Consequences to keep in
mind when adding code:

- **Never construct `runs/...` paths by hand.** Ask
  `engine/log_storage.py`: `generate_new_run_dir()` to start a run,
  `get_latest_run_dir()` / `list_run_dirs()` to read one,
  `iter_log_dirs(customer, profile, kind)` to enumerate every folder a log
  kind can live in (all runs, newest first, plus pre-run legacy folders).
- **Read paths must not create folders.** `existing_profile_dir()` returns
  `None` instead of repairing, and `get_profile_log_paths()` returns `None`
  when no run exists. An empty folder is indistinguishable from real data to
  the UI, which is how "logs listed for a profile that was never inspected"
  used to happen.
- **A run with no output is deleted.** If an inspection produces zero
  `raw/*.txt` files, the run folder is removed so the dashboard/Workspace tab
  don't count a phantom session.
- Pre-run layouts (`cache/{original,problem,masking}_log/`,
  `00_orignal_log/`, `01_problem_log/`, `02_masking_log/` at the profile root)
  are still **read** for backward compatibility, never written.

## Handles

- `Profile(customer, name, path)` — returned by `ProfileManager.get_profile()`.
  `path` always points at a fully-repaired profile directory (missing
  subfolders/meta files are created on demand by `repair_profile()`).
- `RunHandle(profile, run_id, path)` — returned by `RunManager.create_run()`.
  Exposes `raw_dir` / `masked_dir` / `parsed_dir` / `analysis_dir` /
  `reports_dir` / `exports_dir` as `Path` properties; nothing should
  construct these paths by hand.

## Version markers

Stored in `data/.workspace_version.json`:

```json
{
  "schema_version": 1,
  "workspace_version": 1,
  "profile_version": 1,
  "migration_version": 1,
  "migrated_at": "2026-07-24T23:25:37"
}
```

- **schema_version** — shape of the JSON documents themselves
  (`profile.json`, `session.json`, ...).
- **workspace_version** — shape of the `data/<customer>/<profile>/` folder
  tree (`PROFILE_SUBDIRS`, `RUN_SUBDIRS`).
- **profile_version** — reserved for future per-profile metadata migrations
  (e.g. adding a new required field to `profile.json`).
- **migration_version** — which one-time legacy→workspace migration has been
  applied; gates `migrate_if_needed()` so it never reruns once caught up.

Future structural changes should bump the relevant version number and add an
`upgrade_vN_to_vN+1(app_root)` function to
`engine/migration_manager.py:UPGRADE_FUNCS`, rather than special-casing old
data inline in managers.
