"""능동 상태 폴링 — 세션 로그로는 볼 수 없는 것을 직접 물어본다.

배경: 실시간 감시의 원래 입력은 SecureCRT 세션 로그 tail 하나였다. 그것으로 링크·라우팅
인접·MLAG 를 알려면 syslog 가 세션에 에코돼야 하고(`terminal monitor`), 실제 워크스페이스의
CRT 세션 로그 60여 개에는 syslog 가 **한 줄도 없었다**. 즉 체크리스트 7항목 중 3개가 작업자의
터미널 설정 때문에 구조적으로 판정 불가였다.

이 파일이 보는 것:
  1. 전이만 보고한다(절대 상태를 매번 올리지 않는다). 첫 폴링은 기준을 세우며 history 로 표시.
  2. 판정은 BaselineDiffEngine 을 통과한다 — 그래야 출처가 달라도 서로 취소된다
     (폴링이 잡은 LINK_DOWN 을 나중에 온 syslog LINK_UP 이 해제한다).
  3. 조용한 폴링도 관측으로 센다 — '봤고 정상이다'가 체크리스트를 판정 불가에서 풀어 준다.
"""
import pytest

from engine.baseline_diff_engine import BaselineDiffEngine
from engine.realtime_monitor import RealtimeMonitor
from engine.state_poller import StatePoller, _normalize

STATUS_OK = """\
Port       Name   Status       Vlan    Duplex Speed Type
Et1               connected    100     full   10G   10GBASE-SR
Et2               connected    trunk   full   10G   10GBASE-SR
Et3               disabled     100     full   10G   10GBASE-SR
"""
STATUS_ET1_DOWN = STATUS_OK.replace("Et1               connected", "Et1               notconnect")

# Arista `show ip bgp summary` 실제 레이아웃(피어 줄에 상태와 PfxRcd 가 함께 온다).
BGP_OK = """\
BGP summary information for VRF default
Router identifier 10.0.0.1, local AS number 65001
  Neighbor V AS           MsgRcvd   MsgSent  InQ OutQ  Up/Down State   PfxRcd PfxAcc
  10.1.1.2 4 65002            123       456    0    0 01:02:03 Estab   42     42
"""
BGP_DOWN = BGP_OK.replace("01:02:03 Estab   42     42", "01:02:03 Idle")

# Arista `show mlag` 실제 레이아웃 — 필드명이 소문자이고 값이 오른쪽 정렬돼 있다.
# (workspace 의 20260807_095325_raw_Core1.txt 에서 그대로 가져왔다.)
MLAG_OK = """\
MLAG Configuration:
domain-id                          :                4093
peer-link                          :    Port-Channel4093

MLAG Status:
state                              :              Active
negotiation status                 :           Connected
peer-link status                   :                  Up
local-int status                   :                  Up
"""
MLAG_DOWN = MLAG_OK.replace("state                              :              Active",
                            "state                              :            Inactive")


class FakeStore:
    def get_device_baseline(self, device):
        return {"vlans": set(), "interfaces": {"Ethernet1", "Ethernet2"},
                "routes": set(), "bgp_neighbors": {"10.1.1.2"}}


class ScriptedPoller(StatePoller):
    """SSH 없이 돌리기 위해 _collect 만 대역으로 바꾼 폴러 — 나머지 로직은 실물 그대로다."""

    def __init__(self, script, **kwargs):
        super().__init__(lambda: [{"name": "Core1", "ip": "10.0.0.1"}], self._record, **kwargs)
        self.script = list(script)
        self.seen = []

    def _record(self, device, events):
        self.seen.append((device, events))

    def _collect(self, target):
        return _normalize(self.script.pop(0))


def events_of(poller):
    return [e for _device, batch in poller.seen for e in batch]


# ---------- 1. 전이만 보고한다 ----------

def test_first_poll_reports_current_problems_as_history():
    """랩/현장에는 원래 내려가 있는 포트가 흔하다 — 알리되 '방금 일어난 일'로 세지 않는다."""
    poller = ScriptedPoller([{"link": STATUS_ET1_DOWN, "mlag": MLAG_OK, "bgp": BGP_OK}])
    poller.poll_once()

    events = events_of(poller)
    assert [(e["kind"], e["subject"], e["down"]) for e in events] == [("link", "Et1", True)]
    assert events[0]["history"] is True


def test_first_poll_is_quiet_when_everything_is_healthy():
    poller = ScriptedPoller([{"link": STATUS_OK, "mlag": MLAG_OK, "bgp": BGP_OK}])
    poller.poll_once()
    assert events_of(poller) == []
    # 조용해도 콜백은 불려야 한다 — '봤고 정상이다'가 전달되어야 하기 때문이다.
    assert poller.seen == [("Core1", [])]


def test_second_poll_reports_only_the_change():
    poller = ScriptedPoller([
        {"link": STATUS_OK, "mlag": MLAG_OK, "bgp": BGP_OK},
        {"link": STATUS_ET1_DOWN, "mlag": MLAG_OK, "bgp": BGP_OK},
    ])
    poller.poll_once()
    poller.poll_once()

    events = events_of(poller)
    assert [(e["kind"], e["subject"], e["down"], e["history"]) for e in events] == [
        ("link", "Et1", True, False)]


def test_recovery_is_reported_too():
    poller = ScriptedPoller([
        {"link": STATUS_ET1_DOWN},
        {"link": STATUS_OK},
    ])
    poller.poll_once()
    poller.poll_once()
    assert [(e["subject"], e["down"]) for e in events_of(poller)] == [("Et1", True), ("Et1", False)]


def test_steady_state_is_not_repeated():
    """같은 이상이 계속 보여도 매 주기 다시 올리지 않는다 — 전이만 본다."""
    poller = ScriptedPoller([{"link": STATUS_ET1_DOWN}] * 3)
    for _ in range(3):
        poller.poll_once()
    assert len(events_of(poller)) == 1


def test_admin_down_ports_are_ignored():
    """관리자가 내려 둔 포트(disabled)는 의도된 상태다 — 장애가 아니다."""
    poller = ScriptedPoller([{"link": STATUS_OK}])
    poller.poll_once()
    assert all(e["subject"] != "Et3" for e in events_of(poller))


def test_bgp_and_mlag_transitions():
    poller = ScriptedPoller([
        {"bgp": BGP_OK, "mlag": MLAG_OK},
        {"bgp": BGP_DOWN, "mlag": MLAG_DOWN},
    ])
    poller.poll_once()
    poller.poll_once()
    got = {(e["kind"], e["subject"], e["down"]) for e in events_of(poller)}
    assert got == {("neighbor", "10.1.1.2", True), ("mlag", "peer-link", True)}


def test_mass_link_transition_is_merged():
    """장비 재부팅이면 포트 수십 개가 동시에 내려간다 — 원인은 하나이므로 한 줄로 묶는다."""
    up = "Port Name Status\n" + "".join(f"Et{i}   connected  100 full 10G x\n" for i in range(1, 13))
    down = up.replace("connected", "notconnect")
    poller = ScriptedPoller([{"link": up}, {"link": down}])
    poller.poll_once()
    poller.poll_once()

    events = events_of(poller)
    assert len(events) == 1
    assert events[0]["subject"] == "다수 인터페이스"
    assert "12개 포트" in events[0]["detail"]


def test_real_arista_mlag_output_is_read_as_healthy():
    """workspace 의 20260807_095326_raw_Agg1.txt 에서 그대로 가져온 형태 —
    필드명이 소문자이고 값이 오른쪽 정렬이다. 이것을 못 읽으면 MLAG 감시가 조용히 죽는다."""
    from engine.state_poller import _is_down

    state = _normalize({"mlag": MLAG_OK})["mlag"]["peer-link"]
    assert state == "Active/Connected/Up"
    assert _is_down("mlag", state) is False
    assert _is_down("mlag", _normalize({"mlag": MLAG_DOWN})["mlag"]["peer-link"]) is True


@pytest.mark.parametrize("text,expected", [
    # Arista: IP 다음이 VRF 이름이라 컬럼 정렬로는 못 읽는다(그래서 상태 단어로 읽는다).
    ("Neighbor ID     VRF       Pri State                  Dead Time   Address\n"
     "10.0.0.2        default   1   FULL/DR                00:00:33    10.1.1.2\n"
     "10.0.0.3        default   1   EXSTART/BDR            00:00:31    10.1.1.6\n",
     {"10.0.0.2": "FULL", "10.0.0.3": "EXSTART"}),
    # Cisco: IP 다음이 Pri(숫자).
    ("Neighbor ID     Pri   State           Dead Time   Address\n"
     "10.0.0.2          1   FULL/DR         00:00:33    10.1.1.2\n",
     {"10.0.0.2": "FULL"}),
])
def test_ospf_layouts_from_both_vendors(text, expected):
    from engine.state_poller import _parse_ospf_peers

    assert _parse_ospf_peers(text) == expected


def test_unparseable_output_yields_nothing_rather_than_guessing():
    """`% BGP inactive`(이 랩의 실제 응답)처럼 읽을 것이 없으면 아무 상태도 만들지 않는다 —
    억지로 판정하면 구성되지 않은 기능이 장애로 잡힌다."""
    assert "bgp" not in _normalize({"bgp": "% BGP inactive\n"})
    assert "ospf" not in _normalize({"ospf": "\n"})


def test_reset_reseeds():
    poller = ScriptedPoller([{"link": STATUS_ET1_DOWN}, {"link": STATUS_ET1_DOWN}])
    poller.poll_once()
    poller.reset()
    poller.poll_once()
    # 기준을 버렸으므로 두 번째도 첫 폴링처럼 history 로 다시 알린다.
    assert [e["history"] for e in events_of(poller)] == [True, True]


# ---------- 2. 판정 파이프라인 통합 ----------

@pytest.fixture
def engine():
    return BaselineDiffEngine(FakeStore())


def test_polled_events_become_alerts_on_the_same_axis(engine):
    alerts = engine.ingest_state_events("Core1", [
        {"kind": "link", "subject": "Et1", "down": True, "detail": "notconnect",
         "source": "show interfaces status"}])

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["type"] == "LINK_DOWN"
    assert alert["severity"] == "CRITICAL", "Baseline 등록 인터페이스이므로 CRITICAL"
    assert alert["target"] == "link:Ethernet1", "diff 엔진과 같은 target 축이어야 한다"
    assert alert["component_id"] == "Ethernet1"
    assert alert["polled"] is True
    assert "show interfaces status" in alert["raw_line"], "근거가 된 명령이 보여야 한다"


def test_syslog_resolves_a_polled_alert(engine):
    """출처가 달라도 같은 구성요소이면 서로 취소된다 — 이것이 별도 alert 경로를 두지 않은 이유다."""
    engine.ingest_state_events("Core1", [
        {"kind": "link", "subject": "Et1", "down": True, "detail": "notconnect"}])
    assert len(engine.open_conditions("Core1")) == 1

    engine.analyze_stream("Core1", "%LINEPROTO-5-UPDOWN: Line protocol on Interface "
                                   "Ethernet1, changed state to up\n")
    assert len(engine.drain_resolutions()) == 1
    assert engine.open_conditions("Core1") == []


def test_polling_resolves_a_syslog_alert(engine):
    """반대 방향도 된다 — syslog 로 잡힌 장애를 폴링이 '이제 정상'으로 확인해 해제한다."""
    engine.analyze_stream("Core1", "%LINEPROTO-5-UPDOWN: Line protocol on Interface "
                                   "Ethernet1, changed state to down\n")
    engine.ingest_state_events("Core1", [
        {"kind": "link", "subject": "Et1", "down": False, "detail": "connected"}])
    assert len(engine.drain_resolutions()) == 1
    assert engine.open_conditions("Core1") == []


def test_history_events_do_not_claim_to_be_now(engine):
    alerts = engine.ingest_state_events("Core1", [
        {"kind": "link", "subject": "Et1", "down": True, "history": True}])
    assert alerts[0]["history"] is True
    assert alerts[0]["ts"] == "--:--:--"
    # 판정은 그대로 돈다 — 지금 실제로 내려가 있는 상태이므로 상태표는 열려야 한다.
    assert len(engine.open_conditions("Core1")) == 1


# ---------- 3. 관측으로 센다 ----------

def test_quiet_poll_unblocks_the_checklist(engine):
    monitor = RealtimeMonitor()
    monitor.reset(["Core1"], ["Core1"])
    blocked = {c["key"]: c["status"] for c in monitor.state()["devices"][0]["checklist"]}
    assert blocked["link"] == "unknown" and blocked["stp_mlag"] == "unknown"

    engine.ingest_state_events("Core1", [])          # 조용한 폴링
    monitor.set_observations(engine.observations())

    rows = {c["key"]: c for c in monitor.state()["devices"][0]["checklist"]}
    assert rows["link"]["status"] == "pending"
    assert rows["stp_mlag"]["status"] == "pending"
    assert rows["link"]["detail"] == "변경 없음"


def test_blocked_reason_mentions_both_missing_sources():
    monitor = RealtimeMonitor()
    monitor.reset(["Core1"], ["Core1"])
    monitor.set_observations({"Core1": {"commands": 5, "syslog": 0, "output": 9, "polled": 0}})
    detail = next(c["detail"] for c in monitor.state()["devices"][0]["checklist"]
                  if c["key"] == "link")
    assert "syslog" in detail and "폴링" in detail
