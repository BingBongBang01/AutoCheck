"""analyze_text() 전체 출력 스냅샷 — 고정 합성 코퍼스에 대한 판정 결과를 통째로 못박는다.

단위 케이스(test_log_rule_engine_golden.py)는 줄 하나의 판정을 본다. 여기서는 FSM 블록
분할·스로틀(중복 접기)·상관분석(복합 finding 승격)까지 **파이프라인 전체**를 본다.
OPTIMIZATION_PLAN 2-1(메모화)은 줄 판정만 바꾸므로 이 스냅샷이 그대로 유지되어야 한다 —
스냅샷이 바뀌면 메모화가 판정을 바꿨다는 뜻이고, 그것이 이 파일의 존재 이유다.

스냅샷 갱신 규칙: 규칙 파일(config/log_rules.json)이나 코퍼스 생성기를 의도적으로 바꿨을
때만 갱신한다. 성능 최적화 때문에 바뀌었다면 갱신하지 말고 최적화를 되돌린다.
"""
import pytest

from engine.log_analysis import analyze_text, format_report, summarize
from engine.log_rule_engine import ContextTracker
from tools.synthetic_log import build_corpus, corpus_stats

# 스냅샷용 코퍼스는 작게 고정한다 — 실패했을 때 사람이 눈으로 대조할 수 있어야 한다.
CORPUS_DEVICES = 2
CORPUS_SYSLOG_PER_DEVICE = 6


@pytest.fixture(scope="module")
def corpus():
    return build_corpus(devices=CORPUS_DEVICES, syslog_per_device=CORPUS_SYSLOG_PER_DEVICE)


def test_corpus_is_deterministic():
    """같은 인자면 항상 같은 텍스트여야 한다 — 아니면 아래 스냅샷 전부가 무의미하다."""
    first = build_corpus(devices=CORPUS_DEVICES, syslog_per_device=CORPUS_SYSLOG_PER_DEVICE)
    second = build_corpus(devices=CORPUS_DEVICES, syslog_per_device=CORPUS_SYSLOG_PER_DEVICE)
    assert first == second


def test_corpus_shape(corpus):
    """코퍼스의 성질도 고정한다 — 중복률이 바뀌면 메모화 이득 수치의 전제가 바뀐다."""
    stats = corpus_stats(corpus)
    assert stats == {
        "total_lines": 380,
        "evaluated_lines": 372,
        "unique_evaluated": 148,
        "duplicate_ratio": 0.6022,
    }


# --------------------------------------------------------------------------- 원시 계층(스로틀 전)


def test_raw_findings_snapshot(corpus):
    """correlate=False — 스로틀/상관분석을 거치지 않은 원시 finding 목록."""
    findings = analyze_text(corpus, correlate=False)
    assert len(findings) == 18

    counts = {}
    for finding in findings:
        counts[finding["rule_id"]] = counts.get(finding["rule_id"], 0) + 1
    assert counts == {"counter_nonzero": 6, "link_state_down": 12}

    # 원시 계층에서는 접기가 일어나지 않았으므로 repeat 키가 아직 없다.
    assert all("repeat" not in finding for finding in findings)


# --------------------------------------------------------------------------- 전체 파이프라인

# (rule_id, severity, line_no, repeat, last_line_no, is_correlated)
EXPECTED_FINDINGS = [
    ("link_flapping", "critical", 185, 1, 380, True),
    ("counter_nonzero", "major", 52, 6, 244, False),
    ("link_state_down", "major", 185, 12, 380, False),
]


def test_full_pipeline_snapshot(corpus):
    findings = analyze_text(corpus)
    actual = [
        (
            finding["rule_id"],
            finding["severity"],
            finding["line_no"],
            finding.get("repeat"),
            finding.get("last_line_no"),
            bool(finding.get("is_correlated")),
        )
        for finding in findings
    ]
    assert actual == EXPECTED_FINDINGS


def test_summary_snapshot(corpus):
    assert summarize(analyze_text(corpus)) == {"info": 0, "minor": 0, "major": 2, "critical": 1}


def test_throttle_folds_repeats(corpus):
    """스로틀이 실제로 접는지 — 18건이 3건으로 줄고 repeat 합이 원시 건수를 보존한다.

    복합 finding(link_flapping)은 원시 줄에서 만들어진 게 아니라 합성된 것이므로 repeat 합
    계산에서 제외한다. 이 구분이 무너지면 보고서의 '동일 사유 N회 반복' 숫자가 틀린다.
    """
    raw = analyze_text(corpus, correlate=False)
    folded = analyze_text(corpus)
    non_composite = [finding for finding in folded if not finding.get("is_correlated")]
    assert sum(finding["repeat"] for finding in non_composite) == len(raw)


def test_correlated_finding_carries_evidence(corpus):
    """복합 finding 은 어떤 서명들이 결론을 뒷받침했는지 남겨야 한다 — 없으면 되짚을 수 없다."""
    composite = next(f for f in analyze_text(corpus) if f.get("is_correlated"))
    assert composite["rule_id"] == "link_flapping"
    assert composite["matched_rules"]
    assert composite["block"], "근거 줄이 비어 있다"
    assert composite["reason"].startswith("링크") or "근거:" in composite["reason"]


def test_format_report_orders_by_severity(corpus):
    """보고서는 '지금 당장 볼 것'이 맨 위에 와야 한다 — critical 이 major 보다 앞선다."""
    findings = analyze_text(corpus)
    report = format_report("Core1.txt", findings)
    assert report.startswith("=== 이상탐지 결과 — Core1.txt (총 3건)")
    assert "치명 1건" in report
    assert report.index("[치명]") < report.index("[중대]")


def test_evaluated_line_count_matches_context_tracker(corpus):
    """analyze_text 가 실제로 판정하는 줄 수 = ContextTracker 가 소비하지 않은 줄 수.

    최적화가 ctx.feed() 경로를 건드려 명령/구분선 줄을 판정 대상에 넣기 시작하면
    (또는 그 반대로 데이터 줄을 소비하기 시작하면) 여기서 잡힌다.
    """
    ctx = ContextTracker()
    evaluated = [line for line in corpus.splitlines() if not ctx.feed(line)]
    assert len(evaluated) == 372
