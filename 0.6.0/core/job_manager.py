"""
Job Manager — 앱 전체의 "실행 가능한 작업 하나"를 표준 상태 기계로 통일한다.

이전에는 채점(api/grade_api.py, 완전 동기/블로킹)·로그분석(api/job_runner.py, kind당
슬롯 1개, Queued 없음)·터미널점검(api/terminal_inspection_api.py, 자체 cancel 플래그)이
각자 다른 방식으로 "지금 뭐가 실행 중인가"를 관리했다. JobManager는 그 상태를
QUEUED/RUNNING/COMPLETED/FAILED/CANCELLED 5가지로 통일하고, 실제 큐(동시에 여러 개
제출돼도 순서대로 처리)와 취소(협조적 — fn이 job.cancel_requested를 직접 확인해야 함)를
제공한다. engine/run_manager.py의 Run 상태 기계(READY/RUNNING/PAUSED/...)는 그대로
두고 건드리지 않는다 — Job Manager는 그 위에서 "언제 어떤 fn을 실행할지"만 조율한다.

모든 상태 전이는 core/event_bus.py로 publish된다 — GUI/History Manager 등은 이 이벤트만
구독하면 되고 JobManager 내부를 직접 알 필요가 없다.
"""
import queue
import threading
import time
import uuid

from core.event_bus import bus as event_bus


class JobStatus:
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CancelledError(Exception):
    """fn이 job.cancel_requested를 확인하고 협조적으로 중단할 때 던지는 예외."""


class Job:
    def __init__(self, job_id, kind, fn, args, kwargs, resumer=None):
        self.job_id = job_id
        self.kind = kind
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.resumer = resumer          # 선택: resume() 시 다시 호출할 fn(보통 fn과 동일)
        self.status = JobStatus.QUEUED
        self.created_at = time.time()
        self.started_at = None
        self.ended_at = None
        self.error = None
        self.result = None
        self.cancel_requested = False

    def to_dict(self):
        return {
            "job_id": self.job_id, "kind": self.kind, "status": self.status,
            "created_at": self.created_at, "started_at": self.started_at, "ended_at": self.ended_at,
            "error": self.error, "cancel_requested": self.cancel_requested,
        }


class JobManager:
    """kind 구분 없이 하나의 큐로 처리하는 단일 워커 스레드. 여러 job이 제출되면
    QUEUED 상태로 쌓였다가 순서대로 실행된다(기존 JobRunner처럼 두 번째 제출을 그냥
    버리지 않음 — 이게 '큐' 개념이 없던 이전 구현과의 핵심 차이)."""

    def __init__(self):
        self._jobs = {}
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def submit(self, kind, fn, *args, resumer=None, **kwargs):
        job_id = f"{kind}_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        job = Job(job_id, kind, fn, args, kwargs, resumer=resumer or fn)
        with self._lock:
            self._jobs[job_id] = job
        event_bus.publish("job.queued", job.to_dict())
        self._queue.put(job_id)
        return job_id

    def _loop(self):
        while True:
            job_id = self._queue.get()
            job = self._jobs.get(job_id)
            if job is None or job.status == JobStatus.CANCELLED:
                continue  # 큐에 있는 동안 취소됨
            self._run(job)

    def _run(self, job):
        job.status = JobStatus.RUNNING
        job.started_at = time.time()
        event_bus.publish("job.started", job.to_dict())
        try:
            job.result = job.fn(job, *job.args, **job.kwargs)
        except CancelledError:
            job.status = JobStatus.CANCELLED
            job.ended_at = time.time()
            event_bus.publish("job.cancelled", job.to_dict())
            return
        except Exception as e:
            job.status = JobStatus.FAILED
            job.ended_at = time.time()
            job.error = str(e)
            event_bus.publish("job.failed", job.to_dict())
            return
        job.status = JobStatus.COMPLETED
        job.ended_at = time.time()
        event_bus.publish("job.completed", job.to_dict())

    def cancel(self, job_id):
        """QUEUED면 즉시 취소 확정. RUNNING이면 cancel_requested만 세우고, fn이
        스스로 확인해서 CancelledError를 던져야 실제로 멈춘다(협조적 취소)."""
        job = self._jobs.get(job_id)
        if job is None:
            return False
        job.cancel_requested = True
        if job.status == JobStatus.QUEUED:
            job.status = JobStatus.CANCELLED
            job.ended_at = time.time()
            event_bus.publish("job.cancelled", job.to_dict())
        return True

    def resume(self, job_id):
        """FAILED/CANCELLED로 끝난 job과 동일한 fn/kwargs로 새 job을 제출한다.
        fn 자체가 '이어서 하기'를 지원해야 의미가 있다(예: engine/run_manager.resume_run을
        내부에서 호출하는 fn) — JobManager는 재제출만 담당."""
        job = self._jobs.get(job_id)
        if job is None:
            raise ValueError(f"존재하지 않는 job: {job_id}")
        if job.status not in (JobStatus.FAILED, JobStatus.CANCELLED):
            raise ValueError(f"resume 불가 상태(FAILED/CANCELLED만 가능): {job.status}")
        return self.submit(job.kind, job.resumer, *job.args, resumer=job.resumer, **job.kwargs)

    def get(self, job_id):
        job = self._jobs.get(job_id)
        return job.to_dict() if job else None

    def list_jobs(self, kind=None, status=None):
        with self._lock:
            jobs = list(self._jobs.values())
        if kind:
            jobs = [j for j in jobs if j.kind == kind]
        if status:
            jobs = [j for j in jobs if j.status == status]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return [j.to_dict() for j in jobs]


job_manager = JobManager()


if __name__ == "__main__":
    def _work(job, n):
        for i in range(n):
            if job.cancel_requested:
                raise CancelledError()
            time.sleep(0.01)
        return n * 2

    jid = job_manager.submit("demo", _work, 5)
    time.sleep(0.5)
    print(job_manager.get(jid))
    print("전체 목록:", job_manager.list_jobs())
