"""
LAB1 7대 장비에 병렬 SSH 접속해 커맨드를 실행하고 원본 로그를 저장한다.

실행 위치 주의: 이 코드는 본인 노트북(EVE-NG 서버에 접근 가능한 환경)에서 실행해야 한다.
회사 내부망(EVE-NG) IP에 접속해야 하므로, 외부 샌드박스에서는 실행할 수 없다.

사전 준비:
  pip install netmiko pyyaml --break-system-packages
"""
import os
import socket
import yaml
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from netmiko import ConnectHandler
except ImportError:
    ConnectHandler = None  # 본인 노트북에서 pip install netmiko 필요


def pre_flight_check(target_ip, port=22, timeout=3):
    """VPN/내부망 연결 여부를 대표 장비 1대로 사전 확인. 실제 SSH 인증은 안 하고 포트만 확인."""
    if not target_ip:
        return False, "check_target_node의 IP가 ip_allocation.yaml에 비어있음"
    try:
        with socket.create_connection((target_ip, port), timeout=timeout):
            return True, None
    except OSError as e:
        return False, f"{target_ip}:{port} 연결 실패 — VPN/내부망 연결 상태를 확인하세요 ({e})"


def load_credentials(node_name, ip_allocation):
    for entry in ip_allocation["allocations"]:
        if entry["node_name"] == node_name:
            username = entry.get("username") or ip_allocation["default_credentials"]["username"]
            password = entry.get("password") or ip_allocation["default_credentials"]["password"]
            ip = entry["ip"]
            return ip, username, password
    raise ValueError(f"{node_name}에 대한 IP 할당 정보가 ip_allocation.yaml에 없음")


def collect_device(node_name, commands, ip_allocation, session_dir, on_update=None):
    def emit(status, detail=""):
        if on_update:
            on_update(node_name, status, detail)
        else:
            print(f"[{node_name}] {status} {detail}")

    emit("접속 중")
    try:
        ip, username, password = load_credentials(node_name, ip_allocation)
        if not ip:
            raise ValueError(f"{node_name}의 IP가 비어있음 — ip_allocation.yaml 확인 필요")

        conn = ConnectHandler(
            device_type="arista_eos",
            host=ip,
            username=username,
            password=password,
            timeout=20,
        )
        raw_outputs = {}
        for cmd in commands:
            emit("명령 실행 중", detail=cmd)
            raw_outputs[cmd] = conn.send_command(cmd)
        conn.disconnect()
        emit("완료")

        log_path = os.path.join(session_dir, f"{node_name}.txt")
        with open(log_path, "w", encoding="utf-8") as f:
            for cmd, output in raw_outputs.items():
                f.write(f"--- {cmd} ---\n{output}\n\n")

        return node_name, raw_outputs, None
    except Exception as e:
        emit("실패", detail=str(e))
        return node_name, None, str(e)


def collect_all(lab_meta_path, ip_allocation_path, commands, connection_path="connection.yaml", max_workers=None):
    with open(lab_meta_path) as f:
        lab_meta = yaml.safe_load(f)
    with open(ip_allocation_path) as f:
        ip_allocation = yaml.safe_load(f)

    conn_cfg = {}
    if os.path.exists(connection_path):
        with open(connection_path) as f:
            conn_cfg = yaml.safe_load(f) or {}

    if max_workers is None:
        max_workers = lab_meta.get("max_parallel_workers", 5)

    # --- 사전점검(pre-flight): VPN/내부망 연결 여부 대표 장비 1대로 확인 ---
    net_cfg = conn_cfg.get("network", {})
    if net_cfg.get("pre_flight_check"):
        check_node = net_cfg.get("check_target_node")
        try:
            check_ip, _, _ = load_credentials(check_node, ip_allocation) if check_node else (None, None, None)
        except ValueError as e:
            print(f"[경고] 사전점검 대상 조회 실패({e}) — 사전점검 건너뛰고 계속 진행")
            check_ip = None

        if check_ip:
            ok, reason = pre_flight_check(
                check_ip,
                port=net_cfg.get("check_port", 22),
                timeout=net_cfg.get("check_timeout_sec", 3),
            )
            if not ok:
                print(f"[중단] 사전점검 실패 — {reason}")
                return None, None, None

    session_ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    lab_name = lab_meta["lab_name"]
    session_dir = os.path.join("raw_logs", lab_name, session_ts)
    os.makedirs(session_dir, exist_ok=True)

    results, errors = {}, {}
    node_names = [d["name"] for d in lab_meta["devices"]]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(collect_device, name, commands, ip_allocation, session_dir): name
            for name in node_names
        }
        for future in as_completed(futures):
            name, output, error = future.result()
            if error:
                errors[name] = error
            else:
                results[name] = output

    manifest = {
        "lab": lab_name, "session": session_ts,
        "success": list(results.keys()), "failed": errors,
    }
    with open(os.path.join(session_dir, "_session_manifest.json"), "w", encoding="utf-8") as f:
        import json
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return results, errors, session_dir


if __name__ == "__main__":
    if ConnectHandler is None:
        print("netmiko가 설치되어 있지 않습니다. 본인 노트북에서 실행하세요:")
        print("  pip install netmiko pyyaml --break-system-packages")
    else:
        import yaml as _yaml
        with open("labs/lab1_campus/stages.yaml") as f:
            _stages_cfg = _yaml.safe_load(f)["stages"]
        commands = []
        for _stage in _stages_cfg:
            for _cmd in _stage.get("commands", []):
                if _cmd not in commands:
                    commands.append(_cmd)

        results, errors, session_dir = collect_all(
            "labs/lab1_campus/lab_meta.yaml",
            "labs/lab1_campus/ip_allocation.yaml",
            commands,
        )
        print(f"\n성공: {list(results.keys()) if results else None}")
        print(f"실패: {errors}")
        print(f"로그 저장 위치: {session_dir}")
