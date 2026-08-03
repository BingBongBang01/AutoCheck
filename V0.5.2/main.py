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
from api.workspace_api import WorkspaceApiMixin
from api.window_ref import set_window
from core.app_logger import install_print_capture, log_event
from core.paths import AppPaths
from engine.migration_manager import migrate_if_needed


class Api(BaseApiMixin, ProjectApiMixin, DashboardApiMixin, GradeApiMixin,
          ReportApiMixin, InspectionReportApiMixin,
          CatalogApiMixin, InventoryApiMixin, ConnectionApiMixin,
          KnowledgeApiMixin, SettingsApiMixin,
          TerminalApiMixin, LogViewerApiMixin, MaskingApiMixin, LogsApiMixin, WorkspaceApiMixin):
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
    window = webview.create_window("AutoCheck", web_ui_path, js_api=Api(),
                                    width=1280, height=800, min_size=(1000, 640))
    set_window(window)
    webview.start(debug=False)


if __name__ == "__main__":
    main()
