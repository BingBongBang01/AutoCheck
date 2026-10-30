"""ScheduleApiMixin — 워크스페이스 '무인 정기점검' 카드와 사이드바 실패 배지(IMPROVEMENT_PLAN 4-3).

활성 프로파일의 일정을 보여 주고 저장·삭제하며, 저장할 때 Windows 작업 스케줄러에 등록한다(꺼 두면 지운다).
'지금 실행'은 작업 스케줄러가 부르는 것과 같은 무인 점검(engine/scheduled_inspection)을 앱 안에서 돌리고 진행을
JobRunner 로 보여 준다. 실패 배지는 확인하지 않은 실패(모든 프로파일)를 센다.
"""
from api.job_runner import JobRunner

_KIND = "scheduled"


class ScheduleApiMixin:
    def _schedule_jobs(self):
        if not hasattr(self, "_schedule_job_runner"):
            self._schedule_job_runner = JobRunner((_KIND,))
        return self._schedule_job_runner

    def _schedule_view(self, project_id):
        from engine import inspection_schedule as store
        from engine import scheduled_inspection as runs
        from engine import task_scheduler
        from engine.inspection_report_builder import report_formats
        schedule = store.for_project(project_id)
        job = self._schedule_jobs().status(_KIND)
        return {
            "exists": schedule is not None,
            "schedule": schedule or dict(store.DEFAULT_SETTINGS),
            "description": store.describe(schedule),
            "next_run": task_scheduler.next_run_text(schedule),
            "registered": bool(schedule) and task_scheduler.is_registered(schedule["id"]),
            "scheduler_available": task_scheduler.available(),
            "running": job["status"] == "running" or runs.running_elsewhere(project_id),
            "job": job,
            "history": runs.history(project_id, limit=10),
            "formats": [{"id": fmt["id"], "label": store.FORMAT_LABELS.get(fmt["id"], fmt["id"])}
                        for fmt in report_formats()],
        }

    def get_inspection_schedule(self):
        """활성 프로파일의 무인 정기점검 — 일정·작업 스케줄러 등록 여부·다음 실행·진행 상태·최근 실행 이력."""
        try:
            project_id = self._project()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}
        return self._schedule_view(project_id)

    def save_inspection_schedule(self, settings):
        """일정 저장 — 켜 두면 작업 스케줄러에 등록하고(이미 있으면 바꾼다), 끄면 등록을 지운다. 등록이 실패해도
        일정은 저장된다 — 결과의 warning 에 이유를 싣고, 화면은 '등록되지 않음'으로 보여 준다."""
        from engine import inspection_schedule as store
        from engine import log_storage, task_scheduler
        try:
            project_id = self._project()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}
        try:
            schedule = store.upsert(project_id, settings)
        except store.ScheduleError as exc:
            return {"error": str(exc)}
        if schedule["enabled"]:
            customer, profile = log_storage.resolve_names_from_project(project_id)
            result = task_scheduler.register(
                schedule, f"AutoCheck 무인 정기점검 — {customer} / {profile} ({store.describe(schedule)})")
        else:
            result = task_scheduler.unregister(schedule["id"])
        view = self._schedule_view(project_id)
        if not result.get("ok"):
            view["warning"] = result.get("error")
        return {"ok": True, **view}

    def delete_inspection_schedule(self):
        """일정을 지운다 — 작업 스케줄러의 작업을 먼저 지우고, 그게 실패하면 일정도 남긴다(주인 없는 작업이 남지 않게)."""
        from engine import inspection_schedule as store
        from engine import task_scheduler
        try:
            project_id = self._project()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}
        schedule = store.for_project(project_id)
        if schedule is None:
            return {"error": "저장된 일정이 없습니다."}
        result = task_scheduler.unregister(schedule["id"])
        if not result.get("ok"):
            return {"error": result.get("error")}
        store.remove(project_id)
        return {"ok": True, **self._schedule_view(project_id)}

    def run_inspection_schedule_now(self):
        """'지금 실행' — 저장된 일정 그대로 한 번 돌린다(꺼진 일정도). 즉시 반환하고 진행은 get_inspection_schedule 의
        job 으로 본다. 장비에 실제로 접속한다."""
        from engine import inspection_schedule as store
        from engine import scheduled_inspection as runs
        try:
            project_id = self._project()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}
        schedule = store.for_project(project_id)
        if schedule is None:
            return {"error": "먼저 일정을 저장하세요."}
        if runs.running_elsewhere(project_id):
            return {"error": "이 프로파일의 무인 점검이 이미 진행 중입니다."}
        jobs = self._schedule_jobs()

        def on_progress(message, current=None, total=None):
            fields = {"message": message}
            if current is not None:
                fields["current"] = current
            if total:
                fields["total"] = total
            jobs.set(_KIND, **fields)

        def worker():
            record = runs.run_scheduled_inspection(schedule["id"], trigger="manual", on_progress=on_progress)
            if record["status"] == runs.FAILED:
                raise RuntimeError(record["message"])       # 진행 표시가 '오류'로 끝나게
            return record

        if not jobs.start(_KIND, worker):
            return {"error": "이 프로파일의 무인 점검이 이미 진행 중입니다."}
        return {"ok": True}

    def get_schedule_alerts(self):
        """사이드바 실패 배지 — 확인하지 않은 무인 점검 실패(모든 프로파일)의 수와 최근 것 몇 건."""
        from engine import inspection_schedule as store
        from engine import scheduled_inspection as runs
        failures = runs.unseen_failures(store.alerts_seen_at())
        keys = ("customer", "profile", "status", "message", "finished_at", "trigger")
        return {"count": len(failures), "items": [{key: item.get(key) for key in keys} for item in failures[:5]]}

    def acknowledge_schedule_alerts(self):
        """실패 배지 확인 — 지금까지의 실패는 더 세지 않는다(이력에는 그대로 남는다)."""
        from engine import inspection_schedule as store
        store.mark_alerts_seen()
        return {"ok": True}
