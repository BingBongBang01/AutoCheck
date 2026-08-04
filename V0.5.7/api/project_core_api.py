"""ProjectCoreApiMixin — 프로젝트(labs/<id>) 자체의 원시 CRUD + zip 내보내기/불러오기.
고객사 단위 그룹핑은 customer_profile_api.py(CustomerProfileApiMixin)가 그 위에 얹는다 —
delete_project 등에서 self.get_customer_profiles()를 호출하므로 항상 함께(ProjectApiMixin에서)
조합되어야 한다.
"""
import os
from engine import project_manager as pm


class ProjectCoreApiMixin:
    def list_projects(self):
        return pm.list_projects()

    def get_active_project(self):
        return pm.get_active_project()

    def get_last_profile_by_customer(self):
        """{고객사 id: 마지막으로 연 프로파일 id} — 고객사/정기점검 팝업이 기본 선택에 쓴다."""
        return pm.get_last_profile_by_customer()

    def ensure_active_profile(self):
        """프로그램 시작 시 '마지막에 쓰던 프로파일'이 반드시 열리도록 활성 상태를 확정한다.

        active_project.yaml에 id가 남아 있어도 그 프로파일 폴더가 지워졌거나(다른 PC에서 복사,
        수동 정리) 값이 비어 있으면 화면은 '프로파일 없음'으로 뜬다. 그때는 고객사별 마지막
        사용 기록 -> 남아 있는 첫 프로파일 순으로 되살린다. 이미 유효하면 아무것도 바꾸지 않는다.
        """
        active = pm.get_active_project()
        existing = {p["id"] for p in pm.list_projects()}
        if active and active in existing:
            return {"ok": True, "project_id": active, "changed": False}

        # 가장 최근에 쓰던 것부터 — 기록이 여러 고객사에 있으면 폴더 수정시각이 가장 늦은 것.
        candidates = [pid for pid in pm.get_last_profile_by_customer().values() if pid in existing]
        candidates.sort(key=lambda pid: os.path.getmtime(os.path.join(pm.LABS_DIR, pid)), reverse=True)
        fallback = next(iter(candidates), None) or next(iter(sorted(existing)), None)
        if not fallback:
            return {"ok": False, "project_id": None, "changed": False,
                    "reason": "등록된 프로파일이 없습니다."}
        pm.set_active_project(fallback)
        return {"ok": True, "project_id": fallback, "changed": True, "previous": active}

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
        import shutil
        from engine import log_storage
        customer_name, profile_name = None, None
        for customer in self.get_customer_profiles():
            profile = next((p for p in customer["profiles"] if p["id"] == project_id), None)
            if profile:
                customer_name, profile_name = customer["name"], profile["profile_name"]
                break
        pm.delete_project(project_id)
        if customer_name and profile_name:
            profile_dir = log_storage.get_profile_dir(customer_name, profile_name)
            if os.path.isdir(profile_dir):
                shutil.rmtree(profile_dir)
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
