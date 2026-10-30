"""감시 건강 — IMPROVEMENT_PLAN 4-8b.

'경고 0건'이 '이상 없음'인지 '못 보고 있음'인지 가르는 세 가지를 본다:
  1. 세션 로그가 자라고 있나(감시기의 파일별 마지막 성장 시각 — 처음 본 파일은 수정 시각).
  2. syslog·명령을 마지막으로 '지금' 받은 때(감시 시작 때 되짚어 읽은 줄은 세지 않는다, 재시작을 넘는다).
  3. 상태 폴링 실패의 종류(인증 실패·호스트키 바뀜·접속 실패).
서비스는 이것을 심각한 순서의 문제 목록으로 묶고, 폴링 델타에서는 바뀌지 않으면 뺀다.
"""
import os
import time

import paramiko
import pytest

from core.crt_stream_watcher import CRTStreamWatcher
from core.paths import AppPaths
from engine.realtime_monitor import RealtimeMonitor
from engine.realtime_watch_service import LOG_STALE_SEC
from engine.state_poller import StatePoller, failure_kind

SYSLOG_DOWN = "%LINEPROTO-5-UPDOWN: Line protocol on Interface Ethernet1, changed state to down"


# --------------------------------------------------------------------------- 1. 세션 로그 성장

def test_watcher_reports_when_each_device_file_last_grew(tmp_path):
    log = tmp_path / "Core1_20261029.log"
    log.write_text("Core1#show version\n", encoding="utf-8")
    old = time.time() - 3600
    os.utime(log, (old, old))
    watcher = CRTStreamWatcher(tmp_path, lambda *a, **k: None, device_resolver=lambda path, head="": "Core1",
                               seed_history=False)
    watcher._tick()
    assert watcher.status()["device_growth"]["Core1"] == pytest.approx(old, abs=1)   # 처음 본 파일은 수정 시각
    with open(log, "a", encoding="utf-8") as f:
        f.write("Core1#show clock\n")
    watcher._tick()
    assert watcher.status()["device_growth"]["Core1"] == pytest.approx(time.time(), abs=5)
    assert watcher.status()["first_tick_done"] is False            # 루프를 돌기 전(_tick 만 불렀다)


# --------------------------------------------------------------------------- 3. 폴링 실패의 종류

def test_failure_kinds():
    from engine.host_keys import HostKeyChanged
    assert failure_kind(paramiko.AuthenticationException("Authentication failed.")) == "auth"
    assert failure_kind(paramiko.BadAuthenticationType("bad", ["publickey"])) == "auth"
    assert failure_kind(HostKeyChanged("10.0.0.1", 22, "SHA256:a", "SHA256:b")) == "host_key"
    assert failure_kind(OSError("timed out")) == "connect"


def test_poller_keeps_the_failure_kind_until_a_poll_succeeds(monkeypatch):
    from engine import ssh_client
    outcome = {"exc": paramiko.AuthenticationException("Authentication failed.")}

    class Client:
        def exec_command(self, command, timeout=None):
            class Out:
                @staticmethod
                def read():
                    return b""
            return None, Out(), None

        def close(self):
            pass

    def fake_connect(target, timeout=10):
        if outcome["exc"]:
            raise outcome["exc"]
        return Client()

    monkeypatch.setattr(ssh_client, "connect", fake_connect)
    poller = StatePoller(lambda: [{"name": "Core1", "ip": "10.0.0.1"}], lambda device, events: None)
    poller.poll_once()
    status = poller.status()
    assert status["error_kinds"] == {"Core1": "auth"} and "Authentication" in status["errors"]["Core1"]
    outcome["exc"] = None
    poller.poll_once()
    assert poller.status()["error_kinds"] == {} and poller.status()["errors"] == {}


# --------------------------------------------------------------------------- 2. 마지막 수신 시각

def test_only_live_input_stamps_the_last_seen_time():
    monitor = RealtimeMonitor()
    monitor.reset(["Core1"], ["Core1"])
    monitor.set_observations({"Core1": {"commands": 4, "syslog": 2}}, live=False)   # 되짚어 읽은 구간
    assert monitor.last_seen() == {}
    monitor.set_observations({"Core1": {"commands": 4, "syslog": 3}})
    seen = monitor.last_seen()["Core1"]
    assert set(seen) == {"syslog"} and seen["syslog"] == pytest.approx(time.time(), abs=5)


def test_last_seen_survives_a_restart_and_the_new_engine_still_stamps():
    first = RealtimeMonitor()
    first.reset(["Core1"], ["Core1"])
    first.set_observations({"Core1": {"syslog": 5}})
    first._last_seen["Core1"]["syslog"] = 1000.0                     # 어제 받은 것으로
    again = RealtimeMonitor()
    again.reset(["Core1"], ["Core1"])
    again.restore(first.snapshot())
    assert again.last_seen() == {"Core1": {"syslog": 1000.0}}
    # 새 엔진은 0부터 센다 — 되살린 카운트(5)보다 작아도 늘었으면 받은 것이다.
    again.set_observations({"Core1": {"syslog": 1}})
    assert again.last_seen()["Core1"]["syslog"] > 1000.0


# --------------------------------------------------------------------------- 서비스

class FakeStore:
    source_kind = "inspection"
    key = ("고객사A", "2026-07")

    def device_names(self):
        return ["Core1", "Core2"]

    def get_device_baseline(self, device):
        return {"vlans": set(), "interfaces": {"Ethernet1"}, "routes": set(), "bgp_neighbors": set()}


class FakeWatcher:
    status_value = {}

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
        return dict({"tracked_files": 1, "unmatched": [], "device_files": {}, "rollovers": [],
                     "device_growth": {}, "first_tick_done": True}, **FakeWatcher.status_value)


class FakePoller:
    def __init__(self, errors=None, kinds=None):
        self.errors, self.kinds = errors or {}, kinds or {}

    def is_running(self):
        return True

    def status(self):
        return {"errors": dict(self.errors), "error_kinds": dict(self.kinds), "last_poll": {"Core2": 123.0}}

    def stop(self):
        pass

    def reset(self):
        pass


@pytest.fixture
def api(tmp_path, monkeypatch):
    original = AppPaths._user_data_root
    AppPaths._user_data_root = tmp_path / "AutoCheck"
    AppPaths.forget_ensured()
    from api.realtime_watch_api import RealtimeWatchApiMixin
    from core import crt_stream_watcher
    from engine.profile_manager import profile_manager as prm
    monkeypatch.setattr(crt_stream_watcher, "CRTStreamWatcher", FakeWatcher)
    FakeWatcher.status_value = {}
    prm.create_profile("고객사A", "2026-07")

    class Stub(RealtimeWatchApiMixin):
        def _resolve_active_customer_profile_names(self):
            return ("고객사A", "2026-07")

        def get_terminal_targets(self):
            return [{"name": "Core1", "ip": "10.0.0.1", "port": 22}, {"name": "Core2", "ip": "10.0.0.2", "port": 22}]

        def _push_js(self, *args, **kwargs):
            pass

    stub = Stub()
    stub._realtime().baseline_store = lambda: FakeStore()
    yield stub
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def test_health_lists_problems_most_severe_first(api):
    stale = time.time() - LOG_STALE_SEC - 60
    FakeWatcher.status_value = {"device_files": {"Core1": "Core1_1.log"}, "device_growth": {"Core1": stale}}
    api.start_realtime_baseline_watch(device_names=["Core1", "Core2"])
    api._realtime().state_poller = FakePoller({"Core1": "Authentication failed."}, {"Core1": "auth"})
    health = api.get_realtime_watch_health()
    assert health["ok"] and health["running"] and health["poll_enabled"]
    assert [(i["kind"], i["level"]) for i in health["issues"]] == [
        ("poll_auth", "bad"), ("no_log", "warn"), ("log_stopped", "info")]
    assert health["level"] == "bad"
    assert health["issues"][0]["devices"] == [{"device": "Core1", "detail": "Authentication failed."}]
    assert health["issues"][1]["devices"] == [{"device": "Core2"}]
    assert health["issues"][2]["devices"] == [{"device": "Core1", "at": stale}]
    rows = {r["device"]: r for r in health["devices"]}
    assert rows["Core1"]["poll_error_kind"] == "auth" and rows["Core2"]["poll_at"] == 123.0
    assert rows["Core2"]["log_file"] is None


def test_host_key_and_connect_failures_are_told_apart(api):
    FakeWatcher.status_value = {"device_files": {"Core1": "a.log", "Core2": "b.log"},
                                "device_growth": {"Core1": time.time(), "Core2": time.time()}}
    api.start_realtime_baseline_watch(device_names=["Core1", "Core2"])
    api._realtime().state_poller = FakePoller({"Core1": "SSH 호스트키가 바뀌었습니다", "Core2": "timed out"},
                                              {"Core1": "host_key", "Core2": "connect"})
    health = api._realtime().watch_health()
    assert [(i["kind"], [d["device"] for d in i["devices"]]) for i in health["issues"]] == [
        ("host_key", ["Core1"]), ("poll_connect", ["Core2"])]
    assert "devices" not in health                                   # 폴링마다 실리는 요약에는 장비별 행이 없다


def test_no_log_waits_for_the_first_pass_and_a_stopped_watch_says_nothing(api):
    FakeWatcher.status_value = {"first_tick_done": False}
    api.start_realtime_baseline_watch(device_names=["Core1"])
    assert api._realtime().watch_health()["issues"] == []           # 아직 찾아보지 않았다
    FakeWatcher.status_value = {}
    assert [i["kind"] for i in api._realtime().watch_health()["issues"]] == ["no_log"]
    api.stop_realtime_baseline_watch()
    health = api._realtime().watch_health()
    assert health["issues"] == [] and health["level"] == "ok" and health["running"] is False


def test_history_lines_do_not_count_as_received_syslog(api):
    FakeWatcher.status_value = {"device_files": {"Core1": "a.log"}, "device_growth": {"Core1": time.time()}}
    api.start_realtime_baseline_watch(device_names=["Core1"])
    watcher = api._realtime().watcher
    watcher.on_delta("Core1", SYSLOG_DOWN + "\n", "a.log", True)    # 감시 시작 때 되짚어 읽은 줄
    assert api._realtime().watch_health()["syslog_at"] is None
    watcher.on_delta("Core1", SYSLOG_DOWN.replace("down", "up") + "\n", "a.log", False)
    assert api._realtime().watch_health()["syslog_at"] == pytest.approx(time.time(), abs=5)


def test_health_rides_the_poll_delta_only_when_it_changes(api):
    FakeWatcher.status_value = {"device_files": {"Core1": "a.log"}, "device_growth": {"Core1": time.time()}}
    api.start_realtime_baseline_watch(device_names=["Core1"])
    full = api.get_realtime_monitor_state(160)
    assert full["health"]["level"] == "ok" and full["versions"]["health"]
    cursor = {"epoch": full["epoch"], "versions": full["versions"],
              "devices": {d["device"]: d["line_seq"] for d in full["devices"]}}
    assert api.get_realtime_monitor_state(160, cursor)["health"] is None
    api._realtime().state_poller = FakePoller({"Core1": "Authentication failed."}, {"Core1": "auth"})
    changed = api.get_realtime_monitor_state(160, cursor)
    assert changed["health"]["level"] == "bad" and changed["versions"]["health"] != full["versions"]["health"]
