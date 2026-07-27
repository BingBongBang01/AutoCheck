"""
Progress Engine — 채점 실행의 실시간 진행 상태(장비 진행률/커맨드 진행률/경과 시간/
잔여 시간 추정/재시도 횟수)를 서버 쪽에서 전부 계산해 이벤트로 쌓아둔다.
GUI는 snapshot()/events()로 이미 계산된 결과만 읽는다 — 경과/잔여 시간을 GUI(JS)가
직접 계산하지 않는다("GUI receives events only" 원칙).
"""
import threading
import time

_LOCK = threading.Lock()
_JOBS = {}
_MAX_EVENTS = 1000
_last_job_id = None


class ProgressEngine:
    def __init__(self, job_id, devices, commands_per_device=0):
        self.job_id = job_id
        self.started_at = time.time()
        self.device_total = len(devices)
        self.devices_completed = 0
        self.devices = {
            d: {"status": "pending", "command_done": 0, "command_total": commands_per_device,
                "retry": 0, "started_at": None, "finished_at": None}
            for d in devices
        }
        self.events = []
        self._lock = threading.Lock()

    def _estimate_remaining(self, elapsed):
        if self.devices_completed == 0 or self.device_total == 0:
            return None
        avg_per_device = elapsed / self.devices_completed
        return round(avg_per_device * (self.device_total - self.devices_completed), 2)

    def _push(self, event_type, device=None, **extra):
        elapsed = time.time() - self.started_at
        event = {
            "job_id": self.job_id, "event": event_type, "device": device,
            "elapsed_sec": round(elapsed, 2), "remaining_sec": self._estimate_remaining(elapsed),
            "devices_completed": self.devices_completed, "device_total": self.device_total,
            "ts": time.time(),
        }
        event.update(extra)
        with self._lock:
            self.events.append(event)
            if len(self.events) > _MAX_EVENTS:
                self.events.pop(0)
        return event

    def device_start(self, device):
        info = self.devices.setdefault(device, {"status": "pending", "command_done": 0,
                                                  "command_total": 0, "retry": 0,
                                                  "started_at": None, "finished_at": None})
        info["status"] = "running"
        info["started_at"] = time.time()
        return self._push("device_start", device=device)

    def command_done(self, device, command, duration_ms=None):
        info = self.devices.get(device)
        if info is not None:
            info["command_done"] += 1
        return self._push("command_done", device=device, command=command, duration_ms=duration_ms,
                           command_done=info["command_done"] if info else None,
                           command_total=info["command_total"] if info else None)

    def retry(self, device, attempt, max_attempts):
        info = self.devices.get(device)
        if info is not None:
            info["retry"] = attempt
        return self._push("retry", device=device, retry=attempt, retry_max=max_attempts)

    def device_done(self, device, success):
        info = self.devices.get(device)
        if info is not None:
            info["status"] = "success" if success else "failed"
            info["finished_at"] = time.time()
        self.devices_completed += 1
        return self._push("device_done", device=device, success=success)

    def snapshot(self):
        elapsed = time.time() - self.started_at
        return {
            "job_id": self.job_id,
            "elapsed_sec": round(elapsed, 2),
            "remaining_sec": self._estimate_remaining(elapsed),
            "devices_completed": self.devices_completed,
            "device_total": self.device_total,
            "devices": {k: dict(v) for k, v in self.devices.items()},
        }

    def recent_events(self, since=0):
        with self._lock:
            return list(self.events[since:])


class _NullProgress:
    """progress engine이 없는 호출부(단독 실행/테스트)를 위한 무동작 대체 구현."""

    def device_start(self, *a, **kw): return None
    def command_done(self, *a, **kw): return None
    def retry(self, *a, **kw): return None
    def device_done(self, *a, **kw): return None
    def snapshot(self): return {}
    def recent_events(self, since=0): return []


NULL_PROGRESS = _NullProgress()


def get_or_create(job_id, devices, commands_per_device=0):
    global _last_job_id
    with _LOCK:
        engine = _JOBS.get(job_id)
        if engine is None:
            engine = ProgressEngine(job_id, devices, commands_per_device)
            _JOBS[job_id] = engine
        _last_job_id = job_id
        return engine


def get(job_id):
    with _LOCK:
        return _JOBS.get(job_id)


def get_last_job_id():
    with _LOCK:
        return _last_job_id


def snapshot(job_id=None):
    """job_id를 안 주면 가장 최근에 생성된 job(보통 진행 중이거나 직전에 끝난 채점)을 반환."""
    with _LOCK:
        job_id = job_id or _last_job_id
    engine = get(job_id) if job_id else None
    return engine.snapshot() if engine else None


if __name__ == "__main__":
    pe = get_or_create("job1", ["Core1", "Core2"], commands_per_device=2)
    pe.device_start("Core1")
    pe.command_done("Core1", "show vlan brief", duration_ms=80)
    pe.retry("Core2", 2, 3)
    pe.device_done("Core1", True)
    print(pe.snapshot())
    print(pe.recent_events())
