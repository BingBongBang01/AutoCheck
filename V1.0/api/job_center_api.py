"""JobCenterApiMixin — 상태바의 '작업 센터'(IMPROVEMENT_PLAN 4-9).

백그라운드 작업은 종류마다 JobRunner(api/job_runner.py)에 있다 — 세션 점검(모듈 공용), 무인 정기점검 '지금 실행',
분석 세 가지, 워크스페이스의 보고서·내보내기. 이 믹스인은 그것들을 한 목록으로 모아 상태바가 진행·취소·완료 이력을
한곳에서 보이게 한다. 작업을 시작하는 곳은 그대로 각 탭이고, 취소는 같은 JobRunner 에 요청한다(협조형 — 작업 함수가
다음 확인 지점에서 멈춘다).

멈출 수 없는 것: 무인 점검 '지금 실행'(수집기에 멈춤 신호가 없다), 보고서·내보내기(파일 하나를 쓰는 짧은 작업이라 끝까지
쓴 파일을 '취소됨'으로 적으면 거짓이 된다).
"""
import os

from api.terminal_inspection_api import STOP_MESSAGE, inspection_job_runner

_AI_STOP = "취소 요청됨 — 보내는 중인 요청이 끝나면 멈춥니다(분석한 파일은 남습니다)."
# (id, 묶음, 이름, 취소를 요청할 때 진행 로그에 남기는 말 — None 이면 멈출 수 없다). 화면에 이 순서로 보인다.
JOB_SOURCES = (
    ("inspection", "점검", "세션 점검", STOP_MESSAGE),
    ("scheduled", "점검", "무인 정기점검(지금 실행)", None),
    ("analysis.program", "분석", "규칙 기반 분석", "취소 요청됨 — 지금 파일을 마치고 멈춥니다(분석한 파일은 남습니다)."),
    ("analysis.local", "분석", "로컬 AI 분석", _AI_STOP),
    ("analysis.cloud", "분석", "클라우드 AI 분석", _AI_STOP),
    ("workspace.report", "보고서", "보고서 생성", None),
    ("workspace.export", "내보내기", "내보내기", None),
)
_RUNNER_GETTERS = {"scheduled": "_schedule_jobs", "analysis": "_jobs", "workspace": "_workspace_jobs"}
_HISTORY_SHOWN = 20


class JobCenterApiMixin:
    def _job_source(self, job_id):
        """작업 id -> (JobRunner, 종류). 이 Api 에 그 작업을 돌리는 믹스인이 없으면 None."""
        if job_id == "inspection":
            return inspection_job_runner(), "inspection"
        group, _, kind = job_id.partition(".")
        getter = getattr(self, _RUNNER_GETTERS.get(group, ""), None)
        return (getter(), kind or group) if callable(getter) else None

    def get_job_center(self):
        """상태바 작업 센터 — 지금 도는 작업과 최근 끝난 작업(종류를 넘어 최신순, 최대 20).

        반환: {jobs: [{id, group, label, current, total, message, elapsed_sec, eta_sec, queued, cancel_requested,
                       cancellable, last_log}], running, history: [{id, group, label, seq, status, error, start_ts,
                       end_ts, summary}]}. 결과 사본은 싣지 않고 한 줄 요약만 — 분석 결과 목록은 길다."""
        jobs, history = [], []
        for job_id, group, label, stop_note in JOB_SOURCES:
            source = self._job_source(job_id)
            if source is None:
                continue
            runner, kind = source
            state = runner.status(kind)
            if state["status"] == "running":
                jobs.append({"id": job_id, "group": group, "label": label,
                             **{key: state[key] for key in ("current", "total", "message", "elapsed_sec",
                                                            "eta_sec", "queued", "cancel_requested")},
                             "cancellable": stop_note is not None,
                             "last_log": state["log"][-1] if state["log"] else ""})
            for entry in runner.history(kind):
                history.append({"id": job_id, "group": group, "label": label, "seq": entry["seq"],
                                "status": entry["status"], "error": entry["error"],
                                "start_ts": entry.get("start_ts"), "end_ts": entry["end_ts"],
                                "summary": _summary(job_id, entry["results"])})
        history.sort(key=lambda entry: entry["end_ts"] or 0, reverse=True)
        return {"jobs": jobs, "running": len(jobs), "history": history[:_HISTORY_SHOWN]}

    def cancel_job(self, job_id):
        """작업 센터의 '취소' — 그 작업의 JobRunner 에 취소를 요청한다(대기열도 비운다)."""
        spec = next((s for s in JOB_SOURCES if s[0] == job_id), None)
        if spec is None:
            return {"error": "알 수 없는 작업입니다."}
        _id, _group, label, stop_note = spec
        if stop_note is None:
            return {"error": f"중간에 멈출 수 없는 작업입니다({label}) — 끝날 때까지 기다려 주세요."}
        source = self._job_source(job_id)
        if source is None or not source[0].request_cancel(source[1], stop_note):
            return {"error": f"진행 중인 작업이 없습니다({label})."}
        return {"ok": True}


def _summary(job_id, results):
    """끝난 작업의 한 줄 요약(결과 모양은 작업마다 다르다). 읽을 수 없는 모양이면 빈 문자열."""
    try:
        if job_id == "inspection":
            saved = (results or {}).get("saved")
            return f"장비 {saved}대 로그 저장" if saved is not None else ""
        if job_id == "scheduled":
            return str((results or {}).get("message") or "")
        if job_id == "analysis.program":
            files = results or []
            return f"파일 {len(files)}개 · 이상 {sum(f.get('problem_count') or 0 for f in files)}건"
        if job_id.startswith("analysis."):
            files = results or []
            stopped = any(f.get("stopped") for f in files)
            return f"파일 {len(files)}개 분석" + (" — 마지막 파일은 받은 파트까지" if stopped else "")
        if job_id.startswith("workspace."):
            path = (results or {}).get("path")
            return os.path.basename(path) if path else ""
    except (AttributeError, TypeError):
        return ""
    return ""
