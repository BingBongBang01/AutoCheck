"""실시간 감시 폴링 델타 프로토콜 — OPTIMIZATION_PLAN 3-1.

이 항목의 리스크가 HIGH 인 이유는 성능이 아니라 **원자성**이다. engine/realtime_monitor.py
상단 주석이 그 이유를 적어 놓았다 — 세 패널(로그/체크리스트/분석)이 같은 이벤트에서
파생되므로 갱신 시점이 어긋나면 "왼쪽 로그에는 보이는데 체크리스트는 정상"인 화면이 나온다.
델타화가 그 불변식을 깨서는 안 된다.

그래서 이 파일은 세 가지를 본다:
  1. 델타로 받은 것을 이어붙이면 전체 스냅샷과 같은 상태가 된다(줄 유실/중복 없음).
  2. 되돌릴 수 없는 상황(버퍼 밀림, 감시 재시작, 초기화, 새 장비)에서 재동기화를 지시한다.
  3. `since` 를 생략하면 예전과 완전히 같게 동작한다 — 강제 재동기화 경로가 살아 있어야 한다.
"""
import pytest

from engine.realtime_monitor import RealtimeMonitor

ALERT_TEMPLATE = {
    "type": "LINK_DOWN", "severity": "CRITICAL", "target": "link:Et1",
    "message": "link down", "raw_line": "%LINEPROTO-5-UPDOWN Et1 down", "ts": "12:00:00",
}


def make_monitor(devices=("Dev0", "Dev1"), lines_per_device=400):
    monitor = RealtimeMonitor(lines_per_device=lines_per_device)
    monitor.reset(list(devices), list(devices))
    return monitor


def cursor(state):
    """서버 응답에서 다음 요청에 쓸 since 를 만든다 — 프론트엔드가 하는 일과 같다."""
    return {
        "epoch": state["epoch"],
        "versions": dict(state["versions"]),
        "devices": {d["device"]: d["line_seq"] for d in state["devices"]},
    }


def device_entry(state, name):
    return next(d for d in state["devices"] if d["device"] == name)


# --------------------------------------------------------------------------- 하위 호환


def test_without_since_behaves_like_before():
    """since 를 생략하면 전체 tail 을 담고 resync 를 지시한다(예전 동작)."""
    monitor = make_monitor()
    monitor.append_lines("Dev0", "a\nb\nc")

    state = monitor.state(tail=160)
    entry = device_entry(state, "Dev0")
    assert [line["text"] for line in entry["lines"]] == ["a", "b", "c"]
    assert entry["resync"] is True
    # 섹션은 전부 실려 온다.
    assert state["analysis"] is not None
    assert state["filter"] is not None
    assert entry["checklist"] is not None


def test_state_shape_is_backward_compatible():
    """기존 프론트엔드가 읽던 키가 그대로 있어야 한다."""
    monitor = make_monitor()
    state = monitor.state(tail=160)
    for key in ("devices", "pinned", "alerts", "analysis", "started_at", "filter",
                "hidden_counts", "seen_rules"):
        assert key in state, f"기존 키 {key} 가 사라졌다"
    for key in ("device", "has_baseline", "lines", "line_count", "checklist",
                "fail_count", "warn_count", "status", "last_activity"):
        assert key in state["devices"][0], f"장비 항목의 기존 키 {key} 가 사라졌다"


# --------------------------------------------------------------------------- 델타 정확성


def test_delta_returns_only_new_lines():
    monitor = make_monitor()
    monitor.append_lines("Dev0", "a\nb")
    first = monitor.state(tail=160)
    since = cursor(first)

    monitor.append_lines("Dev0", "c\nd")
    delta = monitor.state(tail=160, since=since)
    entry = device_entry(delta, "Dev0")
    assert [line["text"] for line in entry["lines"]] == ["c", "d"]
    assert entry["resync"] is False


def test_no_change_returns_no_lines():
    monitor = make_monitor()
    monitor.append_lines("Dev0", "a\nb")
    since = cursor(monitor.state(tail=160))
    delta = monitor.state(tail=160, since=since)
    assert device_entry(delta, "Dev0")["lines"] == []


def test_appending_deltas_reconstructs_full_state():
    """**핵심 정확성.** 델타를 이어붙인 결과가 전체 스냅샷과 같아야 한다.

    줄이 하나라도 유실되거나 중복되면 화면의 로그가 실제 입력과 달라진다 — 실시간 감시에서
    그건 판정 근거가 틀리는 것과 같다.
    """
    monitor = make_monitor()
    monitor.append_lines("Dev0", "line0")

    state = monitor.state(tail=160)
    accumulated = [line["text"] for line in device_entry(state, "Dev0")["lines"]]
    since = cursor(state)

    for index in range(1, 30):
        monitor.append_lines("Dev0", f"line{index}")
        state = monitor.state(tail=160, since=since)
        entry = device_entry(state, "Dev0")
        assert entry["resync"] is False, "밀리지 않았는데 재동기화를 요구했다"
        accumulated.extend(line["text"] for line in entry["lines"])
        since = cursor(state)

    expected = [line["text"] for line in device_entry(monitor.state(tail=160), "Dev0")["lines"]]
    assert accumulated == expected


def test_multiple_devices_track_independently():
    monitor = make_monitor(devices=("Dev0", "Dev1", "Dev2"))
    monitor.append_lines("Dev0", "a")
    monitor.append_lines("Dev1", "b")
    since = cursor(monitor.state(tail=160))

    monitor.append_lines("Dev1", "c")
    delta = monitor.state(tail=160, since=since)
    assert device_entry(delta, "Dev0")["lines"] == []
    assert [l["text"] for l in device_entry(delta, "Dev1")["lines"]] == ["c"]
    assert device_entry(delta, "Dev2")["lines"] == []


# --------------------------------------------------------------------------- 재동기화


def test_buffer_overflow_forces_resync():
    """버퍼(maxlen)를 넘겨 클라이언트가 놓친 구간이 생기면 통째로 갈아야 한다.

    되살릴 수 없는 줄을 조용히 건너뛰면 화면의 로그에 구멍이 생기고, 사용자는 그 사실을
    알 수 없다.
    """
    monitor = make_monitor(lines_per_device=10)
    monitor.append_lines("Dev0", "\n".join(f"old{i}" for i in range(10)))
    since = cursor(monitor.state(tail=160))

    # 버퍼 크기보다 많이 밀어 넣어 클라이언트가 본 구간을 밀어낸다.
    monitor.append_lines("Dev0", "\n".join(f"new{i}" for i in range(20)))
    delta = monitor.state(tail=160, since=since)
    entry = device_entry(delta, "Dev0")

    assert entry["resync"] is True, "버퍼가 밀렸는데 재동기화를 지시하지 않았다"
    assert len(entry["lines"]) == 10, "재동기화 시에는 현재 버퍼 전체를 보내야 한다"
    assert entry["lines"][0]["text"] == "new10"


def test_reset_bumps_epoch_and_forces_resync():
    """감시를 다시 시작하면 seq 가 되감기므로 클라이언트 커서를 믿을 수 없다."""
    monitor = make_monitor()
    monitor.append_lines("Dev0", "a\nb\nc")
    since = cursor(monitor.state(tail=160))

    monitor.reset(["Dev0", "Dev1"], ["Dev0"])
    monitor.append_lines("Dev0", "x")

    delta = monitor.state(tail=160, since=since)
    assert delta["epoch"] != since["epoch"], "reset 이 epoch 를 올리지 않았다"
    entry = device_entry(delta, "Dev0")
    assert entry["resync"] is True
    assert [l["text"] for l in entry["lines"]] == ["x"], "낡은 줄이 남아 있다"


def test_clear_alerts_bumps_epoch():
    """초기화는 체크리스트/경고를 통째로 바꾼다 — 부분 갱신으로 따라올 수 없다."""
    monitor = make_monitor()
    monitor.apply_alerts([dict(ALERT_TEMPLATE, device="Dev0", alert_id="a1")])
    before = monitor.state(tail=160)["epoch"]
    monitor.clear_alerts()
    assert monitor.state(tail=160)["epoch"] != before


def test_unknown_device_gets_full_lines():
    """감시 중 새로 등장한 장비는 클라이언트가 모르므로 전체를 보낸다."""
    monitor = make_monitor(devices=("Dev0",))
    monitor.append_lines("Dev0", "a")
    since = cursor(monitor.state(tail=160))

    monitor.append_lines("NewDev", "p\nq")
    delta = monitor.state(tail=160, since=since)
    entry = device_entry(delta, "NewDev")
    assert entry["resync"] is True
    assert [l["text"] for l in entry["lines"]] == ["p", "q"]


def test_stale_epoch_sends_everything():
    monitor = make_monitor()
    monitor.append_lines("Dev0", "a\nb")
    since = cursor(monitor.state(tail=160))
    since["epoch"] = 99999

    delta = monitor.state(tail=160, since=since)
    assert all(d["resync"] for d in delta["devices"])
    assert delta["analysis"] is not None, "epoch 불일치면 섹션도 전부 보내야 한다"
    assert delta["filter"] is not None


# --------------------------------------------------------------------------- 섹션 생략


def test_unchanged_sections_are_omitted():
    monitor = make_monitor()
    monitor.append_lines("Dev0", "a")
    since = cursor(monitor.state(tail=160))

    delta = monitor.state(tail=160, since=since)
    assert delta["analysis"] is None
    assert delta["alerts"] is None
    assert delta["filter"] is None
    assert delta["pinned"] is None
    assert all(d["checklist"] is None for d in delta["devices"])


def test_changed_analysis_is_sent_again():
    monitor = make_monitor()
    since = cursor(monitor.state(tail=160))

    monitor.apply_alerts([dict(ALERT_TEMPLATE, device="Dev0", alert_id="a1")])
    delta = monitor.state(tail=160, since=since)
    assert delta["analysis"] is not None, "분석이 바뀌었는데 생략됐다"
    assert delta["alerts"] is not None
    assert any(d["checklist"] is not None for d in delta["devices"]), "체크리스트가 바뀌었는데 생략됐다"


def test_versions_are_always_present():
    """섹션을 생략하더라도 지문은 항상 보내야 한다 — 클라이언트가 커서를 갱신해야 하므로."""
    monitor = make_monitor()
    since = cursor(monitor.state(tail=160))
    delta = monitor.state(tail=160, since=since)
    assert set(delta["versions"]) == {"analysis", "checklist", "pinned", "filter", "alerts", "devices"}
    assert all(isinstance(v, str) and v for v in delta["versions"].values())


# --------------------------------------------------------------------------- 원자성


def test_delta_and_sections_come_from_one_snapshot():
    """로그 델타와 체크리스트/분석이 같은 시점에서 나와야 한다.

    이 모듈이 존재하는 이유가 그것이다 — 어긋나면 "로그에는 DOWN 이 보이는데 체크리스트는
    정상"인 화면이 나온다. 경고를 넣은 직후의 응답에서 세 가지가 함께 바뀌는지 본다.
    """
    monitor = make_monitor()
    since = cursor(monitor.state(tail=160))

    monitor.append_lines("Dev0", "%LINEPROTO-5-UPDOWN Et1 down")
    monitor.apply_alerts([dict(ALERT_TEMPLATE, device="Dev0", alert_id="a1")])

    delta = monitor.state(tail=160, since=since)
    entry = device_entry(delta, "Dev0")
    assert entry["lines"], "새 줄이 실리지 않았다"
    assert entry["checklist"] is not None, "체크리스트가 함께 오지 않았다"
    assert delta["analysis"] is not None, "분석이 함께 오지 않았다"
    assert entry["status"] == "fail", "장비 상태가 경고를 반영하지 않았다"
    assert delta["analysis"]["counts"]["CRITICAL"] == 1


def test_line_seq_is_globally_unique_and_per_device_increasing():
    """seq 는 **전역 카운터 하나**에서 나와야 한다 — 장비별로 따로 매기면 재동기화가 틀린다.

    장비별 커서(`devices: {name: seq}`)로 델타를 판정하므로 필요한 불변식은 두 개다:
      * 전역 유일 — 두 장비가 같은 seq 를 쓰면 한쪽 커서로 다른 쪽 줄을 건너뛴다.
      * 장비 안에서 증가 — `seq > since_seq` 필터가 오래된 줄을 다시 보내지 않는다.
    장비를 가로질러 평탄화한 목록은 정렬돼 있을 수 없다(Dev0 이 1,3 을 갖고 Dev1 이 2 를 갖는다).
    """
    monitor = make_monitor()
    monitor.append_lines("Dev0", "a")
    monitor.append_lines("Dev1", "b")
    monitor.append_lines("Dev0", "c")

    state = monitor.state(tail=160)
    per_device = {d["device"]: [line["seq"] for line in d["lines"]] for d in state["devices"]}
    flat = [seq for seqs in per_device.values() for seq in seqs]

    assert len(flat) == len(set(flat)), f"seq 가 겹친다: {per_device}"
    for device, seqs in per_device.items():
        assert seqs == sorted(seqs), f"{device} 안에서 seq 가 증가하지 않는다: {seqs}"
    assert per_device["Dev0"] == [1, 3] and per_device["Dev1"] == [2], (
        f"전역 카운터가 아니라 장비별 카운터를 쓰고 있다: {per_device}"
    )


def test_restored_lines_get_sequence_numbers():
    """저장본 복원(프로그램 재시작)으로 들어온 줄도 seq 를 가져야 한다."""
    monitor = make_monitor()
    snapshot = {
        "devices": ["Dev0"], "baseline_devices": ["Dev0"], "checklists": {},
        "alerts": [], "lines": {"Dev0": [{"ts": "--:--:--", "text": "복원된 줄"}]},
        "last_activity": {}, "started_at": 0,
    }
    monitor.restore(snapshot)
    entry = device_entry(monitor.state(tail=160), "Dev0")
    restored = [l for l in entry["lines"] if l["text"] == "복원된 줄"]
    assert restored and restored[0].get("seq"), "복원된 줄에 seq 가 없다 — 델타 판정이 깨진다"


# --------------------------------------------------------------------------- payload 크기


def test_delta_payload_is_much_smaller():
    """이 항목의 목적 — 실측으로 확인한다(수용 기준: 장비 30대에서 50 KB/s 이하)."""
    import json

    count = 30
    devices = [f"Dev{i}" for i in range(count)]
    monitor = RealtimeMonitor()
    monitor.reset(devices, devices)
    for device in devices:
        monitor.append_lines(device, "\n".join(
            f"{device} %LINEPROTO-5-UPDOWN Interface Ethernet{i} changed state to down"
            for i in range(400)))
    monitor.apply_alerts([dict(ALERT_TEMPLATE, device=devices[i % count], alert_id=f"a{i}")
                          for i in range(300)])

    full = monitor.state(tail=160)
    delta = monitor.state(tail=160, since=cursor(full))

    def kilobytes(value):
        return len(json.dumps(value, ensure_ascii=False).encode()) / 1024

    full_kb, delta_kb = kilobytes(full), kilobytes(delta)
    assert full_kb > 400, f"전체 스냅샷이 예상보다 작다({full_kb:.0f} KB) — 테스트 전제 확인"
    assert delta_kb < full_kb / 20, f"델타가 충분히 작지 않다: {delta_kb:.1f} KB vs {full_kb:.0f} KB"
    assert delta_kb / 0.8 <= 50, f"0.8초 폴링 기준 {delta_kb / 0.8:.1f} KB/s — 수용 기준 50 초과"
