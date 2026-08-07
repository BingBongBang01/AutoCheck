"""정기점검 보고서 판정 — '점검 불가/대상 아님'을 '비정상'과 갈라내는 규칙을 고정한다.

배경: vEOS-lab 같은 가상 장비로 점검하면 아래가 전부 '확인필요'로 보고됐다. 같은 항목이
실기 장비(Studio Vird)에서는 정상 판독되므로, 이건 장비 상태가 아니라 판정 로직의 문제였다.

    Power/Fan/Temperature   "There seem to be no power supplies"  가상장비라 PSU 자체가 없음
    Module                  "% Unavailable command (not supported on this hardware platform)"
    STP                     "% Invalid input"                     명령이 거부돼 읽지도 못함
    BGP/EVPN                "% BGP inactive"                      기능 미구성
    Log                     "%SYS-5-CONFIG_I"                     콘솔 로그인/설정모드 진입

여기서 못박는 계약은 두 가지다.
  1) 명령 미지원/리소스 없음/기능 미구성은 '비정상'이 아니라 미수집 또는 해당없음이다.
  2) syslog 심각도 5 이하(minor/info)는 특이 로그로 세지 않는다 — 참고로만 남는다.
"""
import pytest

from report import inspection_excel as ix
from report.inspection_status import (
    STATUS_NA, STATUS_OK, STATUS_SKIP, STATUS_WARN,
)


def evaluate(text, evaluator="keyword_ok", **item):
    return ix.EVALUATORS[evaluator](text, item)


# --------------------------------------------------------------------------- 해당없음 / 미수집

NOT_APPLICABLE_OUTPUTS = [
    pytest.param("There seem to be no power supplies", id="power-no-psu"),
    pytest.param("There seem to be no fans", id="fan-none"),
    pytest.param("There seem to be no sensors", id="temperature-none"),
    pytest.param("% Unavailable command (not supported on this hardware platform)",
                 id="module-unsupported-platform"),
    pytest.param("% BGP inactive", id="bgp-not-configured"),
]


@pytest.mark.parametrize("text", NOT_APPLICABLE_OUTPUTS)
def test_unsupported_or_unconfigured_is_not_a_failure(text):
    result = evaluate(text)
    assert result["status"] == STATUS_SKIP, f"'해당없음'이어야 하는데 {result['status']}: {text!r}"


def test_rejected_command_is_not_collected_rather_than_abnormal():
    """명령이 거부되면 STP 상태를 읽지도 못한 것이다 — 그 상태로 '비정상' 판정을 내면
    Finding 자체가 무효다(무엇이 문제인지 근거가 없다)."""
    result = evaluate("% Invalid input detected at '^' marker.")
    assert result["status"] == STATUS_NA


def test_missing_section_is_not_collected():
    template = {"check_items": [{"no": 1, "group": "L2", "name": "STP 상태",
                                 "commands": ["show spanning-tree"], "criteria": "",
                                 "evaluator": "keyword_ok"}]}
    rows = ix.evaluate_device({"show version": "Arista vEOS"}, template)
    assert rows[0]["status"] == STATUS_NA


# --------------------------------------------------------------------------- 실제 장애는 그대로

def test_real_fault_still_fails():
    """오탐을 줄이려다 미탐을 만들면 안 된다 — 물리 포트 다운은 여전히 확인필요다."""
    result = evaluate("Ethernet3 is down, line protocol is down")
    assert result["status"] == STATUS_WARN


def test_fault_wins_over_unsupported_in_same_section():
    """같은 출력에 명령 거부와 실제 장애가 섞여 있으면 장애가 이긴다 —
    '미수집'으로 접어버리면 봐야 할 것이 사라진다."""
    text = "% Invalid input detected at '^' marker.\nEthernet3 is down, line protocol is down"
    assert evaluate(text)["status"] == STATUS_WARN


# --------------------------------------------------------------------------- Log 심각도 필터

def test_config_change_logs_are_not_counted_as_anomalies():
    """%SYS-5-CONFIG_I/E 는 정상 운영 로그다. 예전에는 장비당 30건씩 '특이 로그'로 셌다."""
    text = "\n".join(
        f"Aug  5 09:5{i}:18 Agg1 %SYS-5-CONFIG_I: Configured from console by admin on console"
        for i in range(5))
    result = ix.EVALUATORS["log_scan"](text, {})
    assert result["status"] == STATUS_OK
    assert "특이 로그" not in str(result["value"])
    assert "참고" in str(result["value"]), "참고 건수까지 사라지면 되짚을 수 없다"


def test_real_log_event_still_counted():
    text = ("Aug  5 09:50:18 Agg1 %SYS-5-CONFIG_I: Configured from console\n"
            "Aug  5 09:51:02 Agg1 %LINEPROTO-5-UPDOWN: Interface Ethernet3, "
            "changed state to down")
    result = ix.EVALUATORS["log_scan"](text, {})
    assert result["status"] == STATUS_WARN
    assert "특이 로그 1건" in result["value"]


# --------------------------------------------------------------------------- SVI lowerlayerdown

def test_svi_lowerlayerdown_is_a_note_not_a_failure():
    """SVI 가 lowerlayerdown 인 것은 그 VLAN 에 up 인 멤버 포트가 없다는 하위 계층의 결과다.
    랩/미구축 구간에서는 정상이므로 바로 비정상으로 올리면 매 회차 오탐이 된다."""
    text = "Vlan10          unassigned      down       lowerlayerdown"
    result = evaluate(text)
    assert result["status"] == STATUS_OK
    assert "참고" in str(result["value"])


def test_svi_hard_down_is_still_a_failure():
    assert evaluate("Vlan20 is down, line protocol is down")["status"] == STATUS_WARN


# --------------------------------------------------------------------------- Free Memory

def test_free_memory_is_reported_as_percent():
    """PDF 의 '(Free / Total) * 100' 칸에 0.126 이 찍히던 문제 — 값은 퍼센트 문자열,
    엑셀 셀용 수치(number)는 0~1 비율로 따로 낸다."""
    text = "Total memory: 8129252 kB\nFree memory: 1024220 kB"
    result = ix.EVALUATORS["memory_free"](text, {"warn_at": 0.3})
    assert result["value"] == "12.6%"
    assert result["number"] == pytest.approx(0.126, abs=0.001)
    assert result["number_format"] == "0.0%"
    assert result["status"] == STATUS_WARN  # 30% 미만


def test_free_memory_threshold_accepts_percent_style_config():
    """템플릿에 warn_at 을 30(퍼센트)으로 적어도 0.3 과 같게 동작해야 한다 —
    안 그러면 판정이 조용히 '항상 정상'이 된다."""
    text = "Total memory: 1000 kB\nFree memory: 500 kB"
    assert ix.EVALUATORS["memory_free"](text, {"warn_at": 30})["status"] == STATUS_OK
    assert ix.EVALUATORS["memory_free"](text, {"warn_at": 0.3})["status"] == STATUS_OK
    assert ix.EVALUATORS["memory_free"](text, {"warn_at": 60})["status"] == STATUS_WARN


# --------------------------------------------------------------------------- PDF 요약표 집계

def test_pdf_summary_counts_separate_not_judged():
    """요약표의 세 숫자(점검항목/정상/비정상) 뒤에 '미수집' 열이 붙었다.

    예전 계산은 passed = total - fail 이라, 가상 장비처럼 절반이 미수집인 장비도 요약표에서는
    '14 12 2'처럼 대부분 정상 점검된 것으로 보였다."""
    from report import inspection_pdf as ip

    device = {"items": [
        {"name": "Power 점검", "status": STATUS_SKIP, "value": "해당없음 (…)"},
        {"name": "FAN 상태 확인", "status": STATUS_SKIP, "value": "해당없음 (…)"},
        {"name": "Temperature 상태 확인", "status": STATUS_NA, "value": "미수집 (…)"},
        {"name": "Interface 상태", "status": STATUS_WARN, "value": "확인필요 (2건)"},
        {"name": "CPU 사용률", "status": STATUS_NA, "value": "미수집 (…)"},
        {"name": "Free Memory", "status": STATUS_OK, "value": "46.2%"},
    ]}
    passed, fail, na = ip._device_counts(device, 14)
    assert (fail, na) == (1, 4)
    assert passed == 14 - 1 - 4
    assert passed + fail + na == 14, "세 숫자의 합이 점검항목 수와 맞아야 한다"


def test_pdf_shows_measured_value_but_short_status_when_uncollected():
    """실측 항목은 값을 그대로 쓰되, 값을 못 뽑았으면 사유 문장 대신 상태만 적는다
    (좁은 결과 칸을 넘치지 않게, 그리고 집계가 '판정하지 못함'으로 알아보게)."""
    from report import inspection_pdf as ip

    rows = ip._switch_rows({"items": [
        {"name": "Free Memory", "status": STATUS_OK, "value": "46.2%"},
        {"name": "CPU 사용률", "status": STATUS_NA, "value": "미수집 (해당 명령 출력 없음)"},
        {"name": "Power 점검", "status": STATUS_SKIP, "value": "해당없음 (이 하드웨어 플랫폼이 …)"},
    ]})
    assert rows["Free Memory"] == "46.2%"
    assert rows["CPU 사용률"] == STATUS_NA
    assert rows["Power"] == STATUS_SKIP
