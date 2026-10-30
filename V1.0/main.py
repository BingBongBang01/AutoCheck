"""
AutoCheck 진입점 — Material Design 3 웹 UI(web_ui/)를 pywebview로 띄운다.

실행: python main.py
필요: pip install -r requirements.txt  (pywebview 포함)
Windows는 시스템 내장 Edge WebView2를 자동으로 사용함(추가 설치 보통 불필요).

Api 클래스는 프로젝트/대시보드/정기점검 보고서/카탈로그/인벤토리/연결설정 등 관심사별
mixin(api/ 패키지)을 합성(compose)만 한다 — 실제 로직은 전부 api/*.py에 있다.

예전의 채점 파이프라인(수집 → 파서 → 규칙 → 점수 → 이력, engine/grading.py·pipeline/·
rule_engine/)은 IMPROVEMENT_PLAN 의 결정 D1 에 따라 퇴역했다 — UI 진입점이 없었고 수집 결과가
파서에 전달되지 않아 점수가 장비 상태와 무관했다. 점검은 '세션 점검 → 로그분석 → 정기점검
보고서' 경로 하나다.

무인 정기점검(IMPROVEMENT_PLAN 4-3): `python main.py --scheduled <일정 id> [--data-root <폴더>]`
(EXE 는 `AutoCheck.exe --scheduled …`)는 창을 띄우지 않고 수집 → 로그 분석 → 보고서를 한 번 돌린 뒤
종료 코드로 끝난다 — Windows 작업 스케줄러가 이렇게 부른다(engine/task_scheduler.py).
"""
import os
import sys


def _option(argv, name):
    """argv 에서 `name 값` 의 값(없으면 None)."""
    if name in argv:
        index = argv.index(name)
        if index + 1 < len(argv):
            return argv[index + 1]
    return None


# --data-root: 무인 점검은 등록한 앱과 같은 데이터 폴더를 쓴다. 몇몇 모듈이 import 되는 순간 경로를 고정하므로
# (engine/project_manager.LABS_DIR 등) 앱 모듈을 하나라도 import 하기 전에 정한다.
if _option(sys.argv, "--data-root"):
    os.environ["AUTOCHECK_HOME"] = _option(sys.argv, "--data-root")

from core.requirements_check import check_python_version, ensure_requirements

# 실행 환경 점검(Python 버전 → 필수 패키지) — webview 및 API 모듈 import 전에 해야 한다.
# 빠진 패키지가 있으면 설치 명령을 안내하고 멈춘다(자세한 규칙은 core/requirements_check.py).
check_python_version()
ensure_requirements(os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt"))

import webview


from api.base import BaseApiMixin
from api.project_api import ProjectApiMixin
from api.dashboard_api import DashboardApiMixin
from api.inspection_report_api import InspectionReportApiMixin
from api.catalog_api import CatalogApiMixin
from api.inventory_api import InventoryApiMixin
from api.knowledge_api import KnowledgeApiMixin
from api.settings_api import SettingsApiMixin
from api.terminal_api import TerminalApiMixin
from api.log_viewer_api import LogViewerApiMixin
from api.masking_api import MaskingApiMixin
from api.logs_api import LogsApiMixin
from api.topology_api import TopologyApiMixin
from api.workspace_api import WorkspaceApiMixin
from api.schedule_api import ScheduleApiMixin
from api.host_key_api import HostKeyApiMixin
from api.round_compare_api import RoundCompareApiMixin
from api.rule_lab_api import RuleLabApiMixin
from api.job_center_api import JobCenterApiMixin
from api.api_logging import log_api_exceptions
from api.window_ref import set_window
from core.app_logger import install_logging_bridge, install_print_capture, log_event, prune_old_logs
from core.paths import AppPaths
from engine.migration_manager import migrate_if_needed


@log_api_exceptions
class Api(BaseApiMixin, ProjectApiMixin, DashboardApiMixin, InspectionReportApiMixin,
          CatalogApiMixin, InventoryApiMixin,
          KnowledgeApiMixin, SettingsApiMixin,
          TerminalApiMixin, LogViewerApiMixin, MaskingApiMixin, LogsApiMixin,
          TopologyApiMixin, WorkspaceApiMixin, ScheduleApiMixin, HostKeyApiMixin,
          RoundCompareApiMixin, RuleLabApiMixin, JobCenterApiMixin):
    """pywebview에 노출되는 최종 API — 실제 로직은 전부 api/*.py의 mixin에 있음."""
    pass


def _run_startup_migration():
    """레거시 데이터(labs/history/config_snapshots/raw_logs/config) -> data/<customer>/<profile>/
    1회 이전. 실패해도 앱 시작을 막지 않는다 — 로그만 남기고 계속 진행."""
    try:
        result = migrate_if_needed()
        upgrades = result.get("upgrades") or {}
        if upgrades.get("applied") or upgrades.get("error"):
            log_event(f"설정·데이터 업그레이드: 적용 {upgrades.get('applied')}, "
                      f"현재 {upgrades.get('upgrade_version')}, 오류 {upgrades.get('error')}", source="migration")
        if not result.get("skipped"):
            log_event(
                f"레거시 데이터 마이그레이션 완료 (성공={result.get('success')}, "
                f"이전됨={len(result.get('migrated_files', []))}, "
                f"건너뜀={len(result.get('skipped_files', []))}, "
                f"실패={len(result.get('failed_files', []))})",
                source="migration",
            )
    except Exception as exc:
        log_event(f"마이그레이션 훅 실행 중 오류(무시하고 계속 진행): {exc}", source="migration")
    try:
        from engine.run_manager import run_manager
        recovered = run_manager.recover_all_incomplete_runs()
        if recovered:
            log_event(f"이전 실행이 도중에 끝난 run {len(recovered)}개를 '중단'으로 정리했습니다: "
                      + ", ".join(f"{r.get('customer')}/{r.get('profile')}/{r.get('run_id')}" for r in recovered[:5]),
                      source="startup")
    except Exception as exc:
        log_event(f"중단된 run 정리 실패(무시하고 계속 진행): {exc}", source="startup")


def run_scheduled(schedule_id):
    """작업 스케줄러가 부르는 무인 정기점검 — 창을 띄우지 않고 한 번 돌린 뒤 종료 코드를 돌려준다
    (0 성공, 2 일부 실패, 1 실패, 3 건너뜀 — 작업 스케줄러의 '마지막 실행 결과'에 보인다). 레거시 이전·업그레이드는
    하지 않는다(앱과 동시에 돌면 같은 파일을 함께 고칠 수 있다) — 남아 있으면 실패로 기록하고 끝난다.
    활성 프로파일(화면 상태)은 바꾸지 않는다."""
    install_print_capture()
    install_logging_bridge()
    log_event(f"무인 정기점검 시작 (일정 {schedule_id})", source="scheduled")
    from engine.scheduled_inspection import exit_code, run_scheduled_inspection
    return exit_code(run_scheduled_inspection(schedule_id, trigger="scheduler"))


def run_self_test(report_path, modules_path=None):
    """릴리스 점검(IMPROVEMENT_PLAN 5-2 — tools/release_check)이 빌드한 EXE 를 시험할 때 부른다. 창을 띄우지 않고
    modules_path 의 모듈(줄마다 하나, '?' 로 시작하면 없어도 앱이 도는 선택 패키지)을 하나씩 import 해 본 결과를
    report_path 에 JSON 으로 쓴다. spec 의 excludes(unittest 등)가 의존 패키지를 깨지 않는지, 번들에 빠진 모듈이 없는지를
    번들 안에서 본다 — 창을 띄우는 것만으로는 함수 안에서 늦게 import 하는 모듈(수집·보고서·AI)까지 닿지 않는다.
    데이터 폴더는 부르는 쪽이 AUTOCHECK_HOME 으로 정한다. 종료 코드: 0 = 전부 됨, 1 = 실패가 있다(또는 목록이 비었다)."""
    import importlib
    import json
    names = []
    if modules_path:
        with open(modules_path, encoding="utf-8") as f:
            names = [line.strip() for line in f if line.strip()]
    failures, missing_optional = {}, []
    for entry in names:
        name = entry.lstrip("?")
        try:
            importlib.import_module(name)
        except (Exception, SystemExit) as exc:
            if entry.startswith("?"):
                missing_optional.append(name)
            else:
                failures[name] = f"{type(exc).__name__}: {exc}"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({"frozen": bool(getattr(sys, "frozen", False)), "checked": len(names), "failures": failures,
                   "missing_optional": missing_optional}, f, ensure_ascii=False, indent=1)
    return 1 if failures or not names else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    report_path = _option(argv, "--self-test")
    if report_path:
        return run_self_test(report_path, _option(argv, "--modules"))
    schedule_id = _option(argv, "--scheduled")
    if schedule_id:
        return run_scheduled(schedule_id)

    install_print_capture()
    install_logging_bridge()
    try:
        removed = prune_old_logs()
        if removed:
            log_event(f"오래된 로그 {removed}개 정리(30일 지난 것, 종류별 최근 20개는 남김)", source="startup")
    except Exception as exc:
        log_event(f"오래된 로그 정리 실패(무시하고 계속): {exc}", source="startup", level="WARNING")
    log_event("AutoCheck 시작", source="startup")
    _run_startup_migration()

    bundle_ui = AppPaths.bundle_root() / "web_ui" / "index.html"
    app_ui = AppPaths.app_root() / "web_ui" / "index.html"
    web_ui_path = str(bundle_ui if bundle_ui.exists() else app_ui)
    api = Api()
    window = webview.create_window("AutoCheck", web_ui_path, js_api=api,
                                    width=1280, height=800, min_size=(1000, 640))
    set_window(window)
    # 창을 닫는 순간이 실시간 감시 상태를 남길 마지막 기회다 — 10초로 묶여 아직 쓰지 않은 변화를 쓴다.
    window.events.closing += lambda: _flush_realtime_on_close(api)
    webview.start(_on_window_ready, api, debug=False)


def _auto_backup():
    """하루 한 번 작업공간 자동 백업(IMPROVEMENT_PLAN 4-10 — engine/workspace_backup.py). 창이 뜬 뒤
    _on_window_ready(별도 스레드)에서 돌아 창이 뜨는 것을 늦추지 않고, 실패해도 앱은 계속 쓴다."""
    try:
        from engine import workspace_backup
        made = workspace_backup.auto_backup_if_due()
        if made:
            log_event(f"작업공간 자동 백업: {made['name']} (파일 {made['files']}개)", source="startup")
    except Exception as exc:
        log_event(f"작업공간 자동 백업 실패(무시하고 계속): {exc}", source="startup", level="WARNING")


def _flush_realtime_on_close(api):
    try:
        api._flush_realtime_state()
    except Exception as exc:
        log_event(f"종료 시 실시간 감시 상태 저장 실패: {exc}", source="shutdown")


def _on_window_ready(api):
    """창이 뜬 뒤 pywebview가 별도 스레드에서 호출 — 시작 시 자동 실행할 백그라운드 작업들.

    '자동 실시간 감시' 체크박스(연결 탭)가 켜져 있으면 사용자가 아무것도 누르지 않아도
    CRTlog 감시가 돌아야 한다. 여기서 바로 시작하지 않고 잠깐 기다리는 이유는, 감시가
    첫 경고를 evaluate_js로 push할 때 web_ui의 핸들러(window.onRealtimeDiffAlert)가
    이미 등록돼 있어야 알림이 버려지지 않기 때문이다."""
    import time
    time.sleep(2.0)
    try:
        result = api._autostart_realtime_baseline_watch()
        if result.get("started"):
            log_event("자동 실시간 감시 시작", source="startup")
    except Exception as exc:
        log_event(f"자동 실시간 감시 시작 실패(무시하고 계속): {exc}", source="startup")
    _auto_backup()


if __name__ == "__main__":
    sys.exit(main())
