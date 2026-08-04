"""판정 엔진 줄 단위 메모 — OPTIMIZATION_PLAN 2-1.

메모는 판정 핫패스에 캐시를 넣는 변경이다. 성능 이득보다 **판정 결과가 한 글자도 바뀌지
않는다**는 것이 먼저 보장돼야 하므로, 이 파일의 대부분은 정확성 테스트다.

성능 수치는 여기서 재지 않는다(머신마다 다르다). 그건 tools/bench_log_analysis.py 의 몫이다.
"""
import pytest

from engine.log_analysis import analyze_text
from engine.log_rule_engine import (
    _MEMO_MAX_ENTRIES,
    ContextTracker,
    RuleEngine,
    get_engine,
    load_rules,
)
from tools.synthetic_log import build_device_corpora


@pytest.fixture
def engine():
    """직접 evaluate() 를 부르는 테스트용 독립 엔진 — 다른 테스트가 채운 메모가 섞이지 않는다."""
    return RuleEngine(load_rules())


@pytest.fixture
def singleton():
    """analyze_text() 를 쓰는 테스트용.

    analyze_text() 는 내부에서 get_engine() 싱글턴을 쓴다 — 독립 엔진을 만들어 그 메모를
    들여다보면 영원히 비어 있다. 처음 이 파일을 쓸 때 그 실수를 했고, 더 나쁘게는
    on/off 대조 테스트가 **양쪽 다 메모 ON 으로 돌면서 거짓 통과**했다.
    앞뒤로 상태를 되돌려 다른 테스트에 영향을 주지 않는다.
    """
    instance = get_engine()
    instance.set_memo_enabled(True)
    instance.clear_memo()
    yield instance
    instance.set_memo_enabled(True)
    instance.clear_memo()


def finding_key(findings):
    """findings 를 비교 가능한 형태로 — 판정에 관계된 필드를 전부 포함한다."""
    return [
        (f["rule_id"], f["severity"], f["keyword"], f.get("category_tag"), f["reason"],
         f["line_no"], f.get("repeat"), f.get("last_line_no"),
         bool(f.get("is_correlated")), tuple(f.get("block") or []))
        for f in findings
    ]


# --------------------------------------------------------------------------- 정확성


def test_memo_is_enabled_by_default(engine):
    stats = engine.memo_stats()
    assert stats["signature_memo_enabled"] is True
    assert stats["keyword_memo_enabled"] is True
    assert stats["signature_entries"] == 0
    assert stats["keyword_entries"] == 0


def test_memo_does_not_change_verdicts_line_by_line(engine):
    """같은 줄을 두 번 판정하면 두 번째(캐시 히트)도 같은 결과여야 한다."""
    lines = [
        "Mar  3 15:22:04 Core1 %LINEPROTO-5-UPDOWN: Interface Ethernet2, changed state to down",
        "NTP is disabled.",
        "Number of table drops    : 0",
        "   U - In Use    D - Down",
        "% Invalid input detected at '^' marker.",
        "Et2        12       0         3         0",
        "정상적인 아무 줄",
    ]
    ctx = ContextTracker()
    first = [engine.evaluate(line, ctx) for line in lines]
    second = [engine.evaluate(line, ctx) for line in lines]
    assert first == second


def test_memo_matches_unmemoized_on_whole_run(singleton):
    """장비 20대 회차 전체를 메모 on/off 로 분석해 findings 가 완전히 같은지.

    이 테스트가 2-1 의 핵심 안전망이다. 실패하면 메모가 판정을 바꿨다는 뜻이므로 변경을
    되돌려야 한다.
    """
    corpora = build_device_corpora(devices=20, seed=7)

    singleton.set_memo_enabled(True)
    with_memo = [analyze_text(text) for _device, text in corpora]
    assert singleton.memo_stats()["signature_entries"] > 0, "메모가 채워지지 않았다 — 대조가 무의미하다"

    singleton.set_memo_enabled(False)
    without_memo = [analyze_text(text) for _device, text in corpora]
    assert singleton.memo_stats()["signature_entries"] == 0, "끈 상태인데 메모가 찼다"

    assert len(with_memo) == len(without_memo) == 20
    assert sum(len(r) for r in with_memo) > 0, "findings 가 하나도 없으면 비교가 무의미하다"
    for index, (a, b) in enumerate(zip(with_memo, without_memo)):
        assert finding_key(a) == finding_key(b), f"장비 {index + 1} 의 판정이 달라졌다"


def test_memoized_signature_result_is_not_mutated_by_callers(engine):
    """evaluate() 가 캐시된 dict 를 그대로 돌려주면 호출자가 오염시킬 수 있다.

    현재 구현은 `dict(sig, keyword=...)` 로 복사하므로 안전하다 — 그 성질을 고정한다.
    누군가 복사를 없애면 첫 판정 이후 캐시에 keyword 가 섞여 들어간다.
    """
    line = "NTP is disabled."
    ctx = ContextTracker()
    verdict = engine.evaluate(line, ctx)
    assert verdict is not None
    verdict["severity"] = "오염됨"
    verdict["keyword"] = "오염됨"

    again = engine.evaluate(line, ctx)
    assert again["severity"] != "오염됨", "캐시된 판정이 호출자에 의해 오염됐다"
    assert again["keyword"] != "오염됨"


def test_scoped_signature_disables_signature_memo():
    """scope 를 가진 서명이 있으면 서명 메모는 켜지지 않는다 — 정확성 게이트가 우선한다.

    match_signature 가 ctx 를 읽는 유일한 곳이 scope 검사이므로, scope 가 있으면 줄만의
    순수 함수가 아니게 되고 메모가 틀린 답을 줄 수 있다.
    """
    rules = load_rules()
    rules = dict(rules)
    rules["signatures"] = list(rules["signatures"]) + [
        {"id": "scoped_rule", "pattern": r"scoped-thing", "scope": r"show version", "severity": "major"}
    ]
    scoped_engine = RuleEngine(rules)

    assert scoped_engine.memo_stats()["signature_memo_enabled"] is False
    # 켜라고 해도 켜지지 않아야 한다.
    scoped_engine.set_memo_enabled(True)
    assert scoped_engine.memo_stats()["signature_memo_enabled"] is False
    # 키워드 메모는 ctx 와 무관하므로 계속 쓸 수 있다.
    assert scoped_engine.memo_stats()["keyword_memo_enabled"] is True


def test_scoped_signature_still_judged_correctly():
    """메모가 꺼진 상태에서도 scope 규칙이 명령에 따라 다르게 판정되는지."""
    rules = dict(load_rules())
    rules["signatures"] = [
        {"id": "scoped_rule", "pattern": r"scoped-thing", "scope": r"show version",
         "severity": "major", "title": "범위 한정 규칙"}
    ]
    scoped_engine = RuleEngine(rules)

    in_scope = ContextTracker()
    in_scope.feed("Core1#show version")
    assert scoped_engine.evaluate("scoped-thing appeared", in_scope) is not None

    out_of_scope = ContextTracker()
    out_of_scope.feed("Core1#show interfaces")
    assert scoped_engine.evaluate("scoped-thing appeared", out_of_scope) is None


# --------------------------------------------------------------------------- 메모 수명/범위


def test_memo_is_shared_across_files(singleton):
    """**2-1 의 이득이 성립하는 조건.**

    파일마다 메모를 새로 만들면 이득이 1.05배로 사라진다(한 파일 안에서는 포트 번호가 달라
    줄이 대부분 고유하다). 이득은 장비 사이의 반복에서 나오므로 메모가 파일 경계를 넘어
    살아남아야 한다. 누가 파일마다 엔진을 새로 만들거나 메모를 비우면 이 테스트가 실패한다.
    """
    corpora = build_device_corpora(devices=3, seed=7)

    analyze_text(corpora[0][1])
    after_first = singleton.memo_stats()["signature_entries"]
    assert after_first > 0

    analyze_text(corpora[1][1])
    after_second = singleton.memo_stats()["signature_entries"]

    # 두 번째 장비가 늘린 항목 수는 첫 장비보다 훨씬 적어야 한다 — 대부분 이미 메모에 있다.
    added = after_second - after_first
    assert added < after_first, (
        f"두 번째 장비가 {added}개를 새로 넣었다(첫 장비 {after_first}개) — "
        "장비 사이 중복이 활용되지 않고 있다"
    )


def test_clear_memo_empties_both_caches(singleton):
    analyze_text(build_device_corpora(devices=2, seed=7)[0][1])
    assert singleton.memo_stats()["signature_entries"] > 0
    singleton.clear_memo()
    stats = singleton.memo_stats()
    assert stats["signature_entries"] == 0
    assert stats["keyword_entries"] == 0


def test_set_memo_enabled_false_clears_and_stops_caching(singleton):
    analyze_text(build_device_corpora(devices=2, seed=7)[0][1])
    singleton.set_memo_enabled(False)
    stats = singleton.memo_stats()
    assert stats["signature_entries"] == 0, "끌 때 메모를 비우지 않았다"
    analyze_text(build_device_corpora(devices=2, seed=7)[0][1])
    assert singleton.memo_stats()["signature_entries"] == 0, "꺼진 상태에서 메모가 채워졌다"


def test_reload_creates_fresh_memo(singleton):
    """규칙을 다시 읽으면 메모도 새로 시작해야 한다 — 낡은 규칙의 판정이 남으면 안 된다."""
    analyze_text(build_device_corpora(devices=2, seed=7)[0][1])
    assert singleton.memo_stats()["signature_entries"] > 0

    second = get_engine(reload=True)
    assert second is not singleton
    assert second.memo_stats()["signature_entries"] == 0


# --------------------------------------------------------------------------- 상한


def test_memo_respects_entry_cap(engine, monkeypatch):
    """상한에 닿으면 더 담지 않는다 — 고유 줄이 아주 많은 로그에서 메모리가 계속 늘면 안 된다."""
    monkeypatch.setattr("engine.log_rule_engine._MEMO_MAX_ENTRIES", 50)
    ctx = ContextTracker()
    for index in range(500):
        engine.evaluate(f"고유한 줄 번호 {index} 입니다", ctx)

    stats = engine.memo_stats()
    assert stats["signature_entries"] <= 50, f"서명 메모가 상한을 넘었다: {stats}"
    assert stats["keyword_entries"] <= 50, f"키워드 메모가 상한을 넘었다: {stats}"


def test_verdicts_still_correct_past_the_cap(engine, monkeypatch):
    """상한을 넘긴 뒤에도 판정은 정확해야 한다(캐시 미스는 그냥 계산하면 된다)."""
    monkeypatch.setattr("engine.log_rule_engine._MEMO_MAX_ENTRIES", 3)
    ctx = ContextTracker()
    for index in range(50):
        engine.evaluate(f"채우기 {index}", ctx)

    # 상한을 넘긴 상태에서 새 줄을 판정 — 캐시에 못 들어가지만 결과는 맞아야 한다.
    verdict = engine.evaluate("NTP is disabled.", ctx)
    assert verdict is not None
    assert verdict["rule_id"] == "ntp_not_synced"


def test_cap_is_a_sane_value():
    """상한이 의미 있는 크기인지 — 너무 작으면 이득이 없고, 없으면 메모리가 새어 나간다."""
    assert 1_000 <= _MEMO_MAX_ENTRIES <= 1_000_000
