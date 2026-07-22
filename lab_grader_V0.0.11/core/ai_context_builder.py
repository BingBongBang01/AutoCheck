"""
AIContextBuilder — Finding 리스트를 AI가 이해하기 좋은 압축된 컨텍스트로 구성.
Sanitizer(마스킹)와 역할을 분리 — 이 객체는 "무엇을 보여줄지"만 결정하고
"어떻게 가릴지"는 전혀 모른다(마스킹 정책이 바뀌어도 이 코드는 안 건드림).
"""


def build_context(findings, max_items=20):
    """
    findings: Finding 객체 리스트 (마스킹 전/후 둘 다 받을 수 있음 — 이 함수는 마스킹을 모름)
    반환: AI 프롬프트에 넣기 좋은 압축된 dict — FAIL/UNKNOWN 위주로 추리고,
          너무 많으면 상위 max_items개만(토큰 절약).
    """
    relevant = [f for f in findings if getattr(f, "result", f.get("result") if isinstance(f, dict) else None) in ("FAIL", "UNKNOWN")]

    def as_dict(f):
        return f.to_dict() if hasattr(f, "to_dict") else f

    items = [as_dict(f) for f in relevant[:max_items]]
    truncated = len(relevant) > max_items

    return {
        "total_findings": len(findings),
        "relevant_findings": len(relevant),
        "truncated": truncated,
        "items": [
            {"device": i["device"], "category": i["category"], "check_id": i["check_id"],
             "result": i["result"], "expected": i.get("expected"), "actual": i.get("actual")}
            for i in items
        ],
    }


def to_prompt_text(context):
    """build_context() 결과를 사람이 읽는 프롬프트 문자열로."""
    lines = [f"점검 결과 요약: 전체 {context['total_findings']}건 중 이상 {context['relevant_findings']}건"]
    if context["truncated"]:
        lines.append(f"(상위 {len(context['items'])}건만 표시, 나머지 생략)")
    for item in context["items"]:
        lines.append(f"- [{item['category']}] {item['device']}/{item['check_id']}: "
                      f"{item['result']} (기대={item['expected']}, 실제={item['actual']})")
    return "\n".join(lines)


if __name__ == "__main__":
    from core.finding import Finding

    findings = [
        Finding(project_id="p", session_id="s", device="Core1", category="STP",
                check_id="root_priority_vlan1_core1", result="FAIL", severity="CRITICAL",
                expected=4096, actual=32768),
        Finding(project_id="p", session_id="s", device="Core2", category="VLAN",
                check_id="vlan_100_exists", result="PASS", severity="INFO"),
    ]
    ctx = build_context(findings)
    print("컨텍스트:", ctx)
    print()
    print("프롬프트 텍스트:")
    print(to_prompt_text(ctx))
