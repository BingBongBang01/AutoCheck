"""로그 읽기/판정 캐시 — OPTIMIZATION_PLAN 3-2.

이 캐시의 위험은 성능이 아니라 **낡은 결과**다. 점검을 새로 돌렸는데 대시보드가 이전 회차
수치를 보여주면 그건 성능 개선이 아니라 버그다. 그래서 이 파일의 대부분은 무효화 테스트다.

캐시 키는 (경로, st_mtime_ns, 크기)이므로 파일을 덮어쓰면 자동으로 무효가 된다.
그 성질이 실제로 성립하는지를 파일을 실제로 고쳐 가며 확인한다.
"""
import time
from pathlib import Path

import pytest

from engine import log_cache
from engine.log_analysis import analyze_text
from engine.log_rule_engine import get_engine
from tools.synthetic_log import build_device_corpora

CLEAN_LOG = """Core1#show interfaces status
Et1   connected    1     full   10G
"""

BROKEN_LOG = """Core1#show interfaces status
Et1   notconnect   1     full   10G
Core1#show ntp status
NTP is disabled.
"""


@pytest.fixture(autouse=True)
def fresh_cache():
    """테스트마다 캐시를 비운다 — 다른 테스트가 남긴 항목이 결과를 바꾸면 안 된다."""
    log_cache.clear()
    get_engine().clear_memo()
    yield
    log_cache.clear()


def write(path, text):
    """파일을 쓰고 mtime 이 확실히 달라지게 한다.

    st_mtime_ns 해상도가 아주 좋은 파일시스템에서도 같은 나노초에 두 번 쓰는 일은 없지만,
    테스트가 파일시스템 해상도에 의존하지 않도록 명시적으로 시간을 벌린다.
    """
    path = Path(path)
    if path.exists():
        time.sleep(0.01)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- 기본 동작


def test_text_is_cached(tmp_path):
    path = write(tmp_path / "log.txt", CLEAN_LOG)
    first = log_cache.cached_log_text(path)
    second = log_cache.cached_log_text(path)
    assert first == second == CLEAN_LOG
    stats = log_cache.stats()
    assert stats["text_misses"] == 1
    assert stats["text_hits"] == 1


def test_findings_match_uncached_analyze_text(tmp_path):
    path = write(tmp_path / "log.txt", BROKEN_LOG)
    cached = log_cache.cached_findings(path)
    direct = analyze_text(BROKEN_LOG)

    def key(findings):
        return [(f["rule_id"], f["severity"], f["line_no"], f.get("repeat")) for f in findings]

    assert key(cached) == key(direct)
    assert cached, "이 로그는 findings 가 있어야 비교가 의미 있다"


def test_findings_are_cached(tmp_path):
    path = write(tmp_path / "log.txt", BROKEN_LOG)
    log_cache.cached_findings(path)
    log_cache.cached_findings(path)
    stats = log_cache.stats()
    assert stats["findings_misses"] == 1
    assert stats["findings_hits"] == 1


def test_text_can_be_passed_in_to_avoid_reread(tmp_path):
    """이미 읽어 둔 전문을 넘기면 파일을 다시 읽지 않는다(대시보드 경로가 이렇게 쓴다)."""
    path = write(tmp_path / "log.txt", BROKEN_LOG)
    text = log_cache.cached_log_text(path)
    before = log_cache.stats()["text_misses"]
    log_cache.cached_findings(path, text=text)
    assert log_cache.stats()["text_misses"] == before


# --------------------------------------------------------------------------- 무효화(핵심)


def test_rewritten_file_is_reanalyzed(tmp_path):
    """**이 캐시의 가장 중요한 성질.** 점검이 로그를 새로 쓰면 판정도 새로 나와야 한다."""
    path = write(tmp_path / "log.txt", CLEAN_LOG)
    clean_findings = log_cache.cached_findings(path)

    write(path, BROKEN_LOG)
    broken_findings = log_cache.cached_findings(path)

    assert len(broken_findings) > len(clean_findings), (
        "파일을 덮어썼는데 판정이 그대로다 — 대시보드가 이전 회차 수치를 보여준다"
    )
    assert any(f["rule_id"] == "ntp_not_synced" for f in broken_findings)


def test_rewritten_file_is_reread(tmp_path):
    path = write(tmp_path / "log.txt", CLEAN_LOG)
    assert log_cache.cached_log_text(path) == CLEAN_LOG
    write(path, BROKEN_LOG)
    assert log_cache.cached_log_text(path) == BROKEN_LOG


def test_same_size_different_content_is_detected(tmp_path):
    """크기가 같아도 mtime 이 달라지므로 무효가 된다 — 키에 둘 다 들어 있다.

    크기만으로 키를 잡으면 놓치는 경우다. 두 내용의 바이트 길이를 정확히 맞춰 확인한다.
    """
    before = "Core1#show ntp status\nNTP is disabled.\n"
    after = "Core1#show ntp status\nNTP is synced!!.\n"
    assert len(before.encode()) == len(after.encode()), "테스트 전제: 크기 동일"

    path = write(tmp_path / "log.txt", before)
    first = log_cache.cached_findings(path)
    assert any(f["rule_id"] == "ntp_not_synced" for f in first), "전제: 원본은 NTP 이상이 잡힌다"

    write(path, after)
    second = log_cache.cached_findings(path)
    assert not any(f["rule_id"] == "ntp_not_synced" for f in second), (
        "크기가 같은 덮어쓰기를 놓쳤다 — 낡은 판정이 남는다"
    )


def test_invalidate_drops_entries(tmp_path):
    path = write(tmp_path / "log.txt", BROKEN_LOG)
    log_cache.cached_findings(path)
    assert log_cache.stats()["findings_entries"] == 1
    log_cache.invalidate(path)
    stats = log_cache.stats()
    assert stats["findings_entries"] == 0
    assert stats["text_entries"] == 0


def test_missing_file_is_not_cached(tmp_path):
    """없는 파일은 캐시하지 않고 예전과 같은 예외를 낸다."""
    missing = tmp_path / "gone.txt"
    with pytest.raises(OSError):
        log_cache.cached_log_text(missing)
    assert log_cache.stats()["text_entries"] == 0


# --------------------------------------------------------------------------- 캐시 오염 방지


def test_mutating_returned_findings_does_not_poison_cache(tmp_path):
    """호출자가 받은 dict 를 고쳐도 다음 호출자가 영향을 받지 않아야 한다.

    캐시가 생기면 "받은 finding 에 필드를 하나 붙이자"는 변경이 자연스러워진다. 그 순간
    다음 호출자가 오염된 값을 받으므로, 얕은 복사로 그 사고를 막는다.
    """
    path = write(tmp_path / "log.txt", BROKEN_LOG)
    first = log_cache.cached_findings(path)
    assert first, "findings 가 있어야 이 테스트가 의미 있다"

    first[0]["severity"] = "오염됨"
    first[0]["새필드"] = True
    first.append({"rule_id": "가짜"})

    second = log_cache.cached_findings(path)
    assert second[0]["severity"] != "오염됨"
    assert "새필드" not in second[0]
    assert all(f["rule_id"] != "가짜" for f in second)


# --------------------------------------------------------------------------- 상한


def test_text_entry_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(log_cache, "MAX_TEXT_ENTRIES", 5)
    for index in range(20):
        log_cache.cached_log_text(write(tmp_path / f"log{index}.txt", CLEAN_LOG))
    assert log_cache.stats()["text_entries"] <= 5


def test_text_byte_cap(tmp_path, monkeypatch):
    """총 바이트 상한 — 전문은 크므로 개수만으로는 메모리를 묶을 수 없다."""
    monkeypatch.setattr(log_cache, "MAX_TEXT_BYTES", 4096)
    big = "x" * 2000 + "\n"
    for index in range(10):
        log_cache.cached_log_text(write(tmp_path / f"big{index}.txt", big))
    stats = log_cache.stats()
    assert stats["text_bytes"] <= 4096, f"바이트 상한을 넘었다: {stats}"
    assert stats["text_entries"] >= 1, "상한이 너무 공격적이어서 전부 버려졌다"


def test_findings_entry_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(log_cache, "MAX_FINDING_ENTRIES", 3)
    for index in range(15):
        log_cache.cached_findings(write(tmp_path / f"log{index}.txt", BROKEN_LOG))
    assert log_cache.stats()["findings_entries"] <= 3


def test_verdicts_correct_past_the_cap(tmp_path, monkeypatch):
    """상한을 넘겨도 판정은 정확해야 한다(캐시 미스는 그냥 계산하면 된다)."""
    monkeypatch.setattr(log_cache, "MAX_FINDING_ENTRIES", 2)
    for index in range(10):
        log_cache.cached_findings(write(tmp_path / f"filler{index}.txt", CLEAN_LOG))
    findings = log_cache.cached_findings(write(tmp_path / "real.txt", BROKEN_LOG))
    assert any(f["rule_id"] == "ntp_not_synced" for f in findings)


# --------------------------------------------------------------------------- 이중 파싱 제거


def test_run_analysis_then_second_pass_parses_once(tmp_path, monkeypatch):
    """**3-2 의 핵심 주장.** 점검 직후 두 번째 패스가 추가 파싱을 하지 않는다.

    api/terminal_inspection_api.py 는 run_analysis() 로 전 파일을 파싱한 **직후** 같은
    파일들을 다시 파싱해 경고 목록을 만들었다.
    """
    import engine.log_analysis as log_analysis_module

    original_dir = tmp_path / "raw"
    problem_dir = tmp_path / "problem"
    original_dir.mkdir()
    problem_dir.mkdir()
    for name, text in build_device_corpora(devices=4, seed=7):
        (original_dir / f"20260801_120000_Type_{name}.txt").write_text(text, encoding="utf-8")

    parses = []
    real_analyze = log_analysis_module.analyze_text

    def counting(text, correlate=True):
        parses.append(len(text))
        return real_analyze(text, correlate=correlate)

    monkeypatch.setattr(log_analysis_module, "analyze_text", counting)

    log_analysis_module.run_analysis(str(original_dir), str(problem_dir))
    after_first = len(parses)
    assert after_first == 4, f"run_analysis 가 4개 파일을 파싱해야 한다(실제 {after_first})"

    for path in sorted(original_dir.glob("*.txt")):
        log_cache.cached_findings(str(path))

    assert len(parses) == after_first, (
        f"두 번째 패스가 {len(parses) - after_first}회 더 파싱했다 — 이중 파싱이 남아 있다"
    )


def test_run_analysis_reads_legacy_cp949_logs(tmp_path):
    """run_analysis 가 cp949 레거시 로그를 다른 읽기 경로와 같게 해독하는지.

    예전에는 이 경로만 open(encoding="utf-8", errors="replace") 를 써서 cp949 로 저장된
    로그를 깨진 문자로 읽었다. 3-2 에서 core/text_io.py 규칙으로 통일했다 —
    틀렸던 것이 맞게 되는 방향의 동작 변경이다.
    """
    original_dir = tmp_path / "raw"
    problem_dir = tmp_path / "problem"
    original_dir.mkdir()
    problem_dir.mkdir()
    legacy = original_dir / "20260801_120000_Type_Core1.txt"
    legacy.write_bytes("Core1#show ntp status\nNTP is disabled. 한글 주석\n".encode("cp949"))

    results = __import__("engine.log_analysis", fromlist=["run_analysis"]).run_analysis(
        str(original_dir), str(problem_dir))
    assert results[0]["problem_count"] >= 1

    output = (problem_dir / results[0]["output"]).read_text(encoding="utf-8")
    assert "한글 주석" in output, "cp949 로그가 깨진 채로 분석됐다"
