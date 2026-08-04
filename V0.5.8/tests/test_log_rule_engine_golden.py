"""판정 엔진 characterization 테스트 — **현재 동작을 고정**하는 것이 목적이다.

이 테스트는 "판정이 이래야 한다"는 당위를 주장하지 않는다. "지금 이렇게 판정한다"를 못박아
두어, 성능 최적화(OPTIMIZATION_PLAN.md 2단계)가 판정 결과를 바꾸는 순간 실패하게 만든다.
그래서 어떤 케이스가 실패하면 기본 대응은 **테스트를 고치는 것이 아니라 코드 변경을 되돌리는
것**이다. 단, 0단계에서 테스트를 처음 쓰는 동안 실패한 케이스는 현재 동작을 잘못 기술한
것이므로 그때는 테스트를 고친다.

케이스 출처: engine/log_rule_engine.py 상단 주석에 실제 수집 로그에서 관측된 오탐/미탐이
문장 단위로 나열돼 있다. 그것을 그대로 골든 케이스로 쓴다 — 그 문장들이 이 엔진이 4층
구조를 갖게 된 이유이므로, 최적화가 깨면 안 되는 것의 정확한 목록이기도 하다.
"""
import json

import pytest

from engine.log_rule_engine import (
    ContextTracker,
    SEVERITY_ORDER,
    _rule_file_path,
    get_engine,
    load_rules,
    max_severity,
    severity_rank,
)


@pytest.fixture(scope="module")
def engine():
    return get_engine()


def judge(engine, line, ctx=None):
    """맥락 없이 한 줄만 판정 — 오탐/미탐 케이스 확인용."""
    return engine.evaluate(line, ctx if ctx is not None else ContextTracker())


def feed_all(ctx, lines):
    """명령/구분선 줄을 ctx에 먹이고, 판정 대상으로 남은 줄만 돌려준다."""
    return [line for line in lines if not ctx.feed(line)]


# --------------------------------------------------------------------------- 오탐(억제되어야 함)

# 키워드는 걸리지만 맥락상 정상인 줄. 하나라도 판정되면 운영자는 정상 장비에서 경고를 본다.
FALSE_POSITIVE_LINES = [
    pytest.param("Number of table drops    : 0", id="counter-value-zero"),
    pytest.param("hitless-reload-down   Disabled   300", id="feature-status-table-row"),
    pytest.param("Interfaces that will be enabled at the next timeout:", id="informational-sentence"),
    pytest.param("   U - In Use    D - Down", id="port-channel-flag-legend"),
    pytest.param("  0 input errors, 0 CRC, 0 frame", id="zero-counters-inline"),
]


@pytest.mark.parametrize("line", FALSE_POSITIVE_LINES)
def test_benign_lines_are_suppressed(engine, line):
    assert judge(engine, line) is None, f"오탐이 되살아났다: {line!r}"


# --------------------------------------------------------------------------- 미탐(잡아야 함)

# 키워드가 없거나 약해서 예전 로직이 통째로 놓쳤던 줄. (줄, rule_id, severity)
FALSE_NEGATIVE_CASES = [
    pytest.param("NTP is disabled.", "ntp_not_synced", "major", id="ntp-disabled"),
    pytest.param("% Invalid input detected at '^' marker.", "command_rejected", "minor", id="invalid-input"),
    pytest.param("% Unavailable command", "command_rejected", "minor", id="unavailable-command"),
    pytest.param("The system rebooted due to unknown reasons.", "unexpected_reload", "major", id="unexpected-reload"),
]


@pytest.mark.parametrize("line,rule_id,severity", FALSE_NEGATIVE_CASES)
def test_signature_only_lines_are_detected(engine, line, rule_id, severity):
    verdict = judge(engine, line)
    assert verdict is not None, f"미탐이 되살아났다: {line!r}"
    assert verdict["rule_id"] == rule_id
    assert verdict["severity"] == severity


# --------------------------------------------------------------------------- 맥락 의존 판정


def test_running_config_section_is_suppressed(engine):
    """running-config 안의 shutdown은 '설정 의도'이지 '지금 상태'가 아니다.

    이 억제가 사라지면 정상 장비에서도 매번 경고가 뜬다. 억제가 ctx.is_config에 의존하므로
    최적화가 ctx 전달을 건드리면 여기서 먼저 깨진다.
    """
    ctx = ContextTracker()
    lines = feed_all(ctx, [
        "Core1#show running-config",
        "interface Ethernet3",
        "   shutdown",
    ])
    assert ctx.command == "show running-config"
    assert ctx.is_config is True
    for line in lines:
        assert judge(engine, line, ctx) is None, f"설정 원문이 장애로 올라왔다: {line!r}"


def test_counter_table_detects_only_nonzero_cells(engine):
    """헤더와 열을 맞춰 0이 아닌 칸만 잡는다 — 헤더 추적(header_tokens)에 의존하는 경로."""
    ctx = ContextTracker()
    lines = feed_all(ctx, [
        "Core1#show interfaces counters errors",
        "Port       FCS      Align     Symbol    Runts",
        "---------- -------- --------- --------- ---------",
        "Et1        0        0         0         0",
        "Et2        12       0         3         0",
    ])
    assert ctx.header_tokens == ["Port", "FCS", "Align", "Symbol", "Runts"]
    # feed()는 헤더 줄을 소비하지 않는다(구분선만 소비하며 그때 직전 줄을 헤더로 기억한다).
    # 그래서 반환 목록에는 헤더 줄도 남아 있다 — 인덱스가 아니라 내용으로 골라야 한다.
    zero_row = next(line for line in lines if line.startswith("Et1"))
    nonzero_row = next(line for line in lines if line.startswith("Et2"))
    assert judge(engine, zero_row, ctx) is None, "0으로 채운 행이 검출됐다"

    verdict = judge(engine, nonzero_row, ctx)
    assert verdict is not None, "0이 아닌 칸을 놓쳤다"
    assert verdict["rule_id"] == "counter_nonzero"
    assert verdict["severity"] == "major"
    # 어느 열이 걸렸는지 사유에 남아야 한다 — 없으면 운영자가 되짚을 수 없다.
    assert "FCS=12" in verdict["reason"]
    assert "Symbol=3" in verdict["reason"]


def test_header_tokens_reset_on_blank_line(engine):
    """빈 줄이 지나가면 표가 끝난 것으로 본다 — 다음 표의 행을 이전 헤더로 해석하면 안 된다."""
    ctx = ContextTracker()
    feed_all(ctx, [
        "Core1#show interfaces counters errors",
        "Port       FCS      Align     Symbol    Runts",
        "---------- -------- --------- --------- ---------",
    ])
    assert ctx.header_tokens is not None
    ctx.feed("")
    assert ctx.header_tokens is None


# --------------------------------------------------------------------------- syslog 심각도


@pytest.mark.parametrize("line,severity", [
    pytest.param(
        "Mar  3 15:22:04 Core1 %LINEPROTO-5-UPDOWN: Interface Ethernet2, changed state to down",
        "major", id="lineproto-5-updown",
    ),
    pytest.param(
        "Mar  3 15:22:04 Core1 %SYS-2-CRITICAL_FAIL: something failed",
        "critical", id="sys-2-critical",
    ),
])
def test_syslog_severity_is_applied(engine, line, severity):
    verdict = judge(engine, line)
    assert verdict is not None
    assert verdict["severity"] == severity


# --------------------------------------------------------------------------- 심각도 유틸


def test_severity_helpers():
    assert SEVERITY_ORDER == ["info", "minor", "major", "critical"]
    assert severity_rank("critical") > severity_rank("major") > severity_rank("minor") > severity_rank("info")
    # 알 수 없는 등급은 major로 떨어진다(조용히 info로 깎이면 장애가 묻힌다).
    assert severity_rank("존재하지않는등급") == severity_rank("major")
    assert max_severity("minor", "critical") == "critical"
    assert max_severity("critical", "minor") == "critical"


# --------------------------------------------------------------------------- 규칙 파일 상태 고정


def test_rules_file_shape_is_unchanged():
    """config/log_rules.json 의 규칙 개수를 고정한다.

    규칙이 늘거나 줄면 성능 수치의 전제가 바뀌므로(31개 순차 정규식이 핫패스다) 여기서
    알아채야 한다. 개수를 의도적으로 바꿨다면 이 숫자와 벤치마크 기준선을 함께 갱신한다.
    """
    rules = load_rules()
    assert len(rules["signatures"]) == 33
    assert len(rules["suppressions"]) == 10
    assert len(rules["correlation_rules"]) == 10
    assert len(rules["anomaly_keywords"]) == 17
    assert len(rules["counter_columns"]) == 15
    assert rules["default_severity"] == "major"


def test_two_signature_entries_are_comment_only():
    """signature 33개 중 2개는 pattern 이 없는 주석 전용 엔트리다 — 의도된 문서다.

    배열 순서가 곧 우선순위이므로(먼저 맞는 것이 이긴다) 이 주석들은 순서 제약을 **그 위치에서**
    설명한다(예: index 4 는 "앞의 4개가 뒤쪽 일반 규칙보다 먼저 와야 한다"). 그래서 배열 밖으로
    옮기지 않고 그대로 두며, _compile_patterns() 가 조용히 건너뛴다(OPTIMIZATION_PLAN 1-4).

    개수를 고정하는 이유는 컴파일되는 서명이 31개라는 성능 전제를 지키기 위함이다.
    """
    with open(_rule_file_path(), encoding="utf-8-sig") as stream:
        raw = json.load(stream)
    signatures = raw["signatures"]
    comment_only = [entry for entry in signatures if "pattern" not in entry]
    assert len(comment_only) == 2
    assert all(set(entry) == {"_comment"} for entry in comment_only)
    assert len(signatures) - len(comment_only) == 31


def test_compiled_signature_count_matches_valid_entries(engine):
    assert len(engine.signatures.signatures) == 31
    assert len(engine.keyword_res) == 17
    assert len(engine.suppressor.patterns) == 10


# --------------------------------------------------------------------------- 메모화 안전성 불변식


def test_no_signature_declares_scope(engine):
    """**OPTIMIZATION_PLAN 2-1(메모화)의 안전성 근거.**

    match_signature() 가 ctx 를 읽는 곳은 `if scope and not scope.search(ctx.command)` 뿐이다.
    모든 signature 의 scope 가 None 이면 그 분기가 절대 실행되지 않으므로 match_signature 는
    '줄 문자열만의 순수 함수'이고, 줄 단위 메모화가 안전하다.

    누군가 log_rules.json 에 scope 를 가진 signature 를 추가하면 이 테스트가 실패한다.
    그때 해야 할 일은 이 테스트를 지우는 것이 아니라, 메모 게이트(_memo_safe)가 실제로
    메모를 끄는지 확인하는 것이다.
    """
    scoped = [entry.get("id") for _rx, scope, entry in engine.signatures.signatures if scope is not None]
    assert scoped == [], (
        f"scope 를 가진 signature 가 추가됐다: {scoped}. "
        "match_signature 메모화의 전제가 깨졌으므로 _memo_safe 게이트를 확인하라."
    )


def test_find_keyword_ignores_context(engine):
    """find_keyword 는 애초에 ctx 를 받지 않는다 — 메모화 안전성의 나머지 절반."""
    line = "Interface Ethernet2 is down"
    bare = ContextTracker()
    in_config = ContextTracker()
    feed_all(in_config, ["Core1#show running-config"])
    assert in_config.is_config is True
    assert engine.find_keyword(line) == engine.find_keyword(line)
    # 같은 줄이면 맥락과 무관하게 같은 키워드가 나온다.
    assert engine.find_keyword(line) is not None


# --------------------------------------------------------------------------- 규칙 컴파일 진단


def test_comment_entries_are_skipped_silently(capsys):
    """주석 전용 엔트리는 경고 없이 건너뛴다 — 정상적인 문서이므로 시끄러우면 안 된다."""
    from engine.log_rule_engine import _compile_patterns

    entries = [
        {"_comment": "이 아래 규칙들은 순서가 중요하다"},
        {"id": "ok", "pattern": r"down"},
    ]
    compiled = _compile_patterns(entries)
    assert len(compiled) == 1
    assert compiled[0][2]["id"] == "ok"
    assert capsys.readouterr().out == "", "주석 엔트리에 대해 경고를 냈다"


def test_broken_pattern_is_reported(capsys):
    """정규식 오타는 경고를 남긴다.

    예전에는 `except Exception: continue` 가 삼켜서, 규칙 하나가 아무 신호 없이 사라졌다.
    그 규칙이 잡던 장애를 앱은 '이상 없음'으로 보고한다 — 점검 도구에서 가장 나쁜 실패다.
    """
    from engine.log_rule_engine import _compile_patterns

    compiled = _compile_patterns([{"id": "broken_rule", "pattern": r"([unclosed"}])
    assert compiled == []
    output = capsys.readouterr().out
    assert "broken_rule" in output
    assert "규칙 오류" in output


def test_broken_scope_is_reported_but_rule_kept(capsys):
    """scope 컴파일 실패는 경고하고 규칙은 살린다.

    scope 가 None 이 되면 규칙이 모든 명령에 적용된다 — 좁히려던 규칙이 넓어지므로
    조용히 넘기면 오탐이 늘어난다. 그래도 규칙 자체를 버리는 것보다는 낫다.
    """
    from engine.log_rule_engine import _compile_patterns

    compiled = _compile_patterns([{"id": "bad_scope", "pattern": r"down", "scope": r"([unclosed"}])
    assert len(compiled) == 1
    assert compiled[0][1] is None, "scope 가 None 으로 떨어져야 한다"
    output = capsys.readouterr().out
    assert "bad_scope" in output
    assert "범위 제한 없이" in output


def test_real_rules_file_compiles_without_warnings(capsys):
    """실제 config/log_rules.json 에 정규식 오타가 없는지 — 배포 전 확인용.

    이 테스트가 실패하면 어떤 규칙이 조용히 빠지고 있다는 뜻이다.
    """
    from engine.log_rule_engine import RuleEngine, load_rules

    RuleEngine(load_rules())
    output = capsys.readouterr().out
    assert "규칙 오류" not in output, f"규칙 파일에 컴파일 실패가 있다:\n{output}"
