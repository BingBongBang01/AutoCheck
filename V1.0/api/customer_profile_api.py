"""CustomerProfileApiMixin — 고객사/정기점검 프로파일 트리 관리(생성/이름변경/삭제/컨텍스트 전환).

각 정기점검 프로파일은 project_api.py의 ProjectCoreApiMixin이 다루는 프로젝트(=labs/<id>)를
그대로 사용하며, 이 클래스는 그 위에 고객사 단위 그룹핑을 얹는다. 항상 ProjectCoreApiMixin과
함께(ProjectApiMixin에서) 조합되어야 한다 — pm(engine.project_manager)과 self.get_customer_profiles()
등을 서로 참조한다(가져온 프로젝트를 트리에 잇는 _link_imported_project 도 여기 있다).
"""
import os
import datetime
from engine import project_manager as pm
from engine.profile_manager import profile_manager as prm
from core.paths import safe_child, validate_name
from core.atomic_io import dump_yaml_atomic
from core.context_cache import workspace_cache
from core.utils import format_iso


def _same_folder(a, b):
    """두 이름이 같은 data 폴더를 가리키는지. Windows 파일시스템은 대소문자를 구분하지 않아서,
    정확히 같은 문자열만 막으면 'ABC'·'abc' 두 프로파일이 한 폴더(로그·보고서·스냅샷)를 함께 쓴다."""
    return (a or "").casefold() == (b or "").casefold()


def _read_meta(meta_path):
    import yaml
    with open(meta_path, encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def _latest_profile(profiles):
    """
    가장 최근에 만든 프로파일. created_at은 초 단위 ISO 문자열이라 문자열 정렬이 곧 시간순이고,
    날짜만 있던 예전 값('2026-07-27')과 섞여도 순서가 성립한다.
    created_at이 아예 없는 프로파일은 가장 오래된 것으로 취급한다.
    """
    if not profiles:
        return None
    return max(profiles, key=lambda p: p.get("created_at") or "")


def _copy_devices_between_projects(src_project_id, dst_project_id):
    """프로젝트 간 장비목록 복사 — 새 프로파일은 비어 있으므로 사실상 통째로 물려받는다."""
    from engine import device_inventory as di

    src_paths = pm.project_paths(src_project_id)
    if not os.path.exists(src_paths["device_inventory"]):
        return None
    src = di.load_inventory(src_paths["device_inventory"], src_paths["lab_meta"], src_paths["ip_allocation"])
    if not src.get("devices"):
        return None

    dst_paths = pm.project_paths(dst_project_id)
    dst = di.load_inventory(dst_paths["device_inventory"], dst_paths["lab_meta"], dst_paths["ip_allocation"])
    result = di.copy_from_inventory(src, dst)
    di.save_inventory(dst, dst_paths["device_inventory"])
    return result


class CustomerProfileApiMixin:
    def get_customer_profiles(self):
        return workspace_cache.get_customer_tree(self._fetch_customer_profiles)

    def _fetch_customer_profiles(self):
        """고객사 → 정기점검 프로파일 트리. 읽기 경로라 필요한 것만 쓴다.

        예전엔 캐시가 빗나갈 때마다 모든 project_meta.yaml 과 customers.yaml 을 다시 썼다(쓰기 조건이
        항상 참이었다) — 병렬 호출이 이름변경과 겹치면 옛 메타로 덮어쓸 수 있었다. 이제 고객사 id 가
        실제로 바뀌거나 profile_name 이 비어 있을 때만 메타를, 고객사가 새로 생겼을 때만 목록을 쓴다.
        메타 하나가 깨져도 트리 전체가 실패하지 않고 그 프로파일만 빠진다(로그에 남긴다)."""
        from engine import customer_manager as cm
        customers = cm.load()
        customer_count = len(customers)
        projects = {item["id"]: item for item in pm.list_projects()}
        metas = {}
        for project in projects.values():
            meta_path = os.path.join(pm.LABS_DIR, project["id"], "project_meta.yaml")
            try:
                meta = _read_meta(meta_path)
            except Exception as exc:
                print(f"[고객사 트리] 메타를 읽을 수 없어 건너뜀({project['id']}): {exc}")
                continue
            customer_name = meta.get("customer_name") or meta.get("display_name") or "미분류 고객사"
            customer = cm.ensure_customer(customers, customer_name)
            profile_name = meta.get("profile_name") or meta.get("display_name", project["id"])
            if meta.get("customer_id") != customer["id"] or meta.get("profile_name") != profile_name:
                meta["customer_id"] = customer["id"]
                meta["profile_name"] = profile_name
                dump_yaml_atomic(meta, meta_path)
            metas[project["id"]] = meta
        if len(customers) != customer_count or not os.path.exists(cm.CUSTOMERS_PATH):
            cm.save(customers)
        result = []
        for customer in customers:
            profiles = []
            for project in projects.values():
                meta = metas.get(project["id"])
                if meta is None:
                    continue
                if meta.get("customer_id") == customer["id"] or meta.get("customer_name") == customer["name"]:
                    profile_name = meta.get("profile_name", project["display_name"])
                    # '가상환경' 여부의 단일 출처는 data/<고객사>/<프로파일>/profile/profile.json 이다
                    # (보고서·AI 분석도 그 파일을 읽는다) — labs/ 쪽 메타에 복제하지 않는다.
                    profiles.append({**project, "profile_name": profile_name,
                                      "description": meta.get("description", ""),
                                      "inspection_date": meta.get("inspection_date", ""),
                                      "status": meta.get("status", "준비"),
                                      "is_virtual": prm.is_virtual(customer["name"], profile_name)})
            result.append({**customer, "profiles": profiles})
        return result

    def create_customer(self, name):
        name = name.strip()
        try:
            name = validate_name(name)
        except ValueError as e:
            return {"error": str(e)}
        tree = self.get_customer_profiles()
        if any(_same_folder(item["name"], name) for item in tree):
            return {"error": "동일한 고객사가 이미 존재합니다."}
        from engine import customer_manager as cm
        customers = cm.load()
        cm.ensure_customer(customers, name)
        cm.save(customers)
        # 고객사를 만든 즉시 data/<고객사>/ 폴더도 만들어 둔다 — 예전엔 첫 정기점검 프로파일을
        # 추가할 때까지 폴더가 아예 없어서, 보고서/로그 저장 위치를 미리 열어볼 수 없었다.
        # (프로파일 아래 reports/ 등 서브폴더는 prm.create_profile()이 만든다.)
        prm.customer_dir(name).mkdir(parents=True, exist_ok=True)
        workspace_cache.invalidate()
        return {"ok": True}

    def rename_customer(self, customer_id, name):
        name = name.strip()
        try:
            name = validate_name(name)
        except ValueError as e:
            return {"error": str(e)}
        from engine import customer_manager as cm
        customers = cm.load()
        target = next((item for item in customers if item["id"] == customer_id), None)
        if not target:
            return {"error": "고객사를 찾을 수 없습니다."}
        if any(_same_folder(item["name"], name) and item["id"] != customer_id for item in customers):
            return {"error": "동일한 고객사가 이미 존재합니다."}
        old_name = target["name"]
        if old_name == name:
            return {"ok": True}
        profiles = next((item["profiles"] for item in self.get_customer_profiles() if item["id"] == customer_id), [])
        # 순서가 곧 안전장치다: data 폴더를 먼저 옮기고(실패하면 아무것도 바뀌지 않은 채 끝), 그다음
        # 메타·고객사 목록을 고친다. 뒤 단계가 실패하면 앞 단계를 되돌린다. 예전엔 메타를 먼저 고친 뒤
        # 폴더 이동이 실패해도 그대로 반환해서, 메타와 폴더가 서로 다른 이름을 가리켰다.
        moving_active = any(p["id"] == pm.get_active_project() for p in profiles)
        moved = False
        if prm.customer_dir(old_name).exists():
            if not _same_folder(old_name, name) and prm.customer_dir(name).exists():
                return {"error": f"'{name}' 이름의 고객사 data 폴더가 이미 있습니다 — 다른 이름을 쓰거나 그 폴더를 먼저 정리하세요."}
            if moving_active:
                self._active_profile_folder_moving(before=True)
            try:
                prm.rename_customer(old_name, name)
            except (OSError, ValueError) as e:
                if moving_active:
                    self._active_profile_folder_moving(before=False)
                return {"error": f"고객사 폴더 이름을 바꾸지 못했습니다: {e}"}
            moved = True
        originals = {}
        try:
            for profile in profiles:
                meta_path = os.path.join(pm.LABS_DIR, profile["id"], "project_meta.yaml")
                meta = _read_meta(meta_path)
                originals[meta_path] = dict(meta)
                meta["customer_name"] = name
                dump_yaml_atomic(meta, meta_path)
            target["name"] = name
            target["updated_at"] = format_iso()
            cm.save(customers)
        except Exception:
            for meta_path, meta in originals.items():
                dump_yaml_atomic(meta, meta_path)
            if moved:
                prm.rename_customer(name, old_name)
            raise
        finally:
            workspace_cache.invalidate()
            if moved and moving_active:
                self._active_profile_folder_moving(before=False)
        return {"ok": True}

    def delete_customer(self, customer_id):
        """고객사를 지운다 = 모든 정기점검 프로파일을 보관하고 고객사 목록에서 뺀다(engine/profile_archive.py).
        예전엔 고객사 폴더를 통째로 rmtree 해서 보관본(_archived_profiles)까지 함께 사라졌다.
        보관본은 data/<고객사>/_archived_profiles/ 에 남고 복원·영구 삭제할 수 있다."""
        from engine import customer_manager as cm
        from engine import profile_archive
        customers = cm.load()
        target = next((item for item in customers if item["id"] == customer_id), None)
        if not target:
            return {"error": "고객사를 찾을 수 없습니다."}
        archived = []
        try:
            for profile in next((item["profiles"] for item in self.get_customer_profiles() if item["id"] == customer_id), []):
                archived.append(str(profile_archive.archive_profile(
                    profile["id"], target["name"], profile["profile_name"], customer_id)))
        except (OSError, ValueError) as e:
            self._activated_profile(pm.get_active_project())
            return {"error": f"보관하지 못한 프로파일이 있어 고객사는 그대로 둡니다: {e}", "archived": archived}
        cm.save([item for item in customers if item["id"] != customer_id])
        # 활성 프로파일이 이 고객사에 있었으면 archive_profile()이 활성 상태를 해제했다.
        self._activated_profile(pm.get_active_project())
        return {"ok": True, "archived": archived}

    def create_inspection_profile(self, customer_id, name, description="", inspection_date="",
                                   is_virtual=False):
        name = name.strip()
        try:
            name = validate_name(name)
        except ValueError as e:
            return {"error": str(e)}
        tree = self.get_customer_profiles()
        customer = next((item for item in tree if item["id"] == customer_id), None)
        if not customer:
            return {"error": "고객사를 찾을 수 없습니다."}
        if any(_same_folder(item["profile_name"], name) for item in customer["profiles"]):
            return {"error": "동일한 정기점검 프로파일이 이미 존재합니다."}
        # 새 프로파일을 만들기 "전"의 형제 목록 — 여기서 직전 회차를 골라 장비목록을 물려받는다.
        previous = _latest_profile(customer["profiles"])
        try:
            project_id, reused = self._create_profile_records(
                customer, name, description=description, inspection_date=inspection_date, is_virtual=is_virtual)
        except (OSError, ValueError) as e:
            return {"error": f"정기점검 프로파일을 만들지 못했습니다: {e}"}
        pm.set_active_project(project_id)

        # 이 고객사의 두 번째 프로파일부터는 직전(최신) 회차의 장비목록을 그대로 물려받는다.
        # 같은 고객사면 장비·IP·계정이 회차가 바뀌어도 대부분 그대로라 매번 다시 입력할 이유가 없다.
        # 복사에 실패해도 프로파일 생성 자체는 성공시킨다 — 장비목록은 나중에 버튼으로 다시 가져올 수 있다.
        copied = None
        if previous:
            try:
                copied = _copy_devices_between_projects(previous["id"], project_id)
            except Exception as exc:
                print(f"[안내] 직전 프로파일 장비목록 복사 실패(무시하고 진행): {exc}")

        # 새 프로파일이 곧 활성 프로파일이다(위 pm.set_active_project) — 캐시 무효화만으로는
        # 부족하고, 실시간 감시도 새(비어 있는) 프로파일 상태로 갈아끼워야 한다.
        self._activated_profile(project_id)
        return {"ok": True, "id": project_id, "reused_data": reused,
                "copied_from": (previous or {}).get("profile_name") if copied else None,
                "copied_count": (copied or {}).get("added", 0)}

    def _create_profile_records(self, customer, name, description="", inspection_date="", is_virtual=False):
        """정기점검 프로파일 하나 = labs/<id> 프로젝트 + data/<고객사>/<프로파일> 폴더. 둘을 함께 만들고,
        중간에 실패하면 이번에 만든 것을 지운 뒤 예외를 그대로 올린다 — 반쯤 만들어진 프로파일
        (labs 쪽만 있거나, 활성인데 data 폴더가 없는 것)을 남기지 않는다. 활성화는 호출부가 한다.

        data 폴더가 이미 있으면(어느 프로파일도 쓰지 않는 이전 기록 — 같은 고객사 안의 이름 겹침은
        호출부가 먼저 거른다) 이어서 쓴다. 반환: (project_id, data 폴더를 이어서 쓰는지)"""
        data_dir_existed = prm.profile_dir(customer["name"], name).exists()
        project_id = pm.create_project(name)
        try:
            meta_path = os.path.join(pm.LABS_DIR, project_id, "project_meta.yaml")
            meta = _read_meta(meta_path)
            meta.update({"customer_id": customer["id"], "customer_name": customer["name"], "profile_name": name,
                         "description": description, "inspection_date": inspection_date, "status": "준비"})
            dump_yaml_atomic(meta, meta_path)
            try:
                prm.create_profile(customer["name"], name, description=description,
                                    inspection_date=inspection_date, is_virtual=bool(is_virtual))
                reused = False
            except FileExistsError:
                # 이번에 지정한 가상환경 여부는 이어 쓰는 폴더에도 반영해야 한다.
                prm.set_virtual(customer["name"], name, bool(is_virtual))
                reused = True
        except Exception:
            pm.delete_project(project_id)
            if not data_dir_existed:
                prm.delete_profile(customer["name"], name)
            raise
        return project_id, reused

    def rename_inspection_profile(self, profile_id, name, description="", inspection_date="",
                                   is_virtual=None):
        name = name.strip()
        try:
            name = validate_name(name)
        except ValueError as e:
            return {"error": str(e)}
        try:
            meta_path = str(safe_child(pm.LABS_DIR, profile_id) / "project_meta.yaml")
        except ValueError:
            return {"error": "정기점검 프로파일을 찾을 수 없습니다."}
        if not os.path.exists(meta_path):
            return {"error": "정기점검 프로파일을 찾을 수 없습니다."}
        meta = _read_meta(meta_path)
        old_name = meta.get("profile_name") or meta.get("display_name") or profile_id
        customer_name = meta.get("customer_name")
        # 같은 고객사의 다른 프로파일과 이름이 겹치면(대소문자만 달라도) 두 회차가 한 data 폴더를 쓰게 된다.
        siblings = [p for c in self.get_customer_profiles()
                    if c["id"] == meta.get("customer_id") or c["name"] == customer_name
                    for p in c["profiles"] if p["id"] != profile_id]
        if any(_same_folder(p["profile_name"], name) for p in siblings):
            return {"error": "동일한 정기점검 프로파일이 이미 존재합니다."}

        # data 폴더를 먼저 옮기고(실패하면 아무것도 바뀌지 않은 채 끝), 그다음 메타를 저장한다.
        # 메타 저장이 실패하면 폴더를 되돌린다. 예전엔 메타를 먼저 저장한 뒤 폴더 이동이 실패해도
        # 그대로 반환해서, 이 회차가 같은 이름을 가진 다른 회차의 data 폴더를 함께 썼다.
        moving_active = profile_id == pm.get_active_project()
        moved = False
        if customer_name and old_name != name and prm.profile_dir(customer_name, old_name).exists():
            if not _same_folder(old_name, name) and prm.profile_dir(customer_name, name).exists():
                return {"error": f"'{name}' 이름의 data 폴더가 이미 있습니다(이전 기록) — 다른 이름을 쓰거나 그 폴더를 먼저 정리하세요."}
            if moving_active:
                self._active_profile_folder_moving(before=True)
            try:
                prm.rename_profile(customer_name, old_name, name)   # 대소문자만 다르면 폴더는 그대로, 메타 이름만
            except (OSError, ValueError) as e:
                if moving_active:
                    self._active_profile_folder_moving(before=False)
                return {"error": f"프로파일 폴더 이름을 바꾸지 못했습니다: {e}"}
            moved = True
        try:
            meta.update({"display_name": name, "profile_name": name, "description": description,
                         "inspection_date": inspection_date,
                         "updated_at": datetime.datetime.now().isoformat(timespec="seconds")})
            dump_yaml_atomic(meta, meta_path)
        except Exception:
            if moved:
                prm.rename_profile(customer_name, name, old_name)
            raise
        finally:
            workspace_cache.invalidate()
            if moved and moving_active:
                self._active_profile_folder_moving(before=False)
        # is_virtual 을 생략하면(None) 기존 값을 그대로 둔다 — 이름만 고치는 호출이 가상환경
        # 설정을 조용히 끄지 않도록.
        if is_virtual is not None and customer_name:
            prm.set_virtual(customer_name, name, bool(is_virtual))
        return {"ok": True}

    def _active_profile_folder_moving(self, before):
        """활성 프로파일의 data 폴더가 옮겨지기 직전(before=True)·직후(False). 실시간 감시가 그 사이에
        옛 경로로 상태를 저장하면 옛 이름의 폴더가 되살아난다(engine/realtime_watch_service.py 의 notify_profile_moving)."""
        hook = getattr(self, "_notify_active_profile_moving", None)
        if hook is None:
            return
        try:
            hook(before)
        except Exception as exc:
            print(f"[프로파일 이름변경] 실시간 감시 상태 이동 실패: {exc}")

    def set_inspection_profile_virtual(self, profile_id, is_virtual):
        """프로파일의 '가상환경' 옵션을 켜고 끈다.

        켜면 (1) 보고서에서 PSU/FAN/온도/모듈/트랜시버 항목이 로그와 무관하게 '해당없음'이 되고,
        (2) AI 로그 분석이 가상 플랫폼용 프롬프트(하드웨어 미지원 출력 무시)를 쓴다.
        vEOS-lab/EVE-NG 같은 실습·검증망 회차에서 매번 같은 오탐을 손으로 걸러내지 않기 위한 옵션."""
        for customer in self.get_customer_profiles():
            profile = next((p for p in customer["profiles"] if p["id"] == profile_id), None)
            if not profile:
                continue
            prm.set_virtual(customer["name"], profile["profile_name"], bool(is_virtual))
            workspace_cache.invalidate()
            return {"ok": True, "is_virtual": bool(is_virtual)}
        return {"error": "정기점검 프로파일을 찾을 수 없습니다."}

    def delete_inspection_profile(self, profile_id):
        """정기점검 프로파일을 지운다 = 보관한다(되돌릴 수 있다). 영구 삭제는 보관본에만 한다
        (purge_archived_profile). 예전엔 labs·data 두 곳을 바로 rmtree 했다."""
        return self._archive_profile_by_id(profile_id)

    def _archive_profile_by_id(self, profile_id):
        from engine import profile_archive
        owner = self._profile_owner(profile_id)
        customer, profile = owner if owner else ({}, {})
        try:
            path = profile_archive.archive_profile(profile_id, customer.get("name"), profile.get("profile_name"),
                                                   customer.get("id"))
        except (OSError, ValueError) as e:
            return {"error": f"보관하지 못했습니다: {e}"}
        # 활성 프로파일이었으면 archive_profile()이 활성 상태를 해제했다.
        self._activated_profile(pm.get_active_project())
        return {"ok": True, "archived": str(path)}

    def _profile_owner(self, profile_id):
        """(고객사, 프로파일) 트리 항목. 없으면 None."""
        for customer in self.get_customer_profiles():
            profile = next((p for p in customer["profiles"] if p["id"] == profile_id), None)
            if profile:
                return customer, profile
        return None

    # ---- 보관함(목록·복원·영구 삭제) — 화면은 web_ui/js/core-archive-modal.js(4-10) ----------------------

    def list_archived_profiles(self, customer_name=None):
        from engine import profile_archive
        return profile_archive.list_archives(customer_name)

    def restore_archived_profile(self, archive_name, customer_name=None):
        from engine import profile_archive
        try:
            project_id = profile_archive.restore_profile(profile_archive.find_archive(archive_name, customer_name))
        except (OSError, ValueError) as e:
            return {"error": str(e)}
        workspace_cache.invalidate()
        return {"ok": True, "id": project_id}

    def purge_archived_profile(self, archive_name, customer_name=None):
        """보관본 하나를 영구 삭제한다 — 되돌릴 수 없다."""
        from engine import profile_archive
        try:
            profile_archive.purge_archive(profile_archive.find_archive(archive_name, customer_name))
        except (OSError, ValueError) as e:
            return {"error": str(e)}
        return {"ok": True}

    def get_customer_context(self):
        project_id = pm.get_active_project()
        tree = self.get_customer_profiles()
        for customer in tree:
            for profile in customer['profiles']:
                if profile['id'] == project_id:
                    # UI 초기 표시에 필요한 profile_name, customer_id 등을 모두 포함하여 반환
                    return {
                        'customer': customer['name'],
                        'customer_id': customer['id'],
                        'profile': profile['id'],
                        'profile_name': profile.get('profile_name', profile.get('display_name', profile['id']))
                    }
        # 매칭되지 않았을 때의 기본 반환값도 구조를 맞춰줌
        return {
            'customer': '',
            'customer_id': '',
            'profile': project_id,
            'profile_name': project_id
        }

    def _resolve_active_customer_profile_names(self):
        """현재 활성 프로젝트(=정기점검 프로파일)의 (고객사명, 프로파일명)을 반환.
        data/<고객사>/<프로파일>/ 로그 저장소 경로 계산에 쓰인다. 캐싱 적용됨."""
        context = workspace_cache.get_active_context(self._uncached_resolve_active_names)
        return context["customer_name"], context["profile_name"]

    def _uncached_resolve_active_names(self):
        project_id = pm.get_active_project()
        tree = self.get_customer_profiles()
        for customer in tree:
            for profile in customer["profiles"]:
                if profile["id"] == project_id:
                    name = profile.get("profile_name") or profile.get("display_name") or project_id
                    return {"customer_name": customer["name"], "profile_name": name}
        return {"customer_name": "미분류", "profile_name": project_id or "미지정"}

    def _link_imported_project(self, project_id):
        """가져온 프로젝트(project_core_api.import_project)를 이 PC 의 고객사 트리에 잇는다(IMPROVEMENT_PLAN 5-3·D5).
        고객사는 이름으로 찾고 없으면 만든다(트리를 만들 때와 같은 규칙 — 대소문자 무시). 같은 고객사에 같은 이름(대소문자
        무시)의 정기점검이 있거나 그 이름의 data 폴더가 이미 있으면 이름 뒤에 '(가져옴)'을 붙인다 — 두 프로파일이 data
        폴더 하나를 함께 쓰면 점검 기록·보고서가 섞인다. data/<고객사>/<프로파일> 을 만들고 트리 캐시를 비운다.
        반환: {customer, profile_name, renamed}. 이름을 폴더 이름으로 쓸 수 없으면 ValueError."""
        from engine import customer_manager as cm
        meta_path = os.path.join(pm.LABS_DIR, project_id, "project_meta.yaml")
        meta = _read_meta(meta_path) if os.path.exists(meta_path) else {}
        if not isinstance(meta, dict):
            meta = {}
        customer_name = validate_name(meta.get("customer_name") or meta.get("display_name") or "미분류 고객사")
        wanted = validate_name(meta.get("profile_name") or meta.get("display_name") or project_id)
        customers = cm.load()
        record = cm.ensure_customer(customers, customer_name)
        cm.save(customers)
        workspace_cache.invalidate()
        taken = {p["profile_name"].casefold() for c in self.get_customer_profiles() if c["id"] == record["id"]
                 for p in c["profiles"] if p["id"] != project_id}
        name, n = wanted, 1
        while name.casefold() in taken or prm.profile_dir(record["name"], name).exists():
            n += 1
            name = f"{wanted} (가져옴)" if n == 2 else f"{wanted} (가져옴 {n - 1})"
        meta.update({"customer_id": record["id"], "customer_name": record["name"], "profile_name": name})
        dump_yaml_atomic(meta, meta_path)
        prm.create_profile(record["name"], name, description=meta.get("description", ""),
                           inspection_date=meta.get("inspection_date", ""))
        workspace_cache.invalidate()
        return {"customer": record["name"], "profile_name": name, "renamed": name != wanted}
