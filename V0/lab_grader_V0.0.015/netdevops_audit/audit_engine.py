"""Rules engine computing per-device and fabric-wide network health scores."""

from __future__ import annotations

from typing import Any, Dict, List

from parser_layer import Finding

CATEGORY_WEIGHTS = {
    "Physical": 0.2,
    "Switching": 0.25,
    "Routing": 0.35,
    "System": 0.2,
}

SEVERITY_DEDUCTIONS = {
    "CRITICAL": 30.0,
    "WARNING": 15.0,
    "INFO": 0.0,
}

CATEGORY_PENALTY_CAP = 40.0

ROLE_WEIGHTS = {
    "core": 2.0,
    "spine": 2.0,
    "leaf": 1.0,
    "access": 1.0,
}
DEFAULT_ROLE_WEIGHT = 1.0

MIN_SCORE = 0.0
MAX_SCORE = 100.0


class NetworkAuditRuleEngine:
    """Computes weighted health scores (H_d, H_fabric) from a set of Findings."""

    def __init__(
        self,
        category_weights: Dict[str, float] = None,
        severity_deductions: Dict[str, float] = None,
        category_penalty_cap: float = CATEGORY_PENALTY_CAP,
    ) -> None:
        self._category_weights = category_weights or CATEGORY_WEIGHTS
        self._severity_deductions = severity_deductions or SEVERITY_DEDUCTIONS
        self._category_penalty_cap = category_penalty_cap

    def _category_penalty(self, findings: List[Finding], category: str) -> float:
        total_deduction = sum(
            self._severity_deductions.get(finding.severity, 0.0)
            for finding in findings
            if finding.category == category
        )
        return min(self._category_penalty_cap, total_deduction)

    def calculate_device_score(self, findings: List[Finding]) -> Dict[str, Any]:
        """Returns {"H_d": float, "breakdown": {category: {"penalty": float, "weight": float}}}."""
        breakdown: Dict[str, Dict[str, float]] = {}
        weighted_penalty_sum = 0.0

        for category, weight in self._category_weights.items():
            penalty = self._category_penalty(findings, category)
            breakdown[category] = {"penalty": penalty, "weight": weight}
            weighted_penalty_sum += weight * penalty

        unknown_categories = {f.category for f in findings} - set(self._category_weights)
        for category in unknown_categories:
            penalty = self._category_penalty(findings, category)
            breakdown[category] = {"penalty": penalty, "weight": 0.0}

        h_d = MAX_SCORE - weighted_penalty_sum
        h_d = max(MIN_SCORE, min(MAX_SCORE, h_d))

        return {"H_d": round(h_d, 4), "breakdown": breakdown}

    def calculate_fabric_score(self, device_scores: Dict[str, Dict[str, Any]]) -> float:
        """device_scores: {hostname: {"H_d": float, "role": str}} -> weighted H_fabric."""
        if not device_scores:
            return MIN_SCORE

        weighted_sum = 0.0
        weight_total = 0.0

        for info in device_scores.values():
            role = str(info.get("role", "")).lower()
            weight = ROLE_WEIGHTS.get(role, DEFAULT_ROLE_WEIGHT)
            weighted_sum += weight * info["H_d"]
            weight_total += weight

        if weight_total == 0:
            return MIN_SCORE

        h_fabric = weighted_sum / weight_total
        return round(max(MIN_SCORE, min(MAX_SCORE, h_fabric)), 4)


import pytest

def make_finding(category: str, severity: str, node_id: str = "sw1") -> Finding:
    return Finding(node_id=node_id, category=category, severity=severity, message="msg", measured_value=None)

def test_calculate_device_score_no_findings_is_perfect():
    engine = NetworkAuditRuleEngine()
    result = engine.calculate_device_score([])
    assert result["H_d"] == 100.0

def test_calculate_device_score_single_critical():
    engine = NetworkAuditRuleEngine()
    findings = [make_finding("Physical", "CRITICAL")]
    result = engine.calculate_device_score(findings)
    # 100 - (0.2 * 30) = 94.0
    assert result["H_d"] == 94.0
    assert result["breakdown"]["Physical"]["penalty"] == 30.0

def test_calculate_device_score_category_penalty_capped():
    engine = NetworkAuditRuleEngine()
    findings = [make_finding("Routing", "CRITICAL") for _ in range(5)]  # 150 raw, capped at 40
    result = engine.calculate_device_score(findings)
    assert result["breakdown"]["Routing"]["penalty"] == 40.0
    # 100 - (0.35 * 40) = 86.0
    assert result["H_d"] == 86.0

def test_calculate_device_score_multi_category():
    engine = NetworkAuditRuleEngine()
    findings = [
        make_finding("Physical", "WARNING"),
        make_finding("Switching", "CRITICAL"),
        make_finding("System", "INFO"),
    ]
    result = engine.calculate_device_score(findings)
    expected = 100.0 - (0.2 * 15.0) - (0.25 * 30.0) - (0.2 * 0.0)
    assert result["H_d"] == round(expected, 4)

def test_calculate_device_score_never_below_zero():
    engine = NetworkAuditRuleEngine()
    findings = [make_finding(cat, "CRITICAL") for cat in CATEGORY_WEIGHTS for _ in range(10)]
    result = engine.calculate_device_score(findings)
    assert result["H_d"] >= 0.0

def test_calculate_fabric_score_weights_core_higher():
    engine = NetworkAuditRuleEngine()
    device_scores = {
        "core1": {"H_d": 100.0, "role": "core"},
        "leaf1": {"H_d": 50.0, "role": "leaf"},
    }
    # (2.0*100 + 1.0*50) / 3.0 = 83.3333
    result = engine.calculate_fabric_score(device_scores)
    assert result == round((2.0 * 100.0 + 1.0 * 50.0) / 3.0, 4)

def test_calculate_fabric_score_empty_returns_zero():
    engine = NetworkAuditRuleEngine()
    assert engine.calculate_fabric_score({}) == 0.0

def test_calculate_fabric_score_unknown_role_uses_default_weight():
    engine = NetworkAuditRuleEngine()
    device_scores = {"unknown1": {"H_d": 80.0, "role": "firewall"}}
    assert engine.calculate_fabric_score(device_scores) == 80.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
