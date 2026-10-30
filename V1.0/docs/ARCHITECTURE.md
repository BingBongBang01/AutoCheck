# AutoCheck Architecture

## Layering

There is one entry point, `main.py`, with three modes. `python main.py` composes
the `api/` mixins into a single `Api` object and hands it to pywebview, which
renders `web_ui/index.html`. `python main.py --scheduled <schedule id>
[--data-root <dir>]` (the EXE: `AutoCheck.exe --scheduled …`) is the headless
unattended inspection that Windows Task Scheduler starts (IMPROVEMENT_PLAN 4-3):
it runs `engine/scheduled_inspection.run_scheduled_inspection` once — SSH
collection (`engine/collector.py`) → rule-based log analysis → inspection
report — and exits with a status code, without opening a window or touching
the GUI's active profile. The old grading pipeline (`engine/grading.py`,
`pipeline/`, `rule_engine/`, the `engine/scheduler.py` that ran it) was retired
by decision D1 in IMPROVEMENT_PLAN.md — it had no UI entry and its collector
output never reached the parsers. `main.py --self-test <report> --modules <list>`
imports the listed modules and writes the result without opening a window; the
release check (`tools/release_check.py`, IMPROVEMENT_PLAN 5-2) runs it inside
the built EXE to prove the bundle is complete.

```
main.py           (pywebview window + Api composition; --scheduled = headless unattended run)
      |
      v
web_ui/          (HTML/JS, pywebview front-end)
      |  window.pywebview.api.<method>(...)
      v
api/              (~20 mixins composed into one Api class in main.py)
      |
      v
engine/           (business logic, workspace lifecycle, log analysis, realtime watch,
      |            inspection judgment and report assembly; ai_analysis/ sits here too)
      |
      v
report/           (Excel/PDF renderers — may use parsers/, never engine/ or api/)
      |
      v
parsers/          (show-output parsers — import only core/)
      |
      v
core/             (path resolution, atomic storage I/O, workspace handles, naming rules)
```

Dependency direction is one-way — `api → engine / ai_analysis → report →
parsers → core`. `engine/` and `ai_analysis/` sit on one level and may use each
other; nothing imports `api/`. `tests/test_layering.py` enforces it for
module-level **and** function-level imports (a deferred import avoids a cycle
but keeps the dependency). The last inverted imports were removed in IMPROVEMENT_PLAN 3-6:
the workspace handles (`Profile`, `RunHandle`, `RUN_SUBDIRS`) moved to
`core/workspace_model.py` (`engine.profile_manager` re-exports them), the
log-file naming rules the CRT watcher needs moved to `core/log_naming.py`, and
the per-item inspection judgment moved out of the Excel renderer into
`engine/inspection_evaluator.py`.

Vendor differences (IMPROVEMENT_PLAN 4-5) sit in two places: `core/vendors.py`
reads the inventory's vendor text into a platform (`arista_eos`, `cisco_ios`,
`cisco_xe`, `cisco_nxos`) and picks the netmiko driver, and
`engine/vendor_commands.py` maps each catalog `check_id` to that platform's
commands and the report template's EOS command patterns to the Cisco commands
that carry the same content. Collection (`engine/collector.py`), the session
inspection, the inspection report and the topology all go through them.

SSH host keys (IMPROVEMENT_PLAN 4-6) are checked in one place,
`engine/host_keys.py`: `engine/ssh_client.connect()` (terminal, connection
check, realtime state polling) and `engine/collector.collect_device()` (SSH
collection, unattended runs) remember a device's first key per customer and
refuse a different one before authenticating.

Round comparison (IMPROVEMENT_PLAN 4-7) lives in `engine/round_compare.py`: it
rebuilds both sides from raw logs with the report and topology builders and
diffs the results (items, device facts, physical links). The topology tab uses
it to overlay new/lost links, and the PDF report draws the topology appendix
from `engine/topology_svg.figure()` with reportlab — `report/` still imports
nothing from `engine/`.

Realtime watch extensions (IMPROVEMENT_PLAN 4-8): the diff engine's open
conditions are mirrored into `RealtimeMonitor`, persisted in the profile's
`realtime_state.json` and re-seeded when the watch restarts (the topology tab
reads them through `RealtimeWatchService.open_conditions()`).
`RealtimeWatchService.watch_health()` combines the watcher's per-file growth
times, the poller's failure kinds and the monitor's last-seen stamps.
`engine/rule_lab.py` replays pasted lines through fresh engines and lints
`config/log_rules.json`; `engine/realtime_audit.py` turns the saved state into
the report's audit appendix (PDF page and Excel sheet).

The job center (IMPROVEMENT_PLAN 4-9) is read-only glue: `api/job_center_api.py`
collects every `JobRunner` (session inspection, scheduled "run now", the three
analyses, workspace report/export) into one list for the status bar
(`web_ui/js/job-center.js`) and forwards cancel requests to the right runner.
Analyses honour them through `log_analysis.run_analysis(should_stop=...)` and
`raw_log_analyzer.KeyRing(should_stop=...)`.

Operations conveniences (IMPROVEMENT_PLAN 4-10):
- `core/device_names.DeviceNameIndex` is the one device-name resolver. The
  topology's LLDP neighbours, the realtime watch's file matching and the
  report's inventory join all use it.
- `engine/workspace_check.py` is a read-only integrity scan of the user data
  folder. `engine/workspace_backup.py` writes the daily
  `backups/workspace_<time>.zip`; `main._on_window_ready` starts it. Both
  surface through the workspace tab's care card (`web_ui/js/workspace-care.js`).
- `engine/diagnostics.py` builds the support bundle.
- Customer report templates are a layer that
  `inspection_evaluator.apply_customer_layer()` puts over the app template.
- `tools/promote_fixture.py` turns real device output into golden fixtures
  under `tests/fixtures/golden/`. It records what the parsers read through the
  app's own entry points: `split_transcript`, `evaluate_device`,
  `topology_builder.device_facts` and `log_analysis.analyze_text`.

## The five workspace managers

| Manager | File | Responsibility |
|---|---|---|
| `ProfileManager` | `engine/profile_manager.py` | Customer/profile workspace CRUD under `data/<customer>/<profile>/`; owns `Profile`/`RunHandle` handle objects. |
| `StorageService` | `core/storage_service.py` | Atomic filesystem primitives (text/bytes/json/csv/zip) resolved against a `Profile`/`RunHandle`. Nothing above this layer should call `open()`/`Path.mkdir()` directly against `data/`. |
| `RunManager` | `engine/run_manager.py` | Run lifecycle (`create → start → finish/abort/fail`), `session.json`/`metadata.json`, startup recovery of interrupted runs. |
| `LogManager` | `engine/log_manager.py` | Raw log save (with sha256 sidecar) and per-category file listing for a `RunHandle`. |
| `ReportManager` | `engine/report_manager.py` | Export orchestration (`exports/` log/run zips, workspace-wide archive export). Report *generation* is `engine/inspection_report_builder.py` (`build_context()` + format registry → profile `reports/`). |

**Rule enforced by convention (not by code):** writes under `data/` go
through one of these five managers. The one-time migration builds the folder,
and whole-folder read-only scans (`engine/workspace_check.py`,
`engine/workspace_backup.py`, `engine/diagnostics.py`) read it directly.

## Legacy layer (still active, being phased out)

- `engine/project_manager.py` — CRUD over `labs/<project_id>/` (profile meta,
  device inventory, catalog copy) and the active-project pointer. **Not** the
  same concept as `ProfileManager` — `labs/` holds what a profile *is*,
  `ProfileManager` manages its execution *workspace* under `data/`. The
  `stages.yaml`/`target_state.yaml` files it still creates belonged to the
  retired grading pipeline and are unused.
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
  `data/<customer>/<profile>/`. See DATA_LAYOUT.md.

## Configuration / "current project" single source of truth

The one persisted runtime pointer is `engine/project_manager.py`'s
`STATE_FILE` (`{"active_project": <id>}`), read via
`get_active_project()`/`set_active_project()`. Two derivation paths sit on
top of it:

- `api/base.py:_project()` — O(1) read, returns the id.
- `api/customer_profile_api.py:_resolve_active_customer_profile_names()` —
  derives `(customer_name, profile_name)` by walking the customer/profile tree.
  The result is cached in `core/context_cache.workspace_cache` (several calls
  per workspace-tab render hit the cache) and invalidated by the profile
  create/rename/archive/activate paths.

## GUI integration

Every `call('method', ...)` site across `web_ui/js/*.js` resolves to an
existing `Api` method, and every public `Api` method is referenced by the UI.
`tests/test_api_surface.py` enforces both directions (its `RESERVED` list of
not-yet-wired endpoints is empty) and also fails on a JS function that calls the
bridge but that nothing references — the profile zip export/import hid in such a
dead modal until IMPROVEMENT_PLAN 5-3; Python-only helpers are named with a leading
`_` so pywebview does not expose them (IMPROVEMENT_PLAN 3-2 cut the exposed
surface from 165 to 135 methods; every method added since is called from the
UI). Front-end conventions are in DEVELOPER_GUIDE.md.

## Known follow-ups (identified, not yet fixed)

Open items, kept here so they aren't lost. Resolved ones are in CHANGELOG.md
and the **결과** blocks of IMPROVEMENT_PLAN.md (the old list's raw `open()`
writes in `api/customer_profile_api.py`, the 13 unwired manager methods, stale
legacy reads, the collector's CWD-relative fallback and the uncached
active-profile lookup are all done).

1. **Duplicated run-creation logic** — partially resolved: every run folder is
   created through `RunManager.create_run()` (the terminal-inspection flow goes
   through `log_storage.generate_new_run_dir()`, which delegates to it), so
   every run has `session.json`/`metadata.json` and shows up in the Workspace
   tab's Run History. `StorageService.create_run()` remains the low-level folder
   builder that `RunManager` delegates *to*; collapsing the two entry points
   into one signature is still open.
2. **Data folders are named by customer/profile name** — renaming a customer
   or profile moves folders, and a hand-renamed folder detaches its data (the
   workspace integrity check reports it). An immutable id mapping was deferred
   in IMPROVEMENT_PLAN 2-1.
3. **API return shapes differ** — most methods return `{ok, …}`/`{error}`, some
   plain values; a single `{ok, data, error}` contract and a shared confirm
   modal were deferred in 2-17 (DEVELOPER_GUIDE.md "Front-end conventions").
4. **Front-end hardening** — no Content-Security-Policy (1-3), and JS globals
   plus three remaining hand-rolled modals (3-7).
5. **Cisco is unverified on real devices** (4-5) — parsers and command maps
   follow Cisco's command reference; `config/log_rules.json` wording and the
   realtime patterns are EOS-first; the CLI prompt regexes are not unified
   (it needs a real-log corpus). `tests/fixtures/golden/` is still empty —
   promote real output with `tools/promote_fixture.py` (DEVELOPER_GUIDE.md
   rule 19).
6. **pandas** is still in `requirements.txt` although no app code imports it
   any more (4-5).
7. **Display timestamps** are formatted inline in many places with different
   `strftime` formats. Stored timestamps are normalised; the display formats
   were left alone on purpose (3-8) — change one only with a reason.
