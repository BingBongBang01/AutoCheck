"""프로파일 단위 '가상환경' 옵션 — 무엇이 달라지는지를 못박는다.

배경: vEOS-lab/EVE-NG 같은 실습·검증망에는 PSU/FAN/온도센서/모듈/트랜시버가 물리적으로
없다. 예전에는 이걸 로그 출력 문구("There seem to be no power supplies" 등)로만 사후
추론했기 때문에, 문구가 벤더/버전마다 다르거나 명령이 아무 것도 출력하지 않으면 그 항목이
'확인필요'/'미수집'으로 남아 매 회차 같은 오탐을 손으로 걸러야 했다.

여기서 고정하는 계약:
  1) 프로파일에 is_virtual 이 켜져 있으면 템플릿의 virtual_na 항목은 로그와 무관하게 '해당없음'.
  2) 꺼져 있으면(기본값) 판정 로직은 예전과 완전히 동일하다 — 실기 점검이 조용히 바뀌면 안 된다.
  3) AI 원본로그 분석 프롬프트는 가상/물리 두 벌이며 이 옵션이 고른다. 물리 프롬프트는
     하드웨어 고장을 무시하라고 지시하지 않는다(실제 PSU 고장이 묻히면 안 되므로).
"""
from ai_analysis import raw_log_analyzer
from engine.profile_manager import ProfileManager
from report import inspection_excel as ix
from report.inspection_status import STATUS_OK, STATUS_SKIP, STATUS_WARN

POWER_ITEM = {"no": 1, "group": "H/W 구동 상태", "name": "Power 점검",
              "commands": ["show environment power"], "criteria": "Power Supply Status OK 확인",
              "evaluator": "keyword_ok", "virtual_na": True}
STP_ITEM = {"no": 2, "group": "L2/인터페이스", "name": "STP 상태",
            "commands": ["show spanning-tree"], "criteria": "", "evaluator": "keyword_ok"}
TEMPLATE = {"check_items": [POWER_ITEM, STP_ITEM]}

SECTIONS = {
    # 실제 vEOS-lab 이 뱉는 출력이 아니라, 규칙에 걸리지 않는 임의 문구 — '문구로 추론'하는
    # 경로를 타지 않고도 해당없음이 되는지 보려고 일부러 이렇게 둔다.
    "show environment power": "Power supply information is not available on this device",
    "show spanning-tree": "  Port 1 (Ethernet1) of VL1 is forwarding",
}


def test_virtual_profile_marks_hardware_item_not_applicable():
    rows = ix.evaluate_device(SECTIONS, TEMPLATE, is_virtual=True)
    power = rows[0]
    assert power["status"] == STATUS_SKIP
    assert "가상환경" in power["value"]
    # 하드웨어가 아닌 항목은 가상환경에서도 그대로 판정한다.
    assert rows[1]["status"] == STATUS_OK


def test_physical_profile_keeps_previous_judgement():
    """옵션이 꺼져 있으면 virtual_na 표시가 있어도 로그를 그대로 판정한다 —
    가상환경용 꼬리표('가상환경 — 해당 하드웨어 없음')가 실기 보고서에 새지 않아야 한다."""
    rows = ix.evaluate_device(SECTIONS, TEMPLATE)
    assert "가상환경" not in (rows[0]["value"] or "")


def test_real_hardware_fault_is_still_reported_when_not_virtual():
    """옵션이 꺼져 있으면 하드웨어 항목에서 규칙에 걸린 장애가 그대로 '확인필요'로 올라온다."""
    sections = dict(SECTIONS)
    sections["show environment power"] = "Aug  5 09:10:11 Core1 rebooted due to power loss"
    rows = ix.evaluate_device(sections, TEMPLATE)
    assert rows[0]["status"] == STATUS_WARN
    # 같은 로그라도 가상환경 프로파일이면 그 항목 자체가 점검 대상이 아니다.
    assert ix.evaluate_device(sections, TEMPLATE, is_virtual=True)[0]["status"] == STATUS_SKIP


def test_virtual_flag_roundtrip(tmp_path):
    manager = ProfileManager(data_root=tmp_path)
    manager.create_profile("고객사A", "2026-08", is_virtual=True)
    assert manager.is_virtual("고객사A", "2026-08") is True

    manager.set_virtual("고객사A", "2026-08", False)
    assert manager.is_virtual("고객사A", "2026-08") is False


def test_default_profile_is_not_virtual(tmp_path):
    """기본값은 실제 장비 — 옵션을 모르는 기존 프로파일이 갑자기 하드웨어 점검을 건너뛰면 안 된다."""
    manager = ProfileManager(data_root=tmp_path)
    manager.create_profile("고객사A", "2026-08")
    assert manager.is_virtual("고객사A", "2026-08") is False
    # 폴더만 있고 메타가 없는(레거시) 프로파일도 조회만으로 예외를 던지지 않는다.
    assert manager.is_virtual("없는고객사", "없는프로파일") is False


def test_ai_prompt_switches_with_the_option():
    virtual = raw_log_analyzer.prompt_prefix(True)
    physical = raw_log_analyzer.prompt_prefix(False)
    assert "IGNORE EXPECTED VIRTUAL LIMITATIONS" in virtual
    assert "IGNORE EXPECTED VIRTUAL LIMITATIONS" not in physical
    assert "REPORT HARDWARE FAULTS" in physical
