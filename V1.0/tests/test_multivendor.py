"""멀티벤더(Cisco) — IMPROVEMENT_PLAN 4-5.

4-5a 벤더별 접속과 enable 비밀번호:
  1. 인벤토리 vendor 칸 -> 플랫폼(core/vendors.platform_of) -> netmiko device_type. 비어 있으면 예전처럼 Arista,
     모르는 벤더는 접속하지 않는다(예전엔 모든 장비를 arista_eos 로 접속했다).
  2. enable 비밀번호(장비별·기본값)는 다른 비밀값처럼 봉인되고 화면에는 has_* 만 간다. 로그인 비밀번호로 대신하지 않는다.
  3. 수집기는 enable 에 실패하면 재시도하지 않는다. 예전엔 실패를 삼키고 사용자 모드에서 나머지 명령을 보냈다.
  4. 세션 점검은 enable 을 통짜 명령에서 빼서 따로 보낸다 — 장비가 'Password:' 를 물을 때 뒤의 명령 줄이 비밀번호로
     들어가지 않게. Cisco 는 비밀번호가 없으면 enable 을 시도하지 않는다(장비에 인증 실패만 남는다).
실장비·SSH 없이 netmiko 와 세션 채널을 가짜로 바꿔 끼운다.
"""
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml

from api import terminal_inspection_api as inspection
from api.job_runner import JobRunner
from api.terminal_session_api import TerminalSessionApiMixin
from core import secret_vault as vault
from core import vendors
from core.secret_redaction import scrub_secret_fields
from core.storage_service import PathTarget
from engine import collector
from engine import device_inventory as di

needs_dpapi = pytest.mark.skipif(sys.platform != "win32" or not vault.available(),
                                 reason="Windows DPAPI 가 있어야 봉인을 확인할 수 있습니다")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 동작 검증을 건너뜁니다")


# --------------------------------------------------------------------------- 1. 벤더 -> 플랫폼

@pytest.mark.parametrize("vendor, platform", [
    ("", "arista_eos"), (None, "arista_eos"), ("Arista", "arista_eos"), ("arista networks", "arista_eos"),
    ("EOS", "arista_eos"), ("arista_eos", "arista_eos"),
    ("Cisco", "cisco_ios"), ("Cisco IOS", "cisco_ios"), ("Catalyst 3850", "cisco_ios"), ("cisco_ios", "cisco_ios"),
    ("Cisco IOS-XE", "cisco_xe"), ("IOS XE", "cisco_xe"), ("cisco_xe", "cisco_xe"),
    ("Cisco NX-OS", "cisco_nxos"), ("Nexus 9300", "cisco_nxos"), ("NXOS", "cisco_nxos"),
    ("Juniper", None), ("Dell", None), ("Cisco ASA", None), ("Cisco IOS-XR", None), ("HPE Aruba", None),
])
def test_vendor_text_selects_a_platform(vendor, platform):
    assert vendors.platform_of(vendor) == platform


def test_every_platform_has_a_netmiko_driver_and_a_vendor_text_that_maps_back():
    for platform, entry in vendors.PLATFORMS.items():
        assert vendors.netmiko_type(platform) == entry["netmiko"]
        assert vendors.platform_of(entry["vendor"]) == platform       # 화면이 저장하는 글자가 같은 플랫폼으로 읽힌다
    assert vendors.netmiko_type(None) is None
    assert vendors.enable_without_secret("arista_eos") is True
    assert vendors.enable_without_secret("cisco_ios") is False and vendors.enable_without_secret(None) is False


# --------------------------------------------------------------------------- 2. enable 비밀번호

def test_enable_secret_comes_from_the_device_then_the_defaults_never_from_the_login_password():
    defaults = {"default_password": "login", "default_enable_secret": "def-en"}
    assert di.resolve_enable_secret({"enable_secret": "own-en", "password": "pw"}, defaults) == "own-en"
    assert di.resolve_enable_secret({"enable_secret": "", "password": "pw"}, defaults) == "def-en"
    assert di.resolve_enable_secret({"password": "pw"}, {"default_password": "login"}) == ""


def test_the_screen_gets_flags_and_the_platform_but_never_the_enable_secret():
    device = di.normalize_device({"name": "R1", "vendor": "Cisco NX-OS", "enable_secret": "own-en"})
    public = di.public_device(device)
    assert public["has_enable_secret"] is True and "enable_secret" not in public
    assert public["platform"] == "cisco_nxos"
    assert "own-en" not in json.dumps(public)
    public_defaults = di.public_defaults(dict(di.DEFAULT_PROJECT_DEFAULTS, default_enable_secret="def-en"))
    assert public_defaults["has_default_enable_secret"] is True and "default_enable_secret" not in public_defaults


def test_saving_the_screen_keeps_or_clears_the_stored_enable_secret():
    stored = [di.normalize_device({"id": "d1", "name": "R1", "enable_secret": "own-en"})]
    screen = dict(di.public_device(stored[0]))          # 화면은 비밀값 없이 돌려보낸다
    (kept,) = di.merge_screen_devices([screen], stored)
    assert kept["enable_secret"] == "own-en" and "platform" not in kept
    (cleared,) = di.merge_screen_devices([dict(screen, clear_secrets=["enable_secret"])], stored)
    assert cleared["enable_secret"] == ""
    (changed,) = di.merge_screen_devices([dict(screen, enable_secret="new-en")], stored)
    assert changed["enable_secret"] == "new-en"


@needs_dpapi
def test_enable_secrets_are_sealed_on_disk(tmp_path):
    path = tmp_path / "device_inventory.yaml"
    inventory = {"defaults": dict(di.DEFAULT_PROJECT_DEFAULTS, default_enable_secret="def-en-1"),
                 "devices": [di.normalize_device({"name": "R1", "vendor": "Cisco IOS", "enable_secret": "own-en-1"})]}
    di.save_inventory(inventory, str(path))
    text = path.read_text(encoding="utf-8")
    assert "own-en-1" not in text and "def-en-1" not in text
    on_disk = yaml.safe_load(text)
    assert on_disk["devices"][0]["enable_secret"].startswith("vault:dpapi:v1:")
    assert on_disk["defaults"]["default_enable_secret"].startswith("vault:dpapi:v1:")
    loaded = di.load_inventory(str(path))
    assert loaded["devices"][0]["enable_secret"] == "own-en-1"
    assert loaded["defaults"]["default_enable_secret"] == "def-en-1"


def test_project_exports_scrub_enable_secrets():
    scrubbed, count = scrub_secret_fields({"defaults": {"default_enable_secret": "def-en"},
                                           "devices": [{"name": "R1", "enable_secret": "own-en"}]})
    assert count == 2
    assert scrubbed == {"defaults": {"default_enable_secret": ""}, "devices": [{"name": "R1", "enable_secret": ""}]}


def test_excel_export_leaves_the_enable_secret_out_unless_asked(tmp_path):
    devices = [di.normalize_device({"name": "R1", "vendor": "Cisco IOS", "password": "pw", "enable_secret": "own-en"})]
    plain, full = tmp_path / "plain.xlsx", tmp_path / "full.xlsx"
    di.export_to_excel(devices, str(plain))
    di.export_to_excel(devices, str(full), include_credentials=True)
    (without,) = di.import_excel(str(plain))
    (with_secret,) = di.import_excel(str(full))
    assert not without["enable_secret"] and without["vendor"] == "Cisco IOS"
    assert with_secret["enable_secret"] == "own-en"


# --------------------------------------------------------------------------- 3. 수집기(netmiko)

class FakeConnection:
    instances = []

    def __init__(self, enabled=False, enable_error=None, **kwargs):
        self.kwargs = kwargs
        self.enabled = enabled
        self.enable_error = enable_error
        self.enable_calls = 0
        self.sent = []
        self.disconnected = False
        FakeConnection.instances.append(self)

    def check_enable_mode(self):
        return self.enabled

    def enable(self):
        self.enable_calls += 1
        if isinstance(self.enable_error, BaseException):
            raise self.enable_error
        if self.enable_error:
            raise ValueError(self.enable_error)                             # netmiko 가 enable 실패를 알리는 방식
        self.enabled = True

    def send_command_timing(self, text, **kwargs):
        self.sent.append(text)
        return "R1#show version\nCisco IOS Software\nR1#"

    def send_command(self, *args, **kwargs):
        return "hostname R1"

    def disconnect(self):
        self.disconnected = True


@pytest.fixture
def fake_netmiko(monkeypatch):
    FakeConnection.instances = []
    behaviour = {}
    monkeypatch.setattr(collector, "ConnectHandler", lambda **kw: FakeConnection(**behaviour, **kw))
    return behaviour


def _collect(tmp_path, **overrides):
    kwargs = dict(name="R1", ip="10.0.0.1", port=22, username="ops", password="pw",
                  commands=["enable", "terminal length 0", "show version"],
                  log_target=PathTarget(path=tmp_path), log_rel_path="R1.txt", on_update=lambda *a: None,
                  retry_count=3, retry_delay_sec=0)
    kwargs.update(overrides)
    return collector.collect_device(**kwargs)


def test_collector_connects_with_the_platform_driver_and_the_enable_secret(tmp_path, fake_netmiko):
    name, output, error = _collect(tmp_path, platform="cisco_nxos", secret="en-1")
    assert error is None
    (conn,) = FakeConnection.instances
    assert conn.kwargs["device_type"] == "cisco_nxos" and conn.kwargs["secret"] == "en-1"
    assert conn.enable_calls == 1
    assert conn.sent == ["terminal length 0\n!\nshow version\n!"]           # enable 은 명령으로 보내지 않는다


def test_collector_defaults_to_arista_as_before(tmp_path, fake_netmiko):
    _collect(tmp_path)
    (conn,) = FakeConnection.instances
    assert conn.kwargs["device_type"] == "arista_eos" and conn.kwargs["secret"] == ""
    assert conn.enable_calls == 1                                           # EOS 는 비밀번호 없이 enable 을 시도한다


def test_collector_does_not_log_in_to_an_unknown_vendor(tmp_path, fake_netmiko):
    name, output, error = _collect(tmp_path, platform=None)
    assert output is None and error == vendors.UNSUPPORTED_TEXT
    assert FakeConnection.instances == []


def test_failed_enable_is_not_retried_and_closes_the_session(tmp_path, fake_netmiko):
    fake_netmiko["enable_error"] = "Failed to enter enable mode."
    name, output, error = _collect(tmp_path, platform="cisco_ios", secret="wrong")
    assert output is None and "enable" in error and "맞는지 확인" in error
    (conn,) = FakeConnection.instances                                      # 재시도 3회가 아니라 한 번
    assert conn.disconnected is True and conn.sent == []
    assert not (tmp_path / "R1.txt").exists()


def test_a_session_dropped_during_enable_is_retried(tmp_path, fake_netmiko):
    fake_netmiko["enable_error"] = OSError("세션이 끊어졌습니다")
    name, output, error = _collect(tmp_path, platform="cisco_ios", secret="en-1")
    assert output is None and "세션이 끊어졌습니다" in error
    assert len(FakeConnection.instances) == 3 and all(conn.disconnected for conn in FakeConnection.instances)


def test_cisco_without_an_enable_secret_does_not_try_enable(tmp_path, fake_netmiko):
    name, output, error = _collect(tmp_path, platform="cisco_ios", secret="")
    assert output is None and "enable 비밀번호가 없어" in error
    (conn,) = FakeConnection.instances
    assert conn.enable_calls == 0 and conn.sent == []


def test_privileged_logins_need_no_enable_secret(tmp_path, fake_netmiko):
    fake_netmiko["enabled"] = True                                          # priv 15 로 로그인해 '#' 에서 시작
    name, output, error = _collect(tmp_path, platform="cisco_ios", secret="")
    assert error is None
    (conn,) = FakeConnection.instances
    assert conn.enable_calls == 0 and conn.sent


def test_collect_all_passes_each_devices_platform_and_enable_secret(tmp_path, monkeypatch):
    inventory = tmp_path / "device_inventory.yaml"
    inventory.write_text(yaml.safe_dump({"defaults": {"default_enable_secret": "def-en"}, "devices": [
        {"name": "Core1", "management_ip": "10.0.0.1", "username": "ops", "password": "pw"},
        {"name": "R1", "management_ip": "10.0.0.2", "username": "ops", "password": "pw", "vendor": "Cisco IOS"},
        {"name": "N1", "management_ip": "10.0.0.3", "username": "ops", "password": "pw", "vendor": "Cisco NX-OS",
         "enable_secret": "own-en"},
        {"name": "J1", "management_ip": "10.0.0.4", "username": "ops", "password": "pw", "vendor": "Juniper"},
    ]}), encoding="utf-8")
    connection = tmp_path / "connection.yaml"
    connection.write_text(yaml.safe_dump({"ssh": {"retry_count": 1, "retry_delay_sec": 0}}), encoding="utf-8")
    seen = {}

    def fake_collect(name, *args, **kwargs):
        seen[name] = (kwargs["platform"], kwargs["secret"])
        return name, {"bulk": "ok"}, None

    monkeypatch.setattr(collector, "collect_device", fake_collect)
    collector.collect_all(str(inventory), "lab_t", ["show version"], connection_path=str(connection))
    assert seen == {"Core1": ("arista_eos", "def-en"), "R1": ("cisco_ios", "def-en"),
                    "N1": ("cisco_nxos", "own-en"), "J1": (None, "def-en")}


# --------------------------------------------------------------------------- 4. 세션 점검의 enable

class FakeDevice:
    """세션 채널 대역 — 보낸 줄마다 장비처럼 답을 세션 버퍼에 바로 넣는다(읽기 스레드 대신).
    secret=None 이면 enable 비밀번호가 설정되지 않은 장비(EOS 기본)다. IOS 처럼 세 번 물은 뒤 '% Bad secrets'."""

    def __init__(self, mode=">", secret="en-1", hostname="R1"):
        self.mode, self.secret, self.hostname = mode, secret, hostname
        self.sent = []
        self.asking = 0
        self.sess = None

    def _emit(self, text):
        with self.sess["read_lock"]:
            self.sess["inspect_buffer"].append(text)
            self.sess["accumulated_tail"] = (self.sess["accumulated_tail"] + text)[-400:]

    def _prompt(self):
        return f"\r\n{self.hostname}{self.mode}"

    def send(self, text):
        self.sent.append(text)
        for line in text.split("\n")[:-1]:
            self._line(line)

    def _line(self, line):
        if self.asking:
            if line == self.secret:
                self.asking, self.mode = 0, "#"
                self._emit(self._prompt())
            elif self.asking >= 3:
                self.asking = 0
                self._emit("\r\n% Bad secrets\r\n" + self._prompt())
            else:
                self.asking += 1
                self._emit("\r\nPassword: ")
        elif line.strip() == "enable" and self.mode == ">":
            if self.secret is None:
                self.mode = "#"
                self._emit("enable" + self._prompt())
            else:
                self.asking = 1
                self._emit("enable\r\nPassword: ")
        elif line in ("", "!"):
            self._emit(line + self._prompt())
        else:
            self._emit(f"{line}\r\noutput of {line}{self._prompt()}")


def _session(device, *, platform="cisco_ios", secret="en-1", tail="\r\nR1>"):
    sess = {"device": "R1", "channel": device, "buffer": [], "inspect_buffer": [], "read_lock": threading.Lock(),
            "connected": True, "error": None, "accumulated_tail": tail, "last_hostname": "R1",
            "platform": platform, "enable_secret": secret}
    device.sess = sess
    return sess


@pytest.fixture
def jobs(monkeypatch):
    runner = JobRunner(("inspection",))
    monkeypatch.setattr(inspection, "_inspection_jobs", runner)
    monkeypatch.setattr(inspection, "_drain_before_send", lambda sess: False)
    return runner


def test_enable_answers_the_password_prompt_with_the_enable_secret(jobs):
    device = FakeDevice()
    assert inspection._enter_enable(_session(device)) is None
    assert device.sent == ["enable\n", "en-1\n"] and device.mode == "#"


def test_wrong_enable_secret_ends_the_prompt_and_reports(jobs):
    device = FakeDevice(secret="en-1")
    problem = inspection._enter_enable(_session(device, secret="nope"))
    assert "맞는지 확인" in problem
    assert device.sent == ["enable\n", "nope\n", "\n", "\n"] and device.mode == ">"   # 빈 줄로 묻기를 끝냈다


def test_cisco_without_an_enable_secret_sends_nothing(jobs):
    device = FakeDevice()
    problem = inspection._enter_enable(_session(device, secret=""))
    assert "enable 비밀번호가 없어" in problem and device.sent == []


def test_arista_tries_enable_without_a_secret(jobs):
    device = FakeDevice(secret=None)
    assert inspection._enter_enable(_session(device, platform="arista_eos", secret="")) is None
    assert device.sent == ["enable\n"]


def test_already_privileged_sessions_send_nothing(jobs):
    device = FakeDevice(mode="#")
    assert inspection._enter_enable(_session(device, tail="\r\nR1#")) is None
    assert device.sent == []


def test_unknown_prompt_is_asked_for_again_before_enable(jobs):
    device = FakeDevice()
    assert inspection._enter_enable(_session(device, tail="")) is None
    assert device.sent == ["\n", "enable\n", "en-1\n"]


@pytest.fixture
def one_session(monkeypatch, jobs):
    def make(device, **kwargs):
        monkeypatch.setitem(inspection._sessions, "s1", _session(device, **kwargs))
        return device
    return make


def test_session_inspection_enters_enable_then_sends_the_rest_in_bulk(one_session, jobs, tmp_path):
    device = one_session(FakeDevice())
    inspection._run_one_session_inspection("s1", ["enable", "terminal length 0", "show version"], [str(tmp_path)])
    assert device.sent == ["enable\n", "en-1\n", "terminal length 0\n!\nshow version\n!\n"]
    (saved,) = tmp_path.glob("*.txt")
    text = saved.read_text(encoding="utf-8")
    assert "output of show version" in text and "en-1" not in text and "Password" not in text
    status = jobs.status("inspection")
    assert status["current"] == 3
    assert "en-1" not in json.dumps(status["log"], ensure_ascii=False)


def test_session_inspection_skips_the_device_when_enable_fails(one_session, jobs, tmp_path):
    device = one_session(FakeDevice(), secret="")
    inspection._run_one_session_inspection("s1", ["enable", "show version"], [str(tmp_path)])
    assert device.sent == [] and list(tmp_path.glob("*.txt")) == []
    status = jobs.status("inspection")
    assert status["current"] == 2
    assert any("enable 비밀번호가 없어" in line and "점검하지 않았습니다" in line for line in status["log"])


def test_session_inspection_sends_nothing_to_an_unknown_vendor(one_session, jobs, tmp_path):
    device = one_session(FakeDevice(), platform=None)
    inspection._run_one_session_inspection("s1", ["enable", "show version"], [str(tmp_path)])
    assert device.sent == [] and list(tmp_path.glob("*.txt")) == []
    assert any("지원하지 않는 벤더" in line for line in jobs.status("inspection")["log"])


def test_terminal_targets_carry_platform_and_enable_secret_but_the_screen_does_not():
    inventory = {"defaults": {"default_username": "ops", "default_password": "pw", "default_enable_secret": "def-en"},
                 "devices": [di.normalize_device(d) for d in (
                     {"name": "R1", "management_ip": "10.0.0.1", "vendor": "Cisco IOS"},
                     {"name": "N1", "management_ip": "10.0.0.2", "vendor": "Cisco NX-OS", "enable_secret": "own-en"},
                     {"name": "J1", "management_ip": "10.0.0.3", "vendor": "Juniper"})]}

    class Stub(TerminalSessionApiMixin):
        def _paths(self):
            return {}

        def _project(self):
            return "p1"

        def _load_inventory(self, paths):
            return inventory

    targets = {t["name"]: t for t in Stub()._resolve_terminal_targets()}
    assert (targets["R1"]["platform"], targets["R1"]["enable_secret"]) == ("cisco_ios", "def-en")
    assert (targets["N1"]["platform"], targets["N1"]["enable_secret"]) == ("cisco_nxos", "own-en")
    assert targets["J1"]["platform"] is None
    screen = json.dumps(Stub().get_terminal_targets())
    assert "def-en" not in screen and "own-en" not in screen and "pw" not in screen


# --------------------------------------------------------------------------- 5. 장비 목록 화면의 벤더 선택

JS_DIR = Path(__file__).resolve().parent.parent / "web_ui" / "js"


def _block(name, start):
    """JS 파일에서 start 로 시작하는 최상위 선언 하나 — 열 0 의 '}'·'};'·'];' 까지(한 줄짜리면 그 줄)."""
    lines = (JS_DIR / name).read_text(encoding="utf-8").splitlines()
    begin = next(i for i, line in enumerate(lines) if line.startswith(start))
    if lines[begin].rstrip().endswith((";", "}")):
        return lines[begin]
    end = next(i for i in range(begin, len(lines)) if lines[i] in ("}", "};", "];"))
    return "\n".join(lines[begin:end + 1])


def _run_js(script, tmp_path):
    path = tmp_path / "check.js"
    path.write_bytes(script.encode("utf-8"))
    completed = subprocess.run(["node", str(path)], capture_output=True, encoding="utf-8")
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


@needs_node
def test_vendor_select_keeps_unknown_values_and_escapes_them(tmp_path):
    script = "\n".join([
        _block("core.js", "const _ESC_MAP"), _block("core.js", "function esc("),
        _block("inventory.js", "const INVENTORY_VENDORS"), _block("inventory-table.js", "function vendorSelect("),
        "console.log(JSON.stringify([",
        "  vendorSelect({vendor: 'Cisco NX-OS', platform: 'cisco_nxos'}, 0),",
        "  vendorSelect({vendor: 'Cisco', platform: 'cisco_ios'}, 1),",
        "  vendorSelect({vendor: '<img src=x onerror=\"x\">', platform: null}, 2),",
        "  vendorSelect({}, 3),",
        "]));",
    ])
    nxos, cisco, nasty, empty = _run_js(script, tmp_path)
    assert '<option value="Cisco NX-OS" selected>' in nxos and nxos.count("<option") == 4
    assert '<option value="Cisco" selected>Cisco (Cisco IOS)</option>' in cisco and cisco.count("<option") == 5
    assert "<img" not in nasty and "&lt;img src=x onerror=&quot;x&quot;&gt; — 지원 안 함" in nasty
    assert '<option value="Arista" selected>' in empty


# --------------------------------------------------------------------------- 6. 체크ID 와 벤더별 명령(4-5b)

def _default_catalog():
    from engine import command_catalog as cc
    return cc._make_default_catalog()


def test_every_default_check_has_a_check_id_and_a_cisco_entry():
    """기본 카탈로그의 체크ID 가 모두 표에 있어야 Cisco 장비에 EOS 명령이 그대로 가지 않는다."""
    from engine import vendor_commands
    catalog = _default_catalog()
    assert len(catalog) >= 40 and all(item.get("check_id") for item in catalog)
    assert {item["check_id"] for item in catalog} <= set(vendor_commands._CISCO)


def test_every_cisco_command_is_read_only_and_ascii():
    from core.command_policy import blocked_reason
    from engine import vendor_commands
    for platform in vendor_commands.TRANSLATED_PLATFORMS:
        commands, skipped, _unmapped = vendor_commands.commands_for(_default_catalog(), platform)
        assert skipped == [] and commands
        assert [c for c in commands if blocked_reason(c) or not c.isascii()] == []


def test_old_catalogs_get_check_ids_by_command_text(tmp_path):
    from engine import command_catalog as cc
    path = tmp_path / "commands_catalog.yaml"
    path.write_text(yaml.safe_dump([
        {"id": "essential_0", "category": "essential", "command": "show  Version ", "enabled": True},
        {"id": "essential_1", "category": "essential", "command": "show port-channel dense", "enabled": True,
         "check_id": "custom_check"},
        {"id": "custom_0", "category": "custom", "command": "show hardware capacity", "enabled": True},
    ], allow_unicode=True), encoding="utf-8")
    version, kept, custom = cc.load_catalog(str(path))
    assert version["check_id"] == "version_info"            # 공백·대소문자가 달라도 같은 명령
    assert kept["check_id"] == "custom_check"                # 이미 있는 체크ID 는 그대로
    assert "check_id" not in custom                          # 기본에 없는 명령은 체크ID 없이


def test_catalog_text_keeps_check_ids_and_old_excel_files_get_them(tmp_path):
    import openpyxl
    from engine import command_catalog as cc
    previous = [{"id": "essential_0", "category": "essential", "command": "show lldp neighbors detail",
                 "description": "", "enabled": True, "check_id": "lldp_neighbors"}]
    saved = cc.catalog_from_commands(["show lldp neighbors detail", "show mlag"], previous)
    assert [item.get("check_id") for item in saved] == ["lldp_neighbors", "mlag_status"]   # 고친 명령 글자도 체크ID 를 지킨다
    old = tmp_path / "old.xlsx"
    workbook = openpyxl.Workbook()
    workbook.active.append(["순서", "카테고리", "사용여부", "커맨드", "설명"])
    workbook.active.append([1, "필수", "TRUE", "show mlag", "MLAG"])
    workbook.save(str(old))
    assert cc.import_from_excel(str(old))[0]["check_id"] == "mlag_status"


def test_arista_keeps_the_catalog_commands():
    from engine import command_catalog as cc
    from engine import vendor_commands
    catalog = _default_catalog()
    assert vendor_commands.commands_for(catalog, "arista_eos")[0] == cc.split_enabled_commands(catalog)[0]
    assert "arista_eos" not in vendor_commands.commands_by_platform(catalog)


@pytest.mark.parametrize("platform, present, absent", [
    ("cisco_ios", ["enable", "terminal length 0", "show etherchannel summary", "show processes cpu sorted",
                   "show cdp neighbors detail", "show standby brief", "show interfaces status"],
     ["show port-channel dense", "show processes top once", "show ip virtual-router", "show mlag",
      "show bgp l2vpn evpn summary"]),
    ("cisco_xe", ["show etherchannel summary", "show bgp l2vpn evpn summary", "show nve peers"],
     ["show interfaces vxlan 1-7", "show bgp evpn summary"]),
    ("cisco_nxos", ["show port-channel summary", "show vpc", "show interface status", "show system resources",
                    "show hsrp brief", "show system reset-reason", "show ip arp vrf all"],
     ["show interfaces status", "show processes top once", "show reload cause", "show mlag"]),
])
def test_cisco_devices_get_the_same_checks_in_their_own_commands(platform, present, absent):
    from engine import vendor_commands
    commands, _skipped, unmapped = vendor_commands.commands_for(_default_catalog(), platform)
    assert [c for c in present if c not in commands] == []
    assert [c for c in absent if c in commands] == []
    assert len(commands) == len(set(commands))
    assert "show ip virtual-router" in unmapped and "show interfaces counters queue" in unmapped


def test_custom_and_edited_commands():
    from engine import vendor_commands
    catalog = [
        {"command": "show port-channel summary", "check_id": "port_channel_status", "enabled": True},  # 글자를 고친 기본 명령
        {"command": "show platform", "enabled": True},                                                # 사용자가 더한 명령
        {"command": "show mlag", "check_id": "mlag_status", "enabled": False},
    ]
    assert vendor_commands.commands_for(catalog, "arista_eos")[0] == ["show port-channel summary", "show platform"]
    assert vendor_commands.commands_for(catalog, "cisco_ios")[0] == ["show etherchannel summary", "show platform"]


def test_collect_all_sends_each_platform_its_commands(tmp_path, monkeypatch):
    inventory = tmp_path / "device_inventory.yaml"
    inventory.write_text(yaml.safe_dump({"defaults": {}, "devices": [
        {"name": "Core1", "management_ip": "10.0.0.1", "username": "ops", "password": "pw"},
        {"name": "N1", "management_ip": "10.0.0.3", "username": "ops", "password": "pw", "vendor": "Cisco NX-OS"},
    ]}), encoding="utf-8")
    connection = tmp_path / "connection.yaml"
    connection.write_text(yaml.safe_dump({"ssh": {"retry_count": 1, "retry_delay_sec": 0}}), encoding="utf-8")
    seen = {}

    def fake_collect(name, ip, port, username, password, commands, *args, **kwargs):
        seen[name] = commands
        return name, {"bulk": "ok"}, None

    monkeypatch.setattr(collector, "collect_device", fake_collect)
    collector.collect_all(str(inventory), "lab_t", ["show mlag"], connection_path=str(connection),
                          commands_by_platform={"cisco_nxos": ["show vpc"]})
    assert seen == {"Core1": ["show mlag"], "N1": ["show vpc"]}


def _wait_inspection(api, timeout=5.0):
    deadline = time.time() + timeout
    while api.get_terminal_inspection_status()["running"] and time.time() < deadline:
        time.sleep(0.01)
    return api.get_terminal_inspection_status()


@pytest.fixture
def inspection_api(monkeypatch, tmp_path):
    from core.paths import AppPaths
    from engine.profile_manager import profile_manager as prm
    monkeypatch.setattr(AppPaths, "_user_data_root", tmp_path / "AutoCheck")
    AppPaths.forget_ensured()
    prm.create_profile("고객사A", "2026-11")
    monkeypatch.setattr(inspection, "_inspection_jobs", JobRunner(("inspection",)))
    for sid, platform in (("a", "arista_eos"), ("c", "cisco_ios"), ("n", "cisco_nxos"), ("j", None)):
        monkeypatch.setitem(inspection._sessions, sid, {"device": sid.upper(), "platform": platform})
    sent = {}

    def fake_session(session_id, commands, results_dirs, sync_name=None):
        sent[session_id] = commands
        inspection._inspection_jobs.advance(inspection._KIND, len(commands))

    monkeypatch.setattr(inspection, "_run_one_session_inspection", fake_session)

    class Stub(inspection.TerminalInspectionApiMixin):
        def _paths(self):
            return {"commands_catalog": None, "lab_meta": None}

        def _project(self):
            return "p1"

        def _load_catalog(self, paths):
            return _default_catalog()

        def _resolve_active_customer_profile_names(self):
            return "고객사A", "2026-11"

        def _refresh_realtime_baseline_after_inspection(self):
            return {"ok": False, "error": "테스트"}

        def _sync_session_name(self, session_id, settled=False):
            return None

    api = Stub()
    yield api, sent
    _wait_inspection(api)
    AppPaths.forget_ensured()


def test_session_inspection_sends_each_session_its_platform_commands(inspection_api):
    from engine import command_catalog as cc
    from engine import vendor_commands
    api, sent = inspection_api
    assert api.run_terminal_inspection(["a", "c", "n", "j"]) == {"ok": True}
    status = _wait_inspection(api)
    catalog = _default_catalog()
    assert sent["a"] == cc.split_enabled_commands(catalog)[0]
    assert sent["c"] == vendor_commands.commands_for(catalog, "cisco_ios")[0]
    assert sent["n"] == vendor_commands.commands_for(catalog, "cisco_nxos")[0]
    assert sent["j"] == []
    assert status["total"] == status["current"] == sum(len(c) for c in sent.values())
    ios_line = next(line for line in status["log"] if line.startswith("Cisco IOS 세션"))
    assert "show ip virtual-router" in ios_line


# --------------------------------------------------------------------------- 7. Cisco 출력 해석(4-5c)
# 아래 출력은 Cisco 명령 레퍼런스의 예시 형식으로 만든 것이다 — 실제 장비 출력으로 확인하지 않았다(장비 없음).

IOS_LOG = """SW1#terminal length 0
SW1#show version
Cisco IOS Software, C3750E Software (C3750E-UNIVERSALK9-M), Version 15.2(4)E10, RELEASE SOFTWARE (fc2)
Technical Support: http://www.cisco.com/techsupport
Copyright (c) 1986-2020 by Cisco Systems, Inc.

SW1 uptime is 3 weeks, 2 days, 4 hours, 10 minutes
System returned to ROM by power-on
cisco WS-C3750X-48P (PowerPC405) processor (revision A0) with 262144K bytes of memory.
Processor board ID FDO1234X0AB
Model number                    : WS-C3750X-48P-S
System serial number            : FDO1234X0AB
SW1#show processes cpu sorted
CPU utilization for five seconds: 12%/1%; one minute: 9%; five minutes: 7%
 PID Runtime(ms)     Invoked      uSecs   5Sec   1Min   5Min TTY Process
 186      123456      654321        188  1.27%  0.98%  0.97%   0 Hulc LED Process
SW1#show processes memory sorted
Processor Pool Total:  1000000 Used:  250000 Free:  750000
      I/O Pool Total:   100000 Used:   50000 Free:   50000
SW1#show interfaces counters errors

Port        Align-Err     FCS-Err    Xmit-Err     Rcv-Err  UnderSize  OutDiscards
Gi1/0/1             0           0           0           0          0            0
Gi1/0/3             0          12           0          12          0            0
SW1#show etherchannel summary
Flags:  D - down        P - bundled in port-channel
        s - suspended   U - in use

Number of channel-groups in use: 1
Group  Port-channel  Protocol    Ports
------+-------------+-----------+-----------------------------------------------
1      Po1(SU)         LACP      Gi1/0/1(P)  Gi1/0/2(P)
SW1#show standby brief
                     P indicates configured to preempt.
Interface   Grp  Pri P State   Active          Standby         Virtual IP
Vl10        10   110 P Active  local           10.0.10.3       10.0.10.1
SW1#show vrrp brief
SW1#show ip route summary
IP routing table name is default (0x0)
IP routing table maximum-paths is 32
Route Source    Networks    Subnets     Replicates  Overhead    Memory (bytes)
connected       0           6           0           576         1824
static          1           0           0           96          304
ospf 1          0           10          0           960         3040
  Intra-area: 4 Inter-area: 6 External-1: 0 External-2: 0
internal        5                                               5940
Total           1           16          0           1632        11108
SW1#show cdp neighbors detail
-------------------------
Device ID: SW2.lab.local
Entry address(es):
  IP address: 10.0.0.2
Platform: cisco WS-C3750X-48P,  Capabilities: Switch IGMP
Interface: GigabitEthernet1/0/1,  Port ID (outgoing port): GigabitEthernet1/0/1
Holdtime : 150 sec

Version :
Cisco IOS Software, C3750E Software (C3750E-UNIVERSALK9-M), Version 15.2(4)E10, RELEASE SOFTWARE (fc2)

-------------------------
Device ID: SW2.lab.local
Interface: GigabitEthernet1/0/2,  Port ID (outgoing port): GigabitEthernet1/0/2
Holdtime : 150 sec
SW1#show lldp neighbors detail
------------------------------------------------
Local Intf: Gi1/0/1
Chassis id: 0011.2233.4466
Port id: Gi1/0/1
Port Description: to-SW1
System Name: SW2.lab.local

Time remaining: 99 seconds
------------------------------------------------
Local Intf: Gi1/0/24
Chassis id: 0050.5689.abcd
Port id: 0050.5689.abcd
System Name: not advertised

Total entries displayed: 2
SW1#show interfaces status

Port      Name               Status       Vlan       Duplex  Speed Type
Gi1/0/1   to-SW2             connected    trunk      a-full a-1000 10/100/1000BaseTX
Gi1/0/2   to-SW2             connected    trunk      a-full a-1000 10/100/1000BaseTX
SW1#
"""

XE_VERSION = """Cisco IOS XE Software, Version 17.03.04a
Cisco IOS Software [Amsterdam], Catalyst L3 Switch Software (CAT9K_IOSXE), Version 17.3.4a, RELEASE SOFTWARE (fc3)
SW9K uptime is 1 week, 2 days, 3 hours, 4 minutes
cisco C9300-48P (X86) processor with 1338934K/6147K bytes of memory.
Processor board ID FOC2345X1YZ
Model Number                       : C9300-48P
System Serial Number               : FOC2345X1YZ
"""

NXOS_LOG = """N9K-1# show version
Cisco Nexus Operating System (NX-OS) Software
TAC support: http://www.cisco.com/tac
Software
  BIOS: version 05.42
  NXOS: version 9.3(8)
  NXOS image file is: bootflash:///nxos.9.3.8.bin
Hardware
  cisco Nexus9000 C93180YC-EX chassis
  Processor Board ID FDO21120U8N
  Device name: N9K-1
Kernel uptime is 120 day(s), 3 hour(s), 4 minute(s), 50 second(s)
N9K-1# show inventory
NAME: "Chassis",  DESCR: "Nexus9000 C93180YC-EX chassis"
PID: N9K-C93180YC-EX     ,  VID: A0  ,  SN: FDO21120U8N
N9K-1# show system resources
Load average:   1 minute: 0.29   5 minutes: 0.33   15 minutes: 0.36
Processes   :   585 total, 1 running
CPU states  :   3.00% user,   2.50% kernel,   94.50% idle
Memory usage:   16000000K total,   6000000K used,   10000000K free
N9K-1# show port-channel summary
Group Port-       Type     Protocol  Member Ports
      Channel
--------------------------------------------------------------------------------
1     Po1(SU)     Eth      LACP      Eth1/49(P)   Eth1/50(P)
10    Po10(SU)    Eth      LACP      Eth1/1(P)
N9K-1# show vpc
vPC domain id                     : 10
Peer status                       : peer adjacency formed ok
vPC keep-alive status             : peer is alive
vPC role                          : primary

vPC Peer-link status
---------------------------------------------------------------------
id    Port   Status Active vlans
--    ----   ------ -------------------------------------------------
1     Po1    up     1,10,20

vPC status
----------------------------------------------------------------------------
Id    Port          Status Consistency Reason                Active vlans
--    ------------  ------ ----------- ------                ---------------
10    Po10          up     success     success               1,10,20
N9K-1# show vpc peer-keepalive
N9K-1# show ip route summary vrf all
IP Route Table for VRF "default"
Total number of routes: 13
Best paths per protocol:      Backup paths per protocol:
  am               : 4          None
  local            : 4
  direct           : 4
  ospf-1           : 2
IP Route Table for VRF "management"
Best paths per protocol:      Backup paths per protocol:
  direct           : 1
  static           : 1
N9K-1# show cdp neighbors detail
----------------------------------------
Device ID:N9K-2(FDO21120U9P)
System Name: N9K-2
Interface address(es): 1
    IPv4 Address: 10.0.0.12
Platform: N9K-C93180YC-EX, Capabilities: Router Switch IGMP Filtering Supports-STP-Dispute
Interface: Ethernet1/49, Port ID (outgoing port): Ethernet1/49
Holdtime: 133 sec
----------------------------------------
Device ID:N9K-2(FDO21120U9P)
System Name: N9K-2
Interface: Ethernet1/50, Port ID (outgoing port): Ethernet1/50
Holdtime: 133 sec
N9K-1# show lldp neighbors detail
Chassis id: 00be.7512.3456
Port id: Ethernet1/49
Local Port id: Eth1/49
Port Description: Ethernet1/49
System Name: N9K-2
Time remaining: 113 seconds
Chassis id: 00be.7512.3456
Port id: Ethernet1/50
Local Port id: Eth1/50
System Name: N9K-2
N9K-1# show interface status
--------------------------------------------------------------------------------
Port          Name               Status    Vlan      Duplex  Speed   Type
--------------------------------------------------------------------------------
Eth1/49       vpc-peer-link      connected trunk     full    100G    QSFP-100G-CR4
Eth1/50       vpc-peer-link      connected trunk     full    100G    QSFP-100G-CR4
N9K-1#
"""


def _sections(log):
    from engine.inspection_evaluator import split_transcript
    return split_transcript(log)


def _rows(log, platform):
    from engine.inspection_evaluator import evaluate_device, load_template
    rows = evaluate_device(_sections(log), load_template(), platform=platform)
    return {row["name"]: row for row in rows}


def test_device_facts_come_from_cisco_show_version():
    from engine import inspection_report_builder as builder
    ios, nxos = _sections(IOS_LOG), _sections(NXOS_LOG)
    xe = {"show version": XE_VERSION}
    assert (builder._os_version(ios), builder._serial(ios), builder._model(ios)) == (
        "15.2(4)E10", "FDO1234X0AB", "WS-C3750X-48P-S")
    assert (builder._os_version(xe), builder._serial(xe), builder._model(xe)) == ("17.03.04a", "FOC2345X1YZ", "C9300-48P")
    assert (builder._os_version(nxos), builder._serial(nxos), builder._model(nxos)) == (
        "9.3(8)", "FDO21120U8N", "N9K-C93180YC-EX")
    eos = {"show version": "Arista DCS-7050SX3-48YC8\nSerial number: JPE123\nSoftware image version: 4.28.3M\n"}
    assert (builder._os_version(eos), builder._serial(eos), builder._model(eos)) == ("4.28.3M", "JPE123", "DCS-7050SX3-48YC8")


def test_the_platform_is_read_from_the_log_before_the_inventory():
    from engine import inspection_report_builder as builder
    assert builder._platform({"vendor": "Arista"}, _sections(IOS_LOG)) == "cisco_ios"     # 벤더 칸이 틀려도 로그가 맞다
    assert builder._platform({}, {"show version": XE_VERSION}) == "cisco_xe"
    assert builder._platform({}, _sections(NXOS_LOG)) == "cisco_nxos"
    assert builder._platform({"vendor": "Cisco NX-OS"}, {}) == "cisco_nxos"                 # 로그가 없으면 벤더 칸


def test_ios_items_are_judged_from_cisco_commands():
    rows = _rows(IOS_LOG, "cisco_ios")
    assert rows["System Uptime"]["value"] == "3 weeks, 2 days, 4 hours, 10 minutes"
    assert rows["CPU 사용률"]["value"] == "7.0%" and rows["CPU 사용률"]["method"] == "show processes cpu sorted"
    assert rows["Free Memory"]["value"] == "75.0%" and rows["Free Memory"]["method"] == "show processes memory sorted"
    assert rows["Interface Error"]["status"] == "확인필요" and "Gi1/0/3 FCS-Err 12" in rows["Interface Error"]["value"]
    assert rows["Port-channel"]["method"] == "show etherchannel summary"
    assert rows["VARP/VRRP 상태"]["method"] == "show standby brief"                       # HSRP 를 먼저 본다
    assert rows["Route Table"]["value"] == "connected:6\nstatic:1\nospf:10"                 # Networks+Subnets, 'ospf 1' 의 1 은 빼고
    assert rows["MLAG 상태"]["status"] == "해당없음" and "MLAG" in rows["MLAG 상태"]["value"]
    assert rows["EVPN 상태"]["status"] == "해당없음"


def test_nxos_items_are_judged_from_cisco_commands():
    rows = _rows(NXOS_LOG, "cisco_nxos")
    assert rows["System Uptime"]["value"] == "120 day(s), 3 hour(s), 4 minute(s), 50 second(s)"
    assert rows["CPU 사용률"]["value"] == "5.5%"
    assert rows["Free Memory"]["value"] == "62.5%"
    assert rows["MLAG 상태"]["method"] == "show vpc"
    assert rows["Route Table"]["value"] == "connected:5\nospf:2\nstatic:1"                  # direct=connected, VRF 를 더한다
    assert rows["Port-channel"]["method"] == "show port-channel summary"


def test_arista_patterns_are_untouched():
    from engine import vendor_commands
    from engine.inspection_evaluator import load_template
    for item in load_template()["check_items"]:
        assert vendor_commands.report_patterns(item["commands"], item["evaluator"], "arista_eos") == item["commands"]
        assert vendor_commands.not_applicable(item["commands"], "arista_eos") is None


def test_cisco_reports_cover_every_item_arista_covers():
    """Arista 장비가 판정받는 항목은 Cisco 장비도 판정받는다(같은 내용의 명령을 보내고 그 출력을 찾는다) — 아니면 '해당없음'."""
    from engine import vendor_commands
    from engine.inspection_evaluator import load_template
    catalog = _default_catalog()
    arista = vendor_commands.commands_for(catalog, "arista_eos")[0]
    for platform in vendor_commands.TRANSLATED_PLATFORMS:
        sent = vendor_commands.commands_for(catalog, platform)[0]
        for item in load_template()["check_items"]:
            if not any(p.lower() in c.lower() for p in item["commands"] for c in arista):
                continue                                                       # Arista 도 못 찾는 항목(dir flash: 등)
            patterns = vendor_commands.report_patterns(item["commands"], item["evaluator"], platform)
            found = any(p.lower() in c.lower() for p in patterns for c in sent)
            assert found or vendor_commands.not_applicable(item["commands"], platform), (platform, item["name"])


def test_every_report_alias_is_something_we_send():
    from engine import vendor_commands
    catalog = _default_catalog()
    for aliases in vendor_commands._REPORT_ALIASES.values():
        for platform, patterns in aliases.items():
            sent = vendor_commands.commands_for(catalog, platform)[0]
            for pattern in patterns:
                assert any(pattern in command for command in sent), (platform, pattern)


def test_cdp_and_lldp_details_are_parsed():
    from parsers.show_lldp_neighbors import parse_cdp_neighbors_detail, parse_lldp_neighbors, parse_lldp_neighbors_detail
    sections = _sections(IOS_LOG)
    assert parse_cdp_neighbors_detail(sections["show cdp neighbors detail"]) == [
        {"local_port": "GigabitEthernet1/0/1", "neighbor_device": "SW2.lab.local", "neighbor_port": "GigabitEthernet1/0/1"},
        {"local_port": "GigabitEthernet1/0/2", "neighbor_device": "SW2.lab.local", "neighbor_port": "GigabitEthernet1/0/2"},
    ]
    assert parse_lldp_neighbors_detail(sections["show lldp neighbors detail"]) == [
        {"local_port": "Gi1/0/1", "neighbor_device": "SW2.lab.local", "neighbor_port": "Gi1/0/1"},
        {"local_port": "Gi1/0/24", "neighbor_device": "0050.5689.abcd", "neighbor_port": "0050.5689.abcd"},  # 이름 없는 이웃
    ]
    nxos = _sections(NXOS_LOG)
    assert [n["neighbor_device"] for n in parse_cdp_neighbors_detail(nxos["show cdp neighbors detail"])] == ["N9K-2", "N9K-2"]
    assert [(n["local_port"], n["neighbor_port"]) for n in parse_lldp_neighbors_detail(nxos["show lldp neighbors detail"])] == [
        ("Eth1/49", "Ethernet1/49"), ("Eth1/50", "Ethernet1/50")]
    assert parse_lldp_neighbors(sections["show lldp neighbors detail"]) == []          # 표 파서는 상세를 이웃으로 읽지 않는다


def test_port_channel_membership_and_vpc_are_parsed():
    from parsers.show_interfaces_status import parse_port_channel_summary
    from parsers.show_inventory_mlag_vrrp import parse_vpc
    assert parse_port_channel_summary(_sections(IOS_LOG)["show etherchannel summary"]) == {
        "Gi1/0/1": "Port-Channel1", "Gi1/0/2": "Port-Channel1"}
    nxos = _sections(NXOS_LOG)
    assert parse_port_channel_summary(nxos["show port-channel summary"]) == {
        "Eth1/49": "Port-Channel1", "Eth1/50": "Port-Channel1", "Eth1/1": "Port-Channel10"}
    vpc = parse_vpc(nxos["show vpc"])
    assert (vpc["domain_id"], vpc["peer_link"], vpc["is_active_full"]) == ("10", "Port-Channel1", True)
    broken = nxos["show vpc"].replace("peer is alive", "peer is not reachable through peer-keepalive")
    assert parse_vpc(broken)["is_active_full"] is False


@pytest.mark.parametrize("short, full", [
    ("Gi1/0/1", "GigabitEthernet1/0/1"), ("Fa0/1", "FastEthernet0/1"), ("Eth1/49", "Ethernet1/49"),
    ("Po1", "port-channel1"), ("Twe1/0/1", "TwentyFiveGigE1/0/1"), ("Hu1/0/49", "HundredGigE1/0/49"),
    ("Fo1/1/1", "FortyGigabitEthernet1/1/1"), ("Tw1/0/1", "TwoGigabitEthernet1/0/1"),
])
def test_cisco_short_and_long_port_names_meet(short, full):
    from engine.baseline_store import normalize_interface
    assert normalize_interface(short) == normalize_interface(full)


def _nxos_peer_log():
    return (NXOS_LOG.replace("N9K-1", "N9K-X").replace("N9K-2", "N9K-1").replace("N9K-X", "N9K-2")
            .replace("FDO21120U9P", "FDO21120U8N"))


def test_cisco_topology_folds_cdp_and_lldp_and_draws_the_vpc_pair():
    from engine.topology_builder import build_topology
    sw2_log = IOS_LOG.replace("SW1", "SW2").replace("SW2.lab.local", "SW1.lab.local")
    devices = [{"name": name, "vendor": vendor} for name, vendor in
               (("SW1", "Cisco IOS"), ("SW2", "Cisco IOS"), ("N9K-1", "Cisco NX-OS"), ("N9K-2", "Cisco NX-OS"))]
    topology = build_topology(devices, {"SW1": _sections(IOS_LOG), "SW2": _sections(sw2_log),
                                        "N9K-1": _sections(NXOS_LOG), "N9K-2": _sections(_nxos_peer_log())})
    edges = {(e["a"], e["b"]): e for e in topology["edges"] if e["b"] in ("SW2", "N9K-2")}
    sw = edges[("SW1", "SW2")]                    # CDP·LLDP 가 같은 링크를 두 번 말해도 선은 하나, Po1 묶음 2가닥
    assert sw["bundle"] == "Port-Channel1" and sw["count"] == 2 and sw["state"] == "up" and not sw["one_sided"]
    nx = edges[("N9K-1", "N9K-2")]
    assert nx["bundle"] == "Port-Channel1" and nx["count"] == 2
    (pair,) = topology["pairs"]
    assert (pair["a"], pair["b"], pair["domain"], pair["healthy"]) == ("N9K-1", "N9K-2", "10", True)
    unregistered = [n["name"] for n in topology["nodes"] if not n["registered"]]
    assert unregistered == ["0050.5689.abcd"]      # 이름을 알리지 않는 이웃도 지우지 않는다


@pytest.mark.parametrize("device, is_server", [
    ({"name": "php-sw1"}, False), ({"name": "graph-core"}, False),
    ({"model": "HPE ProLiant DL380"}, True), ({"name": "dell-r740"}, True), ({"role": "server"}, True),
])
def test_server_detection_needs_a_word_start(device, is_server):
    from report.inspection_pdf import _is_server
    assert _is_server(device) is is_server


def test_cisco_interface_states_are_read():
    from parsers.show_interfaces_status import find_errdisabled, parse_status
    status = parse_status("""Port      Name               Status       Vlan       Duplex  Speed Type
Gi1/0/1   to-SW2             connected    trunk      a-full a-1000 10/100/1000BaseTX
Gi1/0/5   printer            err-disabled 10           auto   auto 10/100/1000BaseTX
Eth1/7    --                 notconnec    1          auto    auto    10Gbase-SR
Eth1/8    --                 xcvrAbsen    1          auto    auto    --
""")
    assert status == {"Gi1/0/1": "connected", "Gi1/0/5": "errdisabled", "Eth1/7": "notconnec", "Eth1/8": "xcvrabsen"}
    assert find_errdisabled(status) == ["Gi1/0/5"]

