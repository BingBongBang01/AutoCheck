"""실시간 판정 일관성 — IMPROVEMENT_PLAN 2-9.

  1. fail 항목이 뒤따른 경미 경고 하나에 warn 으로 내려앉았다(상태를 '방금 들어온 경고'로 계산).
     복구형 경고(LINK_UP)도 fail 을 내리고 alert_ids 에 섞였다.
  2. 재시작 뒤 복원된 경고가 장비가 복구돼도 해제되지 않았다(같은 사건을 새 id 로 다시 판정 → 해제가 새 id 로).
  3. 경고 300건 상한이 오래된 것부터 무조건 밀어내 미해결 CRITICAL 이 패널에서 사라졌다.
  4. `show logging` 출력(장비에 남은 과거 기록)이 지금 사건처럼 CRITICAL·체크리스트 fail·syslog 관측으로 잡혔다.
  5. 같은 syslog 줄을 두 엔진이 따로 경고했고, 규칙 쪽 경고는 영영 해제되지 않았다.
  6. 숨김·고정 필터가 전역이라 고객사 A 에서 숨긴 것이 B 에서도 숨겨졌다.
  7. 처음엔 최신이 아니던 기존 세션 로그가 나중에 갱신되면 파일 전체가 지금 입력으로 재판정됐다.
"""
import os
import time
from pathlib import Path

import pytest

from engine.baseline_diff_engine import BaselineDiffEngine, is_history_command
from engine.realtime_monitor import RealtimeMonitor
from engine.realtime_rule_stream import RealtimeRuleStream


def link_alert(alert_id, severity="CRITICAL", type_="LINK_DOWN", **extra):
    return {"alert_id": alert_id, "device": "Core1", "type": type_, "severity": severity,
            "target": "link:Ethernet1", "message": f"{type_} {severity}",
            "raw_line": f"%LINEPROTO {alert_id}", "ts": "12:00:00", **extra}


@pytest.fixture
def monitor():
    m = RealtimeMonitor()
    m.reset(["Core1"], ["Core1"])
    return m


def link_item(monitor):
    return monitor._checklists["Core1"]["link"]


# --------------------------------------------------------------------------- 1. 강등

def test_a_later_warning_does_not_demote_a_failure(monitor):
    monitor.apply_alerts([link_alert("a1", "CRITICAL")])
    monitor.apply_alerts([link_alert("a2", "WARNING")])
    assert link_item(monitor)["status"] == "fail" and link_item(monitor)["severity"] == "CRITICAL"


def test_a_recovery_alert_does_not_demote_or_join_a_failure(monitor):
    monitor.apply_alerts([link_alert("a1", "CRITICAL")])
    monitor.apply_alerts([link_alert("up1", "WARNING", type_="LINK_UP")])
    item = link_item(monitor)
    assert item["status"] == "fail" and item["alert_ids"] == ["a1"]


def test_resolving_the_worst_alert_recomputes_from_what_is_left(monitor):
    monitor.apply_alerts([link_alert("a1", "CRITICAL"), link_alert("a2", "WARNING")])
    monitor.resolve_alerts([{"alert_id": "a1", "ts": "12:01:00"}])
    item = link_item(monitor)
    assert item["status"] == "warn" and item["severity"] == "WARNING" and item["alert_ids"] == ["a2"]


# --------------------------------------------------------------------------- 2. 재시작 뒤 해제

def test_restored_alert_is_resolved_through_the_reseeded_id(monitor):
    old = link_alert("old#1", history=True)
    monitor.restore({"saved_at": time.time(), "devices": ["Core1"], "alerts": [old]})
    monitor.apply_alerts([dict(old, alert_id="new#7")])               # 감시 시작 seed — 같은 사건, 새 id
    assert len(monitor._alerts) == 1
    applied = monitor.resolve_alerts([{"alert_id": "new#7", "ts": "12:05:00"}])   # 엔진은 새 id 로 해제
    assert applied and monitor._alerts[0]["resolved"] is True


# --------------------------------------------------------------------------- 3. 상한

def test_the_cap_drops_finished_and_minor_alerts_first(monitor):
    monitor._alert_max = 5
    monitor.apply_alerts([link_alert("crit", "CRITICAL")])
    for i in range(3):
        monitor.apply_alerts([link_alert(f"done{i}", "MAJOR", target=f"rule:x{i}")])
        monitor.resolve_alerts([{"alert_id": f"done{i}"}])
    monitor.apply_alerts([link_alert(f"warn{i}", "WARNING", target=f"rule:w{i}") for i in range(3)])
    # 7건 → 5건: 해제된 것 중 오래된 둘(done0, done1)이 빠진다. 미해결 CRITICAL 은 가장 오래됐어도 남는다.
    assert [a["alert_id"] for a in monitor._alerts] == ["crit", "done2", "warn0", "warn1", "warn2"]


# --------------------------------------------------------------------------- 4. show logging

class FakeStore:
    def get_device_baseline(self, device):
        return {"vlans": {"100"}, "interfaces": {"Ethernet1"}, "routes": set(), "bgp_neighbors": set()}


SYSLOG_DOWN = "%LINEPROTO-5-UPDOWN: Line protocol on Interface Ethernet1, changed state to down"


def test_history_commands_are_recognized():
    assert is_history_command("show logging") and is_history_command("sh log last 10")
    assert not is_history_command("show interfaces status")


def test_show_logging_output_is_a_past_record_not_live_syslog():
    engine = BaselineDiffEngine(FakeStore())
    alerts = engine.analyze_stream("Core1", f"Core1#show logging\n{SYSLOG_DOWN}\nCore1#")
    assert alerts and all(a.get("past_record") and a.get("history") for a in alerts)
    assert engine.observations("Core1")["syslog"] == 0               # syslog 수신 근거가 아니다
    live = BaselineDiffEngine(FakeStore())
    live.analyze_stream("Core1", f"{SYSLOG_DOWN}\n")
    assert live.observations("Core1")["syslog"] == 1


def test_rule_stream_marks_show_logging_lines_as_past_records():
    past = RealtimeRuleStream().analyze("Core1", "Core1#show logging\nstate : Inactive\nCore1#")
    now = RealtimeRuleStream().analyze("Core1", "Core1#show mlag\nstate : Inactive\nCore1#")
    assert past and all(a.get("past_record") for a in past)
    assert now and not any(a.get("past_record") for a in now)


def test_past_records_stay_out_of_the_checklist_and_the_live_count(monitor):
    monitor.apply_alerts([link_alert("p1", "CRITICAL", history=True, past_record=True)])
    assert link_item(monitor)["status"] != "fail"
    assert monitor.state()["analysis"]["verdict"] != "fail"
    assert [a["alert_id"] for a in monitor._alerts] == ["p1"]         # 이력에는 남는다


# --------------------------------------------------------------------------- 5. 두 엔진의 중복

def test_rule_alerts_for_lines_the_diff_engine_already_judged_are_dropped(monkeypatch):
    from api.realtime_watch_api import RealtimeWatchApiMixin

    class FakeEngine:
        def analyze_stream(self, device, text):
            return [link_alert("diff#1", raw_line=SYSLOG_DOWN)]

        def observations(self, device=None):
            return {}

        def drain_repeats(self):
            return {}

        def drain_resolutions(self):
            return []

        def open_conditions(self, device=None):
            return []

    class FakeRules:
        def analyze(self, device, text):
            return [link_alert("Core1#rule1", "MAJOR", raw_line=SYSLOG_DOWN, target="rule:link_state_down"),
                    link_alert("Core1#rule2", "MAJOR", raw_line="state : Inactive", target="rule:keyword_inactive")]

    class Stub(RealtimeWatchApiMixin):
        def _resolve_active_customer_profile_names(self):
            return None, None

        def _push_js(self, *args, **kwargs):
            pass

    stub = Stub()
    stub._realtime().diff_engine, stub._realtime().rule_stream = FakeEngine(), FakeRules()
    stub._realtime().monitor().reset(["Core1"], ["Core1"])
    stub._realtime().on_stream_delta("Core1", SYSLOG_DOWN + "\n", "Core1_1.log")
    assert sorted(a["alert_id"] for a in stub._realtime().monitor()._alerts) == ["Core1#rule2", "diff#1"]


# --------------------------------------------------------------------------- 6. 프로파일별 필터

def test_hidden_items_belong_to_one_profile(tmp_path, monkeypatch):
    from api.realtime_watch_api import RealtimeWatchApiMixin
    from engine import project_manager as pm
    from engine.realtime_watch_service import RealtimeWatchConfig
    active = {"id": "proj_a"}
    monkeypatch.setattr(pm, "get_active_project", lambda: active["id"])

    class Stub(RealtimeWatchApiMixin):
        def _resolve_active_customer_profile_names(self):
            return None, None

    stub = Stub()
    stub._realtime().config = RealtimeWatchConfig(tmp_path / "realtime_watch.yaml")   # 이 테스트만의 설정 파일
    stub.save_realtime_filter(hidden_devices=["Access2"])
    assert stub._realtime().config.normalized_filter()["hidden_devices"] == ["Access2"]
    active["id"] = "proj_b"
    assert stub._realtime().config.normalized_filter()["hidden_devices"] == []
    active["id"] = "proj_a"
    assert stub._realtime().config.normalized_filter()["hidden_devices"] == ["Access2"]


# --------------------------------------------------------------------------- 7. 나중에 따라가기 시작한 기존 파일

def test_a_late_tracked_old_log_is_not_rejudged_from_the_start(tmp_path):
    from core.crt_stream_watcher import CRTStreamWatcher
    old = tmp_path / "Core1_old.log"
    new = tmp_path / "Core1_new.log"
    old.write_text("Core1#no vlan 100\n" * 50, encoding="utf-8")
    new.write_text("Core1#show version\n", encoding="utf-8")
    past = time.time() - 3600
    os.utime(old, (past, past))
    os.utime(new, (past + 60, past + 60))       # 시계 해상도와 무관하게 '나중에 갱신된 old' 가 최신이 되게
    seen = []
    watcher = CRTStreamWatcher(tmp_path, lambda device, text, path, is_history=False: seen.append((Path(path).name, text, is_history)),
                               device_resolver=lambda path, head="": "Core1")
    watcher._tick()
    watcher._first_tick_done = True
    assert all(name == "Core1_new.log" for name, _, _ in seen)          # 첫 tick: 최신 파일만
    seen.clear()
    with old.open("ab") as f:                                           # 옛 세션 로그가 다시 기록된다
        f.write(b"Core1#show clock\n")
    os.utime(old, None)
    watcher._tick()
    live = [(name, text) for name, text, history in seen if not history]
    assert live == [("Core1_old.log", "Core1#show clock\n")]           # 새로 붙은 줄만 지금 입력
    assert "no vlan 100" not in "".join(text for _, text in live)
