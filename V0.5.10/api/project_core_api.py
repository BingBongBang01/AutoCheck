"""ProjectCoreApiMixin — 프로젝트(labs/<id>) 자체의 원시 CRUD + zip 내보내기/불러오기.
고객사 단위 그룹핑은 customer_profile_api.py(CustomerProfileApiMixin)가 그 위에 얹는다 —
delete_project 등에서 self.get_customer_profiles()를 호출하므로 항상 함께(ProjectApiMixin에서)
조합되어야 한다.
"""
import os
from engine import project_manager as pm
from core.context_cache import workspace_cache


class ProjectCoreApiMixin:
    def _activated_profile(self, project_id=None):
        """활성 프로파일이 바뀐 직후에 반드시 지나야 하는 후처리.

        두 가지를 한다:
          1) workspace_cache 무효화 — resolve_active_customer_profile_names()가 캐시되어 있어서,
             여기서 비우지 않으면 활성 프로젝트 파일은 바뀌었는데도 앱 전체가 **이전 프로파일의
             (고객사, 프로파일)** 을 계속 돌려준다. 그러면 data/<고객사>/<프로파일> 로 갈라지는
             모든 것(로그 경로·보고서·실시간 감시 상태)이 이전 프로파일에 머문다.
          2) 프로파일 단위로 살아 있는 메모리 상태(실시간 감시)를 새 프로파일 것으로 갈아끼운다.

        후처리가 실패해도 프로파일 전환 자체는 성공시켜야 하므로 예외는 삼키고 알린다.
        """
        workspace_cache.invalidate()
        hook = getattr(self, "notify_active_profile_changed", None)
        if hook is None:
            return
        try:
            hook()
        except Exception as exc:
            print(f"[프로파일 전환] 실시간 감시 상태 전환 실패: {exc}")

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
        self._activated_profile(fallback)
        return {"ok": True, "project_id": fallback, "changed": True, "previous": active}

    def create_project(self, name):
        new_id = pm.create_project(name)
        pm.set_active_project(new_id)
        self._activated_profile(new_id)
        return new_id

    def set_active_project(self, project_id):
        pm.set_active_project(project_id)
        self._activated_profile(project_id)
        return True

    def rename_project(self, project_id, new_name):
        pm.rename_project(project_id, new_name)
        workspace_cache.invalidate()
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
        # 지운 것이 활성 프로파일이면 pm.delete_project()가 활성 상태를 해제한다 —
        # 캐시를 비워야 앱이 '없어진 프로파일'을 계속 활성으로 보지 않는다.
        self._activated_profile(pm.get_active_project())
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
