"""점검 명령 안전 정책 — 점검은 장비를 '읽기만' 한다.

커맨드 카탈로그의 명령은 세션 터미널 점검에서 작업자의 SSH 세션에 그대로 입력되고
(api/terminal_inspection_api.py), SSH 수집(engine/collector.py)에서도 장비로 보내진다. 기본
카탈로그에 `conf`(설정 모드 진입)가 들어 있어서, 점검이 끝나면 작업자 터미널이 `(config)#` 에
남았다 — 작업자의 다음 입력이 곧 설정 변경이 되는 상태다.

그래서 **허용 목록**으로 막는다. 장비 상태를 바꾸지 않는 명령만 통과시킨다:

  show ...      조회(sh, sho 축약 포함). 단, 출력을 장비 파일에 쓰는 것(| redirect · | tee ·
                | append · > flash:)은 막는다 — startup-config 를 덮어쓸 수도 있다.
  terminal ...  세션 설정(페이징 등)만 바꾼다(term 축약 포함).
  enable        특권 모드 진입(설정 변경이 아니다). 숫자 권한 수준만 인자로 허용한다.
  dir ...       파일 목록 조회.

나머지(conf/configure, copy, write, reload, delete, clear, no ..., exit/end, bash 등)는 전부 막는다.
금지 목록으로 하지 않는 이유: 벤더·축약형이 많아 금지 목록은 반드시 새는 곳이 생긴다. 허용
목록은 잘못돼도 '점검 명령 하나가 안 돌았다'로 끝나지만, 금지 목록이 새면 운영 장비가 바뀐다.
"""
import re

_FILE_WRITE_RE = re.compile(r"\|\s*(redirect|tee|append)\b|>{1,2}\s*[\w-]+:", re.IGNORECASE)
_ENABLE_ARGS_RE = re.compile(r"^\d{1,2}$")


def _abbreviates(word, keyword, min_len):
    """word 가 keyword 의 축약형인지(Cisco/Arista CLI 처럼 앞부분만 쳐도 되는 규칙)."""
    return min_len <= len(word) <= len(keyword) and keyword.startswith(word)


def blocked_reason(command):
    """읽기 전용 점검에 쓸 수 없는 명령이면 이유(문자열)를, 쓸 수 있으면 None 을 돌려준다.
    빈 문자열·'!' 주석 줄은 장비에 아무 일도 하지 않으므로 None 이다."""
    text = (command or "").strip()
    if not text or text.startswith("!"):
        return None
    words = text.split()
    first = words[0].lower()

    if _abbreviates(first, "show", 2):
        if _FILE_WRITE_RE.search(text):
            return "출력을 장비 파일에 쓰는 명령(redirect/tee/append/>)은 점검에서 쓸 수 없습니다"
        return None
    if _abbreviates(first, "terminal", 4) or first == "dir":
        return None
    if _abbreviates(first, "enable", 2):
        if all(_ENABLE_ARGS_RE.match(arg) for arg in words[1:]):
            return None
        return "enable 뒤에는 권한 수준 숫자만 쓸 수 있습니다(비밀번호를 카탈로그에 넣지 마세요)"
    if _abbreviates(first, "configure", 4):
        return "설정 모드 진입은 점검에서 허용하지 않습니다 — 점검이 끝나도 터미널이 설정 모드에 남습니다"
    return "장비를 바꾸지 않는 명령(show · terminal · enable · dir)만 점검에서 실행합니다"
