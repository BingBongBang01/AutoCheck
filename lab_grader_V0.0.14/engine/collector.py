"""
Device Inventory에 등록된(Enable=True) 장비에 병렬 SSH 접속해 커맨드를 실행하고 원본 로그를 저장한다.

구조 변경(IP 관리 위치 이동): 이 모듈은 이제 IP/계정을 직접 읽지 않는다.
device_inventory.py가 제공하는 get_enabled_devices() + resolve_credentials()로만
접속 대상을 얻는다 — "IP가 코드 여기저기 흩어짐" 문제 해결.

실행 위치 주의: 이 코드는 본인 노트북(EVE-NG 서버에 접근 가능한 환경)에서 실행해야 한다.
사전 준비: pip install netmiko pyyaml --break-system-packages
"""
import os
import socket
import time
import yaml
import json
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from engine import device_inventory as di

try:
    from netmiko import ConnectHandler
except ImportError:
    ConnectHandler = None  # 본인 노트북에서 pip install netmiko 필요


def pre_flight_check(target_ip, port=22, timeout=3):
    """VPN/내부망 연결 여부를 대표 장비 1대로 사전 확인. 실제 SSH 인증은 안 하고 포트만 확인."""
    if not target_ip:
        return False, "사전점검 대상 장비의 IP가 비어있음 — Device Inventory에서 확인 필요"
    try:
        with socket.create_connection((target_ip, port), timeout=timeout):
            return True, None
    except OSError as e:
        return False, f"{target_ip}:{port} 연결 실패 — VPN/내부망 연결 상태를 확인하세요 ({e})"


def collect_device(name, ip, port, username, password, commands, session_dir, on_update=None,
                    retry_count=3, retry_delay_sec=5, config_snapshot_dir=None, key_path=None, key_passphrase=None):
    """IP/계정은 이미 resolve된 값을 그대로 받는다 — 이 함수는 조회 로직을 모름(관심사 분리)."""
    def emit(status, detail=""):
        if on_update:
            on_update(name, status, detail)
        else:
            print(f"[{name}] {status} {detail}")

    if not ip:
        emit("실패", detail="IP 없음 — Device Inventory에서 management_ip 확인 필요")
        return name, None, "management_ip가 비어있음"

    last_error = None
    for attempt in range(1, retry_count + 1):
        if attempt > 1:
            emit("재시도", detail=f"{attempt}/{retry_count}회차")
            time.sleep(retry_delay_sec)
        else:
            emit("접속 중")
        try:
            connection = dict(
                device_type="arista_eos",
                host=ip, port=port, username=username, password=password,
                timeout=20,
            )
            if key_path:
                connection.update(use_keys=True, key_file=key_path, password=None, passphrase=key_passphrase or None)
            conn = ConnectHandler(**connection)
            raw_outputs = {}
            for cmd in commands:
                emit("명령 실행 중", detail=cmd)
                raw_outputs[cmd] = conn.send_command(cmd)

            if config_snapshot_dir:
                running_cfg = raw_outputs.get("show running-config")
                if running_cfg is None:
                    try:
                        running_cfg = conn.send_command("show running-config")
                    except Exception:
                        running_cfg = None
                if running_cfg:
                    os.makedirs(config_snapshot_dir, exist_ok=True)
                    today = datetime.date.today().isoformat()
                    snap_path = os.path.join(config_snapshot_dir, f"{name}_{today}.txt")
                    with open(snap_path, "w", encoding="utf-8") as f:
                        f.write(running_cfg)

            conn.disconnect()
            emit("완료")

            log_path = os.path.join(session_dir, f"{name}.txt")
            with open(log_path, "w", encoding="utf-8") as f:
                for cmd, output in raw_outputs.items():
                    f.write(f"--- {cmd} ---\n{output}\n\n")

            return name, raw_outputs, None
        except Exception as e:
            last_error = str(e)
            emit("실패", detail=last_error)

    return name, None, last_error


def collect_all(inventory_path, lab_name, commands, connection_path="connection.yaml", max_workers=None,
                 lab_meta_path=None, ip_allocation_path=None):
    """
    inventory_path: labs/{project}/device_inventory.yaml
    lab_meta_path/ip_allocation_path: 없어도 되지만, device_inventory.yaml이 아직 없는
    기존 프로젝트라면 이 둘을 넘기면 자동 마이그레이션됨(방어적 옵션).
    Device Inventory에서 enabled=True인 장비만 골라 병렬 수집.
    """
    inventory = di.load_inventory(inventory_path, lab_meta_path, ip_allocation_path)
    enabled_devices = di.get_enabled_devices(inventory)

    conn_cfg = {}
    if os.path.exists(connection_path):
        with open(connection_path, encoding="utf-8") as f:
            conn_cfg = yaml.safe_load(f) or {}

    if max_workers is None:
        max_workers = conn_cfg.get("thread", {}).get("max_parallel_workers") or len(enabled_devices) or 1

    # --- 사전점검(pre-flight) ---
    net_cfg = conn_cfg.get("network", {})
    if net_cfg.get("pre_flight_check"):
        check_node_name = net_cfg.get("check_target_node")
        check_device = next((d for d in enabled_devices if d["name"] == check_node_name), None)
        if check_device:
            check_ip, check_port, _, _ = di.resolve_credentials(check_device, inventory["defaults"])
            ok, reason = pre_flight_check(check_ip, port=check_port, timeout=net_cfg.get("check_timeout_sec", 3))
            if not ok:
                print(f"[중단] 사전점검 실패 — {reason}")
                return None, None, None
        else:
            print(f"[경고] 사전점검 대상 '{check_node_name}'을 Inventory에서 찾을 수 없음 — 사전점검 건너뛰고 계속 진행")

    session_ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    session_dir = os.path.join("raw_logs", lab_name, session_ts)
    os.makedirs(session_dir, exist_ok=True)

    ssh_cfg = conn_cfg.get("ssh", {})
    retry_count = ssh_cfg.get("retry_count", 3)
    retry_delay_sec = ssh_cfg.get("retry_delay_sec", 5)
    config_snapshot_dir = os.path.join("config_snapshots", lab_name)

    results, errors = {}, {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for device in enabled_devices:
            ip, port, username, password = di.resolve_credentials(device, inventory["defaults"])
            fut = executor.submit(
                collect_device, device["name"], ip, port, username, password, commands, session_dir,
                retry_count=retry_count, retry_delay_sec=retry_delay_sec,
                config_snapshot_dir=config_snapshot_dir,
                key_path=device.get("key_path") if device.get("auth_method") == "public_key" else None,
                key_passphrase=device.get("key_passphrase") if device.get("auth_method") == "public_key" else None,
            )
            futures[fut] = device["name"]

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
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return results, errors, session_dir


if __name__ == "__main__":
    if ConnectHandler is None:
        print("netmiko가 설치되어 있지 않습니다. 본인 노트북에서 실행하세요:")
        print("  pip install netmiko pyyaml --break-system-packages")
    else:
        with open("labs/lab1_campus/stages.yaml", encoding="utf-8") as f:
            _stages_cfg = yaml.safe_load(f)["stages"]
        commands = []
        for _stage in _stages_cfg:
            for _cmd in _stage.get("commands", []):
                if _cmd not in commands:
                    commands.append(_cmd)

        results, errors, session_dir = collect_all(
            "labs/lab1_campus/device_inventory.yaml", "lab1_campus", commands,
        )
        print(f"\n성공: {list(results.keys()) if results else None}")
        print(f"실패: {errors}")
        print(f"로그 저장 위치: {session_dir}")
