"""작업공간 무결성 점검 — 데이터 폴더가 앱이 기대하는 모양인지 훑어 문제를 알린다(IMPROVEMENT_PLAN 4-10).

손으로 폴더 이름을 바꾸거나 지우고, 앱이 쓰는 도중에 꺼지고, 다른 PC 로 데이터 폴더를 옮기면 이런 일이 생긴다 —
지금까지는 그 결과가 '고객사 목록에 프로파일이 안 보인다', '보고서에 장비가 빈다', '비밀번호가 사라졌다'로만 드러났다.
고치지는 않는다(무엇이 맞는지 사람이 정해야 한다). 무엇이 어긋났는지와 어떻게 하면 되는지를 말한다. 읽기만 한다.

수준: error — 그대로 두면 기록을 못 쓰거나 잃는다 / warn — 화면과 폴더가 어긋났다 / info — 정리해도 되는 찌꺼기.
잠금 파일(locks/)은 보지 않는다 — OS 파일 잠금이라 프로세스가 죽으면 풀리고, 파일이 남아도 해가 없다(core/process_lock).
"""
import fnmatch
import json
import os
import time
from pathlib import Path

import yaml

from core.paths import AppPaths

# 원자적 쓰기의 임시 파일(core/atomic_io·storage_service: .<이름>.….tmp, profile_manager: .<이름>.tmp-<uuid>)과
# 쓰다 만 zip(.part). 지금 쓰는 중인 파일을 찌꺼기로 몰지 않게 10분 지난 것만 센다.
_TEMP_PATTERNS = ("*.tmp", ".*.tmp-*", "*.part")
_TEMP_MIN_AGE_SEC = 600
_CORRUPT_PATTERN = "*.corrupt-*"   # 깨진 파일을 앱이 옮겨 둔 것(지원 이력·실시간 감시 상태)
_MAX_LISTED = 20


def check_workspace():
    """반환: {checked_at, counts:{error,warn,info}, findings:[{level, area, path, message, hint}]}."""
    root = AppPaths.user_data_root()
    findings = []

    def add(level, area, path, message, hint=""):
        findings.append({"level": level, "area": area, "path": _rel(path, root), "message": message, "hint": hint})

    projects = _check_projects(add)
    _check_data_folders(add, projects)
    _check_runs(add, projects)
    _check_files(add, root)
    _check_leftovers(add, root)
    counts = {level: sum(1 for f in findings if f["level"] == level) for level in ("error", "warn", "info")}
    order = {"error": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: (order[f["level"]], f["area"], f["path"]))
    return {"checked_at": time.strftime("%Y-%m-%d %H:%M:%S"), "counts": counts, "findings": findings}


def _rel(path, root):
    try:
        return str(Path(path).relative_to(root))
    except (ValueError, TypeError):
        return str(path or "")


def _key(customer, profile):
    """data 폴더 비교용 — Windows 폴더 이름은 대소문자를 가리지 않는다(api/customer_profile_api._same_folder)."""
    return (customer or "").casefold(), (profile or "").casefold()


def _check_projects(add):
    """labs/<id> — 메타를 읽을 수 있는가, 장비 목록이 읽히는가, 이 PC 에서 풀 수 없는 봉인 비밀번호가 있는가.
    반환: {(고객사, 프로파일): project_id} — 데이터 폴더와 견주는 데 쓴다."""
    from engine import device_inventory as di
    from engine import project_manager as pm
    linked = {}
    labs = Path(pm.LABS_DIR)
    if not labs.is_dir():
        return linked
    for entry in sorted(labs.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        meta_path = entry / "project_meta.yaml"
        if not meta_path.is_file():
            continue
        meta = _load_yaml(meta_path)
        if not isinstance(meta, dict):
            add("error", "프로파일", meta_path, "프로젝트 정보(project_meta.yaml)를 읽을 수 없어 목록에서 빠진다",
                "백업(backups/)에서 이 파일을 되살리세요")
            continue
        if meta.get("customer_name") and meta.get("profile_name"):
            linked[(meta["customer_name"], meta["profile_name"])] = entry.name
        inventory_path = entry / "device_inventory.yaml"
        if not inventory_path.is_file():
            continue
        if not isinstance(_load_yaml(inventory_path), dict):
            add("error", "장비 목록", inventory_path, "장비 목록을 읽을 수 없다",
                "백업(backups/)에서 device_inventory.yaml 을 되살리세요")
            continue
        try:
            inventory = di.load_inventory(str(inventory_path))   # 파일이 있으면 읽기만 한다
        except Exception as exc:
            add("error", "장비 목록", inventory_path, f"장비 목록을 읽지 못했다 — {type(exc).__name__}: {exc}")
            continue
        records = [inventory.get("defaults") or {}] + list(inventory.get("devices") or [])
        sealed = sum(len(record.get("unreadable_secrets") or {}) for record in records)
        if sealed:
            add("warn", "장비 목록", inventory_path,
                f"이 PC 에서 풀 수 없는 봉인 비밀번호 {sealed}개 — 다른 PC·사용자에서 옮겨 온 데이터다",
                "장비 목록에서 비밀번호를 다시 입력하세요")
    return linked


def _check_data_folders(add, linked):
    """data/<고객사>/<프로파일> 과 프로젝트·고객사 목록이 서로 맞는가."""
    from engine import customer_manager as cm
    data_root = AppPaths.data_root()
    try:
        known_customers = {(c.get("name") or "").casefold() for c in cm.load()}
    except Exception:
        known_customers = None      # 고객사 목록 자체가 깨졌다 — _check_files 가 알린다. 폴더마다 경고하지 않는다
    linked_keys = {_key(*pair) for pair in linked}
    folders = set()
    for customer in sorted(p for p in data_root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        if known_customers is not None and customer.name.casefold() not in known_customers:
            add("warn", "고객사", customer, "고객사 목록에 없는 고객사 폴더 — 화면에서 보이지 않는다",
                "폴더 이름을 바꿨다면 원래 이름으로 되돌리거나, 고객사를 같은 이름으로 다시 만드세요")
        for profile in sorted(p for p in customer.iterdir() if p.is_dir() and not p.name.startswith("_")):
            folders.add(_key(customer.name, profile.name))
            if _key(customer.name, profile.name) not in linked_keys:
                add("warn", "프로파일", profile, "어느 정기점검 프로파일에도 연결되지 않은 점검 데이터 폴더 — 화면에서 열 수 없다",
                    "폴더 이름을 바꿨다면 되돌리세요. 같은 이름으로 프로파일을 만들면 이 기록을 이어서 씁니다")
    for (customer, profile), project_id in sorted(linked.items()):
        if _key(customer, profile) not in folders:
            add("warn", "프로파일", data_root / customer / profile,
                f"정기점검 프로파일({project_id})의 점검 데이터 폴더가 없다 — 점검 기록·보고서가 비어 보인다",
                "폴더를 옮기거나 이름을 바꿨는지 확인하세요")


def _check_runs(add, linked):
    from engine.run_manager import CORRUPTED_STATUS, run_manager
    for customer, profile in sorted(linked):
        try:
            runs = run_manager.list_runs(customer, profile)
        except Exception:
            continue
        for run in runs:
            if run.get("status") == CORRUPTED_STATUS:
                add("warn", "Run", AppPaths.data_root() / customer / profile / "runs" / str(run.get("run_id")),
                    f"Run 정보(session.json)를 읽을 수 없다 — {run.get('error') or '?'}",
                    "원본 로그(raw/)는 그대로 있다. Run 이력에는 CORRUPTED 로만 보인다")


def _check_files(add, root):
    """설정·상태 파일이 읽히는가 — 앱 전역 설정과 프로파일마다의 상태·보고서 기록."""
    candidates = [root / name for name in ("ai_config.yaml", "ai_settings.yaml", "connection.yaml", "schedules.yaml",
                                           "ssh_host_keys.json")]
    candidates += sorted(p for p in AppPaths.config_root().iterdir() if p.suffix.lower() in (".yaml", ".yml", ".json"))
    data_root = AppPaths.data_root()
    for pattern in ("*/*/realtime_state.json", "*/*/reports/_snapshot.json", "*/*/reports/_support_history.json",
                    "*/report_template.yaml"):
        candidates += sorted(data_root.glob(pattern))
    for path in candidates:
        if not path.is_file():
            continue
        loaded = _load_json(path) if path.suffix.lower() == ".json" else _load_yaml(path)
        if loaded is None:
            backup = path.with_name(path.name + ".bak")
            add("error", "파일", path, "파일을 읽을 수 없다(형식이 깨졌다)",
                "같은 폴더의 .bak 으로 되살릴 수 있다" if backup.is_file() else "백업(backups/)에서 되살리세요")


def _check_leftovers(add, root):
    """쓰다 만 임시 파일과 깨져서 옮겨 둔 파일 — 해는 없지만 쌓인다. 한 번만 훑는다(로그가 많은 폴더)."""
    now = time.time()
    temps, corrupt = [], []
    for folder, _dirs, files in os.walk(root):
        for name in files:
            path = Path(folder) / name
            if fnmatch.fnmatch(name, _CORRUPT_PATTERN):
                corrupt.append(path)
            elif any(fnmatch.fnmatch(name, pattern) for pattern in _TEMP_PATTERNS):
                try:
                    if now - path.stat().st_mtime >= _TEMP_MIN_AGE_SEC:
                        temps.append(path)
                except OSError:
                    continue
    for paths, message, hint in (
            (sorted(corrupt), "깨져서 앱이 옮겨 둔 파일 — 앱은 .bak 이나 빈 상태로 이어 썼다",
             "무엇이 깨졌는지 볼 일이 없으면 지워도 된다"),
            (sorted(temps), "쓰다 만 임시 파일 — 쓰는 도중에 앱이 꺼졌다", "지워도 된다")):
        for path in paths[:_MAX_LISTED]:
            add("info", "찌꺼기", path, message, hint)
        if len(paths) > _MAX_LISTED:
            add("info", "찌꺼기", root, f"{message.split(' —')[0]} {len(paths) - _MAX_LISTED}개 더")


def _load_yaml(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = yaml.safe_load(f)
    except (OSError, ValueError, yaml.YAMLError):
        return None
    return data if data is not None else {}


def _load_json(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
