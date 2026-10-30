"""실시간 감시 상태 영속화 — IMPROVEMENT_PLAN 2-8.

  1. 10초 안에 들어온 저장 요청은 버려지고 다시 저장되지 않았다 → 경고 직후 자동 복구된 상태가 남지 않아,
     재실행 때 CRITICAL 이 미해결로 되살아났다. 이제 후행 저장한다.
  2. 창을 닫을 때 저장하는 훅이 없었다.
  3. 저장된 시각에 날짜가 없어 어제 일이 오늘 일처럼 보였다.
  4. 깨진 상태 파일을 그대로 둬서 다음 저장이 덮어썼다 — 진단할 것도, 되돌릴 직전 상태도 남지 않았다.
"""
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from core.paths import AppPaths
from engine import realtime_monitor as rtm

ALERT = {"type": "LINK_DOWN", "severity": "CRITICAL", "target": "link:Et1", "message": "link down",
         "raw_line": "%LINEPROTO-5-UPDOWN Et1 down", "ts": "12:00:00", "device": "Core1", "alert_id": "Core1-1"}


@pytest.fixture
def api():
    original = AppPaths._user_data_root
    AppPaths._user_data_root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths.forget_ensured()
    from api.realtime_watch_api import RealtimeWatchApiMixin
    from engine.profile_manager import profile_manager as prm

    class Stub(RealtimeWatchApiMixin):
        def _resolve_active_customer_profile_names(self):
            return "고객사A", "2026-10"

    prm.create_profile("고객사A", "2026-10")
    stub = Stub()
    monitor = stub._realtime().monitor()
    monitor.adopt_devices(["Core1"], ["Core1"])
    yield stub
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def saved_state(api):
    return json.loads(Path(api._realtime().state_path()).read_text(encoding="utf-8"))


def test_a_throttled_change_is_saved_later_not_dropped(api):
    monitor = api._realtime().monitor()
    monitor.apply_alerts([dict(ALERT)])
    assert api._realtime().save_state() is True                       # 첫 저장
    monitor.resolve_alerts([{"alert_id": "Core1-1", "ts": "12:00:05"}])   # 5초 뒤 자동 복구
    api._realtime()._state_saved_at = time.time() - 9.0                 # (10초 창이 1초 뒤 끝나는 시점으로 — 0.2초로 두면 바쁠 때 창이 지나 흔들렸다)
    assert api._realtime().save_state() is False                      # 창 안 — 묶인다(예전엔 여기서 버려졌다)
    assert saved_state(api)["alerts"][0].get("resolved") is not True
    deadline = time.time() + 3
    while time.time() < deadline and saved_state(api)["alerts"][0].get("resolved") is not True:
        time.sleep(0.05)
    assert saved_state(api)["alerts"][0]["resolved"] is True        # 후행 저장이 남겼다


def test_flush_writes_only_when_something_is_pending(api):
    monitor = api._realtime().monitor()
    monitor.apply_alerts([dict(ALERT)])
    api._realtime().save_state(force=True)
    assert api._flush_realtime_state() is False
    monitor.append_lines("Core1", "no vlan 100")
    api._realtime().save_state()
    assert api._flush_realtime_state() is True
    assert any(line["text"] == "no vlan 100" for line in saved_state(api)["lines"]["Core1"])


def test_main_flushes_on_window_close():
    source = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    assert "window.events.closing +=" in source and "_flush_realtime_state()" in source


def test_restored_times_from_another_day_carry_the_date():
    monitor = rtm.RealtimeMonitor()
    monitor.adopt_devices(["Core1"], ["Core1"])
    yesterday = time.time() - 86400
    snapshot = {"saved_at": yesterday, "devices": ["Core1"],
                "lines": {"Core1": [{"ts": "23:59:58", "text": "no vlan 100"}, {"ts": "--:--:--", "text": "old"}]},
                "alerts": [dict(ALERT, ts="23:59:59", resolved_ts="00:00:01")]}
    monitor.restore(snapshot)
    state = monitor.snapshot()
    day = time.strftime("%m-%d", time.localtime(yesterday))
    assert [line["ts"] for line in state["lines"]["Core1"]] == [f"{day} 23:59:58", "--:--:--"]
    assert state["alerts"][0]["ts"] == f"{day} 23:59:59" and state["alerts"][0]["resolved_ts"] == f"{day} 00:00:01"


def test_restored_times_from_today_stay_short():
    monitor = rtm.RealtimeMonitor()
    monitor.adopt_devices(["Core1"], ["Core1"])
    monitor.restore({"saved_at": time.time(), "devices": ["Core1"],
                     "lines": {"Core1": [{"ts": "09:00:00", "text": "show version"}]}})
    assert monitor.snapshot()["lines"]["Core1"][0]["ts"] == "09:00:00"


def test_save_keeps_one_backup(tmp_path):
    path = str(tmp_path / "realtime_state.json")
    rtm.save_snapshot(path, {"n": 1})
    rtm.save_snapshot(path, {"n": 2})
    assert json.loads(Path(path).read_text(encoding="utf-8")) == {"n": 2}
    assert json.loads(Path(path + ".bak").read_text(encoding="utf-8")) == {"n": 1}


def test_a_corrupt_state_file_is_kept_and_the_backup_used(tmp_path):
    path = str(tmp_path / "realtime_state.json")
    rtm.save_snapshot(path, {"n": 1})
    rtm.save_snapshot(path, {"n": 2})
    Path(path).write_text('{"n": 3', encoding="utf-8")                # 저장 중 죽은 파일
    assert rtm.load_snapshot(path) == {"n": 1}
    corrupt = [p for p in os.listdir(tmp_path) if p.startswith("realtime_state.json.corrupt-")]
    assert len(corrupt) == 1
    assert (tmp_path / corrupt[0]).read_text(encoding="utf-8") == '{"n": 3'
