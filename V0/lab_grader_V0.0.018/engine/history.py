"""
채점 결과를 세션(회차)별로 history/{lab_name}/{timestamp}.json 에 저장한다.
"""
import os
import json
import glob
import datetime


def save_history(lab_name, scored, elapsed_sec, base_dir="history", findings=None, diff=None):
    session_ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    lab_dir = os.path.join(base_dir, lab_name)
    os.makedirs(lab_dir, exist_ok=True)

    path = os.path.join(lab_dir, f"{session_ts}.json")
    payload = {
        "session": session_ts,
        "elapsed_sec": round(elapsed_sec, 2),
        "stages": scored,
    }
    if findings is not None:
        payload["findings"] = [f.to_dict() if hasattr(f, "to_dict") else f for f in findings]
    if diff is not None:
        payload["diff"] = diff
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def load_latest(lab_name, base_dir="history"):
    lab_dir = os.path.join(base_dir, lab_name)
    files = sorted(glob.glob(os.path.join(lab_dir, "*.json")))
    if not files:
        return None
    with open(files[-1], encoding="utf-8") as f:
        return json.load(f)


def load_previous(lab_name, base_dir="history"):
    """가장 최근 것 말고 그 전 회차 (diff 비교용)."""
    lab_dir = os.path.join(base_dir, lab_name)
    files = sorted(glob.glob(os.path.join(lab_dir, "*.json")))
    if len(files) < 2:
        return None
    with open(files[-2], encoding="utf-8") as f:
        return json.load(f)


def compare_sessions(prev, curr):
    """
    두 회차의 채점 결과를 비교해서 stage별 PASS/FAIL 증감을 계산.
    반환: [{"stage": str, "prev_pass": int, "curr_pass": int, "delta": int, "trend": "개선"|"퇴보"|"동일"}]
    """
    if not prev:
        return []

    prev_by_id = {s["id"]: s for s in prev["stages"]}
    result = []
    for curr_stage in curr["stages"]:
        prev_stage = prev_by_id.get(curr_stage["id"])
        if not prev_stage:
            continue
        delta = curr_stage["pass"] - prev_stage["pass"]
        trend = "개선" if delta > 0 else ("퇴보" if delta < 0 else "동일")
        result.append({
            "stage": curr_stage["label"],
            "prev_pass": prev_stage["pass"], "prev_total": prev_stage["total"],
            "curr_pass": curr_stage["pass"], "curr_total": curr_stage["total"],
            "delta": delta, "trend": trend,
        })
    return result


STATUS_NEW = "NEW"
STATUS_PERSISTENT = "PERSISTENT"
STATUS_RESOLVED = "RESOLVED"


def _finding_key(f):
    return (f.get("device"), f.get("check_id"))


def _is_open_finding(f):
    """PASS/SKIPPED는 이슈가 아니므로 diff 비교 대상에서 제외."""
    return f.get("result") not in ("PASS", "SKIPPED")


def compare_findings(prev, curr):
    """
    정기점검(inspection mode) 전용 — 두 회차의 Finding을 device+check_id 키로 비교해서
    NEW(신규 발생)/PERSISTENT(재발/유지)/RESOLVED(해소됨)로 분류.
    PASS/SKIPPED Finding은 이슈가 아니므로 비교 대상에서 제외.
    반환: {"new": [Finding dict,...], "persistent": [...], "resolved": [...]}
          각 dict에는 diff_status 필드가 추가되어 붙는다.
    """
    prev_open = {_finding_key(f): f for f in (prev or {}).get("findings", []) if _is_open_finding(f)}
    curr_open = {_finding_key(f): f for f in (curr or {}).get("findings", []) if _is_open_finding(f)}

    new, persistent, resolved = [], [], []
    for key, f in curr_open.items():
        tagged = dict(f, diff_status=STATUS_NEW if key not in prev_open else STATUS_PERSISTENT)
        (new if key not in prev_open else persistent).append(tagged)
    for key, f in prev_open.items():
        if key not in curr_open:
            resolved.append(dict(f, diff_status=STATUS_RESOLVED))

    return {"new": new, "persistent": persistent, "resolved": resolved}


def compare_check_level(prev, curr):
    """개별 check 단위로 PASS<->FAIL 전환된 것만 뽑음 (어떤 항목이 새로 고쳐지거나 새로 망가졌는지)."""
    if not prev:
        return []

    prev_checks = {}
    for stage in prev["stages"]:
        for r in stage.get("results", []):
            prev_checks[r["check"]] = r["result"]

    changes = []
    for stage in curr["stages"]:
        for r in stage.get("results", []):
            prev_result = prev_checks.get(r["check"])
            if prev_result and prev_result != r["result"]:
                changes.append({
                    "check": r["check"], "stage": stage["label"],
                    "from": prev_result, "to": r["result"],
                })
    return changes
