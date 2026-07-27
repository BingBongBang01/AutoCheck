"""
Worker Pool — 장비 단위 병렬 수집을 위한 재사용 가능한 스레드풀 래퍼.
engine/collector.py에 인라인으로 있던 ThreadPoolExecutor 생성/캡 로직을 여기로 옮겨
설정 가능한(worker 수) 하나의 컴포넌트로 만든다.

장비-레벨 병렬성만 제공한다 — 장비 하나 안에서 커맨드를 순차 실행할지 말지는
전달되는 fn(collect_device 등)의 책임이며, 이 클래스는 fn을 device당 정확히 1번,
서로 다른 스레드에서 동시에 호출할 뿐이다(SSH 세션은 장비당 stateful하므로
fn 내부에서 커맨드를 병렬화하면 안 됨).
"""
from concurrent.futures import ThreadPoolExecutor, as_completed

DEFAULT_MAX_WORKERS_CAP = 50


class WorkerPool:
    def __init__(self, max_workers=None, item_count=1, cap=DEFAULT_MAX_WORKERS_CAP):
        """max_workers: 명시 설정값(우선). None이면 item_count(예: 장비 수)만큼 쓰되 cap을 넘지 않음."""
        resolved = max_workers or item_count or 1
        self.max_workers = min(resolved, cap)

    def run(self, items, fn, *args, **kwargs):
        """items를 fn(item, *args, **kwargs)로 병렬 처리하고, 완료되는 순서대로
        (item, result) 튜플을 yield한다. 예외는 삼키지 않고 그대로 올린다."""
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(fn, item, *args, **kwargs): item for item in items}
            for future in as_completed(futures):
                yield futures[future], future.result()

    def run_keyed(self, jobs):
        """jobs: {key: (fn, args, kwargs)} — 장비마다 인자가 다른 경우(collector.py처럼)를 위한 변형.
        완료되는 순서대로 (key, result)를 yield한다."""
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            for key, (fn, args, kwargs) in jobs.items():
                futures[executor.submit(fn, *args, **kwargs)] = key
            for future in as_completed(futures):
                yield futures[future], future.result()


if __name__ == "__main__":
    import time

    def _work(n):
        time.sleep(0.05)
        return n * n

    pool = WorkerPool(max_workers=4, item_count=10)
    for item, result in pool.run(range(10), _work):
        print(item, "->", result)
