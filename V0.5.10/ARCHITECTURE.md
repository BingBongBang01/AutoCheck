# AutoCheck Architecture

## Layering

There is exactly one entry point: `python main.py`. It composes the `api/`
mixins into a single `Api` object and hands it to pywebview, which renders
`web_ui/index.html`. There is no CLI grading entry point and no mock
collector — the only headless path is `python -m engine.scheduler`, which
calls the same `engine/grading.py` the UI does.

```
main.py           (pywebview window + Api composition — the only entry point)
      |
      v
web_ui/          (HTML/JS, pywebview front-end)
      |  window.pywebview.api.<method>(...)
      v
api/              (~20 mixins composed into one Api class in main.py)
      |
      v
engine/           (business logic, workspace lifecycle, log/report orchestration)
      |           engine/grading.py drives pipeline/ for a grading run
      |
      v
core/             (path resolution, atomic storage I/O, plain-data schemas)
      |
      v
report/           (pluggable output-format renderers, imports only core/)
```

Dependency direction is one-way (`api → engine → core`) and verified with no
inverted imports: `core/` never imports `engine/` or `api/`, and `engine/`
never imports `api/`. `report/` depends only on `core/` (e.g.
`report/inspection_report.py` imports `core.finding`).

One contained exception: `core/storage_service.py.create_run()` and
`engine/profile_manager.py.get_run_handle()` each do a **local, deferred
import** of the other layer (`storage_service` → `engine.run_manager`,
`profile_manager` → `engine.run_manager`) to avoid a real circular import
between `core.storage_service` ⇄ `engine.run_manager` ⇄
`engine.profile_manager`. This works today but is a structural smell — see
"Known follow-ups" below.

## The five workspace managers

| Manager | File | Responsibility |
|---|---|---|
| `ProfileManager` | `engine/profile_manager.py` | Customer/profile workspace CRUD under `data/<customer>/<profile>/`; owns `Profile`/`RunHandle` handle objects. |
| `StorageService` | `core/storage_service.py` | Atomic filesystem primitives (text/bytes/json/csv/zip) resolved against a `Profile`/`RunHandle`. Nothing above this layer should call `open()`/`Path.mkdir()` directly against `data/`. |
| `RunManager` | `engine/run_manager.py` | Run lifecycle state machine (`create → start → pause/resume → finish/fail → archive`), `session.json`/`metadata.json`. |
| `LogManager` | `engine/log_manager.py` | Raw/masked/parsed/analysis log file operations for a `RunHandle`. |
| `ReportManager` | `engine/report_manager.py` | Report generation and export orchestration (`reports/`, `exports/`, workspace-wide archive export). |

**Rule enforced by convention (not yet by code):** all filesystem access
under `data/` should go through one of these five managers. This session's
review found several places that still bypass them — see MIGRATION_GUIDE.md
"Known legacy bypasses" for the concrete list.

## Legacy layer (still active, being phased out)

- `engine/project_manager.py` — CRUD over `labs/<project_id>/` grading-lab
  definitions (stages/target_state/commands). **Not** the same concept as
  `ProfileManager` — this manages lab *definitions*, `ProfileManager` manages
  execution *workspaces*. Still the backbone of the grading flow
  (`engine/grading.py` resolves the active project through it).
- `engine/customer_manager.py` — CRUD over `config/customers.yaml` (flat
  customer registry used by the customer/profile tree UI).
- `engine/log_storage.py` — the single place that decides **which folders hold
  inspection logs**. Translates the old inspection-folder names
  (`00_orignal_log/`, `03_CMD/`, ...) onto run-scoped paths
  (`runs/<run_id>/{raw,problem,masked,reports}`), lists every folder a given
  log kind may live in (`iter_log_dirs()`, including pre-run legacy folders),
  and creates a new run via `RunManager` (`generate_new_run_dir()`).
  Read-only helpers never create directories — an empty folder would make the
  UI report data that does not exist.
- `engine/migration_manager.py` — one-time migration of the legacy
  `labs/`/`history/`/`config_snapshots/`/`raw_logs/`/`config/` trees into
  `data/<customer>/<profile>/`. See MIGRATION_GUIDE.md.

## Configuration / "current project" single source of truth

The one persisted runtime pointer is `engine/project_manager.py`'s
`STATE_FILE` (`{"active_project": <id>}`), read via
`get_active_project()`/`set_active_project()`. Two derivation paths sit on
top of it:

- `api/base.py:_project()` — O(1) read, returns the id.
- `api/customer_profile_api.py:resolve_active_customer_profile_names()` —
  derives `(customer_name, profile_name)` by walking the **entire**
  customer/profile tree on every call, with no caching. This is called
  repeatedly (6+ times per workspace-tab render via
  `api/workspace_api.py:_active_customer_profile()`).

This is safe today but is the single biggest scalability risk identified in
this review — see "Known follow-ups."

## GUI integration

Every `call('method', ...)` site across `web_ui/js/*.js` was cross-checked
against `api/*.py` mixins: **100% resolve to an existing method**. There are
no dead/nonexistent endpoint calls from the JS side as of this review. The
risk sits on the API side reading stale legacy paths, not on the JS side
calling missing methods.

## Known follow-ups (identified, not yet fixed)

These were found during the production-readiness review and deliberately
**not** rushed into this pass, to avoid destabilizing a working app with a
sweeping rewrite. Tracked here so they aren't lost:

1. **Duplicated run-creation logic** — partially resolved: every run folder is
   now created through `RunManager.create_run()` (the terminal-inspection flow
   goes through `log_storage.generate_new_run_dir()`, which delegates to it),
   so every run has `session.json`/`metadata.json` and shows up in the
   Workspace tab's Run History. `StorageService.create_run()` remains the
   low-level folder builder that `RunManager` delegates *to*; collapsing the
   two entry points into one signature is still open.
2. **`api/customer_profile_api.py`** writes profile/customer meta YAML via
   raw `open()` in 10+ places instead of `ProfileManager`/`StorageService` —
   bypasses the atomic-write guarantee and the "5 managers own `data/`" rule.
3. **13 unwired manager methods** (`pause_run`, `resume_run`, `abort_run`,
   `retry_run`, `delete_run`, `archive_run`, `recover_incomplete_runs`,
   `compare_runs`, `generate_diff`, `compress_log`, `decompress_log`,
   `verify_integrity`, `load_masked_log`, `export_inventory`,
   `export_commands`, `preview_report`, `validate_report`) exist on
   `RunManager`/`LogManager`/`ReportManager` but have zero callers — either
   wire them into `api/workspace_api.py` or remove them.
4. ~~**Legacy path reads that go stale after migration**~~ — **fixed.** First
   pass moved them off CWD-relative literals onto `AppPaths`. Second pass
   (this one) cut the legacy locations out of the read path entirely:
   `api/log_file_browser_api.py`, `api/report_api.py` and `api/dashboard_api.py`
   now list/aggregate **only** the active profile's run folders (via
   `log_storage.iter_log_dirs()`), so a profile with no inspection data shows
   an empty list instead of another lab's `terminal_sessions/`/`raw_logs/`
   contents. Generated reports land in the run's `reports/` (previously
   `labs/<project>/`, where the report tab could not see them).
5. ~~**`engine/collector.py`'s legacy fallback**~~ — **fixed** alongside #4;
   it built `Path("raw_logs") / lab_name` CWD-relative, so collected logs
   landed outside the app root where the Reports and 점검 로그 tabs could
   never find them. Now resolved via `AppPaths`.
6. **`resolve_active_customer_profile_names()`** full-tree-scan cost (see
   above) — worth caching, invalidated on profile create/rename/delete.
7. Timestamp formatting is reimplemented inline in ~20 places with several
   different `strftime` formats — a shared helper would remove format drift.
