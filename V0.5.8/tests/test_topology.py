"""네트워크 구성도 — 수집된 점검 로그에서 물리 구성을 복원한다.

새로 수집하는 것은 없다. 정기점검이 이미 모으는 네 가지 출력만으로 구성이 나온다:
`show lldp neighbors`(연결) / `show interfaces status`(상태 + Po 소속) /
`show interfaces description`(라벨) / `show mlag`(이중화 쌍).

아래 픽스처는 **실제 워크스페이스(data/eve/7sw/runs/2026-08-07_095306/raw)에서 그대로 가져온
출력 형태**다. 벤더 출력 형태를 상상해서 만든 테스트는 현장에서 한 줄도 못 읽는 파서를
통과시킨다 — 실제로 이 프로젝트의 기존 BGP/OSPF 파서가 그 상태였다.
"""
import xml.etree.ElementTree as ET

import pytest

from engine.topology_builder import (KIND_L2, KIND_L3, TIER_ACCESS, TIER_AGG, TIER_CORE,
                                     TIER_UNKNOWN, build_topology)
from engine.topology_layout import layout
from engine.topology_svg import render_svg
from parsers.show_lldp_neighbors import parse_lldp_neighbors

# ---------- 실제 출력에서 가져온 픽스처 ----------
LLDP_CORE1 = """\
Last table change time   : 8 days, 17:13:19 ago
Number of table inserts  : 6
Number of table deletes  : 0
Number of table drops    : 0
Number of table age-outs : 0

Port Neighbor Device ID Neighbor Port ID TTL
---- ------------------ ---------------- ---
Et1  Core2              Ethernet1        120
Et2  Core2              Ethernet2        120
Et3  Agg1               Ethernet3        120
Et4  Agg1               Ethernet4        120
"""
LLDP_CORE2 = """\
Port Neighbor Device ID Neighbor Port ID TTL
---- ------------------ ---------------- ---
Et1  Core1              Ethernet1        120
Et2  Core1              Ethernet2        120
"""
LLDP_AGG1 = """\
Port Neighbor Device ID Neighbor Port ID TTL
---- ------------------ ---------------- ---
Et3  Core1              Ethernet3        120
Et4  Core1              Ethernet4        120
Et7  Access1            Ethernet1        120
"""
LLDP_ACCESS1 = """\
Port Neighbor Device ID Neighbor Port ID TTL
---- ------------------ ---------------- ---
Et1  Agg1               Ethernet7        120
"""
STATUS_CORE1 = """\
Port       Name          Status       Vlan      Duplex Speed  Type
Et1                      connected    in Po4093 full   1G     EbraTestPhyPort
Et2                      connected    in Po4093 full   1G     EbraTestPhyPort
Et3        core_agg_mlag connected    in Po2048 full   1G     EbraTestPhyPort
Et4        core_agg_mlag connected    in Po2048 full   1G     EbraTestPhyPort
Et9                      disabled     100       full   1G     EbraTestPhyPort
"""
DESC_CORE1 = """\
Interface                      Status         Protocol           Description
Et1                            up             up
Et3                            up             up                 core_agg_mlag
Et4                            up             up                 core_agg_mlag
"""
MLAG_CORE1 = """\
MLAG Configuration:
domain-id                          :                4093
local-interface                    :            Vlan4093
peer-address                       :          10.10.10.3
peer-link                          :    Port-Channel4093

MLAG Status:
state                              :              Active
negotiation status                 :           Connected
peer-link status                   :                  Up
"""


def sections(lldp="", status="", desc="", mlag="", running=""):
    return {"show lldp neighbors": lldp, "show interfaces status": status,
            "show interfaces description": desc, "show mlag": mlag,
            "show running-config": running}


def device(name, **kw):
    base = {"name": name, "role": "", "management_ip": "", "vendor": "Arista",
            "model": "", "enabled": True}
    base.update(kw)
    return base


@pytest.fixture
def lab():
    """Core1/Core2/Agg1/Access1 — 실제 랩의 축소판(MLAG 쌍 + Po 묶음 + 하향 링크)."""
    devices = [device("Core1", management_ip="192.168.205.101"),
               device("Core2", management_ip="192.168.205.102"),
               device("Agg1", management_ip="192.168.205.103"),
               device("Access1", management_ip="192.168.205.105")]
    by_device = {
        "Core1": sections(LLDP_CORE1, STATUS_CORE1, DESC_CORE1, MLAG_CORE1, "ip routing\n"),
        "Core2": sections(LLDP_CORE2, STATUS_CORE1, "", MLAG_CORE1),
        "Agg1": sections(LLDP_AGG1, STATUS_CORE1),
        "Access1": sections(LLDP_ACCESS1),
    }
    return devices, by_device


# ---------- 파서 ----------

def test_parser_reads_the_neighbor_table():
    got = parse_lldp_neighbors(LLDP_CORE1)
    assert [(g["local_port"], g["neighbor_device"], g["neighbor_port"]) for g in got] == [
        ("Et1", "Core2", "Ethernet1"), ("Et2", "Core2", "Ethernet2"),
        ("Et3", "Agg1", "Ethernet3"), ("Et4", "Agg1", "Ethernet4")]


def test_parser_ignores_summary_and_header_lines():
    """'Last table change time : ...' 같은 요약 줄을 이웃으로 읽으면 유령 장비가 생긴다."""
    devices = {g["neighbor_device"] for g in parse_lldp_neighbors(LLDP_CORE1)}
    assert devices == {"Core2", "Agg1"}


@pytest.mark.parametrize("text", ["", "\n\n", "LLDP is not enabled\n",
                                  "Port Neighbor Device ID Neighbor Port ID TTL\n---- --- --- ---\n"])
def test_parser_returns_empty_for_no_neighbors(text):
    assert parse_lldp_neighbors(text) == []


# ---------- 빌더 ----------

def test_bidirectional_links_are_counted_once(lab):
    """같은 링크가 양쪽 LLDP 표에 각각 나온다 — 두 개로 세면 선이 겹쳐 그려진다."""
    topology = build_topology(*lab)
    pairs = {(e["a"], e["b"]) for e in topology["edges"]}
    assert ("Core1", "Core2") in pairs
    # Core1↔Core2 는 Et1/Et2 두 물리 링크지만 Po4093 하나로 묶여 edge 1개다.
    core_edges = [e for e in topology["edges"] if {e["a"], e["b"]} == {"Core1", "Core2"}]
    assert len(core_edges) == 1
    assert core_edges[0]["count"] == 2


def test_port_channel_members_are_bundled(lab):
    topology = build_topology(*lab)
    edge = next(e for e in topology["edges"] if {e["a"], e["b"]} == {"Core1", "Agg1"})
    assert edge["bundle"] == "Port-Channel2048"
    assert edge["count"] == 2
    assert [m["a_port"] for m in edge["members"]] == ["Ethernet3", "Ethernet4"]


def test_interface_description_becomes_the_link_label(lab):
    topology = build_topology(*lab)
    edge = next(e for e in topology["edges"] if {e["a"], e["b"]} == {"Core1", "Agg1"})
    assert edge["label"] == "core_agg_mlag"


def test_mlag_pair_is_detected_from_peer_link(lab):
    topology = build_topology(*lab)
    assert [(p["a"], p["b"]) for p in topology["pairs"]] == [("Core1", "Core2")]
    assert topology["pairs"][0]["peer_link"] == "Port-Channel4093"
    assert topology["pairs"][0]["healthy"] is True


def test_unregistered_neighbor_is_kept_not_dropped(lab):
    """장비 목록에 없는 이웃을 지우면 문서화되지 않은 연결을 영구히 못 본다."""
    devices, by_device = lab
    devices = [d for d in devices if d["name"] != "Access1"]
    del by_device["Access1"]
    topology = build_topology(devices, by_device)

    ghost = next(n for n in topology["nodes"] if n["name"] == "Access1")
    assert ghost["registered"] is False
    assert any(w["kind"] == "unregistered" for w in topology["warnings"])
    assert any({e["a"], e["b"]} == {"Agg1", "Access1"} for e in topology["edges"])


def test_one_sided_link_is_reported_not_hidden(lab):
    """한쪽에서만 보이는 링크는 단선 신호일 수 있다 — 조용히 버리면 안 된다."""
    devices, by_device = lab
    by_device["Access1"] = sections("")        # Access1 의 LLDP 를 비운다
    topology = build_topology(devices, by_device)

    edge = next(e for e in topology["edges"] if {e["a"], e["b"]} == {"Agg1", "Access1"})
    assert edge["one_sided"] is True
    assert any(w["kind"] == "one_sided" for w in topology["warnings"])


def test_devices_without_logs_are_reported(lab):
    devices, by_device = lab
    devices.append(device("Access9"))
    topology = build_topology(devices, by_device)
    warning = next(w for w in topology["warnings"] if w["kind"] == "no_log")
    assert "Access9" in warning["devices"]


def test_admin_down_port_is_not_a_link(lab):
    """`disabled`(관리자가 내려 둔 포트)는 LLDP 이웃도 없다 — 링크로 만들지 않는다."""
    topology = build_topology(*lab)
    assert all("Ethernet9" not in (e["a_port"], e["b_port"]) for e in topology["edges"])


# ---------- 계층 / 기호 ----------

def test_tier_comes_from_the_name_when_role_is_empty(lab):
    """실제 인벤토리의 role 은 비어 있다 — 이름이 사실상 유일한 자동 근거다."""
    topology = build_topology(*lab)
    tiers = {n["name"]: n["tier"] for n in topology["nodes"]}
    assert tiers["Core1"] == TIER_CORE
    assert tiers["Agg1"] == TIER_AGG
    assert tiers["Access1"] == TIER_ACCESS


def test_role_field_overrides_the_name():
    devices = [device("Box1", role="access"), device("Box2", role="core")]
    topology = build_topology(devices, {"Box1": sections(), "Box2": sections()})
    tiers = {n["name"]: n["tier"] for n in topology["nodes"]}
    assert tiers["Box1"] == TIER_ACCESS and tiers["Box2"] == TIER_CORE


def test_unclassifiable_devices_are_reported_not_guessed():
    """추정으로 틀린 계층을 그리면 사용자는 그림을 믿을 수 없고 무엇이 틀렸는지도 모른다."""
    devices = [device("Box1"), device("Box2")]
    topology = build_topology(devices, {"Box1": sections(), "Box2": sections()})
    assert all(n["tier"] == TIER_UNKNOWN for n in topology["nodes"])
    assert any(w["kind"] == "tier_unknown" for w in topology["warnings"])


def test_l3_and_l2_symbols_are_chosen_from_the_config(lab):
    topology = build_topology(*lab)
    kinds = {n["name"]: n["kind"] for n in topology["nodes"]}
    assert kinds["Core1"] == KIND_L3, "running-config 에 ip routing 이 있다"
    assert kinds["Agg1"] == KIND_L2, "라우팅 근거가 없으면 L2 로 둔다"


# ---------- 배치 ----------

def test_layout_is_deterministic_and_layered(lab):
    topology = build_topology(*lab)
    first = layout(topology)
    second = layout(build_topology(*lab))
    assert [(n["id"], n["x"], n["y"]) for n in topology["nodes"]]
    assert first["rows"][0]["tier"] < first["rows"][-1]["tier"], "위에서 아래로 계층이 쌓인다"
    assert (first["width"], first["height"]) == (second["width"], second["height"])


def test_mlag_pair_is_placed_side_by_side(lab):
    topology = build_topology(*lab)
    layout(topology)
    by_name = {n["name"]: n for n in topology["nodes"]}
    assert by_name["Core1"]["y"] == by_name["Core2"]["y"]
    # 쌍 사이 간격은 다른 노드 사이보다 좁다(한 논리 장비처럼 보이게).
    assert abs(by_name["Core1"]["x"] - by_name["Core2"]["x"]) < 160


def test_manual_positions_override_the_auto_layout(lab):
    """계층 자동 판정이 틀릴 수 있으므로 직접 배치가 반드시 이겨야 한다."""
    topology = build_topology(*lab)
    layout(topology, {"Core1": [900, 700]})
    node = next(n for n in topology["nodes"] if n["name"] == "Core1")
    assert (node["x"], node["y"]) == (900.0, 700.0)
    assert node["manual"] is True
    # 자동 좌표는 남겨 둔다 — '되돌리기'가 가능해야 한다.
    assert node["auto_x"] != 900


def test_canvas_grows_to_fit_moved_nodes(lab):
    topology = build_topology(*lab)
    info = layout(topology, {"Core1": [2000, 1500]})
    assert info["width"] > 2000 and info["height"] > 1500


# ---------- SVG ----------

def test_svg_is_well_formed_and_complete(lab):
    topology = build_topology(*lab)
    info = layout(topology)
    svg = render_svg(topology, info)
    root = ET.fromstring(svg)          # 깨진 XML 이면 여기서 실패한다

    nodes = root.findall(".//*[@data-tp-node]")
    links = root.findall(".//*[@data-tp-link]")
    assert len(nodes) == len(topology["nodes"])
    assert len(links) == len(topology["edges"])
    assert root.findall(".//*[@data-tp-pair]"), "MLAG 쌍 상자가 그려져야 한다"


def test_svg_always_has_a_legend(lab):
    """기호를 쓰는 그림에 범례가 없으면 규칙이 전달되지 않는다."""
    topology = build_topology(*lab)
    svg = render_svg(topology, layout(topology))
    assert 'class="tp-legend"' in svg
    assert "범례" in svg


def test_down_link_gets_a_shape_not_only_a_color(lab):
    """색만으로 구별하면 색약·흑백 인쇄에서 사라진다 — DOWN 은 ✕ 마커를 함께 그린다."""
    topology = build_topology(*lab)
    for edge in topology["edges"]:
        edge["state"] = "down"
    svg = render_svg(topology, layout(topology))
    assert "tp-mark-down" in svg
    assert "tp-link-down" in svg


def test_bundle_is_drawn_thicker_with_its_name(lab):
    topology = build_topology(*lab)
    svg = render_svg(topology, layout(topology))
    assert "tp-link-bundle" in svg
    assert "Po2048 ×2" in svg


def test_standalone_svg_embeds_its_own_colors(lab):
    """내보낸 파일은 앱 밖에서 열리므로 CSS 변수를 해석해 줄 주체가 없다."""
    topology = build_topology(*lab)
    info = layout(topology)
    inline = render_svg(topology, info, standalone=True)
    in_app = render_svg(topology, info)
    assert "<style>" in inline and "#DC2626" in inline
    assert "<style>" not in in_app, "화면에서는 style.css 의 테마 토큰을 따라간다"
    ET.fromstring(inline)


def test_unregistered_node_uses_the_dashed_symbol(lab):
    devices, by_device = lab
    devices = [d for d in devices if d["name"] != "Access1"]
    del by_device["Access1"]
    topology = build_topology(devices, by_device)
    svg = render_svg(topology, layout(topology))
    assert "tp-sym-unknown" in svg
    assert "tp-node-unregistered" in svg


# ---------- 실시간 상태 오버레이 ----------

def _edge(**kw):
    base = {"id": "e1", "a": "Core1", "b": "Agg1", "a_port": "Ethernet3",
            "b_port": "Ethernet3", "count": 2, "state": "up",
            "members": [{"a_port": "Ethernet3", "b_port": "Ethernet3", "state": "up"},
                        {"a_port": "Ethernet4", "b_port": "Ethernet4", "state": "up"}]}
    base.update(kw)
    return base


@pytest.mark.parametrize("live,expected", [
    # 감시가 꺼져 있으면 점검 시점의 판정을 그대로 보여준다.
    ({"running": False, "down": [["Core1", "Ethernet3"]], "observed": ["Core1"]}, "up"),
    ({"running": True, "down": [], "observed": ["Core1", "Agg1"]}, "up"),
    # 묶음 일부만 down = 이중화가 이미 깎였다. 전부 down 과 구별해야 한다.
    ({"running": True, "down": [["Core1", "Ethernet3"]], "observed": ["Core1"]}, "degraded"),
    ({"running": True, "down": [["Core1", "Ethernet3"], ["Agg1", "Ethernet4"]],
      "observed": ["Core1"]}, "down"),
    # 상태를 관측할 수 없으면 '정상'이라고 말하지 않는다.
    ({"running": True, "down": [], "observed": []}, "unknown"),
])
def test_live_state_overlay(live, expected):
    from api.topology_api import _apply_live_state

    topology = {"edges": [_edge()]}
    _apply_live_state(topology, live)
    assert topology["edges"][0]["state"] == expected


def test_live_overlay_uses_the_same_component_axis_as_realtime_watch():
    """실시간 감시의 component_id 는 정규화된 인터페이스명이라 이름 변환 없이 맞물린다 —
    두 곳에서 따로 판정하면 '구성도는 빨간데 감시는 정상'인 화면이 나온다."""
    from api.topology_api import _apply_live_state
    from engine.baseline_diff_engine import BaselineDiffEngine

    class Store:
        def get_device_baseline(self, device):
            return {"vlans": set(), "interfaces": {"Ethernet3"},
                    "routes": set(), "bgp_neighbors": set()}

    engine = BaselineDiffEngine(Store())
    engine.analyze_stream("Core1", "%LINEPROTO-5-UPDOWN: Line protocol on Interface "
                                   "Ethernet3, changed state to down\n")
    live = {"running": True, "observed": ["Core1"],
            "down": [[c["device"], c["component_id"]] for c in engine.open_conditions()
                     if c["condition"] == "interface_down"]}
    topology = {"edges": [_edge()]}
    _apply_live_state(topology, live)
    assert topology["edges"][0]["state"] == "degraded"
    assert topology["edges"][0]["members"][0]["state"] == "down"
