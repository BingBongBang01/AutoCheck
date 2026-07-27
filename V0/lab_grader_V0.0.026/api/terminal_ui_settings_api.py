"""TerminalUiSettingsApiMixin — 터미널 우클릭 동작(메뉴/바로 붙여넣기) 설정."""
import os
from core.atomic_io import dump_yaml_atomic


class TerminalUiSettingsApiMixin:
    @property
    def TERMINAL_UI_PATH(self):
        """앱 전역 설정 — 경로는 AppPaths로 고정(core/app_settings.py 참고)."""
        from core.app_settings import terminal_ui_path
        return terminal_ui_path()


    def get_terminal_ui_settings(self):
        import yaml
        if not os.path.exists(self.TERMINAL_UI_PATH):
            return {"context_menu_mode": "menu"}
        with open(self.TERMINAL_UI_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return {"context_menu_mode": cfg.get("context_menu_mode", "menu")}

    def save_terminal_ui_settings(self, context_menu_mode):
        import yaml
        if context_menu_mode not in ("menu", "paste"):
            return {"error": f"알 수 없는 모드: {context_menu_mode}"}
        os.makedirs(os.path.dirname(self.TERMINAL_UI_PATH), exist_ok=True)
        dump_yaml_atomic({"context_menu_mode": context_menu_mode}, self.TERMINAL_UI_PATH)
        return {"ok": True}
