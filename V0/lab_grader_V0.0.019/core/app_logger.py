"""전역 애플리케이션 로그 버퍼.

화면(아키텍처 탭 아래 '전체 로그')에는 최근 N줄만 보여줘서 로그가 아무리 쌓여도 랙이 걸리지
않게 하고, 세션이 시작된 이후의 모든 로그는 logs/ 아래 세션 파일에 계속 누적해서 남긴다.
'전체 로그 내보내기'는 화면에 보이지 않는 부분까지 포함해 이 세션 파일 전체를 .txt로 저장한다.

기존 코드 전반에 흩어진 print(...) 호출(AI 라우터 폴백 로그 등)을 하나하나 고치지 않고도
전부 캡처하기 위해 builtins.print를 감싼다 — 콘솔 출력 동작 자체는 그대로 유지된다.
"""
import builtins
import datetime
import os
import threading
from collections import deque

_LOCK = threading.Lock()
_MAX_MEMORY_LINES = 500
_buffer = deque(maxlen=_MAX_MEMORY_LINES)
_total_count = 0
_LOG_DIR = "logs"
_session_file_path = None
_original_print = builtins.print
_print_capture_installed = False


def _ensure_session_file():
    global _session_file_path
    if _session_file_path is None:
        os.makedirs(_LOG_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        _session_file_path = os.path.join(_LOG_DIR, f"app_full_{stamp}.log")
    return _session_file_path


def log_event(message, source="app"):
    """로그 한 줄을 메모리 버퍼(최근 N줄, 화면 표시용)와 세션 로그 파일(전체 보존)에 함께 기록."""
    global _total_count
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{source}] {message}"
    with _LOCK:
        _buffer.append(line)
        _total_count += 1
        try:
            with open(_ensure_session_file(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    return line


def get_recent_logs(limit=300):
    with _LOCK:
        lines = list(_buffer)[-limit:]
        return {
            "lines": lines,
            "shown": len(lines),
            "total_count": _total_count,
            "truncated": _total_count > len(lines),
            "session_file": _session_file_path,
        }


def get_session_file_path():
    with _LOCK:
        return _ensure_session_file()


def install_print_capture():
    """builtins.print를 감싸서 기존 print(...) 로그도 전부 버퍼/파일에 남긴다. 여러 번 호출돼도 안전."""
    global _print_capture_installed
    if _print_capture_installed:
        return
    _print_capture_installed = True

    def _patched_print(*args, **kwargs):
        _original_print(*args, **kwargs)
        try:
            text = " ".join(str(a) for a in args).strip()
            if text:
                log_event(text, source="print")
        except Exception:
            pass

    builtins.print = _patched_print
