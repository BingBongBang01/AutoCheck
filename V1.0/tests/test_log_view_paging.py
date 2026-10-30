"""로그 보기의 쪽 나누기·찾기 — IMPROVEMENT_PLAN 4-10d.

  1. read_log_file 은 한 쪽(기본 5,000줄)만 넘기고 전체 줄 수를 알린다 — 예전엔 파일 전체를 넘겨 큰 로그에서 화면이 멈췄다.
  2. search_log_file 은 파일 전체에서 찾는다(대소문자 무시·정규식·앞 500줄까지와 전체 수).
  3. 허용된 폴더 밖·없는 파일은 그대로 거절하고, 파일이 바뀌면 다시 읽는다.
"""
import os
import tempfile
import time
from pathlib import Path

import pytest

from core.paths import AppPaths

CUSTOMER, PROFILE = "고객사", "2026-10 정기점검"


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

        def _resolve_active_customer_profile_names(self):
            return (CUSTOMER, PROFILE)

    prm.create_profile(CUSTOMER, PROFILE)
    yield Stub()
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


@pytest.fixture
def big_log(api):
    from engine import log_storage
    raw = Path(log_storage.get_profile_dir(CUSTOMER, PROFILE)) / "runs" / "2026-10-28_101500" / "raw"
    raw.mkdir(parents=True)
    path = raw / "20261028_101500_raw_Core1.txt"
    lines = [f"line {i:05d} Ethernet{i % 7} {'DOWN' if i % 1000 == 0 else 'up'}" for i in range(12001)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_a_big_file_is_read_one_page_at_a_time(api, big_log):
    first = api.read_log_file(big_log)
    assert (first["start_line"], first["lines"], first["total_lines"], first["truncated"]) == (0, 5000, 12001, True)
    assert first["text"].splitlines()[0] == "line 00000 Ethernet0 DOWN" and first["text"].count("\n") == 4999
    last = api.read_log_file(big_log, 10000)
    assert (last["start_line"], last["lines"]) == (10000, 2001) and last["text"].endswith("line 12000 Ethernet2 DOWN")
    clamped = api.read_log_file(big_log, 999999, 50)
    assert clamped["start_line"] == 12000 and clamped["lines"] == 1
    small = api.read_log_file(big_log, 0, 20000)
    assert small["lines"] == 12001 and small["truncated"] is False


def test_search_covers_the_whole_file(api, big_log):
    found = api.search_log_file(big_log, "down")
    assert found["total"] == 13 and [h["line"] for h in found["hits"]][:3] == [0, 1000, 2000]
    assert found["hits"][-1] == {"line": 12000, "text": "line 12000 Ethernet2 DOWN"}
    regex = api.search_log_file(big_log, r"Ethernet[56] up$", regex=True)
    assert regex["total"] > 500 and len(regex["hits"]) == 500 and regex["truncated"] is True
    assert api.search_log_file(big_log, "down", case_sensitive=True)["total"] == 0
    assert "정규식" in api.search_log_file(big_log, "([", regex=True)["error"]
    assert api.search_log_file(big_log, "  ")["error"] == "찾을 말을 입력하세요."


def test_paths_outside_the_profile_are_still_refused(api, big_log, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("x", encoding="utf-8")
    assert api.read_log_file(str(outside))["error"] == "허용되지 않은 경로입니다."
    assert api.search_log_file(str(outside), "x")["error"] == "허용되지 않은 경로입니다."
    assert api.read_log_file(big_log + ".nope")["error"] == "파일이 존재하지 않습니다."


def test_a_changed_file_is_read_again(api, big_log):
    assert api.read_log_file(big_log)["total_lines"] == 12001
    with open(big_log, "a", encoding="utf-8") as f:
        f.write("appended line\n")
    later = time.time() + 5
    os.utime(big_log, (later, later))
    assert api.read_log_file(big_log)["total_lines"] == 12002
    assert api.search_log_file(big_log, "appended")["hits"][0]["line"] == 12001
