"""
JobRunner — 백그라운드 스레드에서 실행되는 장시간 작업(점검/파싱/분석/리포트 생성/내보내기)의
상태(idle/running/done/error, 진행률, 경과·예상 시간)를 추적하는 공용 유틸리티.

기존 api/log_analysis_run_api.py가 각자 만들어 쓰던 _jobs()/_set_job()/get_..._status()/
_run_job_thread() 패턴을 재사용 가능한 클래스로 뽑아냈다 — 그 파일도 이제 이 클래스를
감싸서 쓴다(중복 제거). web_ui/js/analysis-progress.js가 기대하는 JSON 모양
({status, current, total, message, elapsed_sec, eta_sec, error})은 그대로 유지된다.
"""
import threading
import time


class JobRunner:
    """kinds(예: ("program","local","cloud") 또는 ("inspection","parsing","analysis",
    "report","export"))별로 하나씩 슬롯을 두고, 슬롯당 동시에 하나의 백그라운드 작업만 허용한다."""

    def __init__(self, kinds):
        self._kinds = tuple(kinds)
        self._jobs = {kind: self._idle_job() for kind in self._kinds}
        self._lock = threading.Lock()

    @staticmethod
    def _idle_job() -> dict:
        return {"status": "idle", "current": 0, "total": 0, "message": "",
                "start_ts": None, "end_ts": None, "error": None, "results": None}

    def is_running(self, kind: str) -> bool:
        with self._lock:
            return self._jobs[kind]["status"] == "running"

    def set(self, kind: str, **fields) -> None:
        with self._lock:
            self._jobs[kind].update(fields)

    def status(self, kind: str) -> dict:
        with self._lock:
            return self._to_public(self._jobs[kind])

    def status_all(self) -> dict:
        """상단 진행바 폴링용 — 등록된 모든 kind의 현재 상태를 한 번에 반환."""
        with self._lock:
            return {kind: self._to_public(job) for kind, job in self._jobs.items()}

    @staticmethod
    def _to_public(job: dict) -> dict:
        now = time.time()
        elapsed = eta = None
        if job["start_ts"] is not None:
            end = job["end_ts"] if job["end_ts"] is not None else now
            elapsed = end - job["start_ts"]
            if job["status"] == "running" and job["current"] > 0 and job["total"] > 0:
                per_item = elapsed / job["current"]
                eta = max(0.0, per_item * (job["total"] - job["current"]))
        return {"status": job["status"], "current": job["current"], "total": job["total"],
                "message": job["message"], "elapsed_sec": elapsed, "eta_sec": eta,
                "error": job["error"], "results": job.get("results")}

    def start(self, kind: str, worker, *, daemon: bool = True) -> bool:
        """worker()를 백그라운드 스레드에서 실행하고 즉시 반환한다.
        이미 실행 중이면 아무것도 하지 않고 False를 반환(중복 시작 방지) — 호출부가
        "이미 진행 중입니다" 같은 안내를 붙이려면 start() 전에 is_running()으로 먼저 확인할 것."""
        if self.is_running(kind):
            return False
        self.set(kind, status="running", current=0, total=0, message="준비 중...",
                  start_ts=time.time(), end_ts=None, error=None, results=None)

        def runner():
            try:
                results = worker()
                self.set(kind, status="done", end_ts=time.time(), message="완료", results=results)
            except Exception as e:
                self.set(kind, status="error", end_ts=time.time(), error=str(e), message="오류")

        threading.Thread(target=runner, daemon=daemon).start()
        return True
