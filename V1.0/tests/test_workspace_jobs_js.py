"""워크스페이스 작업 완료 알림(web_ui/js/workspace.js 의 newlyFinishedJobs) — IMPROVEMENT_PLAN 3-4.

보고서 생성은 대기열을 쓴다(Excel 을 만드는 중에 PDF 를 누르면 차례로 만든다). 예전 화면은 상태 전이
(running -> done)로 완료를 찾아서, 대기열의 다음 작업이 곧바로 running 이 되면 앞 작업의 완료를 놓쳤다.
이제 서버의 완료 이력(history·finished)으로 찾는다. node 가 없으면 건너뛴다(test_adaptive_poller 와 같은 방식).
"""
import json
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

WORKSPACE_JS = Path(__file__).resolve().parent.parent / "web_ui" / "js" / "workspace.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 동작 검증을 건너뜁니다")


def _function_source(name):
    """workspace.js 에서 최상위 함수 하나만 떼어낸다 — 파일 전체는 document 를 만지므로 node 에 못 넣는다.
    이 파일의 최상위 함수는 닫는 중괄호가 열 0 에 있다."""
    text = WORKSPACE_JS.read_text(encoding="utf-8")
    lines = text[text.index(f"function {name}("):].splitlines(keepends=True)
    collected = []
    for line in lines:
        collected.append(line)
        if line.rstrip("\r\n") == "}":
            return "".join(collected)
    raise AssertionError(f"{name} 의 끝을 찾지 못했다")


def run(body):
    script = _function_source("newlyFinishedJobs") + "\n" + textwrap.dedent(body)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        path = handle.name
    try:
        done = subprocess.run(["node", path], capture_output=True, timeout=30)
    finally:
        Path(path).unlink()
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return json.loads(done.stdout.decode("utf-8"))


def entry(seq, status="done", path=None):
    return {"seq": seq, "status": status, "error": None, "results": {"path": path} if path else None}


def test_first_poll_only_sets_the_baseline():
    jobs = {"report": {"status": "idle", "finished": 2, "history": [entry(1), entry(2)]}}
    assert run(f"console.log(JSON.stringify(newlyFinishedJobs({{}}, {json.dumps(jobs)})))") == []


def test_queued_reports_are_each_announced_even_if_running_never_stopped():
    before = {"report": {"status": "running", "finished": 0, "history": []},
              "export": {"status": "idle", "finished": 0, "history": []}}
    after = {"report": {"status": "running", "finished": 1, "history": [entry(1, path="a.xlsx")]},   # 다음 보고서가 곧바로 시작
             "export": {"status": "idle", "finished": 0, "history": []}}
    later = {"report": {"status": "done", "finished": 2,
                        "history": [entry(1, path="a.xlsx"), entry(2, "error")]},
             "export": {"status": "idle", "finished": 0, "history": []}}
    found = run(f"""
        const a = newlyFinishedJobs({json.dumps(before)}, {json.dumps(after)});
        const b = newlyFinishedJobs({json.dumps(after)}, {json.dumps(later)});
        console.log(JSON.stringify([a, b]));
    """)
    assert found == [[["report", entry(1, path="a.xlsx")]], [["report", entry(2, "error")]]]
