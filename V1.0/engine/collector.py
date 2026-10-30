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

from engine import device_inventory as di
from engine import host_keys
from core import vendors
from core.ansi_sanitizer import clean_terminal_log
from core.command_policy import blocked_reason
from core.log_naming import build_inspection_log_name
from core.paths import safe_filename
from core.storage_service import storage_service, PathTarget
from core.worker_pool import WorkerPool
from core import progress_engine
from core.structured_log import write_event as slog_event
from core.event_bus import bus as event_bus

try:
    from netmiko import ConnectHandler
except ImportError:
    ConnectHandler = None  # 본인 노트북에서 pip install netmiko 필요

# 커맨드 카탈로그의 "enable"은 프롬프트 자체를 바꾸는 지시어다. send_command()로 그대로 보내면
# netmiko가 이전 프롬프트 기준으로 응답 끝을 기다리다 "Pattern not detected"로 매번
# 실패·재시도하며 로그가 폭주한다. 데이터를 "수집"하는 커맨드가 아니라 세션 상태를 바꾸는
# 것뿐이므로 netmiko 전용 API로 처리하고 출력에도 남기지 않는다.
# 설정 모드 진입(configure …)·exit/end 는 예전에는 여기서 netmiko 로 처리했지만, 점검은 장비를
# 읽기만 하므로 이제 읽기 전용 정책(core/command_policy.py)이 아예 보내지 않는다.
_ENABLE_ALIASES = {"enable"}

# 사전점검에서 확인해 볼 장비 수(대표 장비 + 대체 장비). 전부 실패하면 망 연결 문제로 보고 멈춘다.
_PRE_FLIGHT_CANDIDATES = 3


class EnableFailed(Exception):
    """enable(특권 모드)로 들어가지 못함 — 비밀번호 문제라 다시 접속해도 같다(재시도마다 장비에 인증 실패만 쌓인다)."""


_NO_ENABLE_SECRET = "장비 목록에서 enable 비밀번호(또는 기본 enable 비밀번호)를 입력하세요"


def _enter_enable(conn, secret, platform):
    """카탈로그의 'enable' — 이미 특권 모드('#')면 그대로, 아니면 enable 비밀번호(netmiko secret)로 들어간다.

    예전엔 실패를 삼키고 사용자 모드('>')에서 나머지 명령을 보냈다 — Cisco 처럼 로그인 뒤 '>' 에서 시작하는 장비는
    권한이 필요한 명령마다 오류만 남은 로그가 점검 결과로 저장됐다(IMPROVEMENT_PLAN 4-5). 비밀번호가 없으면 비밀번호
    없이 enable 이 되는 플랫폼(EOS)만 시도한다 — Cisco 에 빈 비밀번호를 보내면 장비에 인증 실패만 남는다."""
    if conn.check_enable_mode():
        return
    if not secret and not vendors.enable_without_secret(platform):
        raise EnableFailed(f"enable 비밀번호가 없어 특권 모드로 들어가지 않았습니다 — {_NO_ENABLE_SECRET}")
    try:
        conn.enable()
    except ValueError as exc:    # netmiko 가 enable 실패를 알리는 방식 — 세션 끊김 같은 다른 예외는 재시도한다
        hint = "enable 비밀번호가 맞는지 확인하세요" if secret else _NO_ENABLE_SECRET
        raise EnableFailed(f"enable(특권 모드)로 들어가지 못했습니다 — {hint}") from exc


def _server_key(conn):
    """netmiko 연결이 받은 서버 호스트키(paramiko PKey) — 알 수 없으면 None."""
    try:
        return conn.remote_conn_pre.get_transport().get_remote_server_key()
    except Exception:
        return None


def _plan_commands(commands):
    """카탈로그 명령 -> (enable 필요 여부, 장비로 보낼 명령, [(보내지 않을 명령, 이유)])."""
    wants_enable, send, skipped = False, [], []
    for cmd in commands:
        if cmd.strip().lower() in _ENABLE_ALIASES:
            wants_enable = True
            continue
        reason = blocked_reason(cmd)
        if reason:
            skipped.append((cmd, reason))
        else:
            send.append(cmd)
    return wants_enable, send, skipped


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
                    config_snapshot_rel_template=None, key_path=None, key_passphrase=None, run=None,
                    job_id=None, progress=None, key_content=None, connect_timeout_sec=20,
                    platform=vendors.DEFAULT_PLATFORM, secret="", host_key_scope=host_keys.GLOBAL_SCOPE):
    """IP/계정은 이미 resolve된 값을 그대로 받는다 — 이 함수는 조회 로직을 모름(관심사 분리).

    계정이 비어 있으면(사용자명, 또는 비밀번호와 키 둘 다) 접속하지 않고 실패로 돌려준다 —
    예전처럼 admin/admin 으로 대신 접속하면 장비 N대 × 재시도로 AAA 계정이 잠길 수 있다.
    key_content(화면에 붙여넣은 개인키)는 파일로 쓰지 않고 메모리에서 읽어 netmiko 에 넘긴다.
    connect_timeout_sec 는 connection.yaml 의 ssh.connect_timeout_sec 다.
    platform 은 core/vendors 의 플랫폼 id(netmiko device_type 을 고른다) — None(모르는 벤더)이면 접속하지 않는다.
    secret 은 enable 비밀번호 — enable 에 실패하면 재시도하지 않고 실패로 돌려준다.
    host_key_scope 는 SSH 호스트키를 묶는 범위(engine/host_keys) — 기억한 키가 있으면 netmiko 가 그 키로 확인하게 하고
    (다르면 비밀번호를 보내기 전에 끊긴다), 처음 보는 장비는 접속한 뒤 받은 키를 기억한다. 키가 바뀌었으면 재시도하지 않는다.

    파일 저장:
      run이 있으면(프로파일 기반 실행) 원본 로그는 LogManager.save_raw_log()로 저장한다
      (raw/<device>.txt + 체크섬 sidecar까지 LogManager가 책임짐 — 여기서 직접 쓰지 않음).
      run이 없으면(레거시 폴백 경로) log_target/log_rel_path로 StorageService에 직접 저장한다.
      config_snapshot_target/template : running-config 스냅샷 저장 위치(exports/ 상당,
                                         template은 "...{name}_{date}.txt" 형태로 name/date를 채운다)

    job_id/progress: Progress Engine(core/progress_engine.py) 연동용 — job_id는 이 실행 전체를
    묶는 상관관계 키(구조화 로그의 job 필드와 동일), progress는 ProgressEngine 인스턴스
    (없으면 core.progress_engine.NULL_PROGRESS로 무동작 처리, 단독 호출/테스트에서도 안전).
    """
    progress = progress or progress_engine.NULL_PROGRESS

    def emit(status, detail=""):
        if on_update:
            on_update(name, status, detail)
        else:
            print(f"[{name}] {status} {detail}")

    if not ip:
        emit("실패", detail="IP 없음 — Device Inventory에서 management_ip 확인 필요")
        slog_event("collector", "device_failed", job_id=job_id, device=name, status="실패",
                   message="management_ip가 비어있음")
        return name, None, "management_ip가 비어있음"

    device_type = vendors.netmiko_type(platform)
    if device_type is None:     # 모르는 벤더에 Arista 드라이버로 로그인하지 않는다
        emit("실패", detail=vendors.UNSUPPORTED_TEXT)
        slog_event("collector", "device_failed", job_id=job_id, device=name, status="실패",
                   message=vendors.UNSUPPORTED_TEXT)
        return name, None, vendors.UNSUPPORTED_TEXT

    problem = di.credential_problem(username, password, uses_key=bool(key_path or key_content))
    pkey = None
    if not problem and key_content and not key_path:
        try:
            from engine.ssh_client import load_private_key_text
            pkey = load_private_key_text(key_content, key_passphrase or None)
        except Exception as exc:   # 키가 잘못됐으면 재시도해도 같다 — 바로 알린다
            problem = str(exc)
    if problem:
        emit("실패", detail=problem)
        slog_event("collector", "device_failed", job_id=job_id, device=name, status="실패", message=problem)
        return name, None, problem

    progress.device_start(name)
    wants_enable, collect_commands, skipped = _plan_commands(commands)
    for cmd, reason in skipped:
        emit("명령 건너뜀", detail=f"{cmd} — {reason}")
    last_error = None
    for attempt in range(1, retry_count + 1):
        if attempt > 1:
            emit("재시도", detail=f"{attempt}/{retry_count}회차")
            progress.retry(name, attempt, retry_count)
            slog_event("collector", "retry", job_id=job_id, device=name, retry=attempt,
                       message=f"{attempt}/{retry_count}회차")
            time.sleep(retry_delay_sec)
        else:
            emit("접속 중")
        conn = None
        known_hosts = None
        try:
            connection = dict(
                device_type=device_type,
                host=ip, port=port, username=username, password=password, secret=secret or "",
                timeout=connect_timeout_sec, conn_timeout=connect_timeout_sec,
            )
            if key_path:
                connection.update(use_keys=True, key_file=key_path, password=None, passphrase=key_passphrase or None)
            elif pkey is not None:
                connection.update(use_keys=True, pkey=pkey, password=None)
            known_hosts = host_keys.known_hosts_file(host_key_scope, ip, port)
            if known_hosts:
                connection.update(alt_host_keys=True, alt_key_file=known_hosts)
            conn = ConnectHandler(**connection)
            server_key = _server_key(conn)
            if server_key is not None:
                host_keys.remember(host_key_scope, ip, port, server_key, device=name)
            raw_outputs = {}
            if wants_enable:
                emit("명령 실행 중", detail="enable")
                _enter_enable(conn, secret, platform)

            if collect_commands:
                bulk_text = "\n!\n".join(collect_commands) + "\n!"
                emit("통짜 명령 실행 중", detail=f"{len(collect_commands)}개 명령어")
                cmd_started = time.time()
                
                output = conn.send_command_timing(bulk_text, read_timeout=120, strip_command=False, strip_prompt=False)
                
                duration_ms = round((time.time() - cmd_started) * 1000, 1)
                raw_outputs["bulk_execution"] = output
                
                for cmd in collect_commands:
                    progress.command_done(name, cmd, duration_ms=duration_ms)
                    slog_event("collector", "command_done", job_id=job_id, device=name, command=cmd,
                               prompt="bulk", duration_ms=duration_ms)

            if config_snapshot_target and config_snapshot_rel_template:
                running_cfg = raw_outputs.get("show running-config")
                if running_cfg is None:
                    try:
                        running_cfg = conn.send_command("show running-config")
                    except Exception:
                        running_cfg = None
                if running_cfg:
                    today = datetime.date.today().isoformat()
                    rel = config_snapshot_rel_template.format(name=safe_filename(name), date=today)
                    storage_service.save_text(config_snapshot_target, rel, running_cfg, overwrite=True)

            emit("완료")
            progress.device_done(name, True)
            slog_event("collector", "device_done", job_id=job_id, device=name, status="완료")

            log_text = "".join(clean_terminal_log(output) for output in raw_outputs.values())
            if run is not None:
                from engine.log_manager import log_manager
                # 세션 점검과 같은 파일명({날짜}_{시각}_raw_{장비}.txt) — 로그 분석·보고서·실시간 감시 기준이
                # 점검 로그를 이 이름으로 알아본다(core/log_naming.py). 예전엔 raw/<장비>.txt 라 기준에서 빠졌다.
                log_manager.save_raw_log(run, build_inspection_log_name(name), log_text, overwrite=True)
            else:
                storage_service.save_text(log_target, log_rel_path, log_text, overwrite=True)

            return name, raw_outputs, None
        except Exception as e:
            # enable 실패와 호스트키 바뀜은 다시 접속해도 같다 — 재시도하지 않는다(장비에 인증 실패만 쌓이고,
            # 키가 바뀐 상대에게는 다시 접속하지 않는다). netmiko 는 BadHostKeyException 을 감싸서 올린다.
            changed = host_keys.from_exception(e, host_key_scope, ip, port, name)
            last_error = str(changed or e)
            emit("실패", detail=last_error)
            slog_event("collector", "attempt_failed", job_id=job_id, device=name, retry=attempt,
                       status="실패", message=last_error)
            if changed is not None or isinstance(e, EnableFailed):
                break
        finally:
            if known_hosts:
                try:
                    os.unlink(known_hosts)
                except OSError:
                    pass
            # 명령 실행·저장 중 예외가 나도 세션을 닫는다 — 예전엔 성공 경로에서만 닫아서 실패한
            # 시도마다 vty 세션이 남았고, 재시도 3회가 장비의 vty 를 고갈시킬 수 있었다.
            if conn is not None:
                try:
                    conn.disconnect()
                except Exception:
                    pass

    progress.device_done(name, False)
    slog_event("collector", "device_done", job_id=job_id, device=name, status="실패", message=last_error)
    return name, None, last_error


def collect_all(inventory_path, lab_name, commands, connection_path=None, max_workers=None,
                 lab_meta_path=None, ip_allocation_path=None, customer_name=None, profile_name=None,
                 execution_mode="ssh_collect", on_progress=None, commands_by_platform=None):
    """
    inventory_path: labs/{project}/device_inventory.yaml
    lab_meta_path/ip_allocation_path: 없어도 되지만, device_inventory.yaml이 아직 없는
    기존 프로젝트라면 이 둘을 넘기면 자동 마이그레이션됨(방어적 옵션).
    customer_name/profile_name: 지정되면 원본 로그·running-config 스냅샷을
    data/<고객사>/<프로파일>/runs/<run_id>/ 아래에 저장한다(모든 로그 저장소를 고객사/프로파일
    기준 data/ 트리로 통일하기 위함). 지정되지 않으면(예: 프로파일 밖에서 직접 실행) 기존
    raw_logs/{lab_name}/, config_snapshots/{lab_name}/ 경로로 폴백한다.
    execution_mode: run 메타에 남길 실행 방식(무인 정기점검은 'scheduled').
    on_progress(done, total, device, error): 장비 하나가 끝날 때마다 부른다(무인 점검의 JobRunner 진행 표시).
    commands_by_platform: {플랫폼 id: [명령]} — 있으면 장비마다 그 플랫폼의 명령을 보낸다(engine/vendor_commands —
    Cisco 장비에는 같은 체크ID 의 Cisco 명령). 없거나 그 플랫폼이 빠져 있으면 commands 를 그대로 보낸다.
    Device Inventory에서 enabled=True인 장비만 골라 병렬 수집.
    """
    inventory = di.load_inventory(inventory_path, lab_meta_path, ip_allocation_path)
    enabled_devices = di.get_enabled_devices(inventory)
    device_platforms = {d["name"]: vendors.platform_of(d.get("vendor")) for d in enabled_devices}
    host_key_scope = host_keys.scope_for_project(lab_name)    # 고객사 단위로 기억한 SSH 호스트키(IMPROVEMENT_PLAN 4-6)
    known_platforms = sorted({p for p in device_platforms.values() if p})

    # connection.yaml은 앱 전역 설정 — 기본값을 CWD 상대경로로 두면 실행 위치에 따라
    # SSH 타임아웃·재시도·병렬 워커 수가 조용히 기본값으로 되돌아간다(core/app_settings.py 참고).
    if connection_path is None:
        from core.app_settings import connection_path as default_connection_path
        connection_path = default_connection_path()

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
    # 대표 장비 하나가 꺼져 있다는 것만으로 전체 수집을 멈추지 않는다 — 실패하면 다른 활성 장비로
    # 다시 확인하고(최대 _PRE_FLIGHT_CANDIDATES 대), 모두 응답이 없을 때만 '망에 붙어 있지 않다'로 보고 멈춘다.
    net_cfg = conn_cfg.get("network", {})
    if net_cfg.get("pre_flight_check") and enabled_devices:
        check_node_name = net_cfg.get("check_target_node")
        candidates = sorted(enabled_devices, key=lambda d: d["name"] != check_node_name)[:_PRE_FLIGHT_CANDIDATES]
        if candidates[0]["name"] != check_node_name:
            print(f"[사전점검] 대상 '{check_node_name}'을 Inventory에서 찾을 수 없어 "
                  f"'{candidates[0]['name']}'로 대체해 확인합니다.")
        failures = []
        for check_device in candidates:
            check_ip, check_port, _, _ = di.resolve_credentials(check_device, inventory["defaults"])
            ok, reason = pre_flight_check(check_ip, port=check_port, timeout=net_cfg.get("check_timeout_sec", 3))
            if ok:
                break
            failures.append(f"{check_device['name']}: {reason}")
            print(f"[사전점검] {check_device['name']} 응답 없음 — 다른 장비로 다시 확인합니다.")
        else:
            print(f"[중단] 사전점검 실패 — 확인한 {len(failures)}대 모두 응답 없음 ({' / '.join(failures)})")
            return None, None, None

    run = None
    if customer_name and profile_name:
        # RunManager가 runs/<Run ID>/ 아래에 raw/masked/parsed/analysis/reports/exports를
        # 전부 만들고 session.json/metadata.json까지 채운다 — 이번 실행의 산출물과 상태는
        # 전부 이 run 하나에만 쌓여 다른 실행과 절대 섞이지 않는다. 폴더는 여기서 만들지 않는다.
        from engine.run_manager import run_manager
        run = run_manager.create_run(
            customer_name, profile_name,
            device_count=len(enabled_devices), command_count=len(commands),
            platform=known_platforms[0] if len(known_platforms) == 1 else ("mixed" if known_platforms else None),
            execution_mode=execution_mode,
        )
        run_manager.start_run(run)
        session_ts = run.run_id
        log_target, log_rel_prefix = run, "raw"
        config_snapshot_target = run
        config_snapshot_rel_template = "exports/config_snapshots/{name}_{date}.txt"
        session_dir = str(run.raw_dir)
    else:
        # 고객사/프로파일 없이 실행된 경우의 레거시 폴백. 경로는 AppPaths 기준 —
        # CWD 상대로 두면 앱을 다른 폴더에서 실행했을 때 수집한 원본 로그가 엉뚱한 곳에
        # 쌓이고, 점검 로그·보고서 탭은 앱 루트만 보므로 그 로그를 영영 못 찾는다.
        from core.paths import AppPaths
        session_ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
        legacy_dir = AppPaths.raw_logs_root() / lab_name / session_ts
        log_target, log_rel_prefix = PathTarget(path=legacy_dir), ""
        config_snapshot_target = PathTarget(path=AppPaths.app_root() / "config_snapshots" / lab_name)
        config_snapshot_rel_template = "{name}_{date}.txt"
        session_dir = str(legacy_dir)
    legacy_dir_obj = Path(session_dir)
    legacy_dir_obj.mkdir(parents=True, exist_ok=True)  # 장비가 0대여도 조회용 폴더는 항상 존재해야 함

    ssh_cfg = conn_cfg.get("ssh", {})
    retry_count = ssh_cfg.get("retry_count", 3)
    retry_delay_sec = ssh_cfg.get("retry_delay_sec", 5)
    connect_timeout_sec = ssh_cfg.get("connect_timeout_sec", 20)   # 예전엔 timeout=20 고정이라 설정이 먹지 않았다

    results, errors = {}, {}

    # Worker Pool: 장비 단위 병렬 수집(설정 가능한 max_workers), 장비 하나 안의 커맨드는
    # collect_device 내부에서 순차 실행(SSH 세션 stateful이라 병렬화 안 함).
    job_id = session_ts
    device_names = [d["name"] for d in enabled_devices]
    progress = progress_engine.get_or_create(job_id, device_names, commands_per_device=len(commands))
    pool = WorkerPool(max_workers=max_workers, item_count=len(enabled_devices))
    event_bus.publish("collector.started", {"job_id": job_id, "device_count": len(enabled_devices)})

    jobs = {}
    for device in enabled_devices:
        ip, port, username, password = di.resolve_credentials(device, inventory["defaults"])
        log_file = f"{safe_filename(device['name'])}.txt"   # 장비명이 파일명 — 경로 구분자·':' 를 다듬는다
        log_rel_path = f"{log_rel_prefix}/{log_file}" if log_rel_prefix else log_file
        device_commands = (commands_by_platform or {}).get(device_platforms[device["name"]], commands)
        jobs[device["name"]] = (collect_device, (device["name"], ip, port, username, password, device_commands,
                                                  log_target, log_rel_path), dict(
            retry_count=retry_count, retry_delay_sec=retry_delay_sec,
            config_snapshot_target=config_snapshot_target,
            config_snapshot_rel_template=config_snapshot_rel_template,
            key_path=device.get("key_path") if device.get("auth_method") == "public_key" else None,
            key_passphrase=device.get("key_passphrase") if device.get("auth_method") == "public_key" else None,
            key_content=device.get("key_content") if device.get("auth_method") == "public_key" else None,
            connect_timeout_sec=connect_timeout_sec,
            run=run, job_id=job_id, progress=progress,
            platform=device_platforms[device["name"]],
            secret=di.resolve_enable_secret(device, inventory["defaults"]),
            host_key_scope=host_key_scope,
        ))

    try:
        total = len(jobs)
        done = 0
        for _key, (name, output, error) in pool.run_keyed(jobs):
            if error:
                errors[name] = error
            else:
                results[name] = output
            done += 1
            if run is not None:
                run_manager.increment_counts(run, success=0 if error else 1, failed=1 if error else 0)
                run_manager.update_progress(run, progress=100.0 * done / total if total else 100.0)
            if on_progress is not None:
                on_progress(done, total, name, error)
    except Exception as e:
        if run is not None:
            run_manager.fail_run(run, reason=str(e))
        event_bus.publish("collector.failed", {"job_id": job_id, "error": str(e)})
        raise

    event_bus.publish("collector.completed", {"job_id": job_id, "success": list(results.keys()), "failed": list(errors.keys())})

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

    if run is not None:
        if errors and not results:
            run_manager.fail_run(run, reason=f"전체 {len(errors)}대 실패")
        else:
            run_manager.finish_run(run)

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
