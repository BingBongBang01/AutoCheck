"""CatalogApiMixin — 커맨드 카탈로그를 메모장처럼 고친다(한 줄에 명령 하나, 명령 다음 줄에 '!').

카탈로그 명령은 점검 때 장비에 그대로 입력되므로 읽기 전용 정책(core/command_policy.py)을 지킨다: 장비 설정을 바꿀 수 있는
명령이 한 줄이라도 있으면 저장하지 않고 그 줄을 알린다. 내보내기·불러오기는 같은 모양의 .txt 이고, 파일은 정기점검의
commands 폴더에 둔다('폴더 열기'). 예전 화면이 내보낸 Excel 도 불러온다.
"""


class CatalogApiMixin:
    def _catalog_dir(self):
        from engine import log_storage
        customer_name, profile_name = self._resolve_active_customer_profile_names()
        return log_storage.get_cmd_catalog_dir(customer_name, profile_name)

    def get_catalog_text(self):
        """편집기에 보일 글 — {"text", "count", "hidden": [{command, reason}], "defaults": [{command, description}]}.
        hidden: 메모장에 보이지 않는 예전 항목(꺼 둔 것·정책에 걸리는 것 — 점검에서 실행되지 않던 것). 다음에 저장하면 빠진다.
        defaults: 기본 카탈로그 명령 — 화면이 지금 글에 없는 것을 골라 눌러서 더하게 보여 준다."""
        from engine import command_catalog as cc
        try:
            paths = self._paths()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다.", "text": "", "count": 0, "hidden": [], "defaults": []}
        catalog = self._load_catalog(paths)
        commands = cc.shown_commands(catalog)
        return {"text": cc.commands_to_text(commands), "count": len(commands),
                "hidden": [{"command": command, "reason": reason} for command, reason in cc.hidden_items(catalog)],
                "defaults": cc.default_commands()}

    def save_catalog_text(self, text):
        """편집기 글을 카탈로그로 저장 — 반환: {"ok", "count", "text"(줄을 정리한 글), "warnings"} 또는
        {"error"[, "blocked"]}(저장하지 않음). blocked·warnings 의 줄은 {line, command, reason}."""
        from engine import command_catalog as cc
        try:
            paths = self._paths()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}
        commands, blocked, warnings = cc.check_catalog_text(text)
        if blocked:
            return {"error": "장비 설정을 바꿀 수 있는 명령이 있어 저장하지 않았습니다.", "blocked": blocked}
        if not commands:
            return {"error": "명령이 없어 저장하지 않았습니다 — 한 줄에 명령 하나씩 적으세요."}
        cc.save_catalog(cc.catalog_from_commands(commands, self._load_catalog(paths)), paths["commands_catalog"])
        return {"ok": True, "count": len(commands), "text": cc.commands_to_text(commands), "warnings": warnings}

    def reset_catalog_defaults(self):
        """카탈로그 전체를 기본 카탈로그(엑셀)로 되돌린다 — 반환: {"ok", "text", "count"}."""
        from engine import command_catalog as cc
        try:
            paths = self._paths()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}
        catalog = cc.default_catalog()
        cc.save_catalog(catalog, paths["commands_catalog"])
        commands = cc.shown_commands(catalog)
        return {"ok": True, "text": cc.commands_to_text(commands), "count": len(commands)}

    def export_catalog_text(self, text):
        """편집기 글(줄을 정리해서)을 .txt 로 내보낸다 — 저장 창은 정기점검의 commands 폴더에서 열린다.
        Windows 메모장에서 그대로 열리게 줄바꿈은 CRLF. 반환: {"path"} / {"error"} / None(취소)."""
        import webview
        from api.window_ref import get_window
        from engine import command_catalog as cc
        try:
            self._paths()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}
        commands = [command for _number, command in cc.parse_catalog_text(text)]
        if not commands:
            return {"error": "내보낼 명령이 없습니다."}
        result = get_window().create_file_dialog(
            webview.SAVE_DIALOG, directory=self._catalog_dir(), save_filename="commands_catalog.txt",
            file_types=("Text files (*.txt)",),
        )
        if not result:
            return None
        dst = result if isinstance(result, str) else result[0]
        if not dst.lower().endswith(".txt"):
            dst += ".txt"
        with open(dst, "w", encoding="utf-8", newline="") as f:
            f.write(cc.commands_to_text(commands, newline="\r\n"))
        return {"path": dst, "count": len(commands)}

    def import_catalog_text(self):
        """.txt(또는 예전 화면이 내보낸 .xlsx)를 골라 그 명령을 편집기 글로 돌려준다 — 저장은 화면이 이어서 한다
        (save_catalog_text — 정책 검사·저장이 한 곳). 반환: {"text", "count", "path"} / {"error"} / None(취소)."""
        import webview
        from api.window_ref import get_window
        from engine import command_catalog as cc
        try:
            self._paths()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}
        result = get_window().create_file_dialog(
            webview.OPEN_DIALOG, directory=self._catalog_dir(),
            file_types=("Text files (*.txt)", "Excel files (*.xlsx)"),
        )
        if not result:
            return None
        src = result if isinstance(result, str) else result[0]
        try:
            commands = cc.read_catalog_file(src)
        except (OSError, ValueError, RuntimeError) as exc:
            return {"error": f"불러오지 못했습니다 — {exc}"}
        if not commands:
            return {"error": "파일에서 읽은 명령이 없습니다."}
        return {"text": cc.commands_to_text(commands), "count": len(commands), "path": src}

    def open_catalog_folder(self):
        """내보내기·불러오기가 쓰는 정기점검의 commands 폴더를 탐색기로 연다."""
        from engine.log_storage import open_in_file_explorer
        try:
            self._paths()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}
        folder = self._catalog_dir()
        open_in_file_explorer(folder)
        return {"ok": True, "path": folder}
