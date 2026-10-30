"""접속한 장비의 호스트명을 장비 이름에 바로 적용 — 장비 목록의 연결 확인, 터미널 접속, 세션 점검.

예전엔 (1) EOS 의 'Hostname: 201.4_7050SX3-48YC8' 을 첫 점에서 잘라 여러 장비가 '201' 이 되어 이름이 겹쳤고,
(2) 터미널로 접속해도 '이름 동기화' 버튼을 눌러야만 바뀌었고, (3) 바꾼 뒤 옛 이름이 사라져 옛 이름으로 저장된 로그가
이 장비로 묶이지 않았다. 실장비 없이 세션 대역으로 본다.
"""
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from api import terminal_inspection_api as inspection
from api import terminal_session_api as sessions
from api.job_runner import JobRunner
from core.device_names import DeviceNameIndex
from engine import device_inventory as di
from engine.device_probe import extract_hostname


def _inventory(*names):
    return {"defaults": {}, "devices": [di.normalize_device({"name": n}) for n in names]}


@pytest.mark.parametrize("output, expected", [
    ("Hostname: 201.4_7050SX3-48YC8\nFQDN:     201.4_7050SX3-48YC8", "201.4_7050SX3-48YC8"),
    ("hostname Spine1.201.31", "Spine1.201.31"),
    ("Hostname: core1\nFQDN:     core1.lab.local", "core1"),
    ("201.30\n", "201.30"),
])
def test_dotted_hostnames_are_kept_whole(output, expected):
    assert extract_hostname(output) == expected


def test_rename_keeps_the_old_name_as_an_alias():
    inv = _inventory("Core-A", "Agg1")
    assert di.rename_device(inv, "Core-A", "201.4_7050SX3-48YC8") is None
    device = inv["devices"][0]
    assert device["name"] == "201.4_7050SX3-48YC8" and device["aliases"] == ["Core-A"]
    # 옛 이름으로 저장된 로그도 이 장비로 묶인다
    assert DeviceNameIndex(inv["devices"]).resolve("core-a") == "201.4_7050SX3-48YC8"


def test_placeholder_names_are_not_kept_as_aliases():
    inv = _inventory("AUTO-30")
    assert di.rename_device(inv, "AUTO-30", "201.30") is None
    assert inv["devices"][0]["aliases"] == []
    assert di.is_placeholder_name("AUTO-30_") and di.is_placeholder_name("auto-fe80::1")
    assert not di.is_placeholder_name("AUTO-SW 1") and not di.is_placeholder_name("Core-AUTO-1")


def test_a_name_another_device_uses_in_other_case_is_refused():
    """장비명이 로그 파일명이 된다 — Windows 는 대소문자를 가리지 않아 두 장비의 로그가 한 파일이 된다."""
    inv = _inventory("AUTO-30", "SPINE1")
    problem = di.rename_device(inv, "AUTO-30", "spine1")
    assert problem and "spine1" in problem
    assert [d["name"] for d in inv["devices"]] == ["AUTO-30", "SPINE1"]


def test_renaming_to_its_own_alias_drops_that_alias():
    inv = {"defaults": {}, "devices": [di.normalize_device({"name": "Core-A", "aliases": ["core1", "old-core"]})]}
    assert di.rename_device(inv, "Core-A", "CORE1") is None
    assert inv["devices"][0]["aliases"] == ["old-core", "Core-A"]


@pytest.mark.parametrize("bad", ["", "a b", "<img>", "x" * 64, "코어1", "-lead"])
def test_names_that_cannot_be_hostnames_are_refused(bad):
    inv = _inventory("AUTO-1")
    assert di.rename_device(inv, "AUTO-1", bad)
    assert inv["devices"][0]["name"] == "AUTO-1"


def test_missing_device_is_reported():
    assert "없습니다" in di.rename_device(_inventory("A"), "B", "core1")


def test_a_second_session_of_the_same_device_finds_it_already_renamed():
    inv = _inventory("201.30")
    assert di.rename_device(inv, "AUTO-30", "201.30") is None
    assert [d["name"] for d in inv["devices"]] == ["201.30"]


# ---------- 터미널 세션 ----------

class _SessionApi(sessions.TerminalSessionApiMixin):
    """장비 목록 대신 — 이름 변경 요청을 묶음째 기록한다."""

    def __init__(self):
        self.calls = []
        self.refuse = None

    @property
    def renames(self):
        return [pair for call in self.calls for pair in call]

    def _rename_devices(self, renames):
        self.calls.append(list(renames))
        return [self.refuse] * len(renames)

    def poll(self, *session_ids):
        return self.get_terminal_output_multi(list(session_ids))


@pytest.fixture
def add_session(monkeypatch):
    def add(sid, device, tail, quiet_for=1.0):
        sess = {"device": device, "buffer": [], "inspect_buffer": [], "read_lock": threading.Lock(),
                "connected": True, "error": None, "accumulated_tail": tail, "last_hostname": None,
                "last_data_at": time.time() - quiet_for, "name_checked": False, "platform": "arista_eos"}
        monkeypatch.setitem(sessions._sessions, sid, sess)
        return sess
    return add


@pytest.mark.parametrize("tail, hostname", [
    ("Last login: Fri Oct 30 10:16:38 2026\r\n201.4_7050SX3-48YC8#", "201.4_7050SX3-48YC8"),
    ("\r\nSpine1.201.31(s1)#", "Spine1.201.31"),          # 모듈형 장비 — 슈퍼바이저 슬롯 표시
    ("\r\nR1>", "R1"),
])
def test_session_is_renamed_once_the_device_waits_at_its_prompt(add_session, tail, hostname):
    api = _SessionApi()
    sess = add_session("s1", "AUTO-30", tail)
    out = api.poll("s1")["s1"]
    assert out["name_sync"] == {"from": "AUTO-30", "to": hostname}
    assert sess["device"] == hostname and api.renames == [("AUTO-30", hostname)]
    assert "name_sync" not in api.poll("s1")["s1"]      # 화면에는 한 번만 알린다
    assert api.renames == [("AUTO-30", hostname)]                  # 세션마다 한 번만 본다


def test_nothing_is_decided_while_output_is_still_arriving(add_session):
    api = _SessionApi()
    sess = add_session("s1", "AUTO-30", "\r\nAuthorized_users_only>", quiet_for=0.1)
    assert "name_sync" not in api.poll("s1")["s1"]
    assert api.renames == [] and not sess["name_checked"]


def test_output_that_does_not_end_at_a_prompt_is_not_a_hostname(add_session):
    api = _SessionApi()
    sess = add_session("s1", "AUTO-30", "\r\n*** authorized use only ***\r\n", quiet_for=5)
    assert "name_sync" not in api.poll("s1")["s1"]
    assert api.renames == [] and not sess["name_checked"]


def test_same_name_is_left_alone(add_session):
    api = _SessionApi()
    sess = add_session("s1", "core1", "\r\ncore1#")
    assert "name_sync" not in api.poll("s1")["s1"]
    assert api.renames == [] and sess["name_checked"]


def test_refused_rename_is_reported_once_and_the_session_keeps_its_name(add_session):
    api = _SessionApi()
    api.refuse = "이미 'core1' 이름을 쓰는 장비가 있습니다"
    sess = add_session("s1", "AUTO-30", "\r\ncore1#")
    note = api.poll("s1")["s1"]["name_sync"]["note"]
    assert "AUTO-30" in note and "core1" in note and api.refuse in note
    assert sess["device"] == "AUTO-30"
    assert "name_sync" not in api.poll("s1")["s1"]


def test_sessions_connected_together_are_renamed_with_one_inventory_write(add_session):
    """51대를 한꺼번에 접속하면 이름 변경이 한 폴링에 몰린다 — 한 대씩 읽고 쓰면 7초 동안 터미널 출력이 멈췄다."""
    api = _SessionApi()
    for i in range(1, 4):
        add_session(f"s{i}", f"AUTO-{i}", f"\r\n201.{i}#")
    out = api.poll("s1", "s2", "s3")
    assert api.calls == [[("AUTO-1", "201.1"), ("AUTO-2", "201.2"), ("AUTO-3", "201.3")]]
    assert [out[f"s{i}"]["name_sync"]["to"] for i in range(1, 4)] == ["201.1", "201.2", "201.3"]


def test_rename_button_retries_after_an_automatic_refusal(add_session):
    api = _SessionApi()
    api.refuse = "이미 'core1' 이름을 쓰는 장비가 있습니다"
    sess = add_session("s1", "AUTO-30", "\r\ncore1#")
    api.poll("s1")["s1"]
    sess["last_hostname"] = "core1"
    api.refuse = None                                              # 겹치던 장비를 고쳤다
    assert api.auto_rename_device_from_session("s1") == {"success": True, "old_name": "AUTO-30", "new_name": "core1"}
    assert api.auto_rename_device_from_session("s1")["unchanged"] is True


def test_renames_are_saved_with_aliases_and_secrets_kept(add_session, tmp_path):
    """장비 목록 파일까지 — 이름·별칭이 저장되고, 봉인된 비밀번호는 그대로 이어진다."""
    from api.inventory_api import InventoryApiMixin
    paths = {"device_inventory": str(tmp_path / "device_inventory.yaml"),
             "lab_meta": str(tmp_path / "lab_meta.yaml"), "ip_allocation": str(tmp_path / "ip_allocation.yaml")}

    class Api(InventoryApiMixin, sessions.TerminalSessionApiMixin):
        def _paths(self):
            return paths

        def _load_inventory(self, p):
            return di.load_inventory(p["device_inventory"], p["lab_meta"], p["ip_allocation"])

    di.save_inventory({"defaults": {}, "devices": [
        {"name": "AUTO-30", "management_ip": "10.0.0.30", "password": "pw30"},
        {"name": "Core-A", "management_ip": "10.0.0.31", "password": "pw31"},
        {"name": "201.32", "management_ip": "10.0.0.32", "password": "pw32"}]}, paths["device_inventory"])
    add_session("s1", "AUTO-30", "\r\n201.30#")
    add_session("s2", "Core-A", "\r\nSpine1.201.31(s1)#")
    add_session("s3", "201.32", "\r\n201.32#")
    out = Api().get_terminal_output_multi(["s1", "s2", "s3"])
    assert [out[s].get("name_sync") for s in ("s1", "s2", "s3")] == [
        {"from": "AUTO-30", "to": "201.30"}, {"from": "Core-A", "to": "Spine1.201.31"}, None]
    stored = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])["devices"]
    assert [(d["name"], d["aliases"], d["password"]) for d in stored] == [
        ("201.30", [], "pw30"), ("Spine1.201.31", ["Core-A"], "pw31"), ("201.32", [], "pw32")]


# ---------- 세션 점검 ----------

class _EchoChannel:
    """보낸 줄마다 '명령 출력 + 프롬프트' 를 세션 버퍼에 넣는 채널 대역(읽기 스레드 대신)."""

    def __init__(self, sess, hostname):
        self.sess, self.hostname = sess, hostname

    def send(self, text):
        for line in text.split("\n")[:-1]:
            out = f"{line}\r\noutput of {line}\r\n{self.hostname}#"
            with self.sess["read_lock"]:
                self.sess["inspect_buffer"].append(out)
                self.sess["accumulated_tail"] = (self.sess["accumulated_tail"] + out)[-400:]


def test_inspection_log_is_named_after_the_hostname(add_session, monkeypatch, tmp_path):
    """접속하자마자 점검을 시작해(자동 접속) 아직 이름을 못 맞춘 세션도 로그 파일은 호스트명으로 짓는다."""
    monkeypatch.setattr(inspection, "_inspection_jobs", JobRunner(("inspection",)))
    monkeypatch.setattr(inspection, "_drain_before_send", lambda sess: False)
    api = _SessionApi()
    sess = add_session("s1", "AUTO-30", "", quiet_for=0)
    sess["channel"] = _EchoChannel(sess, "201.30")
    inspection._run_one_session_inspection("s1", ["show version"], [str(tmp_path)], api._sync_session_name)
    (saved,) = tmp_path.glob("*.txt")
    assert saved.name.endswith("_201.30.txt")
    assert sess["device"] == "201.30" and sess["name_event"] == {"from": "AUTO-30", "to": "201.30"}


def test_inspection_uses_the_name_the_screen_already_synced(add_session, monkeypatch, tmp_path):
    monkeypatch.setattr(inspection, "_inspection_jobs", JobRunner(("inspection",)))
    monkeypatch.setattr(inspection, "_drain_before_send", lambda sess: False)
    sess = add_session("s1", "AUTO-30", "", quiet_for=0)
    sess["channel"] = _EchoChannel(sess, "201.30")

    def screen_poll_renamed_meanwhile(session_id, settled=False):
        sess["device"] = "201.30"

    inspection._run_one_session_inspection("s1", ["show version"], [str(tmp_path)], screen_poll_renamed_meanwhile)
    (saved,) = tmp_path.glob("*.txt")
    assert saved.name.endswith("_201.30.txt")


# ---------- 장비 목록 화면(연결 확인) — 서버와 같은 규칙 ----------

JS_DIR = Path(__file__).resolve().parent.parent / "web_ui" / "js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 동작 검증을 건너뜁니다")


def _block(name, start):
    lines = (JS_DIR / name).read_text(encoding="utf-8").splitlines()
    begin = next(i for i, line in enumerate(lines) if line.startswith(start))
    if lines[begin].rstrip().endswith((";", "}")):
        return lines[begin]
    end = next(i for i in range(begin, len(lines)) if lines[i] in ("}", "};", "];"))
    return "\n".join(lines[begin:end + 1])


@needs_node
def test_inventory_probe_applies_the_hostname_with_the_server_rules(tmp_path):
    script = "\n".join([
        _block("inventory-probe.js", "const PLACEHOLDER_NAME_RE"), _block("inventory-probe.js", "function applyHostname("),
        "const inventoryDevices = [",
        "  {name: 'Core-A', aliases: ['old-core']}, {name: 'AUTO-31', aliases: []},",
        "  {name: 'AUTO-32', aliases: 'x1, x2'}, {name: 'SPINE1', aliases: []}, {name: 'AUTO-33', aliases: []}];",
        "const results = [",
        "  applyHostname(inventoryDevices[0], '201.4_7050SX3-48YC8'),",
        "  applyHostname(inventoryDevices[1], '201.31'),",
        "  applyHostname(inventoryDevices[2], 'x2'),",
        "  applyHostname(inventoryDevices[4], 'spine1'),",
        "  applyHostname(inventoryDevices[3], 'SPINE1')];",
        "console.log(JSON.stringify({results, devices: inventoryDevices}));",
    ])
    path = tmp_path / "check.js"
    path.write_bytes(script.encode("utf-8"))
    completed = subprocess.run(["node", str(path)], capture_output=True, encoding="utf-8")
    assert completed.returncode == 0, completed.stderr
    out = json.loads(completed.stdout)
    renamed, auto, to_alias, clash, same = out["results"]
    devices = out["devices"]
    assert renamed == {"renamed": True, "before": "Core-A", "after": "201.4_7050SX3-48YC8"}
    assert devices[0] == {"name": "201.4_7050SX3-48YC8", "aliases": ["old-core", "Core-A"]}
    assert auto["renamed"] and devices[1]["aliases"] == []                       # 자리표시 이름은 남기지 않는다
    assert to_alias["renamed"] and devices[2] == {"name": "x2", "aliases": ["x1"]}
    assert clash["renamed"] is False and devices[4]["name"] == "AUTO-33"        # 대소문자만 다른 이름도 중복
    assert same is None
