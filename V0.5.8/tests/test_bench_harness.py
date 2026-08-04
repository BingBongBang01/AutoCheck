"""벤치마크 하네스 자체의 테스트 — 하네스가 조용히 망가지면 이후 모든 성능 주장이 무의미해진다.

성능 **수치**를 검증하지는 않는다(머신마다 3배 이상 차이 나므로 테스트로 고정할 수 없다).
검증하는 것은 하네스의 계약이다: 지표가 빠지지 않았는지, 퇴행 판정 방향이 맞는지,
코퍼스가 결정적인지.

퇴행 방향 판정은 특히 중요하다 — 개발 중에 실제로 `_speedup_run` 처럼 뒤에 범위가 붙은
지표가 endswith 매칭에서 빠져 **메모 이득 퇴행이 감지되지 않는** 버그가 있었다.
"""
import pytest

from tools import bench_log_analysis as bench
from tools.synthetic_log import build_corpus, build_device_corpora, corpus_stats

# 하네스가 반드시 내놔야 하는 지표 — 이름이 바뀌면 기준선 비교가 조용히 끊긴다.
REQUIRED_METRICS = {
    "analyze_text_ms",
    "us_per_evaluated_line",
    "suppressor_check_ms",
    "match_signature_ms",
    "find_keyword_ms",
    "evaluate_ms",
    "match_signature_memo_speedup_per_file",
    "match_signature_memo_speedup_run",
    "find_keyword_memo_speedup_per_file",
    "find_keyword_memo_speedup_run",
    "memo_hit_ratio_run",
    "state_dev4_payload_kb",
    "state_dev12_payload_kb",
    "state_dev30_payload_kb",
    "state_dev30_kb_per_sec",
}


@pytest.fixture(scope="module")
def result():
    # 테스트에서는 작고 빠르게 — 수치의 정확성이 아니라 구조를 본다.
    return bench.run_benchmark(devices=2, seed=7, repeat=1)


def test_benchmark_emits_all_required_metrics(result):
    missing = REQUIRED_METRICS - set(result["metrics"])
    assert not missing, f"지표가 사라졌다: {sorted(missing)}"


def test_benchmark_metrics_are_numeric(result):
    for name, value in result["metrics"].items():
        assert isinstance(value, (int, float)), f"{name}이 숫자가 아니다: {value!r}"
        assert value >= 0, f"{name}이 음수다: {value}"


def test_benchmark_records_platform_and_config(result):
    """기준선 비교가 다른 머신 수치를 섞지 않으려면 이 메타데이터가 있어야 한다."""
    assert result["platform"]["system"]
    assert result["platform"]["python"]
    assert result["config"] == {"devices": 2, "seed": 7, "repeat": 1}


# --------------------------------------------------------------------------- 퇴행 판정 방향


@pytest.mark.parametrize("name,expected", [
    ("analyze_text_ms", "lower"),
    ("us_per_evaluated_line", "lower"),
    ("state_dev30_payload_kb", "lower"),
    ("state_dev30_kb_per_sec", "lower"),
    ("match_signature_memo_speedup_run", "higher"),
    ("find_keyword_memo_speedup_per_file", "higher"),
    ("memo_hit_ratio_run", "higher"),
    # 잡음/기술 정보는 비교 대상이 아니다.
    ("analyze_text_spread", None),
    ("analyze_text_median_ms", None),   # 대표값(min)과 중복이라 잡음 오탐만 낸다
    ("stage_lines", None),
    ("memo_unique_lines", None),
    ("duplicate_ratio", None),
])
def test_regression_direction(name, expected):
    assert bench._direction(name) == expected


def _fake(metrics, devices=2, seed=7, repeat=1):
    return {
        "config": {"devices": devices, "seed": seed, "repeat": repeat},
        "corpus": {},
        "platform": {"system": "Linux", "python": "3.11.0", "machine": "x86_64"},
        "metrics": metrics,
    }


def test_slower_time_is_a_regression():
    baseline = _fake({"analyze_text_ms": 100.0})
    current = _fake({"analyze_text_ms": 130.0})
    regressions, improvements = bench.compare_with_baseline(current, baseline)
    assert [name for name, *_ in regressions] == ["analyze_text_ms"]
    assert improvements == []


def test_faster_time_is_an_improvement():
    baseline = _fake({"analyze_text_ms": 100.0})
    current = _fake({"analyze_text_ms": 70.0})
    regressions, improvements = bench.compare_with_baseline(current, baseline)
    assert regressions == []
    assert [name for name, *_ in improvements] == ["analyze_text_ms"]


def test_dropped_speedup_is_a_regression():
    """이 케이스가 개발 중 실제로 감지되지 않았다 — 회귀 방지를 위해 고정한다."""
    baseline = _fake({"match_signature_memo_speedup_run": 6.4})
    current = _fake({"match_signature_memo_speedup_run": 3.2})
    regressions, _improvements = bench.compare_with_baseline(current, baseline)
    assert [name for name, *_ in regressions] == ["match_signature_memo_speedup_run"]


def test_change_within_threshold_is_ignored():
    baseline = _fake({"analyze_text_ms": 100.0})
    current = _fake({"analyze_text_ms": 105.0})     # +5% < 임계값 10%
    regressions, improvements = bench.compare_with_baseline(current, baseline)
    assert regressions == []
    assert improvements == []


def test_missing_baseline_metric_is_skipped():
    """기준선에 없는 새 지표는 퇴행으로 보지 않는다(지표를 추가할 때마다 실패하면 안 된다)."""
    baseline = _fake({"analyze_text_ms": 100.0})
    current = _fake({"analyze_text_ms": 100.0, "brand_new_metric_ms": 42.0})
    regressions, improvements = bench.compare_with_baseline(current, baseline)
    assert regressions == []
    assert improvements == []


# --------------------------------------------------------------------------- 코퍼스 계약


def test_device_corpora_matches_device_count():
    corpora = build_device_corpora(devices=3, seed=7)
    assert [device for device, _text in corpora] == ["Core1", "Core2", "Core3"]
    assert all(text.strip() for _device, text in corpora)


def test_device_corpora_is_deterministic():
    first = build_device_corpora(devices=3, seed=7)
    second = build_device_corpora(devices=3, seed=7)
    assert first == second


def test_duplicate_ratio_solver_hits_target():
    """이분탐색이 목표 중복률을 오차 0.02 이내로 맞춰야 한다.

    닫힌 형태 근사식을 쓰던 시절 0.68 목표에 0.60 이 나왔다 — 메모 이득 곡선을 찍는
    근거가 되는 값이므로 정확해야 한다.
    """
    for target in (0.3, 0.6, 0.85):
        corpus = build_corpus(devices=8, duplicate_ratio=target)
        achieved = corpus_stats(corpus)["duplicate_ratio"]
        assert abs(achieved - target) <= 0.02, f"목표 {target} -> 달성 {achieved}"


def test_invalid_duplicate_ratio_rejected():
    with pytest.raises(ValueError):
        build_corpus(devices=2, duplicate_ratio=1.0)
    with pytest.raises(ValueError):
        build_corpus(devices=2, duplicate_ratio=-0.1)


# --------------------------------------------------------------------------- 잡음 하한


def test_tiny_absolute_change_is_not_a_regression():
    """절대값이 작은 지표는 상대 변화가 커도 퇴행이 아니다.

    counter_row_ms 가 0.31 -> 0.57 로 흔들려 +83.9% 퇴행으로 보고된 적이 있다. 0.26 ms 는
    아무도 체감하지 못하는 차이인데, 그 오탐 하나로 게이트 전체가 신뢰를 잃는다.
    """
    baseline = _fake({"counter_row_ms": 0.31})
    current = _fake({"counter_row_ms": 0.57})
    regressions, improvements = bench.compare_with_baseline(current, baseline)
    assert regressions == []
    assert improvements == []


def test_large_absolute_change_still_flagged():
    """잡음 하한이 진짜 퇴행을 삼켜서는 안 된다."""
    baseline = _fake({"analyze_text_ms": 68.0})
    current = _fake({"analyze_text_ms": 136.0})
    regressions, _improvements = bench.compare_with_baseline(current, baseline)
    assert [name for name, *_ in regressions] == ["analyze_text_ms"]


def test_small_speedup_wobble_is_ignored():
    baseline = _fake({"match_signature_memo_speedup_run": 3.20})
    current = _fake({"match_signature_memo_speedup_run": 3.15})   # 0.05배 차이
    regressions, improvements = bench.compare_with_baseline(current, baseline)
    assert regressions == []
    assert improvements == []


@pytest.mark.parametrize("name,expected", [
    ("analyze_text_ms", 2.0),
    ("state_dev30_payload_kb", 5.0),
    ("state_dev30_kb_per_sec", 10.0),
    ("us_per_evaluated_line", 2.0),
    ("match_signature_memo_speedup_run", 0.1),
    ("memo_hit_ratio_run", 0.1),
])
def test_noise_floor_values(name, expected):
    assert bench._noise_floor(name) == expected


# --------------------------------------------------------------------------- 판정 불가 처리


def test_exit_codes_are_distinct():
    """호출자가 '퇴행'과 '판정 불가'를 구별할 수 있어야 한다 — 둘의 대응이 다르다."""
    codes = {bench.EXIT_OK, bench.EXIT_REGRESSION, bench.EXIT_NO_BASELINE, bench.EXIT_INCONCLUSIVE}
    assert len(codes) == 4
    assert bench.EXIT_OK == 0


def test_spread_limit_is_above_quiet_machine_noise():
    """허용 편차는 조용한 머신의 잡음(5%)보다 크고 퇴행 임계값보다 작지 않아야 한다."""
    assert 0.05 < bench.SPREAD_LIMIT
    assert bench.SPREAD_LIMIT <= bench.REGRESSION_THRESHOLD


# --------------------------------------------------------------------------- 잡음 적응 임계값


def _fake_spread(spread, metrics):
    payload = _fake(dict(metrics))
    payload["metrics"]["measurement_spread_p75"] = spread
    return payload


def test_effective_threshold_floor_on_quiet_machine():
    """조용한 실행에서는 기본 임계값을 그대로 쓴다."""
    quiet = _fake_spread(0.02, {"analyze_text_ms": 100.0})
    assert bench.effective_threshold(quiet) == bench.REGRESSION_THRESHOLD


def test_effective_threshold_rises_with_noise():
    """시끄러운 실행에서는 임계값이 잡음에 비례해 올라간다."""
    noisy = _fake_spread(0.062, {"analyze_text_ms": 100.0})
    assert bench.effective_threshold(noisy) == pytest.approx(0.155)


def test_borderline_change_ignored_on_noisy_run():
    """p75 6.2% 실행에서 +13.5% 는 잡음 범위 — 퇴행으로 보지 않는다(실제로 오탐이 났던 케이스)."""
    baseline = _fake({"match_signature_ms": 34.22})
    current = _fake_spread(0.062, {"match_signature_ms": 38.85})
    regressions, _improvements = bench.compare_with_baseline(current, baseline)
    assert regressions == []


def test_same_change_flagged_on_quiet_run():
    """같은 +13.5% 가 조용한 실행에서는 퇴행이다 — 적응 임계값이 진짜 퇴행을 삼키지 않는다."""
    baseline = _fake({"match_signature_ms": 34.22})
    current = _fake_spread(0.02, {"match_signature_ms": 38.85})
    regressions, _improvements = bench.compare_with_baseline(current, baseline)
    assert [name for name, *_ in regressions] == ["match_signature_ms"]


def test_large_regression_flagged_even_when_noisy():
    """잡음이 커도 2배 퇴행은 반드시 잡힌다."""
    baseline = _fake({"analyze_text_ms": 68.0})
    current = _fake_spread(0.09, {"analyze_text_ms": 136.0})
    regressions, _improvements = bench.compare_with_baseline(current, baseline)
    assert [name for name, *_ in regressions] == ["analyze_text_ms"]
