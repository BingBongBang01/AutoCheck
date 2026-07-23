"""
ANSI/터미널 제어 시퀀스 sanitizer — SSH 원본 출력(raw stdout)에 섞여 들어오는
컬러 코드, 커서 이동, 캐리지리턴/백스페이스 오버라이트를 제거해서
사람이 그대로 읽을 수 있는 평문 로그로 만든다.

core/sanitizer.py(Cloud AI 마스킹 계층)와는 목적이 다름 — 이 모듈은
"터미널 잡음 제거"만 하고 IP/호스트명 등 실제 값은 전혀 건드리지 않는다.
"""
import re

# CSI(ESC [ ... 최종바이트), OSC(ESC ] ... BEL/ESC\\), 단순 2바이트 ESC 시퀀스까지 포괄
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_OTHER_RE = re.compile(r"\x1b[@-Z\\-_]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # \t(\x09)/\n(\x0a)/\r(\x0d)는 별도 처리


def strip_ansi(text):
    """ANSI escape 시퀀스(색상, 커서 이동 등)만 제거. \\r/\\b 처리는 clean_terminal_log 참고."""
    if not text:
        return text
    text = _ANSI_OSC_RE.sub("", text)
    text = _ANSI_CSI_RE.sub("", text)
    text = _ANSI_OTHER_RE.sub("", text)
    return text


def _apply_backspaces(line):
    """\\b(0x08)를 실제 오버라이트로 반영 — 커서를 한 칸 되돌려 다음 문자로 덮어씀."""
    if "\b" not in line:
        return line
    out = []
    for ch in line:
        if ch == "\b":
            if out:
                out.pop()
        else:
            out.append(ch)
    return "".join(out)


def _apply_carriage_returns(line):
    """\\r(0x0d)로 나뉜 조각 중 마지막 조각만 남긴다(터미널의 '같은 줄 덮어쓰기' 재현)."""
    if "\r" not in line:
        return line
    return line.split("\r")[-1]


def clean_terminal_log(raw_text):
    """
    SSH 세션 원본 출력을 파일로 저장하기 전에 호출.
    1) ANSI 컬러/커서 이동 시퀀스 제거
    2) 각 줄 안에서 \\r 덮어쓰기 및 \\b 백스페이스를 실제 결과로 반영
    3) 남은 제어문자 제거, 줄끝 공백 정리
    """
    if not raw_text:
        return ""
    text = strip_ansi(raw_text)
    lines = []
    for raw_line in text.split("\n"):
        line = _apply_carriage_returns(raw_line)
        line = _apply_backspaces(line)
        line = _CONTROL_RE.sub("", line)
        lines.append(line.rstrip())
    return "\n".join(lines)


if __name__ == "__main__":
    sample = "\x1b[32mHostname\x1b[0m: Core1\r\nLoading...\rLoading... 100%\x08\x08\x08done\n"
    print(repr(clean_terminal_log(sample)))
