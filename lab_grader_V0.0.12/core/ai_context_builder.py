"""
AIContextBuilder

Finding을 AI가 이해하기 쉬운 형태로 변환한다.

AI는

Finding
↓

Summary

↓

Recommendation

만 생성한다.

판정(result)은 절대 변경하지 않는다.
"""


def build_context(findings, max_items=20):

    relevant = [
        f
        for f in findings
        if getattr(
            f,
            "result",
            f.get("result") if isinstance(f, dict) else None,
        )
        in (
            "FAIL",
            "UNKNOWN",
        )
    ]

    def as_dict(f):
        return f.to_dict() if hasattr(f, "to_dict") else f

    items = [as_dict(f) for f in relevant[:max_items]]

    truncated = len(relevant) > max_items

    return {
        "total_findings": len(findings),
        "relevant_findings": len(relevant),
        "truncated": truncated,
        "items": [
            {
                "device": i["device"],
                "interface": i.get("interface", ""),
                "category": i["category"],
                "check_id": i["check_id"],
                "severity": i.get("severity", "Info"),
                "status": i.get("status", "Open"),
                "result": i["result"],
                "expected": i.get("expected"),
                "actual": i.get("actual"),
                "recommendation": i.get("recommendation", ""),
            }
            for i in items
        ],
    }


def to_prompt_text(context):

    lines = [
        f"점검 결과 요약: 전체 {context['total_findings']}건 중 이상 {context['relevant_findings']}건"
    ]

    if context["truncated"]:
        lines.append(
            f"(상위 {len(context['items'])}건만 표시)"
        )

    for item in context["items"]:

        lines.append(
            f"- [{item['severity']}] "
            f"{item['device']} "
            f"{item['interface']} "
            f"{item['category']} "
            f"{item['check_id']} : "
            f"{item['result']} "
            f"(기대={item['expected']}, 실제={item['actual']})"
        )

    return "\n".join(lines)


if __name__ == "__main__":

    from core.finding import Finding

    findings = [
        Finding(
            project_id="p",
            session_id="s",
            device="Core1",
            category="STP",
            check_id="root_priority_vlan1_core1",
            result="FAIL",
            severity="Critical",
            expected=4096,
            actual=32768,
        ),
        Finding(
            project_id="p",
            session_id="s",
            device="Core2",
            category="VLAN",
            check_id="vlan_100_exists",
            result="PASS",
            severity="Info",
        ),
    ]

    ctx = build_context(findings)

    print("컨텍스트")
    print(ctx)

    print()

    print("프롬프트")

    print(to_prompt_text(ctx))