"""무인 정기점검 — IMPROVEMENT_PLAN 4-3(결정 D1: 수집 → 로그 분석 → 정기점검 보고서로 다시 잇는다).

3-1 에서 퇴역한 예전 스케줄러의 결함을 요구사항으로 삼았다 — 여기서 하나씩 고정한다:
  - cron 식('*/5', '1-5')을 오류 없이 무시했다 → 받을 수 있는 형식만 받고 이유를 붙여 거절한다.
  - 실행 중에 도래한 일정을 놓쳤다 → 작업 스케줄러가 깨우고 놓친 일정은 다음 기회에(StartWhenAvailable), 겹치면 건너뜀.
  - 실패를 print 만 했고 결과가 None 이어도 성공 → 성공·일부 실패·실패·건너뜀을 이력에 남기고 실패 배지로 알린다.
  - 실행하면 화면의 활성 프로젝트를 바꿨다 → 일정의 project id 로 직접 찾고 활성 프로파일은 그대로 둔다.
실제 장비·실제 작업 스케줄러에는 닿지 않는다(netmiko 접속기와 schtasks 호출을 가짜로 바꾼다).
"""
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from core.paths import AppPaths
from core.process_lock import FileLock, is_locked, profile_lock_path
from engine import collector
from engine import inspection_schedule as store
from engine import scheduled_inspection as runs
from engine import task_scheduler

ROOT = Path(__file__).resolve().parent.parent
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
WEDNESDAY = datetime.datetime(2026, 10, 28, 10, 0)


@pytest.fixture(autouse=True)
def no_real_task_scheduler(monkeypatch):
    """실제 작업 스케줄러는 건드리지 않는다 — 테스트가 가짜로 바꾸지 않은 schtasks 호출은 실패시킨다."""
    def refuse(args):
        raise AssertionError(f"실제 schtasks 를 부르려 했다: {args}")

    monkeypatch.setattr(task_scheduler, "_schtasks", refuse)


@pytest.fixture
def root(monkeypatch):
    from core.context_cache import workspace_cache
    from engine import customer_manager as cm
    from engine import project_manager as pm
    original = AppPaths._user_data_root
    data_root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths._user_data_root = data_root
    AppPaths.forget_ensured()
    monkeypatch.setattr(pm, "LABS_DIR", str(data_root / "labs"))
    monkeypatch.setattr(pm, "STATE_FILE", str(data_root / "config" / "active_project.yaml"))
    monkeypatch.setattr(cm, "CUSTOMERS_PATH", str(data_root / "config" / "customers.yaml"))
    workspace_cache.invalidate()
    yield data_root
    workspace_cache.invalidate()
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


WEEKLY = {"enabled": True, "frequency": "weekly", "time": "02:00", "weekdays": [3, 0, 3], "formats": ["excel"]}


# --------------------------------------------------------------------------- 1. 일정

def test_a_valid_schedule_is_normalized():
    assert store.normalize(WEEKLY) == {"enabled": True, "frequency": "weekly", "time": "02:00",
                                       "weekdays": [0, 3], "monthday": 1, "formats": ["excel"]}
    daily = store.normalize({"frequency": "daily", "time": "23:59", "weekdays": [1], "formats": ["pdf", "pdf"]})
    assert daily["weekdays"] == [] and daily["formats"] == ["pdf"] and daily["enabled"] is False


@pytest.mark.parametrize("change, message", [
    ({"frequency": "*/5"}, "반복 주기"),                      # 예전 스케줄러가 조용히 무시하던 cron 식
    ({"frequency": "1-5"}, "반복 주기"),
    ({"time": "25:00"}, "시각"),
    ({"time": "7:05"}, "시각"),
    ({"time": ""}, "시각"),
    ({"weekdays": []}, "요일"),
    ({"weekdays": [7]}, "요일"),
    ({"weekdays": ["월"]}, "요일"),
    ({"frequency": "monthly", "monthday": 29}, "1~28"),
    ({"frequency": "monthly", "monthday": 0}, "1~28"),
    ({"frequency": "monthly", "monthday": "말일"}, "날짜"),
    ({"formats": []}, "보고서 형식"),
    ({"formats": ["docx"]}, "docx"),
])
def test_unsupported_schedules_are_refused_with_a_reason(change, message):
    with pytest.raises(store.ScheduleError, match=message):
        store.normalize({**WEEKLY, **change})


@pytest.mark.parametrize("schedule, now, expected", [
    ({"frequency": "daily", "time": "02:00"}, WEDNESDAY, datetime.datetime(2026, 10, 29, 2, 0)),
    ({"frequency": "daily", "time": "23:30"}, WEDNESDAY, datetime.datetime(2026, 10, 28, 23, 30)),
    ({"frequency": "weekly", "time": "02:00", "weekdays": [0, 3]}, WEDNESDAY, datetime.datetime(2026, 10, 29, 2, 0)),
    ({"frequency": "weekly", "time": "02:00", "weekdays": [0, 3]},
     datetime.datetime(2026, 10, 29, 3, 0), datetime.datetime(2026, 11, 2, 2, 0)),
    ({"frequency": "monthly", "time": "02:00", "monthday": 1}, WEDNESDAY, datetime.datetime(2026, 11, 1, 2, 0)),
    ({"frequency": "monthly", "time": "09:00", "monthday": 28}, WEDNESDAY, datetime.datetime(2026, 11, 28, 9, 0)),
])
def test_next_run_follows_the_trigger_rules(schedule, now, expected):
    assert store.next_run({"enabled": True, "weekdays": [], "monthday": 1, **schedule}, now=now) == expected


def test_a_disabled_schedule_has_no_next_run():
    assert store.next_run({**store.normalize(WEEKLY), "enabled": False}, now=WEDNESDAY) is None


def test_one_schedule_per_profile_with_a_stable_id(root):
    first = store.upsert("p1", WEEKLY)
    again = store.upsert("p1", {**WEEKLY, "time": "03:30"})
    other = store.upsert("p2", {**WEEKLY, "frequency": "monthly", "monthday": 15})
    assert again["id"] == first["id"] and again["time"] == "03:30" and other["id"] != first["id"]
    assert [s["project_id"] for s in store.load()["schedules"]] == ["p1", "p2"]
    assert store.get(first["id"])["time"] == "03:30" and store.for_project("p2")["monthday"] == 15
    assert store.describe(again) == "매주 월·목 03:30 · Excel"
    assert store.remove("p1")["id"] == first["id"] and store.for_project("p1") is None


# --------------------------------------------------------------------------- 2. 작업 스케줄러

def _xml_tree(xml):
    declaration, body = xml.split("\n", 1)
    assert declaration.rstrip("\r") == '<?xml version="1.0" encoding="UTF-16"?>'   # 파일로 쓰면 CRLF
    return ET.fromstring(body)


def _task(schedule, **overrides):
    kwargs = dict(description="AutoCheck 무인 정기점검 — A&B <고객사> / 2026-10", command=r"C:\App\AutoCheck.exe",
                  arguments='--scheduled abc --data-root "C:\\Data"', working_dir=r"C:\App",
                  start=datetime.datetime(2026, 10, 29, 2, 0), user_id="PC\\ops")
    kwargs.update(overrides)
    return _xml_tree(task_scheduler.task_xml(schedule, **kwargs))


def test_the_task_wakes_missed_runs_skips_overlaps_and_runs_as_the_logged_on_user():
    task = _task(store.normalize(WEEKLY))
    settings = task.find("t:Settings", NS)
    assert settings.find("t:StartWhenAvailable", NS).text == "true"          # 꺼져 있어 놓친 일정은 켜진 뒤에
    assert settings.find("t:MultipleInstancesPolicy", NS).text == "IgnoreNew"  # 앞 실행이 안 끝났으면 건너뜀
    assert settings.find("t:ExecutionTimeLimit", NS).text == task_scheduler.EXECUTION_TIME_LIMIT
    assert settings.find("t:DisallowStartIfOnBatteries", NS).text == "false"
    principal = task.find("t:Principals/t:Principal", NS)
    assert principal.find("t:LogonType", NS).text == "InteractiveToken"       # 비밀번호를 작업에 저장하지 않는다
    assert principal.find("t:UserId", NS).text == "PC\\ops"
    action = task.find("t:Actions/t:Exec", NS)
    assert action.find("t:Command", NS).text == r"C:\App\AutoCheck.exe"
    assert action.find("t:Arguments", NS).text == '--scheduled abc --data-root "C:\\Data"'
    assert task.find("t:RegistrationInfo/t:Description", NS).text == "AutoCheck 무인 정기점검 — A&B <고객사> / 2026-10"


def test_each_frequency_becomes_a_calendar_trigger():
    weekly = _task(store.normalize(WEEKLY)).find("t:Triggers/t:CalendarTrigger", NS)
    assert weekly.find("t:StartBoundary", NS).text == "2026-10-29T02:00:00"
    assert [day.tag.split("}")[1] for day in weekly.find("t:ScheduleByWeek/t:DaysOfWeek", NS)] == ["Monday", "Thursday"]
    daily = _task(store.normalize({**WEEKLY, "frequency": "daily"})).find("t:Triggers/t:CalendarTrigger", NS)
    assert daily.find("t:ScheduleByDay/t:DaysInterval", NS).text == "1"
    monthly = _task(store.normalize({**WEEKLY, "frequency": "monthly", "monthday": 15}))
    by_month = monthly.find("t:Triggers/t:CalendarTrigger/t:ScheduleByMonth", NS)
    assert by_month.find("t:DaysOfMonth/t:Day", NS).text == "15" and len(by_month.find("t:Months", NS)) == 12


def test_the_exe_runs_itself_and_the_source_runs_main_py_without_a_console(root, monkeypatch):
    python = sys.executable
    command, arguments, working_dir = task_scheduler.launch_command("abc123")
    assert Path(command).name.lower() in ("pythonw.exe", "python.exe") and Path(command).parent == Path(python).parent
    assert arguments == f'"{AppPaths.app_root() / "main.py"}" --scheduled abc123 --data-root "{AppPaths.user_data_root()}"'
    assert working_dir == str(AppPaths.app_root())
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\AutoCheck\AutoCheck.exe")
    command, arguments, working_dir = task_scheduler.launch_command("abc123")
    assert (command, working_dir) == (r"C:\Apps\AutoCheck\AutoCheck.exe", r"C:\Apps\AutoCheck")
    assert arguments == f'--scheduled abc123 --data-root "{AppPaths.user_data_root()}"'


def test_registering_hands_schtasks_a_utf16_task_definition(root, monkeypatch):
    calls = []

    def fake_schtasks(args):
        xml_path = args[args.index("/XML") + 1] if "/XML" in args else None
        calls.append((args, Path(xml_path).read_bytes() if xml_path else None))
        return 0, "성공: 예약된 작업을 만들었습니다."

    monkeypatch.setattr(task_scheduler, "available", lambda: True)
    monkeypatch.setattr(task_scheduler, "_schtasks", fake_schtasks)
    schedule = store.upsert("p1", WEEKLY)
    result = task_scheduler.register(schedule, "설명", now=WEDNESDAY)
    assert result == {"ok": True, "task_name": f"\\AutoCheck\\scheduled-{schedule['id']}"}
    args, data = calls[0]
    assert args[:3] == ["/Create", "/TN", result["task_name"]] and args[-1] == "/F"
    assert data.startswith(b"\xff\xfe")                                        # UTF-16 LE + BOM
    task = _xml_tree(data.decode("utf-16"))
    assert task.find("t:Triggers/t:CalendarTrigger/t:StartBoundary", NS).text == "2026-10-29T02:00:00"
    assert f"--scheduled {schedule['id']}" in task.find("t:Actions/t:Exec/t:Arguments", NS).text
    assert not Path(args[args.index("/XML") + 1]).exists()                     # 넘긴 XML 은 지운다


def test_a_failed_registration_reports_schtasks_own_words(root, monkeypatch):
    monkeypatch.setattr(task_scheduler, "available", lambda: True)
    monkeypatch.setattr(task_scheduler, "_schtasks", lambda args: (1, "오류: 액세스가 거부되었습니다."))
    result = task_scheduler.register(store.upsert("p1", WEEKLY), "설명", now=WEDNESDAY)
    assert result["ok"] is False and "액세스가 거부" in result["error"]


def test_removing_a_task_that_is_already_gone_is_fine(monkeypatch):
    monkeypatch.setattr(task_scheduler, "available", lambda: True)
    monkeypatch.setattr(task_scheduler, "_schtasks", lambda args: (1, "지정된 파일을 찾을 수 없습니다."))
    assert task_scheduler.unregister("abc") == {"ok": True}
    monkeypatch.setattr(task_scheduler, "available", lambda: False)
    assert task_scheduler.is_registered("abc") is None
    assert "Windows" in task_scheduler.register({"id": "abc"}, "설명")["error"]


# --------------------------------------------------------------------------- 3. 실행

class FakeConnection:
    """netmiko 자리 — Agg1(10.0.0.2)은 응답하지 않는다."""
    hosts = []

    def __init__(self, **kwargs):
        FakeConnection.hosts.append(kwargs["host"])
        if kwargs["host"] == "10.0.0.2":
            raise OSError("TCP connection to device failed")

    def check_enable_mode(self):
        return True

    def send_command_timing(self, text, **kwargs):
        return ("Core1#show version\nArista vEOS\nCore1#show logging\nOct 28 10:00:01 Core1 Ebra: "
                "%LINEPROTO-5-UPDOWN: Line protocol on Interface Ethernet1, changed state to down\nCore1#")

    def send_command(self, command, **kwargs):
        return ""

    def disconnect(self):
        pass


@pytest.fixture
def lab(root, monkeypatch):
    """고객사 ABC 의 프로파일 P1(장비 2대)·P2(활성) — P1 에는 지난 회차의 Agg1 로그가 있다."""
    from api.customer_profile_api import CustomerProfileApiMixin
    from api.project_core_api import ProjectCoreApiMixin
    from engine import device_inventory as di
    from engine import log_storage, migration_manager
    from engine import project_manager as pm

    class Api(CustomerProfileApiMixin, ProjectCoreApiMixin):
        pass

    api = Api()
    api.create_customer("ABC")
    cid = next(c["id"] for c in api.get_customer_profiles())
    for name in ("P1", "P2"):
        api.create_inspection_profile(cid, name)
    ids = {p["profile_name"]: p["id"] for c in api.get_customer_profiles() for p in c["profiles"]}
    di.save_inventory({"defaults": {}, "devices": [
        {"name": "Core1", "management_ip": "10.0.0.1", "username": "ops", "password": "pw", "enabled": True},
        {"name": "Agg1", "management_ip": "10.0.0.2", "username": "ops", "password": "pw", "enabled": True},
    ]}, pm.project_paths(ids["P1"])["device_inventory"])
    old = log_storage.generate_new_run_dir("ABC", "P1", device_count=1, execution_mode="terminal_inspection")
    Path(old["original"], "20261001_090000_raw_Agg1.txt").write_text("Agg1#show version\nArista vEOS\n",
                                                                      encoding="utf-8")
    (root / "connection.yaml").write_text(yaml.safe_dump({"ssh": {"retry_count": 1, "retry_delay_sec": 0}}),
                                          encoding="utf-8")
    FakeConnection.hosts = []
    monkeypatch.setattr(collector, "ConnectHandler", FakeConnection)
    monkeypatch.setattr(migration_manager, "pending_work", lambda: None)
    schedule = store.upsert(ids["P1"], WEEKLY)
    return {"ids": ids, "schedule": schedule, "old_run": old["run_id"]}


def test_a_scheduled_run_collects_analyzes_and_reports_without_touching_the_gui(lab, monkeypatch):
    from engine import inspection_report_builder as builder
    from engine import log_storage
    from engine import project_manager as pm
    exported = []
    real_export = builder.export
    monkeypatch.setattr(builder, "export", lambda *a, **k: (exported.append(k), real_export(*a, **k))[1])
    active_before = pm.get_active_project()

    record = runs.run_scheduled_inspection(lab["schedule"]["id"])

    assert record["status"] == runs.PARTIAL and runs.exit_code(record) == 2
    assert (record["customer"], record["profile"], record["trigger"]) == ("ABC", "P1", "scheduler")
    assert record["devices_ok"] == ["Core1"] and list(record["devices_failed"]) == ["Agg1"]
    assert record["problems"] >= 1 and len(record["reports"]) == 1 and record["report_errors"] == {}
    assert pm.get_active_project() == active_before == lab["ids"]["P2"]        # 화면의 활성 프로파일은 그대로
    run = next(p for p in log_storage.list_run_dirs("ABC", "P1") if p["run_id"] == record["run_id"])
    raw_names = sorted(os.listdir(run["original"]))
    assert any(name.endswith("_raw_Core1.txt") for name in raw_names), raw_names   # 세션 점검과 같은 파일명
    assert os.listdir(run["problem"])
    metadata = json.loads(Path(run["run_dir"], "metadata.json").read_text(encoding="utf-8"))
    assert metadata["execution_mode"] == "scheduled"                         # Run 이력에서 무인 점검으로 보인다
    assert Path(log_storage.get_profile_dir("ABC", "P1"), "reports", record["reports"][0]).exists()
    # 보고서는 이번 회차 로그로만 — Agg1 은 지난 회차 로그가 있어도 '접속 불가'다.
    assert exported[0]["log_dirs"] == [run["original"]]
    devices = {d["name"]: d for d in builder.build_context("ABC", "P1", project_id=lab["ids"]["P1"],
                                                           log_dirs=[run["original"]])["devices"]}
    assert devices["Agg1"]["unreachable"] is True and devices["Core1"]["unreachable"] is False
    everything = {d["name"]: d for d in builder.build_context("ABC", "P1", project_id=lab["ids"]["P1"])["devices"]}
    assert everything["Agg1"]["unreachable"] is False                         # 회차를 섞으면 지난 로그가 쓰인다
    assert runs.history(lab["ids"]["P1"])[0] == record
    assert [r["id"] for r in runs.unseen_failures()] == [record["id"]]
    assert not is_locked(profile_lock_path(runs.profile_dir_of(lab["ids"]["P1"])))


def test_a_profile_already_being_inspected_is_skipped(lab):
    lock = FileLock(profile_lock_path(runs.profile_dir_of(lab["ids"]["P1"])))
    assert lock.acquire()
    try:
        record = runs.run_scheduled_inspection(lab["schedule"]["id"])
    finally:
        lock.release()
    assert record["status"] == runs.SKIPPED and runs.exit_code(record) == 3
    assert FakeConnection.hosts == [] and runs.history()[0]["status"] == runs.SKIPPED


def test_opening_the_app_does_not_abort_a_run_another_process_is_making(lab):
    from engine import log_storage
    from engine.run_manager import RunManager, run_manager
    running = log_storage.generate_new_run_dir("ABC", "P1", device_count=1)
    run_manager.start_run(running["run_handle"])
    lock = FileLock(profile_lock_path(runs.profile_dir_of(lab["ids"]["P1"])))
    assert lock.acquire()
    try:
        assert RunManager().recover_all_incomplete_runs() == []               # 다른 프로세스가 돌리는 중
    finally:
        lock.release()
    assert [r["run_id"] for r in RunManager().recover_all_incomplete_runs()] == [running["run_id"]]


@pytest.mark.parametrize("prepare, status, message", [
    (lambda lab: store.upsert(lab["ids"]["P1"], {**WEEKLY, "enabled": False}), runs.SKIPPED, "꺼져"),
    (lambda lab: store.remove(lab["ids"]["P1"]), runs.FAILED, "일정이 없습니다"),
    (lambda lab: shutil.rmtree(Path(AppPaths.user_data_root(), "labs", lab["ids"]["P1"])), runs.FAILED,
     "프로파일이 없습니다"),
])
def test_runs_that_cannot_proceed_say_why(lab, prepare, status, message):
    prepare(lab)
    record = runs.run_scheduled_inspection(lab["schedule"]["id"])
    assert record["status"] == status and message in record["message"] and FakeConnection.hosts == []


def test_archiving_a_profile_removes_its_schedule_and_task(lab, monkeypatch):
    from engine import profile_archive
    removed = []
    monkeypatch.setattr(task_scheduler, "unregister", lambda schedule_id: (removed.append(schedule_id), {"ok": True})[1])
    profile_archive.archive_profile(lab["ids"]["P1"], "ABC", "P1")
    assert removed == [lab["schedule"]["id"]] and store.for_project(lab["ids"]["P1"]) is None


def test_a_profile_being_inspected_cannot_be_archived(lab):
    from engine import profile_archive
    lock = FileLock(profile_lock_path(runs.profile_dir_of(lab["ids"]["P1"])))
    assert lock.acquire()
    try:
        with pytest.raises(ValueError, match="진행 중"):
            profile_archive.archive_profile(lab["ids"]["P1"], "ABC", "P1")
    finally:
        lock.release()
    assert Path(AppPaths.user_data_root(), "labs", lab["ids"]["P1"]).is_dir()
    assert store.for_project(lab["ids"]["P1"]) is not None


def test_pending_startup_work_is_left_to_the_app(lab, monkeypatch):
    from engine import migration_manager
    monkeypatch.setattr(migration_manager, "pending_work", lambda: "설정·데이터 업그레이드")
    record = runs.run_scheduled_inspection(lab["schedule"]["id"])
    assert record["status"] == runs.FAILED and "앱을 한 번 열어" in record["message"]


def test_a_manual_run_ignores_the_switch_but_a_failure_is_still_recorded(lab, monkeypatch):
    store.upsert(lab["ids"]["P1"], {**WEEKLY, "enabled": False})
    monkeypatch.setattr(collector, "pre_flight_check", lambda *a, **k: (False, "응답 없음"))
    (Path(AppPaths.user_data_root()) / "connection.yaml").write_text(
        yaml.safe_dump({"network": {"pre_flight_check": True}, "ssh": {"retry_count": 1}}), encoding="utf-8")
    record = runs.run_scheduled_inspection(lab["schedule"]["id"], trigger="manual")
    assert record["status"] == runs.FAILED and "사전점검" in record["message"] and runs.exit_code(record) == 1


# --------------------------------------------------------------------------- 4. 화면 API

@pytest.fixture
def schedule_api(lab, monkeypatch):
    from api.schedule_api import ScheduleApiMixin
    registered = {}
    monkeypatch.setattr(task_scheduler, "available", lambda: True)
    monkeypatch.setattr(task_scheduler, "register",
                        lambda schedule, description, now=None: registered.update({schedule["id"]: description})
                        or {"ok": True})
    monkeypatch.setattr(task_scheduler, "unregister", lambda schedule_id: (registered.pop(schedule_id, None), {"ok": True})[1])
    monkeypatch.setattr(task_scheduler, "is_registered", lambda schedule_id: schedule_id in registered)

    class Api(ScheduleApiMixin):
        def _project(self):
            return lab["ids"]["P1"]

    api = Api()
    api.registered = registered
    return api


def test_saving_registers_and_switching_off_unregisters(schedule_api, lab):
    view = schedule_api.save_inspection_schedule({**WEEKLY, "time": "04:15"})
    schedule_id = lab["schedule"]["id"]
    assert view["ok"] and view["registered"] is True and view["schedule"]["time"] == "04:15"
    assert "ABC / P1" in schedule_api.registered[schedule_id] and view["next_run"].endswith("04:15")
    view = schedule_api.save_inspection_schedule({**WEEKLY, "enabled": False})
    assert view["registered"] is False and schedule_id not in schedule_api.registered
    assert "요일" in schedule_api.save_inspection_schedule({**WEEKLY, "weekdays": []})["error"]


def test_a_failed_registration_still_saves_and_warns(schedule_api, monkeypatch):
    monkeypatch.setattr(task_scheduler, "register", lambda *a, **k: {"ok": False, "error": "작업 스케줄러 등록 실패: 거부"})
    view = schedule_api.save_inspection_schedule(WEEKLY)
    assert view["ok"] and view["warning"].endswith("거부") and view["registered"] is False and view["exists"]


def test_deleting_keeps_the_schedule_when_the_task_cannot_be_removed(schedule_api, lab, monkeypatch):
    monkeypatch.setattr(task_scheduler, "unregister", lambda schedule_id: {"ok": False, "error": "거부됨"})
    assert schedule_api.delete_inspection_schedule() == {"error": "거부됨"}
    assert store.for_project(lab["ids"]["P1"]) is not None                     # 주인 없는 작업이 남지 않게
    monkeypatch.setattr(task_scheduler, "unregister", lambda schedule_id: {"ok": True})
    assert schedule_api.delete_inspection_schedule()["exists"] is False


def test_run_now_reports_through_the_job_and_the_failure_badge(schedule_api):
    assert schedule_api.run_inspection_schedule_now() == {"ok": True}
    deadline = time.time() + 30
    while schedule_api.get_inspection_schedule()["running"]:
        assert time.time() < deadline, "실행이 끝나지 않았다"
        time.sleep(0.05)
    view = schedule_api.get_inspection_schedule()
    assert view["job"]["status"] == "done" and view["history"][0]["trigger"] == "manual"
    alerts = schedule_api.get_schedule_alerts()
    assert alerts["count"] == 1 and alerts["items"][0]["status"] == runs.PARTIAL
    assert schedule_api.acknowledge_schedule_alerts() == {"ok": True}
    assert schedule_api.get_schedule_alerts() == {"count": 0, "items": []}


# --------------------------------------------------------------------------- 5. 진입점·화면

def test_the_exe_entry_runs_headless_against_the_given_data_root(tmp_path):
    """`main.py --scheduled <id> --data-root <폴더>` 는 창 없이 한 번 돌고 종료 코드로 끝난다 — 없는 일정이면 1."""
    data_root = tmp_path / "AutoCheck"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    done = subprocess.run([sys.executable, str(ROOT / "main.py"), "--scheduled", "missing01",
                           "--data-root", str(data_root)], cwd=str(ROOT), env=env, capture_output=True, timeout=120)
    assert done.returncode == 1, done.stderr.decode("utf-8", "replace")
    records = [json.loads(line) for line in (data_root / "scheduled_runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["schedule_id"], r["status"]) for r in records] == [("missing01", runs.FAILED)]


JS_DIR = ROOT / "web_ui" / "js"
NASTY = "<img src=x onerror=\"alert('x')\">"


def _declaration(name, start):
    lines = (JS_DIR / name).read_text(encoding="utf-8").splitlines()
    begin = next(i for i, line in enumerate(lines) if line.startswith(start))
    if lines[begin].rstrip().endswith(";"):
        return lines[begin]
    end = next(i for i in range(begin, len(lines)) if lines[i].rstrip() in ("}", "};"))
    return "\n".join(lines[begin:end + 1])


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 동작 검증을 건너뜁니다")
def test_the_schedule_card_escapes_everything_it_shows():
    info = {"exists": True, "registered": True, "scheduler_available": True, "running": False, "next_run": NASTY,
            "description": NASTY, "schedule": {**store.normalize(WEEKLY)},
            "formats": [{"id": "excel", "label": "Excel"}],
            "history": [{"id": "r1", "status": "partial", "trigger": "scheduler", "started_at": "2026-10-28T02:00:00",
                         "message": NASTY, "devices_failed": {NASTY: NASTY}, "reports": [NASTY]}]}
    alerts = {"count": 1, "items": [{"customer": NASTY, "profile": "P1", "status": "failed", "message": NASTY,
                                     "finished_at": "2026-10-28T02:05:00"}]}
    script = "\n".join([
        _declaration("core.js", "const _ESC_MAP"), _declaration("core.js", "function esc("),
        *[_declaration("workspace-schedule.js", start) for start in (
            "const WS_SCHEDULE_STATUS", "const WS_WEEKDAYS", "const WS_FREQUENCIES",
            "function wsScheduleStatusLine(", "function wsScheduleHtml(")],
        f"console.log(JSON.stringify(wsScheduleHtml({json.dumps(info)}, {json.dumps(alerts)})));",
    ])
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        path = handle.name
    try:
        done = subprocess.run(["node", path], capture_output=True, timeout=30)
    finally:
        os.unlink(path)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    html = json.loads(done.stdout.decode("utf-8"))
    assert "<img" not in html and html.count("&lt;img") == 8
    assert "일부 실패" in html and "확인하지 않은 무인 정기점검 실패 1건" in html and 'data-sch-weekday="3" checked' in html


def test_a_scheduled_run_sends_cisco_devices_their_own_commands(lab, monkeypatch):
    """무인 점검도 세션 점검처럼 장비마다 그 벤더의 명령을 보낸다(IMPROVEMENT_PLAN 4-5)."""
    seen = {}
    real_collect_all = collector.collect_all

    def spy(*args, **kwargs):
        seen.update(kwargs.get("commands_by_platform") or {})
        return real_collect_all(*args, **kwargs)

    monkeypatch.setattr(collector, "collect_all", spy)
    runs.run_scheduled_inspection(lab["schedule"]["id"])
    assert "show etherchannel summary" in seen["cisco_ios"] and "show vpc" in seen["cisco_nxos"]
    assert "show port-channel dense" not in seen["cisco_ios"]
