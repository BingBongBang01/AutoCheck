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


def make_batches(items, max_chars=1500, max_segs=10):
    """items(dict 리스트)를 문자 수/세그먼트 수 한도 안에서 여러 배치로 분할.
    PDF번역 프로그램의 pdf_engine/batching.py:make_batches와 동일한 그리디 방식 —
    한 배치가 max_chars(문자 수) 또는 max_segs(항목 수)를 넘기기 직전에 새 배치로 넘어간다.
    컨텍스트 오버플로우 방지가 목적이므로 항목 하나가 max_chars를 넘어도 쪼개지 않고 단독 배치로 둔다.
    """
    import json as _json
    batches = []
    current = []
    current_chars = 0
    for item in items:
        item_len = len(_json.dumps(item, ensure_ascii=False))
        if current and (current_chars + item_len > max_chars or len(current) >= max_segs):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_len
    if current:
        batches.append(current)
    return batches or [[]]


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
                check_id="root_priority_vlan1_core1", result="FAIL", severity="Critical",
                expected=4096, actual=32768),
        Finding(project_id="p", session_id="s", device="Core2", category="VLAN",
                check_id="vlan_100_exists", result="PASS", severity="Info"),
    ]
    ctx = build_context(findings)
    print("컨텍스트:", ctx)
    print()
    print("프롬프트 텍스트:")
    print(to_prompt_text(ctx))
