"""점검 로그 텍스트 디코딩의 단일 출처.

같은 디코딩 규칙(UTF-8/BOM 우선, 실패하면 cp949, 그래도 실패하면 치환)이 세 곳에 각각
구현돼 있었다:

    api/log_file_browser_api.py   _read_text_auto()
    engine/inspection_report_builder.py  _read_text()
    core/storage_service.py       load_text()

규칙이 흩어져 있으면 한쪽만 고쳐져 "점검 로그 탭에서는 읽히는데 보고서에서는 깨지는" 어긋남이
생긴다. 실제로 이 규칙은 레거시 로그(과거에 시스템 기본 인코딩으로 저장된 파일) 때문에
존재하므로, 어느 경로로 읽어도 같아야 한다.

부수 효과로 구조 문제 하나가 풀린다: engine/baseline_store.py 가 이 함수를 쓰려고
`from api.log_file_browser_api import _read_text_auto` 를 지역 import 로 하고 있었다 —
engine 이 api 를 참조하는 역방향 의존이다(ARCHITECTURE.md 의 단방향 규칙 위반).
이제 engine 은 core 를 보면 된다.
"""
from pathlib import Path

__all__ = ["LOG_ENCODINGS", "decode_log_bytes", "read_log_text"]

# 시도 순서. utf-8-sig 는 BOM 이 있으면 벗기고, 없으면 utf-8 과 같게 동작한다.
# cp949 는 과거 Windows 기본 인코딩으로 저장된 레거시 로그를 위한 것이다.
LOG_ENCODINGS = ("utf-8-sig", "cp949")


def decode_log_bytes(raw: bytes) -> str:
    """로그 바이트를 문자열로. 어떤 입력이든 예외를 내지 않는다.

    마지막 폴백에서 errors="replace" 를 쓰는 이유: 로그 한 줄이 깨졌다고 점검 전체를
    실패시키면 안 된다. 깨진 문자를 표시하고 나머지를 읽는 것이 낫다.
    """
    for encoding in LOG_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_log_text(path) -> str:
    """절대경로 파일을 로그 인코딩 규칙으로 읽는다."""
    return decode_log_bytes(Path(path).read_bytes())
