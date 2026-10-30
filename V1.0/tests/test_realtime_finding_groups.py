"""실시간 오류 분석 목록의 묶음 단위 — 같은 오류는 한 줄, 다른 오류는 다른 줄.

보고된 증상: 목록에 "기타 / 7대 · 92건" 한 줄이 떠 있는데, 열어 보면 성질이 다른 여러 오류가
그 안에 섞여 있었다. 원인은 묶는 키가 category 였다는 것이다:

  * 체크리스트 항목(CHECK_ITEMS)에 매핑되지 않는 규칙 엔진 경고는 전부 category=None(기타)로
    떨어진다 — '비정상 재기동'·'인증 실패'·'카운터 증가'가 한 줄로 합쳐졌다.
  * 같은 category 안에 성질이 다른 판정이 공존한다 — 'MLAG peer-link 이상(split-brain, 즉시
    조치)'과 'STP/MLAG 상태 변화(확인 대상)'가 둘 다 stp_mlag 이다.

그래서 finding 마다 group_key(어떤 판정에서 나왔는지, 규칙 경고면 rule_id 까지)를 붙였다.
이 파일이 보는 것은 그 키의 두 방향이다 — **같은 오류는 반드시 합쳐지고, 다른 오류는 절대
합쳐지지 않는다**. 장비를 가로지르는 병합(4대 = 한 줄)은 화면이 하지만, 그 근거인 키는 여기서
장비와 무관하게 같은 값이어야 한다.
"""
import pytest

from engine.realtime_monitor import RealtimeMonitor

DEVICES = ("Core1", "Core2", "Agg1")


def make_monitor(devices=DEVICES):
    monitor = RealtimeMonitor()
    monitor.reset(list(devices), list(devices))
    return monitor


def rule_alert(device, rule_id, message, *, severity="MAJOR", alert_id=None, raw_line="log"):
    """규칙 엔진(config/log_rules.json)이 낸 경고 — 체크리스트 축에 매핑되지 않는다.

    필드 구성은 api/terminal_inspection_api.py 가 실제로 만드는 것과 같다
    (type=rule_id, rule_id, message=규칙 title).
    """
    return {"device": device, "type": rule_id, "rule_id": rule_id, "severity": severity,
            "message": message, "raw_line": raw_line, "ts": "12:00:00",
            "alert_id": alert_id or f"{device}-{rule_id}"}


def groups(monitor):
    """화면(rtmGroupFindings)이 하는 묶음을 그대로 재현 — group_key 로 묶고 장비를 합친다."""
    findings = monitor.state(tail=10)["analysis"]["findings"]
    out = {}
    for f in findings:
        key = f.get("group_key") or f.get("category") or "etc"
        group = out.setdefault(key, {"label": f.get("group_label"), "devices": [], "count": 0})
        group["count"] += f.get("count") or 0
        if f["device"] not in group["devices"]:
            group["devices"].append(f["device"])
    return out


# --------------------------------------------------------------- 다른 오류는 갈라져야 한다


def test_different_rules_do_not_merge_into_one_row():
    """category 가 없는(기타) 규칙 경고 셋 — 예전에는 이 셋이 한 줄이었다."""
    monitor = make_monitor()
    monitor.apply_alerts([
        rule_alert("Core1", "unexpected_reload", "비정상 재기동 이력"),
        rule_alert("Core2", "unexpected_reload", "비정상 재기동 이력"),
        rule_alert("Core1", "auth_failure", "인증 실패 누적"),
        rule_alert("Agg1", "counter_nonzero", "카운터 표에서 0이 아닌 값 검출 (CRC=12)"),
    ])

    result = groups(monitor)
    assert len(result) == 3, f"규칙 3종이 {len(result)}줄로 묶였다: {list(result)}"

    # 같은 규칙은 장비가 달라도 한 줄 — 그 줄에 두 장비가 들어간다.
    reload_group = next(g for k, g in result.items() if "unexpected_reload" in k)
    assert sorted(reload_group["devices"]) == ["Core1", "Core2"]
    assert reload_group["count"] == 2
    # 목록에 쓸 이름은 id 가 아니라 규칙 문구다.
    assert reload_group["label"] == "비정상 재기동 이력"


def test_same_category_different_verdicts_do_not_merge():
    """MLAG peer-link 이상(즉시 조치)과 STP/MLAG 상태 변화(확인 대상)는 둘 다 stp_mlag 이다."""
    monitor = make_monitor()
    monitor.apply_alerts([
        {"device": "Core1", "type": "MLAG_PEER_DOWN", "severity": "CRITICAL", "target": "mlag:peer",
         "message": "peer-link down", "raw_line": "MLAG peer down", "ts": "12:00:00",
         "alert_id": "m1"},
        {"device": "Core2", "type": "STP_CHANGE", "severity": "MAJOR", "target": "mlag:stp",
         "message": "topology change", "raw_line": "STP topology change", "ts": "12:00:01",
         "alert_id": "s1"},
    ])

    result = groups(monitor)
    assert set(result) == {"stp_mlag:mlag_peer_link", "stp_mlag:topology_change"}, (
        f"성질이 다른 두 판정이 합쳐졌다: {list(result)}")


def test_keyword_rules_fall_back_to_readable_id():
    """줄마다 문구가 갈리는 판정(키워드/syslog)은 문구 대신 규칙 이름으로 묶어야 한다.

    문구를 라벨로 쓰면 같은 규칙이 장비마다 다른 줄로 갈린다 — 묶음 키는 규칙이고, 라벨은
    거기서 파생된 하나여야 한다.
    """
    monitor = make_monitor()
    monitor.apply_alerts([
        rule_alert("Core1", "keyword_errdisable", "키워드 'errdisable' 검출 — Et1"),
        rule_alert("Core2", "keyword_errdisable", "키워드 'errdisable' 검출 — Et9"),
    ])

    result = groups(monitor)
    assert len(result) == 1, f"같은 규칙이 갈라졌다: {list(result)}"
    group = next(iter(result.values()))
    assert sorted(group["devices"]) == ["Core1", "Core2"]
    assert group["label"] == "키워드 'errdisable'", group["label"]


# --------------------------------------------------------------- 같은 오류는 합쳐져야 한다


def test_structured_verdicts_share_a_key_across_devices():
    """장비 3대에서 같은 판정이 나면 목록은 한 줄이어야 한다(예전 동작 유지)."""
    monitor = make_monitor()
    monitor.apply_alerts([
        {"device": device, "type": "CONFIG_REMOVED", "severity": "CRITICAL",
         "target": f"vlan:{100 + i}", "message": f"VLAN {100 + i} 삭제",
         "raw_line": f"no vlan {100 + i}", "ts": "12:00:00", "alert_id": f"c{i}"}
        for i, device in enumerate(DEVICES)
    ])

    result = groups(monitor)
    assert set(result) == {"vlan:config_change"}, list(result)
    assert sorted(result["vlan:config_change"]["devices"]) == sorted(DEVICES)


def test_every_finding_carries_a_group_key():
    """group_key 가 빠지면 화면이 category 로 물러나 예전 증상이 그대로 돌아온다."""
    monitor = make_monitor()
    monitor.apply_alerts([
        {"device": "Core1", "type": "DESTRUCTIVE_COMMAND", "severity": "CRITICAL", "target": "cmd:reload",
         "message": "reload", "raw_line": "reload", "ts": "12:00:00", "alert_id": "d1"},
        {"device": "Core2", "type": "LINK_DOWN", "severity": "CRITICAL", "target": "link:Et1",
         "message": "link down", "raw_line": "Et1 down", "ts": "12:00:00", "alert_id": "l1"},
        rule_alert("Agg1", "unexpected_reload", "비정상 재기동 이력"),
    ])

    findings = monitor.state(tail=10)["analysis"]["findings"]
    assert findings
    for f in findings:
        assert f.get("group_key"), f
        assert f.get("group_label"), f


def test_dropped_count_is_reported(monkeypatch):
    """상한을 넘겨 자른 몫은 응답에 실려야 한다 — 조용히 자르면 '이게 전부'로 읽힌다."""
    from engine import realtime_monitor as rtm
    monkeypatch.setattr(rtm, "_FINDING_MAX", 2)

    monitor = make_monitor()
    monitor.apply_alerts([rule_alert(device, f"rule_{i}", f"규칙 {i}", alert_id=f"x{i}")
                          for i, device in enumerate(DEVICES)])

    analysis = monitor.state(tail=10)["analysis"]
    assert len(analysis["findings"]) == 2
    assert analysis["findings_dropped"] == 1
