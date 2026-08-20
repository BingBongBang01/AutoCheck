"""
PDF 리포트 — reportlab이 설치돼 있을 때만 동작(report/markdown_report.py의 save_docx_report가
python-docx 미설치 시 None을 반환하며 안내만 하는 것과 동일한 패턴).
ReportManager(engine/report_manager.py)가 이 모듈을 통해서만 PDF를 만든다.
"""
import datetime


def save_pdf_report(project_name, scored, ai_result, output_path, root_causes=None, header_lines=None):
    """reportlab 미설치면 None을 반환하고 안내만 출력 — Markdown/HTML/Excel 등 다른 포맷은
    그대로 생성되도록 호출부(ReportManager)가 이 반환값으로 실패를 감지해 건너뛴다."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors
    except ImportError:
        print("[안내] reportlab 미설치 — PDF 보고서는 건너뜀(pip install reportlab로 추가 가능)")
        return None

    styles = getSampleStyleSheet()
    story = []

    for line in (header_lines or []):
        story.append(Paragraph(line, styles["Normal"]))
    if header_lines:
        story.append(Spacer(1, 6 * mm))

    story.append(Paragraph(f"{project_name} 점검 보고서", styles["Title"]))
    story.append(Paragraph(f"작성일: {datetime.date.today().isoformat()}", styles["Normal"]))
    story.append(Spacer(1, 4 * mm))

    if ai_result:
        story.append(Paragraph("Executive Summary", styles["Heading2"]))
        story.append(Paragraph(ai_result.get("summary", ""), styles["Normal"]))
        story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("단계별 결과", styles["Heading2"]))
    rows = [["단계", "상태", "PASS", "TOTAL"]]
    for s in scored or []:
        rows.append([s.get("label", ""), s.get("status", ""), str(s.get("pass", "")), str(s.get("total", ""))])
    table = Table(rows, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(table)

    doc = SimpleDocTemplate(str(output_path), pagesize=A4)
    doc.build(story)
    return output_path
