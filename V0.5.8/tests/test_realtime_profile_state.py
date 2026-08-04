"""실시간 감시 내역은 프로파일마다 따로 남아야 한다.

보고된 증상: 프로파일을 바꿔도 이전 프로파일의 실시간 감시 내역(좌측 로그·체크리스트·오류
분석)이 화면에 그대로 남았다. 저장/복원 자체(engine/realtime_monitor.py의 snapshot/restore)는
있었고, 원인은 두 군데였다:

  1. core/context_cache.py의 활성 컨텍스트 캐시가 만료 없이 살아 있는데
     api/project_core_api.py의 set_active_project()가 그것을 비우지 않았다 — 활성 프로젝트
     파일은 바뀌었는데 resolve_active_customer_profile_names()는 이전 프로파일을 계속
     돌려주므로, 저장 경로가 안 바뀌어 '프로파일이 바뀌었다'는 판정 자체가 성립하지 않았다.
  2. 감시가 도는 중에는 프로파일 전환을 아예 건너뛰었다 — 자동시작을 켜 두면 감시가 항상
     돌고 있어서 전환이 영구히 무시되고, 새 프로파일의 경고까지 이전 프로파일 파일에 쌓였다.

그래서 이 파일은 저장/복원 결과가 아니라 **전환 경계**를 본다.
"""
import tempfile
from pathlib import Path

import pytest

from core.paths import AppPaths

ALERT = {
    "type": "LINK_DOWN", "severity": "CRITICAL", "target": "link:Et1",
    "message": "link down", "raw_line": "%LINEPROTO-5-UPDOWN Et1 down", "ts": "12:00:00",
}


@pytest.fixture
def api(monkeypatch):
    """임시 루트에 프로파일 두 개를 만들고, 활성 프로파일을 바꿀 수 있는 Api 대역을 준다."""
    original = AppPaths._user_data_root
    AppPaths._user_data_root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths.forget_ensured()

    from api.log_analysis_run_api import LogAnalysisRunApiMixin
    from engine.profile_manager import profile_manager as prm

    class Stub(LogAnalysisRunApiMixin):
        active = ("고객사A", "2026-07")

        def resolve_active_customer_profile_names(self):
            return self.active

    for profile in ("2026-07", "2026-08"):
        prm.create_profile("고객사A", profile)

    yield Stub()

    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def texts(state, device="Core1"):
    entry = next((d for d in state["devices"] if d["device"] == device), None)
    return [line["text"] for line in (entry or {}).get("lines", [])]


def record(api, device="Core1", text="no vlan 100", alert=None):
    """감시 중에 벌어지는 일을 최소로 재현 — 로그 한 줄 + 경고 하나."""
    monitor = api._realtime_monitor()
    monitor.adopt_devices([device], [device])
    monitor.append_lines(device, text)
    monitor.apply_alerts([dict(ALERT, device=device, alert_id=alert or f"{device}-1")])
    api._save_realtime_state(force=True)


def test_switch_hides_previous_profile_and_restores_its_own(api):
    record(api, text="no vlan 100")
    assert texts(api.get_realtime_monitor_state()) == ["no vlan 100"]

    api.active = ("고객사A", "2026-08")
    assert api.notify_active_profile_changed() is True
    state = api.get_realtime_monitor_state()
    assert state["devices"] == []
    assert state["alerts"] == []
    assert state["analysis"]["verdict"] == "ok"

    # 새 프로파일에서 찾은 것은 새 프로파일에만 쌓인다.
    record(api, text="shutdown", alert="Core1-2")
    assert texts(api.get_realtime_monitor_state()) == ["shutdown"]

    # 돌아오면 이전 프로파일의 마지막 내역이 그대로 보인다(복원된 줄은 history 표시).
    api.active = ("고객사A", "2026-07")
    api.notify_active_profile_changed()
    state = api.get_realtime_monitor_state()
    assert texts(state) == ["no vlan 100"]
    assert [a["alert_id"] for a in state["alerts"]] == ["Core1-1"]


def test_each_profile_keeps_its_own_file(api):
    record(api, text="no vlan 100")
    api.active = ("고객사A", "2026-08")
    api.notify_active_profile_changed()
    record(api, text="shutdown", alert="Core1-2")

    from engine import realtime_monitor as rtm
    first = rtm.load_snapshot(api._realtime_state_path("고객사A", "2026-07"))
    second = rtm.load_snapshot(api._realtime_state_path("고객사A", "2026-08"))
    assert [line["text"] for line in first["lines"]["Core1"]] == ["no vlan 100"]
    # 전환 후의 경고가 이전 프로파일 파일로 새지 않아야 한다.
    assert [a["alert_id"] for a in first["alerts"]] == ["Core1-1"]
    assert [a["alert_id"] for a in second["alerts"]] == ["Core1-2"]


def test_polling_path_also_switches_but_is_throttled(api):
    """훅을 못 탄 경로(외부에서 활성 프로젝트만 바뀐 경우)도 폴링이 따라잡는다."""
    record(api)
    api.active = ("고객사A", "2026-08")

    # 방금 확인했으므로 3초 창 안에서는 그대로다 — 폴링마다 프로파일 경로를 다시 읽지 않는다.
    api._realtime_profile_checked_at = 9e18
    assert api._sync_realtime_profile() is False
    assert texts(api.get_realtime_monitor_state()) == ["no vlan 100"]

    api._realtime_profile_checked_at = 0
    assert api._sync_realtime_profile() is True
    assert api.get_realtime_monitor_state()["devices"] == []


def test_switch_while_watching_restarts_on_new_profile(api):
    """감시 중에도 전환한다 — 예전에는 여기서 물러나 이전 내역이 영구히 남았다."""
    record(api)

    class FakeWatcher:
        interval = 0.3
        stopped = False

        def is_running(self):
            return not self.stopped

        def stop(self):
            self.stopped = True

        def status(self):
            return {}

    started = []
    api._baseline_stream_watcher = FakeWatcher()
    api.start_realtime_baseline_watch = lambda interval=0.3, device_names=None: (
        started.append((interval, device_names)) or {"ok": True})

    api.active = ("고객사A", "2026-08")
    assert api.notify_active_profile_changed() is True
    assert api._baseline_stream_watcher.stopped is True
    # 감시 대상 장비는 새 프로파일의 장비 목록에서 다시 고른다(넘기지 않는다).
    assert started == [(0.3, None)]
    assert api.get_realtime_monitor_state()["devices"] == []
