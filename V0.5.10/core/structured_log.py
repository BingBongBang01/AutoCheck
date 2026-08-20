"""
Structured Logging — Job/Device/Command/Retry/Timing/Prompt/Parser/Rule을
자유 텍스트 print가 아니라 고정 필드를 가진 JSON 한 줄로 남긴다.
logs/structured_<date>.jsonl 에 append하고, core/app_logger의 화면용 로그 버퍼에도
압축된 한 줄 요약을 같이 남겨서(기존 '전체 로그' 화면과 호환) 이중 관리 없이 한 번에 본다.
로깅 실패가 채점 파이프라인을 절대 막으면 안 되므로 모든 I/O는 try/except로 감싼다.
"""
import datetime
import json
import threading

from core.paths import AppPaths

_LOCK = threading.Lock()
_FIELDS = ("job_id", "device", "command", "retry", "duration_ms", "prompt", "check_id", "rule", "status")


def _log_path():
    date = datetime.date.today().isoformat()
    return AppPaths.logs_root() / f"structured_{date}.jsonl"


def log_event(stage, event, message="", **fields):
    """stage: 'collector'/'parser'/'rule' 등 파이프라인 단계.
    event: 'device_start'/'command_done'/'retry'/'device_done'/'parse_step'/'rule_eval' 등.
    fields: job_id/device/command/retry/duration_ms/prompt/check_id/rule/status 중 해당하는 것만 채움.
    """
    record = {
        "ts": datetime.datetime.now().isoformat(),
        "stage": stage,
        "event": event,
    }
    for key in _FIELDS:
        if key in fields and fields[key] is not None:
            record[key] = fields[key]
    if message:
        record["message"] = message

    try:
        with _LOCK:
            with open(_log_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass

    try:
        from core.app_logger import log_event as app_log_event
        summary = " ".join(f"{k}={record[k]}" for k in ("job_id", "device", "command", "retry", "duration_ms", "rule") if k in record)
        app_log_event(f"[{stage}] {event} {summary} {message}".strip(), source="structured")
    except Exception:
        pass

    return record


if __name__ == "__main__":
    log_event("collector", "command_done", job_id="s1", device="Core1", command="show vlan brief", duration_ms=120)
    print("기록 위치:", _log_path())
