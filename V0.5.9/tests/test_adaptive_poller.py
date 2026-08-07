"""web_ui/js/core.js 의 createAdaptivePoller 동작 검증 — OPTIMIZATION_PLAN 1-3.

왜 파이썬 테스트에서 JS 를 돌리는가: 이 프로젝트에는 JS 테스트 러너가 없고, 그것 하나를
들여오는 것은 이 최적화 항목에 비해 과한 변경이다. 그렇다고 검증을 생략할 수는 없다 —
폴러는 진행 표시의 심장이고, 이걸 잘못 만들면 "작업이 끝났는데 화면이 안 바뀐다"가 된다
(실제로 이 항목을 만드는 중에 존재하지 않는 함수를 부르는 상태를 한 번 만들었다).

node 가 없는 환경에서는 skip 한다. Windows 개발 PC 에 node 가 없을 수 있으므로 이 테스트가
없어도 나머지 테스트는 전부 돌아야 한다.
"""
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

CORE_JS = Path(__file__).resolve().parent.parent / "web_ui" / "js" / "core.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 동작 검증을 건너뜁니다")


def _poller_source():
    """core.js 에서 createAdaptivePoller 함수만 떼어낸다.

    core.js 는 최상단에서 document 를 만지므로 통째로 node 에 넣을 수 없다.

    중괄호 깊이를 세는 방식으로 처음 만들었다가 틀렸다 — 파라미터가 구조분해
    (`function createAdaptivePoller({ tick, isBusy, ... })`)라서 그 중괄호가 함수 본문의
    시작으로 잡히고, 파라미터 목록이 닫히는 지점에서 깊이가 0이 되어 **서명만** 잘려 나왔다.
    이 파일의 최상위 함수는 닫는 중괄호가 열 0 에 있으므로 그것을 끝으로 삼는다.
    """
    text = CORE_JS.read_text(encoding="utf-8")
    start = text.index("function createAdaptivePoller")
    lines = text[start:].splitlines(keepends=True)
    collected = []
    for line in lines:
        collected.append(line)
        if line.rstrip("\r\n") == "}":
            return "".join(collected)
    raise AssertionError("createAdaptivePoller 의 끝(열 0 의 닫는 중괄호)을 찾지 못했다")


def _run_js(body, tmp_path_factory=None):
    """폴러 소스 + 테스트 본문을 임시 .js 파일로 써서 실행한다.

    `node -e` 를 쓰지 않는 이유: node 22 부터 -e 입력을 TypeScript 로 먼저 해석해서,
    `function f({ a, b = 1 })` 같은 구조분해 기본값 파라미터를 타입 표기로 잘못 읽고
    SyntaxError 를 낸다. 실제 .js 파일은 그 경로를 타지 않는다.
    """
    script = _poller_source() + "\n" + textwrap.dedent(body)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        path = handle.name
    try:
        completed = subprocess.run(["node", path], capture_output=True, text=True)
    finally:
        os.unlink(path)
    assert completed.returncode == 0, f"node 실행 실패:\n{completed.stderr}"
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_poller_source_is_extractable():
    """추출이 서명만 잘라오지 않는지 — 실제로 그 버그가 있었으므로 본문 표식을 확인한다."""
    source = _poller_source()
    assert "createAdaptivePoller" in source
    assert source.count("{") == source.count("}")
    for marker in ("function runOnce", "function schedule", "start()", "stop()", "wake()"):
        assert marker in source, f"추출된 소스에 {marker} 가 없다 — 함수가 잘렸다"


def test_idle_uses_long_interval():
    """바쁘지 않으면 idleMs 주기로 돈다 — 이 항목의 목적 자체."""
    result = _run_js("""
        const stamps = [];
        const poller = createAdaptivePoller({
          tick: async () => { stamps.push(Date.now()); return {busy: false}; },
          isBusy: (s) => s.busy,
          activeMs: 20, idleMs: 200,
        }).start();
        setTimeout(() => {
          poller.stop();
          const gaps = stamps.slice(1).map((t, i) => t - stamps[i]);
          console.log(JSON.stringify({count: stamps.length, gaps}));
        }, 500);
    """)
    # 500ms 동안 200ms 주기면 3회 안팎. activeMs(20ms)로 돌았다면 20회를 넘는다.
    assert result["count"] <= 5, f"유휴 상태에서 너무 자주 돌았다: {result}"
    assert all(gap >= 150 for gap in result["gaps"]), f"간격이 idleMs 에 못 미친다: {result}"


def test_busy_uses_short_interval():
    """작업이 돌고 있으면 activeMs 주기로 촘촘하게 돈다."""
    result = _run_js("""
        const stamps = [];
        const poller = createAdaptivePoller({
          tick: async () => { stamps.push(Date.now()); return {busy: true}; },
          isBusy: (s) => s.busy,
          activeMs: 20, idleMs: 500,
        }).start();
        setTimeout(() => {
          poller.stop();
          console.log(JSON.stringify({count: stamps.length}));
        }, 300);
    """)
    assert result["count"] >= 5, f"바쁜 상태에서 너무 드물게 돌았다: {result}"


def test_switches_from_idle_to_active():
    """유휴로 돌던 폴러가 작업이 시작되면 빠른 주기로 전환된다."""
    result = _run_js("""
        let busy = false;
        const stamps = [];
        const poller = createAdaptivePoller({
          tick: async () => { stamps.push({t: Date.now(), busy}); return {busy}; },
          isBusy: (s) => s.busy,
          activeMs: 20, idleMs: 150,
        }).start();
        setTimeout(() => { busy = true; poller.wake(); }, 200);
        setTimeout(() => {
          poller.stop();
          const busyTicks = stamps.filter(s => s.busy).length;
          console.log(JSON.stringify({total: stamps.length, busyTicks}));
        }, 500);
    """)
    # wake 이후 약 300ms 동안 20ms 주기면 10회 이상 돌아야 한다.
    assert result["busyTicks"] >= 5, f"바쁜 상태로 전환된 뒤 촘촘해지지 않았다: {result}"


def test_ticks_do_not_overlap():
    """tick 이 주기보다 오래 걸려도 호출이 겹쳐 쌓이지 않는다.

    setInterval 을 쓰던 원래 코드의 실제 결함이다 — 브리지 호출이 1초를 넘으면(대량 로그
    스캔 중에는 그렇다) 호출이 중첩됐다.
    """
    result = _run_js("""
        let inFlight = 0, maxInFlight = 0, ticks = 0;
        const poller = createAdaptivePoller({
          tick: async () => {
            inFlight += 1; ticks += 1;
            maxInFlight = Math.max(maxInFlight, inFlight);
            await new Promise(r => setTimeout(r, 60));   // 주기(10ms)보다 오래 걸린다
            inFlight -= 1;
            return {busy: true};
          },
          isBusy: () => true,
          activeMs: 10, idleMs: 10,
        }).start();
        setTimeout(() => {
          poller.stop();
          console.log(JSON.stringify({maxInFlight, ticks}));
        }, 400);
    """)
    assert result["maxInFlight"] == 1, f"tick 이 겹쳐 실행됐다: {result}"


def test_survives_tick_error():
    """tick 이 예외를 던져도 폴러가 죽지 않는다.

    죽으면 진행 표시가 영구히 멈추고, 사용자는 작업이 멈춘 것으로 오해한다.
    """
    result = _run_js("""
        let calls = 0;
        const poller = createAdaptivePoller({
          tick: async () => { calls += 1; if (calls <= 2) throw new Error('bridge down'); return {busy: false}; },
          isBusy: (s) => s.busy,
          activeMs: 20, idleMs: 20,
        }).start();
        setTimeout(() => {
          poller.stop();
          console.log(JSON.stringify({calls}));
        }, 300);
    """)
    assert result["calls"] > 2, f"예외 이후 폴링이 멈췄다: {result}"


def test_stop_halts_polling():
    result = _run_js("""
        let calls = 0;
        const poller = createAdaptivePoller({
          tick: async () => { calls += 1; return {busy: true}; },
          isBusy: () => true,
          activeMs: 10, idleMs: 10,
        }).start();
        setTimeout(() => { poller.stop(); }, 100);
        setTimeout(() => {
          const atStop = calls;
          setTimeout(() => console.log(JSON.stringify({grewAfterStop: calls > atStop})), 150);
        }, 120);
    """)
    assert result["grewAfterStop"] is False, "stop() 이후에도 폴링이 계속됐다"


def test_null_state_is_treated_as_idle():
    """tick 이 null 을 반환하면(브리지가 null 을 준 경우) 유휴로 본다 — 예외가 아니라 백오프."""
    result = _run_js("""
        const stamps = [];
        const poller = createAdaptivePoller({
          tick: async () => { stamps.push(Date.now()); return null; },
          isBusy: () => { throw new Error('isBusy 는 null 에 대해 호출되면 안 된다'); },
          activeMs: 10, idleMs: 200,
        }).start();
        setTimeout(() => {
          poller.stop();
          console.log(JSON.stringify({count: stamps.length}));
        }, 450);
    """)
    assert result["count"] <= 4, f"null 반환을 바쁜 것으로 취급했다: {result}"
