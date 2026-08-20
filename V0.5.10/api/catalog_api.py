"""CatalogApiMixin — Command Catalog(체크 활성화/추가/삭제)만 담당."""


class CatalogApiMixin:
    def get_catalog(self):
        try:
            paths = self._paths()
        except RuntimeError:
            return []
        return self._load_catalog(paths)

    def add_catalog_command(self, command, description):
        paths = self._paths()
        catalog = self._load_catalog(paths)
        from engine import command_catalog as cc
        cc.add_command(catalog, command, description)
        cc.save_catalog(catalog, paths["commands_catalog"])
        return True

    def remove_catalog_command(self, command_id):
        paths = self._paths()
        catalog = self._load_catalog(paths)
        from engine import command_catalog as cc
        cc.remove_command(catalog, command_id)
        cc.save_catalog(catalog, paths["commands_catalog"])
        return True

    def update_catalog_command(self, command_id, command, description):
        paths = self._paths()
        catalog = self._load_catalog(paths)
        for item in catalog:
            if item['id'] == command_id:
                item['command'] = command
                item['description'] = description
                break
        from engine import command_catalog as cc
        cc.save_catalog(catalog, paths['commands_catalog'])
        return True

    def move_catalog_items(self, item_ids, target_index=None):
        """item_ids(1개 이상, 다중 선택 드래그 지원)를 통합 목록의 target_index
        위치로 이동. 항상 디스크의 최신 카탈로그를 기준으로 계산하므로 클라이언트가
        들고 있던 목록이 오래되어도(다른 탭에서 수정 등) 항목이 사라지지 않는다."""
        paths = self._paths()
        catalog = self._load_catalog(paths)
        from engine import command_catalog as cc
        cc.move_items(catalog, item_ids, target_index)
        cc.save_catalog(catalog, paths['commands_catalog'])
        return True

    def set_catalog_category(self, command_id, category):
        """드래그 없이 배지(select)로 필수/선택사항/커스텀 라벨만 변경 — 목록 내 위치는 유지."""
        paths = self._paths()
        catalog = self._load_catalog(paths)
        from engine import command_catalog as cc
        cc.set_category(catalog, command_id, category)
        cc.save_catalog(catalog, paths['commands_catalog'])
        return True

    def save_catalog_toggles(self, toggles):
        paths = self._paths()
        catalog = self._load_catalog(paths)
        from engine import command_catalog as cc
        for item in catalog:
            if item["id"] in toggles:
                item["enabled"] = toggles[item["id"]]
        cc.save_catalog(catalog, paths["commands_catalog"])
        return True

    def save_catalog_full(self, new_catalog):
        """프론트엔드에서 보낸 100% 완전한 카탈로그 배열(순서/텍스트/카테고리/토글 포함)을 통째로 덮어쓴다."""
        paths = self._paths()
        from engine import command_catalog as cc
        cc.save_catalog(new_catalog, paths["commands_catalog"])
        return True

    def reset_catalog_defaults(self):
        """커스텀 추가분을 포함해 전체 카탈로그를 최초 기본값(필수/선택)으로 되돌림."""
        paths = self._paths()
        from engine import command_catalog as cc
        catalog = cc._make_default_catalog()
        cc.save_catalog(catalog, paths["commands_catalog"])
        return True

    def export_catalog_excel(self):
        """파일 저장 다이얼로그로 현재 카탈로그를 엑셀로 내보냄."""
        import webview
        from api.window_ref import get_window
        try:
            paths = self._paths()
        except RuntimeError:
            return {"error": "활성 프로젝트가 없습니다."}
        from engine import log_storage
        customer_name, profile_name = self.resolve_active_customer_profile_names()
        default_dir = log_storage.get_cmd_catalog_dir(customer_name, profile_name)
        window = get_window()
        result = window.create_file_dialog(
            webview.SAVE_DIALOG, directory=default_dir, save_filename="commands_catalog.xlsx",
            file_types=("Excel files (*.xlsx)",),
        )
        if not result:
            return None
        dst = result if isinstance(result, str) else result[0]
        if not dst.lower().endswith(".xlsx"):
            dst += ".xlsx"
        catalog = self._load_catalog(paths)
        from engine import command_catalog as cc
        try:
            cc.export_to_excel(catalog, dst)
        except RuntimeError as exc:
            return {"error": str(exc)}
        return {"path": dst}

    def import_catalog_excel(self):
        """파일 선택 다이얼로그로 엑셀을 읽어 카탈로그 전체를 대체함."""
        import webview
        from api.window_ref import get_window
        try:
            paths = self._paths()
        except RuntimeError:
            return {"error": "활성 프로젝트가 없습니다."}
        from engine import log_storage
        customer_name, profile_name = self.resolve_active_customer_profile_names()
        default_dir = log_storage.get_cmd_catalog_dir(customer_name, profile_name)
        window = get_window()
        result = window.create_file_dialog(
            webview.OPEN_DIALOG, directory=default_dir, file_types=("Excel files (*.xlsx)",),
        )
        if not result:
            return None
        src = result if isinstance(result, str) else result[0]
        from engine import command_catalog as cc
        try:
            catalog = cc.import_from_excel(src)
        except RuntimeError as exc:
            return {"error": str(exc)}
        if not catalog:
            return {"error": "엑셀에서 읽은 커맨드가 없습니다."}
        cc.save_catalog(catalog, paths["commands_catalog"])
        return {"count": len(catalog)}
