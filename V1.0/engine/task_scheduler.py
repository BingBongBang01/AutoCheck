"""Windows 작업 스케줄러 등록 — 무인 정기점검을 정해진 시각에 깨운다(IMPROVEMENT_PLAN 4-3).

작업 하나 = 일정 하나: `\\AutoCheck\\scheduled-<일정 id>` 가 `AutoCheck.exe --scheduled <일정 id> --data-root <폴더>`
를 실행한다(소스 실행이면 pythonw 로 main.py). 등록은 schtasks.exe 에 작업 정의 XML 을 넘긴다 — 명령줄 옵션
만으로는 '놓친 일정은 다음 기회에 실행'(StartWhenAvailable)과 '겹치면 새 실행은 건너뜀'(IgnoreNew)을 줄 수 없다.

실행 계정은 등록한 Windows 사용자, '로그인해 있을 때만'(InteractiveToken)이다. 장비 비밀번호·API 키는 그 사용자의
DPAPI 로 봉인돼 있어(4-1) 비밀번호 없이 도는 다른 로그온 방식으로는 풀 수 없고, 비밀번호를 작업에 저장하지
않기 위해서다. 화면 잠금 상태는 괜찮다.

schtasks 호출은 _schtasks 한 곳으로만 한다(테스트가 바꿔 끼운다). Windows 가 아니면 available() 이 False 다.
"""
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from core.paths import AppPaths
from engine import inspection_schedule

TASK_FOLDER = "AutoCheck"
_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
_DAY_TAGS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTH_TAGS = ("January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December")
# 한 번 실행이 이보다 길면 작업 스케줄러가 끝낸다 — 장비가 수백 대여도 이 안에 끝나야 정상이다.
EXECUTION_TIME_LIMIT = "PT4H"


def task_name(schedule_id):
    return f"\\{TASK_FOLDER}\\scheduled-{schedule_id}"


def available():
    return os.name == "nt" and shutil.which("schtasks") is not None


def _console_encoding():
    try:
        import ctypes
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:
        return "utf-8"


def _schtasks(args):
    """schtasks.exe 를 한 번 부른다 -> (종료 코드, 출력 문자열). 출력은 콘솔 코드 페이지(한국어 Windows 는 cp949)다."""
    completed = subprocess.run(["schtasks", *args], capture_output=True, timeout=30,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    text = (completed.stdout or b"") + (completed.stderr or b"")
    return completed.returncode, text.decode(_console_encoding(), errors="replace").strip()


def launch_command(schedule_id):
    """작업이 실행할 (프로그램, 인자, 작업 폴더). EXE 면 AutoCheck.exe 자신을, 소스 실행이면 콘솔 창이 뜨지 않는
    pythonw 로 main.py 를 부른다. 데이터 폴더를 인자로 넘긴다 — 등록한 앱과 같은 폴더를 쓰게(AUTOCHECK_HOME)."""
    data_root = str(AppPaths.user_data_root())
    if data_root.endswith("\\"):
        data_root += "."            # "D:\" 는 따옴표 안에서 닫는 따옴표를 이스케이프한다
    tail = f'--scheduled {schedule_id} --data-root "{data_root}"'
    if getattr(sys, "frozen", False):
        executable = sys.executable
        return executable, tail, os.path.dirname(executable)
    python = sys.executable
    pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
    if os.path.exists(pythonw):
        python = pythonw
    root = str(AppPaths.app_root())
    return python, f'"{os.path.join(root, "main.py")}" {tail}', root


def _user_id():
    domain, user = os.environ.get("USERDOMAIN"), os.environ.get("USERNAME")
    return f"{domain}\\{user}" if domain and user else None


def task_xml(schedule, *, description, command, arguments, working_dir, start, user_id=None):
    """작업 정의 XML(작업 스케줄러 스키마 1.2) 문자열. start: 첫 실행 시각 — 지난 시각으로 두면 등록하자마자
    '놓친 일정'으로 보고 바로 실행할 수 있어서 다음 실행 시각을 넣는다."""
    ET.register_namespace("", _NS)

    def child(parent, tag, text=None, **attrs):
        element = ET.SubElement(parent, f"{{{_NS}}}{tag}", attrs)
        if text is not None:
            element.text = str(text)
        return element

    task = ET.Element(f"{{{_NS}}}Task", {"version": "1.2"})
    info = child(task, "RegistrationInfo")
    child(info, "Author", "AutoCheck")
    child(info, "Description", description)

    trigger = child(child(task, "Triggers"), "CalendarTrigger")
    child(trigger, "StartBoundary", start.strftime("%Y-%m-%dT%H:%M:%S"))
    child(trigger, "Enabled", "true")
    if schedule["frequency"] == "daily":
        by_day = child(trigger, "ScheduleByDay")
        child(by_day, "DaysInterval", 1)
    elif schedule["frequency"] == "weekly":
        by_week = child(trigger, "ScheduleByWeek")
        days = child(by_week, "DaysOfWeek")
        for day in schedule["weekdays"]:
            child(days, _DAY_TAGS[day])
        child(by_week, "WeeksInterval", 1)
    else:
        by_month = child(trigger, "ScheduleByMonth")
        child(child(by_month, "DaysOfMonth"), "Day", schedule["monthday"])
        months = child(by_month, "Months")
        for month in _MONTH_TAGS:
            child(months, month)

    principal = child(child(task, "Principals"), "Principal", id="Author")
    if user_id:
        child(principal, "UserId", user_id)
    child(principal, "LogonType", "InteractiveToken")
    child(principal, "RunLevel", "LeastPrivilege")

    settings = child(task, "Settings")
    child(settings, "MultipleInstancesPolicy", "IgnoreNew")       # 앞 실행이 안 끝났으면 새 실행은 건너뛴다
    child(settings, "DisallowStartIfOnBatteries", "false")
    child(settings, "StopIfGoingOnBatteries", "false")
    child(settings, "AllowHardTerminate", "true")
    child(settings, "StartWhenAvailable", "true")                 # 꺼져 있어 놓친 일정은 켜진 뒤 실행
    child(settings, "RunOnlyIfNetworkAvailable", "false")
    idle = child(settings, "IdleSettings")
    child(idle, "StopOnIdleEnd", "false")
    child(idle, "RestartOnIdle", "false")
    child(settings, "AllowStartOnDemand", "true")
    child(settings, "Enabled", "true")
    child(settings, "Hidden", "false")
    child(settings, "RunOnlyIfIdle", "false")
    child(settings, "WakeToRun", "false")
    child(settings, "ExecutionTimeLimit", EXECUTION_TIME_LIMIT)
    child(settings, "Priority", 7)

    action = child(child(task, "Actions", Context="Author"), "Exec")
    child(action, "Command", command)
    child(action, "Arguments", arguments)
    child(action, "WorkingDirectory", working_dir)
    return '<?xml version="1.0" encoding="UTF-16"?>\n' + ET.tostring(task, encoding="unicode")


def register(schedule, description, now=None):
    """일정을 작업 스케줄러에 등록한다(같은 이름이 있으면 바꾼다) -> {"ok": True, "task_name"} | {"ok": False, "error"}."""
    if not available():
        return {"ok": False, "error": "이 PC 에서는 Windows 작업 스케줄러를 쓸 수 없습니다(Windows 에서만 등록됩니다)."}
    start = inspection_schedule.next_run(dict(schedule, enabled=True), now=now)
    if start is None:
        return {"ok": False, "error": "다음 실행 시각을 계산하지 못했습니다 — 일정을 확인하세요."}
    command, arguments, working_dir = launch_command(schedule["id"])
    xml = task_xml(schedule, description=description, command=command, arguments=arguments,
                   working_dir=working_dir, start=start, user_id=_user_id())
    handle, path = tempfile.mkstemp(suffix=".xml", prefix="autocheck_task_")
    os.close(handle)
    try:
        with open(path, "w", encoding="utf-16") as stream:      # 작업 정의는 UTF-16(BOM 포함)으로 넘긴다
            stream.write(xml)
        code, output = _schtasks(["/Create", "/TN", task_name(schedule["id"]), "/XML", path, "/F"])
    finally:
        os.unlink(path)
    if code != 0:
        return {"ok": False, "error": f"작업 스케줄러 등록 실패: {output or f'종료 코드 {code}'}"}
    return {"ok": True, "task_name": task_name(schedule["id"])}


def is_registered(schedule_id):
    """등록돼 있으면 True, 없으면 False, 작업 스케줄러를 쓸 수 없으면 None."""
    if not available():
        return None
    code, _output = _schtasks(["/Query", "/TN", task_name(schedule_id)])
    return code == 0


def unregister(schedule_id):
    """작업을 지운다 — 처음부터 없었어도 성공으로 본다."""
    if not available():
        return {"ok": True}
    code, output = _schtasks(["/Delete", "/TN", task_name(schedule_id), "/F"])
    if code == 0 or not is_registered(schedule_id):
        return {"ok": True}
    return {"ok": False, "error": f"작업 스케줄러에서 지우지 못했습니다: {output or f'종료 코드 {code}'}"}


def next_run_text(schedule, now=None):
    when = inspection_schedule.next_run(schedule, now=now)
    return when.strftime("%Y-%m-%d %H:%M") if isinstance(when, datetime.datetime) else ""
