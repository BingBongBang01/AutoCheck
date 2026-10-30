"""화면 수명주기 — IMPROVEMENT_PLAN 3-7.

화면은 그릴 때 '떠날 때 정리할 것'(인터벌, document/window 전역 리스너)을 onPageLeave() 로 맡기고,
renderPage 가 다음 화면을 그리기 직전에 leaveCurrentPage() 로 한 번에 정리한다. 예전엔:
  - 전체 로그 탭의 인터벌이 탭을 떠나도 계속 돌았다(매 주기 currentPage 만 보고 그냥 돌아섰다).
  - 장비 목록은 열 때마다 document mouseup 리스너를, 구성도는 window mousemove/mouseup 한 쌍을 새로 붙여
    탭을 오갈수록 쌓였다.
  - 분석·워크스페이스 진행 폴러가 브리지 준비 전에 시작해 첫 호출이 '브리지 없음'으로 버려지고 5초를 쉬었다.
"""
import json
import re
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parent.parent / "web_ui" / "js"
APP_LEVEL = {"core.js"}     # 앱 전체에 한 번만 거는 리스너(오류 보고·브리지 준비·전역 클릭)


def _function_source(text, name):
    lines = text[text.index(f"function {name}("):].splitlines(keepends=True)
    collected = []
    for line in lines:
        collected.append(line)
        if line.rstrip("\r\n") == "}":
            return "".join(collected)
    raise AssertionError(f"{name} 의 끝을 찾지 못했다")


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 동작 검증을 건너뜁니다")
def test_leaving_a_page_runs_each_cleanup_once_even_if_one_fails():
    nav = (JS / "core-navigation.js").read_text(encoding="utf-8")
    script = "let pageLeaveCallbacks = [];\n" + _function_source(nav, "onPageLeave") + \
        _function_source(nav, "leaveCurrentPage") + textwrap.dedent("""
        const calls = [];
        console.error = () => {};
        onPageLeave(() => calls.push('timer'));
        onPageLeave(() => { throw new Error('정리 실패'); });
        onPageLeave(() => calls.push('listener'));
        leaveCurrentPage();
        leaveCurrentPage();                       // 두 번째 이동에서는 이미 정리했다
        onPageLeave(() => calls.push('next page'));
        leaveCurrentPage();
        console.log(JSON.stringify(calls));
    """)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        path = handle.name
    try:
        done = subprocess.run(["node", path], capture_output=True, timeout=30)
    finally:
        Path(path).unlink()
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert json.loads(done.stdout.decode("utf-8")) == ["timer", "listener", "next page"]


def test_render_page_cleans_up_before_drawing_the_next_page():
    render = _function_source((JS / "core-navigation.js").read_text(encoding="utf-8"), "renderPage")
    assert render.index("leaveCurrentPage();") < render.index("currentPage = page;")


_ADD = re.compile(r"\b(document|window)\.addEventListener\(\s*'([a-z]+)'\s*,\s*([^,)]+)")
_REMOVE = re.compile(r"\b(document|window)\.removeEventListener\(\s*'([a-z]+)'\s*,\s*([A-Za-z_$][\w$]*)")


@pytest.mark.parametrize("path", sorted(p for p in JS.glob("*.js") if p.name not in APP_LEVEL), ids=lambda p: p.name)
def test_page_level_global_listeners_are_removed(path):
    """화면 코드가 document/window 에 거는 리스너는 이름 붙은 함수여야 하고, 같은 파일에 떼는 짝이 있어야 한다
    — 익명 함수는 뗄 수 없어서 화면을 열 때마다 쌓인다."""
    text = path.read_text(encoding="utf-8")
    removed = {(target, event, handler) for target, event, handler in _REMOVE.findall(text)}
    problems = []
    for target, event, handler in _ADD.findall(text):
        handler = handler.strip()
        if not re.fullmatch(r"[A-Za-z_$][\w$]*", handler):
            problems.append(f"{target} '{event}' 에 익명 함수 — 뗄 수 없다")
        elif (target, event, handler) not in removed:
            problems.append(f"{target} '{event}' {handler} 를 떼는 곳이 없다")
    assert problems == []


@pytest.mark.parametrize("name, poller", [("analysis-progress.js", "analysisPoller"), ("workspace.js", "workspacePoller"),
                                          ("workspace-schedule.js", "scheduleAlertPoller"),
                                          ("job-center.js", "jobCenterPoller")])
def test_load_time_pollers_wait_for_the_bridge(name, poller):
    text = (JS / name).read_text(encoding="utf-8")
    assert f"whenApiReady(() => {poller}.start());" in text
    assert "}).start();" not in text


def test_inventory_select_all_checkbox_is_bound_once():
    """장비 목록 머리줄의 전체 선택 체크박스는 목록을 다시 그려도 그대로 있다. renderInventoryTable 이 그릴 때마다
    addEventListener 로 붙여서 리스너가 쌓였고, 장비를 읽기 전(행 0개)에 붙은 리스너가 먼저 돌며 체크를 풀어 버려
    전체 선택이 동작하지 않았다 — 처리기는 onchange 하나로 두고 누르는 그때의 행을 읽는다."""
    body = _function_source((JS / "inventory-table.js").read_text(encoding="utf-8"), "renderInventoryTable")
    assert "selectAllCb.addEventListener" not in body
    assert "selectAllCb.onchange = () =>" in body and "getInventoryRows()" in body
