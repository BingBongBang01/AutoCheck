"""SSH 호스트키 — IMPROVEMENT_PLAN 4-6.

예전엔 paramiko·netmiko 모두 AutoAddPolicy 라 장비 자리에 끼어든 다른 기계에도 그대로 로그인해 비밀번호를 넘겼다.
  1. 처음 접속한 장비의 키를 기억하고(TOFU), 다음부터 다른 키면 **비밀번호를 보내기 전에** 끊는다.
  2. 바뀐 키는 '바뀐 키'로 남고, '다시 신뢰'로만 바뀐다. 키는 고객사마다 따로 기억한다(같은 사설 IP).
  3. SSH 수집(netmiko)도 같은 규칙 — 키가 바뀌면 재시도하지 않는다.
장비 대신 127.0.0.1 에 paramiko 서버 모드로 띄운 진짜 SSH 서버를 쓴다(호스트키를 바꿔 끼울 수 있다).
"""
import json
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import paramiko
import pytest

from core.storage_service import PathTarget
from engine import collector
from engine import host_keys
from engine import ssh_client

SCOPE = "customer:c-alpha"


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "ssh_host_keys.json"
    monkeypatch.setattr(host_keys, "host_keys_path", lambda: str(path))
    return path


def _key():
    return paramiko.ECDSAKey.generate()


# --------------------------------------------------------------------------- 1. 저장소

def test_host_ids_follow_known_hosts_names():
    assert host_keys.host_id("10.0.0.1", 22) == "10.0.0.1"
    assert host_keys.host_id(" Core1.LAB ", "2222") == "[core1.lab]:2222"
    assert host_keys.split_host_id("[10.0.0.1]:2222") == ("10.0.0.1", 2222)
    assert host_keys.split_host_id("10.0.0.1") == ("10.0.0.1", 22)


def test_first_key_is_remembered_and_a_different_key_is_refused(store):
    first, second = _key(), _key()
    assert host_keys.remember(SCOPE, "10.0.0.1", 22, first, device="Core1") == "new"
    assert host_keys.remember(SCOPE, "10.0.0.1", 22, first, device="Core1") == "known"
    with pytest.raises(host_keys.HostKeyChanged) as exc:
        host_keys.remember(SCOPE, "10.0.0.1", 22, second, device="Core1")
    assert exc.value.trusted_fingerprint == host_keys.fingerprint(first)
    assert exc.value.got_fingerprint == host_keys.fingerprint(second)
    assert "로그인하지 않았습니다" in str(exc.value) and "다시 신뢰" in str(exc.value)
    listing = host_keys.listing(SCOPE)
    assert [k["fingerprint"] for k in listing["trusted"]] == [host_keys.fingerprint(first)]   # 기억한 키는 그대로
    (changed,) = listing["changed"]
    assert (changed["fingerprint"], changed["trusted_fingerprint"], changed["device"]) == (
        host_keys.fingerprint(second), host_keys.fingerprint(first), "Core1")
    assert "key" not in changed and "key" not in listing["trusted"][0]                       # 화면에는 지문만


def test_trusting_the_changed_key_replaces_it_and_forget_starts_over(store):
    first, second = _key(), _key()
    host_keys.remember(SCOPE, "10.0.0.1", 22, first)
    with pytest.raises(host_keys.HostKeyChanged):
        host_keys.remember(SCOPE, "10.0.0.1", 22, second)
    assert host_keys.trust_changed(SCOPE, "10.0.0.1", 22) is True
    assert host_keys.remember(SCOPE, "10.0.0.1", 22, second) == "known"
    trusted = host_keys.trusted_entry(SCOPE, "10.0.0.1", 22)
    assert trusted["replaced"] == host_keys.fingerprint(first)
    assert host_keys.listing(SCOPE)["changed"] == []
    assert host_keys.trust_changed(SCOPE, "10.0.0.1", 22) is False                            # 바뀐 키가 없으면 아무 일 없음
    assert host_keys.forget(SCOPE, "10.0.0.1", 22) is True
    assert host_keys.trusted_entry(SCOPE, "10.0.0.1", 22) is None
    assert host_keys.remember(SCOPE, "10.0.0.1", 22, first) == "new"
    assert host_keys.forget(SCOPE, "10.9.9.9", 22) is False


def test_customers_keep_their_own_keys_for_the_same_address(store):
    """여러 고객사를 도는 점검자는 같은 사설 IP 를 고객사마다 다른 장비로 만난다 — 서로 '바뀜'이 아니다."""
    alpha, beta = _key(), _key()
    host_keys.remember("customer:a", "10.0.0.1", 22, alpha)
    assert host_keys.remember("customer:b", "10.0.0.1", 22, beta) == "new"
    assert host_keys.remember("customer:a", "10.0.0.1", 22, alpha) == "known"
    assert host_keys.trusted_entry("customer:a", "10.0.0.1", 2222) is None                     # 포트도 이름의 일부


def test_scope_is_the_customer_of_the_project(monkeypatch):
    from engine import project_manager as pm
    monkeypatch.setattr(pm, "customer_id_of", lambda project_id: "c123" if project_id == "p1" else None)
    assert host_keys.scope_for_project("p1") == "customer:c123"
    assert host_keys.scope_for_project("old-project") == "project:old-project"
    assert host_keys.scope_for_project(None) == host_keys.GLOBAL_SCOPE


def test_parallel_connections_all_get_recorded(store):
    keys = [_key() for _ in range(12)]
    threads = [threading.Thread(target=host_keys.remember, args=(SCOPE, f"10.0.0.{i}", 22, key))
               for i, key in enumerate(keys)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(host_keys.listing(SCOPE)["trusted"]) == 12
    assert json.loads(store.read_text(encoding="utf-8"))["version"] == 1


def test_a_broken_file_is_read_as_empty(store):
    store.write_text("{not json", encoding="utf-8")
    assert host_keys.trusted_entry(SCOPE, "10.0.0.1", 22) is None
    assert host_keys.remember(SCOPE, "10.0.0.1", 22, _key()) == "new"


def test_known_hosts_file_for_netmiko(store):
    key = _key()
    assert host_keys.known_hosts_file(SCOPE, "10.0.0.1", 2222) is None
    host_keys.remember(SCOPE, "10.0.0.1", 2222, key)
    path = host_keys.known_hosts_file(SCOPE, "10.0.0.1", 2222)
    try:
        loaded = paramiko.HostKeys(path)
        assert loaded.lookup("[10.0.0.1]:2222")[key.get_name()] == key
    finally:
        Path(path).unlink()


# --------------------------------------------------------------------------- 2. 진짜 SSH 서버로 접속

class _Server(paramiko.ServerInterface):
    def __init__(self, attempts):
        self.attempts = attempts

    def get_allowed_auths(self, username):
        return "password"

    def check_auth_password(self, username, password):
        self.attempts.append((username, password))
        return paramiko.AUTH_SUCCESSFUL if (username, password) == ("ops", "pw-1") else paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


@pytest.fixture
def ssh_server():
    """127.0.0.1 의 SSH 서버 — state["key"] 를 바꾸면 다음 접속부터 다른 호스트키를 내민다(장비 자리에 끼어든 기계)."""
    state = {"key": _key(), "attempts": [], "connections": 0}
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    transports = []

    def accept_loop():
        while True:
            try:
                sock, _ = listener.accept()
            except OSError:
                return
            state["connections"] += 1
            transport = paramiko.Transport(sock)
            transport.add_server_key(state["key"])
            transports.append(transport)
            try:
                transport.start_server(server=_Server(state["attempts"]))
            except Exception:
                pass

    threading.Thread(target=accept_loop, daemon=True).start()
    state["port"] = listener.getsockname()[1]
    yield state
    listener.close()
    for transport in transports:
        transport.close()


def _target(server, scope=SCOPE):
    return {"name": "Core1", "ip": "127.0.0.1", "port": server["port"], "username": "ops", "password": "pw-1",
            "auth_method": "password", "host_key_scope": scope}


def test_terminal_connections_remember_then_refuse_a_new_key_before_sending_the_password(store, ssh_server):
    client = ssh_client.connect(_target(ssh_server), timeout=5)
    server_key = collector._server_key(type("Netmiko", (), {"remote_conn_pre": client})())   # netmiko 와 같은 경로
    client.close()
    assert server_key == ssh_server["key"]
    assert host_keys.trusted_entry(SCOPE, "127.0.0.1", ssh_server["port"])["fingerprint"] == \
        host_keys.fingerprint(ssh_server["key"])
    assert ssh_server["attempts"] == [("ops", "pw-1")]

    first = ssh_server["key"]
    ssh_server["key"] = _key()                                     # 다른 기계가 끼어들었다
    with pytest.raises(host_keys.HostKeyChanged) as exc:
        ssh_client.connect(_target(ssh_server), timeout=5)
    assert exc.value.trusted_fingerprint == host_keys.fingerprint(first)
    assert ssh_server["attempts"] == [("ops", "pw-1")]               # 비밀번호는 끼어든 기계로 가지 않았다

    assert host_keys.trust_changed(SCOPE, "127.0.0.1", ssh_server["port"])
    ssh_client.connect(_target(ssh_server), timeout=5).close()   # 다시 신뢰한 뒤에는 접속한다
    assert len(ssh_server["attempts"]) == 2


def test_another_customer_does_not_inherit_the_key(store, ssh_server):
    ssh_client.connect(_target(ssh_server, "customer:a"), timeout=5).close()
    ssh_server["key"] = _key()
    ssh_client.connect(_target(ssh_server, "customer:b"), timeout=5).close()   # 다른 고객사 — 처음 보는 장비
    with pytest.raises(host_keys.HostKeyChanged):
        ssh_client.connect(_target(ssh_server, "customer:a"), timeout=5)


def test_ssh_collection_does_not_retry_or_log_in_when_the_key_changed(store, ssh_server, tmp_path, monkeypatch):
    if collector.ConnectHandler is None:
        pytest.skip("netmiko 가 없습니다")
    host_keys.remember(SCOPE, "127.0.0.1", ssh_server["port"], _key(), device="Core1")   # 예전에 기억한 다른 키
    files = []
    real_file = host_keys.known_hosts_file
    monkeypatch.setattr(host_keys, "known_hosts_file", lambda *a: files.append(real_file(*a)) or files[-1])
    name, output, error = collector.collect_device(
        "Core1", "127.0.0.1", ssh_server["port"], "ops", "pw-1", ["show version"],
        PathTarget(path=tmp_path), "Core1.txt", on_update=lambda *a: None, retry_count=3, retry_delay_sec=0,
        platform="cisco_ios", host_key_scope=SCOPE, connect_timeout_sec=5)
    assert output is None and "호스트키가 바뀌었습니다" in error
    assert ssh_server["connections"] == 1                         # 재시도하지 않았다
    assert ssh_server["attempts"] == []                           # 비밀번호를 보내지 않았다(netmiko 도 기억한 키로 확인)
    assert host_keys.listing(SCOPE)["changed"][0]["fingerprint"] == host_keys.fingerprint(ssh_server["key"])
    assert files and not Path(files[0]).exists()                  # 임시 known_hosts 는 지웠다


class _FakeNetmiko:
    """접속은 됐다고 치고 서버 키만 내미는 netmiko 대역."""
    instances = []
    server_key = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.disconnected = False
        key = _FakeNetmiko.server_key
        self.remote_conn_pre = type("Client", (), {"get_transport": lambda _self: type(
            "Transport", (), {"get_remote_server_key": lambda _t: key})()})()
        _FakeNetmiko.instances.append(self)

    def check_enable_mode(self):
        return True

    def send_command_timing(self, *args, **kwargs):
        return "Core1#show version\nArista vEOS\nCore1#"

    def send_command(self, *args, **kwargs):
        return ""

    def disconnect(self):
        self.disconnected = True


def _collect_fake(tmp_path):
    return collector.collect_device(
        "Core1", "10.0.0.1", 22, "ops", "pw-1", ["show version"], PathTarget(path=tmp_path), "Core1.txt",
        on_update=lambda *a: None, retry_count=3, retry_delay_sec=0, host_key_scope=SCOPE)


def test_ssh_collection_remembers_first_keys_and_hands_known_ones_to_netmiko(store, tmp_path, monkeypatch):
    _FakeNetmiko.instances = []
    _FakeNetmiko.server_key = _key()
    monkeypatch.setattr(collector, "ConnectHandler", _FakeNetmiko)
    assert _collect_fake(tmp_path)[2] is None
    assert "alt_key_file" not in _FakeNetmiko.instances[0].kwargs              # 처음 — 받은 키를 기억한다
    assert host_keys.trusted_entry(SCOPE, "10.0.0.1", 22)["fingerprint"] == host_keys.fingerprint(_FakeNetmiko.server_key)
    assert _collect_fake(tmp_path)[2] is None
    second = _FakeNetmiko.instances[1].kwargs
    assert second["alt_host_keys"] is True and not Path(second["alt_key_file"]).exists()   # 기억한 키로 확인, 파일은 지움


def test_a_key_that_changed_after_login_still_stops_collection(store, tmp_path, monkeypatch):
    """기억한 키가 netmiko 에 넘긴 뒤 생긴 경우(다른 프로세스가 먼저 기억) — 접속 뒤 확인에서 걸러 재시도하지 않는다."""
    _FakeNetmiko.instances = []
    _FakeNetmiko.server_key = _key()
    host_keys.remember(SCOPE, "10.0.0.1", 22, _key())
    monkeypatch.setattr(collector, "ConnectHandler", _FakeNetmiko)
    name, output, error = _collect_fake(tmp_path)
    assert output is None and "호스트키가 바뀌었습니다" in error
    assert len(_FakeNetmiko.instances) == 1 and _FakeNetmiko.instances[0].disconnected
    assert not (tmp_path / "Core1.txt").exists()


# --------------------------------------------------------------------------- 3. 연결 확인·화면 API

def test_connection_check_reports_new_and_changed_keys(store, ssh_server, monkeypatch):
    from engine import device_probe
    monkeypatch.setattr(device_probe, "_hostname_via_exec", lambda client, role: "Core1")
    device = {"name": "Core1", "management_ip": "127.0.0.1", "ssh_port": ssh_server["port"],
              "username": "ops", "password": "pw-1"}
    first = device_probe.probe_device(device, {}, timeout=5, host_key_scope=SCOPE)
    assert first["authenticated"] and first["host_key"] == {
        "status": "new", "fingerprint": host_keys.fingerprint(ssh_server["key"])}
    assert "호스트키 기억함" in first["detail"]
    again = device_probe.probe_device(device, {}, timeout=5, host_key_scope=SCOPE)
    assert again["authenticated"] and "host_key" not in again
    ssh_server["key"] = _key()
    changed = device_probe.probe_device(device, {}, timeout=5, host_key_scope=SCOPE)
    assert not changed["authenticated"] and changed["reachable"]
    assert changed["host_key"]["status"] == "changed" and changed["host_key"]["got"] == host_keys.fingerprint(ssh_server["key"])
    assert "다시 신뢰" in changed["detail"]


def test_screen_api_lists_trusts_and_forgets(store, monkeypatch):
    from api.host_key_api import HostKeyApiMixin
    monkeypatch.setattr(host_keys, "scope_for_project", lambda project_id: SCOPE)

    class Stub(HostKeyApiMixin):
        def _project(self):
            return "p1"

    api = Stub()
    first, second = _key(), _key()
    host_keys.remember(SCOPE, "10.0.0.1", 22, first, device="Core1")
    with pytest.raises(host_keys.HostKeyChanged):
        host_keys.remember(SCOPE, "10.0.0.1", 22, second, device="Core1")
    listed = api.get_host_keys()
    assert [k["device"] for k in listed["changed"]] == ["Core1"]
    assert "key" not in json.dumps(listed).replace("key_type", "")
    assert api.trust_host_key("10.0.0.1", 22) == {"ok": True}
    assert "error" in api.trust_host_key("10.0.0.1", 22)
    assert api.forget_host_key("10.0.0.1", 22) == {"ok": True}
    assert api.get_host_keys() == {"trusted": [], "changed": []}


def test_terminal_targets_carry_the_customer_scope(monkeypatch):
    from api.terminal_session_api import TerminalSessionApiMixin
    monkeypatch.setattr(host_keys, "scope_for_project", lambda project_id: f"customer:of-{project_id}")

    class Stub(TerminalSessionApiMixin):
        def _paths(self):
            return {}

        def _project(self):
            return "p1"

        def _load_inventory(self, paths):
            return {"defaults": {}, "devices": [{"name": "Core1", "management_ip": "10.0.0.1", "enabled": True}]}

    (target,) = Stub()._resolve_terminal_targets()
    assert target["host_key_scope"] == "customer:of-p1"


# --------------------------------------------------------------------------- 4. 화면(node)

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
def test_changed_keys_are_shown_with_a_trust_button_and_escaped(tmp_path):
    script = "\n".join([
        _block("core.js", "const _ESC_MAP"), _block("core.js", "function esc("),
        _block("core.js", "function escapeHtml("), _block("core.js", "function escapeAttr("),
        _block("inventory-probe.js", "function probeStateOf("), _block("inventory-probe.js", "function probeShortLabel("),
        _block("inventory-probe.js", "function probeStatusCell("),
        _block("inventory-hostkeys.js", "function hostKeyAddress("), _block("inventory-hostkeys.js", "function hostKeyTime("),
        _block("inventory-hostkeys.js", "function hostKeyRowsHtml("),
        "const inventoryProbe = {r7: {status: 'fail', reachable: true, detail: '바뀜 <b>x</b>',",
        "  hostKey: {status: 'changed', host: '10.0.0.1', port: 22, trusted: 'SHA256:a', got: 'SHA256:b'}}};",
        "console.log(JSON.stringify([probeStatusCell({_rid: 'r7'}), hostKeyRowsHtml({",
        "  changed: [{device: '<img src=x>', host: '10.0.0.1', port: 22, key_type: 'ecdsa-sha2-nistp256',",
        "             fingerprint: 'SHA256:b', trusted_fingerprint: 'SHA256:a', seen_at: '2026-10-29T10:00:00'}],",
        "  trusted: [{device: 'Agg1', host: '10.0.0.2', port: 2222, key_type: 'ssh-ed25519', fingerprint: 'SHA256:c',",
        "             first_seen: '2026-10-28T09:00:00'}]})]));",
    ])
    path = tmp_path / "check.js"
    path.write_bytes(script.encode("utf-8"))
    completed = subprocess.run(["node", str(path)], capture_output=True, encoding="utf-8")
    assert completed.returncode == 0, completed.stderr
    cell, rows = json.loads(completed.stdout.strip().splitlines()[-1])
    assert "호스트키 바뀜" in cell and 'data-trust-key="r7"' in cell and "<b>" not in cell
    assert "<img" not in rows and "&lt;img src=x&gt;" in rows
    assert 'data-hk-trust="0"' in rows and 'data-hk-forget="0"' in rows
    assert "10.0.0.2:2222" in rows and "2026-10-28 09:00:00" in rows and "지금 키 SHA256:b" in rows
