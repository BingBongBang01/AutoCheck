"""규칙 실험실 — IMPROVEMENT_PLAN 4-8c.

  1. 붙여넣은 줄을 실시간 감시와 같은 판정기에 넣고 층마다 결과를 보인다 — 맥락(명령·설정 원문·도움말),
     구조 억제, 서명·카운터·키워드, 키워드 억제, diff 엔진의 경고와 복구, 실시간 목록에 올라가는지(아니면 왜 아닌지).
  2. 최종 판정은 RuleEngine.evaluate 그대로다 — 독립으로 돌린 판정과 줄마다 같다.
  3. 장비를 고르면 감시가 들고 있는 Baseline 과 대조한다. 감시 중인 엔진은 건드리지 않는다.
  4. 린트 — 실제 규칙 파일은 깨끗하고, 판정을 조용히 바꾸는 실수(정규식 오류·빈 줄에도 맞는 정규식·빈 정상 문구·
     중복 id·모르는 심각도·성립할 수 없는 상관 규칙·이름 오타)를 찾는다.
"""
import json

import pytest

from engine import rule_lab
from engine.baseline_store import BaselineStore
from engine.log_rule_engine import ContextTracker, get_engine

SYSLOG_DOWN = "%LINEPROTO-5-UPDOWN: Line protocol on Interface Ethernet1, changed state to down"

SAMPLE = "\n".join([
    "Core1#show mlag",
    "state                              :             Inactive",
    "Core1#show running-config",
    " no service interface inactive port-id allocation disabled",
    "Core1#no ?",
    "  inactive            Configure actions taken when MLAG state is inactive",
    "Core1#show hardware counter drop",
    "Number of table drops    : 0",
    "% Invalid input",
    "Core1#conf t",
    "Core1(config)#interface Ethernet1",
    "Core1(config-if-Et1)#shutdown",
    SYSLOG_DOWN,
    "Core1(config-if-Et1)#no shutdown",
    "Core1#show logging",
    "Ethernet5 is down, line protocol is down (notconnect)",
])


@pytest.fixture(scope="module")
def lab():
    return {row["no"]: row for row in rule_lab.run_lab(SAMPLE, device="Core1")["lines"]}


def test_context_and_keyword_layers(lab):
    assert lab[1]["kind"] == "command" and lab[1]["rule"]["stage"] == "boundary"
    assert lab[2]["rule"]["stage"] == "keyword" and lab[2]["rule"]["verdict"]["rule_id"] == "keyword_inactive"
    assert lab[2]["context"]["command"] == "show mlag"
    assert lab[2]["realtime"] == {"raised": True, "severity": "MAJOR", "note": "실시간 감시 목록에 올라간다"}


def test_running_config_and_help_are_not_state(lab):
    assert lab[4]["context"]["config"] is True
    assert lab[4]["rule"]["stage"] == "keyword_suppressed" and "설정 원문" in lab[4]["rule"]["keyword_suppressed"]
    assert lab[4]["realtime"]["raised"] is False
    assert lab[6]["rule"]["stage"] == "help" and lab[6]["rule"]["verdict"] is None


def test_structural_suppression_and_low_severity_signature(lab):
    assert lab[8]["rule"]["stage"] == "suppressed" and lab[8]["rule"]["suppressed"]
    assert lab[9]["rule"]["stage"] == "signature" and lab[9]["rule"]["verdict"]["rule_id"] == "command_rejected"
    assert "major 이상만" in lab[9]["realtime"]["note"]


def test_diff_engine_column_and_recovery(lab):
    assert lab[12]["kind"] == "command" and lab[12]["mode"] == "config-if-Et1"
    assert [d["type"] for d in lab[12]["diff"]] == ["INTERFACE_SHUTDOWN"]
    assert [d["type"] for d in lab[13]["diff"]] == ["LINK_DOWN"]
    # 감시 서비스는 diff 엔진이 판정한 줄의 규칙 경고를 버린다 — 실험실도 그렇게 말한다.
    assert lab[13]["rule"]["verdict"] and lab[13]["realtime"]["raised"] is False
    assert "diff 엔진이 같은 줄" in lab[13]["realtime"]["note"]
    assert lab[14]["diff"] == [] and lab[14]["resolved"] == 2


def test_show_logging_output_is_a_past_record(lab):
    assert lab[16]["rule"]["verdict"] and lab[16]["realtime"]["raised"] is False
    assert "과거 기록" in lab[16]["realtime"]["note"]


def test_final_verdict_is_exactly_rule_engine_evaluate(lab):
    engine, ctx = get_engine(), ContextTracker()
    for no, line in enumerate(SAMPLE.splitlines(), 1):
        expected = None if ctx.feed(line) else engine.evaluate(line, ctx)
        assert lab[no]["rule"]["verdict"] == expected, (no, line)


def test_summary_and_truncation():
    result = rule_lab.run_lab("\n".join(f"Et{i} is up" for i in range(rule_lab.MAX_LINES + 5)))
    assert result["device"] == rule_lab.LAB_DEVICE and result["baseline"] is False
    assert result["summary"]["lines"] == rule_lab.MAX_LINES and result["truncated"] == 5


# --------------------------------------------------------------------------- API

class FakeStore(BaselineStore):
    def __init__(self):
        super().__init__()
        self._devices = {"Core1": {"vlans": {"100"}, "interfaces": {"Ethernet1"}, "routes": set(),
                                   "bgp_neighbors": set()}}


@pytest.fixture
def api():
    from api.realtime_watch_api import RealtimeWatchApiMixin
    from api.rule_lab_api import RuleLabApiMixin

    class Stub(RealtimeWatchApiMixin, RuleLabApiMixin):
        pass

    stub = Stub()
    store = FakeStore()
    stub._realtime().baseline_store = lambda: store
    return stub


def test_picking_a_device_judges_against_its_baseline(api):
    text = "Core1(config)#no vlan 100"
    with_baseline = api.run_rule_lab(text, "Core1")
    without = api.run_rule_lab(text, "")
    assert with_baseline["ok"] and with_baseline["baseline"] is True and without["baseline"] is False
    [judged] = with_baseline["lines"][0]["diff"]
    assert judged["type"] == "CONFIG_REMOVED" and "Baseline" in judged["message"]
    assert [d["message"] for d in without["lines"][0]["diff"]] != [judged["message"]]


def test_the_lab_leaves_the_watch_alone(api):
    service = api._realtime()
    assert service.diff_engine is None and service.rule_stream is None
    api.run_rule_lab("Core1(config-if-Et1)#shutdown", "Core1")
    assert service.diff_engine is None and service.rule_stream is None


def test_api_rejects_empty_and_huge_text(api):
    assert api.run_rule_lab("   \n", "")["error"]
    assert "너무 깁니다" in api.run_rule_lab("x" * 200_001, "")["error"]


# --------------------------------------------------------------------------- 린트

def test_the_shipped_rules_file_is_clean():
    lint = rule_lab.lint_rules()
    assert lint["readable"] is True and lint["clean"] is True
    assert [f for f in lint["findings"] if f["level"] != "info"] == []
    assert lint["counts"]["signatures"] > 20 and 0 < lint["realtime_signatures"] <= lint["counts"]["signatures"]


def test_lint_finds_mistakes_that_silently_change_judgement(tmp_path):
    rules = {
        "anomaly_keywords": ["DOWN", "down"],
        "keyword_severity": {"DOWN": "major", "FLAPPING": "high"},
        "benign_phrases": ["idle-timeout", " "],
        "signatures": [
            {"_comment": "순서 설명 — 경고 대상이 아니다"},
            {"id": "dup", "pattern": "a", "severity": "major", "category": "lag"},
            {"id": "dup", "pattern": "b", "severity": "high"},
            {"id": "broken", "pattern": "(unclosed", "severity": "major"},
            {"id": "everything", "pattern": "x*", "severity": "info"},
            {"id": "scoped", "pattern": "y", "scope": "[bad", "severity": "minor"},
            {"id": "nopattern", "severity": "major"},
        ],
        "suppressions": [{"pattern": "z", "reason": "id 없음"}],
        "correlation_rules": [
            {"id": "ok", "all_of": ["dup", "lag"], "severity": "critical"},
            {"id": "ghost", "all_of": ["no_such_rule"], "severity": "critical", "scope": "device"},
            {"id": "empty", "severity": "critical"},
        ],
        "signature": [],
    }
    path = tmp_path / "log_rules.json"
    path.write_text(json.dumps(rules), encoding="utf-8")
    lint = rule_lab.lint_rules(str(path))
    got = {(f["level"], f["where"].split()[0], f["message"].split(" — ")[0].split("(")[0].strip())
           for f in lint["findings"]}
    expected_fragments = [
        ("warn", "signature", "엔진이 읽지 않는 항목이다"),
        ("error", "signatures[2]", "id 가 겹친다"),
        ("warn", "signatures[2]", "모르는 심각도 'high'"),
        ("error", "signatures[3]", "pattern 이 컴파일되지 않아 이 규칙이 빠진다"),
        ("error", "signatures[4]", "pattern 이 빈 문자열에도 맞는다"),
        ("error", "signatures[5]", "scope 가 컴파일되지 않아 모든 명령에 적용된다"),
        ("error", "signatures[6]", "pattern 이 없어 건너뛴다"),
        ("warn", "suppressions[0]", "id 가 없다"),
        ("info", "anomaly_keywords", "'DOWN' 가 두 번 이상 있다"),
        ("warn", "keyword_severity.FLAPPING", "anomaly_keywords 에 없는 키워드라 쓰이지 않는다"),
        ("warn", "keyword_severity.FLAPPING", "모르는 심각도 'high'"),
        ("error", "benign_phrases[1]", "빈 문구는 모든 줄에 들어 있다"),
        ("warn", "correlation_rules[1]", "'no_such_rule' 을"),
        ("warn", "correlation_rules[1]", "scope 는 interface 또는 global 이다"),
        ("error", "correlation_rules[2]", "all_of·any_of 가 비어 있어 성립하지 않는다"),
    ]
    for level, where, fragment in expected_fragments:
        assert any(g[0] == level and g[1] == where and g[2].startswith(fragment[:12]) for g in got), \
            (level, where, fragment, sorted(got))
    assert not any(f["where"].startswith("signatures[0]") for f in lint["findings"])   # 주석 항목
    assert not any("correlation_rules[0]" in f["where"] for f in lint["findings"])      # 서명 id·분류는 성립
    assert lint["clean"] is False and lint["counts"]["signatures"] == 5


def test_lint_reports_an_unreadable_file(tmp_path):
    path = tmp_path / "log_rules.json"
    path.write_text("{ 쉼표가 빠진 파일", encoding="utf-8")
    lint = rule_lab.lint_rules(str(path))
    assert lint["readable"] is False and lint["clean"] is False
    assert lint["findings"][0]["level"] == "error" and "기본 키워드" in lint["findings"][0]["message"]
