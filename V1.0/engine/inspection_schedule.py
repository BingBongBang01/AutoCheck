"""무인 정기점검 일정 — 프로파일마다 하나(IMPROVEMENT_PLAN 4-3).

일정은 <사용자 데이터>/schedules.yaml 에 두고, 정해진 시각에 깨우는 일은 Windows 작업 스케줄러가 한다
(engine/task_scheduler.py → `AutoCheck.exe --scheduled <일정 id>` → engine/scheduled_inspection.py).
3-1 에서 퇴역한 예전 스케줄러의 결함을 되풀이하지 않는다:
  - 경로는 AppPaths 로만 계산한다(예전엔 CWD 상대라 설치본의 설정을 읽지 못했다).
  - 받을 수 있는 형식(매일 / 매주 요일 / 매월 날짜 + 시:분)만 받고, 나머지는 이유를 붙여 거절한다
    (예전엔 '*/5'·'1-5' 같은 cron 식이 오류 없이 무시됐다).
  - 앱이 떠 있지 않아도 작업 스케줄러가 깨우고, PC 가 꺼져 있어 놓친 일정은 다음 기회에 돌린다(StartWhenAvailable).
일정은 프로파일의 project id(labs/<id>)로 가리킨다 — 프로파일 이름이 바뀌어도 그대로다.
"""
import datetime
import os
import re
import uuid

import yaml

from core.app_settings import schedules_path
from core.atomic_io import dump_yaml_atomic
from core.utils.datetime import format_iso

FREQUENCIES = {"daily": "매일", "weekly": "매주", "monthly": "매월"}
WEEKDAY_NAMES = ("월", "화", "수", "목", "금", "토", "일")       # datetime.weekday() 순서(월=0)
FORMAT_LABELS = {"excel": "Excel", "pdf": "PDF"}
# 29~31일은 없는 달이 있어 그 달에는 건너뛴다 — 헷갈리지 않게 1~28일만 받는다.
MAX_MONTHDAY = 28
_TIME_RE = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")

DEFAULT_SETTINGS = {"enabled": False, "frequency": "weekly", "time": "02:00", "weekdays": [0],
                    "monthday": 1, "formats": ["excel"]}


class ScheduleError(ValueError):
    """화면에 그대로 보여 줄 수 있는 일정 오류."""


def _int_list(values, low, high, label):
    try:
        numbers = sorted({int(v) for v in values or []})
    except (TypeError, ValueError):
        raise ScheduleError(f"{label} 값이 올바르지 않습니다: {values}") from None
    if any(n < low or n > high for n in numbers):
        raise ScheduleError(f"{label} 값이 올바르지 않습니다: {values}")
    return numbers


def normalize(settings):
    """화면이 보낸 값 -> 저장할 일정 필드. 받을 수 없는 값이면 ScheduleError(이유)."""
    from engine.inspection_report_builder import report_formats
    settings = settings or {}
    frequency = str(settings.get("frequency") or "").strip()
    if frequency not in FREQUENCIES:
        raise ScheduleError(f"반복 주기는 매일·매주·매월 중에서 고르세요(받은 값: {frequency or '없음'}).")
    time_text = str(settings.get("time") or "").strip()
    if not _TIME_RE.fullmatch(time_text):
        raise ScheduleError(f"시각은 00:00~23:59 사이를 HH:MM 으로 넣으세요(받은 값: {time_text or '없음'}).")
    weekdays = _int_list(settings.get("weekdays"), 0, 6, "요일")
    if frequency == "weekly" and not weekdays:
        raise ScheduleError("매주 실행할 요일을 하나 이상 고르세요.")
    monthday = settings.get("monthday")
    try:
        monthday = 1 if monthday in (None, "") else int(monthday)
    except (TypeError, ValueError):
        raise ScheduleError(f"날짜 값이 올바르지 않습니다: {settings.get('monthday')}") from None
    if not 1 <= monthday <= MAX_MONTHDAY:
        raise ScheduleError(f"매월 실행할 날짜는 1~{MAX_MONTHDAY}일 중에서 고르세요"
                            "(29~31일은 없는 달에 건너뛰게 됩니다).")
    known = {fmt["id"] for fmt in report_formats()}
    formats = [fmt for fmt in dict.fromkeys(settings.get("formats") or []) if fmt]
    unknown = [fmt for fmt in formats if fmt not in known]
    if unknown:
        raise ScheduleError(f"지원하지 않는 보고서 형식입니다: {', '.join(map(str, unknown))}")
    if not formats:
        raise ScheduleError("만들 보고서 형식(Excel·PDF)을 하나 이상 고르세요.")
    return {"enabled": bool(settings.get("enabled")), "frequency": frequency, "time": time_text,
            "weekdays": weekdays if frequency == "weekly" else [], "monthday": monthday, "formats": formats}


# ---------- 저장 ----------
def load():
    """{"schedules": [...], "alerts_seen_at": str|None}. 파일이 없거나 깨졌으면 빈 목록."""
    path = schedules_path()
    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as stream:
                data = yaml.safe_load(stream) or {}
        except (OSError, yaml.YAMLError) as exc:
            print(f"[무인 점검] 일정 파일을 읽을 수 없습니다({exc}) — 빈 목록으로 봅니다.")
            data = {}
    if not isinstance(data, dict):
        data = {}
    data["schedules"] = [s for s in data.get("schedules") or []
                         if isinstance(s, dict) and s.get("id") and s.get("project_id")]
    return data


def _save(data):
    dump_yaml_atomic(data, schedules_path())


def get(schedule_id):
    return next((s for s in load()["schedules"] if s["id"] == schedule_id), None)


def for_project(project_id):
    return next((s for s in load()["schedules"] if s["project_id"] == project_id), None)


def upsert(project_id, settings):
    """프로파일의 일정을 만들거나 고친다(프로파일마다 하나 — id 는 처음 만들 때 정해져 바뀌지 않는다)."""
    fields = normalize(settings)
    data = load()
    now = format_iso()
    schedule = next((s for s in data["schedules"] if s["project_id"] == project_id), None)
    if schedule is None:
        schedule = {"id": uuid.uuid4().hex[:12], "project_id": project_id, "created_at": now}
        data["schedules"].append(schedule)
    schedule.update(fields, updated_at=now)
    _save(data)
    return dict(schedule)


def remove(project_id):
    """프로파일의 일정을 지운다 -> 지운 일정(없으면 None)."""
    data = load()
    schedule = next((s for s in data["schedules"] if s["project_id"] == project_id), None)
    if schedule is not None:
        data["schedules"] = [s for s in data["schedules"] if s is not schedule]
        _save(data)
    return schedule


def alerts_seen_at():
    return load().get("alerts_seen_at")


def mark_alerts_seen(when=None):
    """실패 배지를 확인한 시각 — 그 뒤에 끝난 실패만 배지에 센다."""
    data = load()
    data["alerts_seen_at"] = when or format_iso()
    _save(data)
    return data["alerts_seen_at"]


# ---------- 시각 ----------
def _runs_on(schedule, day):
    if schedule["frequency"] == "daily":
        return True
    if schedule["frequency"] == "weekly":
        return day.weekday() in schedule.get("weekdays") or ()
    return day.day == schedule.get("monthday")


def next_run(schedule, now=None):
    """다음 실행 시각(작업 스케줄러 트리거와 같은 규칙) — 꺼져 있으면 None. 지금보다 뒤인 첫 시각."""
    if not schedule or not schedule.get("enabled"):
        return None
    now = now or datetime.datetime.now()
    hour, minute = (int(part) for part in schedule["time"].split(":"))
    for offset in range(0, 400):
        day = now.date() + datetime.timedelta(days=offset)
        when = datetime.datetime.combine(day, datetime.time(hour, minute))
        if when > now and _runs_on(schedule, day):
            return when
    return None


def describe(schedule):
    """'매주 월·목 02:00 · Excel, PDF' 같은 한 줄 설명."""
    if not schedule:
        return ""
    if schedule["frequency"] == "weekly":
        when = f"매주 {'·'.join(WEEKDAY_NAMES[d] for d in schedule.get('weekdays') or [])}"
    elif schedule["frequency"] == "monthly":
        when = f"매월 {schedule.get('monthday')}일"
    else:
        when = "매일"
    formats = ", ".join(FORMAT_LABELS.get(f, f) for f in schedule.get("formats") or [])
    return f"{when} {schedule['time']} · {formats}"
