"""재현 가능한 합성 점검 로그 생성기 — 벤치마크와 골든 테스트가 공유하는 단일 코퍼스 출처.

왜 합성 로그인가: 실제 수집 로그는 고객사 데이터라 저장소에 넣을 수 없고, 넣더라도 장비
구성이 바뀌면 성능 수치가 흔들려 회귀 판정을 할 수 없다. 여기서는 **고정 시드**로 같은
텍스트를 항상 만들어, "코드 변경 때문에 느려졌는지"만 남긴다.

무엇을 흉내내는가 (실제 Arista/Cisco 수집 로그의 성질):
  * 인터페이스 표가 장비마다 그대로 반복된다 -> 중복률이 높다(메모화 이득의 근거)
  * syslog 줄은 타임스탬프/포트 번호가 매번 달라 중복되지 않는다 -> 메모가 안 먹는 부분
  * running-config 출력이 길다 -> 억제 계층이 가장 많이 도는 구간
  * counters errors 표는 헤더 정렬이 필요하다 -> ContextTracker.header_tokens 경로

duplicate_ratio 파라미터로 '중복 줄 비율'을 조절할 수 있다. 메모화 이득은 이 값에
비례하므로, 벤치마크는 여러 값으로 곡선을 찍어 단일 숫자로 과대주장하지 않게 한다.
"""
import random

__all__ = ["build_corpus", "build_device_corpora", "corpus_stats", "DEFAULT_SEED"]

DEFAULT_SEED = 7

# 장비 1대의 '반복되는 부분' — 포트 수만큼 거의 동일한 줄이 나온다.
_PORTS_PER_DEVICE = 48
_CONFIG_INTERFACES = 24


def _counters_table(rng, device, lines):
    lines.append(f"{device}#show interfaces counters errors")
    lines.append("Port       FCS      Align     Symbol    Runts")
    lines.append("---------- -------- --------- --------- ---------")
    for port in range(1, _PORTS_PER_DEVICE + 1):
        lines.append(f"Et{port}        0        0         0         0")
    # 0이 아닌 칸 — counter_nonzero 서명이 걸려야 하는 줄
    for port in (2, 7, 13):
        lines.append(f"Et{port}        {rng.randint(1, 99)}       0         3         0")


def _interface_status(device, lines):
    lines.append(f"{device}#show interfaces status")
    for port in range(1, _PORTS_PER_DEVICE + 1):
        state = "connected" if port % 7 else "notconnect"
        lines.append(f"Et{port}   {state}    1     full   10G")


def _running_config(device, lines):
    lines.append(f"{device}#show running-config")
    for port in range(1, _CONFIG_INTERFACES + 1):
        lines.append(f"interface Ethernet{port}")
        lines.append("   no shutdown")
        lines.append("!")
    # 설정 원문 안의 shutdown — 억제되어야 하는 줄(오탐 방지 경로)
    lines.append("interface Ethernet47")
    lines.append("   shutdown")
    lines.append("!")


def _benign_noise(lines):
    """키워드는 걸리지만 정상인 줄들 — 억제 계층을 반드시 통과하게 만든다."""
    lines.append("Number of table drops    : 0")
    lines.append("hitless-reload-down   Disabled   300")
    lines.append("   U - In Use    D - Down")
    lines.append("Interfaces that will be enabled at the next timeout:")
    lines.append("  0 input errors, 0 CRC, 0 frame")


def _syslog(rng, device, lines, count):
    """타임스탬프와 포트가 매번 달라 중복되지 않는 줄 — 메모가 안 먹는 구간."""
    for _ in range(count):
        hour, minute, sec = rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59)
        port = rng.randint(1, _PORTS_PER_DEVICE)
        lines.append(
            f"Mar  3 {hour:02d}:{minute:02d}:{sec:02d} {device} "
            f"%LINEPROTO-5-UPDOWN: Interface Ethernet{port}, changed state to down"
        )


def build_corpus(devices=8, seed=DEFAULT_SEED, syslog_per_device=40, duplicate_ratio=None):
    """합성 점검 로그 텍스트를 만든다.

    devices           : 장비 수(= 반복 블록 수). 중복률을 올리는 가장 직접적인 손잡이.
    seed              : 고정 시드. 같은 인자면 항상 같은 텍스트.
    syslog_per_device : 장비당 syslog 줄 수. 늘리면 고유 줄 비율이 올라간다.
    duplicate_ratio   : None이 아니면 syslog_per_device를 역산해 목표 중복률에 맞춘다.
                        0.0(전부 고유) ~ 0.95 범위를 권장한다.
    """
    if duplicate_ratio is not None:
        # 닫힌 형태의 역산은 쓰지 않는다. 장비명이 명령 줄에 박혀 있어("Core1#show ..." vs
        # "Core2#show ...") 반복 블록조차 장비마다 완전히 동일하지 않고, 0이 아닌 카운터 행은
        # 난수라서, 근사식이 목표치를 최대 0.1까지 빗나갔다(0.68 목표 -> 0.60 실측).
        # 그래서 실제로 만들어 재고 맞춘다 — 결정적이고, 달성치를 정직하게 보고할 수 있다.
        syslog_per_device = _solve_syslog_count(devices, seed, duplicate_ratio)

    rng = random.Random(seed)
    lines = []
    for index in range(1, devices + 1):
        device = f"Core{index}"
        _counters_table(rng, device, lines)
        _interface_status(device, lines)
        _running_config(device, lines)
        _benign_noise(lines)
        _syslog(rng, device, lines, syslog_per_device)
    return "\n".join(lines) + "\n"


def build_device_corpora(devices=8, seed=DEFAULT_SEED, syslog_per_device=40):
    """장비별 로그를 **따로** 돌려준다 — [(장비명, 텍스트), ...].

    앱의 실제 분석 단위는 파일 하나(= 장비 하나)다. engine/log_analysis.py 의 run_analysis()
    가 raw/*.txt 를 순회하며 파일마다 analyze_text() 를 부르고, RuleEngine 은 프로세스
    싱글턴이라 규칙 객체(그리고 앞으로 도입할 메모)는 파일 사이에 공유된다.
    build_corpus()의 한 덩어리 텍스트로는 이 '파일 경계'를 재현할 수 없어서 따로 둔다.
    """
    rng = random.Random(seed)
    corpora = []
    for index in range(1, devices + 1):
        device = f"Core{index}"
        lines = []
        _counters_table(rng, device, lines)
        _interface_status(device, lines)
        _running_config(device, lines)
        _benign_noise(lines)
        _syslog(rng, device, lines, syslog_per_device)
        corpora.append((device, "\n".join(lines) + "\n"))
    return corpora


def _solve_syslog_count(devices, seed, target_ratio, tolerance=0.01, max_iter=24):
    """목표 중복률에 가장 가까운 syslog_per_device 를 이분탐색으로 찾는다.

    syslog 줄은 전부 고유하므로 개수를 늘리면 중복률이 단조 감소한다 — 이분탐색이 성립한다.
    달성 불가능한 목표(예: 반복 블록만으로도 넘는 상한 초과)는 가장 가까운 값으로 수렴한다.
    """
    if not 0.0 <= target_ratio < 1.0:
        raise ValueError("duplicate_ratio는 0.0 이상 1.0 미만이어야 합니다.")

    def ratio_for(count):
        text = build_corpus(devices=devices, seed=seed, syslog_per_device=count)
        return corpus_stats(text)["duplicate_ratio"]

    low, high = 0, 64
    # 상한 확장: syslog 를 늘려도 목표보다 중복률이 높으면 더 늘린다.
    while ratio_for(high) > target_ratio and high < 100_000:
        low, high = high, high * 2

    best, best_gap = high, abs(ratio_for(high) - target_ratio)
    for _ in range(max_iter):
        if low >= high:
            break
        mid = (low + high) // 2
        achieved = ratio_for(mid)
        gap = abs(achieved - target_ratio)
        if gap < best_gap:
            best, best_gap = mid, gap
        if best_gap <= tolerance:
            break
        if achieved > target_ratio:
            low = mid + 1
        else:
            high = mid
    return best


def corpus_stats(text):
    """코퍼스의 성질 — 벤치마크가 '어떤 로그에서 낸 숫자인지' 함께 보고하기 위함."""
    from engine.log_rule_engine import ContextTracker

    all_lines = text.splitlines()
    ctx = ContextTracker()
    evaluated = [line for line in all_lines if not ctx.feed(line)]
    unique = len(set(evaluated))
    return {
        "total_lines": len(all_lines),
        "evaluated_lines": len(evaluated),
        "unique_evaluated": unique,
        "duplicate_ratio": round(1.0 - unique / len(evaluated), 4) if evaluated else 0.0,
    }
