"""GradeApiMixin — Pipeline 기반 채점 실행만 담당."""
import io
import contextlib


class GradeApiMixin:
    def run_grade(self):
        try:
            project_id = self._project()
        except RuntimeError:
            return "활성 프로젝트 없음"
        import main as main_module
        main_module.init_project(project_id)
        collect_fn = main_module.real_collect
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                main_module.grade_via_pipeline(collect_fn)
            except Exception as e:
                print(f"[오류] {e}")
        return buf.getvalue()
