"""ProjectCoreApiMixin — 프로젝트(labs/<id>) 자체의 원시 CRUD + zip 내보내기/불러오기.
고객사 단위 그룹핑은 customer_profile_api.py(CustomerProfileApiMixin)가 그 위에 얹는다 —
import_project 가 가져온 프로젝트를 그쪽의 _link_imported_project()로 고객사 트리에 잇으므로 항상
함께(ProjectApiMixin에서) 조합되어야 한다.
"""
import os
import shutil

import yaml

from engine import project_manager as pm
from core.context_cache import workspace_cache
from core.paths import safe_child, safe_join
from core.secret_redaction import scrub_secret_fields

# 프로젝트 폴더는 YAML 몇 개다. 이보다 크게 풀리는 zip 은 프로젝트 내보내기가 아니다(zip bomb 방지).
_IMPORT_MAX_BYTES = 512 * 1024 * 1024


def _revealed_yaml(path):
    """YAML 파일에 봉인된 비밀값이 있으면 풀어 놓은 YAML 텍스트, 없으면 None — '계정 정보 포함' 내보내기용.
    봉인(DPAPI)은 이 PC·이 사용자에서만 풀리므로, 다른 PC 로 옮길 zip 에는 풀어서 담아야 쓸 수 있다.
    이 PC 에서도 풀 수 없는 값은 빈 값으로 담는다."""
    if not path.lower().endswith((".yaml", ".yml")):
        return None
    from core.secret_vault import PREFIX, reveal_fields
    from engine.secret_files import SECRET_KEYS
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        data = yaml.safe_load(raw)
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    if PREFIX not in raw:
        return None
    revealed, _unreadable = reveal_fields(data, SECRET_KEYS)
    return yaml.safe_dump(revealed, allow_unicode=True, sort_keys=False)


def _scrubbed_yaml(path):
    """YAML 파일에 계정 정보 값(장비 비밀번호·개인키 등)이 있으면 그 값을 비운 YAML 텍스트,
    없으면 None — None 이면 원본 바이트를 그대로 담는다(주석·서식 보존)."""
    if not path.lower().endswith((".yaml", ".yml")):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    scrubbed, count = scrub_secret_fields(data)
    if not count:
        return None
    return yaml.safe_dump(scrubbed, allow_unicode=True, sort_keys=False)


class ProjectCoreApiMixin:
    def _activated_profile(self, project_id=None):
        """활성 프로파일이 바뀐 직후에 반드시 지나야 하는 후처리.

        두 가지를 한다:
          1) workspace_cache 무효화 — _resolve_active_customer_profile_names()가 캐시되어 있어서,
             여기서 비우지 않으면 활성 프로젝트 파일은 바뀌었는데도 앱 전체가 **이전 프로파일의
             (고객사, 프로파일)** 을 계속 돌려준다. 그러면 data/<고객사>/<프로파일> 로 갈라지는
             모든 것(로그 경로·보고서·실시간 감시 상태)이 이전 프로파일에 머문다.
          2) 프로파일 단위로 살아 있는 메모리 상태(실시간 감시)를 새 프로파일 것으로 갈아끼운다.

        후처리가 실패해도 프로파일 전환 자체는 성공시켜야 하므로 예외는 삼키고 알린다.
        """
        workspace_cache.invalidate()
        hook = getattr(self, "_notify_active_profile_changed", None)
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

    def set_active_project(self, project_id):
        pm.set_active_project(project_id)
        self._activated_profile(project_id)
        return True

    def export_project(self, project_id, include_credentials=False):
        """프로젝트(고객사/점검 프로파일) 폴더 전체를 zip 하나로 내보내기 — 다른 PC로 이전/백업용.
        장비 비밀번호·개인키(device_inventory.yaml, ip_allocation.yaml)는 기본으로 비워서 담는다.
        include_credentials=True 면 그대로 담고 결과에 warning 을 붙인다(화면이 묻고 경고한다)."""
        import webview
        import zipfile
        from api.window_ref import get_window
        try:
            src_dir = str(safe_child(pm.LABS_DIR, project_id))   # '..' 로 사용자 데이터 전체를 묶지 못하게
        except ValueError as exc:
            return {"error": str(exc)}
        if not os.path.isdir(src_dir):
            return {"error": f"프로젝트 없음: {project_id}"}
        window = get_window()
        result = window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=f"{project_id}.zip",
            file_types=("ZIP files (*.zip)",),
        )
        if not result:
            return None
        dst = result if isinstance(result, str) else result[0]
        scrubbed = 0
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(src_dir):
                for fname in files:
                    full = os.path.join(root, fname)
                    arcname = os.path.relpath(full, pm.LABS_DIR)
                    text = _revealed_yaml(full) if include_credentials else _scrubbed_yaml(full)
                    if text is None:
                        zf.write(full, arcname)
                    else:
                        zf.writestr(arcname.replace(os.sep, "/"), text)
                        scrubbed += 1
        result = {"ok": True, "path": dst}
        if include_credentials:
            result["warning"] = "장비 비밀번호·개인키가 zip 안에 평문으로 들어갔습니다 — 전달·보관에 주의하세요."
        elif scrubbed:
            result["notice"] = "장비 비밀번호·개인키는 비워서 내보냈습니다 — 가져간 PC에서 다시 입력하세요."
        return result

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
                infos = zf.infolist()
                if not infos:
                    return {"error": "빈 zip 파일입니다."}
                # zip 은 받은 파일이다 — 멤버 이름('proj/../../Startup/x.bat')과 크기(zip bomb)를 믿지 않는다.
                if sum(info.file_size for info in infos) > _IMPORT_MAX_BYTES:
                    return {"error": f"zip 의 압축을 푼 크기가 {_IMPORT_MAX_BYTES // (1024 * 1024)}MB 를 넘습니다."}
                original_id = infos[0].filename.split("/")[0]
                safe_child(pm.LABS_DIR, original_id)   # 최상위 폴더부터 한 단계 이름이어야 한다('..', 'C:' 거부)
                if original_id.startswith("_"):   # labs/_customers 같은 앱 내부 폴더 자리 — 프로젝트 id 는 '_' 로 시작하지 않는다
                    return {"error": "프로젝트 내보내기 zip 이 아닙니다."}
                new_id = original_id
                if os.path.exists(os.path.join(pm.LABS_DIR, new_id)):
                    new_id = f"{original_id}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
                project_root = safe_child(pm.LABS_DIR, new_id)
                # 쓰기 전에 모든 멤버의 자리를 먼저 검사한다 — 중간에 멈춰 반쯤 풀린 프로젝트를 남기지 않게.
                plan = []
                for info in infos:
                    if not info.filename.startswith(original_id + "/"):
                        continue
                    rel = info.filename[len(original_id):].lstrip("/")
                    if rel:
                        plan.append((info, safe_join(project_root, rel)))
                if not plan:
                    return {"error": "프로젝트 내보내기 zip 이 아닙니다."}
                os.makedirs(project_root, exist_ok=True)
                for info, target_path in plan:
                    if info.is_dir():
                        os.makedirs(target_path, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with zf.open(info) as fsrc, open(target_path, "wb") as fdst:
                        shutil.copyfileobj(fsrc, fdst)
            # '계정 정보 포함'으로 내보낸 zip 은 평문이다 — 들어오자마자 봉인한다(IMPROVEMENT_PLAN 4-1).
            from engine.secret_files import seal_secret_files
            seal_secret_files(project_root)
        except zipfile.BadZipFile:
            return {"error": "올바른 zip 파일이 아닙니다."}
        except ValueError as exc:
            return {"error": f"zip 안에 허용되지 않는 경로가 있어 가져오지 않았습니다 — {exc}"}
        # 고객사 트리에 잇는다 — 같은 이름이 있으면 '(가져옴)'을 붙이고 data 폴더를 만든다(IMPROVEMENT_PLAN 5-3·D5).
        try:
            linked = self._link_imported_project(new_id)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            pm.delete_project(new_id)
            workspace_cache.invalidate()
            return {"error": f"가져온 프로파일을 고객사 목록에 잇지 못해 되돌렸습니다 — {exc}"}
        return {"ok": True, "project_id": new_id, **linked}
