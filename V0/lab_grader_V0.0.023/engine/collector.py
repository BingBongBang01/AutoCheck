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
import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from engine import device_inventory as di
from core.ansi_sanitizer import clean_terminal_log
from core.storage_service import storage_service, PathTarget

try:
    from netmiko import ConnectHandler
except ImportError:
    ConnectHandler = None  # 본인 노트북에서 pip install netmiko 필요

# 커맨드 카탈로그에 "enable"/"configure terminal"처럼 프롬프트 자체를 바꾸는 지시어가
# 등록되면(터미널 점검용 인터랙티브 쉘에서는 문제없지만) send_command()로 그대로 보내면
# netmiko가 이전 프롬프트 기준으로 응답 끝을 기다리다 "Pattern not detected"로 매번
# 실패·재시도하며 로그가 폭주한다. 이런 모드 전환 지시어는 데이터를 "수집"하는 커맨드가
# 아니라 세션 상태를 바꾸는 것뿐이므로 netmiko 전용 API로 처리하고 출력에도 남기지 않는다.
_ENABLE_ALIASES = {"enable"}
_CONFIG_MODE_ALIASES = {"configure terminal", "conf t", "config terminal", "config t"}
_EXIT_CONFIG_ALIASES = {"exit", "end"}


def pre_flight_check(target_ip, port=22, timeout=3):
    """VPN/내부망 연결 여부를 대표 장비 1대로 사전 확인. 실제 SSH 인증은 안 하고 포트만 확인."""
    if not target_ip:
        return False, "사전점검 대상 장비의 IP가 비어있음 — Device Inventory에서 확인 필요"
    try:
        with socket.create_connection((target_ip, port), timeout=timeout):
            return True, None
    except OSError as e:
        return False, f"{target_ip}:{port} 연결 실패 — VPN/내부망 연결 상태를 확인하세요 ({e})"


def collect_device(name, ip, port, username, password, commands, log_target, log_rel_path, on_update=None,
                    retry_count=3, retry_delay_sec=5, config_snapshot_target=None,
                    config_snapshot_rel_template=None, key_path=None, key_passphrase=None):
    """IP/계정은 이미 resolve된 값을 그대로 받는다 — 이 함수는 조회 로직을 모름(관심사 분리).

    파일 저장은 전부 StorageService를 통한다:
      log_target/log_rel_path         : 원본 로그 1건 저장 위치(raw/<device>.txt 상당)
      config_snapshot_target/template : running-config 스냅샷 저장 위치(exports/ 상당,
                                         template은 "...{name}_{date}.txt" 형태로 name/date를 채운다)
    """
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
                normalized = cmd.strip().lower()
                if normalized in _ENABLE_ALIASES:
                    emit("명령 실행 중", detail=cmd)
                    try:
                        if not conn.check_enable_mode():
                            conn.enable()
                    except Exception:
                        pass  # 이미 enable 모드이거나 이 장비/드라이버엔 없는 개념
                    continue
                if normalized in _CONFIG_MODE_ALIASES:
                    emit("명령 실행 중", detail=cmd)
                    try:
                        conn.config_mode()
                    except Exception:
                        pass
                    continue
                if normalized in _EXIT_CONFIG_ALIASES:
                    emit("명령 실행 중", detail=cmd)
                    try:
                        if conn.check_config_mode():
                            conn.exit_config_mode()
                    except Exception:
                        pass
                    continue
                emit("명령 실행 중", detail=cmd)
                raw_outputs[cmd] = conn.send_command(cmd)

            if config_snapshot_target and config_snapshot_rel_template:
                running_cfg = raw_outputs.get("show running-config")
                if running_cfg is None:
                    try:
                        running_cfg = conn.send_command("show running-config")
                    except Exception:
                        running_cfg = None
                if running_cfg:
                    today = datetime.date.today().isoformat()
                    rel = config_snapshot_rel_template.format(name=name, date=today)
                    storage_service.save_text(config_snapshot_target, rel, running_cfg, overwrite=True)

            conn.disconnect()
            emit("완료")

            log_text = "".join(f"--- {cmd} ---\n{clean_terminal_log(output)}\n\n"
                                for cmd, output in raw_outputs.items())
            storage_service.save_text(log_target, log_rel_path, log_text, overwrite=True)

            return name, raw_outputs, None
        except Exception as e:
            last_error = str(e)
            emit("실패", detail=last_error)

    return name, None, last_error


def collect_all(inventory_path, lab_name, commands, connection_path="connection.yaml", max_workers=None,
                 lab_meta_path=None, ip_allocation_path=None, customer_name=None, profile_name=None):
    """
    inventory_path: labs/{project}/device_inventory.yaml
    lab_meta_path/ip_allocation_path: 없어도 되지만, device_inventory.yaml이 아직 없는
    기존 프로젝트라면 이 둘을 넘기면 자동 마이그레이션됨(방어적 옵션).
    customer_name/profile_name: 지정되면 원본 로그·running-config 스냅샷을
    data/<고객사>/<프로파일>/99_log/ 아래에 저장한다(모든 로그 저장소를 고객사/프로파일
    기준 data/ 트리로 통일하기 위함). 지정되지 않으면(예: 프로파일 밖에서 직접 실행) 기존
    raw_logs/{lab_name}/, config_snapshots/{lab_name}/ 경로로 폴백한다.
    Device Inventory에서 enabled=True인 장비만 골라 병렬 수집.
    """
    inventory = di.load_inventory(inventory_path, lab_meta_path, ip_allocation_path)
    enabled_devices = di.get_enabled_devices(inventory)

    conn_cfg = {}
    if os.path.exists(connection_path):
        with open(connection_path, encoding="utf-8") as f:
            conn_cfg = yaml.safe_load(f) or {}

    if max_workers is None:
        # ThreadPoolExecutor 동시 세션 상한 — netmiko는 세션당 blocking I/O라 GIL에 안 걸리게
        # 스레드로 병렬화(요구사항 5 Fast path). 장비 수만큼 무한정 늘리지 않도록 50으로 캡.
        max_workers = min(conn_cfg.get("thread", {}).get("max_parallel_workers") or len(enabled_devices) or 1, 50)

    # --- 사전점검(pre-flight) ---
    # check_target_node는 사람이 붙인 논리적 이름(예: "Core1")인데, Inventory의 장비명은
    # IP 자동할당 시 생성된 "AUTO-101" 같은 이름이라 서로 네임스페이스가 달라 항상 못 찾는
    # 경우가 흔하다. 그럴 땐 사전점검 자체를 건너뛰지 말고, 첫 번째 활성 장비로 대체해
    # "적어도 망에 붙어있는지"는 여전히 확인한다(사전점검의 목적 유지).
    net_cfg = conn_cfg.get("network", {})
    if net_cfg.get("pre_flight_check") and enabled_devices:
        check_node_name = net_cfg.get("check_target_node")
        check_device = next((d for d in enabled_devices if d["name"] == check_node_name), None)
        if not check_device:
            check_device = enabled_devices[0]
            print(f"[사전점검] 대상 '{check_node_name}'을 Inventory에서 찾을 수 없어 "
                  f"'{check_device['name']}'로 대체해 확인합니다.")
        check_ip, check_port, _, _ = di.resolve_credentials(check_device, inventory["defaults"])
        ok, reason = pre_flight_check(check_ip, port=check_port, timeout=net_cfg.get("check_timeout_sec", 3))
        if not ok:
            print(f"[중단] 사전점검 실패 — {reason}")
            return None, None, None

    if customer_name and profile_name:
        # ProfileManager(-> StorageService)가 runs/<타임스탬프>/ 아래에
        # raw/masked/parsed/analysis/reports/exports를 전부 만들어준다 — 이번 실행의 산출물은
        # 전부 이 run 하나에만 쌓여 다른 실행과 절대 섞이지 않는다.
        from engine.profile_manager import profile_manager
        run = profile_manager.get_run_handle(customer_name, profile_name)
        session_ts = run.run_id
        log_target, log_rel_prefix = run, "raw"
        config_snapshot_target = run
        config_snapshot_rel_template = "exports/config_snapshots/{name}_{date}.txt"
        session_dir = str(run.raw_dir)
    else:
        session_ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
        legacy_dir = Path("raw_logs") / lab_name / session_ts
        log_target, log_rel_prefix = PathTarget(path=legacy_dir), ""
        config_snapshot_target = PathTarget(path=Path("config_snapshots") / lab_name)
        config_snapshot_rel_template = "{name}_{date}.txt"
        session_dir = str(legacy_dir)
    legacy_dir_obj = Path(session_dir)
    legacy_dir_obj.mkdir(parents=True, exist_ok=True)  # 장비가 0대여도 조회용 폴더는 항상 존재해야 함

    ssh_cfg = conn_cfg.get("ssh", {})
    retry_count = ssh_cfg.get("retry_count", 3)
    retry_delay_sec = ssh_cfg.get("retry_delay_sec", 5)

    results, errors = {}, {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for device in enabled_devices:
            ip, port, username, password = di.resolve_credentials(device, inventory["defaults"])
            log_rel_path = f"{log_rel_prefix}/{device['name']}.txt" if log_rel_prefix else f"{device['name']}.txt"
            fut = executor.submit(
                collect_device, device["name"], ip, port, username, password, commands,
                log_target, log_rel_path,
                retry_count=retry_count, retry_delay_sec=retry_delay_sec,
                config_snapshot_target=config_snapshot_target,
                config_snapshot_rel_template=config_snapshot_rel_template,
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

    # 이벤트 타임라인 추출은 grading 흐름과 무관한 부가 산출물이라, 실패해도
    # 수집 자체(results/errors/session_dir)는 절대 막지 않는다 — 그래서 광범위 except.
    try:
        from engine.session_timeline import write_session_timeline
        timeline = write_session_timeline(session_dir)
        manifest["events"] = {"count": timeline["event_count"], "group_count": len(timeline["groups"])}
    except Exception as e:
        manifest["events"] = {"error": str(e)}

    manifest_rel = f"{log_rel_prefix}/_session_manifest.json" if log_rel_prefix else "_session_manifest.json"
    storage_service.save_json(log_target, manifest_rel, manifest)

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
