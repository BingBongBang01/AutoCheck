"""실출력 골든 픽스처 — IMPROVEMENT_PLAN 4-10(만들기: python -m tools.promote_fixture promote …).

tests/fixtures/golden/<이름>/ 마다 지금 파서가 session.log 에서 읽는 것(플랫폼·명령·장비 정보·보고서 점검 항목 판정·
구성도 사실·로그 규칙)이 expected.json 과 같아야 하고, session.log 에 가짜가 아닌 주소·MAC·FQDN 이나 비밀이 없어야 한다.
다르면 둘 중 하나다 — 파서를 일부러 바꿨다(`python -m tools.promote_fixture refresh` 로 다시 만들고 그 diff 를 커밋에
담는다), 아니면 실출력에서 회귀가 생겼다. 픽스처는 실제 장비 출력만 쓴다(IMPROVEMENT_PLAN '지킬 것' — 상상으로 만든
벤더 출력에 맞춘 파서가 실출력을 한 줄도 읽지 못한 일이 있었다).
"""
from tools import promote_fixture as pf


def test_every_golden_fixture_still_parses_the_same():
    changed = {path.name: pf.compare(path) for path in pf.fixtures()}
    assert {name: keys for name, keys in changed.items() if keys} == {}, \
        "파서 결과가 달라진 픽스처(일부러 바꿨다면 python -m tools.promote_fixture refresh)"


def test_golden_fixtures_hold_no_identifiers():
    leaks = {}
    for path in pf.fixtures():
        text, meta = pf.read_fixture(path)
        found = pf.residual_leaks(text, keep=meta.get("kept") or ())
        if found:
            leaks[path.name] = [(leak["what"], leak["lines"][:5]) for leak in found]
    assert leaks == {}


def test_fixture_folders_are_complete():
    assert (pf.GOLDEN_DIR / "README.md").is_file()
    for path in pf.fixtures():
        assert pf._FIXTURE_NAME_RE.match(path.name), path.name
        assert (path / "expected.json").is_file() and (path / "fixture.json").is_file(), path.name
