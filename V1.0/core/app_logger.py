"""
전역 애플리케이션 로그 버퍼 및 비동기 파일 로깅 시스템.

1. 전체 파일 로그 (app_full_<timestamp>.log):
   - DEBUG 수준을 포함한 모든 로그와 print(...) 캡처 내용을 비동기 큐(Queue)를 통해
     메인 execution thread의 차단(blocking) 없이 파일에 기록한다.
2. UI 스트리밍 메모리 버퍼:
   - UI 렌더링 랙을 방지하기 위해 UI 버퍼에는 INFO, WARNING, ERROR, CRITICAL 수준의
     요약/핵심 로그만 필터링하여 상주시킨다 (DEBUG/raw verbose 로그 제외).
"""
import builtins
import datetime
import logging
import os
import queue
import threading
import time
from collections import deque
from typing import Any, Dict, Optional, Union

from core.paths import AppPaths
from core.secret_redaction import redact_secrets

# Log Levels
LOG_LEVELS = {
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "WARN": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}
DEFAULT_LOG_LEVEL = 20  # INFO
UI_MIN_LOG_LEVEL = 20   # Filter out DEBUG (< 20) for UI streaming

_LOCK = threading.RLock()
# 메모리 버퍼는 '레벨과 무관하게 전부' 담는다. 예전에는 INFO 이상만 담아서, 로그 보기 탭에서
# 표시 수준을 DEBUG로 내려도 그 시점 이후에 생긴 DEBUG만 보였다(이미 지나간 것은 파일에만
# 있었다). 지금은 전부 들고 있다가 요청받은 수준으로 걸러 주므로, 수준을 바꾸면 지난 로그도
# 그 수준으로 다시 보이고 이후 로그도 같은 기준으로 이어진다.
# 한 줄 100~200바이트 기준 2만 줄이면 수 MB 수준 — 화면에는 한 번에 500줄만 보내므로
# 버퍼가 커져도 UI가 느려지지 않는다(랙은 DOM 줄 수에서 온다).
_MAX_MEMORY_LINES = 20000
_buffer = deque(maxlen=_MAX_MEMORY_LINES)   # [(seq, level_num, line)] — 모든 레벨
_total_ui_count = 0    # 하위호환용 별칭(= _total_full_count)
_total_full_count = 0  # 모든 로그의 통산 일련번호. since_index는 이 값을 기준으로 한다.

_session_file_path: Optional[str] = None
_original_print = builtins.print
_print_capture_installed = False

# Async File Logging Queue and Worker Thread
_log_queue: queue.Queue = queue.Queue()
_writer_thread: Optional[threading.Thread] = None
_stop_writer_event = threading.Event()


def _ensure_session_file() -> str:
    global _session_file_path
    if _session_file_path is None:
        log_dir = AppPaths.logs_root()
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        _session_file_path = str(log_dir / f"app_full_{stamp}.log")
    return _session_file_path


def _parse_level(level: Union[str, int]) -> int:
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        return LOG_LEVELS.get(level.upper().strip(), DEFAULT_LOG_LEVEL)
    return DEFAULT_LOG_LEVEL


def _async_log_writer_loop():
    """Background worker thread that flushes queued log lines to session log file."""
    file_handle = None
    current_path = None
    
    while not _stop_writer_event.is_set() or not _log_queue.empty():
        try:
            item = _log_queue.get(timeout=0.1)
        except queue.Empty:
            if file_handle:
                try:
                    file_handle.flush()
                except OSError:
                    pass
            continue

        try:
            target_path = _ensure_session_file()
            if file_handle is None or current_path != target_path:
                if file_handle:
                    try:
                        file_handle.close()
                    except OSError:
                        pass
                current_path = target_path
                file_handle = open(target_path, "a", encoding="utf-8")

            file_handle.write(item + "\n")

            # Batch write remaining items in queue to minimize disk I/O calls
            batch_count = 0
            while batch_count < 100:
                try:
                    next_item = _log_queue.get_nowait()
                except queue.Empty:
                    break
                
                try:
                    file_handle.write(next_item + "\n")
                finally:
                    _log_queue.task_done()
                batch_count += 1

            file_handle.flush()
        except Exception:
            pass
        finally:
            _log_queue.task_done()

    if file_handle:
        try:
            file_handle.flush()
            file_handle.close()
        except Exception:
            pass


def _ensure_writer_started():
    global _writer_thread
    if _writer_thread is None or not _writer_thread.is_alive():
        _stop_writer_event.clear()
        _writer_thread = threading.Thread(
            target=_async_log_writer_loop,
            name="LogFileWriterThread",
            daemon=True,
        )
        _writer_thread.start()


def log_event(message: str, source: str = "app", level: Union[str, int] = "INFO") -> str:
    """
    로그를 파일(전체 DEBUG 등 포함) 및 UI 버퍼(INFO 이상)에 기록한다.
    - 파일 쓰기는 비동기 큐를 통해 백그라운드 스레드에서 수행 (Blocking 없음)
    - UI 버퍼에는 DEBUG 수준을 제외한 INFO 이상만 유지하여 UI 랙 방지
    - API 키·토큰은 기록 전에 지운다(core/secret_redaction.py) — 파일·화면·내보내기 어디에도 남지 않게
    """
    global _total_ui_count, _total_full_count

    message = redact_secrets(str(message))
    level_num = _parse_level(level)
    level_name = next((k for k, v in LOG_LEVELS.items() if v == level_num), "INFO")
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    formatted_line = f"[{ts}] [{level_name}] [{source}] {message}"

    # 1. Enqueue to async file writer (ALL log levels)
    _ensure_writer_started()
    _log_queue.put(formatted_line)

    with _LOCK:
        _total_full_count += 1
        _total_ui_count = _total_full_count
        # 2. 메모리 버퍼에는 레벨과 무관하게 전부 넣는다 — 화면에 무엇을 보여줄지는 읽는 쪽
        #    (get_recent_logs)이 요청받은 수준으로 정한다.
        _buffer.append((_total_full_count, level_num, formatted_line))

    return formatted_line


def get_recent_logs(
    limit: int = 300,
    since_index: Optional[int] = None,
    min_level: Optional[Union[str, int]] = None,
) -> Dict[str, Any]:
    """UI 표시용 최근 로그 또는 Delta 로그 반환.

    버퍼는 모든 레벨을 들고 있고 여기서 요청받은 수준으로 거른다 — 그래서 화면에서 표시 수준을
    바꾸면 '지금까지 쌓인 것'도 그 수준으로 다시 나오고, 이후 로그도 같은 기준으로 이어진다.
    since_index는 레벨과 무관한 통산 일련번호(total_count)다. 레벨을 바꿔도 델타 위치가
    어긋나지 않게 하려면 이 기준이 레벨별 카운트가 아니라 전체 카운트여야 한다.
    limit는 최대 500줄 — 화면 랙은 DOM 줄 수에서 오므로 한 번에 넘기는 양을 여기서 묶는다.
    """
    effective_limit = max(1, min(limit or 300, 500))
    level_filter = _parse_level(min_level) if min_level is not None else UI_MIN_LOG_LEVEL

    with _LOCK:
        gap = False
        if since_index is not None and since_index >= 0:
            matched = [line for seq, lvl, line in _buffer
                       if seq > since_index and lvl >= level_filter]
            # 버퍼가 순환해 이전 수신 지점을 지나쳐 버린 경우 — 사이가 비었다고 알린다.
            oldest_seq = _buffer[0][0] if _buffer else _total_full_count
            if since_index + 1 < oldest_seq:
                gap = True
            lines = matched[-effective_limit:]
        else:
            lines = [line for _seq, lvl, line in _buffer if lvl >= level_filter][-effective_limit:]

        return {
            "lines": lines,
            "shown": len(lines),
            "total_count": _total_full_count,
            "full_log_count": _total_full_count,
            "buffered_count": len(_buffer),
            "truncated": _total_full_count > len(lines),
            "gap": gap,
            "session_file": _ensure_session_file(),
            "poll_interval_ms": 1000,
        }


def get_session_file_path() -> str:
    with _LOCK:
        return _ensure_session_file()


def flush_logs(timeout: float = 2.0):
    """Wait for all queued log items to be written to disk."""
    if _log_queue.empty():
        return
    try:
        _log_queue.join()
    except Exception:
        pass


def stop_logger():
    """Signal background logging thread to stop and flush remaining logs."""
    _stop_writer_event.set()
    flush_logs()
    if _writer_thread and _writer_thread.is_alive():
        _writer_thread.join(timeout=0.3)


import atexit
atexit.register(stop_logger)


def _detect_print_level(text: str) -> str:
    """Detect log level from raw printed string."""
    text_upper = text.upper()
    if "ERROR" in text_upper or "EXCEPTION" in text_upper or "TRACEBACK" in text_upper or "CRITICAL" in text_upper:
        return "ERROR"
    if "WARNING" in text_upper or "WARN" in text_upper or "[경고]" in text_upper:
        return "WARNING"
    if "[안내]" in text_upper or "[INFO]" in text_upper:
        return "INFO"
    # Detailed prints default to DEBUG to keep UI buffer clean and land in full log file
    return "DEBUG"


def install_print_capture():
    """builtins.print를 감싸서 기존 print(...) 로그도 파일에 남긴다."""
    global _print_capture_installed
    if _print_capture_installed:
        return
    _print_capture_installed = True

    def _patched_print(*args, **kwargs):
        _original_print(*args, **kwargs)
        try:
            text = " ".join(str(a) for a in args).strip()
            if text:
                level = _detect_print_level(text)
                log_event(text, source="print", level=level)
        except Exception:
            pass

    builtins.print = _patched_print


# ---------------------------------------------------------------- logging 모듈 -> 앱 로그
class _AppLogHandler(logging.Handler):
    """logging 기록을 log_event 로 넘긴다 — 파일(모든 수준)과 화면 버퍼(수준별 필터)에 같이 남는다."""

    def emit(self, record):
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + logging.Formatter().formatException(record.exc_info)
            log_event(message, source=record.name, level=record.levelno)
        except Exception:
            self.handleError(record)


_logging_bridge = None


def install_logging_bridge():
    """루트 logger 에 앱 로그 핸들러를 단다(앱 시작 때 한 번). 예전엔 core/storage_service 의 기록이 콘솔(stderr)로만
    가서 콘솔 없는 EXE 에서는 사라졌다(IMPROVEMENT_PLAN 3-8).

    루트 수준은 바꾸지 않는다(기본 WARNING) — 그래서 paramiko·netmiko 같은 라이브러리는 WARNING 이상만 오고,
    자기 logger 수준을 직접 낮춘 앱 모듈(storage_service: DEBUG)은 그 수준 그대로 온다."""
    global _logging_bridge
    if _logging_bridge is not None:
        return _logging_bridge
    _logging_bridge = _AppLogHandler(level=logging.DEBUG)
    logging.getLogger().addHandler(_logging_bridge)
    return _logging_bridge


# ---------------------------------------------------------------- 로그 보존
SESSION_LOG_PATTERNS = ("app_full_*.log", "structured_*.jsonl")


def prune_old_logs(max_age_days=30, keep_latest=20, now=None):
    """logs/ 의 세션 로그 보존 — 종류별로 최근 keep_latest 개는 나이와 상관없이 남기고, 그보다 오래된 것 중
    max_age_days 일이 지난 것을 지운다. 지금 쓰는 세션 파일은 건드리지 않는다. 반환: 지운 파일 수.
    예전엔 보존 규칙이 없어서 실행할 때마다 app_full_*.log 가 하나씩 영원히 쌓였다(IMPROVEMENT_PLAN 3-8)."""
    log_dir = AppPaths.logs_root()
    cutoff = (now if now is not None else time.time()) - max_age_days * 86400
    current = os.path.normcase(os.path.abspath(_session_file_path)) if _session_file_path else None
    removed = 0
    for pattern in SESSION_LOG_PATTERNS:
        files = []
        for path in log_dir.glob(pattern):
            try:
                files.append((path.stat().st_mtime, path))
            except OSError:
                continue
        files.sort(key=lambda item: item[0], reverse=True)
        for mtime, path in files[keep_latest:]:
            if mtime >= cutoff or os.path.normcase(os.path.abspath(path)) == current:
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed

