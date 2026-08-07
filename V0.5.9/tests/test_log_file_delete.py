"""'점검 로그' 삭제 버튼이 실제로 파일을 지우는지.

보고된 증상: 삭제 버튼을 눌러도 파일이 지워지지 않고, 오류 메시지도 뜨지 않았다.

원인은 delete_log_files() 가 지울 목록을 만드는 도중 부르는 _derived_output_paths() 안의
정의되지 않은 변수(`body`)였다. 그래서
  * targets 를 만들다 NameError 가 터져 os.remove 가 **한 번도** 불리지 않았고,
  * 예외가 JS 브릿지까지 올라가 호출부(await 한 줄)가 그 자리에서 중단됐으므로
    목록 갱신도, 오류 alert 도 실행되지 않았다 — 아무 일도 안 일어난 것처럼 보였다.
이 함수는 활성 프로파일에 masked/ 폴더가 있을 때만 그 줄에 도달하므로, 마스킹을 한 번이라도
돌린 프로파일에서만 재현됐다.

그래서 이 파일은 결과만 보지 않고 **부수 작업이 본래 요청을 삼키지 않는지**까지 본다.
"""
import tempfile
from pathlib import Path

import pytest

from core.paths import AppPaths

CUSTOMER, PROFILE = "고객사A", "2026-07"
RAW_NAME = "20260807_095325_raw_Core1.txt"


@pytest.fixture
def api():
    original = AppPaths._user_data_root
    AppPaths._user_data_root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths.forget_ensured()

    from api.log_file_browser_api import LogFileBrowserApiMixin
    from engine.profile_manager import profile_manager as prm

    class Stub(LogFileBrowserApiMixin):
        def _project(self):
            return "proj"

        def resolve_active_customer_profile_names(self):
            return (CUSTOMER, PROFILE)

    prm.create_profile(CUSTOMER, PROFILE)
    yield Stub()

    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


@pytest.fixture
def run_dirs(api):
    """masked/ 가 존재하는 run 하나 — 이것이 버그 재현 조건이었다."""
    from engine import log_storage

    root = Path(log_storage.get_profile_dir(CUSTOMER, PROFILE)) / "runs" / "2026-08-07_095306"
    dirs = {}
    for key, name in (("raw", "raw"), ("problem", "problem"), ("masked", "masked")):
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        dirs[key] = path
    return dirs


def write(path, text="log\n"):
    path.write_text(text, encoding="utf-8")
    return path


# ---------- 원인이 된 헬퍼 ----------

def test_derived_output_paths_does_not_raise(api, run_dirs):
    """masked/ 폴더가 있으면 이 함수가 NameError 로 터졌다 — 삭제가 시작조차 못 했다."""
    paths = api._derived_output_paths(RAW_NAME)
    assert paths, "masked/ 가 있으면 파생 경로가 하나 이상 나와야 한다"


def test_derived_paths_use_the_masking_naming_rule(api, run_dirs):
    """engine/log_masking.py 는 '확장자를 뗀 원본 이름 + _masked.txt' 로 저장한다."""
    names = {Path(p).name for p in api._derived_output_paths(RAW_NAME)}
    assert "20260807_095325_raw_Core1_masked.txt" in names
    # 분석 결과(problem/*)도 함께 대상이 된다.
    assert "RuleCheck_20260807_095325_Core1_problems.txt" in names


# ---------- 삭제가 실제로 일어난다 ----------

def test_delete_removes_the_requested_file(api, run_dirs):
    target = write(run_dirs["raw"] / RAW_NAME)

    result = api.delete_log_files([str(target)])

    assert result["errors"] == {}
    assert result["deleted"] == [str(target)]
    assert not target.exists()


def test_delete_also_removes_derived_outputs(api, run_dirs):
    """원본이 사라지면 그 분석·마스킹 결과도 같이 사라져야 한다 — 남으면 유령 결과가 된다."""
    target = write(run_dirs["raw"] / RAW_NAME)
    masked = write(run_dirs["masked"] / "20260807_095325_raw_Core1_masked.txt")
    problem = write(run_dirs["problem"] / "RuleCheck_20260807_095325_Core1_problems.txt")
    unrelated = write(run_dirs["raw"] / "20260807_095325_raw_Core2.txt")

    api.delete_log_files([str(target)])

    assert not masked.exists()
    assert not problem.exists()
    assert unrelated.exists(), "요청하지 않은 장비의 로그를 지우면 안 된다"


def test_delete_reports_multiple_files(api, run_dirs):
    first = write(run_dirs["raw"] / RAW_NAME)
    second = write(run_dirs["raw"] / "20260807_095325_raw_Core2.txt")

    result = api.delete_log_files([str(first), str(second)])

    assert sorted(result["deleted"]) == sorted([str(first), str(second)])
    assert not first.exists() and not second.exists()


# ---------- 부수 작업이 본래 요청을 삼키지 않는다 ----------

def test_primary_deletion_survives_a_derived_path_failure(api, run_dirs, monkeypatch):
    """파생 결과 경로 계산이 어떤 이유로든 실패해도 요청한 파일은 지워져야 한다.

    이 순서가 지켜지지 않아서 버그 하나가 삭제 기능 전체를 멈춰 세웠다.
    """
    target = write(run_dirs["raw"] / RAW_NAME)
    monkeypatch.setattr(type(api), "_derived_output_paths",
                        lambda self, fname: (_ for _ in ()).throw(RuntimeError("boom")))

    result = api.delete_log_files([str(target)])

    assert result["deleted"] == [str(target)]
    assert not target.exists()


# ---------- 경로 경계는 그대로 ----------

def test_outside_paths_are_refused(api, run_dirs):
    outside = Path(tempfile.mkdtemp()) / "victim.txt"
    write(outside, "secret")

    result = api.delete_log_files([str(outside)])

    assert result["deleted"] == []
    assert "허용되지 않은" in result["errors"][str(outside)]
    assert outside.exists()


def test_non_txt_is_refused(api, run_dirs):
    other = write(run_dirs["raw"] / "notes.md", "x")

    result = api.delete_log_files([str(other)])

    assert result["deleted"] == []
    assert other.exists()


def test_already_missing_file_is_not_an_error(api, run_dirs):
    """목록이 디스크보다 앞서 있는 경우(탐색기에서 먼저 지운 경우)에 굳이 실패로 만들지 않는다."""
    ghost = run_dirs["raw"] / RAW_NAME
    result = api.delete_log_files([str(ghost)])
    assert result["deleted"] == [str(ghost)]
    assert result["errors"] == {}
