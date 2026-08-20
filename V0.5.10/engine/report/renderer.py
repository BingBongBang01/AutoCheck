"""
Report Formatting and Rendering Module.
Renders aggregated run data into target formats (Markdown, HTML, Text).
"""
from typing import Any, Dict


class ReportRenderer:
    """Renders aggregated data into formatted documents."""

    @staticmethod
    def render_markdown_summary(aggregated_data: Dict[str, Any]) -> str:
        """Render a Markdown format summary report."""
        run_id = aggregated_data.get("run_id", "")
        session = aggregated_data.get("session", {})
        summary = aggregated_data.get("summary", {})
        health = aggregated_data.get("health_score", {})

        md = []
        md.append(f"# 점검 결과 보고서 (Run: {run_id})")
        md.append("")
        md.append(f"- **고객사**: {session.get('customer', '-')}")
        md.append(f"- **프로파일**: {session.get('profile', '-')}")
        md.append(f"- **진행 시각**: {session.get('started_at', '-')}")
        md.append(f"- **종합 건전성 점수**: {health.get('score', '-')}점")
        md.append("")
        md.append("## 점검 요약")
        md.append(f"- 전체 검사 항목: {summary.get('total_checks', 0)}")
        md.append(f"- 통과: {summary.get('passed_checks', 0)}")
        md.append(f"- 실패: {summary.get('failed_checks', 0)}")
        md.append("")
        return "\n".join(md)
