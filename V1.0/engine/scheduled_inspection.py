"""무인 정기점검 실행 — 수집 → 규칙 기반 로그 분석 → 정기점검 보고서(IMPROVEMENT_PLAN 4-3, 결정 D1).

작업 스케줄러가 `AutoCheck.exe --scheduled <일정 id>` 로 부르거나(창 없이 한 번 돌고 끝난다, main.py), 화면의
'지금 실행'이 앱 안에서 부른다(진행은 JobRunner, api/schedule_api.py). 어느 쪽이든 run_scheduled_inspection 하나다.

- 화면 상태를 건드리지 않는다: 활성 프로파일을 바꾸지 않고(예전 스케줄러는 set_active_project 를 불렀다) 일정의
  project id 로 장비 목록·명령 카탈로그·데이터 폴더를 직접 찾는다.
- 같은 프로파일은 한 번에 하나만: 프로파일 잠금(core/process_lock)을 잡지 못하면 '건너뜀'으로 끝난다. 잠금은
  앱을 열 때의 '중단된 run 정리'가 이 run 을 '중단'으로 바꾸지 않게도 한다.
- 결과를 숨기지 않는다: 성공·일부 실패·실패·건너뜀을 scheduled_runs.jsonl 에 남기고(화면의 실행 이력과 실패
  배지) 앱 로그에도 남긴다. 예전 스케줄러는 실패를 print 만 했고, 결과가 None 이어도 성공으로 봤다.
- 보고서는 이번 회차의 로그로만 만든다 — 수집하지 못한 장비는 지난 회차 로그 대신 '접속 불가'로 나온다.
"""
import json
import os
import threading
import uuid

from core.app_logger import log_event
from core.app_settings import scheduled_runs_path
from core.atomic_io import write_text_atomic
from core.process_lock import FileLock, is_locked, profile_lock_path
from core.utils.datetime import format_iso
from engine import inspection_schedule

SUCCESS, PARTIAL, FAILED, SKIPPED = "success", "partial", "failed", "skipped"
STATUS_LABELS = {SUCCESS: "성공", PARTIAL: "일부 실패", FAILED: "실패", SKIPPED: "건너뜀"}
# 작업 스케줄러의 '마지막 실행 결과'에 그대로 보인다.
EXIT_CODES = {SUCCESS: 0, FAILED: 1, PARTIAL: 2, SKIPPED: 3}
HISTORY_KEEP = 500

_HISTORY_LOCK = threading.Lock()


def exit_code(record):
    return EXIT_CODES.get(record.get("status"), 1)


def profile_dir_of(project_id):
    """일정의 프로파일 데이터 폴더(문자열) — 이름을 풀지 못하면 None."""
    from engine import log_storage
    customer, profile = log_storage.resolve_names_from_project(project_id)
    try:
        return log_storage.get_profile_dir(customer, profile)
    except ValueError:
        return None


def running_elsewhere(project_id):
    """이 프로파일의 무인 점검을 다른 곳(작업 스케줄러가 띄운 프로세스 등)이 돌리고 있는가."""
    profile_dir = profile_dir_of(project_id)
    return bool(profile_dir) and is_locked(profile_lock_path(profile_dir))


def run_scheduled_inspection(schedule_id, *, trigger="scheduler", on_progress=None):
    """일정 하나를 한 번 실행한다 -> 실행 기록(dict — 이력 파일에도 남긴다). 예외를 밖으로 던지지 않는다.
    trigger: 'scheduler'(작업 스케줄러) | 'manual'(화면의 '지금 실행' — 꺼진 일정도 돈다).
    on_progress(message, current, total): 진행 표시(화면의 JobRunner)."""
    record = {"id": uuid.uuid4().hex[:12], "schedule_id": schedule_id, "trigger": trigger,
              "project_id": None, "customer": "", "profile": "",
              "started_at": format_iso(), "finished_at": None, "status": None, "message": "",
              "run_id": None, "devices_ok": [], "devices_failed": {}, "problems": 0,
              "reports": [], "report_errors": {}}
    progress = on_progress or (lambda message, current=None, total=None: None)
    try:
        _run(record, schedule_id, trigger, progress)
    except Exception as exc:          # 무인 실행은 어떤 실패도 기록으로 남기고 끝나야 한다
        _finish(record, FAILED, f"예상하지 못한 오류: {exc}")
    if record["status"] is None:
        _finish(record, FAILED, "실행 결과를 알 수 없습니다.")
    record["finished_at"] = format_iso()
    _append_history(record)
    level = {SUCCESS: "INFO", SKIPPED: "WARNING", PARTIAL: "WARNING"}.get(record["status"], "ERROR")
    log_event(f"[무인 점검] {record['customer'] or '-'}/{record['profile'] or '-'} "
              f"{STATUS_LABELS[record['status']]} — {record['message']}", source="scheduled", level=level)
    try:
        # 실패·수집 실패는 알림 채널로도 알린다(4-4) — 작업 스케줄러가 띄운 프로세스는 곧 끝나므로 그 자리에서 보낸다.
        from engine.notifications import notifier
        notifier.notify_scheduled(record)
    except Exception as exc:              # 알림이 실패해도 기록은 이미 남았다
        log_event(f"[무인 점검] 알림 채널로 보내지 못했습니다: {exc}", source="scheduled", level="WARNING")
    return record


def _finish(record, status, message):
    record["status"], record["message"] = status, message
    return record


def _run(record, schedule_id, trigger, progress):
    from engine import log_storage, migration_manager
    from engine import project_manager as pm
    schedule = inspection_schedule.get(schedule_id)
    if schedule is None:
        return _finish(record, FAILED, "일정이 없습니다(지워졌을 수 있습니다) — 작업 스케줄러에 남은 이 작업을 지우세요.")
    project_id = record["project_id"] = schedule["project_id"]
    if trigger == "scheduler" and not schedule.get("enabled"):
        return _finish(record, SKIPPED, "일정이 꺼져 있어 건너뛰었습니다.")
    if not os.path.isfile(os.path.join(pm.LABS_DIR, project_id, "project_meta.yaml")):
        return _finish(record, FAILED, "일정의 프로파일이 없습니다(삭제·보관됨) — 일정을 지우세요.")
    record["customer"], record["profile"] = log_storage.resolve_names_from_project(project_id)
    pending = migration_manager.pending_work()
    if pending:
        return _finish(record, FAILED, f"앱을 한 번 열어 '{pending}'을(를) 마친 뒤에 무인 점검이 돕니다.")
    profile_dir = profile_dir_of(project_id)
    if not profile_dir:
        return _finish(record, FAILED, "프로파일 데이터 폴더를 찾지 못했습니다.")
    lock = FileLock(profile_lock_path(profile_dir))
    if not lock.acquire():
        return _finish(record, SKIPPED, "이 프로파일의 무인 점검이 이미 진행 중이라 건너뛰었습니다.")
    try:
        return _inspect(record, schedule, progress)
    finally:
        lock.release()


def _inspect(record, schedule, progress):
    from engine import collector, log_analysis, log_storage
    from engine import command_catalog as cc
    from engine import inspection_report_builder as report_builder
    from engine import project_manager as pm
    from engine import vendor_commands
    project_id, customer, profile = record["project_id"], record["customer"], record["profile"]
    paths = pm.project_paths(project_id)
    # 세션 점검과 같은 규칙 — 읽기 전용 정책(core/command_policy)을 통과한 활성 명령만, 장비마다 그 벤더의 명령으로 보낸다.
    catalog = cc.load_catalog(paths["commands_catalog"])
    commands, _skipped = cc.split_enabled_commands(catalog)
    if not commands:
        return _finish(record, FAILED, "활성화된 점검 명령이 없습니다(명령어 카탈로그를 확인하세요).")

    progress("장비 수집 중...", 0, None)
    results, errors, session_dir = collector.collect_all(
        paths["device_inventory"], project_id, commands,
        lab_meta_path=paths.get("lab_meta"), ip_allocation_path=paths.get("ip_allocation"),
        customer_name=customer, profile_name=profile, execution_mode="scheduled",
        on_progress=lambda done, total, device, error: progress(f"수집 {done}/{total} — {device}", done, total),
        commands_by_platform=vendor_commands.commands_by_platform(catalog))
    if results is None:
        return _finish(record, FAILED, "사전점검 실패 — 확인한 장비가 모두 응답하지 않았습니다(VPN·내부망 연결을 확인하세요).")
    if not results and not errors:
        return _finish(record, FAILED, "장비 목록에 사용으로 체크된 장비가 없습니다.")
    record["run_id"] = os.path.basename(os.path.dirname(os.path.normpath(session_dir)))
    record["devices_ok"] = sorted(results)
    record["devices_failed"] = {name: str(error) for name, error in sorted(errors.items())}
    if not results:
        return _finish(record, FAILED, f"장비 {len(errors)}대를 모두 수집하지 못했습니다(장비·계정·망을 확인하세요).")

    run_paths = next(p for p in log_storage.list_run_dirs(customer, profile) if p["run_id"] == record["run_id"])
    progress("로그 분석 중...", None, None)
    analysis = log_analysis.run_analysis(run_paths["original"], run_paths["problem"])
    record["problems"] = sum(item["problem_count"] for item in analysis)

    for fmt in schedule["formats"]:
        progress(f"{inspection_schedule.FORMAT_LABELS.get(fmt, fmt)} 보고서 만드는 중...", None, None)
        try:
            # 이번 회차의 raw 로만 — 수집하지 못한 장비가 지난 회차 로그로 '정상' 보고되지 않게.
            result = report_builder.export(customer, profile, fmt, project_id=project_id,
                                           log_dirs=[run_paths["original"]])
            record["reports"].append(result["filename"])
        except Exception as exc:      # 형식 하나가 실패해도(PDF 글꼴 등) 다른 형식은 만든다
            record["report_errors"][fmt] = str(exc)

    failed = len(record["devices_failed"])
    parts = [f"장비 {len(results)}대 수집" + (f"·{failed}대 실패" if failed else ""),
             f"이상 징후 {record['problems']}건", f"보고서 {len(record['reports'])}개"]
    if record["report_errors"]:
        parts.append(f"보고서 실패 {len(record['report_errors'])}개")
    return _finish(record, PARTIAL if failed or record["report_errors"] else SUCCESS, ", ".join(parts))


# ---------- 이력 ----------
def _read_history():
    path = scheduled_runs_path()
    if not os.path.exists(path):
        return []
    records = []
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue              # 쓰다 끊긴 줄 — 나머지는 읽는다
    return records


def _append_history(record):
    """한 줄을 더하고, 너무 길어지면 최근 HISTORY_KEEP 건만 남긴다."""
    path = scheduled_runs_path()
    with _HISTORY_LOCK:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        records = _read_history()
        if len(records) > HISTORY_KEEP * 2:
            write_text_atomic(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records[-HISTORY_KEEP:]))


def history(project_id=None, limit=20):
    """최근 실행 기록(최신 먼저). project_id 를 주면 그 프로파일 것만."""
    records = [r for r in _read_history() if project_id is None or r.get("project_id") == project_id]
    return list(reversed(records))[:limit]


def unseen_failures(seen_at=None):
    """확인하지 않은 실패(실패·일부 실패) — seen_at 뒤에 끝난 것, 최신 먼저. 화면의 실패 배지가 센다."""
    return [r for r in reversed(_read_history())
            if r.get("status") in (FAILED, PARTIAL) and (not seen_at or (r.get("finished_at") or "") > seen_at)]
