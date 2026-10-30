"""규칙 실험실 — 붙여넣은 줄을 실시간 감시와 같은 판정기에 한 줄씩 통과시켜 층마다 결과를 보인다(IMPROVEMENT_PLAN 4-8).

같은 줄이 왜 경고가 됐는지(또는 왜 안 됐는지)는 지금까지 코드를 읽어야만 알 수 있었다 — 판정이 두 갈래(diff 엔진과
규칙 엔진)이고 규칙 엔진은 다시 네 층(맥락 → 억제 → 서명·카운터 → 키워드)이어서다. 실험실은 판정을 새로 짜지 않고
**같은 객체를 같은 순서로** 부른다. 그래서 여기 결과와 실시간 감시의 결과가 갈릴 수 없다:
  * diff 엔진: classify_line(작업자 입력인가 장비 출력인가)과 새 BaselineDiffEngine 이 그 줄로 낸 경고·복구.
  * 규칙 엔진: ContextTracker 로 맥락을 따라가며 Suppressor·SignatureMatcher·키워드를 각각 물어 보이고, 최종 판정은
    RuleEngine.evaluate 그대로다. 실시간에 올라가는지는 새 RealtimeRuleStream 에 같은 줄을 넣어 본다(최소 심각도·
    show logging 과거 기록·중복 접기).
실행 중인 감시의 엔진·문맥은 건드리지 않는다 — 판정기는 매번 새로 만든다(규칙 엔진만 프로세스 공용이다).

lint_rules() 는 규칙 파일(config/log_rules.json)에서 판정을 조용히 바꾸는 실수를 찾는다: 컴파일되지 않는 정규식,
빈 줄에도 맞는 정규식, 모든 줄을 억제하는 빈 정상 문구, 중복 id, 모르는 심각도, 어떤 판정도 내지 않는 이름을
기다리는 상관 규칙.
"""
import json
import re

from core.ansi_sanitizer import strip_ansi

MAX_LINES = 400          # 실험실 한 번에 판정할 줄 수 — 화면 표가 감당할 만큼
LAB_DEVICE = "LAB"       # 장비를 고르지 않았을 때 쓰는 이름(Baseline 없음)

# 규칙 파일의 최상위 키 중 엔진이 읽는 것 — 이 밖의 키는 오타일 가능성이 크다('_'로 시작하면 주석).
_KNOWN_KEYS = ("anomaly_keywords", "benign_phrases", "keyword_severity", "default_severity", "counter_columns",
               "suppressions", "signatures", "correlation_rules", "throttle")


# --------------------------------------------------------------------------- 실험실

def run_lab(text, device="", store=None):
    """줄마다 diff 엔진과 규칙 엔진의 판정을 돌려준다.

    store: Baseline 저장소(engine.baseline_store.BaselineStore) — 감시 중인 프로파일의 것을 넘기면 그 장비의
           Baseline 대조까지 같다. 없으면 빈 저장소(Baseline 없이 감시를 시작했을 때와 같다).
    반환: {device, baseline, lines:[...], truncated, summary:{...}}
    """
    from engine.baseline_diff_engine import COMMAND, BaselineDiffEngine, classify_line
    from engine.baseline_store import BaselineStore
    from engine.log_rule_engine import ContextTracker, get_engine
    from engine.realtime_rule_stream import RealtimeRuleStream

    device = (device or "").strip() or LAB_DEVICE
    store = store if store is not None else BaselineStore()
    lines = strip_ansi(text or "").splitlines()
    truncated = max(0, len(lines) - MAX_LINES)
    engine = get_engine()
    tracker = ContextTracker()
    stream = RealtimeRuleStream(rule_engine=engine)
    diff = BaselineDiffEngine(store)
    rows = []
    for no, raw in enumerate(lines[:MAX_LINES], 1):
        line = raw.rstrip()
        kind, body, mode = classify_line(line)
        boundary = tracker.feed(line)
        rule = _rule_path(engine, line, tracker, boundary)
        raised = stream.analyze(device, line + "\n")
        alerts = diff.analyze_stream(device, line + "\n")
        resolved = diff.drain_resolutions()
        diff.drain_repeats()
        rows.append({
            "no": no,
            "line": line,
            "kind": "command" if kind == COMMAND else "output",
            "command": body if kind == COMMAND else None,
            "mode": mode,
            "context": {"command": tracker.command, "config": tracker.is_config, "help": tracker.is_help,
                        "table": bool(tracker.header_tokens)},
            "rule": rule,
            "realtime": _realtime_note(rule.get("verdict"), raised, covered=bool(alerts)),
            "diff": [{"type": a.get("type"), "severity": a.get("severity"), "message": a.get("message", ""),
                      "component_id": a.get("component_id"), "staged": bool(a.get("staged")),
                      "past_record": bool(a.get("past_record"))} for a in alerts],
            "resolved": len(resolved),
        })
    return {
        "device": device,
        "baseline": device.lower() in {d.lower() for d in store.device_names()},
        "lines": rows,
        "truncated": truncated,
        "summary": {
            "lines": len(rows),
            "rule_hits": sum(1 for r in rows if r["rule"].get("verdict")),
            "realtime": sum(1 for r in rows if r["realtime"]["raised"]),
            "suppressed": sum(1 for r in rows if r["rule"]["stage"] in ("suppressed", "keyword_suppressed")),
            "diff_alerts": sum(len(r["diff"]) for r in rows),
            "recoveries": sum(1 for r in rows if r["resolved"]),
        },
    }


def _rule_path(engine, line, ctx, boundary):
    """RuleEngine.evaluate 와 같은 순서로 층마다 물어본 결과. 최종 판정(verdict)은 evaluate 그대로다.

    stage: boundary(명령 줄·구분선 — 맥락만 바뀐다) / blank / help(`?` 도움말) / suppressed(구조 억제) /
           signature / counter / keyword / keyword_suppressed(키워드는 맞았지만 억제) / none
    """
    if boundary:
        return {"stage": "boundary", "verdict": None}
    if not line.strip():
        return {"stage": "blank", "verdict": None}
    if getattr(ctx, "is_help", False):
        return {"stage": "help", "verdict": None}
    structural = engine.suppressor.check(line, None, ctx, include_config_scope=False)
    signature = engine.signatures.match_signature(line, ctx)
    counter = [[name, value] for name, value in engine.signatures.counter_row(line, ctx)]
    keyword = engine.find_keyword(line)
    keyword_suppressed = engine.suppressor.check(line, keyword, ctx) if keyword else None
    verdict = engine.evaluate(line, ctx)
    if structural:
        stage = "suppressed"
    elif signature:
        stage = "signature"
    elif counter:
        stage = "counter"
    elif keyword:
        stage = "keyword_suppressed" if keyword_suppressed else "keyword"
    else:
        stage = "none"
    return {"stage": stage, "suppressed": structural, "signature": signature, "counter": counter,
            "keyword": keyword, "keyword_suppressed": keyword_suppressed, "verdict": verdict}


def _realtime_note(verdict, raised, covered=False):
    """그 판정이 실시간 감시 목록에 올라가는가, 아니면 왜 아닌가.

    covered: diff 엔진이 같은 줄로 경고를 냈다 — 감시 서비스는 그 줄의 규칙 경고를 버린다(복구로 해제되는 diff
             경고만 남긴다 — engine/realtime_watch_service.py 의 on_stream_delta)."""
    from engine.log_rule_engine import SEVERITY_ORDER
    from engine.realtime_rule_stream import MIN_SEVERITY

    if verdict and covered:
        return {"raised": False, "severity": None,
                "note": "diff 엔진이 같은 줄을 판정했다 — 규칙 경고는 버리고 복구로 해제되는 diff 경고를 남긴다"}
    if raised:
        alert = raised[0]
        if alert.get("past_record"):
            return {"raised": False, "severity": alert.get("severity"),
                    "note": "show logging 등 장비에 남은 과거 기록 — 이력에만 남는다"}
        return {"raised": True, "severity": alert.get("severity"), "note": "실시간 감시 목록에 올라간다"}
    if not verdict:
        return {"raised": False, "severity": None, "note": ""}
    severity = (verdict.get("severity") or "").lower()
    if severity not in SEVERITY_ORDER:
        return {"raised": False, "severity": None,
                "note": f"모르는 심각도 '{verdict.get('severity')}' — 실시간에 올라가지 않는다(규칙 파일 확인)"}
    if SEVERITY_ORDER.index(severity) < SEVERITY_ORDER.index(MIN_SEVERITY):
        return {"raised": False, "severity": None,
                "note": f"{severity} — 실시간 목록에는 {MIN_SEVERITY} 이상만 올린다(점검 로그 분석에는 남는다)"}
    return {"raised": False, "severity": None, "note": "같은 판정이 이미 올라가 접혔다(60초 안의 중복)"}


# --------------------------------------------------------------------------- 린트

def lint_rules(path=None):
    """규칙 파일을 읽어 판정을 조용히 바꾸는 실수를 찾는다.

    반환: {clean(오류 없음), path, readable, counts, realtime_signatures, findings:[{level, where, message}]}
          level: error(규칙이 빠지거나 모든 줄에 걸린다) / warn(의도와 다르게 동작할 수 있다) / info
    """
    from engine.log_rule_engine import SEVERITY_ORDER, rules_version
    from engine.realtime_rule_stream import MIN_SEVERITY

    target = path or rules_version()[0]
    findings = []

    def add(level, where, message):
        findings.append({"level": level, "where": where, "message": message})

    try:
        with open(target, "r", encoding="utf-8-sig") as f:
            rules = json.load(f)
        if not isinstance(rules, dict):
            raise ValueError("최상위가 객체({ ... })가 아닙니다")
        readable = True
    except Exception as exc:
        add("error", "파일", f"읽지 못해 기본 키워드만으로 판정한다 — {type(exc).__name__}: {exc}")
        return {"clean": False, "path": target, "readable": False, "counts": {}, "realtime_signatures": 0,
                "findings": findings}

    for key in rules:
        if not key.startswith("_") and key not in _KNOWN_KEYS:
            add("warn", key, "엔진이 읽지 않는 항목이다(이름 오타?)")

    severities = set(SEVERITY_ORDER)
    signature_ids, categories = _lint_patterns(rules.get("signatures"), "signatures", severities, add)
    _lint_patterns(rules.get("suppressions"), "suppressions", None, add)

    keywords = [str(k) for k in rules.get("anomaly_keywords") or []]
    upper = [k.upper() for k in keywords]
    for kw in sorted({k for k in upper if upper.count(k) > 1}):
        add("info", "anomaly_keywords", f"'{kw}' 가 두 번 이상 있다")
    keyword_severity = rules.get("keyword_severity") or {}
    for kw, sev in keyword_severity.items():
        if str(kw).upper() not in upper:
            add("warn", f"keyword_severity.{kw}", "anomaly_keywords 에 없는 키워드라 쓰이지 않는다")
        if str(sev).lower() not in severities:
            add("warn", f"keyword_severity.{kw}", f"모르는 심각도 '{sev}' — 실시간 감시에 올라가지 않는다")
    default = str(rules.get("default_severity", "major")).lower()
    if default not in severities:
        add("warn", "default_severity", f"모르는 심각도 '{rules.get('default_severity')}'")
    for i, phrase in enumerate(rules.get("benign_phrases") or []):
        if not str(phrase).strip():
            add("error", f"benign_phrases[{i}]", "빈 문구는 모든 줄에 들어 있다 — 키워드 판정이 전부 억제된다")
    columns = [str(c).upper() for c in rules.get("counter_columns") or []]
    for col in sorted({c for c in columns if columns.count(c) > 1}):
        add("info", "counter_columns", f"'{col}' 가 두 번 이상 있다")

    # 상관 규칙이 기다리는 이름 — 서명 id, 키워드·카운터 판정의 rule_id, 판정이 다는 분류(category_tag).
    produced = set(signature_ids) | {f"keyword_{k.lower()}" for k in keywords} | {"counter_nonzero"}
    produced |= set(categories) | {"general", "interface"}
    seen_ids = set()
    for i, rule in enumerate(rules.get("correlation_rules") or []):
        if not isinstance(rule, dict) or set(rule) <= {"_comment"}:
            continue
        where = f"correlation_rules[{i}] {rule.get('id') or '(id 없음)'}"
        if not rule.get("id"):
            add("warn", where, "id 가 없다 — 결과가 'correlated' 로 묶인다")
        elif rule["id"] in seen_ids:
            add("error", where, "id 가 겹친다")
        seen_ids.add(rule.get("id"))
        names = list(rule.get("all_of") or []) + list(rule.get("any_of") or [])
        if not names:
            add("error", where, "all_of·any_of 가 비어 있어 성립하지 않는다")
        for name in names:
            if name not in produced:
                add("warn", where, f"'{name}' 을(를) 내는 판정이 없다 — 이 조건은 채워지지 않는다")
        if str(rule.get("severity", "major")).lower() not in severities:
            add("warn", where, f"모르는 심각도 '{rule.get('severity')}'")
        if (rule.get("scope") or "interface") not in ("interface", "global"):
            add("warn", where, f"scope 는 interface 또는 global 이다('{rule.get('scope')}' 는 interface 로 본다)")

    signatures = [e for e in rules.get("signatures") or [] if isinstance(e, dict) and "pattern" in e]
    realtime = sum(1 for e in signatures
                   if str(e.get("severity", default)).lower() in severities
                   and SEVERITY_ORDER.index(str(e.get("severity", default)).lower())
                   >= SEVERITY_ORDER.index(MIN_SEVERITY))
    counts = {"signatures": len(signatures),
              "suppressions": sum(1 for e in rules.get("suppressions") or [] if isinstance(e, dict) and "pattern" in e),
              "keywords": len(keywords),
              "correlation_rules": sum(1 for r in rules.get("correlation_rules") or []
                                       if isinstance(r, dict) and set(r) - {"_comment"})}
    return {"clean": not any(f["level"] == "error" for f in findings), "path": target, "readable": readable,
            "counts": counts, "realtime_signatures": realtime, "findings": findings}


def _lint_patterns(entries, section, severities, add):
    """서명(signatures)·억제(suppressions) 목록 검사. 반환: (id 목록, 분류 목록)."""
    ids, categories = [], []
    for i, entry in enumerate(entries or []):
        if not isinstance(entry, dict):
            add("error", f"{section}[{i}]", "객체({ ... })가 아니다 — 건너뛴다")
            continue
        if "pattern" not in entry:
            if set(entry) - {"_comment"}:
                add("error", f"{section}[{i}] {entry.get('id') or ''}".strip(), "pattern 이 없어 건너뛴다")
            continue
        where = f"{section}[{i}] {entry.get('id') or '(id 없음)'}"
        if not entry.get("id"):
            add("warn", where, "id 가 없다 — 화면의 규칙 숨기기·고정이 이 규칙을 가리킬 수 없다")
        elif entry["id"] in ids:
            add("error", where, "id 가 겹친다 — 숨기기·고정·상관 규칙이 둘을 구분하지 못한다")
        ids.append(entry.get("id"))
        try:
            rx = re.compile(entry["pattern"], re.IGNORECASE)
        except (re.error, TypeError) as exc:
            add("error", where, f"pattern 이 컴파일되지 않아 이 규칙이 빠진다 — {exc}")
            rx = None
        if rx is not None and rx.search(""):
            add("error", where, "pattern 이 빈 문자열에도 맞는다 — 모든 줄에 걸린다")
        if entry.get("scope"):
            try:
                re.compile(entry["scope"], re.IGNORECASE)
            except (re.error, TypeError) as exc:
                add("error", where, f"scope 가 컴파일되지 않아 모든 명령에 적용된다 — {exc}")
        if severities is not None:
            severity = str(entry.get("severity", "major")).lower()
            if severity not in severities:
                add("warn", where, f"모르는 심각도 '{entry.get('severity')}' — 실시간 감시에 올라가지 않는다")
            if entry.get("category"):
                categories.append(entry["category"])
    return [i for i in ids if i], categories
