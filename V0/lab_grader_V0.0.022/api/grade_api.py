"""GradeApiMixin — Pipeline 기반 채점 실행만 담당."""
import io
import threading
import contextlib

# 채점 1건이 완료되기 전에 버튼이 중복 클릭되거나 다른 경로(예: 스케줄러)로 또 호출되면
# 같은 Inventory 전체에 대해 접속·재시도 로직이 통째로 한 번 더 겹쳐 실행되어 로그가
# 두세 배로 폭주하는 원인이 됐다. run_terminal_inspection의 _inspection_lock과 동일한
# 재진입 방지 락으로 "이미 채점 중이면 새 요청은 즉시 거절"하게 만든다.
_grade_lock = threading.Lock()


class GradeApiMixin:
    def run_grade(self):
        try:
            project_id = self._project()
        except RuntimeError:
            return "활성 프로젝트 없음"
        if not _grade_lock.acquire(blocking=False):
            return "이미 채점이 진행 중입니다. 완료 후 다시 시도하세요."
        try:
            import main as main_module
            main_module.init_project(project_id)
            customer_name, profile_name = self.resolve_active_customer_profile_names()
            collect_fn = lambda: main_module.real_collect(customer_name, profile_name)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                try:
                    main_module.grade_via_pipeline(collect_fn)
                except Exception as e:
                    print(f"[오류] {e}")
            return buf.getvalue()
        finally:
            _grade_lock.release()
