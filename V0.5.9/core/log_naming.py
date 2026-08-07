"""점검 로그 파일명 규칙의 단일 출처 — 만드는 쪽과 읽는 쪽이 같은 규칙을 쓰게 한다.

규칙(둘 다 지원):
    현재  {YYYYMMDD}_{HHMMSS}_{종류}_{장비}.txt      예: 20260804_105238_raw_Core1.txt
    레거시 AutoCheck_{장비}_{YYYYMMDD}_{HHMMSS}.txt   예: AutoCheck_Core1_20260804_105238.txt

왜 모으는가: 이 규칙을 파싱하는 코드가 네 곳에 중복돼 있었다.

    engine/log_analysis.py            run_analysis() 안에 인라인
    api/log_analysis_run_api.py       start_ai_log_analysis() 안에 인라인(같은 로직 복제)
    api/log_file_browser_api.py       _parse_terminal_session_filename() + 삭제 경로에 또 한 벌

네 곳이 지금은 같은 답을 내지만(실측 확인), 규칙이 바뀌면 한 곳만 고쳐질 것이 뻔하다. 그러면
장비명이 화면마다 달라지고("대시보드에는 Core1, 보고서에는 105238") 원인을 찾기 어렵다.

**주의 — engine/baseline_store.device_from_filename() 은 여기로 합치지 말 것.**
그쪽은 다른 파일 집단을 다룬다: SecureCRT 가 임의로 붙인 CRTlog 이름(`192.168.205.101_
20260803_152204.txt`, `session.log` 등)에서 장비를 추정하는 휴리스틱이다. 그래서 첫 두 토큰이
숫자인지 검사하고, 실패하면 **첫 토큰**을 장비명으로 본다. 이 함수는 우리가 만든 이름만
다루므로 실패하면 **이름 전체**를 장비명으로 돌려준다. 실측으로 확인한 차이:

    Core1_backup_config_v2.txt  ->  이 함수: "v2"     /  baseline_store: "Core1"

둘을 합치면 CRTlog 장비 매칭이나 점검 로그 장비명 중 하나가 반드시 깨진다.
"""
import datetime
import os

__all__ = [
    "LEGACY_PREFIX", "STAMP_FORMAT", "DEFAULT_KIND",
    "build_inspection_log_name", "parse_inspection_log_name", "device_from_log_name",
]

LEGACY_PREFIX = "AutoCheck_"
# 파일명 안의 타임스탬프. 파일명은 정렬 가능해야 하므로 고정폭 숫자만 쓴다.
STAMP_FORMAT = "%Y%m%d_%H%M%S"
DEFAULT_KIND = "raw"

_UNKNOWN_STAMP = "unknown_time"


def build_inspection_log_name(device, when=None, kind=DEFAULT_KIND):
    """{YYYYMMDD}_{HHMMSS}_{종류}_{장비}.txt — 점검 로그 파일명을 만든다."""
    stamp = (when or datetime.datetime.now()).strftime(STAMP_FORMAT)
    return f"{stamp}_{kind}_{device}.txt"


def parse_inspection_log_name(filename):
    """파일명에서 (장비명, 타임스탬프 문자열)을 뽑는다.

    규칙에 맞지 않으면 (확장자 뗀 이름 전체, None)을 돌려준다 — 사용자가 손으로 넣은 로그도
    목록에 장비 하나로 보여야 하기 때문이다. 예외를 던지지 않는다.
    """
    body = os.path.basename(str(filename))
    if body.lower().endswith(".txt"):
        body = body[:-len(".txt")]

    if body.startswith(LEGACY_PREFIX):
        rest = body[len(LEGACY_PREFIX):]
        parts = rest.rsplit("_", 2)
        if len(parts) == 3:
            return parts[0], f"{parts[1]}_{parts[2]}"
        return rest, None

    parts = body.split("_", 3)
    if len(parts) == 4:
        return parts[3], f"{parts[0]}_{parts[1]}"
    return body, None


def device_from_log_name(filename):
    """장비명만 필요할 때. parse_inspection_log_name()의 앞쪽 값과 항상 같다."""
    return parse_inspection_log_name(filename)[0]


def stamp_or_unknown(filename):
    """분석 결과 파일명을 조립할 때 쓰는 타임스탬프 — 없으면 'unknown_time'.

    호출부가 각자 이 폴백 문자열을 들고 있으면 결과 파일명이 갈라진다
    ("LocalAI__Core1_problems.txt" 대 "LocalAI_unknown_time_Core1_problems.txt").
    """
    return parse_inspection_log_name(filename)[1] or _UNKNOWN_STAMP
