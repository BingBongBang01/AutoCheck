"""규칙 파일 운영 — IMPROVEMENT_PLAN 2-12.

  1. 규칙을 고쳐도 옛 판정이 나왔다 — 판정 캐시 키가 (로그 경로, 수정시각, 크기)뿐이었다.
  2. JSON 오류가 나면 조용히 기본값을 썼다 — 쉼표 하나로 서명 전부가 사라져도 아무 표시가 없었다.
  3. 실시간 규칙 스트림은 처음 받은 엔진을 붙들어 재로드를 반영하지 않았다.
"""
import json

import pytest

from engine import log_cache
from engine import log_rule_engine as lre
from engine.realtime_rule_stream import RealtimeRuleStream

RULES = {
    "anomaly_keywords": [],
    "signatures": [{"id": "custom_alarm", "pattern": "CUSTOM-ALARM-\\d", "severity": "critical",
                    "category": "general", "title": "사용자 정의 경보"}],
}


@pytest.fixture
def rule_file(tmp_path, monkeypatch):
    path = tmp_path / "log_rules.json"
    path.write_text(json.dumps(RULES), encoding="utf-8")
    monkeypatch.setattr(lre, "_rule_file_path", lambda: str(path))
    monkeypatch.setattr(lre, "_engine", None)
    log_cache.clear()
    yield path
    log_cache.clear()
    lre.get_engine(reload=True)


def bump(path, rules):
    """규칙 파일을 다시 쓴다(수정시각이 같은 해상도 안이어도 크기가 달라지게 내용을 바꾼다)."""
    path.write_text(json.dumps(rules), encoding="utf-8")


def test_changing_the_rule_file_changes_cached_verdicts(rule_file, monkeypatch):
    log = rule_file.parent / "Core1.txt"
    log.write_text("Core1#show logging\nCUSTOM-ALARM-1 fan failure\nOTHER-ALARM-2 psu\n", encoding="utf-8")
    first = log_cache.cached_findings(str(log))
    assert any("CUSTOM-ALARM-1" in json.dumps(f, ensure_ascii=False) for f in first)
    assert not any("OTHER-ALARM-2" in json.dumps(f, ensure_ascii=False) for f in first)

    bump(rule_file, {**RULES, "signatures": RULES["signatures"] + [
        {"id": "other_alarm", "pattern": "OTHER-ALARM-\\d", "severity": "major", "category": "general",
         "title": "다른 경보"}]})
    second = log_cache.cached_findings(str(log))                   # 로그 파일은 그대로인데도
    assert any("OTHER-ALARM-2" in json.dumps(f, ensure_ascii=False) for f in second)


def test_a_broken_rule_file_is_reported(rule_file, capsys):
    rule_file.write_text('{"signatures": [ {"id": "x", }', encoding="utf-8")
    lre.get_engine(reload=True)
    status = lre.rules_status()
    assert status["ok"] is False and "JSONDecodeError" in status["error"]
    assert "[규칙 오류]" in capsys.readouterr().out


def test_realtime_stream_follows_rule_changes(rule_file):
    stream = RealtimeRuleStream()
    assert stream.analyze("Core1", "NEW-ALARM-7 detected\n") == []
    bump(rule_file, {**RULES, "signatures": [
        {"id": "new_alarm", "pattern": "NEW-ALARM-\\d", "severity": "critical", "category": "general",
         "title": "새 경보"}]})
    alerts = stream.analyze("Core1", "NEW-ALARM-8 detected\n")
    assert [a["rule_id"] for a in alerts] == ["new_alarm"]


def test_watch_start_warning_mentions_a_broken_rule_file(rule_file):
    from engine.realtime_watch_service import rules_warning
    assert rules_warning() is None
    rule_file.write_text("[1, 2", encoding="utf-8")
    assert "log_rules.json" in rules_warning()
