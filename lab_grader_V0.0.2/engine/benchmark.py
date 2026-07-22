"""
병렬 연결 수(max_parallel_workers)를 여러 값으로 바꿔가며 실제 수집 시간을 측정하고,
"더 늘려도 별 차이 없어지는 지점(diminishing returns)"을 자동으로 찾아 추천값으로 제안한다.

사용법(본인 노트북, 실제 장비 대상):
    python3 -m engine.benchmark --apply
    (--apply 없이 실행하면 추천값만 출력하고 lab_meta.yaml은 안 건드림)
"""
import time
import yaml
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed


def measure_worker_count(connect_fn, device_names, worker_count):
    """connect_fn(device_name) -> None (또는 raw output). 소요시간(초)만 측정."""
    started = time.time()
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(connect_fn, name) for name in device_names]
        for f in as_completed(futures):
            f.result()
    return time.time() - started


def recommend_worker_count(timings, tolerance=0.10):
    """
    timings: {worker_count: elapsed_sec} (worker_count 오름차순)
    가장 빠른 시간 대비 tolerance(기본 10%) 이내로 들어오는 것 중
    '가장 작은' worker_count를 추천 — 리소스(EVE-NG RAM 여유 부족 이력 감안)를
    필요 이상으로 많이 쓰지 않기 위함. 무작정 최댓값을 추천하지 않는다.
    """
    fastest = min(timings.values())
    threshold = fastest * (1 + tolerance)
    candidates = sorted(wc for wc, t in timings.items() if t <= threshold)
    return candidates[0]


def run_benchmark(device_names, connect_fn, candidate_counts=None):
    if candidate_counts is None:
        # 1부터 장비 수까지 전부 시도 (상한 없음 — 장비 수만큼은 다 테스트)
        candidate_counts = list(range(1, len(device_names) + 1))

    timings = {}
    print("=" * 50)
    print("병렬 연결 수 성능 테스트")
    print("=" * 50)
    for wc in candidate_counts:
        elapsed = measure_worker_count(connect_fn, device_names, wc)
        timings[wc] = elapsed
        print(f"  workers={wc:2d}  ->  {elapsed:.2f}초")

    recommended = recommend_worker_count(timings)
    print(f"\n추천 병렬 연결 수: {recommended}  (최속 대비 10% 이내에서 가장 적은 리소스 사용)")
    print("=" * 50)
    return recommended, timings


def apply_recommendation(lab_meta_path, recommended):
    with open(lab_meta_path) as f:
        lab_meta = yaml.safe_load(f)
    lab_meta["max_parallel_workers"] = recommended
    with open(lab_meta_path, "w", encoding="utf-8") as f:
        yaml.dump(lab_meta, f, allow_unicode=True, sort_keys=False)
    print(f"[반영됨] lab_meta.yaml의 max_parallel_workers = {recommended}")


def real_connect_probe(device_name, ip_allocation_path="labs/lab1_campus/ip_allocation.yaml"):
    """실제 장비 대상 - 가벼운 커맨드(show clock) 하나만 실행해 연결 성능만 측정."""
    from netmiko import ConnectHandler
    from engine.collector import load_credentials
    with open(ip_allocation_path) as f:
        ip_allocation = yaml.safe_load(f)
    ip, username, password = load_credentials(device_name, ip_allocation)
    conn = ConnectHandler(device_type="arista_eos", host=ip, username=username, password=password, timeout=20)
    conn.send_command("show clock")
    conn.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="추천값을 lab_meta.yaml에 바로 반영")
    args = parser.parse_args()

    with open("labs/lab1_campus/lab_meta.yaml") as f:
        lab_meta = yaml.safe_load(f)
    device_names = [d["name"] for d in lab_meta["devices"]]

    recommended, _ = run_benchmark(device_names, real_connect_probe)

    if args.apply:
        apply_recommendation("labs/lab1_campus/lab_meta.yaml", recommended)
