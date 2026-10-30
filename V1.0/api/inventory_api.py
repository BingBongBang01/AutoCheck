"""InventoryApiMixin — Device Inventory(장비별 SSH 접속 IP/계정 등) 단일 관리 지점.

비밀값(장비 비밀번호·enable 비밀번호·개인키·passphrase, 기본 비밀번호·기본 enable 비밀번호)은 화면으로 보내지 않는다 — 저장 여부(has_*)만 보낸다.
화면이 보낸 값은 '새로 입력한 칸만' 바꾸고, 나머지는 장비 id 로 저장된 비밀값을 잇는다(IMPROVEMENT_PLAN 4-1,
engine/device_inventory_core 의 public_device·merge_screen_devices). 예전엔 get_devices 가 비밀번호·개인키 평문을
돌려주고 화면이 그 값을 input value 로 DOM 에 넣었다.
"""
import os
import threading

# 장비 목록 파일을 읽고 고쳐 쓰는 구간 — 세션 점검은 세션마다 스레드에서 장비 이름을 바꾼다(_rename_devices). 잠그지 않으면
# 두 스레드가 같은 파일을 읽어 각자 고쳐 쓰면서 먼저 쓴 쪽의 변경이 사라진다(화면의 자동 저장 save_devices 도 같다).
_INVENTORY_WRITE_LOCK = threading.Lock()


class InventoryApiMixin:
    def get_devices(self):
        from engine import device_inventory as di
        try:
            paths = self._paths()
        except RuntimeError:
            return []
        inv = self._load_inventory(paths)
        return [di.public_device(d) for d in inv["devices"]]

    def save_devices(self, devices):
        """화면의 장비 목록 저장. 비밀 칸은 새로 입력한 값만 바꾸고(clear_secrets 로 지움), 나머지는 기존 값을 잇는다."""
        from engine import device_inventory as di
        try:
            paths = self._paths()
        except RuntimeError:
            return False
        with _INVENTORY_WRITE_LOCK:
            inv = self._load_inventory(paths)
            inv["devices"] = di.merge_screen_devices(devices or [], inv["devices"])
            di.save_inventory(inv, paths["device_inventory"])
        return True

    def get_inventory_defaults(self):
        from engine import device_inventory as di
        try:
            paths = self._paths()
        except RuntimeError:
            return {}
        inv = self._load_inventory(paths)
        return di.public_defaults(inv["defaults"])

    def save_inventory_defaults(self, defaults):
        """기본값 저장 — default_password 는 새로 입력했을 때만 바꾸고, clear_secrets 에 넣으면 지운다."""
        from engine import device_inventory as di
        try:
            paths = self._paths()
        except RuntimeError:
            return False
        inv = self._load_inventory(paths)
        inv["defaults"] = dict(inv["defaults"], **di.apply_secret_changes(defaults or {}, inv["defaults"],
                                                                           di.DEFAULTS_SECRET_FIELDS))
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
        from engine import log_storage
        customer_name, profile_name = self._resolve_active_customer_profile_names()
        default_dir = log_storage.get_device_list_dir(customer_name, profile_name)
        window = get_window()
        result = window.create_file_dialog(
            webview.OPEN_DIALOG, directory=default_dir,
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

    def export_devices(self, include_credentials=False):
        """파일 저장 다이얼로그로 현재 장비목록을 엑셀로 내보냄. 비밀번호·enable 비밀번호 열은 기본으로 비운다 —
        include_credentials=True 일 때만 평문으로 넣고 결과에 warning 을 붙인다(화면이 묻고 경고한다)."""
        import webview
        from api.window_ref import get_window
        from engine import log_storage
        try:
            paths = self._paths()
        except RuntimeError:
            return {"error": "활성 프로젝트가 없습니다."}
        customer_name, profile_name = self._resolve_active_customer_profile_names()
        default_dir = log_storage.get_device_list_dir(customer_name, profile_name)
        window = get_window()
        result = window.create_file_dialog(
            webview.SAVE_DIALOG, directory=default_dir, save_filename="device_inventory.xlsx",
            file_types=("Excel files (*.xlsx)",),
        )
        if not result:
            return None
        dst = result if isinstance(result, str) else result[0]
        if not dst.lower().endswith(".xlsx"):
            dst += ".xlsx"
        inv = self._load_inventory(paths)
        from engine import device_inventory as di
        try:
            di.export_to_excel(inv["devices"], dst, include_credentials=bool(include_credentials))
        except RuntimeError as exc:
            return {"error": str(exc)}
        if include_credentials:
            return {"path": dst, "warning": "장비 비밀번호·enable 비밀번호가 엑셀에 평문으로 들어갔습니다 — 전달·보관에 주의하세요."}
        return {"path": dst, "notice": "비밀번호·enable 비밀번호는 비워서 내보냈습니다(개인키도 담지 않습니다)."}

    def _pick_device_key_file(self):
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
        path = self._pick_device_key_file()
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

    # ---------- 자동 연결 확인(Probe) ----------
    # 장비 목록에서 IP/계정을 추가·수정할 때마다 UI가 호출한다. check_reachability()가
    # 소켓 포트만 두드리는 것과 달리 실제 SSH 인증까지 하고 장비의 hostname을 받아온다.
    # 저장된 인벤토리가 아니라 **화면에서 편집 중인 값**을 그대로 받는 게 핵심 — 그래야
    # 사용자가 저장을 누르기 전에도 방금 고친 IP로 확인해 볼 수 있다(자동저장 여부와 무관).

    def probe_device_config(self, device):
        """편집 중인 장비 dict 하나를 그대로 받아 SSH 접속 확인 + hostname 조회.
        화면에는 비밀값이 없다 — 새로 입력하지 않은 비밀 칸은 같은 id 의 저장된 값으로 확인한다."""
        from engine import device_inventory as di
        from engine import device_probe
        try:
            paths = self._paths()
        except RuntimeError:
            return {"name": (device or {}).get("name", ""), "reachable": False, "authenticated": False,
                    "hostname": None, "detail": "활성 프로젝트 없음"}
        from engine import host_keys
        inv = self._load_inventory(paths)
        return device_probe.probe_device(di.with_stored_secrets(device or {}, inv["devices"]), inv["defaults"],
                                         host_key_scope=host_keys.scope_for_project(self._project()))

    def probe_devices_config(self, devices):
        """여러 장비를 병렬 확인 — 불러오기/IP 자동생성 직후, '전체 연결 확인' 버튼용."""
        from engine import device_inventory as di
        from engine import device_probe
        try:
            paths = self._paths()
        except RuntimeError:
            return []
        from engine import host_keys
        inv = self._load_inventory(paths)
        merged = [di.with_stored_secrets(d or {}, inv["devices"]) for d in (devices or [])]
        return device_probe.probe_devices(merged, inv["defaults"],
                                          host_key_scope=host_keys.scope_for_project(self._project()))

    def _rename_devices(self, renames):
        """장비 이름들을 장비가 알려 준 호스트명으로 바꾼다 — 터미널 접속·세션 점검(api/terminal_session_api)이 부른다.
        renames: [(옛 이름, 호스트명)]. 반환: 같은 순서의 [None(바꿈) 또는 바꾸지 않은 이유].
        장비 목록은 한 번 읽고 한 번 쓴다 — 수십 대를 한꺼번에 접속하면 이름 변경이 한 폴링에 몰리는데, 한 대씩 읽고 쓰면
        대당 0.15초쯤(51대 7초) 터미널 출력이 멈췄다. 규칙(옛 이름은 별칭으로 남김, 대소문자만 다른 이름도 중복)은
        engine/device_inventory_core.rename_device."""
        from engine import device_inventory as di
        try:
            paths = self._paths()
        except RuntimeError:
            return ["활성 프로파일 없음"] * len(renames)
        with _INVENTORY_WRITE_LOCK:
            inv = self._load_inventory(paths)
            problems = [di.rename_device(inv, old_name, new_name) for old_name, new_name in renames]
            if any(problem is None for problem in problems):
                di.save_inventory(inv, paths["device_inventory"])
        return problems

    # ---------- 다른 정기점검 프로파일에서 장비목록 가져오기 ----------
    # 같은 고객사를 반복 점검할 때 장비·IP·계정은 회차가 바뀌어도 대부분 그대로다.
    # 새 프로파일을 만들 때마다 수십 대를 다시 입력하는 게 가장 번거로운 일이라
    # 두 번째 프로파일부터는 직전 회차 것을 그대로 물려받게 한다.

    def list_device_copy_sources(self):
        """
        지금 프로파일을 뺀, 같은 고객사의 다른 프로파일 목록을 최신순으로.
        각 항목에 장비 대수(device_count)를 실어 보내 UI가 "몇 대짜리인지" 바로 보여준다.
        """
        from engine import project_manager as pm
        from engine import device_inventory as di
        try:
            current_id = self._project()
        except RuntimeError:
            return []

        customer = self._customer_of_project(current_id)
        if not customer:
            return []

        sources = []
        for profile in customer["profiles"]:
            if profile["id"] == current_id:
                continue
            paths = pm.project_paths(profile["id"])
            try:
                inv = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
            except Exception:
                continue
            sources.append({
                "id": profile["id"],
                "name": profile.get("profile_name") or profile.get("display_name") or profile["id"],
                "created_at": profile.get("created_at") or "",
                "inspection_date": profile.get("inspection_date") or "",
                "device_count": len(inv.get("devices", [])),
            })
        sources.sort(key=lambda s: s["created_at"], reverse=True)
        return sources

    def _customer_of_project(self, project_id):
        """이 프로젝트가 속한 고객사 노드(profiles 포함)를 트리에서 찾는다."""
        for customer in self.get_customer_profiles():
            if any(p["id"] == project_id for p in customer["profiles"]):
                return customer
        return None

    def copy_devices_from_profile(self, source_project_id, overwrite=False):
        """
        다른 프로파일의 장비목록을 현재 프로파일로 가져온다.
        overwrite=False(기본)면 같은 이름의 장비는 건너뛰므로 지금 데이터가 사라지지 않는다.
        """
        from engine import project_manager as pm
        from engine import device_inventory as di
        try:
            paths = self._paths()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}
        if not source_project_id:
            return {"error": "복사할 원본 프로파일을 선택하세요."}
        if source_project_id == pm.get_active_project():
            return {"error": "같은 프로파일에서는 복사할 수 없습니다."}

        src_paths = pm.project_paths(source_project_id)
        if not os.path.exists(src_paths["device_inventory"]):
            return {"error": "원본 프로파일에 장비목록이 없습니다."}

        src = di.load_inventory(src_paths["device_inventory"], src_paths["lab_meta"], src_paths["ip_allocation"])
        if not src.get("devices"):
            return {"error": "원본 프로파일에 등록된 장비가 없습니다."}

        dst = self._load_inventory(paths)
        result = di.copy_from_inventory(src, dst, overwrite=overwrite)
        di.save_inventory(dst, paths["device_inventory"])
        result["source"] = source_project_id
        return result
