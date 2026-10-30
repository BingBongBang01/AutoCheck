"""SSH 호스트키 — 처음 접속한 장비의 키를 기억하고(TOFU), 키가 바뀌면 로그인하지 않는다(IMPROVEMENT_PLAN 4-6).

예전엔 paramiko(터미널·연결 확인·실시간 상태 조회)와 netmiko(SSH 수집·무인 점검) 모두 AutoAddPolicy 라 어떤 키든
받았다 — 장비 자리에 다른 기계가 끼어들어도 그대로 로그인해 비밀번호를 넘겼다. 이제:
  - 처음 보는 장비(주소:포트)는 그 키를 기억한다(Trust On First Use — OpenSSH 의 StrictHostKeyChecking=accept-new).
  - 기억한 키와 다르면 비밀번호를 보내기 전에 끊고 HostKeyChanged 를 낸다. 새 키는 '바뀐 키'로 남겨 두고, 사용자가
    장비 목록의 'SSH 호스트키'에서 지문을 확인한 뒤 '다시 신뢰'를 누를 때만 그 키로 바꾼다.
  - 고객사마다 따로 기억한다 — 여러 고객사를 도는 점검자는 같은 사설 IP(10.0.0.1)를 고객사마다 다른 장비로 만난다.
    한 목록에 두면 고객사를 옮길 때마다 '바뀜' 경고가 나고, 경고에 익숙해지면 진짜 가로채기를 놓친다.

저장: <사용자 데이터>/ssh_host_keys.json(공개키라 봉인하지 않는다). 화면과 무인 점검 프로세스가 함께 쓰므로
읽고-고치고-쓰는 동안 프로세스 사이 잠금(core/process_lock)을 잡는다. 키는 '주소:포트'에 붙는다 — paramiko·OpenSSH
known_hosts 와 같은 이름('10.0.0.1', 포트가 22 가 아니면 '[10.0.0.1]:2222').
"""
import base64
import contextlib
import hashlib
import json
import os
import tempfile
import threading
import time

from core.app_logger import log_event
from core.app_settings import host_keys_path
from core.atomic_io import write_text_atomic
from core.paths import AppPaths
from core.process_lock import FileLock
from core.utils.datetime import format_iso

GLOBAL_SCOPE = "global"     # 프로파일 밖에서 접속하는 경우(예전 CLI 수집 등)
_LOCK_WAIT_SEC = 3.0
_lock = threading.RLock()


class HostKeyChanged(Exception):
    """기억한 호스트키와 다른 키를 받았다 — 로그인하지 않았다. 다시 시도해도 같으므로 재시도하지 않는다."""

    def __init__(self, host, port, trusted_fingerprint, got_fingerprint):
        self.host, self.port = host, int(port or 22)
        self.trusted_fingerprint, self.got_fingerprint = trusted_fingerprint, got_fingerprint
        super().__init__(
            f"SSH 호스트키가 바뀌었습니다({host}:{self.port} — 기억한 키 {trusted_fingerprint}, 지금 키 {got_fingerprint}). "
            "장비를 바꾸거나 다시 설치하지 않았다면 중간에서 가로채는 접속일 수 있어 로그인하지 않았습니다 — 지문을 확인한 뒤 "
            "장비 목록의 'SSH 호스트키'에서 '다시 신뢰'를 누르세요.")

    def as_dict(self):
        return {"status": "changed", "host": self.host, "port": self.port,
                "trusted": self.trusted_fingerprint, "got": self.got_fingerprint}


def host_id(host, port):
    """known_hosts 이름 — 포트 22 는 주소만, 그 밖은 '[주소]:포트'(paramiko 가 키를 찾는 이름과 같다)."""
    host, port = str(host or "").strip().lower(), int(port or 22)
    return host if port == 22 else f"[{host}]:{port}"


def split_host_id(value):
    if value.startswith("[") and "]:" in value:
        host, port = value[1:].split("]:", 1)
        return host, int(port)
    return value, 22


def scope_for_project(project_id):
    """키를 묶는 범위 — 프로파일이 속한 고객사(회차가 바뀌어도 같은 장비다). 고객사 id 가 없는 예전 프로파일은 그
    프로파일 하나."""
    if not project_id:
        return GLOBAL_SCOPE
    from engine import project_manager as pm
    customer_id = pm.customer_id_of(project_id)
    return f"customer:{customer_id}" if customer_id else f"project:{project_id}"


def fingerprint(pkey):
    """OpenSSH 와 같은 'SHA256:…' 지문 — 장비의 키 지문(ssh-keygen -lf, 장비의 호스트키 조회 명령)과 대조할 수 있다."""
    digest = hashlib.sha256(pkey.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _key_fields(pkey):
    return {"key_type": pkey.get_name(), "key": pkey.get_base64(), "fingerprint": fingerprint(pkey)}


def _load():
    try:
        with open(host_keys_path(), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log_event(f"SSH 호스트키 파일을 읽지 못했습니다 — 기억한 키가 없는 것으로 봅니다: {exc}",
                  source="host_keys", level="WARNING")
        return {}
    return data if isinstance(data, dict) else {}


def _bucket(data, scope):
    bucket = data.setdefault("scopes", {}).setdefault(scope, {})
    bucket.setdefault("trusted", {})
    bucket.setdefault("changed", {})
    return bucket


def _read_bucket(scope):
    return ((_load().get("scopes") or {}).get(scope)) or {}


@contextlib.contextmanager
def _update():
    """읽고-고치고-쓰기를 한 번에 하나씩 — 이 프로세스의 스레드끼리(잠금), 화면과 무인 점검 프로세스끼리(파일 잠금).
    파일 잠금을 몇 초 안에 못 잡으면 그냥 쓴다 — 키 기록 하나 때문에 점검을 멈추지 않는다(덮어써도 다음 접속이 다시 적는다)."""
    with _lock:
        file_lock = FileLock(AppPaths.user_data_root() / "locks" / "ssh_host_keys.lock")
        deadline = time.monotonic() + _LOCK_WAIT_SEC
        while not file_lock.acquire() and time.monotonic() < deadline:
            time.sleep(0.05)
        try:
            data = _load()
            yield data
            data["version"] = 1
            write_text_atomic(host_keys_path(), json.dumps(data, ensure_ascii=False, indent=2))
        finally:
            file_lock.release()


def trusted_entry(scope, host, port):
    """기억한 키 {key_type, key, fingerprint, first_seen, device} — 없으면 None."""
    return (_read_bucket(scope).get("trusted") or {}).get(host_id(host, port))


def remember(scope, host, port, pkey, device=""):
    """접속에서 받은 서버 키를 확인한다. 처음 보는 장비면 기억하고 'new', 기억한 키와 같으면 'known'. 다르면 그 키를
    '바뀐 키'로 남기고 HostKeyChanged 를 낸다(기억한 키는 그대로 둔다)."""
    fields = _key_fields(pkey)
    entry = trusted_entry(scope, host, port)
    if entry and (entry.get("key_type"), entry.get("key")) == (fields["key_type"], fields["key"]):
        return "known"
    hid = host_id(host, port)
    with _update() as data:
        bucket = _bucket(data, scope)
        entry = bucket["trusted"].get(hid)
        if entry is None:
            bucket["trusted"][hid] = dict(fields, first_seen=format_iso(), device=device or "")
            bucket["changed"].pop(hid, None)
            status = "new"
        elif (entry.get("key_type"), entry.get("key")) == (fields["key_type"], fields["key"]):
            status = "known"
        else:
            bucket["changed"][hid] = dict(fields, seen_at=format_iso(), device=device or entry.get("device", ""),
                                          trusted_fingerprint=entry.get("fingerprint", ""))
            status = "changed"
    if status == "new":
        log_event(f"SSH 호스트키 기억 — {device or hid} {hid} {fields['fingerprint']}", source="host_keys")
    elif status == "changed":
        log_event(f"SSH 호스트키 바뀜 — {device or hid} {hid}: 기억한 키 {entry.get('fingerprint')}, "
                  f"받은 키 {fields['fingerprint']} — 로그인하지 않음", source="host_keys", level="WARNING")
        raise HostKeyChanged(host, port, entry.get("fingerprint", ""), fields["fingerprint"])
    return status


def trust_changed(scope, host, port):
    """'다시 신뢰' — 바뀐 키로 기억을 바꾼다. 바뀐 키가 남아 있지 않으면 False."""
    hid = host_id(host, port)
    with _update() as data:
        bucket = _bucket(data, scope)
        pending = bucket["changed"].pop(hid, None)
        if pending:
            previous = bucket["trusted"].get(hid) or {}
            bucket["trusted"][hid] = {
                "key_type": pending["key_type"], "key": pending["key"], "fingerprint": pending["fingerprint"],
                "first_seen": format_iso(), "device": pending.get("device") or previous.get("device", ""),
                "replaced": previous.get("fingerprint", ""),
            }
    if pending:
        log_event(f"SSH 호스트키 다시 신뢰 — {hid} {pending['fingerprint']}", source="host_keys")
    return bool(pending)


def forget(scope, host, port):
    """기억한 키(와 바뀐 키)를 지운다 — 다음 접속이 그때 받은 키를 새로 기억한다. 지운 것이 없으면 False."""
    hid = host_id(host, port)
    with _update() as data:
        bucket = _bucket(data, scope)
        removed = [bucket[kind].pop(hid, None) for kind in ("trusted", "changed")]
    if any(removed):
        log_event(f"SSH 호스트키 지움 — {hid}", source="host_keys")
    return any(removed)


def listing(scope):
    """화면용 — {"trusted": [...], "changed": [...]}. 키 본문은 빼고 지문만(대조는 지문으로 한다)."""
    bucket = _read_bucket(scope)

    def rows(kind):
        out = []
        for hid, entry in (bucket.get(kind) or {}).items():
            host, port = split_host_id(hid)
            out.append(dict({k: v for k, v in entry.items() if k != "key"}, host=host, port=port))
        return sorted(out, key=lambda row: (row.get("device") or "", row["host"], row["port"]))

    return {"trusted": rows("trusted"), "changed": rows("changed")}


def _pkey(hid, entry):
    """저장한 키 -> paramiko PKey(known_hosts 한 줄로 읽는다). 읽을 수 없는 종류면 None."""
    from paramiko.hostkeys import HostKeyEntry
    try:
        parsed = HostKeyEntry.from_line(f"{hid} {entry['key_type']} {entry['key']}")
    except Exception:
        return None
    return parsed.key if parsed is not None else None


def prepare_client(client, scope, host, port, device=""):
    """paramiko.SSHClient 를 이 장비의 기억한 키로 준비한다(예전의 AutoAddPolicy 자리).

    기억한 키를 클라이언트에 넣어 두면 paramiko 가 그 종류의 키를 먼저 협상하고, 다른 키를 받으면 인증 전에
    BadHostKeyException 을 낸다(from_exception 이 HostKeyChanged 로 바꾼다). 처음 보는 장비는 정책이 그 키를 기억한다."""
    import paramiko

    hid = host_id(host, port)
    entry = trusted_entry(scope, host, port)
    key = _pkey(hid, entry) if entry else None
    if key is not None:
        client.get_host_keys().add(hid, entry["key_type"], key)

    class RememberNewKey(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, _client, _hostname, server_key):
            remember(scope, host, port, server_key, device)   # 그 사이 다른 키가 기억됐으면 HostKeyChanged

    client.set_missing_host_key_policy(RememberNewKey())


def known_hosts_file(scope, host, port):
    """netmiko(alt_host_keys/alt_key_file)용 — 이 장비의 기억한 키 한 줄을 담은 임시 파일 경로, 기억한 키가 없으면 None.
    부른 쪽이 접속 뒤 지운다. netmiko 는 이 파일의 키를 paramiko 에 넣어 접속하므로 prepare_client 와 같은 검사가 된다."""
    entry = trusted_entry(scope, host, port)
    if not entry:
        return None
    fd, path = tempfile.mkstemp(prefix="autocheck_known_hosts_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"{host_id(host, port)} {entry['key_type']} {entry['key']}\n")
    return path


def from_exception(exc, scope, host, port, device=""):
    """접속 예외에서 호스트키 문제를 찾는다 — paramiko 의 BadHostKeyException(netmiko 는 이것을 NetmikoTimeoutException
    으로 감싼다 — 예외 사슬을 따라간다)이면 받은 키를 '바뀐 키'로 남기고 HostKeyChanged 를 돌려준다. 아니면 None."""
    import paramiko

    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, HostKeyChanged):
            return exc
        if isinstance(exc, paramiko.BadHostKeyException):
            try:
                remember(scope, host, port, exc.key, device)
            except HostKeyChanged as changed:
                return changed
            return None     # 그 사이 기억이 지워져 이번 키를 새로 기억했다 — 다음 시도는 통과한다
        exc = exc.__cause__ or exc.__context__
    return None
