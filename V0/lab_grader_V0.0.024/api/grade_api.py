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

    def get_grade_progress(self, job_id=None):
        """Progress Engine(core/progress_engine.py)이 이미 계산해둔 스냅샷을 그대로 반환한다.
        job_id를 안 주면 가장 최근 채점 실행(engine/collector.py::collect_all이 만든
        job)을 자동으로 조회한다. GUI(JS)는 이 값을 그대로 표시만 하고 경과/잔여 시간을
        직접 계산하지 않는다("GUI receives events only" 원칙)."""
        from core import progress_engine
        snap = progress_engine.snapshot(job_id)
        return snap if snap else {"job_id": job_id, "status": "no_job"}

    def run_grade_async(self):
        """run_grade()의 Job Manager 버전 — pywebview 브릿지 스레드를 블로킹하지 않고
        즉시 job_id를 반환한다. GUI는 get_job_status(job_id)/get_grade_progress()로
        진행 상태를 폴링한다(계산은 전부 서버 쪽 — Job Manager/Progress Engine).
        완료되면(customer/profile 기반 실행인 경우) 방금 만들어진 run을 History Manager로
        보관까지 한 번에 처리한다 — 기존 run_grade()는 호환을 위해 그대로 둔다."""
        try:
            project_id = self._project()
        except RuntimeError:
            return {"error": "활성 프로젝트 없음"}
        if not _grade_lock.acquire(blocking=False):
            return {"error": "이미 채점이 진행 중입니다. 완료 후 다시 시도하세요."}

        import main as main_module
        main_module.init_project(project_id)
        customer_name, profile_name = self.resolve_active_customer_profile_names()

        def _grade_job(job):
            try:
                collect_fn = lambda: main_module.real_collect(customer_name, profile_name)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    result_ctx = main_module.grade_via_pipeline(collect_fn)

                if customer_name and profile_name:
                    from engine.run_manager import run_manager
                    from core.history_manager import history_manager
                    recent = run_manager.list_runs(customer_name, profile_name)
                    if recent:
                        latest_run_id = recent[0]["run_id"]  # collect_all이 방금 만든 run(최신)
                        try:
                            history_manager.archive_completed_run(
                                customer_name, profile_name, latest_run_id, job_id=job.job_id)
                        except Exception:
                            pass  # 보관 실패가 채점 결과 자체를 무효화하면 안 됨

                return buf.getvalue()
            finally:
                _grade_lock.release()

        from core.job_manager import job_manager
        job_id = job_manager.submit("grade", _grade_job)
        return {"job_id": job_id}

    def get_job_status(self, job_id):
        from core.job_manager import job_manager
        return job_manager.get(job_id)

    def cancel_job(self, job_id):
        from core.job_manager import job_manager
        return job_manager.cancel(job_id)

    def list_grade_history(self, customer_name=None, profile_name=None):
        """History Manager — 이 프로파일의 활성 run + 보관된(archive) run을 합친 전체 이력."""
        if not customer_name or not profile_name:
            customer_name, profile_name = self.resolve_active_customer_profile_names()
        if not customer_name or not profile_name:
            return []
        from core.history_manager import history_manager
        return history_manager.list_history(customer_name, profile_name)
