"""
기존 report/markdown_report.py의 함수(build_markdown_report, save_docx_report)를
BaseReporter 인터페이스로 감싸서 Registry에 등록 — 내부 로직은 전혀 안 건드림.
"""
try:
    from report.base_reporter import BaseReporter, register
    from report.markdown_report import build_markdown_report, save_docx_report
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from report.base_reporter import BaseReporter, register
    from report.markdown_report import build_markdown_report, save_docx_report


class MarkdownReporter(BaseReporter):
    format_id = "markdown"
    file_extension = ".md"

    def build(self, project_name, scored, ai_result, output_path, root_causes=None):
        content = build_markdown_report(project_name, scored, ai_result, root_causes=root_causes)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return output_path


class DocxReporter(BaseReporter):
    format_id = "docx"
    file_extension = ".docx"

    def build(self, project_name, scored, ai_result, output_path, root_causes=None):
        # python-docx 표 기반 리포트는 아직 "근본 원인 분석" 섹션 템플릿이 없음(root_causes는
        # 받아만 두고 무시) — Markdown/HTML만 우선 지원, 필요 시 여기에 별도 표 추가.
        return save_docx_report(project_name, scored, ai_result, output_path)


register(MarkdownReporter())
register(DocxReporter())


class HtmlReporter(BaseReporter):
    format_id = "html"
    file_extension = ".html"

    def build(self, project_name, scored, ai_result, output_path, root_causes=None):
        from report.html_report import build_html_report
        return build_html_report(project_name, scored, output_path, root_causes=root_causes)


register(HtmlReporter())


class InspectionReporter(BaseReporter):
    """정기점검(inspection mode) 전용 — 채점표 대신 전월 대비 변화/Critical/조치권고 형태.
    build()의 나머지 reporter들과 시그니처를 맞추기 위해 scored/ai_result는 그대로 받되,
    findings/diff는 kwargs로 추가 — 호출부(ReportStep)가 inspection 모드일 때만 채워 넘김."""
    format_id = "inspection"
    file_extension = ".md"

    def build(self, project_name, scored, ai_result, output_path, findings=None, diff=None, root_causes=None):
        # 정기점검 리포트는 아직 "근본 원인 분석" 섹션이 없음(root_causes는 받아만 두고 무시).
        from report.inspection_report import save_inspection_report
        return save_inspection_report(project_name, output_path, findings=findings, diff=diff, ai_result=ai_result)


register(InspectionReporter())


class PdfReporter(BaseReporter):
    """reportlab 미설치 환경에서는 build()가 None을 반환 — save_docx_report와 동일한 관례."""
    format_id = "pdf"
    file_extension = ".pdf"

    def build(self, project_name, scored, ai_result, output_path, root_causes=None):
        from report.pdf_report import save_pdf_report
        return save_pdf_report(project_name, scored, ai_result, output_path, root_causes=root_causes)


register(PdfReporter())


if __name__ == "__main__":
    from report.base_reporter import list_formats, get_reporter
    print("등록된 리포트 형식:", list_formats())

    sample_scored = [{"label": "VLAN", "status": "COMPLETE", "pass": 18, "total": 18, "results": []}]
    reporter = get_reporter("markdown")
    path = reporter.build("lab1_campus", sample_scored, None, "/tmp/test_report.md")
    print("생성됨:", path)
    with open(path) as f:
        print(f.read()[:100])
