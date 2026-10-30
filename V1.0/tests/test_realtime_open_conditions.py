"""실시간 감시의 '열린 장애' — IMPROVEMENT_PLAN 4-8a.

  1. 열린 장애가 저장본에 남고, 감시를 다시 시작하면 새 엔진의 상태표에 다시 선다 — 재시작 뒤에 온 복구 줄이
     되살린 경고를 해제한다. 예전엔 세션 로그를 다시 읽는 256KB 안에 DOWN 줄이 남아 있을 때만 이어졌고,
     아니면 장비가 돌아와도 경고가 영영 '미해결'로 남았다.
  2. 경고 id 가 실행마다 달라 되살린 경고와 겹치지 않는다(번호가 1부터 다시 나가 '해결'이 엉뚱한 쪽에 걸렸다).
  3. '해결'·열린 장애 '해제'는 그 장애에 매달린 경고를 함께 해결하고 상태를 닫는다 — 감시가 멈춰 있어도.
  4. 화면 상태에 열린 장애가 실리고, 바뀌지 않으면 델타에서 빠진다. '초기화'는 열린 장애도 비운다.
  5. 구성도 탭이 감시 서비스에서 열린 장애를 읽는다 — 3-5 이후 옛 속성 이름을 읽어 한 번도 얹히지 않았다.
"""
import pytest

from core.paths import AppPaths
from engine.baseline_diff_engine import BaselineDiffEngine, StateTracker
from engine.realtime_monitor import RealtimeMonitor
from engine.realtime_rule_stream import RealtimeRuleStream

SYSLOG_DOWN = "%LINEPROTO-5-UPDOWN: Line protocol on Interface Ethernet1, changed state to down"
SYSLOG_UP = "%LINEPROTO-5-UPDOWN: Line protocol on Interface Ethernet1, changed state to up"


class FakeStore:
    source_kind = "inspection"
    key = ("고객사A", "2026-07")

    def device_names(self):
        return ["Core1"]

    def get_device_baseline(self, device):
        return {"vlans": {"100"}, "interfaces": {"Ethernet1"}, "routes": set(), "bgp_neighbors": set()}


class FakeWatcher:
    """CRTStreamWatcher 대신 — 스레드 없이 콜백만 쥐고 있다가 테스트가 줄을 흘려 넣는다."""

    def __init__(self, watch_dir, on_delta, interval=0.3, **kwargs):
        self.watch_dir, self.on_delta, self.interval = str(watch_dir), on_delta, interval
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def is_running(self):
        return self.running

    def status(self):
        return {"tracked_files": 1, "unmatched": [], "device_files": {}, "rollovers": []}


@pytest.fixture
def make_api(tmp_path, monkeypatch):
    """부를 때마다 새 Api(= 프로그램을 다시 켠 것). 데이터 폴더는 둘이 같다."""
    original = AppPaths._user_data_root
    AppPaths._user_data_root = tmp_path / "AutoCheck"
    AppPaths.forget_ensured()
    from api.realtime_watch_api import RealtimeWatchApiMixin
    from api.topology_api import TopologyApiMixin
    from core import crt_stream_watcher
    from engine.profile_manager import profile_manager as prm
    monkeypatch.setattr(crt_stream_watcher, "CRTStreamWatcher", FakeWatcher)
    prm.create_profile("고객사A", "2026-07")

    class Stub(RealtimeWatchApiMixin, TopologyApiMixin):
        def _resolve_active_customer_profile_names(self):
            return ("고객사A", "2026-07")

        def get_terminal_targets(self):
            return [{"name": "Core1", "ip": "10.0.0.1", "port": 22}]

        def _push_js(self, *args, **kwargs):
            pass

    def make():
        stub = Stub()
        stub._realtime().baseline_store = lambda: FakeStore()
        return stub

    yield make
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def feed(api, *lines):
    api._realtime().watcher.on_delta("Core1", "\n".join(lines) + "\n", "Core1_1.log")


def open_faults(api):
    return api.get_realtime_monitor_state(160)["open_conditions"]


def link_alerts(api, type_="LINK_DOWN"):
    return [a for a in api._realtime().monitor().alerts() if a.get("type") == type_]


# --------------------------------------------------------------------------- 1. 재시작을 넘는다

def test_open_fault_survives_a_restart_and_the_recovery_line_resolves_it(make_api):
    first = make_api()
    assert first.start_realtime_baseline_watch(device_names=["Core1"])["ok"]
    feed(first, SYSLOG_DOWN)
    [fault] = open_faults(first)
    assert (fault["device"], fault["component_id"], fault["condition"]) == ("Core1", "Ethernet1", "interface_down")
    assert fault["label"] == "인터페이스 다운" and fault["severity"] == link_alerts(first)[0]["severity"]
    assert fault["earlier"] is False and fault["raw"] == SYSLOG_DOWN
    since = fault["since"]
    first.stop_realtime_baseline_watch()                 # 감시를 멈추면 엔진은 비지만 사본·저장본은 남는다
    assert len(open_faults(first)) == 1

    second = make_api()                                    # 프로그램을 다시 켰다 — 새 엔진, 세션 로그 재판정 없음
    assert len(open_faults(second)) == 1                   # 감시를 시작하기 전에도 보인다
    assert second.start_realtime_baseline_watch(device_names=["Core1"])["ok"]
    [fault] = open_faults(second)
    assert fault["earlier"] is True and fault["since"] == since       # 지속 시간은 원래 시각부터
    feed(second, SYSLOG_UP)
    assert open_faults(second) == []
    restored = link_alerts(second)
    assert restored and all(a.get("restored") and a.get("resolved") for a in restored)


def test_restart_keeps_the_original_start_time():
    tracker = StateTracker(clock=lambda: 5000.0)
    opened = tracker.restore([
        {"device": "Core1", "component_id": "Ethernet1", "condition": "interface_down",
         "since": 1000.0, "raw": SYSLOG_DOWN, "alert_ids": ["Core1#aaaa-1"]},
        {"device": "Core1", "component_id": "Ethernet1", "condition": "interface_down",
         "since": 2000.0, "raw": "dup", "alert_ids": ["Core1#aaaa-2"]},          # 같은 상태 — id 만 합친다
        {"device": "Core1", "component_id": None, "condition": "interface_down"},  # 대상 없음 — 버린다
        "깨진 항목",
    ])
    [cond] = tracker.open_conditions()
    assert opened == 1 and cond["since"] == 1000.0 and cond["restored"] is True
    assert cond["alert_ids"] == ["Core1#aaaa-1", "Core1#aaaa-2"]
    [resolution, _second] = tracker.close("Core1", "Ethernet1", "interface_down", SYSLOG_UP)
    assert resolution["duration_sec"] == 4000.0             # 지속 시간이 원래 시각부터 잰다


def test_snapshot_round_trip_keeps_only_watched_devices():
    saved = RealtimeMonitor()
    saved.reset(["Core1", "Old1"])
    saved.set_open_conditions([
        {"device": "Core1", "component_id": "Ethernet1", "condition": "interface_down", "since": 1.0,
         "raw": "x", "alert_ids": ["a"]},
        {"device": "Old1", "component_id": "Ethernet2", "condition": "interface_down", "since": 1.0,
         "raw": "y", "alert_ids": ["b"]},
    ])
    restored = RealtimeMonitor()
    restored.reset(["Core1"])
    restored.restore(saved.snapshot())
    [cond] = restored.open_conditions()
    assert cond["device"] == "Core1" and cond["restored"] is True
    restored.restore(saved.snapshot())                      # 두 번 얹어도 한 번
    assert len(restored.open_conditions()) == 1


# --------------------------------------------------------------------------- 2. id 가 실행마다 다르다

def test_alert_ids_do_not_repeat_across_runs():
    first = BaselineDiffEngine(FakeStore()).analyze_stream("Core1", SYSLOG_DOWN + "\n")
    second = BaselineDiffEngine(FakeStore()).analyze_stream("Core1", SYSLOG_DOWN + "\n")
    assert first and second and first[0]["alert_id"] != second[0]["alert_id"]
    text = "Core1#show mlag\nstate : Inactive\nCore1#"
    rule_a, rule_b = RealtimeRuleStream().analyze("Core1", text), RealtimeRuleStream().analyze("Core1", text)
    assert rule_a and rule_b and rule_a[0]["alert_id"] != rule_b[0]["alert_id"]


# --------------------------------------------------------------------------- 3. 수동 해결

def test_resolving_one_alert_closes_the_fault_and_its_other_alerts(make_api):
    api = make_api()
    api.start_realtime_baseline_watch(device_names=["Core1"])
    feed(api, "Core1(config-if-Et1)#shutdown", SYSLOG_DOWN)
    [fault] = open_faults(api)
    assert len(fault["alert_ids"]) == 2                     # shutdown 에코 + LINEPROTO DOWN — 장애 하나
    [down] = link_alerts(api)
    assert api.resolve_realtime_finding([down["alert_id"]])["resolved"] == 2
    assert open_faults(api) == []
    assert api._realtime().diff_engine.open_conditions() == []
    alerts = api._realtime().monitor().alerts()
    assert all(a.get("resolved") for a in alerts if a.get("type") in ("LINK_DOWN", "INTERFACE_SHUTDOWN"))
    feed(api, SYSLOG_UP)                                    # 이제 닫을 것이 없다 — 정보성 복구 경고로 남는다
    assert link_alerts(api, "LINK_UP")


def test_closing_a_fault_while_the_watch_is_stopped_sticks(make_api):
    api = make_api()
    api.start_realtime_baseline_watch(device_names=["Core1"])
    feed(api, SYSLOG_DOWN)
    api.stop_realtime_baseline_watch()
    [fault] = open_faults(api)
    assert api.resolve_realtime_finding(fault["alert_ids"], "열린 장애 해제")["ok"]
    assert open_faults(api) == []
    again = make_api()
    again.start_realtime_baseline_watch(device_names=["Core1"])
    assert open_faults(again) == [] and again._realtime().diff_engine.open_conditions() == []
    [down] = link_alerts(again)
    assert down["resolved"] and down["resolved_by"] == "열린 장애 해제"


def test_close_goes_through_aliases():
    """화면의 경고는 지난 실행의 id, 세션 로그를 다시 읽어 열린 상태는 같은 사건의 새 id 를 들고 있다."""
    monitor = RealtimeMonitor()
    monitor.reset(["Core1"], ["Core1"])
    seed = {"device": "Core1", "type": "LINK_DOWN", "severity": "CRITICAL", "target": "link:Ethernet1",
            "message": "down", "raw_line": SYSLOG_DOWN, "history": True}
    monitor.apply_alerts([dict(seed, alert_id="Core1#old-1")])
    monitor.apply_alerts([dict(seed, alert_id="Core1#new-1")])     # 같은 사건 — 별칭으로만 남는다
    monitor.set_open_conditions([{"device": "Core1", "component_id": "Ethernet1", "condition": "interface_down",
                                  "since": 1.0, "raw": SYSLOG_DOWN, "alert_ids": ["Core1#new-1"]}])
    [row] = monitor.state()["open_conditions"]
    assert row["alert_ids"] == ["Core1#new-1", "Core1#old-1"] and row["severity"] == "CRITICAL"
    assert row["earlier"] is True                                  # 감시 시작 전 기록에서만 나온 장애
    assert monitor.close_open_conditions(["Core1#old-1"]) == ["Core1#new-1"]
    assert monitor.open_conditions() == []


# --------------------------------------------------------------------------- 4. 화면 상태

def test_open_faults_ride_the_delta_only_when_they_change():
    monitor = RealtimeMonitor()
    monitor.reset(["Core1"], ["Core1"])
    full = monitor.state()
    cursor = {"epoch": full["epoch"], "versions": full["versions"], "devices": {"Core1": 0}}
    assert full["open_conditions"] == [] and monitor.state(since=cursor)["open_conditions"] is None
    monitor.set_open_conditions([{"device": "Core1", "component_id": "Ethernet1", "condition": "interface_down",
                                  "since": 1.0, "raw": "x", "alert_ids": []}])
    [row] = monitor.state(since=cursor)["open_conditions"]
    assert row["label"] == "인터페이스 다운" and row["severity"] is None and row["since_text"]


def test_hidden_devices_hide_their_open_faults():
    monitor = RealtimeMonitor()
    monitor.reset(["Core1"], ["Core1"])
    monitor.set_open_conditions([{"device": "Core1", "component_id": "Ethernet1", "condition": "interface_down",
                                  "since": 1.0, "raw": "x", "alert_ids": []}])
    monitor.set_filter({"hidden_devices": ["Core1"]})
    assert monitor.state()["open_conditions"] == []


def test_clear_empties_open_faults_in_the_engine_too(make_api):
    api = make_api()
    api.start_realtime_baseline_watch(device_names=["Core1"])
    feed(api, SYSLOG_DOWN)
    api.clear_realtime_alerts()
    assert open_faults(api) == [] and api._realtime().diff_engine.open_conditions() == []


# --------------------------------------------------------------------------- 5. 구성도

def test_topology_reads_open_faults_from_the_watch_service(make_api):
    api = make_api()
    assert api._topology_live_state()["running"] is False
    api.start_realtime_baseline_watch(device_names=["Core1"])
    feed(api, SYSLOG_DOWN)
    live = api._topology_live_state()
    assert live["running"] is True and live["down"] == [["Core1", "Ethernet1"]]
    assert live["observed"] == ["Core1"] and live["unresolved_alerts"] == {"Core1": 1}


def test_topology_does_not_create_the_watch_service():
    from api.realtime_watch_api import RealtimeWatchApiMixin
    from api.topology_api import TopologyApiMixin

    class Bare(RealtimeWatchApiMixin, TopologyApiMixin):
        pass

    bare = Bare()
    assert bare._topology_live_state() == {"running": False, "down": [], "observed": [], "unresolved_alerts": {}}
    assert "_realtime_service" not in bare.__dict__
