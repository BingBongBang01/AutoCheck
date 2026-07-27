"""CustomerProfileApiMixin — 고객사/정기점검 프로파일 트리 관리(생성/이름변경/삭제/컨텍스트 전환).

각 정기점검 프로파일은 project_api.py의 ProjectCoreApiMixin이 다루는 프로젝트(=labs/<id>)를
그대로 사용하며, 이 클래스는 그 위에 고객사 단위 그룹핑을 얹는다. 항상 ProjectCoreApiMixin과
함께(ProjectApiMixin에서) 조합되어야 한다 — pm(engine.project_manager)과 self.list_customer_tree()
등을 서로 참조한다.
"""
import os
import datetime
from engine import project_manager as pm
from engine.profile_manager import profile_manager as prm
from core.paths import validate_name


class CustomerProfileApiMixin:
    def get_customer_profiles(self):
        from engine import customer_manager as cm
        customers = cm.load()
        projects = {item["id"]: item for item in pm.list_projects()}
        changed = False
        for project in projects.values():
            meta_path = os.path.join(pm.LABS_DIR, project["id"], "project_meta.yaml")
            import yaml
            with open(meta_path, encoding="utf-8") as stream:
                meta = yaml.safe_load(stream) or {}
            customer_name = meta.get("customer_name") or meta.get("display_name") or "미분류 고객사"
            customer = cm.ensure_customer(customers, customer_name)
            if project["id"] not in meta.get("customer_profile_ids", []):
                meta["customer_id"] = customer["id"]
                meta["profile_name"] = meta.get("profile_name") or meta.get("display_name", project["id"])
                with open(meta_path, "w", encoding="utf-8") as stream:
                    yaml.dump(meta, stream, allow_unicode=True, sort_keys=False)
                changed = True
        if changed or not os.path.exists(cm.CUSTOMERS_PATH):
            cm.save(customers)
        result = []
        for customer in customers:
            profiles = []
            for project in projects.values():
                meta_path = os.path.join(pm.LABS_DIR, project["id"], "project_meta.yaml")
                with open(meta_path, encoding="utf-8") as stream:
                    meta = yaml.safe_load(stream) or {}
                if meta.get("customer_id") == customer["id"] or meta.get("customer_name") == customer["name"]:
                    profiles.append({**project, "profile_name": meta.get("profile_name", project["display_name"]), "description": meta.get("description", ""), "inspection_date": meta.get("inspection_date", ""), "status": meta.get("status", "준비")})
            result.append({**customer, "profiles": profiles})
        return result

    def create_customer(self, name):
        name = name.strip()
        try:
            name = validate_name(name)
        except ValueError as e:
            return {"error": str(e)}
        tree = self.get_customer_profiles()
        if any(item["name"] == name for item in tree):
            return {"error": "동일한 고객사가 이미 존재합니다."}
        from engine import customer_manager as cm
        customers = cm.load()
        cm.ensure_customer(customers, name)
        cm.save(customers)
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
        if any(item["name"] == name and item["id"] != customer_id for item in customers):
            return {"error": "동일한 고객사가 이미 존재합니다."}
        old_name = target["name"]
        target["name"] = name
        target["updated_at"] = cm._now()
        import yaml
        for profile in next((item["profiles"] for item in self.get_customer_profiles() if item["id"] == customer_id), []):
            meta_path = os.path.join(pm.LABS_DIR, profile["id"], "project_meta.yaml")
            with open(meta_path, encoding="utf-8") as stream:
                meta = yaml.safe_load(stream) or {}
            meta["customer_name"] = name
            with open(meta_path, "w", encoding="utf-8") as stream:
                yaml.dump(meta, stream, allow_unicode=True, sort_keys=False)
        if prm.customer_dir(old_name).exists():
            try:
                prm.rename_customer(old_name, name)
            except FileExistsError as e:
                return {"error": str(e)}
        cm.save(customers)
        return {"ok": True}

    def delete_customer(self, customer_id):
        from engine import customer_manager as cm
        customers = cm.load()
        target = next((item for item in customers if item["id"] == customer_id), None)
        if not target:
            return {"error": "고객사를 찾을 수 없습니다."}
        for profile in next((item["profiles"] for item in self.get_customer_profiles() if item["id"] == customer_id), []):
            pm.delete_project(profile["id"])
        prm.delete_customer(target["name"])
        cm.save([item for item in customers if item["id"] != customer_id])
        return {"ok": True}

    def create_inspection_profile(self, customer_id, name, description="", inspection_date=""):
        name = name.strip()
        try:
            name = validate_name(name)
        except ValueError as e:
            return {"error": str(e)}
        tree = self.get_customer_profiles()
        customer = next((item for item in tree if item["id"] == customer_id), None)
        if not customer:
            return {"error": "고객사를 찾을 수 없습니다."}
        if any(item["profile_name"] == name for item in customer["profiles"]):
            return {"error": "동일한 정기점검 프로파일이 이미 존재합니다."}
        project_id = pm.create_project(name)
        meta_path = os.path.join(pm.LABS_DIR, project_id, "project_meta.yaml")
        import yaml
        with open(meta_path, encoding="utf-8") as stream:
            meta = yaml.safe_load(stream) or {}
        meta.update({"customer_id": customer_id, "customer_name": customer["name"], "profile_name": name, "description": description, "inspection_date": inspection_date, "status": "준비"})
        with open(meta_path, "w", encoding="utf-8") as stream:
            yaml.dump(meta, stream, allow_unicode=True, sort_keys=False)
        pm.set_active_project(project_id)
        try:
            prm.create_profile(customer["name"], name, description=description, inspection_date=inspection_date)
        except FileExistsError:
            pass  # 폴더는 이미 있지만 labs/ 프로젝트가 새로 생겼을 뿐인 경우(재사용) — 무시하고 진행
        return {"ok": True, "id": project_id}

    def rename_inspection_profile(self, profile_id, name, description="", inspection_date=""):
        name = name.strip()
        try:
            name = validate_name(name)
        except ValueError as e:
            return {"error": str(e)}
        meta_path = os.path.join(pm.LABS_DIR, profile_id, "project_meta.yaml")
        if not os.path.exists(meta_path):
            return {"error": "정기점검 프로파일을 찾을 수 없습니다."}
        import yaml
        with open(meta_path, encoding="utf-8") as stream:
            meta = yaml.safe_load(stream) or {}
        old_name = meta.get("profile_name") or meta.get("display_name") or profile_id
        customer_name = meta.get("customer_name")
        meta.update({"display_name": name, "profile_name": name, "description": description, "inspection_date": inspection_date, "updated_at": datetime.datetime.now().isoformat(timespec="seconds")})
        with open(meta_path, "w", encoding="utf-8") as stream:
            yaml.dump(meta, stream, allow_unicode=True, sort_keys=False)
        if customer_name and prm.profile_dir(customer_name, old_name).exists() and old_name != name:
            try:
                prm.rename_profile(customer_name, old_name, name)
            except FileExistsError as e:
                return {"error": str(e)}
        return {"ok": True}

    def delete_inspection_profile(self, profile_id):
        customer_name, profile_name = None, None
        for customer in self.get_customer_profiles():
            profile = next((p for p in customer["profiles"] if p["id"] == profile_id), None)
            if profile:
                customer_name, profile_name = customer["name"], profile["profile_name"]
                break
        pm.delete_project(profile_id)
        if customer_name and profile_name:
            prm.delete_profile(customer_name, profile_name)
        return {"ok": True}

    def get_customer_context(self):
        project_id = pm.get_active_project()
        tree = self.list_customer_tree()
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

    def set_customer_context(self, customer_name, profile_id):
        tree = self.list_customer_tree()
        valid = next((p for c in tree if c['name'] == customer_name for p in c['profiles'] if p['id'] == profile_id), None)
        if not valid:
            return False
        pm.set_active_project(profile_id)
        return True

    def list_customer_tree(self):
        return self.get_customer_profiles()

    def resolve_active_customer_profile_names(self):
        """현재 활성 프로젝트(=정기점검 프로파일)의 (고객사명, 프로파일명)을 반환.
        data/<고객사>/<프로파일>/ 로그 저장소 경로 계산에 쓰인다."""
        project_id = pm.get_active_project()
        tree = self.list_customer_tree()
        for customer in tree:
            for profile in customer["profiles"]:
                if profile["id"] == project_id:
                    name = profile.get("profile_name") or profile.get("display_name") or project_id
                    return customer["name"], name
        return "미분류", project_id or "미지정"

    def create_customer_profile(self, customer_name, profile_name):
        customer_name = validate_name(customer_name)
        profile_name = validate_name(profile_name)
        project_id = pm.create_project(profile_name)
        meta_path = os.path.join(pm.LABS_DIR, project_id, 'project_meta.yaml')
        import yaml
        with open(meta_path, encoding='utf-8') as f:
            meta = yaml.safe_load(f) or {}
        meta.update({'display_name': profile_name, 'profile_name': profile_name, 'customer_name': customer_name})
        with open(meta_path, 'w', encoding='utf-8') as f:
            yaml.dump(meta, f, allow_unicode=True, sort_keys=False)
        try:
            prm.create_profile(customer_name, profile_name)
        except FileExistsError:
            pass
        return project_id
