"""작업공간 자동 백업 — 설정과 프로파일 정의를 하루에 한 번 zip 으로 남긴다(IMPROVEMENT_PLAN 4-10).

담는 것은 '다시 만들기 어렵고 작은 것'이다: 앱 설정(AI·연결·일정·호스트키·config/), 프로파일 정의(labs — 장비 목록·
명령 카탈로그·프로젝트 정보), 고객사 템플릿, 프로파일마다의 보고서 기록(전월 값·지원 이력)과 실시간 감시 상태.
점검 로그·보고서 파일·앱 로그는 담지 않는다 — 크고, 그 자체가 기록이라 덮어쓰이지 않는다.
봉인된 비밀번호는 봉인된 채로 담긴다(같은 PC·사용자에서만 풀린다). 이 PC 의 데이터 폴더(backups/) 안에 남는다.

되살리기는 사람이 한다 — zip 에서 필요한 파일을 같은 자리로 꺼낸다(manifest.json 에 자리와 시각이 있다).
"""
import datetime
import json
import os
import zipfile
from pathlib import Path

from core.paths import AppPaths
from core.utils import format_iso

BACKUP_DIR = "backups"
KEEP = 10                         # 남기는 백업 수
DUE_AFTER_SEC = 24 * 3600         # 자동 백업 주기
MAX_FILE_BYTES = 5 * 1024 * 1024  # 이보다 큰 파일은 담지 않는다(상태 파일이 비정상으로 커진 경우)
_ROOT_FILES = ("ai_config.yaml", "ai_settings.yaml", "connection.yaml", "schedules.yaml", "ssh_host_keys.json")
_SKIPPED_NAMES = ("credential.json",)   # 예전 버전의 기본 계정 사본 — 지금은 만들지도 읽지도 않는다(engine/profile_manager)


def backup_root():
    path = AppPaths.user_data_root() / BACKUP_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def files_to_back_up():
    """[(zip 안 이름, 경로)] — 데이터 폴더 기준 상대 경로를 그대로 이름으로 쓴다(되살릴 자리)."""
    root = AppPaths.user_data_root()
    found = [root / name for name in _ROOT_FILES]
    config = AppPaths.config_root()
    found += [p for p in config.iterdir() if p.suffix.lower() in (".yaml", ".yml", ".json")]
    from engine import project_manager as pm
    labs = Path(pm.LABS_DIR)
    if labs.is_dir():
        for project in labs.iterdir():
            if project.is_dir() and project.name != "_archived":
                found += [p for p in project.rglob("*") if p.suffix.lower() in (".yaml", ".yml", ".json")]
    data_root = AppPaths.data_root()
    for pattern in ("*/report_template.yaml", "*/*/profile/*.json", "*/*/reports/_snapshot.json",
                    "*/*/reports/_support_history.json", "*/*/realtime_state.json"):
        found += [p for p in data_root.glob(pattern)
                  if "_archived_profiles" not in p.parts and p.name not in _SKIPPED_NAMES]
    out = []
    for path in found:
        try:
            if path.is_file() and path.stat().st_size <= MAX_FILE_BYTES:
                out.append((_arcname(path, root), path))
        except OSError:
            continue
    return sorted(set(out))


def _arcname(path, root):
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return Path(path).name


def create_backup(reason="manual", now=None):
    """백업 zip 하나를 만들고 오래된 것을 정리한다. 반환: {path, name, files, size, reason}."""
    from core.version import app_version
    now = now or datetime.datetime.now()
    files = files_to_back_up()
    stem = f"workspace_{now.strftime('%Y%m%d_%H%M%S')}"
    target, n = backup_root() / f"{stem}.zip", 2
    while target.exists():      # 같은 초에 두 번(자동 백업 바로 뒤 '지금 백업') — 덮어쓰지 않는다
        target, n = backup_root() / f"{stem}_{n}.zip", n + 1
    tmp = target.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in files:
            try:
                archive.write(path, name)
            except OSError:
                continue
        archive.writestr("manifest.json", json.dumps({
            "created_at": format_iso(now), "reason": reason, "app_version": app_version(),
            "data_root": "<사용자 데이터 폴더>", "files": [name for name, _ in files]}, ensure_ascii=False, indent=2))
    os.replace(tmp, target)
    prune()
    return {"path": str(target), "name": target.name, "files": len(files), "size": target.stat().st_size,
            "reason": reason}


def list_backups():
    """최근 것 먼저 — [{name, path, size, created_at, reason, files}]."""
    out = []
    for path in sorted(backup_root().glob("workspace_*.zip"), reverse=True):
        entry = {"name": path.name, "path": str(path), "size": path.stat().st_size,
                 "created_at": None, "reason": None, "files": None}
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            entry.update(created_at=manifest.get("created_at"), reason=manifest.get("reason"),
                         files=len(manifest.get("files") or []))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            entry["reason"] = "읽을 수 없음"
        out.append(entry)
    return out


def prune(keep=KEEP):
    """최근 keep 개만 남긴다."""
    for path in sorted(backup_root().glob("workspace_*.zip"), reverse=True)[keep:]:
        try:
            path.unlink()
        except OSError:
            pass


def auto_backup_if_due(now=None):
    """마지막 백업이 하루보다 오래됐으면(없으면) 새로 만든다 — 앱을 켤 때 부른다. 반환: 만든 백업 또는 None."""
    now = now or datetime.datetime.now()
    latest = max(backup_root().glob("workspace_*.zip"), key=lambda p: p.stat().st_mtime, default=None)
    if latest is not None and now.timestamp() - latest.stat().st_mtime < DUE_AFTER_SEC:
        return None
    return create_backup(reason="auto", now=now)
