"""
장비 연결 확인(Probe) — 장비 목록에서 IP/계정을 고칠 때마다 자동으로 돌아가는 검사.

device_inventory_reachability.py의 check_reachability()는 소켓 포트만 두드려서
"열려 있는지"만 본다(대시보드 Reachable/Offline 집계용, 가볍고 빠름).
여기서는 한 단계 더 들어가서 실제로 SSH 인증까지 하고 장비 스스로가 말하는
hostname을 받아온다 — 그래야 장비 목록의 이름을 실제 장비명으로 맞출 수 있다.

그래서 결과가 3단계로 나뉜다:
  reachable=False                   포트가 안 열림 (IP/방화벽/장비 다운)
  reachable=True, authenticated=False  포트는 열렸는데 계정/키가 틀림
  authenticated=True                접속 성공 — 이때만 hostname이 채워진다
"""
import re
import socket

from core.ansi_sanitizer import strip_ansi
from engine.device_inventory_core import resolve_credentials

CONNECT_TIMEOUT = 6
COMMAND_TIMEOUT = 6

# 장비가 hostname을 알려주는 형태는 벤더마다 다르다.
#   Arista EOS `show hostname`     -> "Hostname: core1" / "FQDN: core1.lab.local"
#   Cisco IOS  `show run | i host` -> "hostname core1"
#   Linux      `hostname`          -> "core1"
_HOSTNAME_PATTERNS = (
    re.compile(r"^\s*Hostname\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*hostname\s+(\S+)", re.IGNORECASE | re.MULTILINE),
)
_BARE_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")

# 대화형 쉘로 폴백했을 때 프롬프트에서 장비명을 뽑는 패턴
# (api/terminal_session_api.py의 _PROMPT_TAIL_RE와 같은 규칙).
_PROMPT_TAIL_RE = re.compile(r"[\r\n]\s*([\w][\w.\-]*)(?:\([^\r\n)]{0,40}\))?[#>]\s*$")

_LINUX_COMMANDS = ("hostname",)
_NETWORK_COMMANDS = ("show hostname", "show running-config | include ^hostname", "hostname")


def _hostname_commands(role):
    return _LINUX_COMMANDS if (role or "").lower() == "linux" else _NETWORK_COMMANDS


def extract_hostname(text):
    """CLI 출력 한 덩어리에서 장비명을 뽑는다. 못 찾으면 None."""
    if not text:
        return None
    text = strip_ansi(text)

    for pattern in _HOSTNAME_PATTERNS:
        match = pattern.search(text)
        if match:
            # "Hostname: core1"의 core1처럼 이미 짧은 이름. FQDN이 걸렸으면 앞부분만 쓴다.
            return match.group(1).strip().rstrip(".").split(".")[0] or None

    # 패턴에 안 걸리면 `hostname` 커맨드처럼 값만 한 줄로 오는 경우 — 그 줄 자체가 장비명.
    for line in text.splitlines():
        line = line.strip()
        if line and _BARE_HOSTNAME_RE.match(line):
            return line.split(".")[0]
    return None


def _hostname_via_exec(client, role):
    """exec 채널로 커맨드를 하나씩 시도 — 대부분의 장비/리눅스가 이걸 지원한다."""
    for command in _hostname_commands(role):
        try:
            _, stdout, _ = client.exec_command(command, timeout=COMMAND_TIMEOUT)
            output = stdout.read().decode("utf-8", errors="replace")
        except Exception:
            continue
        hostname = extract_hostname(output)
        if hostname:
            return hostname
    return None


def _hostname_via_shell(client):
    """exec 채널을 막아둔 장비용 폴백 — 대화형 쉘을 열고 프롬프트에서 장비명을 읽는다."""
    import time

    try:
        channel = client.invoke_shell(term="xterm", width=120, height=40)
    except Exception:
        return None
    try:
        channel.settimeout(COMMAND_TIMEOUT)
        channel.send("\n")
        buffer, deadline = "", time.time() + COMMAND_TIMEOUT
        while time.time() < deadline:
            if channel.recv_ready():
                buffer += channel.recv(4096).decode("utf-8", errors="replace")
                match = _PROMPT_TAIL_RE.search(strip_ansi(buffer))
                if match:
                    return match.group(1)
            else:
                time.sleep(0.05)
        return None
    except Exception:
        return None
    finally:
        try:
            channel.close()
        except Exception:
            pass


def _port_open(ip, port, timeout):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def probe_device(device, defaults, timeout=CONNECT_TIMEOUT):
    """
    장비 하나를 실제로 접속해 본다. 예외는 던지지 않고 항상 결과 dict를 돌려준다.

    반환: {reachable, authenticated, hostname, target, detail}
    """
    ip, port, username, password = resolve_credentials(device, defaults)
    name = device.get("name") or "(이름 없음)"
    result = {"name": name, "reachable": False, "authenticated": False,
              "hostname": None, "target": "", "detail": ""}

    if not ip:
        result["detail"] = "IP 미설정"
        return result

    result["target"] = f"{ip}:{port}"
    if not _port_open(ip, port, timeout):
        result["detail"] = f"{result['target']} 연결 실패 (포트 응답 없음)"
        return result
    result["reachable"] = True

    target = {
        "ip": ip, "port": port, "username": username, "password": password,
        "auth_method": device.get("auth_method", "password"),
        "key_path": device.get("key_path", ""),
        "key_content": device.get("key_content", ""),
        "key_passphrase": device.get("key_passphrase", ""),
    }

    from engine.ssh_client import connect

    client = None
    try:
        client = connect(target, timeout=timeout)
        result["authenticated"] = True
        hostname = _hostname_via_exec(client, device.get("role")) or _hostname_via_shell(client)
        result["hostname"] = hostname
        result["detail"] = (f"{result['target']} 연결됨 · {hostname}" if hostname
                            else f"{result['target']} 연결됨 (호스트명 확인 실패)")
    except Exception as exc:
        # 포트는 열려 있으므로 여기 오는 건 대부분 인증 실패 — 원인을 그대로 보여준다.
        result["detail"] = f"{result['target']} 인증 실패: {exc}"
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
    return result


def probe_devices(devices, defaults, timeout=CONNECT_TIMEOUT, max_workers=8):
    """여러 장비를 병렬로 확인. 입력 순서 그대로 결과 리스트를 돌려준다."""
    if not devices:
        return []

    from core.worker_pool import WorkerPool

    pool = WorkerPool(max_workers=max_workers, item_count=len(devices))
    by_index = {}
    indexed = list(enumerate(devices))
    for (index, _device), result in pool.run(indexed, lambda pair: probe_device(pair[1], defaults, timeout)):
        by_index[index] = result
    return [by_index[i] for i in range(len(devices))]
