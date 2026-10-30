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

from core.paths import AppPaths, safe_child
from core.atomic_io import dump_yaml_atomic

# exe 패키징 여부와 무관하게 항상 같은 위치를 가리키도록 AppPaths(core/paths.py)로 계산한다
# (예전엔 "labs"/"config/active_project.yaml" 상대경로 리터럴이라 CWD가 실행 위치와 다르면 깨졌음).
LABS_DIR = str(AppPaths.labs_root())
STATE_FILE = str(AppPaths.config_root() / "active_project.yaml")

DEFAULT_STAGES = {"stages": [{"id": "stage_1", "label": "Stage 1", "depends_on": [], "commands": []}]}
DEFAULT_TARGET_STATE = {}
DEFAULT_LAB_META_TEMPLATE = {"lab_name": None, "max_parallel_workers": None, "devices": []}
DEFAULT_IP_ALLOCATION = {
    "default_credentials": {"username": "", "password": ""},   # admin/admin 기본값 제거(1-8)
    "allocations": [],
}
PROJECT_MODES = {"grading", "inspection"}
# 고객사 id(customer_manager 가 uuid4().hex 로 만든다) — labs/_customers/<id> 폴더 이름으로 쓴다.
_CUSTOMER_ID_RE = re.compile(r"[0-9a-f]{32}")


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
        if entry.startswith("_"):   # _customers(고객사 공용), _archived(보관본) — 프로젝트 id 는 '_' 로 시작하지 않는다
            continue
        path = os.path.join(LABS_DIR, entry)
        if not os.path.isdir(path):
            continue
        meta_path = os.path.join(path, "project_meta.yaml")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = yaml.safe_load(f) or {}
            except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
                # 메타 하나가 깨졌다고 목록 전체(→ 고객사 트리, 거의 모든 화면)가 실패하면 안 된다.
                print(f"[프로젝트 목록] project_meta.yaml 을 읽을 수 없어 건너뜀({entry}): {exc}")
                continue
        else:
            # 기존에 project_meta.yaml 없이 만들어진 폴더(예: lab1_campus)도 인식되게 자동 생성
            meta = {"display_name": entry, "created_at": datetime.date.today().isoformat(), "mode": "grading"}
            dump_yaml_atomic(meta, meta_path)
        if not isinstance(meta, dict):
            print(f"[프로젝트 목록] project_meta.yaml 형식이 아니어서 건너뜀({entry})")
            continue
        mode = meta.get("mode", "grading")
        if mode not in PROJECT_MODES:
            print(f"[프로젝트 목록] 지원하지 않는 프로젝트 모드라 건너뜀({entry}: {mode})")
            continue
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
    meta_path = os.path.join(str(safe_child(LABS_DIR, project_id)), "project_meta.yaml")
    if not os.path.exists(meta_path):
        raise ValueError(f"프로젝트 없음: {project_id}")
    with open(meta_path, encoding="utf-8") as f:
        meta = yaml.safe_load(f) or {}
    meta["display_name"] = new_display_name
    dump_yaml_atomic(meta, meta_path)


def delete_project(project_id):
    # id 를 검증 없이 붙이면 빈 문자열이 labs 전체를, '..' 가 사용자 데이터 폴더 전체를 가리킨다 —
    # 그대로 rmtree 하던 경로다. labs 의 '바로 아래' 폴더 하나만 지울 수 있게 한다.
    path = str(safe_child(LABS_DIR, project_id))
    if not os.path.exists(path):
        raise ValueError(f"프로젝트 없음: {project_id}")
    shutil.rmtree(path)
    # 삭제된 게 활성 프로젝트였다면 활성 상태 해제
    active = get_active_project()
    if active == project_id:
        set_active_project(None)


def _load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_active_project():
    return _load_state().get("active_project")


def _customer_id_of(project_id):
    """프로파일이 속한 고객사 id. meta가 없거나 깨졌으면 None(기억하지 않는다)."""
    meta_path = os.path.join(LABS_DIR, project_id, "project_meta.yaml")
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = yaml.safe_load(f) or {}
    except Exception:
        return None
    return meta.get("customer_id")


def customer_id_of(project_id):
    """프로파일이 속한 고객사 id(없으면 None) — 고객사 단위로 묶는 기록(SSH 호스트키 등)이 쓴다."""
    return _customer_id_of(project_id)


def set_active_project(project_id):
    """활성 프로파일을 바꾸고, 그 고객사의 '마지막으로 쓴 프로파일'로도 기억한다.

    active_project 하나만 저장하던 때는 A고객사 → B고객사로 옮겼다가 A로 돌아오면 목록 첫
    프로파일부터 다시 찾아야 했다. 고객사별로 마지막 선택을 남겨 두면 다음에 그 고객사를 열 때
    바로 그 회차가 열린다(정기점검은 고객사마다 진행 중인 회차가 따로 있기 때문).
    """
    if project_id:
        # 화면에서 온 id 다 — '..' 가 활성 프로젝트가 되면 project_paths() 의 모든 경로가 labs 밖을 가리킨다.
        safe_child(LABS_DIR, project_id)
    state = _load_state()
    state["active_project"] = project_id
    last = state.get("last_profile_by_customer")
    if not isinstance(last, dict):
        last = {}
    if project_id:
        customer_id = _customer_id_of(project_id)
        if customer_id:
            last[customer_id] = project_id
    # 삭제된 프로파일을 가리키는 항목은 여기서 정리한다 — 안 그러면 지운 회차가 계속 열린다.
    state["last_profile_by_customer"] = {
        cid: pid for cid, pid in last.items()
        if os.path.exists(os.path.join(LABS_DIR, pid))
    }
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    dump_yaml_atomic(state, STATE_FILE)


def get_last_profile_by_customer():
    """{고객사 id: 마지막으로 연 프로파일 id}. 지워진 프로파일은 제외하고 돌려준다."""
    last = _load_state().get("last_profile_by_customer")
    if not isinstance(last, dict):
        return {}
    return {cid: pid for cid, pid in last.items()
            if pid and os.path.exists(os.path.join(LABS_DIR, pid))}


def project_paths(project_id):
    base = os.path.join(LABS_DIR, project_id)
    customer_base = base
    meta_path = os.path.join(base, "project_meta.yaml")
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = yaml.safe_load(f) or {}
        customer_name = meta.get("customer_name")
        if customer_name:
            # 고객사 공용 폴더는 고객사 id 로 가른다. 예전엔 이름 슬러그(_slugify)였는데 비ASCII 를 지워서
            # 한글 고객사는 전부 'project', 'NC서비스망'·'NC소프트'는 둘 다 'nc' 가 됐다 — A사 카탈로그를
            # 고치면 B사의 점검 명령이 바뀌었다. id 가 아직 없는 메타(고객사 트리를 한 번도 읽지 않은
            # 예전 프로젝트)만 예전 위치를 그대로 쓴다.
            legacy_base = os.path.join(LABS_DIR, "_customers", _slugify(customer_name))
            customer_id = str(meta.get("customer_id") or "")
            customer_base = (os.path.join(LABS_DIR, "_customers", customer_id)
                             if _CUSTOMER_ID_RE.fullmatch(customer_id) else legacy_base)
            # device_inventory는 프로파일마다 독립적으로 관리한다(고객사 공용 아님) —
            # commands_catalog만 고객사 단위로 공유. 새 위치에 아직 없으면 예전 공용 위치의 것을,
            # 그것도 없으면 이 프로파일의 사본을 올린다 — 예전 위치를 함께 쓰던 고객사들은 각자
            # 그 시점의 사본을 받고, 그 뒤로는 따로 바뀐다(1회 이전).
            for filename in ("commands_catalog.yaml",):
                target = os.path.join(customer_base, filename)
                if not os.path.exists(target):
                    for source in (os.path.join(legacy_base, filename), os.path.join(base, filename)):
                        if os.path.exists(source):
                            os.makedirs(customer_base, exist_ok=True)   # 옮길 것이 있을 때만 만든다
                            shutil.copy2(source, target)
                            break
            # 예전 버전에서 고객사 공용으로 저장돼있던 device_inventory.yaml을
            # 프로파일 최초 접근 시 1회만 이 프로파일 전용 파일로 복사(마이그레이션).
            legacy_inventory = os.path.join(legacy_base, "device_inventory.yaml")
            own_inventory = os.path.join(base, "device_inventory.yaml")
            if os.path.exists(legacy_inventory) and not os.path.exists(own_inventory):
                shutil.copy2(legacy_inventory, own_inventory)
            # (예전엔 여기서 ip_allocation.yaml 의 기본 계정을 data/<고객사>/<프로파일>/profile/credential.json 으로
            #  평문 복사했다. 아무도 읽지 않는 사본이라 4-1 에서 만들지 않는다 — 기본 계정은 장비 목록의 기본값이 정본이다.)
    return {
        "lab_meta": os.path.join(base, "lab_meta.yaml"),
        "target_state": os.path.join(base, "target_state.yaml"),
        "stages": os.path.join(base, "stages.yaml"),
        "ip_allocation": os.path.join(base, "ip_allocation.yaml"),
        "commands_catalog": os.path.join(customer_base, "commands_catalog.yaml"),
        "device_inventory": os.path.join(base, "device_inventory.yaml"),
    }


if __name__ == "__main__":
    print("현재 프로젝트 목록:")
    for p in list_projects():
        active_mark = " (활성)" if p["id"] == get_active_project() else ""
        print(f"  - [{p['id']}] {p['display_name']}{active_mark}")
