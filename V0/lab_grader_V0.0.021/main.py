"""
전체 파이프라인 진입점.
실행: python3 main.py  (실제 collector.collect_all로 장비 접속 후 채점)

프로젝트는 project_manager의 active_project를 따른다.
지정 안 돼있으면 첫 번째로 발견되는 프로젝트를 활성으로 자동 설정한다.
"""
import sys
import time
import yaml

from parsers import show_vlan, show_spanning_tree
from engine.comparator import compare_vlan_stage, compare_stp_stage, build_vlan_index
from engine.scorer import score_all, print_scoreboard
from engine.history import save_history, load_previous
from engine.project_manager import list_projects, get_active_project, set_active_project, load_project_meta


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


LAB_NAME = None
LAB_DIR = None


def init_project(project_id=None):
    """
    project_id를 안 주면 active_project를 따르고, 그것도 없으면 첫 프로젝트를 자동 선택.
    --project 옵션으로 명시하면 그 프로젝트를 활성으로 전환한 뒤 사용.
    (import 시점이 아니라 실행 시점에만 호출 — main.py를 다른 모듈에서 import할 때
     의도치 않게 프로젝트 상태를 건드리지 않기 위함)
    """
    global LAB_NAME, LAB_DIR
    if project_id:
        set_active_project(project_id)
        LAB_NAME = project_id
    else:
        LAB_NAME = resolve_active_project()
    LAB_DIR = f"labs/{LAB_NAME}"
    return LAB_NAME


def load_lab_config():
    with open(f"{LAB_DIR}/target_state.yaml", encoding="utf-8") as f:
        target_state = yaml.safe_load(f)
    with open(f"{LAB_DIR}/stages.yaml", encoding="utf-8") as f:
        stages_cfg = yaml.safe_load(f)["stages"]
    return target_state, stages_cfg


def adapt_raw_to_collected(raw_by_device):
    """
    collector가 리턴하는 {device: {command: raw_text}} 를
    comparator가 요구하는 {device: {vlan_id: {...}}} 두 종류(vlan/stp)로 변환.
    """
    collected_vlan = {}
    collected_stp = {}
    for device, cmds in raw_by_device.items():
        for cmd_text, raw in cmds.items():
            if cmd_text.startswith("show vlan"):
                collected_vlan[device] = show_vlan.parse(raw)
            elif cmd_text.startswith("show spanning-tree vlan"):
                collected_stp[device] = show_spanning_tree.parse_combined(raw)
    return collected_vlan, collected_stp


def grade(collect_fn):
    """collect_fn: () -> {device: {command: raw_text}}  (실장비든 mock이든 동일 인터페이스)"""
    target_state, stages_cfg = load_lab_config()

    started = time.time()
    raw_by_device = collect_fn()
    if raw_by_device is None:
        print("수집 실패 또는 중단됨 — 채점 건너뜀")
        return

    collected_vlan, collected_stp = adapt_raw_to_collected(raw_by_device)
    vlan_index = build_vlan_index(collected_stp)

    stage_results = {
        "stage_vlan": compare_vlan_stage(target_state["stage_vlan"]["checks"], collected_vlan),
        "stage_stp": compare_stp_stage(target_state["stage_stp"]["checks"], collected_stp, vlan_index),
    }
    scored = score_all(stages_cfg, stage_results)
    elapsed = time.time() - started

    print_scoreboard(scored, session_label=f"(자동 수집, {elapsed:.1f}초 소요)")

    prev = load_previous(LAB_NAME)
    path = save_history(LAB_NAME, scored, elapsed)
    print(f"\n[저장됨] {path}")

    if prev:
        from engine.history import compare_sessions, compare_check_level
        curr_payload = {"session": "current", "stages": scored}
        stage_diff = compare_sessions(prev, curr_payload)
        check_diff = compare_check_level(prev, curr_payload)
        print(f"\n[직전 회차({prev['session']}) 대비 변화]")
        for d in stage_diff:
            print(f"  {d['stage']}: {d['prev_pass']}/{d['prev_total']} -> {d['curr_pass']}/{d['curr_total']}  ({d['trend']})")
        for c in check_diff:
            print(f"  [{c['stage']}] {c['check']}: {c['from']} -> {c['to']}")
        if not stage_diff and not check_diff:
            print("  변화 없음")

    # --- AI 분석 (규칙기반 항상 동작, API/로컬 설정 있으면 우선 시도) ---
    from ai_analysis.router import analyze as ai_analyze
    ai_result = ai_analyze(scored, ai_config=None)  # 프로젝트별 ai_config.yaml 연결은 로드맵
    print(f"\n[AI 분석 — {ai_result['source']}] {ai_result['summary']}")

    # --- 보고서 자동 생성 ---
    from report.markdown_report import save_markdown_report
    report_path = f"{LAB_DIR}/report_latest.md"
    save_markdown_report(LAB_NAME, scored, ai_result, report_path)
    print(f"[보고서 생성됨] {report_path}")

    return scored


def grade_via_pipeline(collect_fn):
    """
    grade()의 Pipeline 버전 — main.py가 Stage 이름을 직접 호출하던 방식(OCP 위반)을
    PipelineStep 리스트로 대체. 기존 grade()는 회귀비교용으로 그대로 남겨둠.
    """
    from pipeline.pipeline import Pipeline
    from pipeline.steps import (CollectorStep, ParserStep, RuleEngineStep, ScorerStep,
                                  ScoreboardPrintStep, HistoryStep, AlarmStep, AIAnalysisStep, ReportStep)
    from core.context import ProjectContext, SessionContext
    import datetime

    target_state, stages_cfg = load_lab_config()
    project_meta = load_project_meta(LAB_NAME)
    project_ctx = ProjectContext(project_id=LAB_NAME, mode=project_meta["mode"], meta=project_meta,
                                 paths={"target_state": f"{LAB_DIR}/target_state.yaml"})
    session_id = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    session_ctx = SessionContext(project=project_ctx, session_id=session_id)

    pipeline = Pipeline([
        CollectorStep(collect_fn),
        ParserStep(),
        RuleEngineStep(target_state),
        ScorerStep(stages_cfg),
        ScoreboardPrintStep(),
        HistoryStep(),
        AlarmStep(),
        AIAnalysisStep(),
        ReportStep(f"{LAB_DIR}/report_latest.md"),
    ])

    result_ctx = pipeline.run(session_ctx)
    return result_ctx


def get_all_commands(stages_cfg):
    """stages.yaml에 정의된 커맨드를 순서 보존 + 중복 제거해서 하나로 모음 — 커맨드 목록의 단일 출처."""
    seen = []
    for stage in stages_cfg:
        for cmd in stage.get("commands", []):
            if cmd not in seen:
                seen.append(cmd)
    return seen


def real_collect(customer_name=None, profile_name=None):
    from engine.collector import collect_all
    from engine.command_catalog import load_catalog, get_enabled_commands
    from engine.project_manager import project_paths
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


if __name__ == "__main__":
    project_arg = None
    if "--project" in sys.argv:
        idx = sys.argv.index("--project")
        if idx + 1 < len(sys.argv):
            project_arg = sys.argv[idx + 1]

    active = init_project(project_arg)
    print(f"[프로젝트] {active}")

    if "--pipeline" in sys.argv:
        grade_via_pipeline(real_collect)
    else:
        grade(real_collect)
