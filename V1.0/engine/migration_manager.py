"""
MigrationManager — 레거시(labs/ · history/ · config/ · config_snapshots/ · raw_logs/) 데이터를
새 워크스페이스 구조(data/<고객사>/<프로파일>/...)로 자동 이전하는 단일 관리자.

설계 원칙:
  1) 기존 사용자 파일은 절대 덮어쓰지 않는다(대상이 이미 있으면 skip).
  2) 무엇이든 옮기기 전에 반드시 <사용자 데이터 폴더>/migration_backup/<timestamp>/ 아래 원본을 복사해 둔다.
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
  config/*.yaml (앱 전역 설정)             -> <user_data_root>/_workspace_global/legacy_config/<file>
                                              (data/ 밖에 둔다 — data_root 아래에 두면
                                               ProfileManager.list_customers()가 가짜 고객사로 인식하기 때문)

레거시 원본은 app_root(프로그램 폴더)에서 읽지만, 백업·보고서·전역 설정 보관본 같은 산출물은
전부 사용자 데이터 폴더(AppPaths.user_data_root())에 쓴다. 예전에는 산출물도 app_root 에 써서
개발 트리(python main.py)에서 실행할 때마다 소스 트리에 migration_backup/ · _workspace_global/ ·
migration_report.json 이 쌓였다.

labs/_customers/ 는 과거 "고객사별 override" 폴더라서 대상 고객사 이름 후보로만 참고하고,
실제 워크스페이스 이전 대상에서는 제외한다(현재 코드 어디에서도 참조하지 않는 죽은 경로).
"""
import filecmp
import json
import shutil
import time
import traceback
from pathlib import Path

from core.atomic_io import write_text_atomic
from core.paths import AppPaths, sanitize_component
from core.utils import format_iso
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


def _version_path() -> Path:
    return AppPaths.data_root() / VERSION_FILENAME


def _output_root() -> Path:
    """백업·보고서·전역 설정 보관본을 쓰는 곳. 레거시 원본을 읽는 app_root 와 다르다(모듈 docstring)."""
    return AppPaths.user_data_root()


def _report_path() -> Path:
    return _output_root() / REPORT_FILENAME


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
    # 원자적으로 쓴다 — 쓰다 죽어 잘린 버전 파일은 다음 시작에서 JSON 오류가 되거나, 0 으로 읽혀
    # 마이그레이션·업그레이드를 처음부터 다시 돌게 한다.
    write_text_atomic(str(_version_path()), json.dumps(state, ensure_ascii=False, indent=2))


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
    # config/*.yaml 만으로는 레거시라고 보지 않는다. 개발 트리에서는 app_root/config 가 번들
    # 템플릿 폴더라 항상 있으므로, 이것을 근거로 삼으면 새 사용자 데이터 폴더마다 마이그레이션이
    # 돌았다. 진짜 레거시 설치라면 labs/·history/ 등이 함께 있고, 그때 config 도 같이 보관된다
    # (_migrate_global_config).
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
        self.started_at = format_iso()
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
        finished_at = format_iso()
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
    """수정 전에 레거시 트리 전체를 <사용자 데이터 폴더>/migration_backup/<timestamp>/ 아래로
    복사(원본은 그대로 둠)."""
    timestamp = report.started_at.replace(":", "").replace("-", "")
    backup_root = _output_root() / BACKUP_DIRNAME / timestamp
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
    dest_root = _output_root() / "_workspace_global" / "legacy_config"
    for f in config_dir.glob("*.yaml"):
        _copy_if_missing(f, dest_root / f.name, "settings", report)


def run_migration() -> dict:
    """멱등적 마이그레이션 1회 실행. 항상 dict(migration_report와 동일 내용)를 반환하고
    사용자 데이터 폴더의 migration_report.json으로도 저장한다. 실패한 항목이 있어도 예외를
    던지지 않는다(failed_files에 기록하고 계속 진행)."""
    app_root = AppPaths.app_root()
    previous_state = read_version_state()
    report = MigrationReport()

    if not _has_legacy_data(app_root):
        report.add_warning("레거시 데이터가 발견되지 않아 마이그레이션을 건너뜁니다.")
        result = report.to_dict(previous_state.get("migration_version", 0))
        _write_report(result)
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

        # 다른 키(upgrade_version 등)는 그대로 둔다 — 통째로 새로 쓰면 업그레이드가 처음부터 다시 돈다.
        state = read_version_state()
        state.update({
            "schema_version": SCHEMA_VERSION,
            "workspace_version": WORKSPACE_VERSION,
            "profile_version": PROFILE_VERSION,
            "migration_version": MIGRATION_VERSION,
            "migrated_at": format_iso(),
        })
        _write_version_state(state)
    except Exception as exc:
        report.add_failed(app_root, f"마이그레이션 중 예외 발생: {exc}\n{traceback.format_exc(limit=3)}")

    result = report.to_dict(previous_state.get("migration_version", 0))
    _write_report(result)
    return result


def _write_report(result: dict) -> None:
    report_path = _report_path()
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def rollback_migration(report: dict = None) -> bool:
    """가장 최근 마이그레이션에서 새로 생성된 파일/폴더만 제거하고(migrated_files 기준),
    버전 파일을 이전 값으로 되돌린다. 레거시 원본은 애초에 복사만 했으므로 손대지 않는다."""
    if report is None:
        report_path = _report_path()
        if not report_path.exists():
            # 이전 버전은 보고서를 프로그램 폴더(app_root)에 썼다.
            report_path = AppPaths.app_root() / REPORT_FILENAME
        if not report_path.exists():
            return False
        with report_path.open(encoding="utf-8") as f:
            report = json.load(f)

    ok = True
    for entry in report.get("migrated_files", []):
        try:
            _remove_copied(Path(entry["source"]), Path(entry["destination"]))
        except Exception:
            ok = False

    prev_version = report.get("previous_migration_version", 0)
    state = read_version_state()
    if prev_version <= 0:
        state.pop("migration_version", None)
        state.pop("migrated_at", None)
    else:
        state.update({"migration_version": prev_version, "migrated_at": format_iso()})
    if any(key in state for key in ("migration_version", "upgrade_version")):
        _write_version_state(state)
    elif _version_path().exists():
        _version_path().unlink()
    return ok


def _remove_copied(src: Path, dst: Path) -> None:
    """마이그레이션이 복사해 둔 것만 지운다 — 원본과 내용이 같은(복사한 뒤 손대지 않은) 파일만,
    그리고 그래서 비게 된 폴더만. 예전엔 대상 폴더를 통째로 rmtree 해서, 마이그레이션 뒤에 사용자가
    그 폴더에 쌓은 기록까지 함께 지웠다. 원본(레거시 트리)은 복사만 했으므로 그대로 있다."""
    if dst.is_dir():
        for path in sorted(dst.rglob("*"), reverse=True):     # 깊은 것부터 — 파일을 지운 뒤 폴더를 본다
            if path.is_file():
                original = src / path.relative_to(dst)
                if original.is_file() and filecmp.cmp(original, path, shallow=False):
                    path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        if not any(dst.iterdir()):
            dst.rmdir()
    elif dst.is_file() and src.is_file() and filecmp.cmp(src, dst, shallow=False):
        dst.unlink()


# ---- 앱 버전 업그레이드 단계 -------------------------------------------------
# 사용자 데이터·설정의 형태가 바뀌면 여기에 upgrade_N(app_root) 함수를 추가하고 다음 번호로
# UPGRADE_FUNCS 에 등록한다. apply_pending_upgrades() 가 앱을 시작할 때마다(레거시 데이터가 없어도)
# 버전 파일의 upgrade_version 다음 번호부터 순서대로 돌리고, 한 단계가 끝날 때마다 번호를 기록한다.
# 예전엔 레거시 마이그레이션 안에서만 불려서, 레거시 데이터가 없는 사용자에게는 영영 돌지 않았다.
#
# 함수는 멱등이어야 한다(같은 단계가 두 번 돌아도 결과가 같게) — 기록 직전에 앱이 죽으면 다시 돈다.
# 고객사 공용 카탈로그 위치 이전(2-2)처럼 접근할 때 처리할 수 있는 것은 거기서 한다(project_paths).

def upgrade_1_seal_secrets(app_root) -> dict:
    """4-1 자격증명 금고: 사용자 데이터 폴더에 남은 평문 비밀값(장비 비밀번호·개인키·passphrase·기본 비밀번호,
    레거시 계정 파일, 예전 credential.json, AI API 키)을 봉인한다. 멱등이다 — 이미 봉인된 값은 그대로 둔다.
    앱 폴더(app_root)의 레거시 원본은 건드리지 않는다(예전 설치의 파일이다 — docs/DATA_LAYOUT.md 참고)."""
    from engine.secret_files import seal_secret_files
    result = seal_secret_files(AppPaths.user_data_root())
    if result["values"]:
        print(f"[금고] 평문 비밀값 {result['values']}개를 봉인했습니다(파일 {result['files']}개).")
    return result


UPGRADE_FUNCS = {
    1: upgrade_1_seal_secrets,
}


def apply_pending_upgrades() -> dict:
    """아직 적용하지 않은 업그레이드 단계를 순서대로 돌린다. 한 단계가 실패하면 거기서 멈추고
    (번호를 올리지 않으므로 다음 시작에서 다시 시도한다) 오류를 돌려준다 — 앱 시작은 막지 않는다."""
    state = read_version_state()
    done = int(state.get("upgrade_version", 0) or 0)
    applied, error = [], None
    for version in sorted(v for v in UPGRADE_FUNCS if v > done):
        try:
            UPGRADE_FUNCS[version](AppPaths.app_root())
        except Exception as exc:
            error = f"업그레이드 {version} 실패: {exc}"
            break
        done = version
        applied.append(version)
        state["upgrade_version"] = done
        _write_version_state(state)
    return {"applied": applied, "upgrade_version": done, "error": error}


def pending_work():
    """앱 시작 때 할 일(레거시 데이터 이전·업그레이드)이 남았으면 그 이름, 없으면 None.
    무인 정기점검은 이 일을 직접 하지 않는다 — 앱과 동시에 돌면 같은 파일을 함께 고칠 수 있다(IMPROVEMENT_PLAN 4-3).
    앱을 한 번 열면 끝난다."""
    if needs_migration():
        return "예전 버전 데이터 이전"
    done = int(read_version_state().get("upgrade_version", 0) or 0)
    if any(version > done for version in UPGRADE_FUNCS):
        return "설정·데이터 업그레이드"
    return None


def migrate_if_needed() -> dict:
    """main.py 시작 시 호출하는 진입점. 레거시 마이그레이션은 필요할 때만, 업그레이드 단계는 항상 확인한다.
    마이그레이션이 필요 없으면 {"skipped": True, …}에 업그레이드 결과("upgrades")를 붙여 돌려준다."""
    result = run_migration() if needs_migration() else {"skipped": True, "reason": "마이그레이션 불필요"}
    result["upgrades"] = apply_pending_upgrades()
    return result
