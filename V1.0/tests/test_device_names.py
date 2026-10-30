"""장비 별칭으로 이름 맞추기 통일 — IMPROVEMENT_PLAN 4-10e.

  1. core/device_names.DeviceNameIndex: 장비명·별칭(대소문자 무시), 그다음 도메인을 뗀 짧은 이름. 두 장비가 겹치는
     짧은 이름·별칭으로는 맞추지 않고, 주소는 도메인을 떼지 않는다. 장비명은 다른 장비의 별칭보다 먼저다.
  2. 장비 목록의 aliases(쉼표 글 → 목록, 자기 이름·중복 제거), 엑셀 내보내기/가져오기.
  3. 같은 색인을 쓰는 세 곳 — 구성도의 LLDP 이웃, 실시간 감시의 로그 파일 판정(파일명·프롬프트), 보고서의 장비 목록 조인.
"""
from engine.device_inventory_core import normalize_device

from core.device_names import DeviceNameIndex, short_name

INVENTORY = [
    {"name": "Core1", "aliases": ["core1.lab.local", "OLD-CORE"]},
    {"name": "Agg1", "aliases": "agg1.lab.local, agg-a"},
    {"name": "Acc1"},
]


def test_names_aliases_and_short_names():
    index = DeviceNameIndex(INVENTORY)
    assert index.resolve("core1") == "Core1" and index.resolve(" CORE1 ") == "Core1"
    assert index.resolve("old-core") == "Core1" and index.resolve("core1.lab.local.") == "Core1"
    assert index.resolve("agg-a") == "Agg1" and index.resolve("agg1.other.corp") == "Agg1"    # 짧은 이름 agg1
    assert index.resolve("acc1.lab.local") == "Acc1"
    assert index.resolve("Dist9") is None and index.resolve("") is None


def test_ambiguous_names_are_not_guessed():
    index = DeviceNameIndex([{"name": "SiteA-Core", "aliases": ["core.a.local", "spare"]},
                             {"name": "SiteB-Core", "aliases": ["core.b.local", "spare"]},
                             {"name": "Edge", "aliases": ["SiteA-Core"]}])
    assert index.resolve("core.a.local") == "SiteA-Core" and index.resolve("core.b.local") == "SiteB-Core"
    assert index.resolve("core") is None and index.resolve("core.c.local") is None          # 짧은 이름이 겹친다
    assert index.resolve("spare") is None                                                 # 별칭이 겹친다
    assert index.resolve("siteA-core") == "SiteA-Core"                                     # 장비명이 별칭보다 먼저
    assert index.ambiguous() == ["core", "spare"]


def test_addresses_keep_their_shape():
    index = DeviceNameIndex([{"name": "Core1", "aliases": ["10.0.0.1"]}, {"name": "10"}])
    assert index.resolve("10.0.0.1") == "Core1" and index.resolve("10.0.0.2") is None
    assert short_name("10.0.0.2") == "10.0.0.2" and short_name("aabb.ccdd.eeff") == "aabb.ccdd.eeff"
    assert short_name("core1.lab.local") == "core1"


def test_inventory_aliases_are_normalized():
    device = normalize_device({"name": "Core1", "aliases": "core1.lab.local, OLD-CORE, core1.lab.local, core1"})
    assert device["aliases"] == ["core1.lab.local", "OLD-CORE"]                             # 자기 이름·중복은 뺀다
    assert normalize_device({"name": "Acc1"})["aliases"] == []


def test_aliases_survive_an_excel_round_trip(tmp_path):
    from engine import device_inventory_import_export as io
    path = tmp_path / "inventory.xlsx"
    io.export_to_excel([normalize_device(d) for d in INVENTORY], str(path))
    imported = {d["name"]: d for d in io.import_excel(str(path))}
    assert normalize_device(imported["Agg1"])["aliases"] == ["agg1.lab.local", "agg-a"]
    assert normalize_device(imported["Acc1"])["aliases"] == []


def test_topology_links_an_lldp_neighbor_by_alias():
    from engine.topology_builder import _resolve_device
    index = DeviceNameIndex(INVENTORY)
    assert _resolve_device("OLD-CORE", index) == ("Core1", True)
    assert _resolve_device("core1.lab.local", index) == ("Core1", True)
    assert _resolve_device("unknown.lab.local", index) == ("unknown", False)
    assert _resolve_device("0050.56aa.bb01", index) == ("0050.56aa.bb01", False)


def test_session_log_files_match_by_alias_in_the_name_or_the_prompt():
    from engine.stream_device_matcher import StreamDeviceMatcher
    matcher = StreamDeviceMatcher([dict(d, ip=f"10.0.0.{i}") for i, d in enumerate(INVENTORY, 1)])
    assert matcher.resolve("C:/CRTlog/OLD-CORE_20261028_101500.log") == "Core1"
    assert matcher.resolve("C:/CRTlog/session.log", "agg1.lab.local#show version\n") == "Agg1"
    assert matcher.resolve("C:/CRTlog/10.0.0.3_20261028.log") == "Acc1"
    assert matcher.known_names() == ["Acc1", "Agg1", "Core1"]


def test_report_joins_a_log_of_an_old_name_to_the_inventory():
    from engine import inspection_report_builder as builder
    inventory = {"Core1": {"name": "Core1", "aliases": ["OLD-CORE"], "management_ip": "10.0.0.1"}}
    assert builder._inventory_record(inventory, "old-core")["management_ip"] == "10.0.0.1"
    assert builder._inventory_record(inventory, "core1.lab.local")["management_ip"] == "10.0.0.1"
    assert builder._inventory_record(inventory, "Dist9") == {}
