# Developer Guide

## Ground rules

1. **All filesystem access under `data/` goes through one of the five
   managers** (`ProfileManager`, `StorageService`, `RunManager`,
   `LogManager`, `ReportManager`). If you find yourself writing
   `open(...)`, `Path.mkdir(...)`, or `os.path.join("data", ...)` outside
   `engine/profile_manager.py`, `core/storage_service.py`,
   `engine/run_manager.py`, `engine/log_manager.py`, or
   `engine/report_manager.py` — stop and add a method to the appropriate
   manager instead. (`api/customer_profile_api.py` currently violates this;
   see ARCHITECTURE.md "Known follow-ups" — don't copy that pattern.)
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
   inside the GUI process — `engine/scheduler.py` and `engine/collector.py`
   currently use `print()` for failures and should be treated as legacy, not
   as the pattern to follow.
5. **Type hints**: required on every public method of a manager class.
   Encouraged (not yet universal) on private helpers — several
   `engine/report_manager.py` internal `_build_*` helpers are untyped;
   adding hints there would have caught argument-order mistakes in the past.
6. **Don't add a sixth manager or a new global "current X" pointer.** The
   single source of truth for "what's active" is
   `engine/project_manager.py`'s `STATE_FILE`. If you need the
   customer/profile *names* (not just the id), prefer resolving them once
   per request and passing them down, rather than calling
   `resolve_active_customer_profile_names()` repeatedly — it does a full
   tree walk with no caching.

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
   see WORKSPACE_STRUCTURE.md.

## Adding a new report format

Register a `BaseReporter` subclass with `report/base_reporter.py`'s
registry — do not add format-specific branching inside
`engine/report_manager.py`. See `report/reporters.py` for the pattern used
to wrap the Markdown/Word builder functions.

## Testing changes to a manager

There is no test suite in this repo yet (see CHANGELOG.md — building one is
tracked as a follow-up, not done in this pass). Until one exists, manually
verify any manager change against a throwaway `data_root`:

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

## Threading notes

- `api/job_runner.py`'s `JobRunner` is the shared background-job tracker —
  reuse it for new long-running operations rather than hand-rolling a
  `threading.Thread` + status dict (this is exactly what
  `api/log_analysis_run_api.py` and `api/workspace_api.py` already do).
- If you add a background job that touches `engine/project_manager.py`'s
  active-project state (or anything else read/written without a lock),
  be aware there is currently no locking around it — concurrent scheduler +
  GUI operations can race. Don't design new features that assume this is
  safe; ask before building on top of it.
