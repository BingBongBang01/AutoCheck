"""세션 로그의 '작업자 입력'과 '장비 출력'을 가르지 않으면 감시가 두 방향으로 틀린다.

실제 워크스페이스에서 확인된 것:
  * 오탐 — `show reload cause` 출력의 머리글 `Reload Cause:` 가 CRITICAL '위험 명령 실행'으로
    저장돼 있었다(5일간 남은 유일한 경고가 이것이었다). `?` 도움말의
    `  reload   Reboot the system` 도 같은 경고를 낸다.
  * **오취소** — `show running-config` 출력에는 장비마다 `   no shutdown` 2줄과 `vlan N` 4~8줄이
    들어 있다. 그것이 복구 이벤트로 읽혀, 작업자가 실제로 낸 CRITICAL 경고를 '복구됨'으로
    지웠다. 설정을 한 번 들여다보는 것이 감시를 무력화하는 경로였다.

가르는 근거는 프롬프트다(입력은 항상 프롬프트와 같은 줄에 있다). 그래서 이 파일은
'무엇이 명령으로 읽히는가'와 '출력이 상태를 바꿀 수 있는가'를 본다.
"""
import pytest

from engine.baseline_diff_engine import (COMMAND, OUTPUT, BaselineDiffEngine,
                                         classify_line)

# show running-config 출력에서 그대로 뽑은 조각 — 실제 점검 로그와 같은 모양이다.
RUNNING_CONFIG_DUMP = """\
vlan 100
   name DATA
!
interface Ethernet1
   switchport mode trunk
   no shutdown
!
interface Management1
   vrf MGMT
   ip address 192.168.205.101/21
   no shutdown
!
ip route vrf MGMT 0.0.0.0/0 192.168.200.1
!
end
"""


class FakeStore:
    """Baseline 에 vlan 100 / Ethernet1 이 등록돼 있는 장비."""

    def get_device_baseline(self, device):
        return {"vlans": {"100", "200"}, "interfaces": {"Ethernet1", "Management1"},
                "routes": {"0.0.0.0/0"}, "bgp_neighbors": {"10.1.1.2"}}


@pytest.fixture
def engine():
    return BaselineDiffEngine(FakeStore())


# ---------- 분류 자체 ----------

@pytest.mark.parametrize("line", [
    "Reload Cause:",                                    # show 출력 머리글
    "  reload     Reboot the system",                   # ? 자동완성 도움말
    "  reload-delay        Delay (seconds) after reboot",
    "   no shutdown",                                   # running-config 덤프
    "   shutdown",
    "vlan 200",
    "interface Ethernet1",
    "no aaa root",
    "Last login: Fri Aug  7 10:12:54 2026 from 172.16.103.206",
    " 2048            active-full    Po2048     Po2048          up/up",
])
def test_device_output_is_never_a_command(line):
    kind, _payload, _mode = classify_line(line)
    assert kind is OUTPUT


@pytest.mark.parametrize("line,cmd,mode", [
    ("Core1#show version", "show version", None),
    ("Core1(config)#no vlan 100", "no vlan 100", "config"),
    ("Core1(config-if-Et1)#shutdown", "shutdown", "config-if-Et1"),
    ("Core1(config-s-reset)#no vlan 4093", "no vlan 4093", "config-s-reset"),
    ("Core1#", "", None),                               # 엔터만 침
])
def test_prompt_lines_are_commands(line, cmd, mode):
    kind, payload, got_mode = classify_line(line)
    assert (kind, payload, got_mode) == (COMMAND, cmd, mode)


# ---------- 오탐이 사라진다 ----------

def test_show_reload_cause_output_raises_nothing(engine):
    """실제로 저장돼 있던 유일한 경고가 이것이었다."""
    alerts = engine.analyze_stream("Core1", "Core1(config)#show reload cause\n"
                                            "Reload Cause:\n"
                                            "-------------\n"
                                            "The system rebooted due to unknown reasons.\n")
    assert alerts == []


def test_completion_help_output_raises_nothing(engine):
    alerts = engine.analyze_stream("Core1", "Core1(config)#clear ?\n"
                                            "  reload     Reboot the system\n"
                                            "  counters   Clear counters\n")
    assert alerts == []


def test_running_config_dump_raises_nothing(engine):
    alerts = engine.analyze_stream("Core1", "Core1#show running-config\n" + RUNNING_CONFIG_DUMP)
    assert alerts == []


def test_real_commands_are_still_detected(engine):
    alerts = engine.analyze_stream("Core1", "Core1(config)#no vlan 100\n"
                                            "Core1(config)#reload\n")
    assert [(a["type"], a["severity"]) for a in alerts] == [
        ("CONFIG_REMOVED", "CRITICAL"), ("DESTRUCTIVE_COMMAND", "CRITICAL")]


# ---------- 오취소가 사라진다 ----------

def test_running_config_does_not_cancel_real_alerts(engine):
    """가장 심각한 버그: 설정을 들여다보는 것이 진행 중인 경고를 지웠다."""
    opened = engine.analyze_stream("Core1", "Core1(config)#no vlan 100\n"
                                            "Core1(config)#interface Ethernet1\n"
                                            "Core1(config-if-Et1)#shutdown\n")
    assert len(opened) == 2
    before = {(c["component_id"], c["condition"]) for c in engine.open_conditions("Core1")}
    assert before == {("vlan:100", "config_removed"), ("Ethernet1", "interface_down")}

    engine.analyze_stream("Core1", "Core1#show running-config\n" + RUNNING_CONFIG_DUMP)

    assert engine.drain_resolutions() == []
    after = {(c["component_id"], c["condition"]) for c in engine.open_conditions("Core1")}
    assert after == before


def test_show_mlag_output_cannot_resolve_but_syslog_can(engine):
    """조회 출력에서 온 '복구'는 받지 않는다 — 틀린 UP 은 진짜 장애를 조용히 지운다."""
    down = engine.analyze_stream("Core1", "%MLAG-3-PEER_LINK_DOWN: MLAG peer-link is down\n")
    assert [a["type"] for a in down] == ["MLAG_PEER_DOWN"]

    engine.analyze_stream("Core1", "Core1#show mlag\nPeer-link status: up\nState: active-full\n")
    assert engine.drain_resolutions() == []
    assert len(engine.open_conditions("Core1")) == 1

    engine.analyze_stream("Core1", "%MLAG-5-STATE_CHANGE: peer-link status changed to active-full\n")
    assert len(engine.drain_resolutions()) == 1
    assert engine.open_conditions("Core1") == []


def test_show_mlag_output_still_reports_a_problem(engine):
    """반대 방향은 막지 않는다 — 조회 출력에서 읽은 이상은 근거로 유효하다."""
    alerts = engine.analyze_stream("Core1", "Core1#show mlag\nPeer-link status: down\n")
    assert [a["type"] for a in alerts] == ["MLAG_PEER_DOWN"]
    assert alerts[0]["from_show_output"] is True


# ---------- 프롬프트가 config 문맥의 단일 출처 ----------

def test_prompt_supplies_interface_context(engine):
    """`interface Ethernet1` 줄을 못 봤어도 프롬프트가 대상을 알려준다."""
    alerts = engine.analyze_stream("Core1", "Core1(config-if-Et1)#shutdown\n")
    assert [(a["type"], a["target"]) for a in alerts] == [
        ("INTERFACE_SHUTDOWN", "shutdown:Ethernet1")]


def test_leaving_config_mode_drops_stale_interface_context(engine):
    """프롬프트가 특권 모드로 돌아왔으면 인터페이스 문맥은 끝났다 —
    남겨 두면 뒤에 오는 shutdown 이 엉뚱한 인터페이스에 귀속된다."""
    engine.analyze_stream("Core1", "Core1(config)#interface Ethernet1\n")
    engine.analyze_stream("Core1", "Core1#show version\n")
    alerts = engine.analyze_stream("Core1", "Core1(config)#shutdown\n")
    assert [(a["type"], a["target"]) for a in alerts] == [("INTERFACE_SHUTDOWN", "shutdown:?")]


def test_running_config_cannot_set_interface_context(engine):
    """running-config 출력의 `interface Ethernet1` 수십 줄이 문맥을 훔치면 안 된다."""
    engine.analyze_stream("Core1", "Core1#show running-config\n" + RUNNING_CONFIG_DUMP)
    alerts = engine.analyze_stream("Core1", "Core1(config)#shutdown\n")
    assert alerts[0]["target"] == "shutdown:?"


# ---------- 과거 기록을 지금 발생으로 세지 않는다 ----------

def test_show_logging_output_is_marked_history(engine):
    """`show logging` 은 며칠 전 이벤트를 그대로 다시 뿌린다."""
    alerts = engine.analyze_stream(
        "Core1",
        "Core1#show logging\n"
        "%LINEPROTO-5-UPDOWN: Line protocol on Interface Ethernet1, changed state to down\n")
    assert [a["type"] for a in alerts] == ["LINK_DOWN"]
    assert alerts[0]["history"] is True


def test_live_syslog_is_not_marked_history(engine):
    alerts = engine.analyze_stream(
        "Core1",
        "%LINEPROTO-5-UPDOWN: Line protocol on Interface Ethernet1, changed state to down\n")
    assert alerts[0].get("history") is not True
