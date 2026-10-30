"""상태바 작업 센터 — IMPROVEMENT_PLAN 4-9.

  1. 여러 JobRunner(세션 점검·무인 점검·분석 세 가지·보고서·내보내기)의 도는 작업과 끝난 작업을 한 목록으로 —
     끝난 것은 종류를 넘어 최신순, 결과 사본 대신 한 줄 요약.
  2. 취소: 멈출 수 있는 작업(점검·분석)만, 진행 로그에 무엇을 기다리는지 남긴다. 보고서·내보내기·무인 점검은 거절.
  3. 분석이 취소를 본다 — 규칙 분석은 파일 사이에서, AI 분석은 파일·파트 사이와 재시도·쉼을 기다리는 동안. 분석한 파일은
     남고, 승인한 외부 전송은 '취소됨'으로 기록된다.
  4. 상태바 버튼이 무엇을 보이는지(도는 작업 > 방금 끝난 작업 > 확인 안 한 오류 > 쉼)와 창의 이스케이프.
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
from pathlib import Path

import pytest

from ai_analysis import raw_log_analyzer as rla
from ai_analysis import transmission
from api.job_center_api import JobCenterApiMixin
from api.job_runner import JobRunner
from test_ai_transmission import LOG, LOG_NAME, _records, api, root, sent  # noqa: F401 — api·root·sent 는 fixture

JS = Path(__file__).resolve().parent.parent / "web_ui" / "js"


def wait_until(predicate, timeout=10):
    deadline = time.time() + timeout
    while not predicate():
        assert time.time() < deadline, "기다리던 상태가 오지 않았다"
        time.sleep(0.01)


class Center(JobCenterApiMixin):
    """분석·워크스페이스 러너를 가진 Api 대역(무인 점검 러너는 없다 — 없는 믹스인은 건너뛴다)."""

    def __init__(self):
        self.analysis = JobRunner(("program", "local", "cloud"))
        self.workspace = JobRunner(("report", "export"))

    def _jobs(self):
        return self.analysis

    def _workspace_jobs(self):
        return self.workspace


# --------------------------------------------------------------------------- 1. 한 목록

def test_running_jobs_and_finished_history_from_every_runner():
    center = Center()
    center.workspace.start("report", lambda: {"path": os.path.join("reports", "ABC_정기점검보고서.xlsx")})
    wait_until(lambda: center.workspace.status("report")["finished"] == 1)

    def broken():
        raise RuntimeError("디스크가 가득 찼습니다")

    center.workspace.start("export", broken)
    wait_until(lambda: center.workspace.status("export")["finished"] == 1)
    release = threading.Event()
    center.analysis.start("program", lambda: release.wait(5) and [{"problem_count": 2}, {"problem_count": 0}],
                          total=4, log=["시작"])
    center.analysis.set("program", current=1, message="a.txt")

    view = center.get_job_center()
    [job] = view["jobs"]
    assert view["running"] == 1
    assert {k: job[k] for k in ("id", "group", "label", "current", "total", "message", "cancellable", "last_log")} == {
        "id": "analysis.program", "group": "분석", "label": "규칙 기반 분석", "current": 1, "total": 4,
        "message": "a.txt", "cancellable": True, "last_log": "시작"}
    assert [(h["id"], h["status"]) for h in view["history"]] == [("workspace.export", "error"),
                                                                ("workspace.report", "done")]
    export, report = view["history"]
    assert export["error"] == "디스크가 가득 찼습니다" and export["label"] == "내보내기"
    assert report["summary"] == "ABC_정기점검보고서.xlsx" and report["start_ts"] <= report["end_ts"]
    assert "results" not in report                                          # 결과 사본은 싣지 않는다
    release.set()
    wait_until(lambda: not center.analysis.is_running("program"))
    assert center.get_job_center()["history"][0]["summary"] == "파일 2개 · 이상 2건"


def test_history_is_newest_first_and_capped():
    center = Center()
    for i in range(25):
        kind = ("report", "export")[i % 2]
        center.workspace.start(kind, lambda i=i: {"path": f"f{i}.zip"}, queue=True)
    wait_until(lambda: sum(center.workspace.status(k)["finished"] for k in ("report", "export")) == 25)
    history = center.get_job_center()["history"]
    assert len(history) == 20                                               # 종류마다 10 → 합쳐 최신 20
    assert [h["end_ts"] for h in history] == sorted((h["end_ts"] for h in history), reverse=True)


# --------------------------------------------------------------------------- 2. 취소

def test_cancel_only_what_can_stop():
    center = Center()
    assert center.cancel_job("nope")["error"] == "알 수 없는 작업입니다."
    assert "멈출 수 없는" in center.cancel_job("workspace.report")["error"]
    assert "멈출 수 없는" in center.cancel_job("scheduled")["error"]
    assert "진행 중인 작업이 없습니다" in center.cancel_job("analysis.cloud")["error"]
    center.analysis.start("cloud", lambda: center.analysis.cancel_requested("cloud") or wait_until(
        lambda: center.analysis.cancel_requested("cloud")) or [])
    assert center.cancel_job("analysis.cloud") == {"ok": True}
    wait_until(lambda: center.analysis.status("cloud")["status"] == "cancelled")
    entry = center.get_job_center()["history"][0]
    assert entry["status"] == "cancelled" and entry["summary"] == "파일 0개 분석"
    assert "보내는 중인 요청이 끝나면" in center.analysis.status("cloud")["log"][-1]


def test_session_inspection_shows_up_and_stops_like_the_connection_tab():
    from api import terminal_inspection_api as tia
    runner = tia.inspection_job_runner()
    center = Center()
    runner.start("inspection", lambda: wait_until(lambda: runner.cancel_requested("inspection")) or {"saved": 3},
                 total=10)
    try:
        assert [j["id"] for j in center.get_job_center()["jobs"]] == ["inspection"]
        assert center.cancel_job("inspection") == {"ok": True}
        wait_until(lambda: not runner.is_running("inspection"))
        assert runner.status("inspection")["log"][-1] == tia.STOP_MESSAGE
        entry = next(h for h in center.get_job_center()["history"] if h["id"] == "inspection")
        assert entry["status"] == "cancelled" and entry["summary"] == "장비 3대 로그 저장"
    finally:
        wait_until(lambda: not runner.is_running("inspection"))


# --------------------------------------------------------------------------- 3. 분석이 취소를 본다

def test_rule_analysis_stops_between_files(tmp_path):
    from engine import log_analysis
    original, problem = tmp_path / "original", tmp_path / "problem"
    original.mkdir()
    for name in ("a", "b", "c"):
        (original / f"20261028_101500_raw_{name}.txt").write_text(LOG, encoding="utf-8")
    stop = []
    results = log_analysis.run_analysis(str(original), str(problem),
                                        progress_callback=lambda done, total, name: stop.append(done),
                                        should_stop=lambda: bool(stop))
    assert [r["source"] for r in results] == ["20261028_101500_raw_a.txt"]


def _ring(stop):
    return rla.KeyRing([{"endpoint": "http://localhost:13305", "model": "qwen"}], "local", should_stop=stop)


def test_send_prompts_does_not_send_the_remaining_parts(monkeypatch):
    stopped = []

    def dispatch(prompt, mode, cfg):
        stopped.append(True)                         # 첫 파트를 보낸 뒤 취소가 왔다
        return f"결과:{prompt}"

    monkeypatch.setattr(rla, "_dispatch", dispatch)
    result = rla.send_prompts(rla.PreparedRequests(("p1", "p2", "p3"), True), "local", _ring(lambda: bool(stopped)))
    assert result.stopped is True and result.sent == 1 and len(stopped) == 1
    assert result.texts == ["결과:p1", "[AI 분석 중단 - 파트 2~3] 취소되어 보내지 않았습니다."]


def test_the_retry_wait_is_cut_short(monkeypatch):
    calls = []

    def busy(prompt, mode, cfg):
        calls.append(prompt)
        raise urllib.error.HTTPError("http://x", 429, "Too Many Requests", None, None)

    monkeypatch.setattr(rla, "_dispatch", busy)
    started = time.monotonic()
    result = rla.send_prompts(rla.PreparedRequests(("p1", "p2"), True), "local", _ring(lambda: bool(calls)))
    assert time.monotonic() - started < 3                                  # 22초를 기다리지 않았다
    assert result.stopped is True and len(calls) == 1
    assert result.texts[0].startswith("[AI 분석 중단 - 파트 1]") and result.texts[1].startswith("[AI 분석 중단 - 파트 2~2]")


def _center_for(api):
    """test_ai_transmission 의 분석 Api 대역에 작업 센터를 얹는다(같은 회차 폴더·설정)."""
    return type("CenterApi", (JobCenterApiMixin, type(api)), {})()


def test_cancelling_ai_analysis_keeps_finished_files_and_records_it(api, sent, monkeypatch):
    center = _center_for(api)
    center.local_cfg["endpoint"] = "http://192.0.2.50:13305"            # 다른 PC — 승인·기록을 거친다
    second = LOG_NAME.replace("Core1", "Agg1")
    (center.run / "original" / second).write_text(LOG.replace("Core1", "Agg1"), encoding="utf-8")

    def slow(prompt, cfg):
        sent.append(prompt)
        wait_until(lambda: center._jobs().cancel_requested("local"))       # 첫 파일을 보내는 중에 취소한다
        return "부분 결과"

    monkeypatch.setattr(rla, "_call_local_npu_raw_text", slow)
    preview = center.preview_ai_transmission("local")
    assert center.start_ai_log_analysis("local", preview["preview_id"]) == {"ok": True}
    wait_until(lambda: center.get_job_center()["running"] == 1 and sent)
    assert center.cancel_job("analysis.local") == {"ok": True}
    wait_until(lambda: not center._jobs().is_running("local"))

    status = center._jobs().status("local")
    assert status["status"] == "cancelled" and len(status["results"]) == 1  # 보내던 요청은 끝까지 받았다
    problem = center.run / "problem"
    assert (problem / "LocalAI_20261028_101500_Agg1_problems.txt").read_text(encoding="utf-8") == "부분 결과"
    assert not (problem / "LocalAI_20261028_101500_Core1_problems.txt").exists()
    approved, finished = _records()
    assert approved["event"] == "approved" and finished["event"] == "cancelled"
    assert finished["requests_sent"] == 1 and len(sent) == 1
    assert center.get_job_center()["history"][0]["summary"] == "파일 1개 분석"


def test_a_cancelled_transmission_record():
    assert transmission.finished_record("p", sent=1, failed=0, keys_used=[], cancelled=True)["event"] == "cancelled"
    failed = transmission.finished_record("p", sent=0, failed=1, keys_used=[], error=RuntimeError("x"), cancelled=True)
    assert failed["event"] == "failed"                                     # 오류가 먼저다


# --------------------------------------------------------------------------- 4. 상태바

def _extract(name, start):
    lines = (JS / name).read_text(encoding="utf-8").splitlines()
    begin = next(i for i, line in enumerate(lines) if line.startswith(start))
    end = begin
    while not lines[end].startswith(("}", "};")):
        end += 1
    return "\n".join(lines[begin:end + 1])


def _run_js(body, payload):
    source = "\n".join([
        _extract("core.js", "const _ESC_MAP"), _extract("core.js", "function esc("),
        _extract("analysis-progress.js", "function formatDuration("),
        _extract("job-center.js", "const JC_STATUS"), "let jcConfirming = null;",
        *(_extract("job-center.js", f"function {name}(") for name in
          ("jcPercent", "jcProgressText", "jcClock", "jcIndicator", "jcJobRow", "jcHistoryRow")),
    ])
    script = f"const DATA = {json.dumps(payload, ensure_ascii=False)};\n{source}\n{body}"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        path = handle.name
    try:
        done = subprocess.run(["node", path], capture_output=True, encoding="utf-8", timeout=30)
    finally:
        os.unlink(path)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 동작 검증을 건너뜁니다")
def test_the_status_bar_button_shows_what_matters_most():
    running = [{"id": "analysis.cloud", "label": "클라우드 AI 분석", "current": 3, "total": 12,
                "elapsed_sec": 80, "eta_sec": 241, "queued": 0},
               {"id": "workspace.report", "label": "보고서 생성", "current": 0, "total": 1, "elapsed_sec": 2,
                "eta_sec": None, "queued": 2}]
    history = [{"id": "workspace.export", "label": "내보내기", "status": "error", "end_ts": 2000},
               {"id": "inspection", "label": "세션 점검", "status": "done", "end_ts": 1000}]
    result = _run_js("""
        const now = 3000 * 1000;
        console.log(JSON.stringify({
          busy: jcIndicator({jobs: DATA.running, history: DATA.history}, null, 0, now),
          progress: DATA.running.map(jcProgressText),
          flash: jcIndicator({jobs: [], history: DATA.history},
                             {entry: DATA.history[1], until: now + 1}, 0, now),
          expired: jcIndicator({jobs: [], history: DATA.history},
                               {entry: DATA.history[1], until: now - 1}, 0, now),
          seen: jcIndicator({jobs: [], history: DATA.history}, null, 2500, now),
        }));
    """, {"running": running, "history": history})
    assert result["busy"] == {"tone": "busy", "icon": "sync", "pct": 25, "text": "클라우드 AI 분석 25% 외 1"}
    assert result["progress"] == ["3/12 · 경과 1:20 · 남은 4:01", "0/1 · 경과 0:02 · 대기 2"]
    assert result["flash"]["tone"] == "ok" and result["flash"]["text"] == "세션 점검 완료"
    assert result["expired"] == {"tone": "fail", "icon": "error", "pct": None, "text": "작업 오류 1건"}
    assert result["seen"]["tone"] == "idle"                                # 창을 연 뒤의 오류만 남긴다


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 동작 검증을 건너뜁니다")
def test_job_center_rows_escape_what_comes_from_files_and_errors():
    evil = '<img src=x onerror=alert(1)>'
    result = _run_js("""
        console.log(JSON.stringify({
          job: jcJobRow({id: 'analysis.cloud', group: '분석', label: '클라우드 AI 분석', current: 0, total: 0,
                         elapsed_sec: 1, eta_sec: null, queued: 0, cancellable: true, cancel_requested: false,
                         message: DATA.evil, last_log: ''}),
          hist: jcHistoryRow({id: 'workspace.export', label: '내보내기', status: 'error', error: DATA.evil,
                              start_ts: 1, end_ts: 2}),
        }));
    """, {"evil": evil})
    for html in result.values():
        assert evil not in html and "&lt;img" in html
    assert 'data-jc-cancel="analysis.cloud"' in result["job"] and "jc-bar-busy" in result["job"]
