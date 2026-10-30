"""로그 텍스트 디코딩 단일 출처 + 계층 의존 방향 — OPTIMIZATION_PLAN 3-2 의 선행 정리.

같은 디코딩 규칙이 세 곳에 중복돼 있었고(api/log_file_browser_api, engine/
inspection_report_builder, core/storage_service), 그중 하나를 쓰려고 engine 이 api 를
참조하는 역방향 import 까지 있었다. 3-2 의 파싱 캐시를 engine 에 두려면 그 디코더가
engine 에서 보이는 곳에 있어야 하므로 core/text_io.py 로 내렸다.

이 테스트가 지키는 것:
  1. 네 경로(core / api 위임 / engine 위임 / storage_service)가 같은 답을 낸다.
  (engine/core/report 가 api 를 import 하지 않는다는 검사는 tests/test_layering.py 로 옮겨 넓혔다 — 3-6.)
"""
import tempfile
from pathlib import Path

import pytest

from core.text_io import LOG_ENCODINGS, decode_log_bytes, read_log_text

ROOT = Path(__file__).resolve().parent.parent

# (이름, 바이트) — 실제로 마주치는 인코딩 상황.
CASES = [
    pytest.param("정상 로그\nEt1 up\n".encode("utf-8"), "정상 로그\nEt1 up\n", id="utf-8"),
    pytest.param("﻿정상 로그\n".encode("utf-8"), "정상 로그\n", id="utf-8-bom"),
    pytest.param("레거시 로그\n".encode("cp949"), "레거시 로그\n", id="cp949"),
    pytest.param(b"", "", id="empty"),
]


@pytest.mark.parametrize("raw,expected", CASES)
def test_decode_log_bytes(raw, expected):
    assert decode_log_bytes(raw) == expected


def test_broken_bytes_do_not_raise():
    """로그 한 줄이 깨졌다고 점검 전체가 실패하면 안 된다 — 치환해서라도 읽는다."""
    text = decode_log_bytes(b"\xff\xfe\x00bad bytes")
    assert isinstance(text, str)
    assert "bad bytes" in text


def test_bom_is_stripped():
    """BOM 이 남으면 첫 줄이 프롬프트/명령으로 인식되지 않아 판정 맥락이 깨진다."""
    assert not decode_log_bytes("﻿Core1#show version\n".encode("utf-8")).startswith("﻿")


def test_encoding_order_is_utf8_first():
    """순서가 바뀌면 UTF-8 로 저장된 한글 로그가 cp949 로 잘못 해독될 수 있다."""
    assert LOG_ENCODINGS[0] == "utf-8-sig"
    assert "cp949" in LOG_ENCODINGS


@pytest.mark.parametrize("raw,expected", CASES)
def test_all_read_paths_agree(raw, expected):
    """core / api 위임 / engine 위임이 같은 답을 내야 한다.

    규칙이 흩어져 있으면 한쪽만 고쳐져 "점검 로그 탭에서는 읽히는데 보고서에서는 깨지는"
    어긋남이 생긴다. 이 규칙은 레거시 로그 때문에 존재하므로 경로별로 달라선 안 된다.
    """
    from api.log_file_browser_api import _read_text_auto
    from engine.inspection_report_builder import _read_text

    with tempfile.NamedTemporaryFile("wb", suffix=".txt", delete=False) as handle:
        handle.write(raw)
        path = handle.name
    try:
        assert read_log_text(path) == expected
        assert _read_text_auto(path) == expected
        assert _read_text(Path(path)) == expected
    finally:
        Path(path).unlink()
