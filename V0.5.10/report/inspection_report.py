"""
정기점검(inspection mode) 전용 리포트 본문 생성.
기존 채점표(단계별 PASS/TOTAL) 방식이 아니라
"전월 대비 변화 요약 + Critical Finding 목록 + 조치 권고" 형태 — 실무 제출용 톤앤매너.
"""
import datetime

from core.finding import SEVERITY_CRITICAL


def build_inspection_report(project_name, findings=None, diff=None, ai_result=None):
    findings = findings or []
    lines = []
    lines.append(f"# {project_name} 정기점검 리포트")
    lines.append(f"작성일: {datetime.date.today().isoformat()}")
    lines.append("")

    if ai_result:
        lines.append("## Executive Summary")
        lines.append(ai_result.get("summary", ""))
        lines.append(f"(분석 출처: {ai_result.get('source', 'unknown')})")
        lines.append("")

    lines.append("## 전월 대비 변화 요약")
    if diff is None:
        lines.append("직전 회차 기록이 없어 비교 불가(최초 점검).")
    else:
        lines.append(f"- 신규 발생(NEW): {len(diff.get('new', []))}건")
        lines.append(f"- 재발/유지(PERSISTENT): {len(diff.get('persistent', []))}건")
        lines.append(f"- 해소(RESOLVED): {len(diff.get('resolved', []))}건")
        lines.append("")
        if diff.get("new"):
            lines.append("### 신규 발생 항목")
            for f in diff["new"]:
                lines.append(f"- **{f.get('device')} / {f.get('check_id')}** ({f.get('severity')}) — {f.get('evidence', '')}")
        if diff.get("resolved"):
            lines.append("")
            lines.append("### 해소된 항목")
            for f in diff["resolved"]:
                lines.append(f"- {f.get('device')} / {f.get('check_id')}")
    lines.append("")

    critical = [f.to_dict() if hasattr(f, "to_dict") else f for f in findings]
    critical = [f for f in critical if f.get("severity") == SEVERITY_CRITICAL]
    lines.append(f"## Critical Finding 목록 ({len(critical)}건)")
    if not critical:
        lines.append("Critical 등급 Finding 없음.")
    else:
        for f in critical:
            lines.append(f"- **{f.get('device')} / {f.get('check_id')}** — 기대값: {f.get('expected')} / 실제: {f.get('actual')}")
    lines.append("")

    recommended = [f.to_dict() if hasattr(f, "to_dict") else f for f in findings]
    recommended = [f for f in recommended if f.get("recommendation")]
    lines.append("## 조치 권고")
    if not recommended:
        lines.append("등록된 조치 권고 없음.")
    else:
        for f in recommended:
            lines.append(f"- **{f.get('device')} / {f.get('check_id')}** — {f.get('recommendation')}")

    return "\n".join(lines)


def save_inspection_report(project_name, output_path, findings=None, diff=None, ai_result=None):
    content = build_inspection_report(project_name, findings=findings, diff=diff, ai_result=ai_result)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return output_path
