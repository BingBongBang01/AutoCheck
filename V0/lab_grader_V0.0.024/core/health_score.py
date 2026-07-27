"""
Health Score — Finding severity에 따라 100점에서 감점하는 방식.
문서(2026.07.22 인턴 22일차 설계안)의 요구사항: "Rule마다 감점" + Device/Rack/Site/Project 계층 집계.

지금은 Rack 개념(물리 랙 위치)이 Device Inventory에 없어서 Device -> Site -> Project
3단계만 구현. Rack은 Device Inventory에 zone/site 필드가 이미 있어 zone을 Rack 대용으로
쓸 수 있게 설계해둠(향후 실제 Rack 필드 추가 시 이 함수 시그니처만 확장하면 됨).

감점 기준은 severity 기반 기본값을 두되, check_id별로 override 가능하게 설계
(문서 예시: CPU -5, CRC -10, Power -30, Fan -20, License -5 처럼 check마다 감점폭이 다름).
"""

import os
import yaml

# severity별 기본 감점 및 check_id별 감점 override를 로드한다.
# 파일이 없으면 기본값을 사용.
_SCORING_CONFIG = None
DEFAULT_DEDUCTION_BY_SEVERITY = {
    "Critical": 30,
    "High": 15,
    "Medium": 5,
    "Low": 2,
    "Info": 0,
}
CHECK_ID_DEDUCTION_OVERRIDES = {}

def _load_config():
    global _SCORING_CONFIG, DEFAULT_DEDUCTION_BY_SEVERITY, CHECK_ID_DEDUCTION_OVERRIDES
    if _SCORING_CONFIG is not None:
        return
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "rule_engine", "scoring_config.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
            if cfg:
                DEFAULT_DEDUCTION_BY_SEVERITY = cfg.get("default_severities", DEFAULT_DEDUCTION_BY_SEVERITY)
                overrides = cfg.get("check_overrides", {})
                CHECK_ID_DEDUCTION_OVERRIDES = {k: v.get("deduction", 0) for k, v in overrides.items()}
    _SCORING_CONFIG = True

MIN_SCORE = 0
MAX_SCORE = 100


def _get(finding, key):
    """Finding 객체(속성 접근)와 dict(History JSON에서 로드된 것) 둘 다 지원."""
    return getattr(finding, key) if hasattr(finding, key) else finding.get(key)


def deduction_for(finding):
    """단일 Finding의 감점폭. PASS/SKIPPED는 감점 없음."""
    _load_config()
    result = _get(finding, "result")
    if result not in ("FAIL", "UNKNOWN"):
        return 0
    check_id = _get(finding, "check_id")
    if check_id in CHECK_ID_DEDUCTION_OVERRIDES:
        return CHECK_ID_DEDUCTION_OVERRIDES[check_id]
    return DEFAULT_DEDUCTION_BY_SEVERITY.get(_get(finding, "severity"), 0)


def score_device(findings_for_device):
    """장비 하나의 Health Score. 100점에서 시작해 감점 누적, 0 밑으로는 안 내려감."""
    score = MAX_SCORE
    for f in findings_for_device:
        score -= deduction_for(f)
    return max(MIN_SCORE, score)


def score_by_device(findings):
    """전체 Finding을 device별로 묶어 각 장비 점수를 계산. 반환: {device_name: score}"""
    by_device = {}
    for f in findings:
        by_device.setdefault(_get(f, "device"), []).append(f)
    return {device: score_device(flist) for device, flist in by_device.items()}


def score_site(device_scores):
    """Site(또는 Project) 레벨 점수 — 소속 장비 점수의 평균. device_scores: {device: score} dict 또는 score 리스트."""
    scores = list(device_scores.values()) if isinstance(device_scores, dict) else list(device_scores)
    if not scores:
        return MAX_SCORE
    return round(sum(scores) / len(scores), 1)


def score_project(findings):
    """프로젝트 전체 Health Score — device별 점수 계산 후 평균."""
    device_scores = score_by_device(findings)
    return {
        "project_score": score_site(device_scores),
        "device_scores": device_scores,
    }


if __name__ == "__main__":
    from core.finding import Finding

    findings = [
        Finding(project_id="p", session_id="s", device="Core1", category="STP",
                check_id="root_priority_vlan1_core1", result="FAIL", severity="High"),
        Finding(project_id="p", session_id="s", device="Core1", category="HW",
                check_id="power_status", result="FAIL", severity="Critical"),
        Finding(project_id="p", session_id="s", device="Agg1", category="HW",
                check_id="cpu_usage", result="FAIL", severity="Medium"),
        Finding(project_id="p", session_id="s", device="Access1", category="VLAN",
                check_id="vlan_100_exists", result="PASS", severity="Info"),
    ]

    print("Core1 점수(감점: STP High -15, Power Critical -30 override -30 = 동일):",
          score_device([f for f in findings if f.device == "Core1"]))
    print("Agg1 점수(CPU override -5):", score_device([f for f in findings if f.device == "Agg1"]))
    print("Access1 점수(전부 PASS):", score_device([f for f in findings if f.device == "Access1"]))

    result = score_project(findings)
    print("\n프로젝트 전체:", result)
