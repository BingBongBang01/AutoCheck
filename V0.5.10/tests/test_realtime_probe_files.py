"""'CRT 로그 파일 진단' 모달의 삭제/폴더열기 — api/log_analysis_run_api.py.

CRTlog 폴더는 평평하다(하위 폴더 없음, core/crt_stream_watcher.py max_depth=1과 probe의
os.listdir() 비재귀 스캔이 그 전제 위에 있다). 그래서 다중 삭제는 파일명만 받고, 경로 조작을
막기 위해 os.path.basename()으로만 다룬다. 이 파일이 보는 것 세 가지:
  1. 정상 파일은 지워진다.
  2. txt/log가 아닌 확장자, 경로 조작 시도, 없는 파일은 에러로 보고되고 다른 요청을 막지 않는다.
  3. 지금 감시가 tail 중인 파일(watcher.status()의 active_paths)은 건너뛴다 — 오프셋이
     추적 중인 파일을 지우면 감시가 깨질 수 있어서다.
"""
import tempfile
from pathlib import Path

import pytest

from core.paths import AppPaths


@pytest.fixture
def api(monkeypatch):
    original = AppPaths._user_data_root
    AppPaths._user_data_root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths.forget_ensured()

    from api.log_analysis_run_api import LogAnalysisRunApiMixin

    class Stub(LogAnalysisRunApiMixin):
        pass

    yield Stub()

    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def make_file(name, content="line1\n"):
    root = AppPaths.crt_log_root()
    path = root / name
    path.write_text(content, encoding="utf-8")
    return path


class FakeWatcher:
    def __init__(self, active_paths=()):
        self._active_paths = list(active_paths)

    def status(self):
        return {"active_paths": self._active_paths}


def test_deletes_plain_files(api):
    make_file("Core1.txt")
    make_file("Core2.log")

    result = api.delete_realtime_log_files(["Core1.txt", "Core2.log"])
    assert sorted(result["deleted"]) == ["Core1.txt", "Core2.log"]
    assert result["errors"] == {}
    assert not (AppPaths.crt_log_root() / "Core1.txt").exists()
    assert not (AppPaths.crt_log_root() / "Core2.log").exists()


def test_rejects_disallowed_extension(api):
    make_file("Core1.txt")
    (AppPaths.crt_log_root() / "notes.md").write_text("x", encoding="utf-8")

    result = api.delete_realtime_log_files(["Core1.txt", "notes.md"])
    assert result["deleted"] == ["Core1.txt"]
    assert "notes.md" in result["errors"]
    assert (AppPaths.crt_log_root() / "notes.md").exists()


def test_path_traversal_is_confined_to_crt_log_root(api):
    """상위 경로 조작을 시도해도 basename만 남아 CRTlog 폴더 밖은 절대 건드리지 않는다."""
    outside = Path(tempfile.mkdtemp()) / "victim.txt"
    outside.write_text("secret", encoding="utf-8")

    result = api.delete_realtime_log_files(["../../../../victim.txt"])
    # basename("../../../../victim.txt") == "victim.txt" -> CRTlog 폴더 안에서 찾다가 없어서 에러.
    assert result["deleted"] == []
    assert "victim.txt" in result["errors"]
    assert outside.exists(), "CRTlog 폴더 밖의 파일이 지워지면 안 된다"


def test_missing_file_is_reported_not_raised(api):
    result = api.delete_realtime_log_files(["ghost.txt"])
    assert result["deleted"] == []
    assert "존재하지" in result["errors"]["ghost.txt"]


def test_tracked_file_is_skipped(api):
    """감시가 지금 tail 중인 파일은 지우지 않는다 — 감시를 먼저 멈추라고 안내한다."""
    path = make_file("Core1.txt")
    api._baseline_stream_watcher = FakeWatcher(active_paths=[str(path)])

    result = api.delete_realtime_log_files(["Core1.txt"])
    assert result["deleted"] == []
    assert "감시" in result["errors"]["Core1.txt"]
    assert path.exists()


def test_untracked_file_still_deletes_while_watching(api):
    """감시 중이어도 지금 추적하지 않는 다른 파일은 지울 수 있어야 한다."""
    tracked = make_file("Core1.txt")
    other = make_file("Core2.txt")
    api._baseline_stream_watcher = FakeWatcher(active_paths=[str(tracked)])

    result = api.delete_realtime_log_files(["Core1.txt", "Core2.txt"])
    assert result["deleted"] == ["Core2.txt"]
    assert "Core1.txt" in result["errors"]
    assert tracked.exists()
    assert not other.exists()


def test_open_folder_targets_crt_log_root(api, monkeypatch):
    opened = []
    from engine import log_storage
    monkeypatch.setattr(log_storage, "open_in_file_explorer", lambda path: opened.append(path))

    result = api.open_realtime_log_folder()
    assert result["ok"] is True
    assert opened == [str(AppPaths.crt_log_root())]
