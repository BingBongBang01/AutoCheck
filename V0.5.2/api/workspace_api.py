"""
WorkspaceApiMixin — 새 워크스페이스 아키텍처(ProfileManager/StorageService/RunManager/
LogManager/ReportManager)를 기존 GUI에 노출한다.

고객사/프로파일 선택 자체(생성/이름변경/삭제)는 이미 CustomerProfileApiMixin이 제공하므로
여기서는 재사용만 한다(resolve_active_customer_profile_names()) — 이 클래스는 Api 합성 시
반드시 CustomerProfileApiMixin과 함께 조합돼야 한다. 여기서 새로 추가하는 건 Run History/
Workspace Information/Current Run Status/Recent Reports·Exports·Logs 조회, 폴더 열기,
Archive Profile, 그리고 리포트 생성·내보내기 같은 장시간 작업의 백그라운드 실행뿐이다.

장시간 작업은 JobRunner(api/job_runner.py — log_analysis_run_api.py와 동일 패턴)로 백그라운드
스레드에서 돌리고, get_workspace_job_status()로 폴링한다(web_ui/js/workspace.js 참고).
"""
from engine.profile_manager import profile_manager as prm
from engine.run_manager import run_manager as rmgr, RunManagerError
from engine.log_manager import log_manager as lmgr
from engine.report_manager import report_manager as repmgr, ReportManagerError
from core.storage_service import storage_service
from engine.log_storage import open_in_file_explorer
from api.job_runner import JobRunner

_WORKSPACE_JOB_KINDS = ("inspection", "parsing", "analysis", "report", "export")


class WorkspaceApiMixin:
    def _workspace_jobs(self) -> JobRunner:
        if not hasattr(self, "_workspace_job_runner"):
            self._workspace_job_runner = JobRunner(_WORKSPACE_JOB_KINDS)
        return self._workspace_job_runner

    def get_workspace_job_status(self):
        """'Workspace' 탭 진행바 폴링용 — Inspection/Parsing/Analysis/Report Generation/
        Export 5종 작업의 현재 상태를 한 번에 반환."""
        return self._workspace_jobs().status_all()

    # ---- 활성 고객사/프로파일/run 해석(CustomerProfileApiMixin 재사용, 중복 없음) -------------

    def _active_customer_profile(self):
        return self.resolve_active_customer_profile_names()

    def _active_run(self, run_id: str = None):
        """run_id를 넘기면 그 run, 아니면 진행 중인 run(RunManager 캐시/PAUSED/RUNNING 복구),
        그것도 없으면 가장 최근 run. 아무 run도 없으면 None."""
        customer_name, profile_name = self._active_customer_profile()
        if not customer_name or not profile_name:
            return None
        if run_id:
            try:
                run, _, _ = rmgr.load_run(customer_name, profile_name, run_id)
                return run
            except RunManagerError:
                return None
        run = rmgr.get_active_run(customer_name, profile_name)
        if run is not None:
            return run
        run_ids = prm.list_runs(customer_name, profile_name)
        if not run_ids:
            return None
        try:
            run, _, _ = rmgr.load_run(customer_name, profile_name, run_ids[-1])
            return run
        except RunManagerError:
            return None

    # ---- Workspace Information / Run History ---------------------------------------

    def get_workspace_overview(self):
        """'Workspace' 탭 전체 데이터 — Customer/Profile 컨텍스트, Run History,
        Current Run Status, Recent Reports/Exports/Logs를 한 번에 반환."""
        customer_name, profile_name = self._active_customer_profile()
        if not customer_name or not profile_name:
            return {"error": "활성 고객사/프로파일이 없습니다."}

        latest_run = self._active_run()
        recent_reports = repmgr.list_reports(latest_run)[:10] if latest_run else []
        recent_exports = repmgr.list_exports(latest_run)[:10] if latest_run else []
        recent_logs = lmgr.list_logs(latest_run, "raw")[:10] if latest_run else []

        return {
            "customer": customer_name, "profile": profile_name,
            "run_history": self.get_run_history(),
            "current_run": self.get_current_run_status(),
            "recent_reports": recent_reports, "recent_exports": recent_exports,
            "recent_logs": recent_logs,
        }

    def get_run_history(self):
        """Run History 표: Customer/Profile/Run ID/Execution Time/Status/Health Score/
        Device Count/Command Count/Report Count. 최신 run이 먼저 온다."""
        customer_name, profile_name = self._active_customer_profile()
        if not customer_name or not profile_name:
            return []
        rows = []
        for summary in rmgr.list_runs(customer_name, profile_name):
            run_id = summary["run_id"]
            try:
                run, _, metadata = rmgr.load_run(customer_name, profile_name, run_id)
            except RunManagerError:
                continue
            health = storage_service.load_json(run, "analysis/health_score.json", default=None)
            rows.append({
                "customer": customer_name, "profile": profile_name, "run_id": run_id,
                "execution_time": summary.get("start_time"), "status": summary.get("status"),
                "health_score": (health or {}).get("project_score"),
                "device_count": metadata.device_count, "command_count": metadata.command_count,
                "report_count": len(repmgr.list_reports(run)),
            })
        return rows

    def get_current_run_status(self):
        """진행 중(또는 가장 최근) Run의 상태 카드용 요약. Run이 하나도 없으면 None."""
        customer_name, profile_name = self._active_customer_profile()
        run = self._active_run()
        if run is None:
            return None
        try:
            _, session, metadata = rmgr.load_run(customer_name, profile_name, run.run_id)
        except RunManagerError:
            return None
        health = storage_service.load_json(run, "analysis/health_score.json", default=None)
        return {
            "customer": customer_name, "profile": profile_name, "run_id": run.run_id,
            "execution_time": session.start_time, "status": session.status,
            "progress": session.progress, "health_score": (health or {}).get("project_score"),
            "device_count": metadata.device_count, "command_count": metadata.command_count,
            "success_count": session.success_count, "failed_count": session.failed_count,
            "skipped_count": session.skipped_count, "report_count": len(repmgr.list_reports(run)),
        }

    # ---- Profile 조작(Archive만 신규 — Create/Rename/Delete/Copy는 이미 존재) -----------------

    def archive_profile(self, customer_name, profile_name):
        """정기점검 프로파일을 archive/로 옮긴다 — delete_inspection_profile()과 달리 복구 가능."""
        try:
            dst = prm.archive_profile(customer_name, profile_name)
        except FileNotFoundError as e:
            return {"error": str(e)}
        return {"ok": True, "path": str(dst)}

    # ---- 폴더 열기(engine.log_storage.open_in_file_explorer 재사용) --------------------------

    def open_workspace_folder(self):
        customer_name, profile_name = self._active_customer_profile()
        if not customer_name or not profile_name:
            return {"error": "활성 고객사/프로파일이 없습니다."}
        open_in_file_explorer(str(prm.get_profile(customer_name, profile_name).path))
        return {"ok": True}

    def open_reports_folder(self, run_id=None):
        run = self._active_run(run_id)
        if run is None:
            return {"error": "실행(run)이 없습니다."}
        open_in_file_explorer(str(run.reports_dir))
        return {"ok": True}

    def open_exports_folder(self, run_id=None):
        run = self._active_run(run_id)
        if run is None:
            return {"error": "실행(run)이 없습니다."}
        open_in_file_explorer(str(run.exports_dir))
        return {"ok": True}

    def open_logs_folder(self, run_id=None):
        run = self._active_run(run_id)
        if run is None:
            return {"error": "실행(run)이 없습니다."}
        open_in_file_explorer(str(run.raw_dir))
        return {"ok": True}

    def open_current_run_folder(self):
        run = self._active_run()
        if run is None:
            return {"error": "실행(run)이 없습니다."}
        open_in_file_explorer(str(run.path))
        return {"ok": True}

    def open_user_data_folder(self):
        """사용자 데이터 루트 폴더(Documents/AutoCheck)를 파일 탐색기로 연다."""
        from core.paths import AppPaths
        open_in_file_explorer(str(AppPaths.user_data_root()))
        return {"ok": True}

    # ---- 장시간 작업(리포트 생성/내보내기) — get_workspace_job_status()로 폴링 --------------------

    def start_workspace_report(self, format_id, run_id=None):
        """리포트 생성을 백그라운드로 실행하고 즉시 {"ok":True}를 반환한다."""
        if self._workspace_jobs().is_running("report"):
            return {"error": "이미 리포트 생성이 진행 중입니다."}
        run = self._active_run(run_id)
        if run is None:
            return {"error": "실행(run)이 없습니다."}

        def worker():
            self._workspace_jobs().set("report", total=1, current=0, message=f"{format_id} 생성 중...")
            path = repmgr.generate_report(run, format_id)
            self._workspace_jobs().set("report", current=1)
            return {"path": str(path)}

        self._workspace_jobs().start("report", worker)
        return {"ok": True}

    def start_workspace_export(self, export_kind, fmt="zip", run_id=None):
        """export_kind: logs/run/workspace. 백그라운드로 실행하고 즉시 {"ok":True}를 반환한다."""
        if self._workspace_jobs().is_running("export"):
            return {"error": "이미 내보내기가 진행 중입니다."}
        customer_name, profile_name = self._active_customer_profile()
        run = self._active_run(run_id)
        if run is None and export_kind in ("logs", "run"):
            return {"error": "실행(run)이 없습니다."}

        def worker():
            self._workspace_jobs().set("export", total=1, current=0, message=f"{export_kind} 내보내는 중...")
            if export_kind == "logs":
                path = repmgr.export_logs(run, fmt=fmt)
            elif export_kind == "run":
                path = repmgr.export_run(run)
            elif export_kind == "workspace":
                path = repmgr.export_workspace(prm.get_profile(customer_name, profile_name))
            else:
                raise ReportManagerError(f"알 수 없는 export_kind: {export_kind}")
            self._workspace_jobs().set("export", current=1)
            return {"path": str(path)}

        self._workspace_jobs().start("export", worker)
        return {"ok": True}
