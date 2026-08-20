"""web_ui/js/realtime-monitor-panel.js 의 델타 병합 동작 검증 — OPTIMIZATION_PLAN 3-1 클라이언트.

서버 쪽(engine/realtime_monitor.py)은 tests/test_realtime_delta.py 가 본다. 여기서 보는 것은
**프론트엔드가 그 델타를 원래 상태로 되살리는지**다. 이 항목의 위험은 전부 여기에 있다 —
델타를 잘못 합치면 화면이 조용히 틀린다(사라진 오류가 남아 있거나, 새 줄이 안 붙거나,
체크리스트가 빈 채로 그려진다). 폴링이 0.8초마다 전체를 밀어 넣던 예전 코드에는 이 실패
모드 자체가 없었으므로, 이 파일이 그 안전망을 대신한다.

핵심 테스트는 test_merged_state_matches_full_snapshot 이다: **실제 서버**로 회차를 진행시키며
델타만 받아 합친 결과가, 같은 시점에 전체를 받았을 때와 글자 하나까지 같은지 본다.

node 가 없는 환경에서는 skip 한다(Windows 개발 PC 사정) — tests/test_adaptive_poller.py 와 같다.
"""
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

PANEL_JS = Path(__file__).resolve().parent.parent / "web_ui" / "js" / "realtime-monitor-panel.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node 가 없어 JS 동작 검증을 건너뜁니다")

# 병합에 필요한 최소 조각만 떼어낸다. 패널 전체는 최상단에서 document 를 만지므로 node 에
# 통째로 넣을 수 없다.
_WANTED_FUNCS = ("rtmMergeState", "rtmBuildCursor", "rtmAppendRtmLogLines", "rtmLogLineNode")
_WANTED_CONSTS = ("RTM_TAIL", "RTM_DELTA_SECTIONS", "RTM_BOX_HEIGHT", "RTM_LINE_HEIGHT",
                  "RTM_BOX_LINES")


def _extract(name):
    """최상위 function 하나를 떼어낸다.

    중괄호 깊이를 세지 않는다 — tests/test_adaptive_poller.py 에서 그 방식이 구조분해 파라미터에
    걸려 **서명만** 잘라오는 것을 겪었다. 이 파일의 최상위 함수는 닫는 중괄호가 열 0 에 있다.
    """
    text = PANEL_JS.read_text(encoding="utf-8")
    start = text.index(f"function {name}(")
    collected = []
    for line in text[start:].splitlines(keepends=True):
        collected.append(line)
        if line.rstrip("\r\n") == "}":
            return "".join(collected)
    raise AssertionError(f"{name} 의 끝(열 0 의 닫는 중괄호)을 찾지 못했다")


def _extract_const(name):
    text = PANEL_JS.read_text(encoding="utf-8")
    start = text.index(f"const {name} = ")
    end = text.index("\n", start)
    return text[start:end + 1]


def _panel_source():
    return "".join([_extract_const(c) for c in _WANTED_CONSTS]
                   + [_extract(f) for f in _WANTED_FUNCS])


def _run_js(body, payload=None):
    """떼어낸 소스 + 테스트 본문을 임시 .js 파일로 써서 실행한다.

    `node -e` 를 쓰지 않는 이유: node 22 부터 -e 입력을 TypeScript 로 먼저 해석해서 기본값
    파라미터(`function f(prev, payload = null)`)를 타입 표기로 잘못 읽고 SyntaxError 를 낸다.
    """
    prelude = f"const DATA = {json.dumps(payload or {}, ensure_ascii=False)};\n"
    script = prelude + _panel_source() + "\n" + textwrap.dedent(body)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        path = handle.name
    try:
        completed = subprocess.run(["node", path], capture_output=True, text=True)
    finally:
        os.unlink(path)
    assert completed.returncode == 0, f"node 실행 실패:\n{completed.stderr}"
    return json.loads(completed.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- 추출 자체


def test_source_is_extractable():
    """추출이 서명만 잘라오지 않는지 — adaptive_poller 에서 실제로 그 버그가 있었다."""
    source = _panel_source()
    assert source.count("{") == source.count("}")
    for marker in ("RTM_DELTA_SECTIONS.forEach", "state.devices =", "return { state, changed",
                   "return { epoch: state.epoch", "childElementCount"):
        assert marker in source, f"추출된 소스에 {marker} 가 없다 — 함수가 잘렸다"


# --------------------------------------------------------------------------- 병합 단위 동작


def _full_payload():
    """서버가 since 없이 돌려주는 모양(전체). 필드 이름은 engine/realtime_monitor.state() 와 맞춘다."""
    return {
        "epoch": 1,
        "devices": [{
            "device": "Dev0", "lines": [{"ts": "01:00:00", "text": "a", "seq": 1}],
            "line_count": 1, "line_seq": 1, "resync": True, "checklist": [{"key": "k", "status": "pending"}],
            "fail_count": 0, "warn_count": 0, "status": "ok", "has_baseline": True, "last_activity": 0,
        }],
        "pinned": [], "alerts": [], "analysis": {"counts": {}, "findings": [], "verdict": "ok"},
        "filter": {"hidden_rules": [], "hidden_devices": [], "hidden_keywords": [], "pinned_items": []},
        "versions": {"analysis": "v1", "checklist": "c1", "pinned": "p1", "filter": "f1",
                     "alerts": "a1", "devices": "d1"},
    }


def test_first_poll_keeps_payload_as_is():
    """커서 없이 받은 첫 응답은 그대로 완전한 상태다 — 여기서 뭘 바꾸면 첫 화면이 틀린다."""
    result = _run_js("""
        const merged = rtmMergeState(null, DATA);
        console.log(JSON.stringify({
          complete: merged.complete,
          changed: [...merged.changed].sort(),
          lines: merged.state.devices[0].lines.map(l => l.text),
          analysis: merged.state.analysis.verdict,
        }));
    """, _full_payload())
    assert result["complete"] is True
    assert result["changed"] == ["alerts", "analysis", "checklist", "devices", "filter", "logs", "pinned"]
    assert result["lines"] == ["a"]


def test_delta_appends_new_lines():
    """새 줄만 온 응답을 직전 사본 뒤에 붙인다 — 이 항목의 본론."""
    prev = _full_payload()
    delta = {
        "epoch": 1,
        "devices": [{
            "device": "Dev0", "lines": [{"ts": "01:00:01", "text": "b", "seq": 2}],
            "line_count": 2, "line_seq": 2, "resync": False, "checklist": None,
            "fail_count": 0, "warn_count": 0, "status": "ok", "has_baseline": True, "last_activity": 0,
        }],
        "pinned": None, "alerts": None, "analysis": None, "filter": None,
        "versions": prev["versions"],
    }
    result = _run_js("""
        const merged = rtmMergeState(DATA.prev, DATA.delta);
        console.log(JSON.stringify({
          complete: merged.complete,
          changed: [...merged.changed].sort(),
          lines: merged.state.devices[0].lines.map(l => l.text),
          appended: Object.keys(merged.appended).map(k => [k, merged.appended[k].length]),
          checklist: merged.state.devices[0].checklist,
          analysis: merged.state.analysis,
        }));
    """, {"prev": prev, "delta": delta})
    assert result["complete"] is True
    assert result["lines"] == ["a", "b"], "새 줄이 직전 줄 뒤에 붙지 않았다"
    assert result["changed"] == ["logs"], f"안 바뀐 섹션이 변경으로 잡혔다: {result['changed']}"
    assert result["appended"] == [["Dev0", 1]]
    assert result["checklist"] == prev["devices"][0]["checklist"], "생략된 체크리스트가 되살아나지 않았다"
    assert result["analysis"] == prev["analysis"], "생략된 분석이 되살아나지 않았다"


def test_no_new_lines_changes_nothing():
    """조용한 회차(새 줄 없음, 모든 섹션 생략)에는 아무 패널도 다시 그리지 않아야 한다.

    이게 3-1 의 두 번째 이득이다 — DOM 이 살아남아서 클릭 고정 강조·스크롤·선택이 유지된다.
    """
    prev = _full_payload()
    quiet = dict(prev, devices=[dict(prev["devices"][0], lines=[], resync=False, checklist=None)],
                 pinned=None, alerts=None, analysis=None, filter=None)
    result = _run_js("""
        const merged = rtmMergeState(DATA.prev, DATA.quiet);
        console.log(JSON.stringify({changed: [...merged.changed], complete: merged.complete,
                                    lines: merged.state.devices[0].lines.map(l => l.text)}));
    """, {"prev": prev, "quiet": quiet})
    assert result["changed"] == [], f"바뀐 게 없는데 다시 그리려 한다: {result['changed']}"
    assert result["complete"] is True
    assert result["lines"] == ["a"], "줄이 없어졌다"


def test_resync_replaces_lines():
    """버퍼가 밀려 resync 가 오면 그 장비의 줄을 통째로 갈아야 한다 — 붙이면 중복이 생긴다."""
    prev = _full_payload()
    resync = dict(prev, devices=[dict(
        prev["devices"][0], lines=[{"ts": "01:00:09", "text": "z", "seq": 9}],
        line_seq=9, resync=True, checklist=None)], pinned=None, alerts=None, analysis=None, filter=None)
    result = _run_js("""
        const merged = rtmMergeState(DATA.prev, DATA.resync);
        console.log(JSON.stringify({lines: merged.state.devices[0].lines.map(l => l.text),
                                    resynced: merged.resynced,
                                    appended: Object.keys(merged.appended)}));
    """, {"prev": prev, "resync": resync})
    assert result["lines"] == ["z"], "resync 인데 직전 줄이 남았다"
    assert result["resynced"] is True, "resync 를 호출부에 알리지 않으면 append 경로로 잘못 간다"
    assert result["appended"] == [], "resync 는 append 대상이 아니다"


def test_lines_are_capped_at_tail():
    """사본이 무한히 자라지 않아야 한다 — 서버가 주는 tail 과 같은 상한을 지킨다."""
    prev = _full_payload()
    prev["devices"][0]["lines"] = [{"ts": "t", "text": f"l{i}", "seq": i} for i in range(1, 161)]
    delta = dict(prev, devices=[dict(
        prev["devices"][0],
        lines=[{"ts": "t", "text": "new", "seq": 161}], line_seq=161, resync=False, checklist=None)],
        pinned=None, alerts=None, analysis=None, filter=None)
    result = _run_js("""
        const merged = rtmMergeState(DATA.prev, DATA.delta);
        const lines = merged.state.devices[0].lines;
        console.log(JSON.stringify({count: lines.length, first: lines[0].text,
                                    last: lines[lines.length - 1].text, tail: RTM_TAIL}));
    """, {"prev": prev, "delta": delta})
    assert result["count"] == result["tail"] == 160
    assert result["first"] == "l2", "가장 오래된 줄이 밀려나지 않았다"
    assert result["last"] == "new"


def test_missing_section_without_previous_marks_incomplete():
    """서버가 생략했는데 되살릴 직전 값이 없으면 커서를 버려야 한다.

    빈 값으로 그리면 '오류가 사라졌다'는 잘못된 화면이 된다. 화면은 직전 것을 그대로 두고
    다음 폴링에서 전체를 받아 메운다.
    """
    payload = dict(_full_payload(), analysis=None)
    result = _run_js("""
        const merged = rtmMergeState(null, DATA);
        console.log(JSON.stringify({complete: merged.complete,
                                    analysis: merged.state.analysis === undefined}));
    """, payload)
    assert result["complete"] is False, "불완전한 상태로 커서를 갱신하면 영구히 빈 패널이 된다"


def test_missing_checklist_without_previous_marks_incomplete():
    """체크리스트도 같다 — 한 장비만 비어도 '정상 N건' 합계가 틀린다."""
    payload = _full_payload()
    payload["devices"][0]["checklist"] = None
    result = _run_js("""
        const merged = rtmMergeState(null, DATA);
        console.log(JSON.stringify({complete: merged.complete,
                                    checklist: merged.state.devices[0].checklist}));
    """, payload)
    assert result["complete"] is False
    assert result["checklist"] == []


def test_device_list_change_is_reported():
    """장비가 늘거나 숨겨지면 좌측 배치와 탭이 달라진다 — append 로는 처리할 수 없다."""
    prev = _full_payload()
    grown = dict(prev, devices=prev["devices"] + [dict(prev["devices"][0], device="Dev1")],
                 pinned=None, alerts=None, analysis=None, filter=None)
    result = _run_js("""
        const merged = rtmMergeState(DATA.prev, DATA.grown);
        console.log(JSON.stringify({changed: [...merged.changed].sort()}));
    """, {"prev": prev, "grown": grown})
    assert "devices" in result["changed"]


def test_epoch_change_forces_full_redraw():
    """감시 재시작/초기화(epoch 증가)는 화면을 통째로 다시 그려야 한다."""
    prev = _full_payload()
    fresh = dict(_full_payload(), epoch=2)
    result = _run_js("""
        const merged = rtmMergeState(DATA.prev, DATA.fresh);
        console.log(JSON.stringify({changed: [...merged.changed].sort()}));
    """, {"prev": prev, "fresh": fresh})
    assert "devices" in result["changed"]


def test_cursor_is_built_from_server_fields():
    """커서는 서버가 준 epoch/versions/line_seq 를 그대로 되돌려주는 것이다 — 재계산이 아니다."""
    result = _run_js("""
        console.log(JSON.stringify(rtmBuildCursor(DATA)));
    """, _full_payload())
    assert result == {"epoch": 1, "versions": _full_payload()["versions"], "devices": {"Dev0": 1}}


def test_cursor_uses_line_seq_not_last_line():
    """line_seq 는 **서버 버퍼의 최신 seq** 다. 받은 줄의 마지막 seq 로 대체하면 안 된다 —
    tail 로 잘려 나간 뒤라도 우리는 그 지점까지 받은 것이다."""
    payload = _full_payload()
    payload["devices"][0]["lines"] = [{"ts": "t", "text": "x", "seq": 500}]
    payload["devices"][0]["line_seq"] = 500
    payload["devices"][0]["line_count"] = 900
    result = _run_js("console.log(JSON.stringify(rtmBuildCursor(DATA)));", payload)
    assert result["devices"] == {"Dev0": 500}


# --------------------------------------------------------------------------- 서버와의 왕복


def _record_interaction():
    """실제 RealtimeMonitor 로 회차를 진행시키며 (델타, 같은 시점의 전체) 쌍을 모은다.

    커서는 **직전 회차의 전체 상태에서 서버가 준 필드를 그대로 읽어** 만든다(epoch / versions /
    장비별 line_seq). 클라이언트 로직을 파이썬으로 재구현하는 것이 아니다 — rtmBuildCursor 가
    정확히 그 세 필드를 되돌려준다는 것은 위 test_cursor_is_built_from_server_fields 가 본다.
    """
    from engine.realtime_monitor import RealtimeMonitor

    monitor = RealtimeMonitor(lines_per_device=40)
    monitor.reset(["Core1", "Core2", "Core3"], ["Core1", "Core2", "Core3"])
    steps = []
    cursor = None

    def cursor_from(full):
        return {"epoch": full["epoch"], "versions": full["versions"],
                "devices": {d["device"]: d["line_seq"] for d in full["devices"]}}

    for step in range(24):
        # 회차마다 다른 일이 벌어지게 한다 — 조용한 회차, 줄만 추가, 경고 발생, 초기화까지.
        if step % 3 != 2:
            for device in ("Core1", "Core2", "Core3"):
                monitor.append_lines(device, f"{device} show interface #{step}")
        if step == 7:
            monitor.apply_alerts([{
                "alert_id": f"al-{step}", "device": "Core2", "severity": "CRITICAL",
                "type": "interface_down", "message": "Et1 down", "ts": "01:00:00",
            }])
        if step == 12:
            monitor.set_filter({"hidden_rules": ["interface_down"], "hidden_devices": [],
                                "hidden_keywords": [], "pinned_items": []})
        if step == 17:
            monitor.clear_alerts()          # epoch 증가 — 클라이언트는 전체를 다시 받아야 한다
        if step == 20:
            # 장비 목록이 **줄어드는** 경로. adopt_devices() 는 추가만 하므로 여기서는 쓸 수 없다
            # (처음에 그걸로 썼다가 목록이 그대로여서 이 회차가 아무것도 검증하지 않았다).
            # 실제 UI 에서 목록이 줄어드는 것은 우클릭 '이 장비 숨기기'이고, state() 가 그
            # 장비를 payload 에서 빼는 것으로 나타난다.
            monitor.set_filter({"hidden_rules": ["interface_down"], "hidden_devices": ["Core2"],
                                "hidden_keywords": [], "pinned_items": []})
        if step == 22:
            monitor.adopt_devices(["Core4"], ["Core4"])          # 목록이 늘어나는 경로
            monitor.append_lines("Core4", "Core4 first line")

        delta = monitor.state(tail=160, since=cursor) if cursor else monitor.state(tail=160)
        full = monitor.state(tail=160)
        steps.append({"delta": delta, "full": full})
        cursor = cursor_from(full)
    return steps


def test_merged_state_matches_full_snapshot():
    """**이 파일의 핵심.** 델타만 받아 합친 결과가 전체를 받은 것과 완전히 같아야 한다.

    24 회차 동안 조용한 회차 / 줄 추가 / 경고 발생 / 규칙 숨김 / 초기화(epoch 증가) /
    장비 목록 변화를 모두 지나간다. 한 회차라도 어긋나면 그 뒤 화면은 계속 틀린 상태로 남는다
    (클라이언트가 자기 사본 위에 계속 쌓기 때문에 스스로 회복하지 못한다).
    """
    steps = _record_interaction()
    result = _run_js("""
        // 비교용 투영 — 병합이 책임지는 필드만 본다.
        // resync 는 응답마다 다른 것이 정상이고(전체 응답은 항상 true), lines 는 델타에서
        // 일부만 오므로 '합친 뒤'와 '전체' 양쪽에 같은 투영을 적용해 비교한다.
        function project(state) {
          return {
            epoch: state.epoch,
            analysis: state.analysis, alerts: state.alerts,
            pinned: state.pinned, filter: state.filter,
            versions: state.versions,
            devices: (state.devices || []).map(d => ({
              device: d.device, line_count: d.line_count, line_seq: d.line_seq,
              status: d.status, fail_count: d.fail_count, warn_count: d.warn_count,
              has_baseline: d.has_baseline, checklist: d.checklist,
              lines: (d.lines || []).map(l => [l.seq, l.ts, l.text]),
            })),
          };
        }
        let prev = null;
        const mismatches = [];
        let incomplete = 0;
        DATA.forEach((step, i) => {
          const merged = rtmMergeState(prev, step.delta);
          if (!merged.complete) { incomplete += 1; prev = null; return; }
          prev = merged.state;
          const got = JSON.stringify(project(merged.state));
          const want = JSON.stringify(project(step.full));
          if (got !== want) mismatches.push({step: i, got, want});
        });
        console.log(JSON.stringify({mismatches: mismatches.slice(0, 2),
                                    count: mismatches.length, incomplete,
                                    steps: DATA.length}));
    """, steps)
    assert result["count"] == 0, (
        f"{result['count']}/{result['steps']} 회차에서 합친 상태가 전체와 다르다: {result['mismatches']}"
    )
    assert result["incomplete"] == 0, (
        "정상 왕복에서 불완전한 병합이 나왔다 — 커서를 계속 버리면 델타가 무의미해진다"
    )


def test_interaction_actually_exercises_delta():
    """위 테스트가 '전부 전체 응답'이라 통과한 게 아닌지 확인한다 — 실제로 생략이 일어나야 한다."""
    steps = _record_interaction()
    omitted = sum(1 for s in steps[1:] if s["delta"].get("analysis") is None)
    line_deltas = [s for s in steps[1:]
                   if not any(d["resync"] for d in s["delta"]["devices"])]
    assert omitted >= 10, f"섹션 생략이 거의 없다 — 델타 경로를 타지 않았다({omitted}회)"
    assert len(line_deltas) >= 10, f"줄 델타가 거의 없다({len(line_deltas)}회)"

    # 아래 세 시나리오가 실제로 일어나는지 — 하나라도 조용히 빠지면 왕복 테스트가 그만큼
    # 약해진다(처음에 adopt_devices 로 '목록 축소'를 시도했다가 그 회차가 아무것도 검증하지
    # 않는 상태를 만들었다).
    names = [tuple(d["device"] for d in s["delta"]["devices"]) for s in steps]
    epochs = {s["delta"]["epoch"] for s in steps}
    assert len(epochs) >= 2, f"epoch 가 바뀌는 회차가 없다: {epochs}"
    assert any(len(a) > len(b) for a, b in zip(names, names[1:])), "장비 목록이 줄어드는 회차가 없다"
    assert any(len(a) < len(b) for a, b in zip(names, names[1:])), "장비 목록이 늘어나는 회차가 없다"
    assert any(s["delta"].get("filter") is not None for s in steps[1:]), "필터가 바뀌는 회차가 없다"


def test_payload_is_much_smaller_than_full():
    """왕복 실측 — 델타가 실제로 작아야 한다(계획의 수용 기준)."""
    steps = _record_interaction()
    quiet = [s for s in steps[1:] if not any(d["resync"] for d in s["delta"]["devices"])]
    delta_bytes = sum(len(json.dumps(s["delta"], ensure_ascii=False).encode()) for s in quiet)
    full_bytes = sum(len(json.dumps(s["full"], ensure_ascii=False).encode()) for s in quiet)
    assert delta_bytes < full_bytes / 3, (
        f"델타가 충분히 작지 않다: {delta_bytes}B vs 전체 {full_bytes}B "
        "(장비 3대·버퍼 40줄의 작은 시나리오라 30대 실측(104배)보다 비율이 낮은 것은 정상)"
    )


# --------------------------------------------------------------------------- DOM append


# 최소 DOM 대역 — rtmAppendRtmLogLines 가 실제로 쓰는 연산만 흉내낸다. jsdom 을 들여오는 것은
# 이 항목에 비해 과하고, 여기서 보고 싶은 것은 '몇 줄을 붙이고 몇 줄을 버리는가'라는 계산이다.
_DOM_SHIM = """
class Node {
  constructor(tag) {
    this.tag = tag; this.className = ''; this.dataset = {};
    this.children = []; this.parent = null; this._text = '';
  }
  get childElementCount() { return this.children.length; }
  get firstElementChild() { return this.children[0] || null; }
  set textContent(v) { this._text = v; this.children = []; }
  get textContent() { return this.children.length ? this.children.map(c => c.textContent).join('') : this._text; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  removeChild(child) { this.children = this.children.filter(c => c !== child); return child; }
  remove() { if (this.parent) this.parent.removeChild(this); }
  _all(out) { this.children.forEach(c => { out.push(c); c._all(out); }); return out; }
  querySelectorAll(sel) { return this._all([]).filter(n => _matches(n, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
// 이 함수가 쓰는 선택자만 지원한다: '.cls', '.cls[data-x]'
function _matches(node, sel) {
  const [cls, attr] = sel.replace(']', '').split('[');
  if (!node.className.split(' ').includes(cls.slice(1))) return false;
  if (!attr) return true;
  const key = attr.replace('data-', '').replace(/-(\\w)/g, (_, c) => c.toUpperCase());
  return node.dataset[key] !== undefined;
}
const document = {
  createElement: (tag) => new Node(tag),
  createTextNode: (text) => { const n = new Node('#text'); n._text = text; return n; },
};
function makeBox(device, max, lineTexts) {
  const box = new Node('div');
  box.className = 'rtm-log-box';
  box.dataset.rtmXhlDevice = device;
  box.dataset.rtmBoxMax = String(max);
  const head = box.appendChild(new Node('div'));
  head.className = 'rtm-log-head';
  const count = head.appendChild(new Node('span'));
  count.className = 'rtm-log-count';
  count.textContent = '0줄';
  const holder = box.appendChild(new Node('div'));
  holder.className = 'rtm-log-lines';
  lineTexts.forEach(t => {
    const row = holder.appendChild(new Node('div'));
    row.className = t === null ? 'rtm-log-line rtm-log-idle' : 'rtm-log-line';
    row.textContent = t === null ? '입력 대기 중…' : t;
  });
  return box;
}
function lineTexts(box) {
  return box.querySelector('.rtm-log-lines').children.map(c => c.textContent);
}
"""


def _run_dom_js(body, payload=None):
    return _run_js(_DOM_SHIM + "\n" + textwrap.dedent(body), payload)


def test_append_adds_only_new_lines():
    """기존 박스를 갈지 않고 새 줄만 뒤에 붙인다 — DOM 이 살아남아야 강조/스크롤이 유지된다."""
    result = _run_dom_js("""
        const body = new Node('div');
        body.appendChild(makeBox('Dev0', 8, ['a', 'b']));
        const ok = rtmAppendRtmLogLines(body, {devices: [{device: 'Dev0', line_count: 7}]},
                                        {Dev0: [{ts: '01:00:02', text: 'c'},
                                                {ts: '01:00:03', text: 'd'}]});
        const box = body.querySelector('.rtm-log-box[data-rtm-xhl-device]');
        console.log(JSON.stringify({ok, lines: lineTexts(box),
                                    count: box.querySelector('.rtm-log-count').textContent}));
    """)
    assert result["ok"] is True
    assert result["lines"] == ["a", "b", "01:00:02c", "01:00:03d"]
    assert result["count"] == "7줄", "줄 수 배지는 서버 버퍼 길이를 따라야 한다"


def test_append_trims_to_box_capacity():
    """붙이기만 하면 DOM 이 무한히 자란다 — 박스가 보여줄 수 있는 만큼만 남긴다."""
    result = _run_dom_js("""
        const body = new Node('div');
        body.appendChild(makeBox('Dev0', 3, ['a', 'b', 'c']));
        rtmAppendRtmLogLines(body, {devices: []}, {Dev0: [{ts: '', text: 'd'}, {ts: '', text: 'e'}]});
        console.log(JSON.stringify({lines: lineTexts(body.querySelector('.rtm-log-box[data-rtm-xhl-device]'))}));
    """)
    assert result["lines"] == ["c", "d", "e"], "오래된 줄이 앞에서 빠지지 않았다"


def test_append_removes_idle_placeholder():
    """'입력 대기 중…' 자리표시자가 남으면 첫 줄이 한 칸 밀린다."""
    result = _run_dom_js("""
        const body = new Node('div');
        body.appendChild(makeBox('Dev0', 5, [null]));
        rtmAppendRtmLogLines(body, {devices: []}, {Dev0: [{ts: '', text: 'first'}]});
        console.log(JSON.stringify({lines: lineTexts(body.querySelector('.rtm-log-box[data-rtm-xhl-device]'))}));
    """)
    assert result["lines"] == ["first"]


def test_append_bails_when_no_box_exists():
    """첫 렌더 전에는 붙일 대상이 없다 — false 를 돌려줘 호출부가 전부 그리게 해야 한다."""
    result = _run_dom_js("""
        const body = new Node('div');
        console.log(JSON.stringify({ok: rtmAppendRtmLogLines(body, {devices: []}, {Dev0: [{ts: '', text: 'x'}]})}));
    """)
    assert result["ok"] is False


def test_append_skips_devices_without_a_box():
    """탭 보기에서는 박스가 하나뿐이다 — 안 보이는 장비 때문에 전체 재구성으로 떨어지면
    탭 보기에서 이 최적화가 통째로 사라진다."""
    result = _run_dom_js("""
        const body = new Node('div');
        body.appendChild(makeBox('Dev0', 5, ['a']));
        const ok = rtmAppendRtmLogLines(body, {devices: []},
                                        {Dev0: [{ts: '', text: 'b'}], Dev9: [{ts: '', text: 'z'}]});
        console.log(JSON.stringify({ok, lines: lineTexts(body.querySelector('.rtm-log-box[data-rtm-xhl-device]'))}));
    """)
    assert result["ok"] is True, "안 보이는 장비 때문에 전체 재구성으로 떨어졌다"
    assert result["lines"] == ["a", "b"]


def test_log_line_node_does_not_interpret_markup():
    """로그에 '<' 가 들어와도 글자로 보여야 한다 — textContent 경로를 지키는지."""
    result = _run_dom_js("""
        const node = rtmLogLineNode({ts: '01:00:00', text: '<script>x</script>'});
        console.log(JSON.stringify({tag: node.tag, text: node.textContent,
                                    children: node.children.map(c => c.tag)}));
    """)
    assert result["text"] == "01:00:00<script>x</script>"
    assert result["children"] == ["span", "#text"], "문자열 조립 경로로 돌아갔다 — 이스케이프 위험"
