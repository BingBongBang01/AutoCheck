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

    def reorder_catalog(self, command_ids):
        paths = self._paths()
        catalog = self._load_catalog(paths)
        by_id = {item['id']: item for item in catalog}
        reordered = [by_id[item_id] for item_id in command_ids if item_id in by_id]
        reordered.extend(item for item in catalog if item['id'] not in command_ids)
        from engine import command_catalog as cc
        cc.save_catalog(reordered, paths['commands_catalog'])
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
