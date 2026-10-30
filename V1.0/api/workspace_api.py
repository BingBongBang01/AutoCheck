"""
WorkspaceApiMixin — 새 워크스페이스 아키텍처(ProfileManager/StorageService/RunManager/
LogManager/ReportManager)를 기존 GUI에 노출한다.

고객사/프로파일 선택 자체(생성/이름변경/삭제)는 이미 CustomerProfileApiMixin이 제공하므로
여기서는 재사용만 한다(_resolve_active_customer_profile_names()) — 이 클래스는 Api 합성 시
반드시 CustomerProfileApiMixin과 함께 조합돼야 한다. 여기서 새로 추가하는 건 Run History/
Workspace Information/Current Run Status/Recent Reports·Exports·Logs 조회, 폴더 열기,
Archive Profile, 그리고 리포트 생성·내보내기 같은 장시간 작업의 백그라운드 실행뿐이다.
리포트는 보고서 탭과 같은 정기점검 보고서 경로(engine/inspection_report_builder.export — 프로파일
reports/)를 쓴다. run 마다 리포트를 따로 두지 않는다.

장시간 작업은 JobRunner(api/job_runner.py — log_analysis_run_api.py와 동일 패턴)로 백그라운드
스레드에서 돌리고, get_workspace_job_status()로 폴링한다(web_ui/js/workspace.js 참고).
"""
from engine.profile_manager import profile_manager as prm
from engine.run_manager import CORRUPTED_STATUS, run_manager as rmgr, RunManagerError
from engine.log_manager import log_manager as lmgr
from engine.report_manager import report_manager as repmgr, ReportManagerError
from engine import inspection_report_builder as report_builder
from core.storage_service import storage_service
from engine.log_storage import open_in_file_explorer
from engine import workspace_backup, workspace_check
from api.job_runner import JobRunner
from api.realtime_watch_api import flush_realtime_state

# 워크스페이스 탭이 시작하는 작업. 예전 목록의 inspection/parsing/analysis 는 시작하는 곳이 없는 빈 슬롯이었다
# (세션 점검은 터미널 점검 API, 분석은 Log Analysis 탭의 슬롯을 쓴다).
_WORKSPACE_JOB_KINDS = ("report", "export")


class WorkspaceApiMixin:
    def _workspace_jobs(self) -> JobRunner:
        if not hasattr(self, "_workspace_job_runner"):
            self._workspace_job_runner = JobRunner(_WORKSPACE_JOB_KINDS)
        return self._workspace_job_runner

    # ---- 작업공간 점검·백업(IMPROVEMENT_PLAN 4-10 — engine/workspace_check.py·workspace_backup.py) -------------

    def check_workspace_integrity(self):
        """데이터 폴더가 앱이 기대하는 모양인지 — 고치지 않고 문제와 할 일만 돌려준다."""
        return {"ok": True, **workspace_check.check_workspace()}

    def create_workspace_backup(self):
        """'지금 백업' — 설정·프로파일 정의·보고서 기록을 zip 하나로(backups/, 최근 10개)."""
        try:
            return {"ok": True, **workspace_backup.create_backup(reason="manual")}
        except OSError as exc:
            return {"error": f"백업하지 못했습니다: {exc}"}

    def list_workspace_backups(self):
        return {"ok": True, "backups": workspace_backup.list_backups(), "keep": workspace_backup.KEEP}

    def open_workspace_backup_folder(self):
        open_in_file_explorer(str(workspace_backup.backup_root()))
        return {"ok": True}

    def get_workspace_job_status(self):
        """'Workspace' 탭 진행바 폴링용 — 리포트 생성·내보내기의 현재 상태와 완료 이력. 보고서는 대기열에
        쌓일 수 있어(연달아 누르면 차례로 만든다) 화면은 history 로 완료를 알린다."""
        return self._workspace_jobs().status_all(with_history=True)

    # ---- 활성 고객사/프로파일/run 해석(CustomerProfileApiMixin 재사용, 중복 없음) -------------

    def _active_customer_profile(self):
        return self._resolve_active_customer_profile_names()

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
    # 예전에는 get_workspace_overview() 가 run 이력·현재 run 상태를 따로 구하는 메서드를
    # 각각 불렀고, 세 메서드가 저마다 활성 고객사/프로파일을 해석하고 run 목록을 다시 읽었다.
    # 그 결과 run 하나당 session.json + metadata.json + health_score.json + 리포트 목록을
    # 여러 번 읽는 N+1 이 됐다(실측: run 20개 18.4 ms, 50개 50.6 ms). 워크스페이스 탭은
    # 폴링되므로(작업 중 1초 / 유휴 5초) 그 비용이 반복해서 든다.
    #
    # 이제 run 목록을 한 번만 훑어 필요한 것을 모두 모은다. 화면(web_ui/js/workspace.js)은
    # get_workspace_overview() 하나만 부른다(따로 부르는 곳이 없던 두 메서드는 3-2 에서 지웠다).

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
            if summary.get("status") == CORRUPTED_STATUS:
                # session.json 이 깨진 run — 표시만 한다(예전엔 이 run 하나가 탭 전체를 오류로 만들었다).
                rows.append({"customer": customer_name, "profile": profile_name, "run_id": run_id,
                             "execution_time": None, "status": CORRUPTED_STATUS, "health_score": None,
                             "device_count": None, "command_count": None,
                             "error": summary.get("error"), "_summary": summary})
                continue
            try:
                # load_run() 이 아니라 load_run_metadata() 를 쓴다 — list_runs() 가 이미 이 run 의
                # session.json 을 읽어 summary 를 만들었으므로, load_run() 을 부르면 같은 파일을
                # 두 번 읽는다. session 값은 summary 에 다 들어 있다(RunSession.to_dict()).
                run, metadata = rmgr.load_run_metadata(customer_name, profile_name, run_id)
            except RunManagerError:
                continue
            except Exception:
                run, metadata = rmgr.run_handle(customer_name, profile_name, run_id), None   # metadata.json 손상
            handles[run_id] = run
            try:
                health = storage_service.load_json(run, "analysis/health_score.json", default=None)
            except Exception:
                health = None
            rows.append({
                "customer": customer_name, "profile": profile_name, "run_id": run_id,
                "execution_time": summary.get("start_time"), "status": summary.get("status"),
                "health_score": (health or {}).get("project_score") if isinstance(health, dict) else None,
                "device_count": getattr(metadata, "device_count", None),
                "command_count": getattr(metadata, "command_count", None),
                # 내부용 — 공개 응답(run_history)에서는 뺀다(_public_row).
                "_summary": summary,
            })

        if active_run is None and handles:
            # 진행 중인 run 이 없으면 가장 최근(읽을 수 있는) run. list_runs 는 최신순이다.
            active_run = next(handles[row["run_id"]] for row in rows if row["run_id"] in handles)

        active_row = next((r for r in rows if active_run is not None
                           and r["run_id"] == active_run.run_id), None)
        return {
            "customer": customer_name, "profile": profile_name,
            "rows": rows, "active_run": active_run, "active_row": active_row,
        }

    @staticmethod
    def _public_row(row):
        """내부 필드(_summary)를 뺀 Run History 행."""
        return {key: value for key, value in row.items() if not key.startswith("_")}

    # ---- Workspace Information / Run History ---------------------------------------

    def get_workspace_overview(self):
        """'Workspace' 탭 전체 데이터 — Customer/Profile 컨텍스트, Run History,
        Current Run Status, Recent Reports/Exports/Logs를 한 번에 반환.
        리포트는 프로파일의 정기점검 보고서 목록(보고서 탭과 같은 목록)이다 — report_count 는 그 개수."""
        snapshot = self._workspace_snapshot()
        if snapshot is None:
            return {"error": "활성 고객사/프로파일이 없습니다."}

        latest_run = snapshot["active_run"]
        reports = report_builder.list_reports(snapshot["customer"], snapshot["profile"])
        recent_reports = [report["name"] for report in reports[:10]]
        recent_exports = repmgr.list_exports(latest_run)[:10] if latest_run else []
        recent_logs = lmgr.list_logs(latest_run, "raw")[:10] if latest_run else []

        return {
            "customer": snapshot["customer"], "profile": snapshot["profile"],
            "run_history": [self._public_row(row) for row in snapshot["rows"]],
            "current_run": self._current_run_from(snapshot),
            "recent_reports": recent_reports, "report_count": len(reports),
            "recent_exports": recent_exports, "recent_logs": recent_logs,
        }

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
            "skipped_count": summary.get("skipped_count"),
        }

    def _row_for_run(self, snapshot, run):
        """스냅샷에 없는 run 하나를 읽어 행 형태로 만든다(폴백 경로). 실패하면 None."""
        try:
            _, session, metadata = rmgr.load_run(snapshot["customer"], snapshot["profile"],
                                                 run.run_id)
        except RunManagerError:
            return None
        health = storage_service.load_json(run, "analysis/health_score.json", default=None)
        return {
            "customer": snapshot["customer"], "profile": snapshot["profile"], "run_id": run.run_id,
            "execution_time": session.start_time, "status": session.status,
            "health_score": (health or {}).get("project_score"),
            "device_count": metadata.device_count, "command_count": metadata.command_count,
            # 다른 행과 같은 모양으로 맞춘다 — 소비하는 쪽(_current_run_from)이 dict 만 본다.
            "_summary": session.to_dict(),
        }

    # ---- Profile 조작(Archive만 신규 — Create/Rename/Delete/Copy는 이미 존재) -----------------

    def archive_profile(self, customer_name, profile_name):
        """정기점검 프로파일을 보관한다(삭제와 같은 경로 — engine/profile_archive.py).
        예전엔 data 폴더만 옮겨서 labs 프로젝트가 목록에 그대로 남았고, 다음 접근에 빈 data 폴더가
        다시 생겼다. 이제 labs 쪽까지 함께 옮겨 목록에서 빠지고, 복원할 수 있다."""
        for customer in self.get_customer_profiles():
            if customer["name"].casefold() != (customer_name or "").casefold():
                continue
            profile = next((p for p in customer["profiles"]
                            if p["profile_name"].casefold() == (profile_name or "").casefold()), None)
            if profile:
                result = self._archive_profile_by_id(profile["id"])
                return {"ok": True, "path": result["archived"]} if result.get("ok") else result
        return {"error": f"프로파일이 없습니다: {customer_name}/{profile_name}"}

    # ---- 폴더 열기(engine.log_storage.open_in_file_explorer 재사용) --------------------------

    def open_workspace_folder(self):
        customer_name, profile_name = self._active_customer_profile()
        if not customer_name or not profile_name:
            return {"error": "활성 고객사/프로파일이 없습니다."}
        open_in_file_explorer(str(prm.get_profile(customer_name, profile_name).path))
        return {"ok": True}

    def open_reports_folder(self):
        """정기점검 보고서 폴더(프로파일 reports/) — 보고서 탭의 폴더 열기와 같은 곳."""
        customer_name, profile_name = self._active_customer_profile()
        if not customer_name or not profile_name:
            return {"error": "활성 고객사/프로파일이 없습니다."}
        open_in_file_explorer(str(report_builder.reports_dir(customer_name, profile_name)))
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

    # ---- 장시간 작업(리포트 생성/내보내기) — get_workspace_job_status()로 폴링 --------------------

    def start_workspace_report(self, format_id):
        """정기점검 보고서를 format_id(excel/pdf)로 백그라운드 생성하고 즉시 {"ok": True, "queued": …}를
        반환한다. 보고서 탭과 같은 경로(inspection_report_builder.export)·같은 폴더(프로파일 reports/)이고,
        점검일·담당자 같은 옵션은 프로파일 기본값을 쓴다(바꾸려면 보고서 탭). 앞 보고서가 만들어지는 중이면
        대기열에 넣어 차례로 만든다(queued=True). 예전엔 채점 결과(scored=[])로 빈 보고서를 run 의
        reports/ 에 만들었다."""
        if format_id not in {fmt["id"] for fmt in report_builder.report_formats()}:
            return {"error": f"지원하지 않는 보고서 형식입니다: {format_id}"}
        customer_name, profile_name = self._active_customer_profile()
        try:
            project_id = self._project()
        except RuntimeError:
            project_id = None
        if not customer_name or not profile_name or not project_id:
            return {"error": "활성 고객사/프로파일이 없습니다."}

        def worker():
            self._workspace_jobs().set("report", total=1, current=0, message=f"{format_id} 보고서 생성 중...")
            flush_realtime_state(self)
            result = report_builder.export(customer_name, profile_name, format_id, project_id=project_id)
            self._workspace_jobs().set("report", current=1)
            return {"path": result["path"]}

        started = self._workspace_jobs().start("report", worker, queue=True)
        return {"ok": True, "queued": started == "queued"}

    def start_workspace_export(self, export_kind, fmt="zip", run_id=None, include_credentials=False):
        """export_kind: logs/run/workspace. 백그라운드로 실행하고 즉시 {"ok":True}를 반환한다.
        workspace 는 계정 정보(credential.json·inventory/)를 기본으로 빼고, include_credentials=True 일
        때만 담는다 — 결과(results)의 notice/warning 을 화면이 완료 알림에 붙여 보여준다."""
        if self._workspace_jobs().is_running("export"):
            return {"error": "이미 내보내기가 진행 중입니다."}
        customer_name, profile_name = self._active_customer_profile()
        run = self._active_run(run_id)
        if run is None and export_kind in ("logs", "run"):
            return {"error": "실행(run)이 없습니다."}

        def worker():
            self._workspace_jobs().set("export", total=1, current=0, message=f"{export_kind} 내보내는 중...")
            results = {}
            if export_kind == "logs":
                path = repmgr.export_logs(run, fmt=fmt)
            elif export_kind == "run":
                path = repmgr.export_run(run)
            elif export_kind == "workspace":
                profile = prm.get_profile(customer_name, profile_name)
                skipped = [] if include_credentials else repmgr.credential_files(profile)
                path = repmgr.export_workspace(profile, include_credentials=bool(include_credentials))
                if include_credentials:
                    results["warning"] = ("계정 정보 파일(장비 목록 사본 inventory/·예전 credential.json)이 들어갔습니다 — "
                                          "내보낸 장비 목록 사본은 평문일 수 있으니 전달·보관에 주의하세요.")
                elif skipped:
                    shown = ", ".join(skipped[:3]) + (" 외" if len(skipped) > 3 else "")
                    results["notice"] = f"계정 정보가 담긴 파일 {len(skipped)}개는 뺐습니다({shown})."
            else:
                raise ReportManagerError(f"알 수 없는 export_kind: {export_kind}")
            self._workspace_jobs().set("export", current=1)
            return {"path": str(path), **results}

        self._workspace_jobs().start("export", worker)
        return {"ok": True}
