"""
채점 실행 로직 — 원래 main.py에 있던 것을 engine 계층으로 내렸다.

main.py는 이제 Material Design 3 웹 UI를 띄우는 진입점만 담당하므로,
채점 흐름(프로젝트 해석 -> 커맨드 결정 -> 수집 -> Pipeline 실행)은 여기 모아둔다.
호출부는 두 곳:
  - api/grade_api.py   (웹 UI의 "채점 실행" 버튼)
  - engine/scheduler.py (정기점검 cron 실행)

Pipeline 경로가 유일한 채점 경로다 — 기존 grade()(Stage 이름을 직접 호출하던
하위호환 경로)와 mock 수집 경로는 폐기했다.
"""
from pathlib import Path

import yaml

from engine.project_manager import (list_projects, get_active_project, set_active_project,
                                     load_project_meta, project_paths, LABS_DIR)


LAB_NAME = None
LAB_DIR = None


def resolve_active_project():
    active = get_active_project()
    if active:
        return active

    projects = list_projects()
    if not projects:
        raise RuntimeError("등록된 프로젝트가 없음 — project_manager.create_project()로 먼저 생성하세요")

    fallback = projects[0]["id"]
    print(f"[안내] 활성 프로젝트가 지정되어 있지 않아 '{fallback}'을 자동으로 선택함")
    set_active_project(fallback)
    return fallback


def init_project(project_id=None):
    """
    project_id를 안 주면 active_project를 따르고, 그것도 없으면 첫 프로젝트를 자동 선택.
    명시하면 그 프로젝트를 활성으로 전환한 뒤 사용.
    """
    global LAB_NAME, LAB_DIR
    if project_id:
        set_active_project(project_id)
        LAB_NAME = project_id
    else:
        LAB_NAME = resolve_active_project()
    LAB_DIR = Path(LABS_DIR) / LAB_NAME
    return LAB_NAME


def load_lab_config():
    with open(LAB_DIR / "target_state.yaml", encoding="utf-8") as f:
        target_state = yaml.safe_load(f)
    with open(LAB_DIR / "stages.yaml", encoding="utf-8") as f:
        stages_cfg = yaml.safe_load(f)["stages"]
    return target_state, stages_cfg


def get_all_commands(stages_cfg):
    """stages.yaml에 정의된 커맨드를 순서 보존 + 중복 제거해서 하나로 모음 — 커맨드 목록의 단일 출처."""
    seen = []
    for stage in stages_cfg:
        for cmd in stage.get("commands", []):
            if cmd not in seen:
                seen.append(cmd)
    return seen


def real_collect(customer_name=None, profile_name=None):
    """실장비 접속 수집. 리턴 형태: {device: {command: raw_text}} (수집 불가 시 None)."""
    from engine.collector import collect_all
    from engine.command_catalog import load_catalog, get_enabled_commands
    from engine.device_inventory import load_inventory

    _, stages_cfg = load_lab_config()
    grading_commands = get_all_commands(stages_cfg)  # 채점에 필요한 커맨드(VLAN/STP 등)

    paths = project_paths(LAB_NAME)
    catalog = load_catalog(paths["commands_catalog"])
    catalog_commands = get_enabled_commands(catalog)  # 카탈로그에서 활성화된 헬스체크 커맨드

    # 두 출처를 합치되 중복 제거 — 채점용 + 헬스체크용 전부 한 번의 접속에서 수집
    commands = list(grading_commands)
    for cmd in catalog_commands:
        if cmd not in commands:
            commands.append(cmd)

    # device_inventory.yaml이 아직 없는 기존 프로젝트라면(lab_meta+ip_allocation만 있던 경우)
    # 여기서 마이그레이션을 트리거해 파일을 만들어둔다 (한 번만 실행되고 이후엔 그대로 로드됨)
    inventory = load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
    enabled_devices = [d for d in inventory.get("devices", []) if d.get("enabled", True)]
    missing_ips = [d.get("name", "이름 없음") for d in enabled_devices if not d.get("management_ip")]
    if not enabled_devices:
        print("[중단] 활성화된 장비가 없습니다 — 장비·SSH 설정에서 장비를 등록하고 사용 여부를 켜세요")
        return None
    if missing_ips:
        print(f"[중단] SSH 관리 IP가 비어 있는 장비: {', '.join(missing_ips)}")
        print("       장비·SSH 설정에서 관리 IP를 입력한 뒤 다시 실행하세요")
        return None

    # Device Inventory에서 enabled 장비만 대상으로 수집 — IP는 collector가 직접 안 다룸
    results, errors, session_dir = collect_all(paths["device_inventory"], LAB_NAME, commands,
                                                customer_name=customer_name, profile_name=profile_name)
    if results is None:
        return None
    if errors:
        print(f"[경고] 수집 실패 장비: {list(errors.keys())}")
    return results


def grade_via_pipeline(collect_fn):
    """
    유일한 채점 경로. Stage 이름을 직접 호출하는 대신 PipelineStep 리스트를 순서대로 실행한다
    (Stage 추가 = Step 추가 — OCP).
    collect_fn: () -> {device: {command: raw_text}}
    """
    import datetime

    from pipeline.pipeline import Pipeline
    from pipeline.steps import (CollectorStep, ParserStep, RuleEngineStep, ScorerStep,
                                  ScoreboardPrintStep, HistoryStep, AlarmStep, AIAnalysisStep, ReportStep)
    from core.context import ProjectContext, SessionContext

    target_state, stages_cfg = load_lab_config()
    project_meta = load_project_meta(LAB_NAME)
    project_ctx = ProjectContext(project_id=LAB_NAME, mode=project_meta["mode"], meta=project_meta,
                                 paths={"target_state": str(LAB_DIR / "target_state.yaml")})
    session_id = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    session_ctx = SessionContext(project=project_ctx, session_id=session_id)

    # 장비별 벤더 매핑 — device_inventory.yaml의 vendor 필드를 그대로 ParserStep에 전달해
    # 벤더별 파서 플러그인이 올바르게 선택되게 한다(설정 안 된 장비는 기본값 arista로 처리).
    device_vendors = {}
    inventory_path = LAB_DIR / "device_inventory.yaml"
    if inventory_path.exists():
        with open(inventory_path, encoding="utf-8") as f:
            inventory = yaml.safe_load(f) or {}
        for d in inventory.get("devices", []):
            if d.get("vendor"):
                device_vendors[d["name"]] = d["vendor"].lower()

    pipeline = Pipeline([
        CollectorStep(collect_fn),
        ParserStep(device_vendors=device_vendors),
        RuleEngineStep(target_state),
        ScorerStep(stages_cfg),
        ScoreboardPrintStep(),
        HistoryStep(),
        AlarmStep(),
        AIAnalysisStep(),
        ReportStep(str(LAB_DIR / "report_latest.md")),
    ])

    return pipeline.run(session_ctx)
