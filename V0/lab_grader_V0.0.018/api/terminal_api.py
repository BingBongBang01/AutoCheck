"""
TerminalApiMixin — SecureCRT 스타일 멀티 SSH 터미널 브릿지.

paramiko로 실제 대화형 쉘(invoke_shell)을 열어 세션마다 읽기 스레드를 하나씩 띄우고,
pywebview의 js_api는 항상 동기 요청-응답이라 서버가 먼저 push할 수 없으므로
JS가 짧은 주기로 폴링(get_terminal_output)해서 새로 쌓인 출력을 가져가는 방식을 쓴다
(다른 탭의 진행률 폴링과 동일 패턴).

세션 레지스트리(_sessions)는 모듈 전역 — Api 인스턴스가 여러 개 생성될 일이 없고
pywebview 프로세스 안에서 탭을 오가도 세션이 계속 살아있어야 하므로 의도적으로 모듈 전역에 둠.
"""
import os
import re
import time
import threading
import datetime

import paramiko

from core.ansi_sanitizer import clean_terminal_log, strip_ansi

# 커맨드 출력 끝에서 프롬프트로 복귀했는지 확인하는 패턴(벤더 무관 일반화):
# 줄 시작에 호스트명/디바이스명, 선택적으로 (config)/(config-if) 등의 모드 표시,
# 마지막에 #(enable) 또는 >(user) — 그 뒤로는 공백만 있고 그게 버퍼의 끝이어야 함.
_PROMPT_TAIL_RE = re.compile(r"[\r\n]\s*[\w][\w.\-]*(?:\([^\r\n)]{0,40}\))?[#>]\s*$")

_sessions = {}   # session_id -> dict(client, channel, device, buffer, read_lock, connected, error)
_sessions_lock = threading.Lock()

_inspection_job = {"running": False, "done": False, "log": [], "cancel_requested": False, "discard_on_cancel": False}
_inspection_lock = threading.Lock()


def _load_private_key(path, passphrase=None):
    last_err = None
    for key_cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey, paramiko.DSSKey):
        try:
            return key_cls.from_private_key_file(path, password=passphrase)
        except Exception as e:
            last_err = e
            continue
    raise ValueError(f"키 파일을 읽을 수 없습니다({path}): {last_err}")


def _open_session(session_id, target):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = dict(hostname=target["ip"], port=int(target.get("port") or 22), username=target.get("username", ""),
                      timeout=10, banner_timeout=10, auth_timeout=10, look_for_keys=False, allow_agent=False)
        if target.get("auth_method") == "public_key" and (target.get("key_path") or target.get("key_content")):
            key_path = target.get("key_path")
            temp_path = None
            if not key_path:
                import tempfile
                handle = tempfile.NamedTemporaryFile(mode="w", suffix=".key", delete=False, encoding="utf-8")
                handle.write(target["key_content"])
                handle.close()
                key_path = temp_path = handle.name
            kwargs["pkey"] = _load_private_key(key_path, target.get("key_passphrase") or None)
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
        else:
            kwargs["password"] = target.get("password", "")
        client.connect(**kwargs)
        channel = client.invoke_shell(term="xterm", width=120, height=40)
        channel.settimeout(0.0)
        with _sessions_lock:
            _sessions[session_id] = {
                "client": client, "channel": channel, "device": target["name"],
                "buffer": [], "read_lock": threading.Lock(), "connected": True, "error": None,
            }
        threading.Thread(target=_reader_loop, args=(session_id,), daemon=True).start()
        return True, None
    except Exception as e:
        return False, str(e)


def _reader_loop(session_id):
    while True:
        with _sessions_lock:
            sess = _sessions.get(session_id)
        if sess is None:
            return
        channel = sess["channel"]
        try:
            if channel.closed:
                sess["connected"] = False
                return
            if channel.recv_ready():
                data = channel.recv(4096).decode("utf-8", errors="replace")
                with sess["read_lock"]:
                    sess["buffer"].append(data)
            else:
                time.sleep(0.05)
        except (OSError, EOFError) as e:
            sess["connected"] = False
            sess["error"] = str(e)
            return


def _wait_for_settled_output(sess, settle_sec=0.6, max_wait_sec=8.0, prompt_grace=0.15, poll_interval=0.03):
    """커맨드 전송 후 출력을 수집하고 완료 시점을 판단한다.

    프롬프트 문자(#/>)로 복귀한 게 확인되면 그 뒤로 prompt_grace만 더 기다리고 즉시 반환 —
    장비가 응답을 다 마쳤는데도 매번 settle_sec(구 0.6s)씩 무조건 기다리던 걸 없애서
    커맨드 수 x 세션 수만큼 누적되던 지연을 크게 줄인다.
    프롬프트가 안 보이는 경우(--More-- 페이징 등)에는 기존처럼 idle-settle로 폴백한다.
    """
    collected = ""
    idle = 0.0
    waited = 0.0
    prompt_seen_at = None
    while waited < max_wait_sec:
        with sess["read_lock"]:
            if sess["buffer"]:
                collected += "".join(sess["buffer"])
                sess["buffer"] = []
                idle = 0.0
                # 프롬프트는 항상 출력 맨 끝에 나오므로 누적된 전체 텍스트가 아니라
                # 꼬리 일부만 검사한다 — 매 반복마다 전체 누적 텍스트를 strip_ansi하면
                # show running-config처럼 큰 출력에서 반복 횟수 x 누적 길이로 비용이
                # 커지는(사실상 O(n^2)) 문제가 있었다.
                prompt_seen_at = waited if _PROMPT_TAIL_RE.search(strip_ansi(collected[-400:])) else None
            else:
                idle += poll_interval
        if prompt_seen_at is not None and (waited - prompt_seen_at) >= prompt_grace:
            break
        if idle >= settle_sec and collected:
            break
        time.sleep(poll_interval)
        waited += poll_interval
    return collected


def _run_one_session_inspection(session_id, commands, results_dirs):
    with _sessions_lock:
        sess = _sessions.get(session_id)
    if sess is None:
        return
    device = sess["device"]
    channel = sess["channel"]
    output_lines = [f"=== 점검 시작 — {device} ==="]
    with sess["read_lock"]:
        sess["buffer"] = []
    # 채널이 열린 직후엔 배너/MOTD가 비동기로 도착하는 중일 수 있다 — 그 상태에서 바로
    # 첫 커맨드를 보내면 배너 꼬리와 첫 응답이 뒤섞이거나(다음 커맨드에서 "한꺼번에" 나타남),
    # 배너 출력 중이라 프롬프트 패턴이 아직 안 잡혀서 첫 커맨드가 응답 없음으로 오판된다.
    # 짧게 흘려보내 배너를 비운 뒤 점검을 시작한다.
    _wait_for_settled_output(sess, settle_sec=0.3, max_wait_sec=2.0)
    with sess["read_lock"]:
        sess["buffer"] = []
    cancelled = False
    for cmd in commands:
        with _inspection_lock:
            if _inspection_job["cancel_requested"]:
                cancelled = True
        if cancelled:
            output_lines.append("\n=== 점검 중단(사용자 요청) ===")
            with _inspection_lock:
                _inspection_job["log"].append(f"[{device}] 중지됨(사용자 요청)")
            break
        try:
            channel.send(cmd + "\n")
            collected = _wait_for_settled_output(sess)
            cleaned = clean_terminal_log(collected)
            if not cleaned.strip():
                # 응답 settle-wait가 타임아웃될 때까지 아무 출력도 못 받은 경우 —
                # 예전엔 헤더만 쓰고 본문이 통째로 빈칸이라 "로그가 비어있다"로 보였음.
                # 원인을 남겨서 빈 구간과 진짜 정상-무출력을 구분할 수 있게 한다.
                cleaned = "(응답 없음 — 시간 초과)"
            output_lines.append(f"\n--- {cmd} ---\n{cleaned}")
            with _inspection_lock:
                _inspection_job["log"].append(f"[{device}] {cmd} 완료")
        except (OSError, EOFError) as e:
            output_lines.append(f"\n--- {cmd} (오류: {e}) ---")
            with _inspection_lock:
                _inspection_job["log"].append(f"[{device}] {cmd} 실패: {e}")
            break

    with _inspection_lock:
        discard = cancelled and _inspection_job["discard_on_cancel"]
    if discard:
        with _inspection_lock:
            _inspection_job["log"].append(f"[{device}] 중지 — 결과 폐기됨(저장 안 함)")
        return

    text = "\n".join(output_lines)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    saved_paths = []
    for results_dir in results_dirs:
        os.makedirs(results_dir, exist_ok=True)
        fname = os.path.join(results_dir, f"AutoCheck_{device}_{stamp}.txt")
        with open(fname, "w", encoding="utf-8") as f:
            f.write(text)
        saved_paths.append(fname)
    with _inspection_lock:
        _inspection_job["log"].append(f"[{device}] 결과 저장됨: {', '.join(saved_paths)}")


class TerminalApiMixin:
    def _resolve_terminal_targets(self):
        """서버 내부용 — password/key_path까지 전부 포함(접속에 필요). JS로는 절대 그대로 반환하지 않음."""
        try:
            paths = self._paths()
        except RuntimeError:
            return []
        inv = self._load_inventory(paths)
        defaults = inv["defaults"]
        from engine import device_inventory as di
        targets = []
        for d in inv["devices"]:
            if not d.get("enabled", True):
                continue
            ip, port, username, password = di.resolve_credentials(d, defaults)
            if not ip:
                continue
            targets.append({
                "name": d["name"], "ip": ip, "port": port, "username": username, "password": password,
                "auth_method": d.get("auth_method", "password"), "key_path": d.get("key_path", ""),
                "key_content": d.get("key_content", ""),
                "key_passphrase": d.get("key_passphrase", ""),
            })
        return targets

    def get_terminal_targets(self):
        """JS 노출용 — 비밀번호는 제외하고 접속 후보 목록만 반환."""
        targets = self._resolve_terminal_targets()
        return [{"name": t["name"], "ip": t["ip"], "port": t["port"],
                  "auth_method": t["auth_method"]} for t in targets]

    def connect_terminal_sessions(self, device_names=None):
        """지정 장비(없으면 활성화된 전체)로 SSH 세션을 새로 연다. 반환: [{session_id, device, ok, error}]"""
        targets = self._resolve_terminal_targets()
        if device_names:
            wanted = set(device_names)
            targets = [t for t in targets if t["name"] in wanted]
        results = []
        for t in targets:
            session_id = f"{t['name']}_{int(time.time() * 1000)}"
            ok, err = _open_session(session_id, t)
            results.append({"session_id": session_id, "device": t["name"], "ok": ok, "error": err})
        return results

    def get_terminal_output(self, session_id):
        with _sessions_lock:
            sess = _sessions.get(session_id)
        if sess is None:
            return {"data": "", "connected": False, "error": "세션 없음(닫혔거나 존재하지 않음)"}
        with sess["read_lock"]:
            data = "".join(sess["buffer"])
            sess["buffer"] = []
        return {"data": data, "connected": sess["connected"], "error": sess["error"]}

    def get_terminal_output_multi(self, session_ids):
        """열려있는 모든 탭을 한 번의 js_api 왕복으로 폴링하기 위한 배치 버전.
        탭마다 get_terminal_output을 순차 await하면 왕복 지연이 탭 수만큼 누적되는데
        (열린 세션이 늘수록 폴링 주기가 점점 길어지는 원인), 여기서 한 번에 묶어 반환한다."""
        return {sid: self.get_terminal_output(sid) for sid in session_ids}

    def send_terminal_input(self, session_id, text):
        with _sessions_lock:
            sess = _sessions.get(session_id)
        if sess is None or not sess["connected"]:
            return False
        try:
            sess["channel"].send(text)
            return True
        except OSError:
            sess["connected"] = False
            return False

    def broadcast_terminal_input(self, session_ids, text):
        return {sid: self.send_terminal_input(sid, text) for sid in session_ids}

    def close_terminal_session(self, session_id):
        with _sessions_lock:
            sess = _sessions.pop(session_id, None)
        if sess:
            try:
                sess["channel"].close()
                sess["client"].close()
            except Exception:
                pass
        return True

    def run_terminal_inspection(self, session_ids):
        """열려있는 세션들에 커맨드 카탈로그의 활성 커맨드를 순서대로 입력하고, 장비별 결과를 파일로 저장."""
        with _inspection_lock:
            if _inspection_job["running"]:
                return {"error": "이미 점검이 진행 중입니다."}
        try:
            paths = self._paths()
            project_id = self._project()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}
        catalog = self._load_catalog(paths)
        from engine import command_catalog as cc
        commands = cc.get_enabled_commands(catalog)
        if not commands:
            return {"error": "활성화된 점검 커맨드가 없습니다 (커맨드 카탈로그에서 확인)"}
        valid_ids = [sid for sid in session_ids if sid in _sessions]
        if not valid_ids:
            return {"error": "연결된 세션이 없습니다. 먼저 접속하세요."}

        from engine import log_storage
        customer_name, profile_name = self.resolve_active_customer_profile_names()
        log_paths = log_storage.get_profile_log_paths(customer_name, profile_name)
        log_storage.save_config_snapshot(log_paths["root"], {
            "commands_catalog": paths.get("commands_catalog"),
            "lab_meta": paths.get("lab_meta"),
        })

        with _inspection_lock:
            _inspection_job.update({"running": True, "done": False, "cancel_requested": False, "discard_on_cancel": False,
                                     "log": [f"점검 시작 — 대상 {len(valid_ids)}개 세션, 커맨드 {len(commands)}개"]})

        def worker():
            # 기존 labs/{project}/terminal_sessions/(Reports·Findings·AI분석이 읽는 경로)와
            # 신규 data/<고객사>/<프로파일>/00_orignal_log/ 양쪽에 동일한 원본을 저장 —
            # 새 로그 저장소 요구사항을 만족시키면서 기존 보고서 파이프라인은 그대로 유지.
            results_dirs = [log_paths["original"], os.path.join("labs", project_id, "terminal_sessions")]
            threads = [threading.Thread(target=_run_one_session_inspection, args=(sid, commands, results_dirs)) for sid in valid_ids]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            with _inspection_lock:
                _inspection_job["running"] = False
                _inspection_job["done"] = True
                _inspection_job["log"].append("전체 점검 완료")

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def get_terminal_inspection_status(self):
        with _inspection_lock:
            return {"running": _inspection_job["running"], "done": _inspection_job["done"],
                     "log": list(_inspection_job["log"])}

    def stop_terminal_inspection(self, discard=False):
        """진행 중인 점검에 중지를 요청한다. discard=True면 지금까지 수집된 결과를 저장하지 않고 버린다."""
        with _inspection_lock:
            if not _inspection_job["running"]:
                return {"error": "진행 중인 점검이 없습니다."}
            _inspection_job["cancel_requested"] = True
            _inspection_job["discard_on_cancel"] = bool(discard)
            _inspection_job["log"].append("중지 요청됨 — 진행 중인 커맨드 완료 후 중단합니다.")
        return {"ok": True}
