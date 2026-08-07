"""실시간 감시는 '지금 들어오는 세션 로그'만 봐야 한다 — 점검 결과 폴더를 보면 안 된다.

보고된 사고: 5일간 사용한 워크스페이스에서 실시간 감시가 만든 경고가 딱 1건이었고, 그것이
오탐이었다.

    Access1 CRITICAL DESTRUCTIVE_COMMAND  "위험 명령 실행 감지: Reload Cause:"

`Reload Cause:` 는 `show reload cause` **출력의 머리글**이고, CRTlog(수동 SecureCRT 세션
로그)에는 한 번도 등장하지 않는 문자열이다 — 점검 결과 폴더(runs/<run>/raw)에만 있다.
원인은 refresh_realtime_baseline_after_inspection() 이 점검이 끝날 때마다

    watcher.set_watch_dir(paths["original"])

로 감시 대상을 점검 결과 폴더로 옮기고, **되돌리는 코드가 없었던** 것이다. 그래서
  * 작업자의 SecureCRT 세션 감시가 앱 재시작 때까지 영구히 멈췄고,
  * 오프셋이 초기화되어 점검 출력의 끝부분이 '지금 들어온 입력'으로 재판정됐고,
  * 화면은 계속 CRTlog 를 감시 중이라고 표시해서 원인을 감췄다.

이 파일이 보는 것:
  1. 점검 결과 파일은 순회 자체에서 걸러진다(폴더를 잘못 지정해도 막히는 2차 방어).
  2. 점검이 끝나도 감시 폴더와 판정 문맥(StateTracker)은 그대로다.
  3. 화면이 보고하는 watch_dir 은 상수가 아니라 감시가 실제로 보는 폴더다.
"""
import tempfile
from pathlib import Path

import pytest

from core.crt_stream_watcher import CRTStreamWatcher, iter_session_log_files
from core.paths import AppPaths


@pytest.fixture
def api():
    original = AppPaths._user_data_root
    AppPaths._user_data_root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths.forget_ensured()

    from api.log_analysis_run_api import LogAnalysisRunApiMixin
    from engine.profile_manager import profile_manager as prm

    class FakeStore:
        source_kind = "inspection"

        def device_names(self):
            return ["Core1"]

    class Stub(LogAnalysisRunApiMixin):
        def resolve_active_customer_profile_names(self):
            return ("고객사A", "2026-07")

        def _baseline_store(self):
            return FakeStore()

        def load_realtime_baseline(self):
            return {"ok": True, "loaded": 1}

    prm.create_profile("고객사A", "2026-07")
    yield Stub()

    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


# ---------- 1. 순회가 점검 결과 파일을 걸러낸다 ----------

def test_inspection_result_files_are_never_watched(tmp_path):
    """{stamp}_raw_{device}.txt 는 지나간 스냅샷이다 — 라이브 입력으로 판정되면 안 된다."""
    (tmp_path / "20260807_095325_raw_Access1.txt").write_text("Reload Cause:\n", encoding="utf-8")
    (tmp_path / "192.168.205.101_20260807_101307.txt").write_text("Core1#\n", encoding="utf-8")

    found = {Path(p).name for p in iter_session_log_files(tmp_path)}
    assert found == {"192.168.205.101_20260807_101307.txt"}


def test_watcher_skips_inspection_results_even_if_pointed_at_them(tmp_path):
    """감시 폴더를 점검 결과 폴더로 잘못 지정해도 아무것도 흘러가지 않는다."""
    (tmp_path / "20260807_095325_raw_Core1.txt").write_text(
        "Core1(config)#show reload cause\nReload Cause:\n", encoding="utf-8")

    seen = []
    watcher = CRTStreamWatcher(tmp_path, lambda *a, **k: seen.append(a),
                               device_resolver=lambda path, head="": "Core1",
                               catch_up=True)
    watcher._tick()
    assert seen == []
    assert watcher.status()["tracked_files"] == 0


def test_subdirectories_are_walked_one_level(tmp_path):
    """SecureCRT 를 세션별 하위 폴더로 로깅하는 환경 — 감시와 진단이 같은 목록을 봐야 한다."""
    (tmp_path / "top.log").write_text("x", encoding="utf-8")
    nested = tmp_path / "session1"
    nested.mkdir()
    (nested / "inner.txt").write_text("x", encoding="utf-8")
    deep = nested / "deeper"
    deep.mkdir()
    (deep / "toofar.txt").write_text("x", encoding="utf-8")

    found = {Path(p).name for p in iter_session_log_files(tmp_path)}
    assert found == {"top.log", "inner.txt"}


# ---------- 2. 점검이 끝나도 감시 대상과 문맥은 그대로 ----------

class FakeWatcher:
    interval = 0.3

    def __init__(self, watch_dir):
        self.watch_dir = str(watch_dir)
        self.set_calls = []

    def is_running(self):
        return True

    def set_watch_dir(self, path):
        self.set_calls.append(str(path))
        self.watch_dir = str(path)

    def status(self):
        return {}


def test_inspection_does_not_redirect_the_watcher(api):
    crt_root = str(AppPaths.crt_log_root())
    api._baseline_stream_watcher = FakeWatcher(crt_root)

    result = api.refresh_realtime_baseline_after_inspection()

    assert result["ok"] is True and result["running"] is True
    assert api._baseline_stream_watcher.set_calls == []
    assert api._baseline_stream_watcher.watch_dir == crt_root


def test_inspection_keeps_open_conditions_resolvable(api):
    """reset_context() 를 부르면 열려 있던 장애가 '취소 불가'가 된다 — 나중에 no shutdown 을
    쳐도 해제되지 않고 CRITICAL 이 화면에 영구히 남는다."""
    from engine.baseline_diff_engine import BaselineDiffEngine
    from engine.baseline_store import BaselineStore

    engine = BaselineDiffEngine(BaselineStore())
    api._baseline_diff_engine = engine
    api._baseline_stream_watcher = FakeWatcher(AppPaths.crt_log_root())

    engine.analyze_stream("Core1", "Core1(config)#interface Ethernet1\n"
                                   "Core1(config-if-Et1)#shutdown\n")
    assert len(engine.open_conditions("Core1")) == 1

    api.refresh_realtime_baseline_after_inspection()
    assert len(engine.open_conditions("Core1")) == 1, "점검이 열린 장애 추적을 지워선 안 된다"

    engine.analyze_stream("Core1", "Core1(config-if-Et1)#no shutdown\n")
    assert engine.drain_resolutions(), "점검 후에도 복구 이벤트가 경고를 취소할 수 있어야 한다"


# ---------- 3. 화면이 보고하는 폴더는 실측값 ----------

def test_state_reports_the_folder_actually_watched(api):
    other = Path(tempfile.mkdtemp()) / "elsewhere"
    other.mkdir(parents=True)
    api._baseline_stream_watcher = FakeWatcher(other)

    assert api._realtime_watch_dir() == str(other)
    assert api.get_realtime_monitor_state()["watch_dir"] == str(other)
    assert api.get_realtime_baseline_status()["watch_dir"] == str(other)


def test_state_falls_back_to_crt_root_when_not_watching(api):
    assert api._realtime_watch_dir() == str(AppPaths.crt_log_root())


# ---------- 4. 진단 목록과 삭제의 경계 ----------

def test_probe_lists_subdir_files_and_delete_explains_the_limit(api):
    root = AppPaths.crt_log_root()
    (root / "Core1.txt").write_text("Core1#\n", encoding="utf-8")
    nested = root / "session1"
    nested.mkdir()
    (nested / "Core2.txt").write_text("Core2#\n", encoding="utf-8")

    rows = {r["rel"]: r for r in api.probe_realtime_log_files()["files"]}
    assert set(rows) == {"Core1.txt", "session1/Core2.txt"}
    assert rows["session1/Core2.txt"]["in_subdir"] is True
    assert rows["Core1.txt"]["in_subdir"] is False

    result = api.delete_realtime_log_files(["Core2.txt"])
    assert result["deleted"] == []
    assert "하위 폴더" in result["errors"]["Core2.txt"]
    assert (nested / "Core2.txt").exists()
