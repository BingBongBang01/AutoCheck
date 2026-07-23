"""InventoryApiMixin — Device Inventory(장비별 SSH 접속 IP/계정 등) 단일 관리 지점."""
import os


class InventoryApiMixin:
    def get_devices(self):
        try:
            paths = self._paths()
        except RuntimeError:
            return []
        inv = self._load_inventory(paths)
        return inv["devices"]

    def save_devices(self, devices):
        from engine import device_inventory as di
        try:
            paths = self._paths()
        except RuntimeError:
            return False
        inv = self._load_inventory(paths)
        inv["devices"] = [di._normalize_device(d) for d in devices if d.get("name")]
        di.save_inventory(inv, paths["device_inventory"])
        return True

    def get_inventory_defaults(self):
        try:
            paths = self._paths()
        except RuntimeError:
            return {}
        inv = self._load_inventory(paths)
        return inv["defaults"]

    def save_inventory_defaults(self, defaults):
        from engine import device_inventory as di
        try:
            paths = self._paths()
        except RuntimeError:
            return False
        inv = self._load_inventory(paths)
        inv["defaults"].update(defaults)
        di.save_inventory(inv, paths["device_inventory"])
        return True

    def auto_allocate_ips(self, prefix, start, end):
        from engine import device_inventory as di
        try:
            paths = self._paths()
        except RuntimeError:
            return {"error": "활성 프로젝트가 없습니다."}
        try:
            start_i, end_i = int(start), int(end)
        except (TypeError, ValueError):
            return {"error": "시작/끝 번호는 숫자로 입력하세요."}
        if not prefix or start_i > end_i:
            return {"error": "시작 대역과 번호 범위를 확인하세요."}
        inv = self._load_inventory(paths)
        inv["defaults"]["ip_pool"] = {"prefix": prefix, "start": start_i, "end": end_i}
        allocated = di.auto_allocate_ips(inv, prefix, start_i, end_i)
        di.save_inventory(inv, paths["device_inventory"])
        return allocated

    def import_devices(self, overwrite=False):
        """파일 선택 다이얼로그로 CSV/YAML/JSON/Excel import."""
        import webview
        from api.window_ref import get_window
        window = get_window()
        result = window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("Device files (*.csv;*.yaml;*.yml;*.json;*.xlsx)", "All files (*.*)"),
        )
        if not result:
            return None
        path = result[0]

        from engine import device_inventory as di
        ext = os.path.splitext(path)[1].lower()
        importers = {".csv": di.import_csv, ".yaml": di.import_yaml, ".yml": di.import_yaml,
                     ".json": di.import_json, ".xlsx": di.import_excel}
        importer = importers.get(ext)
        if not importer:
            return {"error": f"지원 안 하는 형식: {ext}"}

        imported = importer(path)
        paths = self._paths()
        inv = self._load_inventory(paths)
        result = di.merge_imported(inv, imported, overwrite=overwrite)
        di.save_inventory(inv, paths["device_inventory"])
        return result

    def pick_device_key_file(self):
        """장비별 SSH 접속용 개인키(identity) 파일 선택 다이얼로그 — 값 자체는 안 건드리고 경로만 반환."""
        import webview
        from api.window_ref import get_window
        window = get_window()
        result = window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("SSH key files (*.*)", "All files (*.*)"),
        )
        if not result:
            return None
        return result if isinstance(result, str) else result[0]

    def read_key_file(self):
        path = self.pick_device_key_file()
        if not path:
            return None
        if path.lower().endswith('.pub'):
            return {'error': 'id_ed25519.pub는 공개키 파일입니다. 개인키인 id_ed25519 파일을 선택하세요.'}
        try:
            with open(path, encoding='utf-8') as f:
                return {'path': path, 'content': f.read()}
        except (OSError, UnicodeDecodeError) as exc:
            return {'error': f'키 파일을 읽을 수 없습니다: {exc}'}

    def check_reachability(self):
        """전체 장비 일괄 도달가능성 체크(Dashboard용)."""
        from engine import device_inventory as di
        try:
            paths = self._paths()
        except RuntimeError:
            return {}
        inv = self._load_inventory(paths)
        return di.check_reachability(inv["devices"], inv["defaults"], timeout=2)

    def check_device_reachability(self, device_name):
        """장비 하나만 개별 테스트(Device Inventory 테이블의 '연결 테스트' 버튼용)."""
        from engine import device_inventory as di
        try:
            paths = self._paths()
        except RuntimeError:
            return {"reachable": False, "detail": "활성 프로젝트 없음"}
        inv = self._load_inventory(paths)
        device = next((d for d in inv["devices"] if d["name"] == device_name), None)
        if not device:
            return {"reachable": False, "detail": "장비를 찾을 수 없음"}
        ip, port, _, _ = di.resolve_credentials(device, inv["defaults"])
        if not ip:
            return {"reachable": False, "detail": "IP 미설정"}
        result = di.check_reachability([device], inv["defaults"], timeout=3)
        reachable = result.get(device_name, False)
        return {"reachable": reachable, "detail": f"{ip}:{port}" + (" 연결됨" if reachable else " 연결 실패")}

    def register_discovered_devices(self, node_names):
        """Discovery(.unl)에서 찾은 노드명을 Device Inventory에 등록 (IP는 비워둔 채, 비활성 상태로)."""
        from engine import device_inventory as di
        try:
            paths = self._paths()
        except RuntimeError:
            return 0
        inv = self._load_inventory(paths)
        existing = {d["name"] for d in inv["devices"]}
        added = 0
        for name in node_names:
            if name not in existing:
                di.add_device(inv, {"name": name, "enabled": False})
                added += 1
        di.save_inventory(inv, paths["device_inventory"])
        return added
