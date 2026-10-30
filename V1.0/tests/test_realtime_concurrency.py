"""실시간 감시 동시성 — IMPROVEMENT_PLAN 2-13.

  1. 감시 스레드와 상태 폴링 스레드가 같은 판정 파이프라인(분석 → 반영 → 해제)을 잠금 없이 돌렸다.
  2. 프로파일을 바꾸는 동안 이전 감시가 늦게 낸 결과가 새 프로파일에 섞일 수 있었다 → 세대 토큰.
  3. observations() 가 잠금 없이 순회해서 다른 스레드가 세는 중이면 예외가 날 수 있었다.
  4. 감시기 stop() 의 join 이 시간 초과로 끝나도 스레드를 놓아 버려, 곧바로 start() 하면 둘이 돌았다.
"""
import threading
import time

from api.realtime_watch_api import RealtimeWatchApiMixin
from core.crt_stream_watcher import CRTStreamWatcher
from engine.baseline_diff_engine import BaselineDiffEngine


class Stub(RealtimeWatchApiMixin):
    def _resolve_active_customer_profile_names(self):
        return None, None


def test_results_from_an_older_generation_are_dropped():
    stub = Stub()
    calls = []
    stub._realtime().bump_generation()
    callback = stub._realtime().callback(lambda *a: calls.append(a))
    callback("Core1", "line")
    stub._realtime().bump_generation()                     # 프로파일 전환·재시작
    callback("Core1", "late line")
    assert calls == [("Core1", "line")]


def test_pipeline_callbacks_never_overlap():
    stub = Stub()
    active, overlaps = [0], []

    def handler(*_args):
        active[0] += 1
        if active[0] > 1:
            overlaps.append(True)
        time.sleep(0.01)
        active[0] -= 1

    watcher_cb = stub._realtime().callback(handler)
    poller_cb = stub._realtime().callback(handler)
    threads = [threading.Thread(target=cb, args=("Core1",)) for cb in (watcher_cb, poller_cb) * 5]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert overlaps == []


class FakeStore:
    def get_device_baseline(self, device):
        return {"vlans": set(), "interfaces": set(), "routes": set(), "bgp_neighbors": set()}


def test_observations_can_be_read_while_another_thread_counts():
    engine = BaselineDiffEngine(FakeStore())
    errors, stop = [], threading.Event()

    def feed():
        i = 0
        while not stop.is_set():
            engine.analyze_stream(f"Dev{i % 50}", "%LINEPROTO-5-UPDOWN: x\n")
            i += 1

    worker = threading.Thread(target=feed)
    worker.start()
    try:
        for _ in range(300):
            try:
                engine.observations()
            except RuntimeError as exc:          # dictionary changed size during iteration
                errors.append(exc)
    finally:
        stop.set()
        worker.join()
    assert errors == []


def test_a_watcher_that_does_not_stop_in_time_is_not_started_twice(tmp_path, monkeypatch):
    release = threading.Event()
    watcher = CRTStreamWatcher(tmp_path, lambda *a, **k: None, interval=0.01)
    monkeypatch.setattr(watcher, "_tick", lambda: release.wait(2))
    assert watcher.start() is True
    time.sleep(0.05)
    watcher.stop(join_timeout=0.05)                      # tick 이 아직 안 끝났다
    assert watcher.is_running() is True
    assert watcher.start() is False                      # 두 번째 스레드를 띄우지 않는다
    release.set()
    deadline = time.time() + 2
    while watcher.is_running() and time.time() < deadline:
        time.sleep(0.02)
    assert watcher.is_running() is False
    assert watcher.start() is True                       # 끝난 뒤에는 다시 시작할 수 있다
    watcher.stop()
