"""
JobRunner — 백그라운드 스레드에서 도는 장시간 작업(세션 점검·로그 분석·보고서 생성·내보내기)의 상태를
추적하는 앱 공용 작업 관리자. 작업 관리는 이것 하나다(IMPROVEMENT_PLAN 3-4) — 예전엔 세션 점검이 모듈
전역 dict+잠금으로 따로 관리해서, 작업 함수가 예외로 끝나면 '진행 중'이 영영 남아 다시 점검할 수 없었다.
(채점 파이프라인이 쓰던 core/job_manager·history_manager 는 D1 퇴역과 함께 없어졌다.)

종류(kind)마다 슬롯이 하나이고 슬롯당 한 번에 하나만 돈다. 그동안 들어온 요청은
  - start(..., queue=False)  거절한다(False) — 호출부가 '이미 진행 중'을 안내한다.
  - start(..., queue=True)   대기열에 넣는다("queued") — 앞 작업이 끝나면 같은 스레드가 이어서 돌린다.
취소는 협조형이다: request_cancel() 은 표시만 하고, 작업 함수가 cancel_requested() 를 보고 멈춘다
(그때까지의 결과를 남길지는 작업 함수가 정한다). 취소된 작업은 'cancelled' 로 끝나고 대기열도 비운다.
끝난 작업은 종류별 최근 이력에 남는다 — 대기열 때문에 'running' 이 이어져도 화면이 완료를 놓치지 않게
finished(누적 완료 수)와 history 를 준다(status_all(with_history=True)).

공개 상태 모양: {status(idle/running/done/cancelled/error), current, total, message, elapsed_sec,
eta_sec, error, results, cancel_requested, queued, log, finished[, history]}
— web_ui/js/analysis-progress.js·workspace.js·connection-inspection.js 가 읽는다.
"""
import collections
import threading
import time

_LOG_LIMIT = 500      # 진행 로그는 화면 표시용 — 최근 것만 둔다
_HISTORY_LIMIT = 10   # 종류별 완료 이력


class JobRunner:
    """kinds(예: ("program", "local", "cloud") 또는 ("report", "export"))별로 슬롯을 하나씩 둔다."""

    def __init__(self, kinds):
        self._kinds = tuple(kinds)
        self._lock = threading.Lock()
        self._jobs = {kind: self._idle_job() for kind in self._kinds}
        self._pending = {kind: collections.deque() for kind in self._kinds}
        self._history = {kind: collections.deque(maxlen=_HISTORY_LIMIT) for kind in self._kinds}
        self._finished = {kind: 0 for kind in self._kinds}

    @staticmethod
    def _idle_job() -> dict:
        return {"status": "idle", "current": 0, "total": 0, "message": "",
                "start_ts": None, "end_ts": None, "error": None, "results": None,
                "cancel_requested": False, "log": []}

    # ---- 작업 함수가 부르는 것 -----------------------------------------------------

    def set(self, kind: str, **fields) -> None:
        with self._lock:
            self._jobs[kind].update(fields)

    def advance(self, kind: str, step: int = 1) -> None:
        """진행 수를 step 만큼 올린다 — 여러 스레드가 함께 세는 작업(세션 점검)용."""
        with self._lock:
            self._jobs[kind]["current"] += step

    def log(self, kind: str, line: str) -> None:
        with self._lock:
            lines = self._jobs[kind]["log"]
            lines.append(line)
            del lines[:-_LOG_LIMIT]

    def cancel_requested(self, kind: str) -> bool:
        with self._lock:
            return self._jobs[kind]["cancel_requested"]

    # ---- 호출부(API)가 부르는 것 ---------------------------------------------------

    def is_running(self, kind: str) -> bool:
        with self._lock:
            return self._jobs[kind]["status"] == "running"

    def start(self, kind: str, worker, *, queue: bool = False, total: int = 0, log=None):
        """worker()를 백그라운드 스레드에서 실행하고 즉시 반환한다. total·log 는 시작 시 진행 총량·로그.
        반환: True(시작) | "queued"(앞 작업 뒤에 대기, queue=True 일 때) | False(이미 진행 중)."""
        with self._lock:
            if self._jobs[kind]["status"] == "running":
                if not queue:
                    return False
                self._pending[kind].append((worker, total, log))
                return "queued"
            self._begin(kind, total, log)
        threading.Thread(target=self._run, args=(kind, worker), daemon=True).start()
        return True

    def request_cancel(self, kind: str, message: str = None) -> bool:
        """돌고 있는 작업에 취소를 요청하고 대기열을 비운다. 돌고 있는 작업이 없으면 False."""
        with self._lock:
            job = self._jobs[kind]
            if job["status"] != "running":
                return False
            job["cancel_requested"] = True
            self._pending[kind].clear()
            if message:
                job["log"].append(message)
            return True

    def status(self, kind: str) -> dict:
        with self._lock:
            return self._to_public(kind, with_history=False)

    def history(self, kind: str) -> list:
        """끝난 작업의 최근 이력(오래된 것부터) — 상태바 작업 센터(api/job_center_api.py)가 종류를 넘어 모은다."""
        with self._lock:
            return [dict(entry) for entry in self._history[kind]]

    def status_all(self, *, with_history: bool = False) -> dict:
        """진행바 폴링용 — 등록된 모든 kind 의 현재 상태를 한 번에. 완료 이력은 필요한 화면만 받는다
        (분석 진행바는 1초마다 폴링하므로 결과 사본을 매번 싣지 않는다)."""
        with self._lock:
            return {kind: self._to_public(kind, with_history) for kind in self._kinds}

    # ---- 내부 ------------------------------------------------------------------------

    def _begin(self, kind, total, log):
        """(잠금 안에서) 슬롯을 새 작업으로 채운다."""
        self._jobs[kind] = {**self._idle_job(), "status": "running", "message": "준비 중...",
                            "start_ts": time.time(), "total": total, "log": list(log or [])}

    def _run(self, kind, worker):
        while worker is not None:
            try:
                results, error = worker(), None
            except Exception as exc:
                results, error = None, str(exc)
            with self._lock:
                job = self._jobs[kind]
                if error is not None:
                    job.update(status="error", error=error, message="오류")
                elif job["cancel_requested"]:
                    job.update(status="cancelled", message="취소됨")
                else:
                    job.update(status="done", message="완료")
                job.update(end_ts=time.time(), results=results)
                self._finished[kind] += 1
                self._history[kind].append({"seq": self._finished[kind], "status": job["status"],
                                            "error": job["error"], "results": results,
                                            "start_ts": job["start_ts"], "end_ts": job["end_ts"]})
                worker = None
                if self._pending[kind]:
                    worker, total, log = self._pending[kind].popleft()
                    self._begin(kind, total, log)

    def _to_public(self, kind, with_history) -> dict:
        """(잠금 안에서) 화면에 줄 사본."""
        job = self._jobs[kind]
        elapsed = eta = None
        if job["start_ts"] is not None:
            end = job["end_ts"] if job["end_ts"] is not None else time.time()
            elapsed = end - job["start_ts"]
            if job["status"] == "running" and job["current"] > 0 and job["total"] > 0:
                eta = max(0.0, elapsed / job["current"] * (job["total"] - job["current"]))
        public = {"status": job["status"], "current": job["current"], "total": job["total"],
                  "message": job["message"], "elapsed_sec": elapsed, "eta_sec": eta,
                  "error": job["error"], "results": job["results"],
                  "cancel_requested": job["cancel_requested"], "queued": len(self._pending[kind]),
                  "log": list(job["log"]), "finished": self._finished[kind]}
        if with_history:
            public["history"] = list(self._history[kind])
        return public
