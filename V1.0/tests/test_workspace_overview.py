"""WorkspaceApiMixin 단일 스캔 — OPTIMIZATION_PLAN 3-3.

예전에는 get_workspace_overview() 가 run 이력·현재 run 상태 메서드를 각각
불렀고, 세 메서드가 저마다 활성 컨텍스트를 해석하고 run 목록을 다시 읽어 run 하나당
session/metadata/health/리포트목록을 여러 번 읽었다(실측: run 20개 18.4 ms, 50개 50.6 ms).
워크스페이스 탭은 폴링되므로 그 비용이 반복해서 든다.

이 테스트가 확인하는 것:
  1. 반환 계약이 그대로다 — JS(web_ui/js/workspace.js)가 get_workspace_overview() 를 폴링한다.
  2. run 하나당 파일을 한 번만 읽는다(N+1 제거).
  3. 활성 run 판정 규칙이 바뀌지 않았다(진행 중 run > 가장 최근 run).
  4. 리포트는 프로파일의 정기점검 보고서 목록이고, '리포트 생성'은 보고서 탭과 같은 경로를 쓴다
     (IMPROVEMENT_PLAN 3-3 — 예전엔 run 마다 채점 데이터로 빈 보고서를 만들었다).
"""
import logging
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from core.paths import AppPaths

CUSTOMER, PROFILE = "고객사A", "2026-07"


@pytest.fixture
def workspace(monkeypatch):
    """임시 루트에 프로파일을 만들고 Api 대역(stub)을 돌려준다.

    전체 Api 를 조합하려면 16개 믹스인과 활성 프로젝트 상태가 필요하다. 여기서 보려는 것은
    WorkspaceApiMixin 의 스캔 로직뿐이므로, 그 믹스인이 의존하는 메서드
    (_resolve_active_customer_profile_names)만 갖춘 최소 클래스를 쓴다.
    """
    for name in ("storage_service", "run_manager", "profile_manager", "report_manager"):
        logging.getLogger(name).setLevel(logging.CRITICAL)

    original = AppPaths._user_data_root
    root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths._user_data_root = root
    AppPaths.forget_ensured()

    from api.workspace_api import WorkspaceApiMixin
    from engine.profile_manager import profile_manager as prm

    class Stub(WorkspaceApiMixin):
        def _resolve_active_customer_profile_names(self):
            return CUSTOMER, PROFILE

    prm.create_profile(CUSTOMER, PROFILE)
    yield Stub()

    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def make_runs(count):
    from core.storage_service import storage_service
    from engine.profile_manager import profile_manager as prm
    from engine.run_manager import run_manager as rmgr

    base = prm.profile_dir(CUSTOMER, PROFILE) / "runs"
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)

    created = []
    for _ in range(count):
        run = rmgr.create_run(CUSTOMER, PROFILE, device_count=20, command_count=15,
                              platform="arista_eos", execution_mode="ssh_collect")
        rmgr.start_run(run)
        rmgr.finish_run(run)
        storage_service.save_json(run, "analysis/health_score.json", {"project_score": 87.5})
        created.append(run)
    return created


# --------------------------------------------------------------------------- 반환 계약


def test_no_runs_returns_empty_shapes(workspace):
    overview = workspace.get_workspace_overview()
    assert overview["customer"] == CUSTOMER
    assert overview["profile"] == PROFILE
    assert overview["run_history"] == []
    assert overview["current_run"] is None
    assert overview["recent_reports"] == []


def test_run_history_shape_and_order(workspace):
    make_runs(3)
    rows = workspace.get_workspace_overview()["run_history"]
    assert len(rows) == 3

    expected_keys = {"customer", "profile", "run_id", "execution_time", "status",
                     "health_score", "device_count", "command_count"}
    for row in rows:
        assert set(row) == expected_keys, f"Run History 행의 필드가 바뀌었다: {sorted(row)}"
        assert row["device_count"] == 20
        assert row["command_count"] == 15
        assert row["health_score"] == 87.5

    # 최신 run 이 먼저 온다(run_id 는 타임스탬프 기반이라 내림차순).
    assert [r["run_id"] for r in rows] == sorted((r["run_id"] for r in rows), reverse=True)


def test_internal_fields_are_not_exposed(workspace):
    """스냅샷 내부 필드(_summary/_reports)가 UI 응답에 새어 나가면 안 된다.

    pywebview 브리지는 이 dict 를 JSON 으로 직렬화하므로, 내부용 필드가 섞이면 payload 가
    부풀고 UI 가 모르는 키를 받는다.
    """
    make_runs(2)
    overview = workspace.get_workspace_overview()
    for row in overview["run_history"]:
        assert not [key for key in row if key.startswith("_")]
    assert not [key for key in overview["current_run"] if key.startswith("_")]


def test_current_run_status_shape(workspace):
    make_runs(2)
    status = workspace.get_workspace_overview()["current_run"]
    expected_keys = {"customer", "profile", "run_id", "execution_time", "status", "progress",
                     "health_score", "device_count", "command_count", "success_count",
                     "failed_count", "skipped_count"}
    assert set(status) == expected_keys, f"Current Run 카드의 필드가 바뀌었다: {sorted(status)}"
    assert status["device_count"] == 20
    assert status["health_score"] == 87.5


# --------------------------------------------------------------------------- 활성 run 판정


def test_active_run_is_latest_when_none_running(workspace):
    runs = make_runs(3)
    latest_id = max(run.run_id for run in runs)
    assert workspace.get_workspace_overview()["current_run"]["run_id"] == latest_id


def test_running_run_wins_over_latest(workspace):
    """진행 중인 run 이 있으면 그것이 활성 run 이다 — 목록의 최신과 다를 수 있다.

    _active_run() 이 원래 갖고 있던 우선순위이고, 단일 스캔으로 바꿀 때 깨지기 쉬운 지점이다.
    """
    from engine.run_manager import run_manager as rmgr

    runs = make_runs(3)
    oldest = min(runs, key=lambda run: run.run_id)
    rmgr.start_run(oldest)          # 가장 오래된 run 을 RUNNING 으로 되돌린다

    active = rmgr.get_active_run(CUSTOMER, PROFILE)
    assert active is not None and active.run_id == oldest.run_id, "테스트 전제 확인"
    assert workspace.get_workspace_overview()["current_run"]["run_id"] == oldest.run_id


# --------------------------------------------------------------------------- N+1 제거


def test_overview_reads_each_json_file_once_per_run(workspace, monkeypatch):
    """**3-3 의 핵심 주장.** run 하나당 session.json / metadata.json 을 한 번씩만 읽는다.

    예전에는 두 겹으로 중복됐다:
      1) get_workspace_overview() 가 run 이력·현재 run 상태 메서드와 _active_run() 을 겹쳐
         부르면서 활성 run 을 여러 번 읽었다.
      2) list_runs() 가 이미 run 마다 session.json 을 읽는데 load_run() 이 또 읽었다
         (run 당 session.json 2회). 그래서 load_run 대신 load_run_metadata 를 쓴다.
    """
    from core import storage_service as storage_module

    make_runs(5)

    reads = []
    original = storage_module.storage_service.load_json

    def counting_load_json(target, rel_path, *args, **kwargs):
        reads.append(rel_path)
        return original(target, rel_path, *args, **kwargs)

    monkeypatch.setattr(storage_module.storage_service, "load_json", counting_load_json)

    workspace.get_workspace_overview()

    sessions = [r for r in reads if r.endswith("session.json")]
    metadata = [r for r in reads if r.endswith("metadata.json")]
    health = [r for r in reads if "health_score" in r]
    assert len(sessions) == 5, f"session.json 을 {len(sessions)}회 읽었다(기대 5)"
    assert len(metadata) == 5, f"metadata.json 을 {len(metadata)}회 읽었다(기대 5)"
    assert len(health) == 5, f"health_score.json 을 {len(health)}회 읽었다(기대 5)"


def test_overview_lists_reports_once_per_request(workspace, monkeypatch):
    """리포트는 프로파일 하나의 목록이다 — run 수와 상관없이 요청당 한 번 훑는다."""
    from api import workspace_api

    make_runs(5)

    calls = []
    original = workspace_api.report_builder.list_reports

    def counting_list_reports(customer, profile):
        calls.append((customer, profile))
        return original(customer, profile)

    monkeypatch.setattr(workspace_api.report_builder, "list_reports", counting_list_reports)

    workspace.get_workspace_overview()

    assert calls == [(CUSTOMER, PROFILE)], f"list_reports 호출: {calls}"


# --------------------------------------------------------------------------- 리포트 = 정기점검 보고서


def test_recent_reports_are_the_profile_inspection_reports(workspace):
    """'최근 리포트'는 보고서 탭과 같은 목록이다 — 프로파일 reports/ 와 예전 버전이 회차 폴더에 만든 보고서.
    예전엔 활성 run 의 reports/ 만 봐서 정기점검 보고서가 보이지 않았다."""
    import os
    from engine.profile_manager import profile_manager as prm

    runs = make_runs(1)
    reports = prm.profile_dir(CUSTOMER, PROFILE) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (runs[0].reports_dir / "old_round.xlsx").write_bytes(b"x")
    for index, name in enumerate(("a_report.xlsx", "b_report.pdf", "_snapshot.json", "notes.md")):
        (reports / name).write_bytes(b"x")
        os.utime(reports / name, (2_000_000_000 + index, 2_000_000_000 + index))

    overview = workspace.get_workspace_overview()
    assert overview["recent_reports"] == ["b_report.pdf", "a_report.xlsx", "old_round.xlsx"]
    assert overview["report_count"] == 3


def _wait_for_report_job(workspace):
    for _ in range(200):
        status = workspace.get_workspace_job_status()["report"]
        if status["status"] in ("done", "error"):
            return status
        time.sleep(0.01)
    raise AssertionError(f"리포트 작업이 끝나지 않았다: {status}")


def test_workspace_report_uses_the_inspection_report_path(workspace, monkeypatch):
    from api import workspace_api
    calls = []

    def fake_export(customer, profile, format_id, **kwargs):
        calls.append((customer, profile, format_id, kwargs))
        return {"path": "보고서.pdf"}

    monkeypatch.setattr(workspace_api.report_builder, "export", fake_export)
    workspace._project = lambda: "p1"

    assert "지원하지 않는" in workspace.start_workspace_report("docx")["error"]   # 예전 채점 포맷
    assert workspace.start_workspace_report("pdf") == {"ok": True, "queued": False}
    status = _wait_for_report_job(workspace)
    assert status["status"] == "done", status
    assert status["results"] == {"path": "보고서.pdf"}
    assert calls == [(CUSTOMER, PROFILE, "pdf", {"project_id": "p1"})]


def test_reports_requested_while_one_is_running_are_queued(workspace, monkeypatch):
    """Excel 을 만드는 중에 PDF 를 누르면 거절하지 않고 차례로 만든다 — 완료는 둘 다 이력에 남는다."""
    import threading
    from api import workspace_api
    release = threading.Event()
    made = []

    def slow_export(customer, profile, format_id, **kwargs):
        release.wait(2)
        made.append(format_id)
        return {"path": f"보고서.{format_id}"}

    monkeypatch.setattr(workspace_api.report_builder, "export", slow_export)
    workspace._project = lambda: "p1"
    assert workspace.start_workspace_report("excel") == {"ok": True, "queued": False}
    assert workspace.start_workspace_report("pdf") == {"ok": True, "queued": True}
    release.set()
    for _ in range(300):
        report = workspace.get_workspace_job_status()["report"]
        if report["finished"] == 2 and report["status"] != "running":
            break
        time.sleep(0.01)
    assert made == ["excel", "pdf"]
    assert [entry["results"]["path"] for entry in report["history"]] == ["보고서.excel", "보고서.pdf"]


def test_workspace_report_needs_an_active_profile(workspace):
    def no_project():
        raise RuntimeError("활성 프로젝트 없음")

    workspace._project = no_project
    assert "error" in workspace.start_workspace_report("excel")


def test_run_list_is_scanned_once_per_request(workspace, monkeypatch):
    """run 목록도 요청당 한 번만 훑는다 — 예전에는 세 메서드가 각자 다시 읽었다."""
    from engine import run_manager as run_manager_module

    make_runs(4)

    calls = []
    original = run_manager_module.run_manager.list_runs

    def counting_list_runs(customer, profile, *args, **kwargs):
        calls.append((customer, profile))
        return original(customer, profile, *args, **kwargs)

    monkeypatch.setattr(run_manager_module.run_manager, "list_runs", counting_list_runs)

    workspace.get_workspace_overview()
    assert len(calls) == 1, f"list_runs 가 {len(calls)}회 호출됐다"
