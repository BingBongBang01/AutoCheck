"""명령·프롬프트 인식 — IMPROVEMENT_PLAN 2-10.

  1. 축약 명령(`int et2`, `shut`, `no shut`, `no int et3`)을 놓쳤다.
  2. Cisco IOS/NX-OS 의 '(config-if)#' 프롬프트가 인터페이스 문맥을 지워서 shutdown 을 추적하지 못했다.
  3. 문맥이 이미 있으면 프롬프트를 무시해서, 놓친 전환 뒤의 shutdown 이 엉뚱한 인터페이스에 붙었다.
  4. 파일명을 '-' 로도 쪼개 부분 일치로 판정해서 'Core1-B_x.log' 가 Core1 로 귀속됐다.
"""
import pytest

from engine.baseline_diff_engine import BaselineDiffEngine
from engine.stream_device_matcher import StreamDeviceMatcher


class FakeStore:
    def get_device_baseline(self, device):
        return {"vlans": {"100"}, "interfaces": {"Ethernet2", "Ethernet3", "GigabitEthernet0/1"},
                "routes": set(), "bgp_neighbors": set()}


def targets(alerts):
    return [(a["type"], a.get("target")) for a in alerts]


def shutdown_targets(text):
    engine = BaselineDiffEngine(FakeStore())
    return [t for t in targets(engine.analyze_stream("Core1", text)) if t[0] != "CONFIG_REMOVED"]


@pytest.mark.parametrize("text", [
    "Core1(config)#int et2\nCore1(config-if-Et2)#shut\n",
    "Core1(config)#interface Ethernet2\nCore1(config-if-Et2)#shutdown\n",
    "Core1(config)#inte Ethernet2\nCore1(config-if-Et2)#shutd\n",
])
def test_abbreviated_interface_and_shutdown_are_recognized(text):
    found = shutdown_targets(text)
    assert found and all(target.endswith("Ethernet2") for _, target in found)


def test_abbreviated_no_interface_is_a_removal():
    engine = BaselineDiffEngine(FakeStore())
    alerts = engine.analyze_stream("Core1", "Core1(config)#no int et3\n")
    assert [(a["type"], a["target"], a["severity"]) for a in alerts] == [
        ("CONFIG_REMOVED", "interface:Ethernet3", "CRITICAL")]


def test_cisco_config_if_prompt_keeps_the_interface():
    found = shutdown_targets("R1(config)#interface GigabitEthernet0/1\nR1(config-if)#shutdown\n")
    assert found and all(target.endswith("GigabitEthernet0/1") for _, target in found)


def test_a_prompt_naming_another_interface_wins():
    # 'interface range' 처럼 인식하지 못한 전환 뒤에도 프롬프트가 지금 인터페이스를 알려 준다.
    found = shutdown_targets("Core1(config)#int et2\nCore1(config-if-Et2)#interface range et3\n"
                             "Core1(config-if-Et3)#shutdown\n")
    assert found and all(target.endswith("Ethernet3") for _, target in found)


@pytest.mark.parametrize("names, filename, expected", [
    (["Core1"], "Core1-B_20261027.log", None),                 # Core1-B 는 다른 장비다
    (["Core1", "Core1-B"], "Core1-B_20261027.log", "Core1-B"),
    (["Core1"], "AutoCheck_Core1_20261027_101500.txt", "Core1"),
    (["SEOUL-CORE-01"], "SEOUL-CORE-01_20261027.log", "SEOUL-CORE-01"),
])
def test_filename_tokens_keep_hyphenated_hostnames(names, filename, expected):
    matcher = StreamDeviceMatcher([{"name": n} for n in names])
    assert matcher.resolve(filename) == expected


def test_hyphen_dated_files_are_still_identified_by_their_prompt():
    matcher = StreamDeviceMatcher([{"name": "Core1"}])
    assert matcher.resolve("Core1-20261027.log", "Core1#show version\n") == "Core1"
