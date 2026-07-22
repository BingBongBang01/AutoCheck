"""ReportApiMixin — 보고서 생성(Report Plugin 포함)만 담당."""


class ReportApiMixin:
    def generate_report(self):
        try:
            project_id = self._project()
        except RuntimeError:
            return None
        from engine.history import load_latest
        latest = load_latest(project_id)
        if not latest:
            return None
        from ai_analysis.router import analyze as ai_analyze
        from report.markdown_report import build_markdown_report
        from report.reporters import MarkdownReporter
        ai_result = ai_analyze(latest["stages"], ai_config=None)
        md = build_markdown_report(project_id, latest["stages"], ai_result)
        paths = self._paths()
        out_path = paths["target_state"].replace("target_state.yaml", "report_latest.md")
        MarkdownReporter().build(project_id, latest["stages"], ai_result, out_path)
        return md

    def generate_report_as(self, format_id):
        """Report Plugin 목록에서 형식 선택해서 생성 — markdown/docx."""
        try:
            project_id = self._project()
        except RuntimeError:
            return {"error": "활성 프로젝트 없음"}
        from engine.history import load_latest
        latest = load_latest(project_id)
        if not latest:
            return {"error": "채점 이력 없음"}
        from ai_analysis.router import analyze as ai_analyze
        from report.reporters import get_reporter
        from report.base_reporter import list_formats
        reporter = get_reporter(format_id)
        if not reporter:
            return {"error": f"지원 안 하는 형식: {format_id} (지원: {list_formats()})"}
        ai_result = ai_analyze(latest["stages"], ai_config=None)
        paths = self._paths()
        out_path = paths["target_state"].replace("target_state.yaml", f"report_latest{reporter.file_extension}")
        result_path = reporter.build(project_id, latest["stages"], ai_result, out_path)
        if not result_path:
            return {"error": f"{format_id} 생성 실패(필요 라이브러리 미설치 가능성)"}
        return {"path": result_path}

    def list_report_formats(self):
        from report.reporters import list_formats  # import 시점에 register() 실행됨
        return list_formats()
