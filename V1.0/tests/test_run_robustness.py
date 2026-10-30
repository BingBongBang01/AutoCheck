"""Run 관리 견고화 — IMPROVEMENT_PLAN 2-4.

  1. session.json·metadata.json·health_score.json 하나가 깨져도 Workspace 탭 전체가 오류가 나지 않는다.
     깨진 run 은 목록에 CORRUPTED 로만 보인다.
  2. 앱이 실행 도중 끝나 RUNNING/PAUSED 로 남은 run 은 다음 시작에서 ABORTED 로 정리된다 —
     예전엔 정리 함수가 어디에도 연결되지 않아 영구히 '현재 Run' 으로 붙어 있었다.
  3. 같은 초에 시작한 두 실행이 한 run 폴더를 함께 쓰지 않는다.
"""
import json
import tempfile
from pathlib import Path

import pytest

from core.paths import AppPaths

C, P = "고객사A", "2026-10"


@pytest.fixture
def root():
    original = AppPaths._user_data_root
    AppPaths._user_data_root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths.forget_ensured()
    from engine.profile_manager import profile_manager as prm
    prm.create_profile(C, P)
    yield AppPaths.data_root()
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


@pytest.fixture
def rm():
    from engine.run_manager import RunManager
    return RunManager()


def make_run(rm, mode=None, status=None):
    run = rm.create_run(C, P, execution_mode=mode)
    if status == "RUNNING":
        rm.start_run(run)
    elif status == "COMPLETED":
        rm.start_run(run)
        rm.finish_run(run)
    return run


def test_corrupted_session_is_listed_not_fatal(root, rm):
    good = make_run(rm, status="COMPLETED")
    bad = make_run(rm, status="COMPLETED")
    (bad.path / "session.json").write_text("{ 깨진 json", encoding="utf-8")
    summaries = {s["run_id"]: s for s in rm.list_runs(C, P)}
    assert summaries[good.run_id]["status"] == "COMPLETED"
    assert summaries[bad.run_id]["status"] == "CORRUPTED" and "JSONDecodeError" in summaries[bad.run_id]["error"]
    assert rm.get_active_run(C, P) is None


def test_workspace_overview_survives_broken_run_files(root, monkeypatch):
    from api.workspace_api import WorkspaceApiMixin
    from engine import run_manager as run_manager_module
    rm = run_manager_module.run_manager
    older = make_run(rm, status="COMPLETED")
    broken_meta = make_run(rm, status="COMPLETED")
    broken_session = make_run(rm, status="COMPLETED")
    (broken_meta.path / "metadata.json").write_text("not json", encoding="utf-8")
    (broken_meta.path / "analysis" / "health_score.json").write_text("{", encoding="utf-8")
    (broken_session.path / "session.json").write_text("", encoding="utf-8")

    class Stub(WorkspaceApiMixin):
        def _resolve_active_customer_profile_names(self):
            return C, P

    overview = Stub().get_workspace_overview()
    assert "error" not in overview
    rows = {row["run_id"]: row for row in overview["run_history"]}
    assert rows[broken_session.run_id]["status"] == "CORRUPTED"
    assert rows[broken_meta.run_id]["device_count"] is None
    assert rows[older.run_id]["status"] == "COMPLETED"


def test_interrupted_runs_are_closed_at_startup(root, rm):
    from engine.run_manager import RunManager
    interrupted = make_run(rm, status="RUNNING")
    done = make_run(rm, status="COMPLETED")

    fresh = RunManager()                       # 다음 시작 — 이 프로세스가 돌리는 run 은 없다
    assert fresh.get_active_run(C, P).run_id == interrupted.run_id   # 정리 전: '현재 Run' 에 붙어 있다
    fresh = RunManager()
    recovered = fresh.recover_all_incomplete_runs()
    assert [r["run_id"] for r in recovered] == [interrupted.run_id]
    assert fresh.get_active_run(C, P) is None
    session = json.loads((interrupted.path / "session.json").read_text(encoding="utf-8"))
    assert session["status"] == "ABORTED" and session["end_time"]
    metadata = json.loads((interrupted.path / "metadata.json").read_text(encoding="utf-8"))
    assert "도중 종료" in metadata["failure_reason"]
    assert json.loads((done.path / "session.json").read_text(encoding="utf-8"))["status"] == "COMPLETED"


def test_startup_cleanup_leaves_this_process_runs_and_creates_nothing(root, rm):
    running_here = make_run(rm, status="RUNNING")
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_dir())
    assert rm.recover_all_incomplete_runs() == []
    assert rm.get_active_run(C, P).run_id == running_here.run_id
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_dir()) == before


def test_two_runs_in_the_same_second_get_their_own_folders(root, monkeypatch):
    from core import utils
    from engine.profile_manager import profile_manager as prm
    from core.storage_service import storage_service
    monkeypatch.setattr(utils, "format_run_id", lambda: "2026-10-27_120000")
    profile = prm.get_profile(C, P)
    first, second = storage_service.create_run(profile), storage_service.create_run(profile)
    assert first.path != second.path and first.path.is_dir() and second.path.is_dir()
