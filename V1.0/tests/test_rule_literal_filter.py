"""규칙 정규식 앞에서 거르기 — 규칙마다 '맞으려면 줄에 반드시 있어야 하는 글자열'로 줄을 먼저 거른다.

대소문자를 가리지 않는 정규식은 파이썬에서 첫 글자로 건너뛰는 빠른 검색을 못 써서, 처음 보는 줄마다 규칙 수십 개를
줄의 모든 자리에서 맞춰 봤다 — 실장비 25대의 show logging 6만 5천 줄을 판정하는 보고서 미리보기 첫 판정이 13초였다
(거른 뒤 3초). 거르기는 필요조건일 뿐이라 판정이 한 글자도 바뀌면 안 된다 — 이 파일은 그 성질을 지킨다:
건전성(정규식이 맞는 줄은 절대 거르지 않는다)과 동일성(켜고 끈 판정이 같다).
"""
import pytest

from engine.log_analysis import analyze_text
from engine.log_rule_engine import ContextTracker, LiteralFilter, RuleEngine, get_engine, load_rules, required_literals
from tools.synthetic_log import build_device_corpora


@pytest.mark.parametrize("pattern, expected", [
    (r"\berrdisable\b", {"errdisable"}),
    (r"CRC Error", {"crc error"}),                            # 대소문자 무시 — 소문자로 찾는다
    (r"is (?:administratively )?down", {"down"}),             # 없어도 되는 부분은 쓰지 않는다
    (r"(?:abc)+x", {"abc"}),                                  # 한 번 이상 나오는 반복은 필요조건이다
    (r"(?:timeout|unreachable)", {"timeout", "unreachable"}),  # 갈래마다 하나씩 — 그중 하나는 있다
    (r"오류abc", {"abc"}),                                     # 영문 밖 글자는 글자열을 끊는다
])
def test_required_literals(pattern, expected):
    assert required_literals(pattern) == expected


@pytest.mark.parametrize("pattern", [
    r"foo|\d+",          # 한 갈래가 글자열 없이도 맞는다
    r"(?:foo)?ba",       # 없어도 되는 것뿐, 남은 글자열은 너무 짧다
    r"[ab]cd",           # 글자 모음은 글자열이 아니다
    r"^\s*\S+:\s*$",
    r"(",                # 깨진 정규식 — 거르지 않는다(규칙 컴파일이 따로 알린다)
])
def test_unknown_patterns_are_not_filtered(pattern):
    assert required_literals(pattern) is None


def _corpus_lines():
    lines = []
    for _device, text in build_device_corpora(devices=12, seed=11):
        lines += text.splitlines()
    return list(dict.fromkeys(lines)) + [
        "LinK down",                       # 켈빈 기호 K — 대소문자 무시 정규식은 k 로 맞춘다
        "%ETH-4-ERRDISABLE: errdisable 감지",   # 영문 밖 글자가 섞인 줄
        "",
    ]


def test_a_line_a_rule_matches_is_never_filtered_out():
    engine = RuleEngine(load_rules())
    rules = [(rx, literals) for (rx, _scope, _entry), literals in zip(engine.signatures.signatures,
                                                                      engine.signatures.filter.sets)]
    rules += [(rx, literals) for (rx, _scope, _entry), literals in zip(engine.suppressor.patterns,
                                                                       engine.suppressor.filter.sets)]
    rules += list(zip(engine.keyword_res.values(), engine.keyword_filter.sets))
    assert sum(1 for _rx, literals in rules if literals) >= 30, "거를 규칙이 거의 없다 — 대조가 무의미하다"
    probe = LiteralFilter(literals for _rx, literals in rules)
    for line in _corpus_lines():
        present = probe.present(line)
        for rx, literals in rules:
            if rx.search(line):
                assert not LiteralFilter.skips(literals, present), (rx.pattern, line)


def test_lines_with_non_ascii_letters_are_not_filtered():
    engine = RuleEngine(dict(load_rules(), signatures=[{"id": "link_down", "pattern": "link down"}]))
    assert engine.signatures.filter.present("LinK down") is None
    assert engine.signatures.match_signature("LinK down", ContextTracker())["rule_id"] == "link_down"


@pytest.fixture
def singleton():
    instance = get_engine()
    yield instance
    instance.set_literal_filter_enabled(True)


def test_filtering_does_not_change_any_finding(singleton):
    corpora = build_device_corpora(devices=20, seed=7)
    singleton.set_literal_filter_enabled(True)
    filtered = [analyze_text(text) for _device, text in corpora]
    singleton.set_literal_filter_enabled(False)
    unfiltered = [analyze_text(text) for _device, text in corpora]
    assert sum(len(found) for found in filtered) > 0, "findings 가 하나도 없으면 대조가 무의미하다"
    assert filtered == unfiltered
