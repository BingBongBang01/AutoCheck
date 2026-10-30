"""구성도 — IMPROVEMENT_PLAN 2-16.

  1. 실시간 오버레이가 캐시된 구조를 직접 고쳐서, 감시를 끈 뒤에도 마지막 상태가 남았다.
  2. 최신 run 하나만 봐서, 1대만 다시 점검하면 구성도 대부분이 사라졌다.
  3. 배치를 '고객사/프로파일' 이름으로 저장해서 이름을 바꾸면 배치가 사라졌다.
  4. 계층 키워드를 부분 문자열로 찾아 'storage-sw1' 이 'tor'(Access)로 분류됐다.
"""
import datetime
import tempfile
from pathlib import Path

import pytest

from core.paths import AppPaths
from engine.topology_builder import TIER_ACCESS, TIER_CORE, TIER_EDGE, TIER_UNKNOWN, _tier_of


@pytest.mark.parametrize("name, tier", [
    ("storage-sw1", TIER_UNKNOWN), ("CoreSW1", TIER_CORE), ("dc1-spine2", TIER_CORE),
    ("edge-sw1", TIER_ACCESS), ("leaf-1", TIER_ACCESS), ("FW01", TIER_EDGE), ("abby-01", TIER_UNKNOWN),
])
def test_tier_keywords_match_name_parts_not_substrings(name, tier):
    assert _tier_of({"name": name}) == tier


C, P = "고객사A", "2026-10"


@pytest.fixture
def api(monkeypatch):
    original = AppPaths._user_data_root
    AppPaths._user_data_root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths.forget_ensured()
    from api.log_file_browser_api import LogFileBrowserApiMixin
    from api.topology_api import TopologyApiMixin
    from engine.profile_manager import profile_manager as prm
    prm.create_profile(C, P)

    class Stub(TopologyApiMixin, LogFileBrowserApiMixin):
        def _project(self):
            return "proj1"

        def _resolve_active_customer_profile_names(self):
            return C, P

        def _topology_devices(self):
            return []

    yield Stub()
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def inspection_run(devices, when, mode="terminal_inspection"):
    from core.log_naming import build_inspection_log_name
    from engine.run_manager import RunManager
    run = RunManager().create_run(C, P, execution_mode=mode)
    for device in devices:
        (run.raw_dir / build_inspection_log_name(device, when=when)).write_text(f"{device}#show lldp neighbors\n",
                                                                               encoding="utf-8")
    return run


def test_each_device_uses_its_latest_log_across_runs(api):
    first = inspection_run(["Core1", "Agg1"], datetime.datetime(2026, 10, 1, 9, 0, 0))
    second = inspection_run(["Core1"], datetime.datetime(2026, 10, 2, 9, 0, 0))     # Core1 만 재점검
    latest = api._topology_log_files()
    assert {device: run_id for device, (_s, _p, run_id) in latest.items()} == {
        "Core1": second.run_id, "Agg1": first.run_id}


def test_live_overlay_does_not_stick_to_the_cached_structure(api, monkeypatch):
    import engine.topology_layout as layout_module
    import engine.topology_svg as svg_module
    edge = {"id": "e1", "a": "Core1", "b": "Agg1", "a_port": "Ethernet1", "b_port": "Ethernet1", "state": "up"}
    structure = {"topology": {"nodes": [], "edges": [edge], "pairs": [], "warnings": []}, "run_id": "r1"}
    monkeypatch.setattr(api, "_topology_structure", lambda: structure)
    monkeypatch.setattr(layout_module, "layout", lambda topology, positions: {"width": 1, "height": 1})
    monkeypatch.setattr(svg_module, "render_svg", lambda *a, **k: "<svg/>")
    live = {"running": True, "down": [("Core1", "Ethernet1")], "observed": ["Core1"]}
    monkeypatch.setattr(api, "_topology_live_state", lambda: live)
    assert api.get_network_topology()["edges"][0]["state"] == "down"
    live = {"running": False}                                             # 감시를 껐다
    monkeypatch.setattr(api, "_topology_live_state", lambda: live)
    assert api.get_network_topology()["edges"][0]["state"] == "up"        # 점검 시점 판정으로 돌아온다
    assert edge["state"] == "up"


def test_layout_follows_the_profile_id_and_reads_the_old_name_key(api, monkeypatch, tmp_path):
    from engine import project_manager as pm
    monkeypatch.setattr(pm, "get_active_project", lambda: "proj1")
    monkeypatch.setattr(api, "_topology_config_path", lambda: str(tmp_path / "topology_layout.yaml"))
    api._write_topology_config({f"{C}/{P}": {"Core1": [10, 20]}})          # 예전 이름 키로 저장된 배치
    assert api._topology_layout_positions() == {"Core1": [10.0, 20.0]}
    api._write_topology_config({api._topology_layout_key(): {"Core1": [30, 40]}})
    monkeypatch.setattr(api, "_resolve_active_customer_profile_names", lambda: (C, "이름을 바꾼 회차"))
    assert api._topology_layout_positions() == {"Core1": [30.0, 40.0]}   # 이름이 바뀌어도 id 로 찾는다
