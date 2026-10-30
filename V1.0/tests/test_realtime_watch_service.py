"""실시간 감시 서비스 분리와 잠금 — IMPROVEMENT_PLAN 3-5.

감시 상태가 api/log_analysis_run_api.py(1,200줄) 믹스인의 getattr 속성으로 흩어져 있던 것을
engine/realtime_watch_service.py 로 옮기면서, 플래그와 잠금 없는 읽기-수정-쓰기를 잠금으로 바꿨다:
  1. 화면 설정(realtime_watch.yaml): 거의 동시에 들어온 두 변경 중 하나가 사라질 수 있었다.
  2. 프로파일 전환: '전환 중' 플래그를 본 뒤 세우는 사이가 비어, 두 스레드가 함께 감시를 재시작할 수 있었다.
  3. 서비스·모니터 지연 생성: 자동 시작 스레드와 화면 폴링이 동시에 만들 수 있었다.
"""
import threading
import time

from engine.realtime_watch_service import RealtimeWatchConfig, RealtimeWatchService


def run_together(target, count):
    barrier = threading.Barrier(count)

    def worker(index):
        barrier.wait()
        target(index)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def test_concurrent_filter_toggles_are_all_kept(tmp_path, monkeypatch):
    from engine import project_manager as pm
    monkeypatch.setattr(pm, "get_active_project", lambda: "proj_a")
    config = RealtimeWatchConfig(tmp_path / "realtime_watch.yaml")

    def hide(index):
        config.edit_filter(lambda current: {"hidden_devices": current["hidden_devices"] + [f"Access{index}"]})

    run_together(hide, 12)
    assert sorted(config.normalized_filter()["hidden_devices"]) == sorted(f"Access{i}" for i in range(12))


def test_concurrent_updates_of_different_keys_do_not_overwrite_each_other(tmp_path):
    config = RealtimeWatchConfig(tmp_path / "realtime_watch.yaml")
    run_together(lambda index: config.update({f"key{index}": index}), 12)
    stored = config.read()
    assert all(stored.get(f"key{i}") == i for i in range(12))


class Host:
    def __init__(self):
        self.names = ("고객사A", "2026-07")

    def active_names(self):
        return self.names

    def profile_log_paths(self):
        return None

    def inventory_targets(self):
        return []

    def connect_targets(self):
        return []

    def push_js(self, fn_name, *args):
        pass


class SlowWatcher:
    interval = 0.3

    def __init__(self):
        self.running = True

    def is_running(self):
        return self.running

    def stop(self):
        time.sleep(0.05)                 # 멈추는 데 시간이 걸린다 — 그 사이 다른 스레드가 들어온다
        self.running = False

    def status(self):
        return {}


def test_two_threads_switching_profiles_restart_the_watch_once(tmp_path, monkeypatch):
    from core.paths import AppPaths
    monkeypatch.setattr(AppPaths, "_user_data_root", tmp_path / "AutoCheck")
    AppPaths.forget_ensured()
    host = Host()
    service = RealtimeWatchService(host, RealtimeWatchConfig(tmp_path / "realtime_watch.yaml"))
    service.monitor()                                   # 감시를 한 번 쓴 상태
    service.watcher = SlowWatcher()
    started = []
    service.start_watch = lambda interval=0.3, device_names=None: started.append(interval) or {"ok": True}

    host.names = ("고객사A", "2026-08")                  # 프로파일이 바뀌었다
    results = []
    run_together(lambda _i: results.append(service.sync_profile(force=True)), 4)
    assert results.count(True) == 1 and started == [0.3]
    AppPaths.forget_ensured()


def test_the_service_and_its_monitor_are_created_once(monkeypatch):
    from api.realtime_watch_api import RealtimeWatchApiMixin
    from engine import realtime_monitor

    created = []
    original_init = realtime_monitor.RealtimeMonitor.__init__

    def counting_init(self, *args, **kwargs):
        created.append(self)
        time.sleep(0.02)                                # 만드는 사이에 다른 스레드가 들어오게
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(realtime_monitor.RealtimeMonitor, "__init__", counting_init)

    class Stub(RealtimeWatchApiMixin):
        def _resolve_active_customer_profile_names(self):
            return None, None

    stub = Stub()
    services, monitors = [], []

    def touch(_index):
        service = stub._realtime()
        services.append(service)
        monitors.append(service.monitor())

    run_together(touch, 6)
    assert len({id(s) for s in services}) == 1
    assert len({id(m) for m in monitors}) == 1 and len(created) == 1
