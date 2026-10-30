"""삭제를 보관으로 — IMPROVEMENT_PLAN 2-5.

예전: 삭제 경로 두 벌(고객사 트리, 프로젝트 모달)이 모두 labs·data 를 영구 rmtree 했다. 고객사를 지우면
보관본(_archived_profiles)까지 함께 지워졌다. 워크스페이스의 '보관'은 data 폴더만 옮겨서 labs 프로젝트가
목록에 그대로 남았고, 다음 접근에 빈 data 폴더가 다시 생겼다. 복원 기능은 없었다.
이제: 지우는 동작은 보관(labs + data 를 한 폴더로), 복원·영구 삭제는 보관본에만 한다.
"""
import json
import tempfile
from pathlib import Path

import pytest

from core.paths import AppPaths


@pytest.fixture
def api(monkeypatch):
    original = AppPaths._user_data_root
    root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths._user_data_root = root
    AppPaths.forget_ensured()
    from api.customer_profile_api import CustomerProfileApiMixin
    from api.project_core_api import ProjectCoreApiMixin
    from api.workspace_api import WorkspaceApiMixin
    from core.context_cache import workspace_cache
    from engine import customer_manager as cm
    from engine import project_manager as pm
    monkeypatch.setattr(pm, "LABS_DIR", str(root / "labs"))
    monkeypatch.setattr(pm, "STATE_FILE", str(root / "config" / "active_project.yaml"))
    monkeypatch.setattr(cm, "CUSTOMERS_PATH", str(root / "config" / "customers.yaml"))
    workspace_cache.invalidate()

    class Api(CustomerProfileApiMixin, ProjectCoreApiMixin, WorkspaceApiMixin):
        pass

    api = Api()
    api.create_customer("ABC")
    cid = next(c["id"] for c in api.get_customer_profiles())
    for name in ("P1", "P2"):
        api.create_inspection_profile(cid, name)
    (AppPaths.data_root() / "ABC" / "P1" / "reports" / "보고서.xlsx").write_bytes(b"xlsx")
    api.cid = cid
    yield api
    workspace_cache.invalidate()
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def profiles(api):
    return {p["profile_name"]: p["id"] for c in api.get_customer_profiles() for p in c["profiles"]}


def archive_root():
    return AppPaths.data_root() / "ABC" / "_archived_profiles"


def test_delete_archives_both_halves(api):
    from engine import project_manager as pm
    pid = profiles(api)["P1"]
    result = api.delete_inspection_profile(pid)
    archived = Path(result["archived"])
    assert archived.parent == archive_root() and archived.name.startswith("P1_")
    assert (archived / "reports" / "보고서.xlsx").exists()
    assert (archived / "_labs_project" / pid / "project_meta.yaml").exists()
    assert json.loads((archived / "archive.json").read_text(encoding="utf-8"))["project_id"] == pid
    assert "P1" not in profiles(api)
    assert pid not in [p["id"] for p in pm.list_projects()]
    assert not (AppPaths.data_root() / "ABC" / "P1").exists()       # 다음 조회에 다시 생기지 않는다


def test_archiving_the_active_profile_clears_it(api):
    from engine import project_manager as pm
    pid = profiles(api)["P2"]                                         # 마지막에 만든 것이 활성
    assert pm.get_active_project() == pid
    api.delete_inspection_profile(pid)
    assert pm.get_active_project() is None


def test_restore_brings_back_the_same_profile(api):
    pid = profiles(api)["P1"]
    archive = Path(api.delete_inspection_profile(pid)["archived"])
    assert api.restore_archived_profile(archive.name, "ABC") == {"ok": True, "id": pid}
    assert profiles(api)["P1"] == pid
    assert (AppPaths.data_root() / "ABC" / "P1" / "reports" / "보고서.xlsx").exists()
    assert not archive.exists()
    assert api.list_archived_profiles("ABC") == []


def test_restore_refuses_to_mix_with_a_new_profile_of_the_same_name(api):
    archive = Path(api.delete_inspection_profile(profiles(api)["P1"])["archived"])
    api.create_inspection_profile(api.cid, "P1")
    new_id = profiles(api)["P1"]
    assert "이미 있습니다" in api.restore_archived_profile(archive.name, "ABC")["error"]
    assert profiles(api)["P1"] == new_id and archive.is_dir()


def test_deleting_a_customer_archives_everything_and_can_come_back(api):
    from engine import customer_manager as cm
    ids = profiles(api)
    result = api.delete_customer(api.cid)
    assert result["ok"] and len(result["archived"]) == 2
    assert api.get_customer_profiles() == [] and cm.load() == []
    listed = api.list_archived_profiles("ABC")
    assert sorted(a["profile_name"] for a in listed) == ["P1", "P2"] and all(a["restorable"] for a in listed)
    p1 = next(a for a in listed if a["profile_name"] == "P1")
    assert api.restore_archived_profile(p1["archive"], "ABC")["id"] == ids["P1"]
    assert [c["id"] for c in cm.load()] == [api.cid]                  # 원래 고객사 id 로 되살아난다


def test_purge_deletes_only_archives(api):
    archive = Path(api.delete_inspection_profile(profiles(api)["P1"])["archived"])
    assert "error" in api.purge_archived_profile("../P2", "ABC")
    assert api.purge_archived_profile(archive.name, "ABC") == {"ok": True}
    assert not archive.exists()
    assert (AppPaths.data_root() / "ABC" / "P2").is_dir()


def test_workspace_archive_takes_the_profile_out_of_the_list(api):
    result = api.archive_profile("ABC", "P1")
    assert Path(result["path"]).parent == archive_root()
    assert "P1" not in profiles(api)
    assert not (AppPaths.data_root() / "ABC" / "P1").exists()


def test_failed_archive_puts_everything_back(api, monkeypatch):
    import shutil
    from engine import profile_archive
    pid = profiles(api)["P1"]
    real_move = shutil.move

    def move(src, dst):
        if "_labs_project" in str(dst):
            raise OSError("잠긴 파일")
        return real_move(src, dst)

    monkeypatch.setattr(profile_archive.shutil, "move", move)
    assert "보관하지 못했습니다" in api.delete_inspection_profile(pid)["error"]
    monkeypatch.setattr(profile_archive.shutil, "move", real_move)
    assert profiles(api)["P1"] == pid
    assert (AppPaths.data_root() / "ABC" / "P1" / "reports" / "보고서.xlsx").exists()
    assert not archive_root().exists() or list(archive_root().iterdir()) == []
