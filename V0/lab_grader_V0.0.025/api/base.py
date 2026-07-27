"""
BaseApiMixin — 모든 API 조각(mixin)이 공유하는 공통 헬퍼.
gui_web.py의 Api 클래스가 프로젝트/대시보드/디스커버리/리포트/카탈로그/인벤토리 등
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

    def get_app_version(self):
        """VERSION 파일에서 앱 버전을 읽어 반환. 파일이 없으면 'unknown'."""
        import os
        version_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "VERSION")
        try:
            with open(version_path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return "unknown"
