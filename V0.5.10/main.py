"""
AutoCheck 진입점 — Material Design 3 웹 UI(web_ui/)를 pywebview로 띄운다.

실행: python main.py
필요: pip install -r requirements.txt  (pywebview 포함)
Windows는 시스템 내장 Edge WebView2를 자동으로 사용함(추가 설치 보통 불필요).

Api 클래스는 프로젝트/대시보드/디스커버리/채점/보고서/Findings/이력/카탈로그/
인벤토리/연결설정 등 관심사별 mixin(api/ 패키지)을 합성(compose)만 한다 —
실제 로직은 전부 api/*.py에 있다.

채점 로직은 engine/grading.py에 있고, 웹 UI(api/grade_api.py)와
정기점검 스케줄러(engine/scheduler.py)가 공유한다.
"""
import os
import sys
import subprocess
import re
import importlib.metadata


def ensure_requirements():
    """main.py 실행 시 requirements.txt에 명시된 패키지 중 미설치 항목이 있으면 자동 설치."""
    if getattr(sys, "frozen", False):
        return

    base_dir = os.path.dirname(os.path.abspath(__file__))
    req_path = os.path.join(base_dir, "requirements.txt")
    if not os.path.exists(req_path):
        return

    missing = []
    try:
        with open(req_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                line = line.split("#")[0].strip()
                if not line:
                    continue
                pkg_name = re.split(r"[<>=!~;\[\s]", line)[0].strip()
                if pkg_name:
                    try:
                        importlib.metadata.version(pkg_name)
                    except Exception:
                        missing.append(pkg_name)
    except Exception as e:
        print(f"[!] requirements.txt 검사 중 오류(무시하고 진행): {e}")
        return

    if missing:
        print(f"[안내] 미설치 라이브러리가 탐지되었습니다: {', '.join(missing)}")
        print("[안내] requirements.txt 패키지 자동 설치를 진행합니다...")
        try:
            cmd = [sys.executable, "-m", "pip", "install", "-r", req_path]
            res = subprocess.run(cmd)
            if res.returncode == 0:
                print("[OK] 필요 라이브러리 자동 설치 완료!")
            else:
                print(f"[!] pip install 반환 코드: {res.returncode}. 실행을 시도합니다.")
        except Exception as exc:
            print(f"[!] 자동 설치 중 오류 발생: {exc}")


# 필수 패키지 자동 확인 및 설치 수행 (webview 및 API 모듈 import 전)
ensure_requirements()

import webview


from api.base import BaseApiMixin
from api.project_api import ProjectApiMixin
from api.dashboard_api import DashboardApiMixin
from api.grade_api import GradeApiMixin
from api.report_api import ReportApiMixin
from api.inspection_report_api import InspectionReportApiMixin
from api.catalog_api import CatalogApiMixin
from api.inventory_api import InventoryApiMixin
from api.connection_api import ConnectionApiMixin
from api.knowledge_api import KnowledgeApiMixin
from api.settings_api import SettingsApiMixin
from api.terminal_api import TerminalApiMixin
from api.log_viewer_api import LogViewerApiMixin
from api.masking_api import MaskingApiMixin
from api.logs_api import LogsApiMixin
from api.topology_api import TopologyApiMixin
from api.workspace_api import WorkspaceApiMixin
from api.window_ref import set_window
from core.app_logger import install_print_capture, log_event
from core.paths import AppPaths
from engine.migration_manager import migrate_if_needed


class Api(BaseApiMixin, ProjectApiMixin, DashboardApiMixin, GradeApiMixin,
          ReportApiMixin, InspectionReportApiMixin,
          CatalogApiMixin, InventoryApiMixin, ConnectionApiMixin,
          KnowledgeApiMixin, SettingsApiMixin,
          TerminalApiMixin, LogViewerApiMixin, MaskingApiMixin, LogsApiMixin,
          TopologyApiMixin, WorkspaceApiMixin):
    """pywebview에 노출되는 최종 API — 실제 로직은 전부 api/*.py의 mixin에 있음."""
    pass


def _run_startup_migration():
    """레거시 데이터(labs/history/config_snapshots/raw_logs/config) -> data/<customer>/<profile>/
    1회 이전. 실패해도 앱 시작을 막지 않는다 — 로그만 남기고 계속 진행."""
    try:
        result = migrate_if_needed()
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


def main():
    install_print_capture()
    log_event("AutoCheck 시작", source="startup")
    _run_startup_migration()

    bundle_ui = AppPaths.bundle_root() / "web_ui" / "index.html"
    app_ui = AppPaths.app_root() / "web_ui" / "index.html"
    web_ui_path = str(bundle_ui if bundle_ui.exists() else app_ui)
    api = Api()
    window = webview.create_window("AutoCheck", web_ui_path, js_api=api,
                                    width=1280, height=800, min_size=(1000, 640))
    set_window(window)
    webview.start(_on_window_ready, api, debug=False)


def _on_window_ready(api):
    """창이 뜬 뒤 pywebview가 별도 스레드에서 호출 — 시작 시 자동 실행할 백그라운드 작업들.

    '자동 실시간 감시' 체크박스(연결 탭)가 켜져 있으면 사용자가 아무것도 누르지 않아도
    CRTlog 감시가 돌아야 한다. 여기서 바로 시작하지 않고 잠깐 기다리는 이유는, 감시가
    첫 경고를 evaluate_js로 push할 때 web_ui의 핸들러(window.onRealtimeDiffAlert)가
    이미 등록돼 있어야 알림이 버려지지 않기 때문이다."""
    import time
    time.sleep(2.0)
    try:
        result = api.autostart_realtime_baseline_watch()
        if result.get("started"):
            log_event("자동 실시간 감시 시작", source="startup")
    except Exception as exc:
        log_event(f"자동 실시간 감시 시작 실패(무시하고 계속): {exc}", source="startup")


if __name__ == "__main__":
    main()
