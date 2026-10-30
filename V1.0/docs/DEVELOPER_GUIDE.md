# Developer Guide

## Ground rules

1. **All filesystem access under `data/` goes through one of the five
   managers** (`ProfileManager`, `StorageService`, `RunManager`,
   `LogManager`, `ReportManager`). If you find yourself writing
   `open(...)`, `Path.mkdir(...)`, or `os.path.join("data", ...)` outside
   `engine/profile_manager.py`, `core/storage_service.py`,
   `engine/run_manager.py`, `engine/log_manager.py`, or
   `engine/report_manager.py` — stop and add a method to the appropriate
   manager instead. The one-time migration (`engine/migration_manager.py`)
   builds the folder, and whole-folder read-only scans
   (`engine/workspace_check.py`, `engine/workspace_backup.py`,
   `engine/diagnostics.py`) read it directly — they never write there.
2. **Never write a file in two steps.** Use `StorageService`'s atomic
   pattern (write to a temp file in the same directory, then
   `os.replace()`) for anything that must survive a crash mid-write.
   `ProfileManager._write_json` now follows this pattern too — copy it
   rather than reverting to `path.open("w")`.
3. **Names from users (customer/profile/project) must go through
   `core.paths.validate_name()`.** Names derived from external/device data
   (inventory device names, filenames in log output) are **not** currently
   sanitized before being used as path components — treat any new code path
   that turns device-supplied strings into filenames as a security review
   item, not a copy-paste target.
4. **Logging**: use `core.app_logger.log_event(message, source=...)` for
   anything a human might need to see after the fact (migration results,
   scheduled-job failures, cleanup errors). `print()` is only acceptable in
   short-lived CLI scripts, never in `engine/`/`api/` modules that run
   inside the GUI process — `engine/collector.py` (the unattended
   inspection's SSH collector) still uses `print()` for some failures; treat
   that as legacy, not as the pattern to follow.
5. **Type hints**: required on every public method of a manager class.
   Encouraged (not yet universal) on private helpers.
6. **Don't add a sixth manager or a new global "current X" pointer.** The
   single source of truth for "what's active" is
   `engine/project_manager.py`'s `STATE_FILE`. If you need the
   customer/profile *names* (not just the id), prefer resolving them once
   per request and passing them down, rather than calling
   `_resolve_active_customer_profile_names()` repeatedly. It is cached
   (`core/context_cache.workspace_cache`), but only the profile create/rename/
   archive/activate paths invalidate that cache — a new code path that
   changes profiles must call `workspace_cache.invalidate()` too.
7. **A public method on an `api/*.py` mixin is a UI endpoint.** pywebview
   exposes every `Api` method whose name does not start with `_` to the
   JavaScript side. Helpers that only Python calls (another mixin, `main.py`,
   a timer) must start with `_`. `tests/test_api_surface.py` fails when a
   public method is not referenced by `web_ui/` (unless it is listed in its
   `RESERVED` map with a reason), when the UI calls a method that no longer
   exists, or when the JS function that calls it is itself unreachable (nothing
   references it — a dead modal is not an entry point).
8. **Long-running work goes through `api/job_runner.JobRunner`.** Start it
   with `start(kind, worker)` and poll `status()`/`status_all()`; do not keep
   your own `running` flag and lock (the session inspection used to, and an
   exception in its worker left it "running" forever). Cancellation is
   cooperative — `request_cancel()` only sets a flag the worker must check via
   `cancel_requested()`. Pass `queue=True` when a second request should run
   after the current one instead of being refused.
9. **Secrets never travel to the UI and never sit on disk in plaintext.**
   Device passwords/keys/passphrases and AI API keys are sealed with Windows
   DPAPI (`core/secret_vault.py`). Write inventories only through
   `save_inventory`, AI settings only through `_save_ai_config` and notification
   channels only through `engine/notifications.save_settings` — they seal
   on the way out; the matching loaders reveal into memory. Anything returned to
   JS goes through `public_device()`/`public_defaults()` (`has_*` flags only),
   and anything coming back is merged by device id with
   `merge_screen_devices()`/`apply_secret_changes()` (empty = keep,
   `clear_secrets` = remove). A new file kind that holds secrets must be added
   to `engine/secret_files.SECRET_FILE_NAMES` so the startup upgrade and project
   import seal it.
10. **Data that leaves this PC for an AI goes out only as an approved preview.**
    Build what will be sent with `ai_analysis/transmission.build_plan` (it masks
    via `raw_log_analyzer.build_prompts`; pass the active inventory's names from
    `neighbor_names` so neighbours named in descriptions/LLDP are hidden too),
    show it with `preview_ai_transmission`, and send only the stored plan after
    the user approves its preview id — never rebuild the payload at send time.
    `send_prompts` accepts only `PreparedRequests` and refuses unmasked content
    for an external destination. Every approval and its outcome is appended to
    `ai_transmissions.jsonl` (no payload, no keys — only the SHA-256). Key
    rotation (`KeyRing`) stays within one destination: never fall back to a
    different provider or address than the one the user approved.
11. **Unattended runs never touch GUI state, and profiles have one runner.**
    The headless run (`main.py --scheduled`, `engine/scheduled_inspection.py`)
    receives the project id and looks up inventory/catalog/data folders from it
    — never call `set_active_project()` outside a user action. Work that writes
    a profile's runs from outside the GUI takes the profile lock
    (`core/process_lock.FileLock(profile_lock_path(profile_dir))`), and anything
    that moves or recovers a profile's folder must check `is_locked()` first
    (`profile_archive.archive_profile`, `run_manager.recover_all_incomplete_runs`
    already do). Report every outcome (success/partial/failed/skipped) to
    `scheduled_runs.jsonl` — a result that is only printed is a result nobody sees.
12. **Notifications go through `engine/notifications.notifier` and never hurt the
    caller.** Build an event and hand it to the notifier — it masks (by the
    configured level), de-duplicates, caps each channel per hour and records each
    channel's last result. Never send to a channel directly, never block a watcher
    thread on the network (realtime alerts are queued), never let a delivery
    failure raise into an inspection, and never put a webhook URL in a log or an
    error text (its path is the token) — use the host.
13. **Vendor differences live in two tables.** `core/vendors.py` turns the
    inventory's vendor text into a platform id and its netmiko driver;
    `engine/vendor_commands.py` turns a catalog item's `check_id` into that
    platform's commands (`commands_for`) and the report template's EOS command
    patterns into the commands that carry the same content on that platform
    (`report_patterns`, `not_applicable`). Never hard-code `arista_eos` or an EOS
    command where the device can be another vendor. A new default check gets a
    `check_id` and a row in `_CISCO` (a test fails when a default `check_id` has
    none, or when a report alias names a command that is never sent). Enable
    handling is shared: never send `enable` inside a bulk command block (a
    password prompt eats the next line) and never retry a failed enable. The
    Cisco parsers follow the documented output formats and none has met a real
    device yet — keep the sample outputs in `tests/test_multivendor.py` in step
    when real logs arrive.
14. **Every SSH connection checks the host key.** Open paramiko sessions only
    through `engine/ssh_client.connect()` and netmiko sessions only through
    `engine/collector.collect_device()`; both take a `host_key_scope`
    (`engine/host_keys.scope_for_project` — per customer) and remember the first
    key, refuse a different one before authenticating and never retry it. Never
    set `AutoAddPolicy` or call `SSHClient.connect()`/`ConnectHandler()`
    yourself. A key changes only through the user's "다시 신뢰" (`trust_host_key`)
    after comparing fingerprints.
15. **Comparisons re-judge logs; they never diff report snapshots.**
    `engine/round_compare.py` builds each side with the same code the report
    and the topology use (`build_context`, `topology_of`) so a comparison can
    never disagree with the screens it summarises. Measured values (CPU,
    memory) only count when their status changes, and a link is "lost" only if
    a device at one of its ends still reported neighbours — otherwise it is
    "unverified". Anything that draws the topology outside the browser (the
    PDF appendix) takes `engine/topology_svg.figure()`, never re-implements
    the layout or the link routing.
16. **Realtime state that must outlive a watch lives in the monitor.**
    `BaselineDiffEngine` is rebuilt on every watch start. Anything users rely
    on across restarts (open conditions, last-seen times) is mirrored into
    `RealtimeMonitor` after each pipeline step, saved with the profile's
    `realtime_state.json`, and re-seeded into the new engine on start. Alert
    ids carry a per-run token — never assume they restart at 1. Tools that
    explain a judgement (`engine/rule_lab.py`) call the same engine objects in
    the same order; never re-implement a layer to describe it. Reports read the
    saved state read-only (`engine/realtime_audit.py`) and mask secrets with
    `log_masking.SECRET_KEYS`.
17. **Long work runs on a JobRunner and shows up in the job center.** A new
    background task gets a JobRunner kind and an entry in
    `api/job_center_api.JOB_SOURCES` (label, and the log line to leave when it
    is cancelled — `None` if it cannot stop). Cancellation is cooperative:
    the worker checks `cancel_requested(kind)` at safe points (between files or
    parts, and while waiting — use interruptible waits, never a bare
    `time.sleep` of seconds), keeps what it finished and returns normally so
    the job ends as `cancelled`. Don't mark something cancellable if the work
    it stops would already be complete (a report file written in one go).
18. **Device names are matched in one place.** Anything that maps a name seen
    in a log (prompt, `hostname`, LLDP/CDP neighbour, SecureCRT file name) to
    an inventory device builds a `core/device_names.DeviceNameIndex` from the
    inventory and calls `resolve()` — the exact name, then aliases, then the
    domain-stripped short name, and nothing when two devices share a name.
    Don't lower-case and strip domains ad hoc; that is how the topology, the
    realtime watch and the report used to disagree about the same device.
19. **Parsers are tested against real output.** Never hand-write vendor output
    to prove a parser — promote a real log with
    `python -m tools.promote_fixture promote <log> --name <name>`. It swaps
    identifiers for format-preserving fakes, refuses to write if anything
    original is left, and records what the parsers read in `expected.json`.
    Read the pseudonymized `session.log` before committing it, and never commit
    the original. When you change a parser on purpose, run
    `python -m tools.promote_fixture refresh` and commit the `expected.json`
    diff together with the change. Short synthetic snippets are fine for edge
    cases and for testing tools, as long as they copy a real line's shape.

## Adding a new workspace subfolder or file

1. Add the subdir name to `PROFILE_SUBDIRS` (or `RUN_SUBDIRS` for
   per-run folders) in `engine/profile_manager.py`.
2. `ProfileManager.repair_profile()` already creates any subdir listed there
   for both new and pre-existing profiles — this is the self-healing
   mechanism that lets old `data/<customer>/<profile>/` folders (created
   before your change) pick up the new subfolder automatically, so you
   don't need a separate migration for purely-additive folder changes.
3. If the change alters the *shape* of an existing JSON file (not just adds
   a folder), bump `PROFILE_VERSION` or `SCHEMA_VERSION` in
   `engine/migration_manager.py` and add an `upgrade_vN_to_vN+1` function —
   see DATA_LAYOUT.md "Upgrades".

## Adding a new report format

There is one report pipeline: `engine/inspection_report_builder.build_context()`
assembles the data and `export(customer, profile, format_id)` saves it to the
profile's `reports/`. To add a format, write a `render(context, path,
customer, profile) -> dict` function (the drawing code belongs in `report/`)
and register it with `register_format(ReportFormat(...))` — do not add
format-specific branching to `export()` or the API layer. File naming,
`_v2` de-duplication and the next-round snapshot are handled for every format.

## Front-end conventions

`web_ui/` is plain scripts sharing one global scope — no modules and no
bundler (pywebview loads `index.html` from disk, so there is no round trip to
save; see Non-goals). The order of the `<script>` tags in `index.html` is the
dependency order: `core.js` (the bridge, `call`, `esc`, toasts, pollers) comes
first and `common.js` (shared widgets: `openModal`, `logViewMarkup`/
`showLogFile`) second, then the page modules, then `core-navigation.js` (the
router), the app-wide modules (profile and archive modals, analysis progress,
job center, realtime alerts and panel, topology) and `core-init.js`, which
starts the app, last. A new page module goes before `core-navigation.js`.

- **`call(name, ...args)` never throws.** It returns the API method's result,
  or `null` when the bridge is missing, the method does not exist or Python
  raised — in that last case it has already shown an error toast. Check both:
  `const r = await call('x'); if (!r) return; if (r.error) { showToast(r.error,
  'error'); return; }`. Most API methods return `{ok: true, …}` on success and
  `{error: '…'}` (a user-facing Korean sentence) for an expected failure; some
  return plain values. One `{ok, data, error}` contract for all of them is
  still open (IMPROVEMENT_PLAN 2-17).
- **Escape everything that came from outside.** Values from devices, logs,
  files, user input or the server go through `esc()` (or the page's alias:
  `rtEscape`, `tpEsc`, `dashEsc`, `escapeReportText`) before they reach
  `innerHTML`; JSON in an attribute is `esc(JSON.stringify(…))`.
  `tests/test_web_ui_escaping.py` fails on an unescaped `="${…}"` attribute
  unless the expression is on its reviewed safe list (app-made ids, numbers,
  class-name constants).
- **Every public API method is called from the UI, and every call resolves**
  (`tests/test_api_surface.py`, rule 7).
- **Pages clean up after themselves.** A page that starts an interval or adds
  a `document`/`window` listener registers its teardown with
  `onPageLeave(fn)`; `renderPage()` runs those before drawing the next page.
  Use named handlers and remove them in the same file
  (`tests/test_page_lifecycle.py`). Listeners on the page's own elements need
  nothing — they go away with the DOM.
- **Polling goes through `createAdaptivePoller({tick, isBusy})`** — fast while
  something runs, slow when idle, alive after a failed tick, `wake()` when the
  user starts work. Never a bare `setInterval` that calls the bridge. A poller
  that starts when its script loads starts with
  `whenApiReady(() => poller.start())`; otherwise its first call meets a bridge
  that is not ready yet.
- **Pushes from Python** (`window.evaluate_js`) land on `window.on…` handlers
  (e.g. `onRealtimeDiffAlert`) that accept both one object and an array.

## Testing

```
pip install -r requirements-dev.txt
python -m pytest tests -q
python -m tools.bench_log_analysis --check   # log-judgement perf regression gate (OPTIMIZATION_PLAN 0-2)
```

- **Tests never touch real user data.** `conftest.py` points `AUTOCHECK_HOME`
  at a fresh temp folder before any app module is imported. Several modules
  freeze paths at import time (`engine/project_manager.LABS_DIR`, ...), so
  merely importing them used to create `Documents\AutoCheck`.
  `tests/test_app_paths.py` fails if that ever regresses.
- **Run on Windows** — it is the target platform. Write test files as bytes
  (`path.write_bytes(text.encode("utf-8"))`): `Path.write_text` turns `\n`
  into `\r\n` on Windows while the log readers keep line endings as-is. That
  is how two `test_log_cache.py` tests passed on Linux but failed on Windows.
- **JS tests need node.** About a dozen test files (the adaptive poller, the
  realtime delta client, the escaping helpers, the job center, the inventory
  secrets screen, …) run the real `web_ui/js` code under node and are skipped
  when node is missing — a release needs 0 skipped. Install Node.js LTS
  (`winget install OpenJS.NodeJS.LTS`; verified with v24) to run them. Read
  node's output with `encoding="utf-8"`, never `text=True` — node always
  writes UTF-8 to a pipe, while `text=True` decodes with the system code page
  (cp949 on Korean Windows) and breaks as soon as the output contains Hangul.
- **Golden fixtures** (`tests/fixtures/golden/`, rule 19):
  `tests/test_golden_fixtures.py` re-parses every promoted real log, compares
  the result with its `expected.json` and re-checks that no non-fake address,
  MAC, FQDN or secret is left. `python -m tools.promote_fixture check` does the
  same from the command line.
- To run the app itself against a scratch data folder, set `AUTOCHECK_HOME`
  (PowerShell: `$env:AUTOCHECK_HOME="C:\temp\ac"; python main.py`).

For a quick manual check of a manager change, use a throwaway `data_root`:

```python
import tempfile, pathlib
from engine.profile_manager import ProfileManager
pm = ProfileManager(data_root=pathlib.Path(tempfile.mkdtemp()))
pm.create_profile("acme", "siteX")
print(pm.load_profile("acme", "siteX"))
```

`ProfileManager`, `RunManager`, and `StorageService` all accept an injectable
root for exactly this reason — use it instead of running against the real
`data/` folder while iterating.

## Dependencies

- `requirements.txt` — runtime only. `main.py` checks it at startup
  (`core/requirements_check.py`) and stops with an install hint when a package
  is missing or older than its `>=` minimum (`AUTOCHECK_AUTO_INSTALL=1`
  restores the old install-on-every-run behaviour). Never put build or test
  tools here — every user's PC would be asked for them.
- `requirements-build.txt` (PyInstaller, Pillow) and `requirements-dev.txt`
  (pytest) each include `requirements.txt`.
- `requirements.lock` — the exact versions the test suite passed with. After
  changing `requirements.txt`, regenerate it with
  `python -m tools.lock_requirements`; a test fails if a runtime requirement
  is missing from the lock.
- Minimum Python is 3.12 (`core/requirements_check.MIN_PYTHON`).

## Build & release

```
pip install -r requirements-build.txt
python build_exe.py
```

`build_exe.py` runs PyInstaller on `AutoCheck.spec` and copies `실행방법.txt`
(the EXE user guide) next to `dist/AutoCheck/AutoCheck.exe`. The spec bundles
`web_ui/`, `config/`, `VERSION` and, when present, `connection.yaml` and
`ai_settings.yaml`. Bundled settings are copied into the user data folder on
first run, so the spec checks every bundled file with
`tools/bundle_secret_check.py` first and stops the build if anything looks like
a secret (a provider key shape, a filled `password`/`api_key` field, a file
named `ai_config.yaml`). It never bundles `labs/`, `ai_config.yaml` or the
requirement files.

Before a release, run the release check (IMPROVEMENT_PLAN 5-2):

```
python -m tools.release_check              # everything — tests, bench and the EXE build take minutes
python -m tools.release_check --no-build   # skip the EXE build and its self-test
```

It prints one line per check and exits 0 only when every check passed (1 = a
check failed, 2 = nothing failed but something was skipped):

1. **Version** — `VERSION` = the CHANGELOG's top section title (`## vX.Y.Z — …`,
   not the "미배포" section) = the version in the README header.
2. **git** — the working tree is clean, and the tag `vX.Y.Z` either does not
   exist yet (create it after the check) or points at HEAD.
3. **Bundle secrets** — what the spec bundles passes `tools/bundle_secret_check`.
4. **Tests** — the full pytest run on Windows: 0 failed, 0 skipped (node
   installed).
5. **Bench** — `python -m tools.bench_log_analysis --check` reports no
   regression. The baseline is per machine and not created for you: save it
   once with `--save-baseline` on the commit you want to compare against.
6. **EXE** — `build_exe.py` builds, `실행방법.txt` sits next to the EXE, and
   `AutoCheck.exe --self-test <report> --modules <list>` imports every app
   module and every outside module the app imports (found by scanning the
   source, including imports inside functions) inside the bundle. That is what
   proves the spec's `excludes` (e.g. `unittest`) break no dependency and that
   nothing is missing from the bundle.

Every child process runs with a scratch `AUTOCHECK_HOME`, so the check never
touches the real data folder. Then bump the version: change `VERSION`, rename
the CHANGELOG's "미배포" section to `## vX.Y.Z — …`, update the README header,
commit, run the check again and `git tag vX.Y.Z`.

## Non-goals

These were measured in the finished optimization plan
(`docs/history/OPTIMIZATION_PLAN.md`); don't redo them without new numbers.

- Merging the rule signatures or keywords into one regex alternation — 2.6–2.7×
  slower: Python's `re` loses its per-pattern literal-prefix optimisation.
- A literal prefilter gate in front of the rules — no gain or slower (71% of
  lines pass it).
- Bundling the `<script>` tags — pywebview loads from `file://`, there is no
  round trip to save.
- Restructuring `engine/collector.py`'s parallelism — devices already run in a
  pool (up to 50) and each device's commands go as one bulk block; commands
  within one SSH session stay sequential on purpose.
- Sweeping rewrites. This is a working field tool: every change is an
  independent commit that can be reverted.

## Version control

The project is a git repository from v0.5.9 on; tag `v0.5.9` is the untouched
original. Don't copy the folder per version any more — commit each change on
its own and tag releases `vX.Y.Z`, keeping tag = `VERSION` = CHANGELOG title.
All text files use LF line endings; `.gitattributes` (`* -text`) turns off
Git's line-ending conversion so a checkout stays byte-identical even where
Git for Windows defaults to `core.autocrlf=true`.

## Threading notes

- `api/job_runner.py`'s `JobRunner` is the shared background-job tracker —
  reuse it for new long-running operations rather than hand-rolling a
  `threading.Thread` + status dict (this is exactly what
  `api/log_analysis_run_api.py` and `api/workspace_api.py` already do).
- Realtime watch state lives in one object, `engine/realtime_watch_service.py`'s
  `RealtimeWatchService` (the API mixin `api/realtime_watch_api.py` only
  delegates). Its judgment pipeline (watcher thread + state poller) is
  serialized by `pipeline_lock`; results from an older watch "generation" are
  dropped. Settings in `realtime_watch.yaml` are changed only through
  `RealtimeWatchConfig.update()`/`edit_filter()`, which hold a lock from read to
  write — do not read, modify and dump that file yourself.
- The active-project pointer (`engine/project_manager.py`'s `STATE_FILE`) has
  no lock. That is safe only because the unattended run never touches it
  (rule 11) and work on one profile's runs is serialized across processes by
  the profile lock (`core/process_lock`). A new background job that reads or
  changes the active project must not assume more than that.
