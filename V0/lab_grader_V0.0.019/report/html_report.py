import html


def _build_root_cause_section(root_causes):
    """RootCauseFinding(core/root_cause.py) 리스트 -> "근본 원인 분석" HTML 섹션.
    stages 기반 채점 표와는 별개 블록 — root_causes=None이면 섹션 자체를 생략(기존 호출부 무변경)."""
    if root_causes is None:
        return ""

    if not root_causes:
        return "<h2>근본 원인 분석</h2><p>(탐지된 상관관계 없음)</p>"

    items = []
    for rc in root_causes:
        rc = rc.to_dict() if hasattr(rc, "to_dict") else rc
        cause = rc.get("cause_event") or {}
        effects = rc.get("effect_events") or []
        confidence = rc.get("confidence")
        confidence_text = f"{confidence:.0%}" if isinstance(confidence, (int, float)) else "불명"

        effect_items = "".join(
            f"<li>{html.escape(eff.get('device', '-'))} / {html.escape(eff.get('event_type', '-'))} "
            f"(인터페이스: {html.escape(', '.join(eff.get('interfaces') or []) or '-')}, "
            f"시각: {html.escape(str(eff.get('timestamp', '-')))})</li>"
            for eff in effects
        )
        items.append(f"""<div class='rc-item'>
<h3>{html.escape(rc.get('cause_device', '-'))} — {html.escape(cause.get('event_type', '-'))}</h3>
<p>발생 시각: {html.escape(str(cause.get('timestamp', '-')))}</p>
<p>근거(source): {html.escape(rc.get('source', '-'))} / 확신도: {confidence_text}</p>
{f"<p>매칭 규칙: {html.escape(rc['rule_id'])}</p>" if rc.get('rule_id') else ""}
{f"<p>설명: {html.escape(rc['explanation'])}</p>" if rc.get('explanation') else ""}
{f"<p>연쇄 영향:</p><ul>{effect_items}</ul>" if effects else ""}
</div>""")

    return f"<h2>근본 원인 분석</h2>{''.join(items)}"


def build_html_report(project, stages, output_path, root_causes=None):
    passed = sum(stage.get("pass", 0) for stage in stages)
    total = sum(stage.get("total", 0) for stage in stages)
    body = []
    for stage in stages:
        body.append(f"<tr><td>{html.escape(stage.get('label', ''))}</td><td>{stage.get('pass', 0)}</td><td>{stage.get('total', 0)}</td><td>{html.escape(stage.get('status', ''))}</td></tr>")
    root_cause_section = _build_root_cause_section(root_causes)
    content = f"""<!doctype html><html lang='ko'><meta charset='utf-8'><title>{html.escape(project)} 점검 보고서</title>
<style>body{{font-family:Arial;background:#1e1e1e;color:#ddd;padding:32px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #444;padding:8px;text-align:left}}th{{background:#252526}}.rc-item{{border:1px solid #444;padding:12px;margin-bottom:12px}}</style>
<h1>{html.escape(project)} 점검 보고서</h1><p>통과: {passed} / {total}</p><table><tr><th>단계</th><th>PASS</th><th>전체</th><th>상태</th></tr>{''.join(body)}</table>{root_cause_section}</html>"""
    with open(output_path, "w", encoding="utf-8") as stream:
        stream.write(content)
    return output_path
