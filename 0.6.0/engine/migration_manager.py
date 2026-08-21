"""
MigrationManager — 레거시(labs/ · history/ · config/ · config_snapshots/ · raw_logs/) 데이터를
새 워크스페이스 구조(data/<고객사>/<프로파일>/...)로 자동 이전하는 단일 관리자.

설계 원칙:
  1) 기존 사용자 파일은 절대 덮어쓰지 않는다(대상이 이미 있으면 skip).
  2) 무엇이든 옮기기 전에 반드시 migration_backup/<timestamp>/ 아래 원본을 복사해 둔다.
  3) 버전(schema/workspace/profile/migration)을 data/.workspace_version.json에 기록해서
     앱을 재시작해도 같은 마이그레이션을 반복 실행하지 않는다(멱등성).
  4) 미래 스키마 변경은 UPGRADE_FUNCS에 함수를 추가하는 것만으로 확장 가능해야 한다.
  5) 실패해도 앱 시작을 막지 않는다 — 예외는 호출부(main.py)에서 삼키고 로그만 남긴다.

레거시 -> 신규 매핑(실측 데이터 기준, core/paths.py의 labs_root/history_root/config_root 참고):
  labs/<lab_name>/device_inventory.yaml   -> data/legacy_import/<lab_name>/inventory/device_inventory.yaml
  labs/<lab_name>/commands_catalog.yaml   -> data/legacy_import/<lab_name>/commands/commands_catalog.yaml
  labs/<lab_name>/target_state.yaml       -> data/legacy_import/<lab_name>/baselines/target_state.yaml
  labs/<lab_name>/{stages,lab_meta,project_meta,ip_allocation}.yaml
                                           -> data/legacy_import/<lab_name>/archive/legacy_lab_meta/<file>
  labs/<lab_name>/terminal_sessions/*.txt -> data/legacy_import/<lab_name>/history/legacy_terminal_sessions/<file>
  history/<lab_name>/*.json               -> data/legacy_import/<lab_name>/history/legacy_grading_history/<file>
  config_snapshots/**                     -> data/legacy_import/_unclassified_logs/cache/config_snapshots/**
  raw_logs/**                             -> data/legacy_import/_unclassified_logs/cache/raw_logs/**
  config/*.yaml (앱 전역 설정)             -> <app_root>/_workspace_global/legacy_config/<file>
                                              (data/ 밖에 둔다 — data_root 아래에 두면
                                               ProfileManager.list_customers()가 가짜 고객사로 인식하기 때문)

labs/_customers/ 는 과거 "고객사별 override" 폴더라서 대상 고객사 이름 후보로만 참고하고,
실제 워크스페이스 이전 대상에서는 제외한다(현재 코드 어디에서도 참조하지 않는 죽은 경로).
"""
import datetime
import json
import shutil
import time
import traceback
from pathlib import Path

from core.paths import AppPaths, sanitize_component
from engine.profile_manager import profile_manager

SCHEMA_VERSION = 1
WORKSPACE_VERSION = 1
PROFILE_VERSION = 1
MIGRATION_VERSION = 1

LEGACY_CUSTOMER = "legacy_import"
UNCLASSIFIED_PROFILE = "_unclassified_logs"

VERSION_FILENAME = ".workspace_version.json"
BACKUP_DIRNAME = "migration_backup"
REPORT_FILENAME = "migration_report.json"

LAB_META_FILES = ("stages.yaml", "lab_meta.yaml", "project_meta.yaml", "ip_allocation.yaml")


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _version_path() -> Path:
    return AppPaths.data_root() / VERSION_FILENAME


def read_version_state() -> dict:
    path = _version_path()
    if not path.exists():
        return {
            "schema_version": 0, "workspace_version": 0,
            "profile_version": 0, "migration_version": 0,
        }
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write_version_state(state: dict) -> None:
    path = _version_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _has_legacy_data(app_root: Path) -> bool:
    labs_dir = app_root / "labs"
    if labs_dir.is_dir():
        for entry in labs_dir.iterdir():
            if entry.is_dir() and entry.name != "_customers":
                return True
    for name in ("history", "config_snapshots", "raw_logs"):
        d = app_root / name
        if d.is_dir() and any(d.iterdir()):
            return True
    config_dir = app_root / "config"
    if config_dir.is_dir() and any(config_dir.glob("*.yaml")):
        return True
    return False


def needs_migration() -> bool:
    """마이그레이션이 아직 필요한지 판단: 레거시 데이터가 있고, 아직 이번 migration_version을
    적용하지 않은 경우에만 True."""
    state = read_version_state()
    if state.get("migration_version", 0) >= MIGRATION_VERSION:
        return False
    return _has_legacy_data(AppPaths.app_root())


class MigrationReport:
    def __init__(self):
        self.started_at = _now_iso()
        self.migrated_files = []
        self.skipped_files = []
        self.failed_files = []
        self.warnings = []
        self.backup_path = None
        self._start_ts = time.monotonic()

    def add_migrated(self, source: Path, destination: Path, category: str):
        self.migrated_files.append({
            "source": str(source), "destination": str(destination), "category": category,
        })

    def add_skipped(self, source: Path, reason: str):
        self.skipped_files.append({"source": str(source), "reason": reason})

    def add_failed(self, source: Path, error: str):
        self.failed_files.append({"source": str(source), "error": error})

    def add_warning(self, message: str):
        self.warnings.append(message)

    def to_dict(self, previous_migration_version: int) -> dict:
        finished_at = _now_iso()
        return {
            "migration_version": MIGRATION_VERSION,
            "previous_migration_version": previous_migration_version,
            "schema_version": SCHEMA_VERSION,
            "workspace_version": WORKSPACE_VERSION,
            "profile_version": PROFILE_VERSION,
            "started_at": self.started_at,
            "finished_at": finished_at,
            "duration_seconds": round(time.monotonic() - self._start_ts, 3),
            "backup_path": str(self.backup_path) if self.backup_path else None,
            "migrated_files": self.migrated_files,
            "skipped_files": self.skipped_files,
            "failed_files": self.failed_files,
            "warnings": self.warnings,
            "success": len(self.failed_files) == 0,
        }


def _backup_legacy_trees(app_root: Path, report: MigrationReport) -> Path:
    """수정 전에 레거시 트리 전체를 migration_backup/<timestamp>/ 아래로 복사(원본은 그대로 둠)."""
    timestamp = report.started_at.replace(":", "").replace("-", "")
    backup_root = app_root / BACKUP_DIRNAME / timestamp
    backup_root.mkdir(parents=True, exist_ok=True)
    for name in ("labs", "history", "config", "config_snapshots", "raw_logs"):
        src = app_root / name
        if not src.exists():
            continue
        dst = backup_root / name
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True)
        except Exception as exc:
            report.add_warning(f"백업 실패({name}): {exc}")
    report.backup_path = backup_root
    return backup_root


def _copy_if_missing(src: Path, dst: Path, category: str, report: MigrationReport) -> None:
    if not src.exists():
        return
    if dst.exists():
        report.add_skipped(src, f"대상이 이미 존재해서 건너뜀: {dst}")
        return
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        report.add_migrated(src, dst, category)
    except Exception as exc:
        report.add_failed(src, f"{exc}\n{traceback.format_exc(limit=2)}")


def _migrate_lab(lab_dir: Path, report: MigrationReport) -> None:
    profile_name = sanitize_component(lab_dir.name)
    try:
        profile_manager.repair_profile(LEGACY_CUSTOMER, profile_name)
    except Exception as exc:
        report.add_failed(lab_dir, f"프로파일 생성 실패({LEGACY_CUSTOMER}/{profile_name}): {exc}")
        return
    pdir = profile_manager.profile_dir(LEGACY_CUSTOMER, profile_name)

    _copy_if_missing(lab_dir / "device_inventory.yaml", pdir / "inventory" / "device_inventory.yaml",
                      "inventory", report)
    _copy_if_missing(lab_dir / "commands_catalog.yaml", pdir / "commands" / "commands_catalog.yaml",
                      "commands", report)
    _copy_if_missing(lab_dir / "target_state.yaml", pdir / "baselines" / "target_state.yaml",
                      "baselines", report)

    for filename in LAB_META_FILES:
        _copy_if_missing(lab_dir / filename, pdir / "archive" / "legacy_lab_meta" / filename,
                          "settings", report)

    sessions_dir = lab_dir / "terminal_sessions"
    if sessions_dir.is_dir():
        for f in sessions_dir.glob("*.txt"):
            _copy_if_missing(f, pdir / "history" / "legacy_terminal_sessions" / f.name, "logs", report)


def _migrate_history(app_root: Path, report: MigrationReport) -> None:
    history_root = app_root / "history"
    if not history_root.is_dir():
        return
    for lab_dir in history_root.iterdir():
        if not lab_dir.is_dir():
            continue
        profile_name = sanitize_component(lab_dir.name)
        try:
            profile_manager.repair_profile(LEGACY_CUSTOMER, profile_name)
        except Exception as exc:
            report.add_failed(lab_dir, f"프로파일 생성 실패: {exc}")
            continue
        pdir = profile_manager.profile_dir(LEGACY_CUSTOMER, profile_name)
        for f in lab_dir.glob("*.json"):
            _copy_if_missing(f, pdir / "history" / "legacy_grading_history" / f.name, "reports", report)


def _migrate_unclassified_logs(app_root: Path, report: MigrationReport) -> None:
    has_snapshots = (app_root / "config_snapshots").is_dir() and any((app_root / "config_snapshots").iterdir())
    has_raw = (app_root / "raw_logs").is_dir() and any((app_root / "raw_logs").iterdir())
    if not has_snapshots and not has_raw:
        return
    try:
        profile_manager.repair_profile(LEGACY_CUSTOMER, UNCLASSIFIED_PROFILE)
    except Exception as exc:
        report.add_failed(app_root, f"미분류 로그 프로파일 생성 실패: {exc}")
        return
    pdir = profile_manager.profile_dir(LEGACY_CUSTOMER, UNCLASSIFIED_PROFILE)
    if has_snapshots:
        _copy_if_missing(app_root / "config_snapshots", pdir / "cache" / "config_snapshots", "logs", report)
    if has_raw:
        _copy_if_missing(app_root / "raw_logs", pdir / "cache" / "raw_logs", "logs", report)
    if has_snapshots or has_raw:
        report.add_warning(
            "config_snapshots/raw_logs는 세션 폴더명이 실험실(lab)과 1:1로 매핑되지 않아 "
            f"{LEGACY_CUSTOMER}/{UNCLASSIFIED_PROFILE}/cache/ 아래에 통째로 보관했습니다. "
            "필요 시 수동으로 올바른 프로파일로 재배치해 주세요."
        )


def _migrate_global_config(app_root: Path, report: MigrationReport) -> None:
    """config/*.yaml은 특정 고객사에 속하지 않는 앱 전역 설정이라 data/ 밖에 보관한다
    (data_root 아래 두면 ProfileManager.list_customers()가 가짜 고객사로 오인함)."""
    config_dir = app_root / "config"
    if not config_dir.is_dir():
        return
    dest_root = app_root / "_workspace_global" / "legacy_config"
    for f in config_dir.glob("*.yaml"):
        _copy_if_missing(f, dest_root / f.name, "settings", report)


def run_migration() -> dict:
    """멱등적 마이그레이션 1회 실행. 항상 dict(migration_report와 동일 내용)를 반환하고
    app_root/migration_report.json으로도 저장한다. 실패한 항목이 있어도 예외를 던지지 않는다
    (failed_files에 기록하고 계속 진행)."""
    app_root = AppPaths.app_root()
    previous_state = read_version_state()
    report = MigrationReport()

    if not _has_legacy_data(app_root):
        report.add_warning("레거시 데이터가 발견되지 않아 마이그레이션을 건너뜁니다.")
        result = report.to_dict(previous_state.get("migration_version", 0))
        _write_report(app_root, result)
        return result

    try:
        _backup_legacy_trees(app_root, report)

        labs_dir = app_root / "labs"
        if labs_dir.is_dir():
            for lab_dir in labs_dir.iterdir():
                if lab_dir.is_dir() and lab_dir.name != "_customers":
                    _migrate_lab(lab_dir, report)

        _migrate_history(app_root, report)
        _migrate_unclassified_logs(app_root, report)
        _migrate_global_config(app_root, report)

        apply_upgrades(previous_state.get("migration_version", 0))

        _write_version_state({
            "schema_version": SCHEMA_VERSION,
            "workspace_version": WORKSPACE_VERSION,
            "profile_version": PROFILE_VERSION,
            "migration_version": MIGRATION_VERSION,
            "migrated_at": _now_iso(),
        })
    except Exception as exc:
        report.add_failed(app_root, f"마이그레이션 중 예외 발생: {exc}\n{traceback.format_exc(limit=3)}")

    result = report.to_dict(previous_state.get("migration_version", 0))
    _write_report(app_root, result)
    return result


def _write_report(app_root: Path, result: dict) -> None:
    report_path = app_root / REPORT_FILENAME
    try:
        with report_path.open("w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def rollback_migration(report: dict = None) -> bool:
    """가장 최근 마이그레이션에서 새로 생성된 파일/폴더만 제거하고(migrated_files 기준),
    버전 파일을 이전 값으로 되돌린다. 레거시 원본은 애초에 복사만 했으므로 손대지 않는다."""
    app_root = AppPaths.app_root()
    if report is None:
        report_path = app_root / REPORT_FILENAME
        if not report_path.exists():
            return False
        with report_path.open(encoding="utf-8") as f:
            report = json.load(f)

    ok = True
    for entry in report.get("migrated_files", []):
        dst = Path(entry["destination"])
        try:
            if dst.is_dir():
                shutil.rmtree(dst, ignore_errors=True)
            elif dst.exists():
                dst.unlink()
        except Exception:
            ok = False

    prev_version = report.get("previous_migration_version", 0)
    if prev_version <= 0:
        version_path = _version_path()
        if version_path.exists():
            version_path.unlink()
    else:
        _write_version_state({
            "schema_version": SCHEMA_VERSION, "workspace_version": WORKSPACE_VERSION,
            "profile_version": PROFILE_VERSION, "migration_version": prev_version,
            "migrated_at": _now_iso(),
        })
    return ok


# ---- 미래 스키마 업그레이드 훅 -----------------------------------------------
# 앞으로 워크스페이스 구조가 바뀌면 여기에 upgrade_v{N}_to_v{N+1}(app_root) 함수를 추가하고
# UPGRADE_FUNCS에 등록하기만 하면 된다. apply_upgrades()가 저장된 migration_version부터
# MIGRATION_VERSION까지 순서대로 실행한다.

UPGRADE_FUNCS = {
    # 1: upgrade_v1_to_v2,
}


def apply_upgrades(from_version: int) -> None:
    app_root = AppPaths.app_root()
    for version in range(from_version, MIGRATION_VERSION):
        func = UPGRADE_FUNCS.get(version)
        if func is not None:
            func(app_root)


def migrate_if_needed() -> dict:
    """main.py 시작 시 호출하는 진입점. 필요 없으면 아무 일도 하지 않고 None 대신
    빈 상태 dict를 반환한다."""
    if not needs_migration():
        return {"skipped": True, "reason": "마이그레이션 불필요"}
    return run_migration()
