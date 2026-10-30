"""원본로그분석·마스킹·보고서에서 '언제 점검한 로그'를 쓸지 고른다.

예전엔 원본로그분석·마스킹은 늘 가장 최근 점검 회차만, 보고서는 모든 회차에서 장비마다 가장 최근 로그만 썼다 —
지난 점검을 다시 분석하거나 그 점검으로 보고서를 다시 만들 길이 없었다. 이제 회차(run_id)를 고른다(실장비·SSH 없음).
"""
import tempfile
from pathlib import Path

import pytest

from core.paths import AppPaths
from engine import log_storage

C, P = "고객사A", "2026-11"
OLD, NEW, EMPTY = "2026-10-28_090000", "2026-10-30_101638", "2026-10-30_120000"
LOG = "Core1#show version\nArista DCS-7050SX3-48YC8\nCore1#show logging\n"


@pytest.fixture
def profile():
    original = AppPaths._user_data_root
    AppPaths._user_data_root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths.forget_ensured()
    from engine.profile_manager import profile_manager as prm
    prm.create_profile(C, P)
    pdir = Path(log_storage.get_profile_dir(C, P))
    for run_id, devices in ((OLD, ("Core1", "Agg1")), (NEW, ("Core1",)), (EMPTY, ())):
        raw = pdir / "runs" / run_id / "raw"
        raw.mkdir(parents=True)
        for device in devices:
            stamp = run_id.replace("-", "")
            (raw / f"{stamp}_raw_{device}.txt").write_text(LOG.replace("Core1", device), encoding="utf-8")
    yield pdir
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def test_runs_with_logs_are_listed_newest_first(profile):
    runs = log_storage.list_log_runs(C, P)
    assert [(run["run_id"], run["log_count"]) for run in runs] == [(NEW, 1), (OLD, 2)]   # 로그 없는 회차는 뺀다
    assert runs[0]["label"] == "2026-10-30 10:16:38"


def test_a_chosen_run_or_the_latest(profile):
    assert log_storage.get_run_log_paths(C, P, OLD)["run_id"] == OLD
    assert log_storage.get_run_log_paths(C, P)["run_id"] == EMPTY            # 고르지 않으면 가장 최근 회차(예전 그대로)
    assert log_storage.get_run_log_paths(C, P, "2099-01-01_000000") is None
    created = log_storage.get_run_log_paths(C, P, OLD, create=True)
    assert Path(created["problem"]).is_dir() and Path(created["masking"]).is_dir()


class _Api:
    """화면 API 조합 — 활성 고객사/프로파일만 정해 둔다."""

    def __new__(cls):
        from api.inspection_report_api import InspectionReportApiMixin
        from api.log_analysis_run_api import LogAnalysisRunApiMixin
        from api.log_file_browser_api import LogFileBrowserApiMixin
        from api.masking_api import MaskingApiMixin

        class Api(LogFileBrowserApiMixin, LogAnalysisRunApiMixin, MaskingApiMixin, InspectionReportApiMixin):
            def _active_names(self):
                return C, P

            def _resolve_active_customer_profile_names(self):
                return C, P

            def _project(self):
                return None

            def _paths(self):
                return {}

            def _load_inventory(self, paths):
                return {"defaults": {}, "devices": []}

        return Api()


def test_masking_preview_reads_the_chosen_run(profile):
    api = _Api()
    assert api.preview_log_masking("original", [], run_id=OLD)["files"] == [
        "20261028_090000_raw_Agg1.txt", "20261028_090000_raw_Core1.txt"]
    assert "없습니다" in api.preview_log_masking("original", [], run_id=EMPTY)["error"]   # 고른 회차에 로그가 없다
    assert "찾을 수 없습니다" in api.preview_log_masking("original", [], run_id="2099-01-01_000000")["error"]


def test_analysis_refuses_a_run_that_is_gone(profile):
    assert "찾을 수 없습니다" in _Api().start_log_analysis("2099-01-01_000000")["error"]


def test_report_uses_only_the_chosen_run(profile):
    api = _Api()
    chosen = api.get_inspection_report_context(OLD)
    assert {d["name"] for d in chosen["devices"]} == {"Core1", "Agg1"} and chosen["run_id"] == OLD
    assert chosen["original_log_dir"].endswith(str(Path("runs") / OLD / "raw"))
    latest_only = api.get_inspection_report_context(NEW)
    assert [d["name"] for d in latest_only["devices"] if not d["unreachable"]] == ["Core1"]
    every_run = api.get_inspection_report_context()           # 고르지 않으면 장비마다 가장 최근 로그(모든 회차)
    assert {d["name"] for d in every_run["devices"] if not d["unreachable"]} == {"Core1", "Agg1"}
    assert "찾을 수 없습니다" in api.get_inspection_report_context("2099-01-01_000000")["error"]
