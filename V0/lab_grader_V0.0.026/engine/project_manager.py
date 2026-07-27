"""
프로젝트(랩) 단위 관리. labs/ 아래 폴더 하나 = 프로젝트 하나.
각 프로젝트는 자기만의 lab_meta.yaml / target_state.yaml / stages.yaml /
ip_allocation.yaml / commands_catalog.yaml 을 따로 가진다 (완전히 독립).

폴더명(id)은 한 번 정해지면 안 바꿈(다른 파일들이 경로로 참조하므로) —
이름 변경은 project_meta.yaml의 display_name만 바꾸는 방식으로 처리해 안전하게 함.
"""
import os
import re
import yaml
import shutil
import datetime

from core.paths import AppPaths
from core.atomic_io import dump_yaml_atomic

# exe 패키징 여부와 무관하게 항상 같은 위치를 가리키도록 AppPaths(core/paths.py)로 계산한다
# (예전엔 "labs"/"config/active_project.yaml" 상대경로 리터럴이라 CWD가 실행 위치와 다르면 깨졌음).
LABS_DIR = str(AppPaths.labs_root())
STATE_FILE = str(AppPaths.config_root() / "active_project.yaml")

DEFAULT_STAGES = {"stages": [{"id": "stage_1", "label": "Stage 1", "depends_on": [], "commands": []}]}
DEFAULT_TARGET_STATE = {}
DEFAULT_LAB_META_TEMPLATE = {"lab_name": None, "max_parallel_workers": None, "devices": []}
DEFAULT_IP_ALLOCATION = {
    "default_credentials": {"username": "admin", "password": "admin"},
    "allocations": [],
}
PROJECT_MODES = {"grading", "inspection"}


def _slugify(name):
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip().lower()).strip("_")
    return slug or "project"


def _unique_project_id(display_name):
    """
    한글 등 비ASCII 이름은 슬러그화하면 다 사라지거나(예: 'project') 숫자 몇 개만 남는 등
    (예: '2') 알아보기 힘든 폴더명이 될 수 있음. 이런 저품질 슬러그는 충돌 여부와 무관하게
    타임스탬프를 붙여 구분 가능하게 만든다. 정상적인 영문 슬러그는 그대로 쓰되, 충돌 시에만 붙인다.
    """
    base = _slugify(display_name)
    is_low_quality = (base == "project") or len(base) < 3 or base.isdigit()
    already_exists = os.path.exists(os.path.join(LABS_DIR, base))

    if not is_low_quality and not already_exists:
        return base

    suffix = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{base}_{suffix}"


def list_projects():
    """각 프로젝트 폴더의 project_meta.yaml을 읽어 표시용 이름까지 포함해 반환."""
    if not os.path.exists(LABS_DIR):
        return []
    projects = []
    for entry in sorted(os.listdir(LABS_DIR)):
        if entry == "_customers":
            continue
        path = os.path.join(LABS_DIR, entry)
        if not os.path.isdir(path):
            continue
        meta_path = os.path.join(path, "project_meta.yaml")
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                meta = yaml.safe_load(f) or {}
        else:
            # 기존에 project_meta.yaml 없이 만들어진 폴더(예: lab1_campus)도 인식되게 자동 생성
            meta = {"display_name": entry, "created_at": datetime.date.today().isoformat(), "mode": "grading"}
            dump_yaml_atomic(meta, meta_path)
        mode = meta.get("mode", "grading")
        if mode not in PROJECT_MODES:
            raise ValueError(f"지원하지 않는 프로젝트 모드: {mode}")
        projects.append({"id": entry, "display_name": meta.get("display_name", entry),
                          "created_at": meta.get("created_at"), "mode": mode})
    return projects


def create_project(display_name):
    project_id = _unique_project_id(display_name)
    path = os.path.join(LABS_DIR, project_id)
    if os.path.exists(path):
        raise ValueError(f"이미 존재하는 프로젝트 id: {project_id} (다른 이름을 쓰세요)")

    os.makedirs(path)
    with open(os.path.join(path, "project_meta.yaml"), "w", encoding="utf-8") as f:
        # created_at은 초 단위까지 기록한다 — 날짜만 있으면 같은 날 만든 프로파일들의
        # 선후를 가릴 수 없어서 "직전 프로파일에서 장비목록 복사"가 엉뚱한 걸 고른다.
        # 날짜만 있는 기존 값('2026-07-27')과 섞여도 문자열 정렬이 그대로 성립한다.
        yaml.dump({"display_name": display_name,
                    "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
                    "mode": "grading"},
                   f, allow_unicode=True, sort_keys=False)

    lab_meta = dict(DEFAULT_LAB_META_TEMPLATE)
    lab_meta["lab_name"] = project_id
    dump_yaml_atomic(lab_meta, os.path.join(path, "lab_meta.yaml"))
    dump_yaml_atomic(DEFAULT_STAGES, os.path.join(path, "stages.yaml"))
    dump_yaml_atomic(DEFAULT_TARGET_STATE, os.path.join(path, "target_state.yaml"))
    dump_yaml_atomic(DEFAULT_IP_ALLOCATION, os.path.join(path, "ip_allocation.yaml"))

    from engine.command_catalog import load_catalog
    load_catalog(path=os.path.join(path, "commands_catalog.yaml"))  # 기본 카탈로그 생성

    from engine.device_inventory import save_inventory, DEFAULT_PROJECT_DEFAULTS
    save_inventory({"defaults": dict(DEFAULT_PROJECT_DEFAULTS), "devices": []},
                    os.path.join(path, "device_inventory.yaml"))

    return project_id


def rename_project(project_id, new_display_name):
    """폴더(id)는 안 건드리고 표시 이름만 바꿈 — 다른 파일의 경로 참조가 깨지지 않게."""
    meta_path = os.path.join(LABS_DIR, project_id, "project_meta.yaml")
    if not os.path.exists(meta_path):
        raise ValueError(f"프로젝트 없음: {project_id}")
    with open(meta_path, encoding="utf-8") as f:
        meta = yaml.safe_load(f) or {}
    meta["display_name"] = new_display_name
    dump_yaml_atomic(meta, meta_path)


def delete_project(project_id):
    path = os.path.join(LABS_DIR, project_id)
    if not os.path.exists(path):
        raise ValueError(f"프로젝트 없음: {project_id}")
    shutil.rmtree(path)
    # 삭제된 게 활성 프로젝트였다면 활성 상태 해제
    active = get_active_project()
    if active == project_id:
        set_active_project(None)


def get_active_project():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, encoding="utf-8") as f:
        state = yaml.safe_load(f) or {}
    return state.get("active_project")


def set_active_project(project_id):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    dump_yaml_atomic({"active_project": project_id}, STATE_FILE)


def project_paths(project_id):
    base = os.path.join(LABS_DIR, project_id)
    customer_base = base
    meta_path = os.path.join(base, "project_meta.yaml")
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = yaml.safe_load(f) or {}
        customer_name = meta.get("customer_name")
        if customer_name:
            customer_base = os.path.join(LABS_DIR, "_customers", _slugify(customer_name))
            os.makedirs(customer_base, exist_ok=True)
            # device_inventory는 프로파일마다 독립적으로 관리한다(고객사 공용 아님) —
            # commands_catalog만 고객사 단위로 공유.
            for filename in ("commands_catalog.yaml",):
                source = os.path.join(base, filename)
                target = os.path.join(customer_base, filename)
                if os.path.exists(source) and not os.path.exists(target):
                    shutil.copy2(source, target)
            # 예전 버전에서 고객사 공용으로 저장돼있던 device_inventory.yaml을
            # 프로파일 최초 접근 시 1회만 이 프로파일 전용 파일로 복사(마이그레이션).
            legacy_inventory = os.path.join(customer_base, "device_inventory.yaml")
            own_inventory = os.path.join(base, "device_inventory.yaml")
            if os.path.exists(legacy_inventory) and not os.path.exists(own_inventory):
                shutil.copy2(legacy_inventory, own_inventory)

            # ip_allocation.yaml에 남아있는 default_credentials(계정/비밀번호)를
            # data/<고객사>/<프로파일>/profile/credential.json으로 1회 이전.
            profile_name = meta.get("profile_name") or meta.get("display_name") or project_id
            from engine.profile_manager import profile_manager
            try:
                profile_manager.migrate_credentials_from_yaml(
                    customer_name, profile_name, os.path.join(base, "ip_allocation.yaml"))
            except ValueError:
                pass  # 고객사/프로파일명이 폴더명으로 쓸 수 없는 값이면 마이그레이션은 건너뜀
    return {
        "lab_meta": os.path.join(base, "lab_meta.yaml"),
        "target_state": os.path.join(base, "target_state.yaml"),
        "stages": os.path.join(base, "stages.yaml"),
        "ip_allocation": os.path.join(base, "ip_allocation.yaml"),
        "commands_catalog": os.path.join(customer_base, "commands_catalog.yaml"),
        "device_inventory": os.path.join(base, "device_inventory.yaml"),
    }


def load_project_meta(project_id):
    path = os.path.join(LABS_DIR, project_id, "project_meta.yaml")
    if not os.path.exists(path):
        raise ValueError(f"프로젝트 없음: {project_id}")
    with open(path, encoding="utf-8") as f:
        meta = yaml.safe_load(f) or {}
    mode = meta.get("mode", "grading")
    if mode not in PROJECT_MODES:
        raise ValueError(f"지원하지 않는 프로젝트 모드: {mode}")
    meta["mode"] = mode
    return meta


if __name__ == "__main__":
    print("현재 프로젝트 목록:")
    for p in list_projects():
        active_mark = " (활성)" if p["id"] == get_active_project() else ""
        print(f"  - [{p['id']}] {p['display_name']}{active_mark}")
