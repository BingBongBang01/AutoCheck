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
import os
import queue
import re
import threading
from collections import deque
from typing import Any, Dict, List, Optional, Union

from core.paths import AppPaths

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
_MAX_MEMORY_LINES = 500
_buffer = deque(maxlen=_MAX_MEMORY_LINES)
_total_ui_count = 0
_total_full_count = 0

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
    """
    global _total_ui_count, _total_full_count
    
    level_num = _parse_level(level)
    level_name = next((k for k, v in LOG_LEVELS.items() if v == level_num), "INFO")
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    formatted_line = f"[{ts}] [{level_name}] [{source}] {message}"

    # 1. Enqueue to async file writer (ALL log levels)
    _ensure_writer_started()
    _log_queue.put(formatted_line)

    with _LOCK:
        _total_full_count += 1

        # 2. Append to UI buffer only if level >= UI_MIN_LOG_LEVEL (INFO, WARNING, ERROR, CRITICAL)
        if level_num >= UI_MIN_LOG_LEVEL:
            _buffer.append(formatted_line)
            _total_ui_count += 1

    return formatted_line


def get_recent_logs(
    limit: int = 300,
    since_index: Optional[int] = None,
    min_level: Optional[Union[str, int]] = None,
) -> Dict[str, Any]:
    """
    UI 표시용 최근 로그 또는 Delta 로그 반환. (INFO 이상 전용 버퍼)
    - since_index가 지정되면 마지막 수신 시점 이후 새로 발생한 delta 로그만 전달
    - limit는 최대 500줄로 IPC 페이로드 폭증 방지
    """
    effective_limit = max(1, min(limit or 300, 500))
    level_filter = _parse_level(min_level) if min_level is not None else UI_MIN_LOG_LEVEL
    
    with _LOCK:
        if level_filter > UI_MIN_LOG_LEVEL:
            filtered_lines = [
                line for line in _buffer
                if any(f"[{lvl}]" in line for lvl, num in LOG_LEVELS.items() if num >= level_filter)
            ]
        else:
            filtered_lines = list(_buffer)

        buffer_len = len(filtered_lines)
        gap = False

        if since_index is not None and since_index >= 0:
            new_count = _total_ui_count - since_index
            if new_count <= 0:
                delta_lines = []
            elif new_count <= buffer_len:
                delta_lines = filtered_lines[-new_count:]
            else:
                # 버퍼가 순환하여 이전 수신 지점을 초과한 경우 (Overflow Gap)
                delta_lines = filtered_lines[-effective_limit:]
                gap = True
            lines = delta_lines[:effective_limit]
        else:
            lines = filtered_lines[-effective_limit:]

        return {
            "lines": lines,
            "shown": len(lines),
            "total_count": _total_ui_count,
            "full_log_count": _total_full_count,
            "truncated": _total_ui_count > len(lines),
            "gap": gap,
            "session_file": _ensure_session_file(),
            "poll_interval_ms": 1000,
        }


def read_session_log_chunk(start_line: int = 1, max_lines: int = 500) -> Dict[str, Any]:
    """
    세션 전체 로그 파일(app_full_<timestamp>.log)에서 특정 범위의 라인을 덩어리(chunk)로 읽어 반환.
    Full Log View 용도. RAM 및 IPC 메모리 낭비를 방지한다.
    """
    filepath = get_session_file_path()
    flush_logs()  # 파일 읽기 전 디스크 동기화

    start_line = max(1, start_line)
    max_lines = max(1, min(max_lines, 1000))

    if not os.path.exists(filepath):
        return {
            "lines": [],
            "start_line": start_line,
            "end_line": start_line - 1,
            "total_lines": 0,
            "eof": True,
            "file_size": 0,
        }

    try:
        file_size = os.path.getsize(filepath)
        lines = []
        total_lines = 0

        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                total_lines += 1
                if start_line <= total_lines < (start_line + max_lines):
                    lines.append(line.rstrip("\r\n"))

        end_line = start_line + len(lines) - 1 if lines else start_line - 1
        eof = (start_line + len(lines)) > total_lines

        return {
            "lines": lines,
            "start_line": start_line if lines else start_line,
            "end_line": end_line,
            "total_lines": total_lines,
            "eof": eof,
            "file_size": file_size,
        }
    except Exception as exc:
        return {
            "error": str(exc),
            "lines": [],
            "start_line": start_line,
            "end_line": start_line - 1,
            "total_lines": 0,
            "eof": True,
            "file_size": 0,
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
