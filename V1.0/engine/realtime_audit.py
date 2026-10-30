"""실시간 감시 이력(감사 기록) — 정기점검 보고서의 부록(IMPROVEMENT_PLAN 4-8).

실시간 감시는 프로파일(회차)마다 상태를 저장한다(<프로파일>/realtime_state.json — engine/realtime_watch_service).
그 안의 경고마다 언제 났고 어떻게 닫혔는지(복구 줄로 자동 해제·사용자의 '해결'·'무시'와 메모, 아직 열림)가 남아
있다. 보고서에 그 기록을 붙여 점검 중에 무엇이 일어났고 어떻게 처리했는지를 낸다.

  * 저장본은 읽기만 한다 — 깨진 파일을 옆으로 옮기는 realtime_monitor.load_snapshot 을 쓰지 않는다(보고서를 만드는
    일이 감시 상태를 바꾸면 안 된다). 깨졌으면 직전 저장본(.bak)을 읽는다.
  * 비밀값(비밀번호 해시·SNMP 커뮤니티·키)은 가린다 — 작업자가 세션에 친 설정 줄이 근거로 남아 있을 수 있다.
    장비명·주소는 그대로다(보고서 본문에도 있는 고객 자신의 정보다).
  * 장비에 남은 과거 기록(show logging 출력)은 이 회차의 사건이 아니므로 뺀다.
"""
import json
import os

from core.utils import parse_iso

STATE_FILE = "realtime_state.json"
_SEVERITY_LABEL = {"CRITICAL": "심각", "MAJOR": "주요", "WARNING": "경고"}
_SEVERITY_ORDER = ("CRITICAL", "MAJOR", "WARNING")
_OUTCOME_LABEL = {"auto": "자동 복구", "manual": "수동 해결", "ignored": "무시", "open": "미해결"}


def state_path(customer, profile):
    from engine import log_storage
    return os.path.join(log_storage.get_profile_dir(customer, profile), STATE_FILE)


def audit(customer, profile, devices=None):
    """이 회차의 실시간 감시 이력. 감시를 쓰지 않은 회차(저장본 없음)는 None.

    devices: 보고서에 넣은 장비 이름 — 주면 그 장비의 기록만 남기고 뺀 수를 excluded 로 알린다.
    반환: {rows, counts, open_faults, period, watched, syslog_devices, excluded}
      rows: 오래된 것부터 [{at, device, severity, severity_label, message, raw, outcome, outcome_label,
             closed_at, duration_sec, note, earlier}]
    """
    snapshot = _read(state_path(customer, profile))
    if snapshot is None:
        return None
    from engine.log_masking import SECRET_KEYS, apply_masking
    from engine.realtime_monitor import condition_label

    def clean(text):
        return apply_masking(str(text or "").strip(), SECRET_KEYS)

    keep = {d.lower() for d in devices} if devices else None
    rows, excluded = [], 0
    for alert in snapshot.get("alerts") or []:
        if not isinstance(alert, dict) or alert.get("past_record"):
            continue
        device = alert.get("device") or ""
        if keep is not None and device.lower() not in keep:
            excluded += 1
            continue
        outcome = _outcome(alert)
        if outcome == "auto":
            note = f"복구 근거: {clean(alert.get('resolved_by'))}" if alert.get("resolved_by") else ""
        elif outcome == "manual":
            note = clean(alert.get("resolved_by"))
        elif outcome == "ignored":
            note = clean(alert.get("ignored_note"))
        else:
            note = ""
        severity = str(alert.get("severity") or "WARNING").upper()
        rows.append({
            "at": _when(alert.get("at")) or str(alert.get("ts") or ""),
            "sort": alert.get("at") or "",
            "device": device,
            "severity": severity,
            "severity_label": _SEVERITY_LABEL.get(severity, severity),
            "message": clean(alert.get("message")),
            "raw": clean(alert.get("raw_line")),
            "outcome": outcome,
            "outcome_label": _OUTCOME_LABEL[outcome],
            "closed_at": (_when(alert.get("resolved_at")) if outcome in ("auto", "manual")
                          else _when(alert.get("ignored_ts")) if outcome == "ignored" else ""),
            "duration_sec": alert.get("duration_sec") if outcome == "auto" else None,
            "note": note,
            # 감시를 시작할 때 세션 로그를 되짚어 읽은 구간(또는 그 기록을 되살린 것)에서 난 경고
            "earlier": bool(alert.get("history")),
        })
    rows.sort(key=lambda r: r["sort"])
    for row in rows:
        row.pop("sort")

    counts = {"total": len(rows), **{key: 0 for key in _OUTCOME_LABEL},
              **{sev: sum(1 for r in rows if r["severity"] == sev) for sev in _SEVERITY_ORDER}}
    for row in rows:
        counts[row["outcome"]] += 1
    open_faults = [{"device": c.get("device"), "component_id": c.get("component_id"),
                    "label": condition_label(c.get("condition")), "since": _epoch_text(c.get("since"))}
                   for c in snapshot.get("open_conditions") or []
                   if isinstance(c, dict) and (keep is None or str(c.get("device") or "").lower() in keep)]
    watched = [d for d in snapshot.get("devices") or [] if keep is None or str(d).lower() in keep]
    observed = snapshot.get("observed") or {}
    stamps = [r["at"] for r in rows if r["at"]]
    return {
        "rows": rows,
        "counts": counts,
        "open_faults": open_faults,
        "period": (stamps[0], stamps[-1]) if stamps else None,
        "watched": len(watched),
        "syslog_devices": sum(1 for d in watched if (observed.get(d) or {}).get("syslog")),
        "excluded": excluded,
    }


def _outcome(alert):
    if alert.get("resolved"):
        # 자동 복구는 StateTracker 가 잰 지속 시간을 갖고, '해결' 버튼(수동)은 갖지 않는다(realtime_watch_service).
        return "manual" if alert.get("duration_sec") is None else "auto"
    if alert.get("ignored"):
        return "ignored"
    return "open"


def _when(stamp):
    """ISO('2026-10-28T14:03:11')·'2026-10-28 14:03:11' -> '10-28 14:03:11'. 읽을 수 없으면 ''."""
    if not stamp:
        return ""
    when = parse_iso(str(stamp).replace(" ", "T", 1))
    return when.strftime("%m-%d %H:%M:%S") if when else ""


def _epoch_text(epoch):
    import time
    try:
        return time.strftime("%m-%d %H:%M:%S", time.localtime(float(epoch)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _read(path):
    """저장본을 읽기만 한다. 없으면 None, 깨졌으면 직전 저장본(.bak), 그것도 안 되면 None."""
    for candidate in (path, path + ".bak"):
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None
