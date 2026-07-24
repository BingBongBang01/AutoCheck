"""
Material Design 3 스타일 웹 UI(web_ui/)를 pywebview로 띄우는 진입점.
Api 클래스가 프로젝트/대시보드/디스커버리/채점/보고서/Findings/이력/카탈로그/
인벤토리/연결설정 10개 관심사를 한 클래스에 다 담고 있던 것(SRP 위반)을
api/ 패키지의 mixin들로 분리하고 여기서는 합성(compose)만 한다 — 동작은 완전히 동일.

실행: python gui_web.py
필요: pip install pywebview
Windows는 시스템 내장 Edge WebView2를 자동으로 사용함(추가 설치 보통 불필요).
"""
import os
import webview

from api.base import BaseApiMixin
from api.project_api import ProjectApiMixin
from api.dashboard_api import DashboardApiMixin
from api.discovery_api import DiscoveryApiMixin
from api.grade_api import GradeApiMixin
from api.report_api import ReportApiMixin
from api.misc_api import FindingsApiMixin, HistoryApiMixin, ArchitectureApiMixin
from api.catalog_api import CatalogApiMixin
from api.inventory_api import InventoryApiMixin
from api.connection_api import ConnectionApiMixin
from api.analysis_api import AnalysisApiMixin
from api.inspection_api import InspectionApiMixin
from api.knowledge_api import KnowledgeApiMixin
from api.settings_api import SettingsApiMixin
from api.terminal_api import TerminalApiMixin
from api.log_viewer_api import LogViewerApiMixin
from api.masking_api import MaskingApiMixin
from api.logs_api import LogsApiMixin
from api.workspace_api import WorkspaceApiMixin
from api.window_ref import set_window
from core.app_logger import install_print_capture, log_event
from engine.migration_manager import migrate_if_needed


class Api(BaseApiMixin, ProjectApiMixin, DashboardApiMixin, DiscoveryApiMixin, GradeApiMixin,
          ReportApiMixin, FindingsApiMixin, HistoryApiMixin, ArchitectureApiMixin,
          CatalogApiMixin, InventoryApiMixin, ConnectionApiMixin,
          AnalysisApiMixin, InspectionApiMixin, KnowledgeApiMixin, SettingsApiMixin,
          TerminalApiMixin, LogViewerApiMixin, MaskingApiMixin, LogsApiMixin, WorkspaceApiMixin):
    """pywebview에 노출되는 최종 API — 실제 로직은 전부 api/*.py의 mixin에 있음."""
    pass


if __name__ == "__main__":
    install_print_capture()
    log_event("AutoCheck 시작", source="startup")
    try:
        migration_result = migrate_if_needed()
        if not migration_result.get("skipped"):
            log_event(
                f"레거시 데이터 마이그레이션 완료 (성공={migration_result.get('success')}, "
                f"이전됨={len(migration_result.get('migrated_files', []))}, "
                f"건너뜀={len(migration_result.get('skipped_files', []))}, "
                f"실패={len(migration_result.get('failed_files', []))})",
                source="migration",
            )
    except Exception as exc:
        log_event(f"마이그레이션 훅 실행 중 오류(무시하고 계속 진행): {exc}", source="migration")
    api = Api()
    web_ui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_ui", "index.html")
    window = webview.create_window("AutoCheck", web_ui_path, js_api=api, width=1280, height=800, min_size=(1000, 640))
    set_window(window)
    webview.start(debug=False)
