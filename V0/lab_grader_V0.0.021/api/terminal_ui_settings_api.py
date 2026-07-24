"""TerminalUiSettingsApiMixin — 터미널 우클릭 동작(메뉴/바로 붙여넣기) 설정."""
import os


class TerminalUiSettingsApiMixin:
    TERMINAL_UI_PATH = "config/terminal_ui.yaml"

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
        with open(self.TERMINAL_UI_PATH, "w", encoding="utf-8") as f:
            yaml.dump({"context_menu_mode": context_menu_mode}, f, allow_unicode=True, sort_keys=False)
        return {"ok": True}
