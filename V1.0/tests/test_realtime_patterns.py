"""오탐·미탐 패턴 — IMPROVEMENT_PLAN 2-11 (코드로 확인할 수 있는 것만. 실기 로그 코퍼스 검증은 따로).

  미탐: EOS BGP 'old state Established … new state Idle'(Down/Up 단어 없음), Cisco %LINK-3-UPDOWN,
        NX-OS %ETHPORT, 규칙의 ADJCHG 서명이 BGP ADJCHANGE 를 못 잡음
  오탐: OSPF 인접 '수립'(to FULL)도 CRITICAL, `copy run start`·`reload cancel` 을 위험 명령으로,
        상태 폴링이 MLAG Disabled(미구성)를 split-brain 으로, 묶어 낸 DOWN 이 영영 해제되지 않음
"""
import pytest

from engine.baseline_diff_engine import BaselineDiffEngine
from engine.log_rule_engine import ContextTracker, RuleEngine, load_rules
from engine.state_poller import StatePoller, _is_down


class FakeStore:
    def get_device_baseline(self, device):
        return {"vlans": set(), "interfaces": {"Ethernet1", "GigabitEthernet0/1", "Ethernet1/1"},
                "routes": set(), "bgp_neighbors": {"10.1.1.2"}}


def judge(line):
    engine = BaselineDiffEngine(FakeStore())
    return [(a["type"], a.get("target"), a["severity"]) for a in engine.analyze_stream("Core1", line + "\n")]


@pytest.mark.parametrize("line, expected", [
    ("%BGP-5-ADJCHANGE: peer 10.1.1.2 (VRF default AS 65001) old state Established event Stop new state Idle",
     ("NEIGHBOR_DOWN", "peer:10.1.1.2", "CRITICAL")),
    ("%BGP-5-ADJCHANGE: peer 10.1.1.2 (VRF default AS 65001) old state OpenConfirm event Est new state Established",
     ("NEIGHBOR_UP", "peer:10.1.1.2", "WARNING")),
    ("%LINK-3-UPDOWN: Interface GigabitEthernet0/1, changed state to down",
     ("LINK_DOWN", "link:GigabitEthernet0/1", "CRITICAL")),
    ("%ETHPORT-5-IF_DOWN_LINK_FAILURE: Interface Ethernet1/1 is down (Link failure)",
     ("LINK_DOWN", "link:Ethernet1/1", "CRITICAL")),
])
def test_syslog_formats_from_other_vendors_and_eos(line, expected):
    assert expected in judge(line)


def test_nxos_link_up_is_a_recovery():
    assert all(t != "LINK_DOWN" for t, _target, _sev in judge("%ETHPORT-5-IF_UP: Interface Ethernet1/1 is up in mode access"))


@pytest.mark.parametrize("command, destructive", [
    ("copy running-config startup-config", False), ("copy run start", False), ("reload cancel", False),
    ("reload", True), ("reload in 10", True), ("copy flash:old.cfg startup-config", True), ("write erase", True),
])
def test_destructive_commands(command, destructive):
    found = any(t == "DESTRUCTIVE_COMMAND" for t, _target, _sev in judge(f"Core1#{command}"))
    assert found is destructive


@pytest.fixture(scope="module")
def rules():
    return RuleEngine(load_rules())


@pytest.mark.parametrize("line, lost", [
    ("%OSPF-5-ADJCHG: Process 1, Nbr 10.0.0.2 on Ethernet1 from LOADING to FULL, Loading Done", False),
    ("%OSPF-5-ADJCHG: Process 1, Nbr 10.0.0.2 on Ethernet1 from FULL to DOWN, Neighbor Down", True),
    ("%BGP-5-ADJCHANGE: neighbor 10.1.1.2 Down BGP Notification sent", True),
    ("%BGP-5-ADJCHANGE: neighbor 10.1.1.2 Up", False),
    ("%BGP-5-ADJCHANGE: peer 10.1.1.2 (VRF default AS 65001) old state Established event Stop new state Idle", True),
])
def test_routing_adjacency_rule_looks_at_the_direction(rules, line, lost):
    verdict = rules.evaluate(line, ContextTracker())
    assert ((verdict or {}).get("rule_id") == "routing_adjacency_lost") is lost


def test_unconfigured_mlag_is_not_split_brain():
    assert _is_down("mlag", "Disabled") is False
    assert _is_down("mlag", "Active/Connected/Up") is False
    assert _is_down("mlag", "Inactive/Disconnected/Down") is True


def test_a_merged_link_down_is_resolved_when_every_port_is_back():
    poller = StatePoller(lambda: [], lambda device, events: None)
    ports = [f"Ethernet{i}" for i in range(1, 9)]
    up = {"link": {p: "connected" for p in ports}}
    down = {"link": {p: "notconnect" for p in ports}}
    poller._diff("Core1", up, first=True)
    poller._prev["Core1"] = up
    merged = poller._diff("Core1", down, first=False)
    assert [(e["subject"], e["down"]) for e in merged] == [("다수 인터페이스", True)]
    poller._prev["Core1"] = down
    half = {"link": {p: ("connected" if i < 4 else "notconnect") for i, p in enumerate(ports)}}
    events = poller._diff("Core1", half, first=False)
    assert not any(e["subject"] == "다수 인터페이스" for e in events)   # 아직 다 안 올라왔다
    poller._prev["Core1"] = half
    events = poller._diff("Core1", up, first=False)
    assert ("다수 인터페이스", False) in [(e["subject"], e["down"]) for e in events]
