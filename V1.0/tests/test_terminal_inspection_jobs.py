"""세션 점검의 작업 관리 — IMPROVEMENT_PLAN 3-4.

세션 점검은 모듈 전역 dict+잠금으로 상태를 따로 관리했다. 작업 함수가 예외로 끝나면 'running' 이 영영
True 로 남아 다시 점검할 수 없었고, 화면은 중지와 오류를 '점검 완료'와 구분하지 못했다. 이제 JobRunner 의
'inspection' 슬롯을 쓴다 — 장비 세션 대신 세션별 점검 함수를 바꿔 끼워 흐름만 본다(실장비·SSH 없음).
"""
import tempfile
import time
from pathlib import Path

import pytest

from core.paths import AppPaths

C, P = "고객사A", "2026-11"


@pytest.fixture
def inspection(monkeypatch):
    original = AppPaths._user_data_root
    AppPaths._user_data_root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths.forget_ensured()
    from api import terminal_inspection_api as module
    from engine import command_catalog
    from engine.profile_manager import profile_manager as prm
    prm.create_profile(C, P)
    monkeypatch.setattr(command_catalog, "split_enabled_commands", lambda catalog: (["show version", "show clock"], []))
    monkeypatch.setitem(module._sessions, "s1", {"device": "Core1"})
    monkeypatch.setitem(module._sessions, "s2", {"device": "Agg1"})

    class Stub(module.TerminalInspectionApiMixin):
        def _paths(self):
            return {"commands_catalog": None, "lab_meta": None}

        def _project(self):
            return "p1"

        def _load_catalog(self, paths):
            return []

        def _resolve_active_customer_profile_names(self):
            return C, P

        def _refresh_realtime_baseline_after_inspection(self):
            return {"ok": False, "error": "테스트 — 감시 꺼짐"}

        def _sync_session_name(self, session_id, settled=False):
            return None

    yield module, Stub()
    wait_done(Stub())
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def wait_done(api, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = api.get_terminal_inspection_status()
        if not status["running"]:
            return status
        time.sleep(0.01)
    raise AssertionError("점검이 끝나지 않았다")


def fake_session(module, *, wait_for_cancel=False):
    """세션 하나를 점검한 척 — 명령 수만큼 진행을 올리고 원본 로그를 남긴다."""
    def run(session_id, commands, results_dirs, sync_name=None):
        device = module._sessions[session_id]["device"]
        if wait_for_cancel:
            deadline = time.time() + 3
            while not module._cancel_requested() and time.time() < deadline:
                time.sleep(0.005)
        module._inspection_jobs.advance(module._KIND, len(commands))
        for directory in results_dirs:
            Path(directory, f"20261115_090000_raw_{device}.txt").write_text(f"{device}#show version\n", encoding="utf-8")
        module._log(f"[{device}] 결과 저장됨")
    return run


def run_status(module):
    from engine.run_manager import RunManager
    return [summary["status"] for summary in RunManager().list_runs(C, P)]


def test_finished_inspection_reports_progress_and_completes_the_run(inspection, monkeypatch):
    module, api = inspection
    monkeypatch.setattr(module, "_run_one_session_inspection", fake_session(module))
    assert api.run_terminal_inspection(["s1", "s2"]) == {"ok": True}
    status = wait_done(api)
    assert status["done"] and status["status"] == "done" and status["error"] is None
    assert status["total"] == 4 and status["current"] == 4
    assert status["log"][0].startswith("점검 시작") and status["log"][-1] == "전체 점검 완료"
    assert run_status(module) == ["COMPLETED"]


def test_stop_ends_as_cancelled_and_keeps_what_was_collected(inspection, monkeypatch):
    module, api = inspection
    monkeypatch.setattr(module, "_run_one_session_inspection", fake_session(module, wait_for_cancel=True))
    assert api.stop_terminal_inspection() == {"error": "진행 중인 점검이 없습니다."}
    api.run_terminal_inspection(["s1"])
    assert api.run_terminal_inspection(["s1"]) == {"error": "이미 점검이 진행 중입니다."}
    assert api.stop_terminal_inspection() == {"ok": True}
    status = wait_done(api)
    assert status["done"] and status["status"] == "cancelled"
    assert "중지 요청됨 — 진행 중인 커맨드 완료 후 저장합니다." in status["log"]
    assert run_status(module) == ["ABORTED"]                                  # 중지된 회차, 결과 파일은 남는다


def test_a_crashing_worker_does_not_leave_the_inspection_stuck(inspection, monkeypatch):
    import glob
    import types
    module, api = inspection
    monkeypatch.setattr(module, "_run_one_session_inspection", fake_session(module))

    def broken_glob(pattern):
        raise OSError("저장 폴더를 읽을 수 없다")

    monkeypatch.setattr(module, "glob", types.SimpleNamespace(glob=broken_glob))   # 이 모듈의 참조만 바꾼다
    api.run_terminal_inspection(["s1"])
    status = wait_done(api)
    assert status["done"] and status["status"] == "error" and "저장 폴더" in status["error"]

    monkeypatch.setattr(module, "glob", glob)
    assert api.run_terminal_inspection(["s1"]) == {"ok": True}               # 예전엔 '이미 진행 중'에 막혔다
    assert wait_done(api)["status"] == "done"
