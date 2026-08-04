"""점검 로그 파일명 규칙 + run_id 정렬 불변식 — OPTIMIZATION_PLAN 3-6.

**계획의 전제를 먼저 정정한다.** 3-6 은 "run_id 포맷이 섞여 '최신 run' 판정이 조용히 틀린다"를
근거로 삼았는데, 확인해 보니 재현되지 않았다:

  * run_id 생성은 core/storage_service.create_run() 한 곳이고 core.utils.format_run_id()
    (`%Y-%m-%d_%H%M%S`)를 쓴다. 충돌 시 `_%f`를 덧붙이는데 접두부가 같아 정렬이 유지된다.
  * V0.5.0~V0.5.8 전부 같은 포맷이었다(git 이력 확인). 섞인 적이 없다.
  * 그 포맷들에 대해 **문자열 정렬 == 시각 정렬**이다.

그래서 이 파일은 두 가지를 한다:
  1. 그 불변식을 못박아, 누가 RUN_ID_FORMAT 을 바꾸면 실패하게 만든다. 이 실패 모드는
     조용하고 파급이 크다 — '최신 run' 을 잘못 고르면 보고서/대시보드/분석이 전부 다른
     회차를 읽는다.
  2. 실제로 남아 있던 위험(같은 파일명 규칙을 파싱하는 코드가 네 곳에 복제)을 통합한
     core/log_naming.py 의 계약을 고정한다.
"""
import datetime

import pytest

from core.log_naming import (
    STAMP_FORMAT,
    build_inspection_log_name,
    device_from_log_name,
    parse_inspection_log_name,
    stamp_or_unknown,
)
from core.utils.datetime import RUN_ID_FORMAT, format_run_id


# --------------------------------------------------------------------------- 파일명 규칙


@pytest.mark.parametrize("device", ["Core1", "Core_1", "SW-01", "스위치1", "a.b.c"])
def test_producer_parser_round_trip(device):
    """만든 이름을 다시 읽으면 같은 장비명이 나와야 한다 — 규칙의 최소 계약."""
    when = datetime.datetime(2026, 8, 4, 10, 52, 38)
    name = build_inspection_log_name(device, when)
    assert name == f"20260804_105238_raw_{device}.txt"
    parsed_device, parsed_stamp = parse_inspection_log_name(name)
    assert parsed_device == device
    assert parsed_stamp == "20260804_105238"


def test_stamp_format_is_sortable():
    """파일명 타임스탬프는 고정폭 숫자여야 한다 — 목록 정렬이 이것에 의존한다."""
    assert STAMP_FORMAT == "%Y%m%d_%H%M%S"
    early = build_inspection_log_name("Core1", datetime.datetime(2026, 8, 4, 9, 0, 0))
    late = build_inspection_log_name("Core1", datetime.datetime(2026, 8, 4, 10, 0, 0))
    assert early < late, "파일명 문자열 정렬이 시간순과 어긋난다"


@pytest.mark.parametrize("name,device,stamp", [
    pytest.param("20260804_105238_raw_Core1.txt", "Core1", "20260804_105238", id="현재-형식"),
    pytest.param("20260804_105238_Type_Core1.txt", "Core1", "20260804_105238", id="종류-다름"),
    pytest.param("20260804_105238_raw_Core_1.txt", "Core_1", "20260804_105238", id="장비명에-밑줄"),
    pytest.param("AutoCheck_Core1_20260804_105238.txt", "Core1", "20260804_105238", id="레거시"),
    pytest.param("AutoCheck_Core_1_20260804_105238.txt", "Core_1", "20260804_105238", id="레거시-밑줄"),
    pytest.param("Core1.txt", "Core1", None, id="규칙-밖-단일토큰"),
    pytest.param("session.log.txt", "session.log", None, id="규칙-밖-점"),
])
def test_parse_inspection_log_name(name, device, stamp):
    assert parse_inspection_log_name(name) == (device, stamp)


def test_parse_accepts_full_paths():
    """호출부가 절대경로를 그대로 넘긴다(engine/log_analysis.run_analysis)."""
    assert device_from_log_name("/tmp/runs/x/raw/20260804_105238_raw_Core1.txt") == "Core1"


def test_stamp_or_unknown_has_single_fallback():
    """폴백 문자열이 호출부마다 다르면 분석 결과 파일명이 갈라진다."""
    assert stamp_or_unknown("20260804_105238_raw_Core1.txt") == "20260804_105238"
    assert stamp_or_unknown("Core1.txt") == "unknown_time"


def test_parse_never_raises():
    """사용자가 손으로 넣은 파일도 목록에 장비 하나로 보여야 한다 — 예외를 던지면 탭이 죽는다."""
    for weird in ["", ".txt", "_", "___", "....txt", "한글.txt", "a" * 300 + ".txt"]:
        device, _stamp = parse_inspection_log_name(weird)
        assert isinstance(device, str)


# --------------------------------------------------------------------------- 파서 통합 확인


def test_all_call_sites_agree():
    """예전에 네 곳에 복제돼 있던 파싱이 이제 한 함수를 쓰는지 — 대표 호출부로 확인.

    복제 상태에서도 현실적인 이름에는 답이 같았다(실측). 문제는 규칙이 바뀔 때 한 곳만
    고쳐지는 것이었다 — 그러면 장비명이 화면마다 달라진다.
    """
    from api.log_file_browser_api import _parse_terminal_session_filename

    names = [
        "20260804_105238_raw_Core1.txt",
        "AutoCheck_Core1_20260804_105238.txt",
        "20260804_105238_raw_Core_1.txt",
        "Core1.txt",
    ]
    for name in names:
        assert _parse_terminal_session_filename(name) == device_from_log_name(name)


def test_crtlog_parser_is_deliberately_different():
    """engine/baseline_store.device_from_filename() 은 여기로 합치면 안 된다.

    그쪽은 SecureCRT 가 임의로 붙인 CRTlog 이름에서 장비를 추정하는 휴리스틱이다(첫 두 토큰이
    숫자인지 보고, 실패하면 첫 토큰을 장비명으로 본다). 이 함수는 우리가 만든 이름만 다루므로
    실패하면 이름 전체를 돌려준다. 실측 차이를 고정해 두어, 나중에 누가 "중복이네" 하며
    합치려 할 때 이 테스트가 이유를 알려준다.
    """
    from engine.baseline_store import device_from_filename

    name = "Core1_backup_config_v2.txt"
    assert device_from_log_name(name) == "v2"          # 우리 규칙: 4토큰 -> 마지막이 장비
    assert device_from_filename(name) == "Core1"       # CRTlog 휴리스틱: 첫 토큰

    ip_named = "192.168.205.101_20260803_152204.txt"
    assert device_from_filename(ip_named) == "192.168.205.101"


# --------------------------------------------------------------------------- run_id 정렬 불변식


def _parse_run_id(run_id):
    """run_id 앞부분을 시각으로 해석. 충돌 접미사(_%f)는 무시한다."""
    try:
        return datetime.datetime.strptime(run_id[:17], "%Y-%m-%d_%H%M%S")
    except ValueError:
        return None


def test_run_id_format_is_unchanged():
    """포맷을 바꾸면 문자열 정렬 기반의 '최신 run' 판정이 조용히 틀릴 수 있다.

    이 테스트가 실패하면 아래 test_string_sort_matches_chronological_sort 도 함께 확인하고,
    engine/log_storage.list_run_dirs() / engine/profile_manager.list_runs() 의 정렬을
    파싱 기반으로 바꿀지 판단해야 한다.
    """
    assert RUN_ID_FORMAT == "%Y-%m-%d_%H%M%S"
    assert format_run_id(datetime.datetime(2026, 8, 4, 10, 52, 38)) == "2026-08-04_105238"


def test_string_sort_matches_chronological_sort():
    """**핵심 불변식.** run_id 문자열 정렬이 시간순과 같아야 한다.

    engine/log_storage.list_run_dirs() 는 폴더명 문자열로 최신순을 판정하고,
    engine/profile_manager.list_runs() 는 오름차순 정렬 후 마지막을 '최신'으로 쓴다.
    이 불변식이 깨지면 보고서/대시보드/분석이 모두 엉뚱한 회차를 읽는다.
    """
    moments = [
        datetime.datetime(2026, 7, 31, 23, 59, 59),
        datetime.datetime(2026, 8, 4, 9, 0, 0),
        datetime.datetime(2026, 8, 4, 10, 52, 38),
        datetime.datetime(2026, 8, 4, 10, 53, 1),
        datetime.datetime(2026, 8, 5, 9, 0, 0),
        datetime.datetime(2026, 12, 31, 23, 59, 59),
        datetime.datetime(2027, 1, 1, 0, 0, 0),
    ]
    run_ids = [format_run_id(m) for m in moments]
    assert sorted(run_ids) == run_ids, "시간순으로 만든 run_id 가 문자열 정렬과 어긋난다"


def test_collision_suffix_preserves_order():
    """같은 초에 두 run 이 생기면 `_%f` 가 붙는다 — 그래도 정렬이 유지되는지."""
    base = format_run_id(datetime.datetime(2026, 8, 4, 10, 52, 38))
    collided = f"{base}_753152"
    later = format_run_id(datetime.datetime(2026, 8, 4, 10, 52, 39))

    ordered = sorted([later, collided, base])
    assert ordered == [base, collided, later]
    assert _parse_run_id(collided) == _parse_run_id(base), "충돌 접미사가 시각 해석을 깨뜨린다"


def test_latest_run_selection_matches_chronology(tmp_path):
    """log_storage.list_run_dirs() 가 실제로 최신 run 을 먼저 돌려주는지 — 폴더로 확인."""
    import logging

    logging.disable(logging.CRITICAL)
    from core.paths import AppPaths

    original = AppPaths._user_data_root
    AppPaths._user_data_root = tmp_path / "AutoCheck"
    AppPaths.forget_ensured()
    try:
        from engine import log_storage
        from engine.profile_manager import profile_manager as prm

        customer, profile = "고객사A", "2026-07"
        prm.create_profile(customer, profile)
        runs_dir = prm.profile_dir(customer, profile) / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)

        moments = [
            datetime.datetime(2026, 8, 4, 9, 0, 0),
            datetime.datetime(2026, 8, 4, 10, 52, 38),
            datetime.datetime(2026, 8, 5, 9, 0, 0),
        ]
        for moment in moments:
            (runs_dir / format_run_id(moment) / "raw").mkdir(parents=True, exist_ok=True)

        listed = [entry["run_id"] for entry in log_storage.list_run_dirs(customer, profile)]
        expected = [format_run_id(m) for m in sorted(moments, reverse=True)]
        assert listed == expected, "최신순 정렬이 시간순과 어긋난다"

        latest = log_storage.get_latest_run_dir(customer, profile)
        assert latest["run_id"] == format_run_id(max(moments))
    finally:
        AppPaths._user_data_root = original
        AppPaths.forget_ensured()
        logging.disable(logging.NOTSET)
