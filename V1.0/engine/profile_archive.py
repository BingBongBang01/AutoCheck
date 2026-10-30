"""정기점검 프로파일 보관(Archive)·복원·영구 삭제.

프로파일 하나는 두 곳이다 — labs/<id>(메타·장비목록) + data/<고객사>/<프로파일>(runs·보고서·로그).
보관은 둘을 한 폴더로 옮긴다:

    data/<고객사>/_archived_profiles/<프로파일>_<시각>/
        …원래 data 폴더 내용 그대로…
        _labs_project/<id>/          ← labs 쪽
        archive.json                 ← 복원에 필요한 것(id, 고객사·id, 프로파일 이름, 시각)

예전엔 삭제가 두 경로(고객사 트리, 프로젝트 모달) 모두 영구 rmtree 였고, 고객사를 지우면 보관본까지
함께 지워졌다. 워크스페이스의 '보관'은 data 폴더만 옮겨서 labs 프로젝트가 목록에 그대로 남았고,
다음 접근에 빈 data 폴더가 다시 생겼다. 이제 지우는 동작은 보관이고, 영구 삭제는 보관본에만 한다.
고객사 없이 만들어진 예전 프로젝트는 labs/_archived/<id>_<시각>/ 에 labs 쪽만 보관한다.
"""
import datetime
import json
import shutil
from pathlib import Path

from core.paths import safe_child, safe_filename
from core.process_lock import is_locked, profile_lock_path
from engine import project_manager as pm
from engine.profile_manager import profile_manager as prm

ARCHIVE_DIRNAME = "_archived_profiles"
LABS_ARCHIVE_DIRNAME = "_archived"
LABS_PART = "_labs_project"
MANIFEST = "archive.json"


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def _unique(path: Path) -> Path:
    candidate, n = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}_{n}")
        n += 1
    return candidate


def archive_profile(project_id, customer_name=None, profile_name=None, customer_id=None) -> Path:
    """프로파일을 보관하고 보관 폴더를 돌려준다. 중간에 실패하면 옮긴 것을 되돌리고 예외를 올린다.
    보관한 프로파일이 활성이었으면 활성 상태를 푼다."""
    labs_dir = safe_child(pm.LABS_DIR, project_id)
    if not labs_dir.is_dir():
        raise ValueError(f"프로젝트 없음: {project_id}")
    if customer_name and profile_name:
        data_dir = prm.profile_dir(customer_name, profile_name)
        if is_locked(profile_lock_path(data_dir)):
            # 작업 스케줄러가 띄운 무인 정기점검이 이 폴더에 쓰는 중이다 — 옮기면 그 점검이 깨진다(4-3).
            raise ValueError("이 프로파일의 무인 정기점검이 진행 중입니다 — 끝난 뒤에 다시 보관하세요.")
        dst = _unique(prm.customer_dir(customer_name) / ARCHIVE_DIRNAME / f"{safe_filename(profile_name)}_{_stamp()}")
    else:
        data_dir = None
        dst = _unique(Path(pm.LABS_DIR) / LABS_ARCHIVE_DIRNAME / f"{project_id}_{_stamp()}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    moved_data = data_dir is not None and data_dir.exists()
    if moved_data:
        shutil.move(str(data_dir), str(dst))
    else:
        dst.mkdir()
    labs_target = dst / LABS_PART / project_id
    try:
        labs_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(labs_dir), str(labs_target))
        manifest = {"project_id": project_id, "customer_name": customer_name, "customer_id": customer_id,
                    "profile_name": profile_name,
                    "archived_at": datetime.datetime.now().isoformat(timespec="seconds")}
        (dst / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        if labs_target.exists() and not labs_dir.exists():
            shutil.move(str(labs_target), str(labs_dir))
        shutil.rmtree(dst / LABS_PART, ignore_errors=True)
        (dst / MANIFEST).unlink(missing_ok=True)
        if moved_data:
            shutil.move(str(dst), str(data_dir))
        else:
            shutil.rmtree(dst, ignore_errors=True)
        raise
    if pm.get_active_project() == project_id:
        pm.set_active_project(None)
    _drop_schedule(project_id)
    return dst


def _drop_schedule(project_id):
    """보관한 프로파일의 무인 정기점검 일정과 작업 스케줄러 작업을 지운다 — 남겨 두면 화면에서 닿지 않는 작업이
    정해진 시각마다 '프로파일이 없습니다'로 실패한다(IMPROVEMENT_PLAN 4-3). 복원해도 일정은 돌아오지 않는다(다시
    저장한다). 작업을 지우지 못하면 일정은 남긴다 — 다음 실행이 실패로 기록되어 배지로 알린다. 보관은 되돌리지 않는다."""
    from engine import inspection_schedule, task_scheduler
    schedule = inspection_schedule.for_project(project_id)
    if schedule is None:
        return
    try:
        result = task_scheduler.unregister(schedule["id"])
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    if result.get("ok"):
        inspection_schedule.remove(project_id)
    else:
        print(f"[보관] 무인 정기점검 작업을 지우지 못했습니다({project_id}): {result.get('error')}")


def _archive_roots():
    """보관본이 있을 수 있는 폴더들: data/<고객사>/_archived_profiles, labs/_archived."""
    roots = []
    data_root = prm.data_root
    if data_root.is_dir():
        roots += [c / ARCHIVE_DIRNAME for c in sorted(data_root.iterdir()) if (c / ARCHIVE_DIRNAME).is_dir()]
    labs_archive = Path(pm.LABS_DIR) / LABS_ARCHIVE_DIRNAME
    if labs_archive.is_dir():
        roots.append(labs_archive)
    return roots


def list_archives(customer_name=None) -> list:
    """보관본 목록(최근 것 먼저). restorable=False 는 예전 '보관'(data 폴더만 옮긴 것)이라 복원 정보가 없다."""
    result = []
    for root in _archive_roots():
        customer = root.parent.name if root.name == ARCHIVE_DIRNAME else None
        if customer_name is not None and (customer or "").casefold() != customer_name.casefold():
            continue
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            manifest = _read_manifest(entry)
            result.append({
                "archive": entry.name, "path": str(entry), "customer_name": customer,
                "profile_name": manifest.get("profile_name") if manifest else entry.name,
                "project_id": manifest.get("project_id") if manifest else None,
                "archived_at": manifest.get("archived_at") if manifest else None,
                "restorable": manifest is not None,
            })
    result.sort(key=lambda a: a["archived_at"] or "", reverse=True)
    return result


def _read_manifest(entry: Path):
    try:
        data = json.loads((entry / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("project_id") else None


def find_archive(archive_name, customer_name=None) -> Path:
    """보관 폴더 이름(+고객사)으로 보관본을 찾는다. 화면이 넘기는 값이라 한 단계 이름만 받는다."""
    if customer_name:
        path = safe_child(prm.customer_dir(customer_name) / ARCHIVE_DIRNAME, archive_name)
    else:
        path = safe_child(Path(pm.LABS_DIR) / LABS_ARCHIVE_DIRNAME, archive_name)
    if not path.is_dir():
        raise ValueError(f"보관본을 찾을 수 없습니다: {archive_name}")
    return path


def restore_profile(archive_dir: Path) -> str:
    """보관본을 원래 자리로 되돌리고 project_id 를 돌려준다. 같은 자리(같은 id 의 프로젝트, 같은 이름의
    data 폴더)가 이미 있으면 아무것도 바꾸지 않고 ValueError — 다른 프로파일과 섞이지 않게."""
    manifest = _read_manifest(archive_dir)
    if manifest is None:
        raise ValueError("복원 정보(archive.json)가 없는 예전 보관본입니다 — 폴더를 직접 옮겨 복원하세요.")
    project_id = manifest["project_id"]
    customer_name, profile_name = manifest.get("customer_name"), manifest.get("profile_name")
    labs_target = safe_child(pm.LABS_DIR, project_id)
    if labs_target.exists():
        raise ValueError(f"같은 id 의 프로젝트가 이미 있습니다: {project_id}")
    data_target = prm.profile_dir(customer_name, profile_name) if customer_name and profile_name else None
    if data_target is not None and data_target.exists():
        raise ValueError(f"'{profile_name}' 이름의 정기점검이 이미 있습니다 — 그 프로파일의 이름을 바꾼 뒤 복원하세요.")

    shutil.move(str(archive_dir / LABS_PART / project_id), str(labs_target))
    shutil.rmtree(archive_dir / LABS_PART, ignore_errors=True)
    (archive_dir / MANIFEST).unlink(missing_ok=True)
    if data_target is not None:
        data_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(archive_dir), str(data_target))
        _restore_customer_record(customer_name, manifest.get("customer_id"))
    else:
        shutil.rmtree(archive_dir, ignore_errors=True)
    return project_id


def _restore_customer_record(customer_name, customer_id):
    """고객사째 보관됐다면 고객사 목록에 원래 id 로 되살린다(고객사 공용 카탈로그가 id 폴더라서)."""
    from engine import customer_manager as cm
    customers = cm.load()
    if any(c["name"].casefold() == customer_name.casefold() for c in customers):
        return
    now = datetime.datetime.now().isoformat(timespec="seconds")
    record = cm.ensure_customer(customers, customer_name)
    if customer_id and not any(c["id"] == customer_id for c in customers):
        record["id"] = customer_id
    record["updated_at"] = now
    cm.save(customers)


def purge_archive(archive_dir: Path) -> None:
    """보관본 하나를 영구 삭제한다 — 되돌릴 수 없다. 보관 폴더 안의 것만 지울 수 있다."""
    archive_dir = Path(archive_dir)
    if archive_dir.parent.name not in (ARCHIVE_DIRNAME, LABS_ARCHIVE_DIRNAME):
        raise ValueError(f"보관본이 아닙니다: {archive_dir}")
    shutil.rmtree(archive_dir)
