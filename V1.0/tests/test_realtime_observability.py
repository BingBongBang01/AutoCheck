"""볼 수 없는 것을 '정상'이라고 표시하면 안 된다.

실제 워크스페이스에서 확인된 것: CRT 세션 로그 60여 개(5일치, 오늘 파일만 2,977줄)에 syslog
줄이 **단 하나도 없었다**. SecureCRT 세션에 `terminal monitor` 가 걸려 있지 않으면 당연한
결과다. 그런데 링크 DOWN / 라우팅 인접 / STP·MLAG 판정은 전부 syslog 에서만 나온다 —
즉 체크리스트 7항목 중 3개는 구조적으로 영원히 '변경 없음(정상)'이었다.

이 화면은 '정상'이라고 적힌 것을 근거로 점검을 마무리하는 데 쓰이므로, 규칙이 한 번도 걸리지
않은 것과 검사해서 통과한 것을 같은 칸에 담으면 안 된다. 그래서 이 파일은 '무엇을 볼 수
있었는가'가 체크리스트/요약에 반영되는지를 본다.
"""
import pytest

from engine.baseline_diff_engine import BaselineDiffEngine
from engine.realtime_monitor import RealtimeMonitor

# 항목 -> 판정에 필요한 입력원. syslog 만으로 판정되는 항목이 이 이야기의 핵심이다.
SYSLOG_ONLY = ("link", "stp_mlag")
COMMAND_ITEMS = ("vlan", "interface", "route", "ops")


class FakeStore:
    def get_device_baseline(self, device):
        return {"vlans": {"100"}, "interfaces": {"Ethernet1"},
                "routes": set(), "bgp_neighbors": {"10.1.1.2"}}


def items(state, device="Core1"):
    entry = next(d for d in state["devices"] if d["device"] == device)
    return {c["key"]: c for c in entry["checklist"]}


@pytest.fixture
def monitor():
    m = RealtimeMonitor()
    m.reset(["Core1"], ["Core1"])       # Baseline 은 있는 상태(그쪽 이유를 배제한다)
    return m


# ---------- 엔진이 '무엇을 봤는지' 센다 ----------

def test_engine_counts_commands_and_syslog_separately():
    engine = BaselineDiffEngine(FakeStore())
    engine.analyze_stream("Core1", "Core1#show version\n"
                                   "Arista DCS-7050SX\n"
                                   "%LINEPROTO-5-UPDOWN: Line protocol on Interface "
                                   "Ethernet1, changed state to down\n")
    obs = engine.observations("Core1")
    assert obs["commands"] == 1
    assert obs["syslog"] == 1
    assert obs["output"] == 2      # 배너 한 줄 + syslog 한 줄


def test_show_output_alone_yields_no_syslog_observation():
    """실제 CRT 로그가 이 상태였다 — 명령과 출력은 많지만 syslog 는 0줄."""
    engine = BaselineDiffEngine(FakeStore())
    engine.analyze_stream("Core1", "Core1#show running-config\n"
                                   "vlan 100\n"
                                   "interface Ethernet1\n"
                                   "   no shutdown\n"
                                   "end\n")
    assert engine.observations("Core1")["syslog"] == 0


# ---------- 체크리스트가 그것을 반영한다 ----------

def test_syslog_only_items_stay_unjudged_without_syslog(monitor):
    monitor.set_observations({"Core1": {"commands": 12, "syslog": 0, "output": 300}})
    rows = items(monitor.state())

    for key in SYSLOG_ONLY:
        assert rows[key]["status"] == "unknown", f"{key}: syslog 없이 정상이라고 할 수 없다"
        assert "syslog" in rows[key]["detail"]
    # 명령으로 판정되는 항목은 그대로 '정상(변경 없음)'이다 — 그건 실제로 보고 있다.
    for key in COMMAND_ITEMS:
        assert rows[key]["status"] == "pending"
        assert rows[key]["detail"] == "변경 없음"


def test_syslog_arrival_unblocks_the_items(monitor):
    monitor.set_observations({"Core1": {"commands": 12, "syslog": 0, "output": 300}})
    assert items(monitor.state())["link"]["status"] == "unknown"

    monitor.set_observations({"Core1": {"commands": 12, "syslog": 1, "output": 301}})
    rows = items(monitor.state())
    for key in SYSLOG_ONLY:
        assert rows[key]["status"] == "pending"
        assert rows[key]["detail"] == "변경 없음"


def test_nothing_observed_blocks_every_item(monitor):
    rows = items(monitor.state())
    assert all(row["status"] == "unknown" for row in rows.values())


def test_observed_facts_are_never_overwritten(monitor):
    """관측된 판정(fail)은 '판정 불가'로 되돌리지 않는다 — 이미 본 사실이다."""
    monitor.apply_alerts([{"device": "Core1", "type": "LINK_DOWN", "severity": "CRITICAL",
                           "target": "link:Ethernet1", "message": "link down",
                           "raw_line": "x", "alert_id": "a1", "ts": "12:00:00"}])
    monitor.set_observations({"Core1": {"commands": 0, "syslog": 0, "output": 0}})
    assert items(monitor.state())["link"]["status"] == "fail"


# ---------- '경고 0건'의 뜻을 화면이 구분한다 ----------

def test_verdict_says_no_input_when_nothing_arrived(monitor):
    analysis = monitor.state()["analysis"]
    assert analysis["verdict"] == "unknown"
    assert "입력 없음" in analysis["headline"]


def test_verdict_says_partial_when_only_syslog_is_missing(monitor):
    monitor.set_observations({"Core1": {"commands": 5, "syslog": 0, "output": 100}})
    analysis = monitor.state()["analysis"]
    assert analysis["verdict"] == "unknown"
    assert "판정 불가" in analysis["headline"]
    assert "terminal monitor" in analysis["summary"]


def test_verdict_is_ok_only_when_both_sources_are_seen(monitor):
    monitor.set_observations({"Core1": {"commands": 5, "syslog": 3, "output": 100}})
    analysis = monitor.state()["analysis"]
    assert analysis["verdict"] == "ok"
    assert analysis["headline"] == "이상 징후 없음"


def test_watch_quality_is_reported(monitor):
    monitor.adopt_devices(["Core2"], ["Core2"])
    monitor.set_observations({"Core1": {"commands": 5, "syslog": 3, "output": 100}})
    quality = monitor.state()["watch_quality"]
    assert quality["devices"] == 2
    assert quality["syslog_devices"] == 1
    assert quality["syslog_missing_devices"] == ["Core2"]
    assert quality["silent_devices"] == ["Core2"]


def test_observations_survive_a_restart(monitor):
    monitor.set_observations({"Core1": {"commands": 5, "syslog": 3, "output": 100}})
    snapshot = monitor.snapshot()

    revived = RealtimeMonitor()
    revived.adopt_devices(["Core1"], ["Core1"])
    revived.restore(snapshot)
    assert items(revived.state())["link"]["status"] == "pending"


# ---------- STP mnemonic ----------

def test_spantree_syslog_is_detected():
    """Arista/Cisco 의 실제 mnemonic 은 %SPANTREE-n-… 다. 'STP-\\d-' 만 보던 예전 패턴은
    두 벤더 어느 쪽에도 매치되지 않아 STP 로그를 통째로 놓치고 있었다."""
    engine = BaselineDiffEngine(FakeStore())
    alerts = engine.analyze_stream(
        "Core1", "%SPANTREE-2-ROOTGUARD_BLOCK: Root guard blocking port Ethernet3 on VLAN0100\n")
    assert [a["type"] for a in alerts] == ["STP_CHANGE"]
