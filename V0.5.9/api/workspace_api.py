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

    # ---- Workspace 스냅샷(단일 스캔) -------------------------------------------------
    #
    # 예전에는 get_workspace_overview() 가 get_run_history() 와 get_current_run_status() 를
    # 각각 불렀고, 세 메서드가 저마다 활성 고객사/프로파일을 해석하고 run 목록을 다시 읽었다.
    # 그 결과 run 하나당 session.json + metadata.json + health_score.json + 리포트 목록을
    # 여러 번 읽는 N+1 이 됐다(실측: run 20개 18.4 ms, 50개 50.6 ms). 워크스페이스 탭은
    # 폴링되므로(작업 중 1초 / 유휴 5초) 그 비용이 반복해서 든다.
    #
    # 이제 run 목록을 한 번만 훑어 필요한 것을 모두 모으고, 세 공개 메서드는 그것을 읽는다.
    # 공개 시그니처와 반환 형태는 그대로다(web_ui/js/workspace.js 가 셋 다 호출한다).

    def _workspace_snapshot(self):
        """이 요청에서 필요한 워크스페이스 데이터를 한 번의 스캔으로 모은다.

        반환: {customer, profile, rows, active_run, active_row} 또는 컨텍스트가 없으면 None.
          rows       : Run History 표 행(최신 먼저). load 실패한 run 은 빠진다.
          active_run : 진행 중인 run, 없으면 가장 최근 run, 그것도 없으면 None.
                       판정 규칙은 _active_run() 과 같아야 한다.
        """
        customer_name, profile_name = self._active_customer_profile()
        if not customer_name or not profile_name:
            return None

        # run 목록을 한 번만 읽는다. 진행 중 run 판정에도 이 요약을 그대로 넘긴다 —
        # 그러지 않으면 get_active_run() 이 session.json 을 전부 다시 읽는다(진행 중 run 이
        # 없으면 캐시가 비어 있어 매번 훑는데, 폴링 시 대개 그 상태다).
        summaries = rmgr.list_runs(customer_name, profile_name)

        # 진행 중인 run 이 목록의 '가장 최근'과 다를 수 있다(_active_run() 과 같은 우선순위).
        active_run = rmgr.get_active_run(customer_name, profile_name, summaries=summaries)

        rows = []
        handles = {}         # run_id -> RunHandle
        for summary in summaries:
            run_id = summary["run_id"]
            try:
                # load_run() 이 아니라 load_run_metadata() 를 쓴다 — list_runs() 가 이미 이 run 의
                # session.json 을 읽어 summary 를 만들었으므로, load_run() 을 부르면 같은 파일을
                # 두 번 읽는다. session 값은 summary 에 다 들어 있다(RunSession.to_dict()).
                run, metadata = rmgr.load_run_metadata(customer_name, profile_name, run_id)
            except RunManagerError:
                continue
            handles[run_id] = run
            health = storage_service.load_json(run, "analysis/health_score.json", default=None)
            reports = repmgr.list_reports(run)
            rows.append({
                "customer": customer_name, "profile": profile_name, "run_id": run_id,
                "execution_time": summary.get("start_time"), "status": summary.get("status"),
                "health_score": (health or {}).get("project_score"),
                "device_count": metadata.device_count, "command_count": metadata.command_count,
                "report_count": len(reports),
                # 아래 두 개는 내부용 — 공개 응답에서는 빼고 쓴다(get_run_history 의 계약 유지).
                "_summary": summary, "_reports": reports,
            })

        if active_run is None and rows:
            # 진행 중인 run 이 없으면 가장 최근 run. list_runs 는 최신순이므로 rows[0] 이다.
            active_run = handles[rows[0]["run_id"]]

        active_row = next((r for r in rows if active_run is not None
                           and r["run_id"] == active_run.run_id), None)
        return {
            "customer": customer_name, "profile": profile_name,
            "rows": rows, "active_run": active_run, "active_row": active_row,
        }

    @staticmethod
    def _public_row(row):
        """내부 필드(_summary/_reports)를 뺀 Run History 행."""
        return {key: value for key, value in row.items() if not key.startswith("_")}

    # ---- Workspace Information / Run History ---------------------------------------

    def get_workspace_overview(self):
        """'Workspace' 탭 전체 데이터 — Customer/Profile 컨텍스트, Run History,
        Current Run Status, Recent Reports/Exports/Logs를 한 번에 반환."""
        snapshot = self._workspace_snapshot()
        if snapshot is None:
            return {"error": "활성 고객사/프로파일이 없습니다."}

        latest_run = snapshot["active_run"]
        active_row = snapshot["active_row"]
        # 리포트 목록은 스냅샷이 이미 읽어 뒀다 — 활성 run 것이면 재사용한다.
        recent_reports = (active_row["_reports"] if active_row is not None
                          else (repmgr.list_reports(latest_run) if latest_run else []))[:10]
        recent_exports = repmgr.list_exports(latest_run)[:10] if latest_run else []
        recent_logs = lmgr.list_logs(latest_run, "raw")[:10] if latest_run else []

        return {
            "customer": snapshot["customer"], "profile": snapshot["profile"],
            "run_history": [self._public_row(row) for row in snapshot["rows"]],
            "current_run": self._current_run_from(snapshot),
            "recent_reports": recent_reports, "recent_exports": recent_exports,
            "recent_logs": recent_logs,
        }

    def get_run_history(self):
        """Run History 표: Customer/Profile/Run ID/Execution Time/Status/Health Score/
        Device Count/Command Count/Report Count. 최신 run이 먼저 온다."""
        snapshot = self._workspace_snapshot()
        if snapshot is None:
            return []
        rows = [self._public_row(row) for row in snapshot["rows"]]
        return rows

    def get_current_run_status(self):
        """진행 중(또는 가장 최근) Run의 상태 카드용 요약. Run이 하나도 없으면 None."""
        return self._current_run_from(self._workspace_snapshot())

    def _current_run_from(self, snapshot):
        """스냅샷에서 Current Run Status 카드를 만든다 — 파일을 다시 읽지 않는다."""
        if snapshot is None:
            return None
        run = snapshot["active_run"]
        if run is None:
            return None

        row = snapshot["active_row"]
        if row is None:
            # 활성 run 이 목록에 없는 드문 경우(방금 만들어져 아직 목록에 안 잡힌 run 등)
            # 에만 직접 읽는다 — 예전과 같은 결과를 내되, 흔한 경로에서는 일어나지 않는다.
            row = self._row_for_run(snapshot, run)
            if row is None:
                return None

        # session 값은 list_runs() 요약 dict 에서 그대로 온다 — session.json 재읽기 없음.
        summary = row["_summary"]
        return {
            "customer": snapshot["customer"], "profile": snapshot["profile"], "run_id": run.run_id,
            "execution_time": summary.get("start_time"), "status": summary.get("status"),
            "progress": summary.get("progress"), "health_score": row["health_score"],
            "device_count": row["device_count"], "command_count": row["command_count"],
            "success_count": summary.get("success_count"), "failed_count": summary.get("failed_count"),
            "skipped_count": summary.get("skipped_count"), "report_count": row["report_count"],
        }

    def _row_for_run(self, snapshot, run):
        """스냅샷에 없는 run 하나를 읽어 행 형태로 만든다(폴백 경로). 실패하면 None."""
        try:
            _, session, metadata = rmgr.load_run(snapshot["customer"], snapshot["profile"],
                                                 run.run_id)
        except RunManagerError:
            return None
        health = storage_service.load_json(run, "analysis/health_score.json", default=None)
        reports = repmgr.list_reports(run)
        return {
            "customer": snapshot["customer"], "profile": snapshot["profile"], "run_id": run.run_id,
            "execution_time": session.start_time, "status": session.status,
            "health_score": (health or {}).get("project_score"),
            "device_count": metadata.device_count, "command_count": metadata.command_count,
            "report_count": len(reports),
            # 다른 행과 같은 모양으로 맞춘다 — 소비하는 쪽(_current_run_from)이 dict 만 본다.
            "_summary": session.to_dict(), "_reports": reports,
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
