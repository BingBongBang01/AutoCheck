"""
BaseApiMixin — 모든 API 조각(mixin)이 공유하는 공통 헬퍼.
main.py의 Api 클래스가 프로젝트/대시보드/디스커버리/리포트/카탈로그/인벤토리 등
6개 이상의 관심사를 한 클래스에 다 담고 있던 것(SRP 위반)을 여기서부터 분리한다.
"""
from engine import project_manager as pm
from engine import command_catalog as cc


class BaseApiMixin:
    def _project(self):
        """현재 활성 프로젝트 id. 없으면 명확한 예외."""
        project_id = pm.get_active_project()
        if not project_id:
            raise RuntimeError("활성 프로젝트가 없습니다.")
        return project_id

    def _paths(self):
        """현재 프로젝트의 경로 dict — project_paths() 반복 호출 제거."""
        return pm.project_paths(self._project())

    def _load_catalog(self, paths):
        return cc.load_catalog(paths["commands_catalog"])

    def _load_inventory(self, paths):
        from engine import device_inventory as di
        return di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])

    def log_client_error(self, message, source="", line=0):
        """화면(JS)에서 난 예외를 앱 로그에 남긴다(web_ui/js/core.js 의 error·unhandledrejection 처리기).
        예전엔 개발자 도구 콘솔에만 남아서, '버튼이 동작하지 않는다'는 보고를 로그로 되짚을 수 없었다."""
        from core.app_logger import log_event
        text = str(message or "")[:2000]
        log_event(f"[화면 오류] {source}:{line} {text}", source="ui", level="ERROR")
        return True

    def get_app_version(self):
        """VERSION 파일에서 앱 버전을 읽어 반환. 파일이 없으면 'unknown'(core/version.py)."""
        from core.version import app_version
        return app_version()
