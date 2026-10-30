"""조회 경로에서 쓰기 제거 — IMPROVEMENT_PLAN 2-7.

  1. 고객사 트리를 읽을 때마다 모든 project_meta.yaml·customers.yaml 을 다시 쓰던 것(쓰기 조건이 항상 참)
     — 병렬 호출이 이름변경과 겹치면 옛 메타로 덮어쓸 수 있었다.
  2. 메타 하나가 깨지거나 모르는 mode 면 프로젝트 목록 전체가 예외로 끝났다(거의 모든 화면이 실패).
  3. list_profiles 가 항목마다 repair_profile 을 불러 보관 폴더(_archived_profiles)까지 가짜 프로파일로 만들었다.
  4. 화면 요청마다 부르는 project_paths() 가 매번 data 폴더를 만들고, 빈 기본 계정까지 credential.json 으로 썼다.
     (4-1 부터는 레거시 기본 계정도 credential.json 으로 옮기지 않는다 — 읽는 곳이 없는 평문 사본이었다.)
"""
import tempfile
from pathlib import Path

import pytest
import yaml

from core.paths import AppPaths


@pytest.fixture
def api(monkeypatch):
    original = AppPaths._user_data_root
    root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths._user_data_root = root
    AppPaths.forget_ensured()
    from api.customer_profile_api import CustomerProfileApiMixin
    from api.project_core_api import ProjectCoreApiMixin
    from core.context_cache import workspace_cache
    from engine import customer_manager as cm
    from engine import project_manager as pm
    monkeypatch.setattr(pm, "LABS_DIR", str(root / "labs"))
    monkeypatch.setattr(pm, "STATE_FILE", str(root / "config" / "active_project.yaml"))
    monkeypatch.setattr(cm, "CUSTOMERS_PATH", str(root / "config" / "customers.yaml"))
    workspace_cache.invalidate()

    class Api(CustomerProfileApiMixin, ProjectCoreApiMixin):
        pass

    api = Api()
    api.create_customer("ABC")
    cid = next(c["id"] for c in api.get_customer_profiles())
    for name in ("P1", "P2"):
        api.create_inspection_profile(cid, name)
    yield api, root
    workspace_cache.invalidate()
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def snapshot(root):
    return {p: (p.stat().st_mtime_ns, p.read_bytes()) for p in root.rglob("*.yaml")}


def test_reading_the_tree_writes_nothing(api):
    from core.context_cache import workspace_cache
    api, root = api
    api.get_customer_profiles()
    before = snapshot(root)
    for _ in range(3):
        workspace_cache.invalidate()
        api.get_customer_profiles()
    assert snapshot(root) == before


def test_one_broken_meta_does_not_break_the_tree(api, capsys):
    from core.context_cache import workspace_cache
    from engine import project_manager as pm
    api, root = api
    p1 = next(p["id"] for c in api.get_customer_profiles() for p in c["profiles"] if p["profile_name"] == "P1")
    (Path(pm.LABS_DIR) / p1 / "project_meta.yaml").write_text("display_name: [깨짐", encoding="utf-8")
    workspace_cache.invalidate()
    names = [p["profile_name"] for c in api.get_customer_profiles() for p in c["profiles"]]
    assert names == ["P2"]
    assert "건너뜀" in capsys.readouterr().out


def test_unknown_mode_is_skipped_not_fatal(api):
    from engine import project_manager as pm
    api, root = api
    folder = Path(pm.LABS_DIR) / "future_project"
    folder.mkdir()
    (folder / "project_meta.yaml").write_text(yaml.safe_dump({"display_name": "X", "mode": "future"}), encoding="utf-8")
    assert "future_project" not in [p["id"] for p in pm.list_projects()]


def test_list_profiles_reads_without_creating_folders(api):
    from engine.profile_manager import profile_manager as prm
    api, root = api
    archived = AppPaths.data_root() / "ABC" / "_archived_profiles" / "Old_20260101"
    archived.mkdir(parents=True)
    before = sorted(p.as_posix() for p in AppPaths.data_root().rglob("*"))
    assert [p["name"] for p in prm.list_profiles("ABC")] == ["P1", "P2"]
    assert sorted(p.as_posix() for p in AppPaths.data_root().rglob("*")) == before


def test_project_paths_does_not_write_empty_credentials(api):
    from engine import project_manager as pm
    api, root = api
    p1 = next(p["id"] for c in api.get_customer_profiles() for p in c["profiles"] if p["profile_name"] == "P1")
    pm.project_paths(p1)
    assert not (AppPaths.data_root() / "ABC" / "P1" / "profile" / "credential.json").exists()


def test_legacy_credentials_are_no_longer_copied_to_credential_json(api):
    """4-1: 레거시 기본 계정을 credential.json 으로 평문 복사하던 것을 그만뒀다 — 아무도 읽지 않는 사본이었다.
    기본 계정의 정본은 장비 목록의 기본값이다(레거시 이전이 거기로 옮긴다)."""
    from engine import project_manager as pm
    api, root = api
    p1 = next(p["id"] for c in api.get_customer_profiles() for p in c["profiles"] if p["profile_name"] == "P1")
    (Path(pm.LABS_DIR) / p1 / "ip_allocation.yaml").write_text(
        yaml.safe_dump({"default_credentials": {"username": "ops", "password": "pw"}}), encoding="utf-8")
    pm.project_paths(p1)
    assert not (AppPaths.data_root() / "ABC" / "P1" / "profile" / "credential.json").exists()
