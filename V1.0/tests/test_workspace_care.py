"""작업공간 점검·자동 백업 — IMPROVEMENT_PLAN 4-10.

  1. 무결성 점검(engine/workspace_check): 손으로 바꾼 폴더 이름, 읽을 수 없는 설정·상태 파일, 깨진 Run, 이 PC 에서 풀 수
     없는 봉인 비밀번호, 남은 임시 파일을 할 일과 함께 알린다. 고치지 않는다(읽기만).
  2. 자동 백업(engine/workspace_backup): 설정과 프로파일 정의를 zip 하나로 — 점검 로그·보고서 파일·앱 로그는 담지 않고,
     봉인된 비밀번호는 봉인된 채로(그대로 복사). 최근 10개만 남기고, 앱을 켤 때 하루 한 번 만든다.
"""
import datetime
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import pytest
import yaml

from core import secret_vault as vault
from engine import workspace_backup, workspace_check
from test_round_compare import root, write_log  # noqa: F401 — 픽스처(임시 데이터 폴더)


@pytest.fixture
def profiles(root):
    """고객사 ABC 의 정기점검 프로파일 2026-10·Lab — {프로파일 이름: project id}."""
    from api.customer_profile_api import CustomerProfileApiMixin
    from api.project_core_api import ProjectCoreApiMixin

    class Api(CustomerProfileApiMixin, ProjectCoreApiMixin):
        pass

    api = Api()
    api.create_customer("ABC")
    cid = next(c["id"] for c in api.get_customer_profiles())
    api.create_inspection_profile(cid, "2026-10", inspection_date="2026-10-15")
    api.create_inspection_profile(cid, "Lab")
    return {p["profile_name"]: p["id"] for c in api.get_customer_profiles() for p in c["profiles"]}


def _findings(level):
    """{(영역, 데이터 폴더 기준 경로): (메시지, 힌트)}"""
    result = workspace_check.check_workspace()
    return {(f["area"], f["path"].replace("\\", "/")): (f["message"], f["hint"])
            for f in result["findings"] if f["level"] == level}


def _tree(root):
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in Path(root).rglob("*") if p.is_file()}


# --------------------------------------------------------------------------- 1. 무결성 점검

def test_a_fresh_workspace_is_clean(profiles):
    result = workspace_check.check_workspace()
    assert result["findings"] == [] and result["counts"] == {"error": 0, "warn": 0, "info": 0}


def test_folders_renamed_by_hand_are_reported_from_both_sides(root, profiles):
    data = root / "data"
    (data / "ABC" / "2026-10").rename(data / "ABC" / "2026-10 old")   # 탐색기에서 이름을 바꿨다
    (data / "ABC" / "Lab").rename(data / "ABC" / "LAB")               # 대소문자만 — Windows 에서는 같은 폴더다
    (data / "Ghost" / "P1").mkdir(parents=True)                        # 고객사 목록에 없는 고객사
    warns = _findings("warn")
    assert set(warns) == {("프로파일", "data/ABC/2026-10 old"), ("프로파일", "data/ABC/2026-10"),
                          ("고객사", "data/Ghost"), ("프로파일", "data/Ghost/P1")}
    assert "연결되지 않은" in warns[("프로파일", "data/ABC/2026-10 old")][0]
    assert profiles["2026-10"] in warns[("프로파일", "data/ABC/2026-10")][0]     # 어느 프로파일인지
    assert _findings("error") == {} and _findings("info") == {}


def test_unreadable_files_are_errors_that_point_at_a_way_back(root, profiles):
    profile_dir = root / "data" / "ABC" / "Lab"
    (profile_dir / "realtime_state.json").write_text("{broken", encoding="utf-8")
    (profile_dir / "realtime_state.json.bak").write_text("{}", encoding="utf-8")
    (root / "schedules.yaml").write_text("schedules: [\n", encoding="utf-8")
    labs = root / "labs"
    (labs / profiles["Lab"] / "device_inventory.yaml").write_text("devices: [\n", encoding="utf-8")
    (labs / profiles["2026-10"] / "project_meta.yaml").write_text("- 읽히지만\n- 모양이 틀렸다\n", encoding="utf-8")
    errors = _findings("error")
    assert set(errors) == {("파일", "data/ABC/Lab/realtime_state.json"), ("파일", "schedules.yaml"),
                           ("장비 목록", f"labs/{profiles['Lab']}/device_inventory.yaml"),
                           ("프로파일", f"labs/{profiles['2026-10']}/project_meta.yaml")}
    assert ".bak" in errors[("파일", "data/ABC/Lab/realtime_state.json")][1]
    assert "backups" in errors[("파일", "schedules.yaml")][1]
    # 메타를 읽지 못한 프로파일은 목록에서 빠진다 — 그 데이터 폴더는 '연결되지 않은 폴더'로 함께 보인다
    assert set(_findings("warn")) == {("프로파일", "data/ABC/2026-10")}


def test_broken_runs_and_leftovers_are_listed_and_nothing_is_touched(root, profiles):
    run = root / "data" / "ABC" / "2026-10" / "runs" / "2026-10-15_090000"
    run.mkdir(parents=True)
    (run / "session.json").write_text("{", encoding="utf-8")
    an_hour_ago = time.time() - 3600
    stale = root / "config" / ".customers.yaml.ab12.tmp"                # 쓰다가 앱이 꺼졌다
    stale.write_text("x", encoding="utf-8")
    os.utime(stale, (an_hour_ago, an_hour_ago))
    (root / "config" / ".connection.yaml.cd34.tmp").write_text("x", encoding="utf-8")   # 지금 쓰는 중일 수 있다
    moved = root / "data" / "ABC" / "2026-10" / "reports" / "_support_history.json.corrupt-20261001_000000"
    moved.parent.mkdir(parents=True, exist_ok=True)
    moved.write_text("{", encoding="utf-8")
    before = _tree(root)

    warns, infos = _findings("warn"), _findings("info")
    assert set(warns) == {("Run", "data/ABC/2026-10/runs/2026-10-15_090000")}
    assert "session.json" in warns[("Run", "data/ABC/2026-10/runs/2026-10-15_090000")][0]
    assert set(infos) == {("찌꺼기", "config/.customers.yaml.ab12.tmp"),
                          ("찌꺼기", "data/ABC/2026-10/reports/_support_history.json.corrupt-20261001_000000")}
    assert _tree(root) == before                                         # 읽기만 한다


@pytest.mark.skipif(sys.platform != "win32" or not vault.available(), reason="DPAPI 봉인은 Windows 에서만")
def test_passwords_sealed_on_another_pc_are_reported(root, profiles):
    foreign = vault.PREFIX + "AAAA"          # 형식은 맞지만 이 PC 에서 풀 수 없는 봉인 값
    path = root / "labs" / profiles["Lab"] / "device_inventory.yaml"
    path.write_text(yaml.safe_dump({"defaults": {"default_password": foreign},
                                    "devices": [{"name": "Core1", "password": foreign}]}), encoding="utf-8")
    before = path.read_bytes()
    warns = _findings("warn")
    assert set(warns) == {("장비 목록", f"labs/{profiles['Lab']}/device_inventory.yaml")}
    assert "2개" in warns[("장비 목록", f"labs/{profiles['Lab']}/device_inventory.yaml")][0]
    assert path.read_bytes() == before


# --------------------------------------------------------------------------- 2. 자동 백업

def test_a_backup_holds_settings_and_definitions_but_not_logs_or_reports(root, profiles):
    from engine import device_inventory as di
    from engine import project_manager as pm
    inventory = pm.project_paths(profiles["2026-10"])["device_inventory"]
    di.save_inventory({"defaults": {}, "devices": [{"name": "Core1", "management_ip": "10.0.0.1",
                                                    "password": "pw-1"}]}, inventory)
    profile_dir = root / "data" / "ABC" / "2026-10"
    write_log("ABC", "2026-10", "2026-10-15_090000", "Core1", "Core1#show version\n")
    (profile_dir / "reports" / "_snapshot.json").write_text("{}", encoding="utf-8")
    (profile_dir / "reports" / "ABC_2026-10.xlsx").write_bytes(b"PK")
    (profile_dir / "realtime_state.json").write_text("{}", encoding="utf-8")
    (profile_dir / "profile" / "credential.json").write_text("{}", encoding="utf-8")     # 예전 계정 사본
    (root / "logs").mkdir(exist_ok=True)
    (root / "logs" / "app.log").write_text("log", encoding="utf-8")

    made = workspace_backup.create_backup(reason="manual", now=datetime.datetime(2026, 10, 29, 9, 0, 0))
    assert made["name"] == "workspace_20261029_090000.zip" and made["reason"] == "manual"
    with zipfile.ZipFile(made["path"]) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        stored = archive.read(f"labs/{profiles['2026-10']}/device_inventory.yaml")
    assert {f"labs/{profiles['2026-10']}/device_inventory.yaml", f"labs/{profiles['2026-10']}/project_meta.yaml",
            "config/customers.yaml", "data/ABC/2026-10/reports/_snapshot.json",
            "data/ABC/2026-10/realtime_state.json"} <= names
    assert [n for n in names if "/runs/" in n or n.startswith(("logs/", "backups/"))
            or n.endswith((".xlsx", "credential.json"))] == []
    assert stored == Path(inventory).read_bytes()          # 그대로 복사 — 봉인된 비밀번호는 봉인된 채로
    assert manifest["reason"] == "manual" and manifest["created_at"] == "2026-10-29T09:00:00"
    assert sorted(manifest["files"]) == sorted(names - {"manifest.json"}) and made["files"] == len(names) - 1
    assert list((root / "backups").glob("*.part")) == []


def test_only_the_newest_ten_are_kept_and_the_app_backs_up_once_a_day(root, profiles):
    start = datetime.datetime(2020, 10, 1, 9, 0, 0)      # 지금보다 앞 — 아래 자동 백업이 가장 새것이 된다
    for day in range(12):
        workspace_backup.create_backup(reason="manual", now=start + datetime.timedelta(days=day))
    listed = workspace_backup.list_backups()
    assert len(listed) == workspace_backup.KEEP == 10
    assert listed[0]["name"] == "workspace_20201012_090000.zip" and listed[-1]["name"] == "workspace_20201003_090000.zip"
    assert listed[0]["reason"] == "manual" and listed[0]["files"] > 0

    now = datetime.datetime.now()
    assert workspace_backup.auto_backup_if_due(now=now) is None          # 방금 만들었다
    made = workspace_backup.auto_backup_if_due(now=now + datetime.timedelta(hours=25))
    assert made and made["reason"] == "auto"
    assert workspace_backup.list_backups()[0]["reason"] == "auto" and len(workspace_backup.list_backups()) == 10


def test_two_backups_in_the_same_second_do_not_overwrite(root, profiles):
    now = datetime.datetime(2020, 1, 1, 9, 0, 0)
    first = workspace_backup.create_backup(reason="auto", now=now)
    second = workspace_backup.create_backup(reason="manual", now=now)
    assert (first["name"], second["name"]) == ("workspace_20200101_090000.zip", "workspace_20200101_090000_2.zip")
    assert [b["reason"] for b in workspace_backup.list_backups()] == ["manual", "auto"]      # 새것 먼저


def test_workspace_api(root, profiles, monkeypatch):
    from api import workspace_api
    opened = []
    monkeypatch.setattr(workspace_api, "open_in_file_explorer", opened.append)
    api = workspace_api.WorkspaceApiMixin()
    assert api.list_workspace_backups() == {"ok": True, "backups": [], "keep": 10}
    made = api.create_workspace_backup()
    assert made["ok"] and made["reason"] == "manual" and made["files"] > 0
    assert [b["name"] for b in api.list_workspace_backups()["backups"]] == [made["name"]]
    assert api.check_workspace_integrity()["counts"] == {"error": 0, "warn": 0, "info": 0}
    assert api.open_workspace_backup_folder() == {"ok": True} and opened == [str(root / "backups")]


def test_the_app_backs_up_once_its_window_is_up():
    """main 을 import 하면 패키지 검사·webview import 가 일어나서 소스로 본다(tests/test_api_surface 와 같은 이유)."""
    import ast
    tree = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}

    def calls(name):
        return {ast.unparse(node.func) for node in ast.walk(functions[name]) if isinstance(node, ast.Call)}

    assert "_auto_backup" in calls("_on_window_ready")                   # 창이 뜬 뒤, 별도 스레드에서
    assert "workspace_backup.auto_backup_if_due" in calls("_auto_backup")
    assert "_auto_backup" not in calls("main")                           # 창이 뜨는 것을 늦추지 않는다
