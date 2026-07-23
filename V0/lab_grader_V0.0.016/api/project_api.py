"""
ProjectApiMixin — 프로젝트(=고객사/점검 프로파일) 자체의 생성/삭제/이름변경/선택/내보내기/불러오기 담당.
각 프로젝트는 labs/<id> 폴더 하나로 완전히 독립(장비목록/커맨드카탈로그/점검기준/이력 등 전부 별도) —
"고객사별 프로파일"과 "점검 프로파일" 요구사항을 하나의 프로젝트 개념으로 통합해서 충족한다.
"""
import os
import datetime
from engine import project_manager as pm


class ProjectApiMixin:
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
        if not name:
            return {"error": "고객사 이름을 입력하세요."}
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
        from engine import customer_manager as cm
        customers = cm.load()
        target = next((item for item in customers if item["id"] == customer_id), None)
        if not target:
            return {"error": "고객사를 찾을 수 없습니다."}
        if any(item["name"] == name and item["id"] != customer_id for item in customers):
            return {"error": "동일한 고객사가 이미 존재합니다."}
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
        cm.save([item for item in customers if item["id"] != customer_id])
        return {"ok": True}

    def create_inspection_profile(self, customer_id, name, description="", inspection_date=""):
        name = name.strip()
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
        return {"ok": True, "id": project_id}

    def rename_inspection_profile(self, profile_id, name, description="", inspection_date=""):
        name = name.strip()
        meta_path = os.path.join(pm.LABS_DIR, profile_id, "project_meta.yaml")
        if not os.path.exists(meta_path):
            return {"error": "정기점검 프로파일을 찾을 수 없습니다."}
        import yaml
        with open(meta_path, encoding="utf-8") as stream:
            meta = yaml.safe_load(stream) or {}
        meta.update({"display_name": name, "profile_name": name, "description": description, "inspection_date": inspection_date, "updated_at": datetime.datetime.now().isoformat(timespec="seconds")})
        with open(meta_path, "w", encoding="utf-8") as stream:
            yaml.dump(meta, stream, allow_unicode=True, sort_keys=False)
        return {"ok": True}

    def delete_inspection_profile(self, profile_id):
        pm.delete_project(profile_id)
        return {"ok": True}
    def get_customer_context(self):
        project_id = pm.get_active_project()
        tree = self.list_customer_tree()
        for customer in tree:
            for profile in customer['profiles']:
                if profile['id'] == project_id:
                    return {'customer': customer['name'], 'profile': profile['id']}
        return {'customer': '', 'profile': project_id}

    def set_customer_context(self, customer_name, profile_id):
        tree = self.list_customer_tree()
        valid = next((p for c in tree if c['name'] == customer_name for p in c['profiles'] if p['id'] == profile_id), None)
        if not valid:
            return False
        pm.set_active_project(profile_id)
        return True
    def list_customer_tree(self):
        return self.get_customer_profiles()

    def create_customer_profile(self, customer_name, profile_name):
        project_id = pm.create_project(profile_name)
        meta_path = os.path.join(pm.LABS_DIR, project_id, 'project_meta.yaml')
        import yaml
        with open(meta_path, encoding='utf-8') as f:
            meta = yaml.safe_load(f) or {}
        meta.update({'display_name': profile_name, 'profile_name': profile_name, 'customer_name': customer_name})
        with open(meta_path, 'w', encoding='utf-8') as f:
            yaml.dump(meta, f, allow_unicode=True, sort_keys=False)
        return project_id
    def list_projects(self):
        return pm.list_projects()

    def get_active_project(self):
        return pm.get_active_project()

    def create_project(self, name):
        new_id = pm.create_project(name)
        pm.set_active_project(new_id)
        return new_id

    def set_active_project(self, project_id):
        pm.set_active_project(project_id)
        return True

    def rename_project(self, project_id, new_name):
        pm.rename_project(project_id, new_name)
        return True

    def delete_project(self, project_id):
        pm.delete_project(project_id)
        return True

    def export_project(self, project_id):
        """프로젝트(고객사/점검 프로파일) 폴더 전체를 zip 하나로 내보내기 — 다른 PC로 이전/백업용."""
        import webview
        import zipfile
        from api.window_ref import get_window
        window = get_window()
        result = window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=f"{project_id}.zip",
            file_types=("ZIP files (*.zip)",),
        )
        if not result:
            return None
        dst = result if isinstance(result, str) else result[0]
        src_dir = os.path.join(pm.LABS_DIR, project_id)
        if not os.path.isdir(src_dir):
            return {"error": f"프로젝트 없음: {project_id}"}
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(src_dir):
                for fname in files:
                    full = os.path.join(root, fname)
                    arcname = os.path.relpath(full, pm.LABS_DIR)
                    zf.write(full, arcname)
        return {"ok": True, "path": dst}

    def import_project(self):
        """내보낸 zip을 새 프로젝트(고객사/점검 프로파일)로 불러오기 — 기존 프로젝트와 완전히 독립적으로 추가됨."""
        import webview
        import zipfile
        import datetime
        from api.window_ref import get_window
        window = get_window()
        result = window.create_file_dialog(webview.OPEN_DIALOG, file_types=("ZIP files (*.zip)",))
        if not result:
            return None
        src = result[0]
        try:
            with zipfile.ZipFile(src) as zf:
                names = zf.namelist()
                if not names:
                    return {"error": "빈 zip 파일입니다."}
                original_id = names[0].split("/")[0]
                new_id = original_id
                if os.path.exists(os.path.join(pm.LABS_DIR, new_id)):
                    new_id = f"{original_id}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
                os.makedirs(pm.LABS_DIR, exist_ok=True)
                for member in names:
                    if not member.startswith(original_id + "/") and member != original_id:
                        continue
                    target_rel = new_id + member[len(original_id):]
                    target_path = os.path.join(pm.LABS_DIR, target_rel)
                    if member.endswith("/"):
                        os.makedirs(target_path, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with zf.open(member) as fsrc, open(target_path, "wb") as fdst:
                        fdst.write(fsrc.read())
            return {"ok": True, "project_id": new_id}
        except zipfile.BadZipFile:
            return {"error": "올바른 zip 파일이 아닙니다."}
