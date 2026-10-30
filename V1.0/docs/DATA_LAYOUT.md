# Data Layout

Where AutoCheck keeps its data, how the folders fit together, and how old
layouts are brought forward. (This replaces WORKSPACE_STRUCTURE.md and
MIGRATION_GUIDE.md.)

## The user data root

All user data lives under one folder, chosen in this order
(`core/paths.AppPaths.user_data_root()`):

1. the folder named by the `AUTOCHECK_HOME` environment variable (the test
   suite and the unattended run's `--data-root` use it);
2. portable mode — an `autocheck_data/` folder (or a `portable.txt` file) next
   to the program;
3. `Documents/AutoCheck/`.

The program folder (`app_root`) is never written. Bundled defaults in its
`config/` are copied into the user data root on first run, and the legacy
migration reads old data from it (see "Legacy migration").

## On-disk layout

```
<user_data_root>/
  config/                              # app-wide user state and settings
    customers.yaml                     #   customer list — the root of the customer/profile tree
    active_project.yaml                #   the one "current profile" pointer (engine/project_manager.STATE_FILE)
    ui.yaml, realtime_watch.yaml       #   window, filter and realtime-watch screen settings
    topology_layout.yaml               #   topology node positions the user dragged, per profile
    alarm.yaml                         #   notification channels (Teams/Slack/webhook/SMTP) + mask level, dedupe,
                                       #     hourly cap, events; webhook URLs and SMTP passwords sealed (4-4)
    inspection_report_template.yaml    #   the user's copy of the report template, layered over the bundled one
  ai_config.yaml                       # AI providers, models, endpoints; API keys sealed (4-1)
  ai_settings.yaml                     # AI provider order (order: — the first is the main analyser)
  connection.yaml                      # SSH timeouts and retries
  data/
    .workspace_version.json            # schema/workspace/profile/migration/upgrade version marker
    <customer>/
      report_template.yaml             # this customer's report template layer (4-10)
      _archived_profiles/              # deleted/archived profiles (engine/profile_archive.py)
        <profile>_<YYYYMMDD_HHMMSS>/   #   the profile's data folder as it was, plus
          _labs_project/<id>/          #   its labs/<id> project folder, and
          archive.json                 #   {project_id, customer_name, customer_id, profile_name, archived_at}
      <profile>/
        profile/
          profile.json                 # id, name, description, inspection_date, mode, status, timestamps
          customer.json                # {"name": <customer>}
          settings.json                # per-profile settings
          variables.json               # per-profile variables
          credential.json              # legacy copy of default credentials — no longer created or read;
                                       #   existing ones are sealed (IMPROVEMENT_PLAN 4-1)
        inventory/                     # device inventory copies (Excel/CSV exports — they hold passwords)
        commands/                      # command catalog .txt exports/imports ('폴더 열기' on the catalog page)
        baselines/                     # target-state comparison snapshots
        reports/                       # inspection reports (.xlsx/.pdf) + _snapshot.json (next round's
                                       #   'previous month') + _support_history.json — the one report
                                       #   location for the Reports and Workspace tabs (IMPROVEMENT_PLAN 3-3)
        realtime_state.json (+ .bak)   # this round's realtime watch state: alerts and how they were handled,
                                       #   open conditions, last-seen times (4-8); the report's audit appendix
                                       #   reads it
        runs/
          <run_id>/                    # run_id = YYYY-MM-DD_HHMMSS
            session.json               # RunSession
            metadata.json              # RunMetadata (progress counts, status)
            raw/       <device>.txt + .sha256      # raw inspection logs (old 00_orignal_log)
            masked/    <device>.txt + .sha256      # masking results (old 02_masking_log)
            problem/   <name>_problems.txt         # anomaly blocks (old 01_problem_log)
            parsed/    <device>.json
            analysis/  health_score.json etc. — written by the retired grading pipeline only
            reports/   <name>.<ext>  — reports older versions made per run (still listed, no longer written)
            exports/   <file>, logs_<run_id>.zip, run_<run_id>.zip
        history/                       # cross-run accumulated records (e.g. running-config history)
        cache/                         # recomputable intermediates (pre/post-mask original logs)
        archive/                       # retired artifacts, not part of the active workspace
  labs/                                # profile definitions — the name is left over from the retired grading
                                       #   pipeline; the folder is current (engine/project_manager.LABS_DIR)
    <project_id>/
      project_meta.yaml                #   customer/profile names, inspection date, status
      device_inventory.yaml            #   devices; the vendor picks the SSH driver and commands; passwords,
                                       #     keys and enable secrets sealed (4-1, 4-5); aliases (4-10)
      knowledge/*.md                   #   the 지식베이스 tab's documents
                                       #   (stages.yaml / target_state.yaml are leftovers of the grading pipeline)
    _customers/<customer_id>/          # customer-shared commands_catalog.yaml (keyed by id, not name); items carry
                                       #   check_id — what the command checks; picks the Cisco command (4-5)
    _archived/<project_id>_<stamp>/    # archived projects that had no customer
  CRTlog/                              # SecureCRT session logs the realtime watch follows (not copied into runs)
  backups/workspace_<time>.zip         # daily workspace backup, newest 10 (4-10 — see "Backups")
  logs/                                # app logs (older than 30 days pruned, the newest 20 per kind kept)
  ai_transmissions.jsonl               # external AI send approvals and outcomes, one JSON object per line —
                                       #   no payload or keys, only the payload's SHA-256 (4-2); never pruned
  schedules.yaml                       # unattended inspection schedules, one per profile (keyed by project id)
                                       #   + alerts_seen_at for the failure badge (4-3)
  scheduled_runs.jsonl                 # unattended run history (success/partial/failed/skipped), last 500–1000 kept
  ssh_host_keys.json                   # SSH host keys per customer scope ("customer:<id>"): trusted key + first seen
                                       #   per "host" / "[host]:port", and changed keys waiting for "다시 신뢰" (4-6);
                                       #   public keys, not sealed; written under locks/ssh_host_keys.lock
  locks/profile_<hash>.lock            # per-profile OS file lock held while an unattended run writes that profile —
                                       #   kept outside the profile folder (a locked byte cannot be zipped); OS locks
                                       #   are released when a process dies, so leftover lock files are harmless
  migration_backup/<timestamp>/        # pre-migration copy of the legacy trees (see "Legacy migration")
  migration_report.json                # output of the most recent migration run
  _workspace_global/legacy_config/     # legacy app-wide config/*.yaml, kept outside data/
```

## Why `_workspace_global/` sits outside `data/`

`ProfileManager.list_customers()` simply lists every directory under
`data/`. Anything placed directly in `data/` is treated as a customer. Legacy
app-wide settings (`config/customers.yaml`, `ui.yaml`,
`active_project.yaml`) are not customer data, so migration copies them to a
sibling folder instead of polluting the customer list. Listing goes by folder,
so files at the customer and profile level (`report_template.yaml`,
`realtime_state.json`) are never mistaken for profiles, and a folder whose name
starts with `_` inside a customer folder (`_archived_profiles/`) is never a
profile.

## One inspection = one run

Every inspection (session-terminal inspection, SSH collection, unattended run)
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
- **SecureCRT session logs are not copied into runs.** Realtime watch tails
  them where SecureCRT writes them (`CRTlog/`); the old copy-into-a-run ingest
  path was never reachable from the UI and was removed (IMPROVEMENT_PLAN 3-2).
- **Interrupted runs are closed at startup.** A run still `RUNNING`/`PAUSED`
  when the app starts was cut short by the previous process;
  `RunManager.recover_all_incomplete_runs()` marks it `ABORTED` with a
  `failure_reason` instead of leaving it stuck as the "current run" — unless
  the profile lock shows another process is still running it.
- **A run with no output is deleted.** If an inspection produces zero
  `raw/*.txt` files, the run folder is removed so the dashboard/Workspace tab
  don't count a phantom session.
- **A run whose `session.json` cannot be read** is listed as `CORRUPTED` (its
  raw logs stay) instead of breaking the Run History.
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

## Which config file is read from where

`core/app_config.py` is the single source:

- **App-owned rules** (`log_rules.json`, `commands_catalog_default.xlsx`) are
  read from the bundle only; a copy in the user folder is ignored.
- **User-editable templates** (`inspection_report_template.yaml`) are the
  bundled defaults with the user's copy layered on top — the user's values
  win, missing keys come from the bundle, and `check_items` gain new fields by
  `name` (so a copy made by an older version still picks up e.g. `virtual_na`).
  A customer's `data/<customer>/report_template.yaml` is layered on top of that
  for that customer's reports (`inspection_evaluator.apply_customer_layer`).
- Everything else in `config_root()` is user state (active project, customer
  list, window/filter settings).

## Backups

There are two kinds, in two places — don't confuse them:

- **`backups/workspace_<time>.zip` — the daily workspace backup**
  (`engine/workspace_backup.py`, IMPROVEMENT_PLAN 4-10). Made once a day after
  the window opens (and on '지금 백업'); the newest 10 are kept. It holds the
  small things that are hard to recreate: the root settings files, `config/`,
  every profile definition under `labs/` (not `_archived/`), customer report
  templates, each profile's `profile/*.json`, report history (`_snapshot.json`,
  `_support_history.json`) and `realtime_state.json`. It leaves out raw logs,
  report files, app logs, the legacy `credential.json` and anything over 5 MB.
  Files are copied as they are, so sealed secrets stay sealed (they open only on
  this PC for this Windows user). `manifest.json` in the zip lists every file
  at its place relative to the user data root; restoring is done by hand — take
  the file out of the zip and put it back at that place.
- **`migration_backup/<timestamp>/` — the pre-migration copy** of the legacy
  trees from the program folder, made once before the legacy migration writes
  anything (see below).

The workspace integrity check (`engine/workspace_check.py`, the workspace
tab's '무결성 점검') only reads: it reports folders that drifted from the
customer/profile lists, unreadable settings or state files (pointing at the
`.bak` next to them or at the backups), corrupted runs, sealed passwords this
PC cannot open, and leftover temp or `.corrupt-*` files.

## Version markers

Stored in `data/.workspace_version.json`:

```json
{
  "schema_version": 1,
  "workspace_version": 1,
  "profile_version": 1,
  "migration_version": 1,
  "upgrade_version": 1,
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
- **upgrade_version** — the last app-version upgrade step applied. Checked on
  every start, independently of the legacy migration (which only runs when
  legacy data exists); recorded after each step, so a failed step is retried
  on the next start.

The file is written atomically and rewrites keep keys they do not own.

## Upgrades

Structural changes add an idempotent `upgrade_N(app_root)` function under the
next number in `engine/migration_manager.py:UPGRADE_FUNCS` (keyed by the
version it upgrades *from*) — never a one-off script and never special-casing
old data inline in managers. `apply_upgrades()` runs every registered function
between the stored version and the current one, in order, on the next start.
Purely additive folder changes need no upgrade: `ProfileManager.repair_profile()`
creates any subdir listed in `PROFILE_SUBDIRS` for old profiles too
(DEVELOPER_GUIDE.md "Adding a new workspace subfolder or file"). Changes that
can be handled on access (like the customer catalog move to
`labs/_customers/<customer_id>/`) are done where the data is read.

### Upgrade 1 — secrets are sealed (IMPROVEMENT_PLAN 4-1)

On the first start of that version, `upgrade_1_seal_secrets` seals every
plaintext secret in the user data folder with Windows DPAPI (current user):
device passwords, pasted private keys and passphrases, default passwords
(`labs/**/device_inventory.yaml`), legacy `ip_allocation.yaml` credentials,
old `profile/credential.json` copies, and cloud AI API keys (`ai_config.yaml`).
Values become `vault:dpapi:v1:<base64>` in place; file structure is unchanged.
It is idempotent and recorded as `upgrade_version: 1`.

- Files you exported yourself (Excel/CSV in `inventory/`) and legacy originals
  left in an old install folder (`app_root`) are **not** touched — delete or
  move them yourself if they hold passwords.
- A sealed value only opens for the same Windows user on the same PC. Copying
  the data folder to another PC leaves those values unreadable: the app shows
  "이 PC에서 풀 수 없음 — 다시 입력" and keeps the old sealed value until you
  type a new one (the integrity check counts them per inventory). To move a
  profile, use '내보내기' on its card in the customer/profile window and answer
  yes to including passwords (a plaintext zip). '가져오기' on the new PC seals
  them again, links the profile to its customer by name and renames it
  '(가져옴)' if that customer already has a profile of that name (so two
  profiles never share one data folder).
- `profile/credential.json` is no longer created (nothing read it); existing
  copies are only sealed.

## Legacy migration

### What migrates automatically

On every `main.py` startup, `engine.migration_manager.migrate_if_needed()`
runs before the pywebview window is created. It is a no-op after the first
successful run (gated by `data/.workspace_version.json`'s
`migration_version`), and a no-op if no legacy data is present at all.
The bundled `config/*.yaml` templates alone — always present in a development
checkout — do not count as legacy data.

Legacy sources are read from the program folder (`app_root`); everything the
migration produces (backup, report, global-config archive) is written under
the user data root, so the program folder is never modified.

| Legacy source | Destination |
|---|---|
| `labs/<lab>/device_inventory.yaml` | `data/legacy_import/<lab>/inventory/device_inventory.yaml` |
| `labs/<lab>/commands_catalog.yaml` | `data/legacy_import/<lab>/commands/commands_catalog.yaml` |
| `labs/<lab>/target_state.yaml` | `data/legacy_import/<lab>/baselines/target_state.yaml` |
| `labs/<lab>/{stages,lab_meta,project_meta,ip_allocation}.yaml` | `data/legacy_import/<lab>/archive/legacy_lab_meta/<file>` |
| `labs/<lab>/terminal_sessions/*.txt` | `data/legacy_import/<lab>/history/legacy_terminal_sessions/<file>` |
| `history/<lab>/*.json` | `data/legacy_import/<lab>/history/legacy_grading_history/<file>` |
| `config_snapshots/**`, `raw_logs/**` | `data/legacy_import/_unclassified_logs/cache/{config_snapshots,raw_logs}/**` (bulk copy — see below) |
| `config/*.yaml` | `<user_data_root>/_workspace_global/legacy_config/<file>` |

### Safety guarantees

- **Backup before anything is touched.** `_backup_legacy_trees()` copies the
  entire `labs/`, `history/`, `config/`, `config_snapshots/`, `raw_logs/`
  trees into `<user_data_root>/migration_backup/<timestamp>/` before any
  destination write happens. The report goes to
  `<user_data_root>/migration_report.json`.
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
- **Startup never blocks on failure.** `main.py` wraps the migration call
  in `try/except`; a migration error is logged via `log_event` and the app
  still starts.

### Known limitation: `config_snapshots/` and `raw_logs/`

Session folder names under `config_snapshots/` and `raw_logs/` (e.g.
`0723_20260723091817`, `asdf`) do not map 1:1 to a lab/profile name. Rather
than guess a possibly-wrong mapping, migration copies these trees wholesale
into a shared `legacy_import/_unclassified_logs` profile and adds a warning
to `migration_report.json`. **If you have logs you need attributed to a
specific customer/profile, move them manually after migration** — do not
rely on automatic per-lab sorting for these two folders.

### Legacy log locations are no longer read

Earlier versions read `labs/<project>/terminal_sessions/` and
`raw_logs/<project>/` **live**, mixed together with the new workspace. That
made a profile with no inspection data display another lab's old logs. Every
module that reads inspection logs (the log browser, the dashboard, the
inspection report, the session inspection that writes them) now goes through
`engine/log_storage.iter_log_dirs()`, which only ever returns folders under
`data/<customer>/<profile>/`. `history/<project>/*.json` held grading sessions;
nothing reads it since the grading pipeline was retired (IMPROVEMENT_PLAN D1),
so it is safe to archive. `raw_logs/` and `config_snapshots/` are only kept as
the migration source.

### Manually forcing a re-migration (support/debugging only)

Delete `data/.workspace_version.json` and restart the app. This is safe
because migration only ever copies (never overwrites existing destination
files), so a forced re-run just re-checks everything and fills in anything
still missing — it will not duplicate or corrupt existing migrated data. The
upgrade steps run again too; they are idempotent.
