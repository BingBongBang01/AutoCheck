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

**주의 — device_from_session_log_name() 은 device_from_log_name() 과 합치지 말 것.**
같은 모듈에 있지만 다른 파일 집단을 다룬다: SecureCRT 가 임의로 붙인 CRTlog 이름(`192.168.205.101_
20260803_152204.txt`, `session.log` 등)에서 장비를 추정하는 휴리스틱이다. 그래서 첫 두 토큰이
숫자인지 검사하고, 실패하면 **첫 토큰**을 장비명으로 본다. device_from_log_name() 은 우리가 만든
이름만 다루므로 실패하면 **이름 전체**를 장비명으로 돌려준다. 실측으로 확인한 차이:

    Core1_backup_config_v2.txt  ->  device_from_log_name: "Core1_backup_config_v2"
                                    device_from_session_log_name: "Core1"

둘을 합치면 CRTlog 장비 매칭이나 점검 로그 장비명 중 하나가 반드시 깨진다. 예전엔
engine/baseline_store.device_from_filename() 이었는데, core 의 CRT 감시기가 그것을 쓰려고 engine 을
거꾸로 import 했다 — 파일명 규칙이므로 여기로 내렸다(IMPROVEMENT_PLAN 3-6).
"""
import datetime
import os
import re

from core.paths import safe_filename

__all__ = [
    "LEGACY_PREFIX", "STAMP_FORMAT", "DEFAULT_KIND",
    "build_inspection_log_name", "parse_inspection_log_name", "device_from_log_name",
    "is_inspection_log", "device_from_session_log_name",
]

LEGACY_PREFIX = "AutoCheck_"
# 파일명 안의 타임스탬프. 파일명은 정렬 가능해야 하므로 고정폭 숫자만 쓴다.
STAMP_FORMAT = "%Y%m%d_%H%M%S"
DEFAULT_KIND = "raw"

_UNKNOWN_STAMP = "unknown_time"


def build_inspection_log_name(device, when=None, kind=DEFAULT_KIND):
    """{YYYYMMDD}_{HHMMSS}_{종류}_{장비}.txt — 점검 로그 파일명을 만든다.
    장비명은 장비 hostname·엑셀에서 온 값이라 파일명 한 조각으로 다듬는다('../x', 'SW:1' 방지)."""
    stamp = (when or datetime.datetime.now()).strftime(STAMP_FORMAT)
    return f"{stamp}_{kind}_{safe_filename(device)}.txt"


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
        if len(parts) == 3 and _is_stamp(parts[1], parts[2]):
            return parts[0], f"{parts[1]}_{parts[2]}"
        return rest, None

    # 날짜·시각 자리가 숫자인지 확인한다 — 예전엔 밑줄 개수만 봐서, 규칙 밖 파일 'SEOUL_CORE_SW_01.txt'
    # 를 '01' 장비의 로그(SEOUL_CORE 시각)로 읽었다.
    parts = body.split("_", 3)
    if len(parts) == 4 and _is_stamp(parts[0], parts[1]):
        return parts[3], f"{parts[0]}_{parts[1]}"
    return body, None


def _is_stamp(date_part, time_part):
    """'YYYYMMDD', 'HHMMSS' 모양인가(STAMP_FORMAT)."""
    return (len(date_part) == 8 and date_part.isdigit()) and (len(time_part) == 6 and time_part.isdigit())


def device_from_log_name(filename):
    """장비명만 필요할 때. parse_inspection_log_name()의 앞쪽 값과 항상 같다."""
    return parse_inspection_log_name(filename)[0]


def stamp_or_unknown(filename):
    """분석 결과 파일명을 조립할 때 쓰는 타임스탬프 — 없으면 'unknown_time'.

    호출부가 각자 이 폴백 문자열을 들고 있으면 결과 파일명이 갈라진다
    ("LocalAI__Core1_problems.txt" 대 "LocalAI_unknown_time_Core1_problems.txt").
    """
    return parse_inspection_log_name(filename)[1] or _UNKNOWN_STAMP


# 점검(SSH) 세션이 남긴 원본 로그 — build_inspection_log_name() 이 만드는 {stamp}_raw_{device}.txt.
# 원본 폴더와 CRT 로그 폴더에는 다른 로그(수동 SecureCRT 세션 로그 등)가 섞여 있을 수 있어 이름으로 가른다.
_INSPECTION_FILE_RE = re.compile(r'^\d{8}_\d{6}_raw_.+\.txt$', re.IGNORECASE)


def is_inspection_log(path):
    """이 파일이 '점검 SSH 세션'이 만든 것인가 — Baseline 기준이 될 자격이 있고, 실시간 감시가 '지금 들어오는
    입력'으로 읽으면 안 되는 지나간 스냅샷이다."""
    return bool(_INSPECTION_FILE_RE.match(os.path.basename(path)))


def device_from_session_log_name(filename):
    """CRT 세션 로그 이름에서 장비명을 추정한다(휴리스틱 — 모듈 머리말의 '주의' 참고).

    지원 형태(수집 경로마다 규칙이 달라 셋 다 쓰인다):
      AutoCheck_<device>_<YYYYMMDD>_<HHMMSS>.txt
      <YYYYMMDD>_<HHMMSS>_..._<device>.txt
      <device>_<무엇이든>.txt  /  <device>.txt
    """
    body = os.path.basename(filename)
    if body.lower().endswith(".txt"):
        body = body[:-4]
    if body.startswith("AutoCheck_"):
        rest = body[len("AutoCheck_"):]
        parts = rest.rsplit("_", 2)
        return parts[0] if len(parts) == 3 else rest
    parts = body.split("_", 3)
    if len(parts) == 4 and parts[0].isdigit() and parts[1].isdigit():
        return parts[3]
    return parts[0]
