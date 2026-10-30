"""프로파일 생성·이름변경을 트랜잭션으로 — IMPROVEMENT_PLAN 2-1.

정기점검 프로파일 하나는 labs/<id>(메타) + data/<고객사>/<프로파일>(로그·보고서·스냅샷) 두 곳이다.
예전 문제:
  - 이름변경이 메타를 먼저 저장한 뒤 폴더 이동이 실패해도 그대로 반환했다 → 메타는 새 이름, 폴더는
    옛 이름. 새 이름의 폴더가 다른 회차 것이면 두 회차가 한 data 폴더를 함께 썼다.
  - 고객사 이름변경도 같은 순서라 중간 실패가 불일치를 남겼다.
  - 'ABC'·'abc' 처럼 대소문자만 다른 이름은 막지 않았다 — Windows 에서는 같은 폴더다.
  - 생성 도중 실패하면 labs 쪽만 있는(그리고 이미 활성인) 반쪽 프로파일이 남았다.
  - 활성 프로파일 이름을 바꾸면 실시간 감시가 옛 경로에 상태를 저장해 옛 폴더가 되살아났다.
"""
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

    from api import customer_profile_api
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
        def __init__(self):
            self.moving_calls = []

        def _notify_active_profile_moving(self, before):
            self.moving_calls.append(before)

    yield Api()

    workspace_cache.invalidate()
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def data_dir(*parts):
    return AppPaths.data_root().joinpath(*parts)


def customer_id(api, name):
    return next(c["id"] for c in api.get_customer_profiles() if c["name"] == name)


def profile(api, customer, name):
    tree = {c["name"]: c for c in api.get_customer_profiles()}
    return next(p for p in tree[customer]["profiles"] if p["profile_name"] == name)


@pytest.fixture
def abc(api):
    """고객사 ABC 에 정기점검 P1·P2 — P1 의 data 폴더에는 기록 하나."""
    assert api.create_customer("ABC") == {"ok": True}
    cid = customer_id(api, "ABC")
    for name in ("P1", "P2"):
        assert api.create_inspection_profile(cid, name)["ok"] is True
    (data_dir("ABC", "P1", "reports") / "보고서.xlsx").write_bytes(b"xlsx")
    return cid


def test_names_differing_only_in_case_are_the_same_folder(api, abc):
    assert "error" in api.create_customer("abc")
    assert "error" in api.create_inspection_profile(abc, "p1")
    assert "error" in api.rename_inspection_profile(profile(api, "ABC", "P2")["id"], "p1")
    assert sorted(p.name for p in data_dir("ABC").iterdir() if p.is_dir()) == ["P1", "P2"]


def test_rename_moves_the_folder_with_the_profile(api, abc):
    pid = profile(api, "ABC", "P1")["id"]
    assert api.rename_inspection_profile(pid, "P1-new") == {"ok": True}
    assert (data_dir("ABC", "P1-new", "reports") / "보고서.xlsx").exists()
    assert not data_dir("ABC", "P1").exists()
    assert profile(api, "ABC", "P1-new")["id"] == pid


def test_rename_onto_a_leftover_folder_changes_nothing(api, abc):
    data_dir("ABC", "Old", "reports").mkdir(parents=True)          # 어느 프로파일도 쓰지 않는 이전 기록
    pid = profile(api, "ABC", "P1")["id"]
    result = api.rename_inspection_profile(pid, "Old")
    assert "이미 있습니다" in result["error"]
    assert profile(api, "ABC", "P1")["id"] == pid
    assert (data_dir("ABC", "P1", "reports") / "보고서.xlsx").exists()


def test_rename_puts_the_folder_back_when_the_meta_cannot_be_saved(api, abc, monkeypatch):
    from api import customer_profile_api

    def broken(*args, **kwargs):
        raise OSError("디스크 오류")

    pid = profile(api, "ABC", "P1")["id"]
    original = customer_profile_api.dump_yaml_atomic
    monkeypatch.setattr(customer_profile_api, "dump_yaml_atomic", broken)
    with pytest.raises(OSError):
        api.rename_inspection_profile(pid, "P1-new")
    monkeypatch.setattr(customer_profile_api, "dump_yaml_atomic", original)
    assert (data_dir("ABC", "P1", "reports") / "보고서.xlsx").exists()
    assert not data_dir("ABC", "P1-new").exists()
    assert profile(api, "ABC", "P1")["id"] == pid


def test_case_only_rename_keeps_the_folder(api, abc):
    pid = profile(api, "ABC", "P1")["id"]
    assert api.rename_inspection_profile(pid, "p1") == {"ok": True}
    assert (data_dir("ABC", "p1", "reports") / "보고서.xlsx").exists()   # Windows 에서는 같은 폴더
    assert profile(api, "ABC", "p1")["id"] == pid


def test_customer_rename_moves_folder_then_metas(api, abc):
    assert api.rename_customer(abc, "XYZ") == {"ok": True}
    assert (data_dir("XYZ", "P1", "reports") / "보고서.xlsx").exists()
    assert not data_dir("ABC").exists()
    tree = api.get_customer_profiles()
    assert [c["name"] for c in tree] == ["XYZ"]
    assert sorted(p["profile_name"] for p in tree[0]["profiles"]) == ["P1", "P2"]


def test_customer_rename_rolls_back_when_the_list_cannot_be_saved(api, abc, monkeypatch):
    from engine import customer_manager as cm

    def broken(customers):
        raise OSError("디스크 오류")

    original = cm.save
    monkeypatch.setattr(cm, "save", broken)
    with pytest.raises(OSError):
        api.rename_customer(abc, "XYZ")
    monkeypatch.setattr(cm, "save", original)
    assert data_dir("ABC", "P1", "reports").exists() and not data_dir("XYZ").exists()
    import yaml
    from engine import project_manager as pm
    for project in pm.list_projects():
        meta = yaml.safe_load((Path(pm.LABS_DIR) / project["id"] / "project_meta.yaml").read_text(encoding="utf-8"))
        assert meta["customer_name"] == "ABC"
    assert [c["name"] for c in api.get_customer_profiles()] == ["ABC"]


def test_customer_rename_onto_a_leftover_folder_is_refused(api, abc):
    data_dir("XYZ").mkdir(parents=True)
    assert "이미 있습니다" in api.rename_customer(abc, "XYZ")["error"]
    assert data_dir("ABC", "P1").exists()
    assert [c["name"] for c in api.get_customer_profiles()] == ["ABC"]


def test_failed_creation_leaves_no_half_profile(api, abc, monkeypatch):
    from engine import project_manager as pm
    from engine.profile_manager import profile_manager as prm
    before_projects = sorted(p["id"] for p in pm.list_projects())
    before_active = pm.get_active_project()

    def broken(*args, **kwargs):
        raise OSError("권한 없음")

    monkeypatch.setattr(prm, "create_profile", broken)
    result = api.create_inspection_profile(abc, "P3")
    assert "만들지 못했습니다" in result["error"]
    assert sorted(p["id"] for p in pm.list_projects()) == before_projects
    assert pm.get_active_project() == before_active
    assert not data_dir("ABC", "P3").exists()


def test_creation_over_a_leftover_folder_says_so(api, abc):
    from engine.profile_manager import profile_manager as prm
    prm.create_profile("ABC", "Old")                                   # labs 쪽 없이 남은 data 폴더
    result = api.create_inspection_profile(abc, "Old")
    assert result["ok"] is True and result["reused_data"] is True
    assert api.create_inspection_profile(abc, "P3")["reused_data"] is False


def test_renaming_the_active_profile_tells_realtime_to_move_its_state(api, abc):
    from engine import project_manager as pm
    active = pm.get_active_project()                                   # 마지막에 만든 P2 가 활성
    assert profile(api, "ABC", "P2")["id"] == active
    api.rename_inspection_profile(profile(api, "ABC", "P1")["id"], "P1-new")
    assert api.moving_calls == []                                      # 활성 아닌 프로파일 — 감시와 무관
    api.rename_inspection_profile(active, "P2-new")
    assert api.moving_calls == [True, False]
    api.rename_customer(abc, "XYZ")
    assert api.moving_calls == [True, False, True, False]


# --------------------------------------------------------------------------- 실시간 감시 상태 이동

@pytest.fixture
def realtime(monkeypatch):
    original = AppPaths._user_data_root
    AppPaths._user_data_root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths.forget_ensured()
    from api.realtime_watch_api import RealtimeWatchApiMixin
    from engine.profile_manager import profile_manager as prm

    class Stub(RealtimeWatchApiMixin):
        active = ("고객사A", "2026-07")

        def _resolve_active_customer_profile_names(self):
            return self.active

    prm.create_profile("고객사A", "2026-07")
    yield Stub()
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def test_realtime_state_moves_with_the_renamed_folder(realtime):
    from engine.profile_manager import profile_manager as prm
    monitor = realtime._realtime().monitor()
    monitor.adopt_devices(["Core1"], ["Core1"])
    monitor.append_lines("Core1", "no vlan 100")                       # 아직 저장 안 된 최신 내역

    realtime._notify_active_profile_moving(True)
    assert realtime._realtime().save_state(force=True) is False          # 옮기는 동안은 저장하지 않는다
    prm.rename_profile("고객사A", "2026-07", "2026-07b")
    realtime.active = ("고객사A", "2026-07b")
    realtime._notify_active_profile_moving(False)

    assert realtime._realtime().sync_profile(force=True) is False        # 같은 프로파일 — 갈아끼우지 않는다
    assert not data_dir("고객사A", "2026-07").exists()                 # 옛 이름 폴더가 되살아나지 않는다
    assert (data_dir("고객사A", "2026-07b") / "realtime_state.json").exists()
    realtime._realtime().save_state(force=True)
    assert not data_dir("고객사A", "2026-07").exists()
