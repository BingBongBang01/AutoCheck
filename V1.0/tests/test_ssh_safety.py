"""SSH 운영 안전 — IMPROVEMENT_PLAN 1-8. 실제 장비에는 접속하지 않는다(netmiko·paramiko 자리를 가짜로 바꾼다).

  1. admin/admin 폴백 제거: 계정이 비면 수집·연결 확인·터미널 접속 모두 로그인 시도 없이 이유를 알린다.
  2. 수집기는 실패한 시도에서도 세션을 닫고(finally), connection.yaml 의 connect_timeout_sec 를 쓴다.
  3. 붙여넣은 개인키는 디스크에 쓰지 않고 메모리에서 읽는다(수집기도 지원).
  4. 사전점검 대표 장비 하나가 꺼져 있어도 다른 장비가 응답하면 수집을 계속한다.
"""
import tempfile

import pytest
import yaml

from engine import collector
from engine import device_inventory as di
from engine import ssh_client


# --------------------------------------------------------------------------- 1. 계정 폴백

def test_empty_credentials_are_not_filled_with_admin():
    device = {"name": "Core1", "management_ip": "10.0.0.1"}
    assert di.resolve_credentials(device, {}) == ("10.0.0.1", 22, "", "")
    assert di.resolve_credentials(device, dict(di.DEFAULT_PROJECT_DEFAULTS))[2:] == ("", "")
    assert di.resolve_credentials(device, {"default_username": "ops", "default_password": "pw"})[2:] == ("ops", "pw")


def test_new_projects_have_no_default_password():
    from engine import project_manager as pm
    assert di.DEFAULT_PROJECT_DEFAULTS["default_username"] == ""
    assert di.DEFAULT_PROJECT_DEFAULTS["default_password"] == ""
    assert pm.DEFAULT_IP_ALLOCATION["default_credentials"] == {"username": "", "password": ""}


@pytest.mark.parametrize("username, password, uses_key, ok", [
    ("ops", "pw", False, True),
    ("ops", "", True, True),          # 키로 접속하면 비밀번호는 없어도 된다
    ("ops", "", False, False),
    ("", "pw", False, False),
    ("", "", True, False),
])
def test_credential_problem(username, password, uses_key, ok):
    assert (di.credential_problem(username, password, uses_key) is None) is ok


def test_uses_key_auth():
    assert di.uses_key_auth({"auth_method": "public_key", "key_content": "-----BEGIN"}) is True
    assert di.uses_key_auth({"auth_method": "public_key", "key_path": "", "key_content": ""}) is False
    assert di.uses_key_auth({"auth_method": "password", "key_path": "C:/k"}) is False


def test_probe_does_not_try_to_log_in_without_credentials(monkeypatch):
    from engine import device_probe

    def no_network(*args, **kwargs):
        raise AssertionError("계정이 없으면 접속을 시도하면 안 된다")

    monkeypatch.setattr(device_probe, "_port_open", no_network)
    monkeypatch.setattr(ssh_client, "connect", no_network)
    result = device_probe.probe_device({"name": "Core1", "management_ip": "10.0.0.1"}, {})
    assert result["authenticated"] is False
    assert "비밀번호가 비어" in result["detail"] or "사용자명이 비어" in result["detail"]


def test_terminal_sessions_are_not_opened_without_credentials(monkeypatch):
    from api import terminal_session_api
    from api.terminal_session_api import TerminalSessionApiMixin
    opened = []
    monkeypatch.setattr(terminal_session_api, "_open_session", lambda sid, t: opened.append(t["name"]) or (True, None))

    class Stub(TerminalSessionApiMixin):
        def _paths(self):
            return {}

        def _project(self):
            return "p1"

        def _load_inventory(self, paths):
            return {"defaults": {}, "devices": [
                {"name": "NoPass", "management_ip": "10.0.0.1", "username": "ops", "enabled": True},
                {"name": "Ready", "management_ip": "10.0.0.2", "username": "ops", "password": "pw", "enabled": True},
            ]}

    results = {r["device"]: r for r in Stub().connect_terminal_sessions()}
    assert opened == ["Ready"]
    assert results["Ready"]["ok"] is True
    assert results["NoPass"]["ok"] is False and "비밀번호가 비어" in results["NoPass"]["error"]


# --------------------------------------------------------------------------- 2·3. 수집기 접속

class FakeConnection:
    instances = []

    def __init__(self, fail_on_command=False, **kwargs):
        self.kwargs = kwargs
        self.fail_on_command = fail_on_command
        self.disconnected = False
        FakeConnection.instances.append(self)

    def check_enable_mode(self):
        return True

    def send_command_timing(self, *args, **kwargs):
        if self.fail_on_command:
            raise OSError("세션이 끊어졌습니다")
        return "Arista vEOS\nCore1#"

    def send_command(self, *args, **kwargs):
        return "hostname Core1"

    def disconnect(self):
        self.disconnected = True


@pytest.fixture
def fake_netmiko(monkeypatch):
    FakeConnection.instances = []
    state = {"fail": False}
    monkeypatch.setattr(collector, "ConnectHandler", lambda **kw: FakeConnection(fail_on_command=state["fail"], **kw))
    return state


def _collect(tmp_path, **overrides):
    from core.storage_service import PathTarget
    kwargs = dict(name="Core1", ip="10.0.0.1", port=22, username="ops", password="pw", commands=["show version"],
                  log_target=PathTarget(path=tmp_path), log_rel_path="Core1.txt", on_update=lambda *a: None,
                  retry_count=2, retry_delay_sec=0)
    kwargs.update(overrides)
    return collector.collect_device(**kwargs)


def test_collector_refuses_to_connect_without_credentials(tmp_path, fake_netmiko):
    name, output, error = _collect(tmp_path, password="")
    assert output is None and "비밀번호가 비어" in error
    assert FakeConnection.instances == []


def test_collector_uses_configured_timeout_and_closes_sessions(tmp_path, fake_netmiko):
    name, output, error = _collect(tmp_path, connect_timeout_sec=7)
    assert error is None
    (conn,) = FakeConnection.instances
    assert conn.kwargs["timeout"] == 7 and conn.kwargs["conn_timeout"] == 7
    assert conn.disconnected is True
    assert (tmp_path / "Core1.txt").exists()


def test_collector_closes_the_session_when_an_attempt_fails(tmp_path, fake_netmiko):
    fake_netmiko["fail"] = True
    name, output, error = _collect(tmp_path)
    assert output is None and "세션이 끊어졌습니다" in error
    assert len(FakeConnection.instances) == 2                       # 재시도 2회
    assert all(conn.disconnected for conn in FakeConnection.instances)


def test_collector_reads_pasted_keys_in_memory(tmp_path, fake_netmiko, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(ssh_client, "load_private_key_text", lambda text, passphrase=None: sentinel)
    name, output, error = _collect(tmp_path, password="", key_content="-----BEGIN OPENSSH PRIVATE KEY-----")
    assert error is None
    (conn,) = FakeConnection.instances
    assert conn.kwargs["pkey"] is sentinel and conn.kwargs["use_keys"] is True and conn.kwargs["password"] is None


def test_collector_reports_a_bad_key_without_retrying(tmp_path, fake_netmiko):
    name, output, error = _collect(tmp_path, password="", key_content="이건 키가 아니다")
    assert output is None and "개인키를 읽을 수 없습니다" in error
    assert "이건 키가 아니다" not in error
    assert FakeConnection.instances == []


def _ed25519_pem():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    return ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption()).decode()


def test_pasted_keys_never_touch_the_disk(monkeypatch):
    def no_temp_files(*args, **kwargs):
        raise AssertionError("붙여넣은 키를 임시 파일로 쓰면 안 된다")

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", no_temp_files)
    monkeypatch.setattr(tempfile, "mkstemp", no_temp_files)
    kwargs = ssh_client.build_connect_kwargs({"ip": "10.0.0.1", "username": "ops", "auth_method": "public_key",
                                              "key_content": _ed25519_pem()})
    assert kwargs["pkey"].get_name() == "ssh-ed25519"
    assert "password" not in kwargs


def test_bad_pasted_key_error_does_not_echo_the_key():
    with pytest.raises(ValueError) as exc:
        ssh_client.load_private_key_text("-----BEGIN OPENSSH PRIVATE KEY-----\nSECRETBODY\n")
    assert "SECRETBODY" not in str(exc.value)


# --------------------------------------------------------------------------- 4. 사전점검

@pytest.fixture
def two_device_lab(tmp_path, monkeypatch):
    inventory = tmp_path / "device_inventory.yaml"
    inventory.write_text(yaml.safe_dump({"defaults": {}, "devices": [
        {"name": "Core1", "management_ip": "10.0.0.1", "username": "ops", "password": "pw", "enabled": True},
        {"name": "Core2", "management_ip": "10.0.0.2", "username": "ops", "password": "pw", "enabled": True},
    ]}), encoding="utf-8")
    connection = tmp_path / "connection.yaml"
    connection.write_text(yaml.safe_dump({"network": {"pre_flight_check": True, "check_target_node": "Core1"},
                                          "ssh": {"retry_count": 1, "retry_delay_sec": 0}}), encoding="utf-8")
    collected = []
    monkeypatch.setattr(collector, "collect_device",
                        lambda name, *a, **k: (collected.append(name), (name, {"bulk": "ok"}, None))[1])
    return str(inventory), str(connection), collected


def test_one_dead_check_target_does_not_stop_collection(two_device_lab, monkeypatch):
    inventory, connection, collected = two_device_lab
    checked = []

    def pre_flight(ip, port=22, timeout=3):
        checked.append(ip)
        return (ip != "10.0.0.1"), "응답 없음"

    monkeypatch.setattr(collector, "pre_flight_check", pre_flight)
    results, errors, _session_dir = collector.collect_all(inventory, "lab_t", ["show version"],
                                                          connection_path=connection)
    assert checked == ["10.0.0.1", "10.0.0.2"]        # 대표 장비(Core1) 먼저, 실패하면 다음 장비
    assert sorted(results) == ["Core1", "Core2"] and errors == {}
    assert sorted(collected) == ["Core1", "Core2"]


def test_collection_stops_when_no_check_target_answers(two_device_lab, monkeypatch):
    inventory, connection, collected = two_device_lab
    monkeypatch.setattr(collector, "pre_flight_check", lambda ip, port=22, timeout=3: (False, "응답 없음"))
    assert collector.collect_all(inventory, "lab_t", ["show version"], connection_path=connection) == (None, None, None)
    assert collected == []
