"""판정 엔진 / 실시간 폴링 벤치마크 하네스 — OPTIMIZATION_PLAN 의 이득 주장을 숫자로 뒷받침한다.

왜 필요한가: 지금까지 성능 측정이 매번 임시 스크립트로 이뤄져 비교가 불가능했다. 그 결과
"정규식 31개를 하나로 합치면 빨라진다"는 그럴듯한 가설이 실제로는 2.6배 퇴행이라는 사실을
아무도 몰랐다. 이 하네스는 고정 시드 코퍼스로 같은 숫자를 재현해, 변경이 이득인지 퇴행인지
판정한다.

사용법:
    python -m tools.bench_log_analysis                    # 측정해서 표로 출력
    python -m tools.bench_log_analysis --save-baseline     # 현재 수치를 기준선으로 저장
    python -m tools.bench_log_analysis --check             # 기준선과 비교
    python -m tools.bench_log_analysis --json              # 기계 판독용 출력

--check 종료 코드:
    0  퇴행 없음
    1  퇴행 감지
    2  기준선 파일 없음
    3  판정 불가 — 머신이 너무 시끄러워 비교가 무의미하다. 조용한 상태에서 다시 재라.

**조용한 머신에서 돌려라.** 빌드/백신/브라우저가 같이 돌면 같은 코드가 14~54% 흔들린다
(실측). 하네스는 이 상황을 스스로 감지해 3번(판정 불가)을 내지만, 완벽하지는 않다 —
공유 컨테이너에서 8회 연속 돌린 실측에서 7회는 정상, 1회는 17% 넘는 스윙으로 퇴행이
보고됐다. 퇴행이 보고되면 **먼저 한 번 더 재보고** 재현되는지 확인할 것.

주의: 절대값은 머신마다 3배 이상 차이 난다(같은 코퍼스가 Windows 15.7us/line, Linux
47.8us/line). 그래서 --check 는 **같은 머신에서 저장한 기준선**과만 비교해야 의미가 있다.
기준선 파일에 플랫폼을 함께 기록해 다른 머신의 기준선과 비교하면 경고한다.
"""
import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path

from engine.log_analysis import analyze_text
from engine.log_rule_engine import ContextTracker, get_engine
from engine.realtime_monitor import RealtimeMonitor
from tools.synthetic_log import DEFAULT_SEED, build_corpus, build_device_corpora, corpus_stats

BASELINE_PATH = Path(__file__).resolve().parent / "bench_baseline.json"

# 이 비율을 넘어 느려지면 퇴행으로 본다. 측정 잡음(조용한 머신에서 반복 편차 5% 이내)보다
# 넉넉하게 잡아, 잡음 때문에 CI 가 흔들리지 않게 한다.
REGRESSION_THRESHOLD = 0.10

# 반복 편차가 이 값을 넘으면 **측정 자체를 신뢰할 수 없다**고 보고 퇴행 판정을 하지 않는다.
#
# 이 장치가 필요한 이유: 공유/가상화 환경에서는 CPU 경합만으로 같은 코드가 14~54% 흔들린다
# (실측: 벤치마크를 5회 연속 돌리자 find_keyword_ms 가 21.3 -> 32.8 ms 로 튀었다). 그 상태에서
# 퇴행을 선언하면 게이트가 거짓말을 하고, 임계값을 그만큼 올리면 진짜 퇴행을 놓친다.
# 그래서 셋째 결과를 둔다 — "판정 불가, 조용한 상태에서 다시 재라".
SPREAD_LIMIT = 0.10

# 유효 임계값 = max(REGRESSION_THRESHOLD, NOISE_MULTIPLIER x 이번 실행의 측정 편차 p75).
#
# 고정 임계값 하나로는 두 실패를 동시에 피할 수 없다. 조용한 실행에 맞춰 10% 로 두면 살짝
# 붐빈 실행에서 오탐이 나고(실측: p75 6.2% 인 실행에서 match_signature_ms 가 +13.5% 로
# 퇴행 판정됨), 시끄러운 실행에 맞춰 20% 로 올리면 진짜 15% 퇴행을 놓친다.
# 그래서 "변화가 이 실행 자신의 잡음보다 도드라지는가"를 기준으로 삼는다.
NOISE_MULTIPLIER = 2.5

EXIT_OK, EXIT_REGRESSION, EXIT_NO_BASELINE, EXIT_INCONCLUSIVE = 0, 1, 2, 3

# 실시간 감시 payload 를 재는 장비 수 — 4(소규모), 12(일반), 30(대규모).
DEVICE_SCALES = (4, 12, 30)

# 폴링 주기(초) — web_ui/js/realtime-monitor-panel.js 의 setInterval 값과 같아야 한다.
POLL_INTERVAL_SEC = 0.8


# 이번 실행의 모든 _timed() 편차를 모은다. 측정 블록이 여러 개라서(analyze_text / 단계별 /
# 메모 / 실시간) analyze_text 만 보면 뒤쪽 블록이 CPU 경합에 맞은 것을 놓친다 — 실제로
# analyze_text 편차가 1.6% 인데 find_keyword_ms 가 +53% 튄 실행이 있었다.
_spreads = []


def _percentile(values, fraction):
    """정렬된 값의 fraction 분위. numpy 없이(의존성 추가 없이) 계산한다."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return ordered[index]


def _timed(fn, repeat, setup=None):
    """fn 을 repeat 회 돌려 (최소, 중앙값, 상대편차) 를 ms 로 반환.

    평균이 아니라 최소를 대표값으로 쓴다 — 다른 프로세스의 방해는 시간을 늘리기만 하므로,
    최소값이 '이 코드가 낼 수 있는 시간'에 가장 가깝고 반복 재현성이 높다.

    setup: 각 측정 **전에** 실행되며 시간에 포함되지 않는다. 판정 엔진이 줄 단위 메모를
    갖게 된 뒤로 이게 필요해졌다 — 메모를 비우지 않으면 2회차부터 전부 캐시 히트라서
    최소값이 '완전히 워밍된 상태'가 되고, 이득이 실제보다 크게 나온다.
    """
    samples = []
    for _ in range(repeat):
        if setup is not None:
            setup()
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    low = min(samples)
    median = statistics.median(samples)
    # 편차는 (중앙값 - 최소)/최소 로 잰다. (최대 - 최소)를 쓰면 한 번의 스케줄링 방해가
    # 그대로 지표를 튀게 해서 "재현성 불량"이라는 잘못된 경고가 뜬다.
    spread = (median - low) / low if low > 0 else 0.0
    _spreads.append(spread)
    return low, median, spread


def measure_analyze_text(corpus, repeat):
    """코퍼스 하나를 분석하는 시간 — 메모를 매번 비우고 잰다.

    비우지 않으면 2회차부터 전부 캐시 히트가 되어 min 이 '완전 워밍' 수치가 된다. 그건 앱의
    실제 상황(회차 첫 장비는 콜드)이 아니고, 2-1 적용 전 기준선과도 비교할 수 없다.
    메모의 진짜 이득(장비 사이 중복)은 *_memo_speedup_run 지표가 따로 보고한다.
    """
    stats = corpus_stats(corpus)
    engine = get_engine()
    low, median, spread = _timed(lambda: analyze_text(corpus), repeat,
                                 setup=engine.clear_memo)
    per_line = low * 1000.0 / stats["evaluated_lines"] if stats["evaluated_lines"] else 0.0
    return {
        "analyze_text_ms": round(low, 2),
        "analyze_text_median_ms": round(median, 2),
        "analyze_text_spread": round(spread, 4),
        "us_per_evaluated_line": round(per_line, 2),
    }


def measure_stages(corpus, repeat):
    """evaluate() 내부 단계별 비용 — 어디를 고쳐야 하는지 알려주는 유일한 지표.

    합계가 evaluate 총합보다 큰 것은 정상이다. evaluate 는 단축평가한다(억제되면 서명을
    돌리지 않고, 서명이 걸리면 키워드를 돌리지 않는다).
    """
    engine = get_engine()
    ctx = ContextTracker()
    lines = [line for line in corpus.splitlines() if not ctx.feed(line)]
    probe = ContextTracker()

    def run(fn):
        return lambda: [fn(line) for line in lines]

    stages = {
        "suppressor_check_ms": run(lambda l: engine.suppressor.check(l, None, probe, include_config_scope=False)),
        "match_signature_ms": run(lambda l: engine.signatures.match_signature(l, probe)),
        "counter_row_ms": run(lambda l: engine.signatures.counter_row(l, probe)),
        "find_keyword_ms": run(lambda l: engine.find_keyword(l)),
        "evaluate_ms": run(lambda l: engine.evaluate(l, probe)),
    }
    result = {"stage_lines": len(lines)}
    for name, fn in stages.items():
        # 단계별 측정도 메모를 비우고 잰다 — 그러지 않으면 match_signature/find_keyword 가
        # 2회차부터 캐시 히트만 하게 되어 '어디가 비싼가'를 알 수 없다.
        low, _median, _spread = _timed(fn, repeat, setup=engine.clear_memo)
        result[name] = round(low, 2)
    return result


def measure_memo_potential(corpus, repeat, devices, seed):
    """줄 단위 메모화의 이득 — OPTIMIZATION_PLAN 2-1 이 실제로 얼마를 버는지.

    두 시나리오를 **분리해서** 잰다. 하나로 뭉치면 이득을 잘못 말하게 된다:

      * per_file — 파일마다 메모를 새로 만든다. 한 장비 로그 안의 중복만 활용한다
                   (인터페이스 표의 "   no shutdown", "!" 처럼 같은 파일에서 반복되는 줄).
      * run      — 회차 전체(장비 N대)가 메모를 공유한다. RuleEngine 이 프로세스 싱글턴이라
                   실제 앱이 이 모양이다. 장비 사이에 반복되는 줄(포트 48개 표가 장비마다
                   똑같다)까지 히트하므로 이쪽이 진짜 이득이다.

    '메모가 100% 채워진 상태'는 재지 않는다. 그건 dict 조회 속도(약 200배)일 뿐 앱에서
    도달할 수 없는 수치이고, 그런 숫자를 근거로 계획을 쓰면 수용 기준이 거짓이 된다.
    """
    engine = get_engine()
    probe = ContextTracker()
    device_corpora = build_device_corpora(devices=devices, seed=seed)

    # 장비별로 판정 대상 줄만 미리 뽑아 둔다(ctx.feed 비용을 측정에서 배제).
    per_device_lines = []
    for _device, text in device_corpora:
        tracker = ContextTracker()
        per_device_lines.append([l for l in text.splitlines() if not tracker.feed(l)])

    # 엔진의 **실제** 메모를 토글해서 잰다. 2-1 적용 전에는 하네스가 자기 래퍼로 메모를
    # 흉내냈는데, 이제 엔진이 직접 메모하므로 그 방식으로는 '메모 없는 기준선'이 이미 메모된
    # 경로를 부르게 되어 측정이 무의미해진다.
    def plain(fn):
        def run():
            engine.set_memo_enabled(False)
            for lines in per_device_lines:
                for line in lines:
                    fn(line)
        return run

    def per_file(fn):
        def run():
            engine.set_memo_enabled(True)
            for lines in per_device_lines:
                engine.clear_memo()               # 파일마다 메모를 버린다
                for line in lines:
                    fn(line)
        return run

    def whole_run(fn):
        def run():
            engine.set_memo_enabled(True)
            engine.clear_memo()                   # 회차 시작에 한 번만 비운다
            for lines in per_device_lines:
                for line in lines:
                    fn(line)
        return run

    def sig_fn(line):
        return engine.signatures.match_signature(line, probe)

    kw_fn = engine.find_keyword

    def speedup(fn):
        base, _m, _s = _timed(plain(fn), repeat)
        file_scoped, _m, _s = _timed(per_file(fn), repeat)
        run_scoped, _m, _s = _timed(whole_run(fn), repeat)
        return (
            round(base / file_scoped, 2) if file_scoped else 0.0,
            round(base / run_scoped, 2) if run_scoped else 0.0,
        )

    try:
        sig_file, sig_run = speedup(sig_fn)
        kw_file, kw_run = speedup(kw_fn)
    finally:
        # 반드시 원래대로 돌려놓는다 — 껐다 둔 채로 두면 뒤따르는 측정이 메모 없는 상태로
        # 재어져 이득이 사라진 것처럼 보인다.
        engine.set_memo_enabled(True)
        engine.clear_memo()

    total_lines = sum(len(lines) for lines in per_device_lines)
    unique_lines = len({line for lines in per_device_lines for line in lines})
    return {
        "duplicate_ratio": corpus_stats(corpus)["duplicate_ratio"],
        "memo_unique_lines": unique_lines,
        "memo_hit_ratio_run": round(1.0 - unique_lines / total_lines, 4) if total_lines else 0.0,
        "match_signature_memo_speedup_per_file": sig_file,
        "match_signature_memo_speedup_run": sig_run,
        "find_keyword_memo_speedup_per_file": kw_file,
        "find_keyword_memo_speedup_run": kw_run,
    }


def measure_realtime_state(repeat):
    """실시간 감시 폴링 1회의 계산 시간과 payload 크기.

    OPTIMIZATION_PLAN 3-1 의 수용 기준(장비 30대에서 50 KB/s 이하)을 이 숫자로 판정한다.
    계산 시간은 이미 싸다는 것이 확인됐으므로, 여기서 실제로 봐야 하는 것은 payload 다.
    """
    result = {}
    for count in DEVICE_SCALES:
        monitor = RealtimeMonitor()
        devices = [f"Dev{index}" for index in range(count)]
        monitor.reset(devices, devices)
        for device in devices:
            monitor.append_lines(device, "\n".join(
                f"{device} %LINEPROTO-5-UPDOWN Interface Ethernet{i} changed state to down"
                for i in range(400)
            ))
        monitor.apply_alerts([{
            "device": devices[i % count],
            "alert_id": f"a{i}",
            "type": "LINK_DOWN",
            "severity": "CRITICAL",
            "target": f"link:Et{i % 40}",
            "message": f"link down {i}",
            "raw_line": f"%LINEPROTO-5-UPDOWN Et{i % 40} down",
            "ts": "12:00:00",
        } for i in range(300)])

        low, _median, _spread = _timed(lambda: monitor.state(tail=160), repeat)
        payload_kb = _payload_kb(monitor.state(tail=160))
        result[f"state_dev{count}_ms"] = round(low, 3)
        result[f"state_dev{count}_payload_kb"] = round(payload_kb, 1)
        result[f"state_dev{count}_kb_per_sec"] = round(payload_kb / POLL_INTERVAL_SEC, 1)

        # 델타 폴링(3-1). 이것이 실제로 화면이 쓰는 경로다 — 전체 payload 는 첫 폴링과
        # 강제 재동기화에서만 쓴다. 두 가지를 재는데, 둘 다 실사용 회차의 모양이다:
        #   quiet  — 새 줄이 없는 회차. 대부분의 폴링이 여기다(사람이 명령을 치는 속도 <<
        #            0.8초 폴링). 섹션 생략만으로 얼마까지 줄어드는지.
        #   active — 장비마다 한 줄이 새로 들어온 회차. 수용 기준(50 KB/s)은 이걸로 본다.
        full = monitor.state(tail=160)
        cursor = {"epoch": full["epoch"], "versions": dict(full["versions"]),
                  "devices": {d["device"]: d["line_seq"] for d in full["devices"]}}
        quiet_kb = _payload_kb(monitor.state(tail=160, since=cursor))
        for device in devices:
            monitor.append_lines(device, f"{device} show running-config | include ntp")
        active_kb = _payload_kb(monitor.state(tail=160, since=cursor))

        result[f"state_dev{count}_delta_quiet_kb"] = round(quiet_kb, 2)
        result[f"state_dev{count}_delta_kb"] = round(active_kb, 2)
        result[f"state_dev{count}_delta_kb_per_sec"] = round(active_kb / POLL_INTERVAL_SEC, 1)
        # 배수도 남긴다 — 절대값만 보면 tail/장비 수를 바꿨을 때 퇴행인지 조건 변경인지 갈린다.
        result[f"state_dev{count}_delta_shrink"] = round(payload_kb / active_kb, 1) if active_kb else 0.0
    return result


def _payload_kb(payload):
    return len(json.dumps(payload, ensure_ascii=False).encode()) / 1024.0


def run_benchmark(devices, seed, repeat):
    corpus = build_corpus(devices=devices, seed=seed)
    _spreads.clear()
    metrics = {}
    metrics.update(measure_analyze_text(corpus, repeat))
    metrics.update(measure_stages(corpus, repeat))
    metrics.update(measure_memo_potential(corpus, repeat, devices, seed))
    metrics.update(measure_realtime_state(repeat))
    # 측정 신뢰도 — 전체 측정 블록 편차의 75분위. "이 실행을 믿을 수 있는가"의 단일 신호다.
    #
    # max 를 쓰면 안 된다: counter_row 처럼 0.3 ms 짜리 블록은 상대 편차가 늘 크기 때문에
    # (조용한 실행에서도 max 8.1%) 매번 판정 불가가 된다. 반대로 analyze_text 하나만 보면
    # 뒤쪽 블록의 경합을 놓친다(편차 1.6% 인데 find_keyword 가 +53% 튄 실행이 있었다).
    # 75분위는 단일 이상치에 흔들리지 않으면서 여러 블록이 동시에 느려지는 상황에는 반응한다
    # (조용한 실행 실측: median 1.2% / p75 2.9% / max 8.1%).
    metrics["measurement_spread_p75"] = round(_percentile(_spreads, 0.75), 4) if _spreads else 0.0
    return {
        "config": {"devices": devices, "seed": seed, "repeat": repeat},
        "corpus": corpus_stats(corpus),
        "platform": {
            "system": platform.system(),
            "python": platform.python_version(),
            "machine": platform.machine(),
        },
        "metrics": metrics,
    }


# --------------------------------------------------------------------------- 출력 / 비교

# 값이 '작을수록 좋은' 지표와 '클수록 좋은' 지표를 나눈다.
# suffix 매칭(endswith)을 쓰면 match_signature_memo_speedup_run 처럼 뒤에 범위가 붙은
# 이름이 조용히 검사에서 빠진다 — 실제로 그 버그로 메모 이득 퇴행이 감지되지 않았다.
# 그래서 부분 문자열로 판정한다.
_HIGHER_IS_BETTER_MARKERS = ("_speedup", "hit_ratio", "_shrink")
_LOWER_IS_BETTER_MARKERS = ("_ms", "_kb", "us_per_evaluated_line")

# 측정 잡음 지표와 순수 기술 정보 — 퇴행 판정에서 제외한다.
#
# analyze_text_median_ms 를 빼는 이유: 대표값은 최소값(analyze_text_ms)이고 중앙값은 참고용
# 이다. 둘을 다 비교하면 같은 것을 두 번 재면서 더 시끄러운 쪽이 오탐을 낸다 — 실제로
# 코드 변경이 전혀 없는 상태에서 중앙값만 +12.6% 로 튀어 퇴행으로 잡혔다. 게이트가 이렇게
# 한 번이라도 거짓말을 하면 아무도 그 결과를 믿지 않게 된다.
_NOT_COMPARED = (
    "analyze_text_spread",
    "measurement_spread_p75",
    "analyze_text_median_ms",
    "stage_lines",
    "memo_unique_lines",
    "duplicate_ratio",
)


def _direction(name):
    if name in _NOT_COMPARED:
        return None
    if any(marker in name for marker in _HIGHER_IS_BETTER_MARKERS):
        return "higher"
    if any(marker in name for marker in _LOWER_IS_BETTER_MARKERS):
        return "lower"
    return None


def _noise_floor(name):
    """이 값보다 작은 절대 변화는 잡음으로 보고 무시한다.

    상대 변화만 보면 절대값이 작은 지표가 계속 오탐을 낸다 — counter_row_ms 는 0.3 ms 라
    0.26 ms 흔들린 것이 +83.9% 로 잡혀 퇴행으로 보고됐다. 0.26 ms 는 어떤 사용자도
    체감하지 못하는 차이다. 상대·절대 조건을 **둘 다** 넘겨야 퇴행으로 본다.
    """
    # 델타 payload 는 전체 payload 보다 두 자리 작다(8 KB 대 700 KB). 아래의 전체 payload 기준
    # 바닥값(5 KB / 10 KB/s)을 그대로 쓰면 1 KB -> 4 KB 같은 4배 퇴행이 조용히 통과한다.
    # 크기 지표는 애초에 측정 잡음이 없다(같은 입력이면 JSON 이 바이트 단위로 같다) — 바닥값은
    # '이 정도 변화는 신경 쓸 필요 없다'는 뜻일 뿐이므로 작게 잡는 것이 맞다.
    if "_delta" in name and "_kb_per_sec" in name:
        return 1.0         # 1 KB/s
    if "_delta" in name and "_kb" in name:
        return 0.5         # 0.5 KB
    if "_kb_per_sec" in name:
        return 10.0        # 10 KB/s 미만 변화는 의미 없다
    if "_kb" in name:
        return 5.0         # 5 KB
    if "us_per" in name:
        return 2.0         # 2 us/line
    if "_ms" in name:
        return 2.0         # 2 ms
    if "_speedup" in name or "hit_ratio" in name or "_shrink" in name:
        return 0.1         # 0.1배 / 10%p
    return 0.0


def print_report(result):
    config, corpus, metrics = result["config"], result["corpus"], result["metrics"]
    print(f"플랫폼: {result['platform']['system']} / Python {result['platform']['python']}")
    print(f"코퍼스: 장비 {config['devices']}대, 시드 {config['seed']}, 반복 {config['repeat']}회")
    print(f"        전체 {corpus['total_lines']:,}줄 / 판정 대상 {corpus['evaluated_lines']:,}줄 / "
          f"고유 {corpus['unique_evaluated']:,}줄 (중복률 {corpus['duplicate_ratio']:.1%})")
    print()
    print(f"{'지표':<34} {'값':>12}")
    print("-" * 48)
    for name, value in metrics.items():
        if name in ("stage_lines", "duplicate_ratio", "memo_unique_lines", "measurement_spread_p75"):
            continue
        print(f"{name:<34} {value:>12}")
    spread = metrics.get("measurement_spread_p75", 0.0)
    print()
    if spread <= 0.05:
        note = "(재현성 양호)"
    elif spread <= SPREAD_LIMIT:
        note = "(허용 범위, 비교 가능)"
    else:
        note = f"({SPREAD_LIMIT:.0%} 초과 — 이 측정으로는 퇴행 판정 불가)"
    print(f"측정 편차(p75): {spread:.1%} {note}")


def effective_threshold(result, base=REGRESSION_THRESHOLD):
    """이번 실행에 적용할 퇴행 임계값 — 측정 잡음이 클수록 함께 올라간다."""
    spread = result["metrics"].get("measurement_spread_p75", 0.0)
    return max(base, NOISE_MULTIPLIER * spread)


def compare_with_baseline(result, baseline, threshold=None):
    """기준선과 비교해 퇴행 목록을 돌려준다. threshold=None 이면 잡음 적응 임계값을 쓴다."""
    if threshold is None:
        threshold = effective_threshold(result)
    if baseline["platform"]["system"] != result["platform"]["system"]:
        print(f"[경고] 기준선 플랫폼({baseline['platform']['system']})이 현재"
              f"({result['platform']['system']})와 다르다 — 절대값 비교는 무의미하다.",
              file=sys.stderr)
    if baseline["config"] != result["config"]:
        print(f"[경고] 기준선 설정({baseline['config']})이 현재({result['config']})와 다르다.",
              file=sys.stderr)

    regressions, improvements = [], []
    for name, current in result["metrics"].items():
        if not isinstance(current, (int, float)):
            continue
        previous = baseline["metrics"].get(name)
        if not isinstance(previous, (int, float)) or previous == 0:
            continue
        direction = _direction(name)
        if direction is None:
            continue
        change = (current - previous) / previous
        # 상대 조건과 절대 조건을 둘 다 넘겨야 한다 — 절대값이 작은 지표의 오탐을 막는다.
        if abs(current - previous) < _noise_floor(name):
            continue
        worse = change > threshold if direction == "lower" else change < -threshold
        better = change < -threshold if direction == "lower" else change > threshold
        if worse:
            regressions.append((name, previous, current, change))
        elif better:
            improvements.append((name, previous, current, change))
    return regressions, improvements


def main(argv=None):
    parser = argparse.ArgumentParser(description="AutoCheck 판정/폴링 벤치마크")
    parser.add_argument("--devices", type=int, default=8, help="합성 코퍼스 장비 수 (기본 8)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="코퍼스 시드 (기본 %d)" % DEFAULT_SEED)
    parser.add_argument("--repeat", type=int, default=5, help="측정 반복 횟수 (기본 5, 최소값을 대표값으로 씀)")
    parser.add_argument("--save-baseline", action="store_true", help="현재 수치를 기준선으로 저장")
    parser.add_argument("--check", action="store_true", help="기준선과 비교해 퇴행이면 exit 1")
    parser.add_argument("--json", action="store_true", help="JSON 으로 출력")
    args = parser.parse_args(argv)

    result = run_benchmark(args.devices, args.seed, args.repeat)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)

    if args.save_baseline:
        BASELINE_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n기준선 저장: {BASELINE_PATH}")
        return 0

    if args.check:
        if not BASELINE_PATH.exists():
            print(f"\n[오류] 기준선이 없다: {BASELINE_PATH}\n"
                  f"       먼저 --save-baseline 으로 만들어라.", file=sys.stderr)
            return EXIT_NO_BASELINE

        spread = result["metrics"].get("measurement_spread_p75", 0.0)
        if spread > SPREAD_LIMIT:
            print(f"\n[판정 불가] 측정 편차(p75) {spread:.1%} > 허용 {SPREAD_LIMIT:.0%} — 이 머신이"
                  f" 지금 너무 시끄러워서 퇴행 여부를 말할 수 없다.\n"
                  f"            다른 작업을 멈추고 다시 재라(--repeat 을 늘리는 것도 도움이 된다).",
                  file=sys.stderr)
            return EXIT_INCONCLUSIVE

        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        threshold = effective_threshold(result)
        regressions, improvements = compare_with_baseline(result, baseline, threshold)
        print()
        for name, previous, current, change in improvements:
            print(f"[개선] {name}: {previous} -> {current} ({change:+.1%})")
        for name, previous, current, change in regressions:
            print(f"[퇴행] {name}: {previous} -> {current} ({change:+.1%})", file=sys.stderr)
        if regressions:
            print(f"\n{len(regressions)}개 지표가 임계값({threshold:.1%})을 넘어 퇴행했다.",
                  file=sys.stderr)
            return EXIT_REGRESSION
        print(f"퇴행 없음 (유효 임계값 {threshold:.1%} = max({REGRESSION_THRESHOLD:.0%}, "
              f"{NOISE_MULTIPLIER}x 편차 {spread:.1%})).")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
