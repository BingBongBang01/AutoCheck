"""앱 공용 작업 관리자(JobRunner) — IMPROVEMENT_PLAN 3-4.

작업 관리가 갈래마다 달랐다: 분석·워크스페이스는 JobRunner, 세션 점검은 모듈 전역 dict+잠금(작업 함수가
예외로 끝나면 '진행 중'이 영영 남아 다시 점검할 수 없었다), 채점은 job_manager(퇴역). 이제 하나로 모으고
취소(협조형)·대기열·진행 로그·완료 이력을 JobRunner 가 맡는다.
"""
import threading
import time

import pytest

from api.job_runner import JobRunner


def wait_until(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    raise AssertionError("시간 안에 조건이 맞지 않았다")


def finished(runner, kind, count):
    wait_until(lambda: runner.status(kind)["finished"] >= count and not runner.is_running(kind))
    return runner.status_all(with_history=True)[kind]


def test_a_job_runs_in_the_background_and_keeps_its_results():
    runner = JobRunner(("report",))
    release = threading.Event()
    assert runner.start("report", lambda: release.wait(2) and {"path": "a.xlsx"}, total=3) is True
    status = runner.status("report")
    assert status["status"] == "running" and status["total"] == 3 and status["message"] == "준비 중..."
    release.set()
    job = finished(runner, "report", 1)
    assert job["status"] == "done" and job["results"] == {"path": "a.xlsx"} and job["elapsed_sec"] >= 0
    entry = job["history"][0]
    assert job["history"] == [{"seq": 1, "status": "done", "error": None, "results": {"path": "a.xlsx"},
                               "start_ts": entry["start_ts"], "end_ts": entry["end_ts"]}]
    assert entry["start_ts"] <= entry["end_ts"]                            # 작업 센터가 걸린 시간을 보인다
    assert runner.history("report") == job["history"]


def test_a_second_start_is_refused_unless_queued():
    runner = JobRunner(("report",))
    release = threading.Event()
    order = []

    def job(name):
        def worker():
            release.wait(2)
            order.append(name)
            return {"path": name}
        return worker

    assert runner.start("report", job("excel")) is True
    assert runner.start("report", job("again")) is False                  # 기본: 거절
    assert runner.start("report", job("pdf"), queue=True) == "queued"
    assert runner.status("report")["queued"] == 1
    release.set()
    job_status = finished(runner, "report", 2)
    assert order == ["excel", "pdf"]
    assert [entry["results"]["path"] for entry in job_status["history"]] == ["excel", "pdf"]
    assert job_status["queued"] == 0


def test_a_failing_job_ends_in_error_and_the_slot_is_free_again():
    runner = JobRunner(("inspection",))

    def broken():
        raise OSError("디스크가 가득 찼다")

    runner.start("inspection", broken)
    job = finished(runner, "inspection", 1)
    assert job["status"] == "error" and "디스크" in job["error"]
    assert runner.start("inspection", lambda: "ok") is True                # 영영 '진행 중'으로 남지 않는다
    assert finished(runner, "inspection", 2)["status"] == "done"


def test_cancel_is_cooperative_and_clears_the_queue():
    runner = JobRunner(("inspection",))
    ran = []

    def worker():
        wait_until(lambda: runner.cancel_requested("inspection"))
        return {"saved": 1}                                                 # 그때까지의 결과는 작업 함수가 남긴다

    assert runner.request_cancel("inspection") is False                     # 돌고 있는 작업이 없다
    runner.start("inspection", worker)
    runner.start("inspection", lambda: ran.append("queued"), queue=True)
    assert runner.request_cancel("inspection", "중지 요청됨") is True
    job = finished(runner, "inspection", 1)
    assert job["status"] == "cancelled" and job["results"] == {"saved": 1}
    assert job["log"][-1] == "중지 요청됨" and job["queued"] == 0
    time.sleep(0.05)
    assert ran == [] and runner.status("inspection")["finished"] == 1        # 대기 작업은 버려졌다


def test_progress_from_many_threads_and_bounded_log():
    runner = JobRunner(("inspection",))
    release = threading.Event()
    runner.start("inspection", lambda: release.wait(2), total=400, log=["점검 시작"])

    def session():
        for _ in range(100):
            runner.advance("inspection")
            runner.log("inspection", "x")

    threads = [threading.Thread(target=session) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    status = runner.status("inspection")
    assert status["current"] == 400 and status["log"][0] == "점검 시작" and len(status["log"]) == 401
    for _ in range(200):
        runner.log("inspection", "y")
    assert len(runner.status("inspection")["log"]) == 500                  # 최근 것만 둔다
    release.set()
    finished(runner, "inspection", 1)


@pytest.mark.parametrize("with_history", [False, True])
def test_history_is_sent_only_when_asked(with_history):
    runner = JobRunner(("program", "local"))
    runner.start("program", lambda: [{"source": "a.txt", "problem_count": 1}])
    finished(runner, "program", 1)
    status = runner.status_all(with_history=with_history)
    assert ("history" in status["program"]) is with_history                 # 분석 진행바(1초 폴링)는 안 받는다
    assert status["local"]["status"] == "idle" and status["local"]["finished"] == 0
