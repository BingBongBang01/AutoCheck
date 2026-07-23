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

    def reset_catalog_defaults(self):
        """커스텀 추가분을 포함해 전체 카탈로그를 최초 기본값(필수/선택)으로 되돌림."""
        paths = self._paths()
        from engine import command_catalog as cc
        catalog = cc._make_default_catalog()
        cc.save_catalog(catalog, paths["commands_catalog"])
        return True
