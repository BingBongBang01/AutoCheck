"""TerminalSessionApiMixin — SecureCRT 스타일 멀티 SSH 터미널 세션 라이프사이클(접속/출력/입력/종료).

paramiko로 실제 대화형 쉘(invoke_shell)을 열어 세션마다 읽기 스레드를 하나씩 띄우고,
pywebview의 js_api는 항상 동기 요청-응답이라 서버가 먼저 push할 수 없으므로
JS가 짧은 주기로 폴링(_get_terminal_output)해서 새로 쌓인 출력을 가져가는 방식을 쓴다
(다른 탭의 진행률 폴링과 동일 패턴).

세션 레지스트리(_sessions)는 모듈 전역 — Api 인스턴스가 여러 개 생성될 일이 없고
pywebview 프로세스 안에서 탭을 오가도 세션이 계속 살아있어야 하므로 의도적으로 모듈 전역에 둠.
terminal_inspection_api.py가 이 모듈의 _sessions/_sessions_lock을 그대로 가져다 쓴다.
"""
import re
import time
import threading

from core import vendors
from core.ansi_sanitizer import strip_ansi
from engine import ssh_client

# 커맨드 출력 끝에서 프롬프트로 복귀했는지 확인하는 패턴(벤더 무관 일반화):
# 줄 시작에 호스트명/디바이스명, 선택적으로 (config)/(config-if) 등의 모드 표시,
# 마지막에 #(enable) 또는 >(user) — 그 뒤로는 공백만 있고 그게 버퍼의 끝이어야 함.
# group(1) 은 호스트명, group(2) 는 프롬프트 끝 글자('#' 이면 특권 모드).
_PROMPT_TAIL_RE = re.compile(r"[\r\n]\s*([\w][\w.\-]*)(?:\([^\r\n)]{0,40}\))?([#>])\s*$")

# 접속한 장비의 호스트명으로 장비 이름을 맞추기 전에 출력이 멈춰 있어야 하는 시간(_sync_session_names) — 배너나 명령
# 출력 중간의 '…>'·'…#' 줄을 프롬프트로 읽지 않게, 장비가 프롬프트를 내고 입력을 기다릴 때만 본다.
_NAME_SETTLE_SEC = 0.5

# session_id -> dict(client, channel, device, buffer, read_lock, connected, error, accumulated_tail, last_hostname,
#                    last_data_at, name_checked, name_event, platform, enable_secret)
# — platform·enable_secret 은 점검의 enable 처리용으로 서버에만 둔다. last_data_at·name_checked·name_event 는 장비 이름을
#   호스트명으로 맞추는 데 쓴다(_sync_session_names).
_sessions = {}
_sessions_lock = threading.Lock()


def _open_session(session_id, target):
    client = None
    try:
        # 접속 인자 조립(비밀번호/개인키 파일/붙여넣은 키 본문)은 engine/ssh_client.py가 단일 출처 —
        # 장비 목록의 자동 연결 확인(engine/device_probe.py)과 정확히 같은 규칙으로 접속해야
        # "터미널은 되는데 연결 확인은 실패" 같은 어긋남이 안 생긴다.
        client = ssh_client.connect(target, timeout=10)
        channel = client.invoke_shell(term="xterm", width=120, height=40)
        channel.settimeout(0.0)
        with _sessions_lock:
            _sessions[session_id] = {
                "client": client, "channel": channel, "device": target["name"],
                "buffer": [], "inspect_buffer": [], "read_lock": threading.Lock(), "connected": True, "error": None,
                "accumulated_tail": "", "last_hostname": None, "last_data_at": time.time(), "name_checked": False,
                "platform": target.get("platform", vendors.DEFAULT_PLATFORM),
                "enable_secret": target.get("enable_secret") or "",
            }
        threading.Thread(target=_reader_loop, args=(session_id,), daemon=True).start()
        return True, None
    except Exception as e:
        if client is not None:   # 접속은 됐는데 셸을 못 연 경우 — 세션을 남기지 않는다
            try:
                client.close()
            except Exception:
                pass
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
                    sess["inspect_buffer"].append(data)
                    sess["accumulated_tail"] = (sess["accumulated_tail"] + data)[-400:]
                    sess["last_data_at"] = time.time()
                    match = _PROMPT_TAIL_RE.search(strip_ansi(sess["accumulated_tail"]))
                    if match:
                        sess["last_hostname"] = match.group(1)
            else:
                time.sleep(0.05)
        except (OSError, EOFError) as e:
            sess["connected"] = False
            sess["error"] = str(e)
            return


def _wait_for_settled_output(sess, settle_sec=0.6, max_wait_sec=120.0, prompt_grace=0.15, poll_interval=0.03):
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
            if sess["inspect_buffer"]:
                collected += "".join(sess["inspect_buffer"])
                sess["inspect_buffer"] = []
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


class TerminalSessionApiMixin:
    def _resolve_terminal_targets(self):
        """서버 내부용 — password/key_path·enable 비밀번호까지 전부 포함(접속·점검에 필요). JS로는 절대 그대로 반환하지 않음.
        platform 은 vendor 칸에서 읽은 플랫폼 id(core/vendors) — 모르는 벤더면 None(터미널은 열지만 점검 명령은 보내지 않는다).
        host_key_scope 는 SSH 호스트키를 묶는 고객사 범위(engine/host_keys) — 터미널·실시간 상태 조회가 이 대상으로 접속한다."""
        try:
            paths = self._paths()
            project_id = self._project()
        except RuntimeError:
            return []
        inv = self._load_inventory(paths)
        defaults = inv["defaults"]
        from engine import device_inventory as di
        from engine import host_keys
        host_key_scope = host_keys.scope_for_project(project_id)
        targets = []
        for d in inv["devices"]:
            if not d.get("enabled", True):
                continue
            ip, port, username, password = di.resolve_credentials(d, defaults)
            if not ip:
                continue
            targets.append({
                "name": d["name"], "aliases": list(d.get("aliases") or []),
                "ip": ip, "port": port, "username": username, "password": password,
                "auth_method": d.get("auth_method", "password"), "key_path": d.get("key_path", ""),
                "key_content": d.get("key_content", ""),
                "key_passphrase": d.get("key_passphrase", ""),
                "platform": vendors.platform_of(d.get("vendor")),
                "enable_secret": di.resolve_enable_secret(d, defaults),
                "host_key_scope": host_key_scope,
            })
        return targets

    def get_terminal_targets(self):
        """JS 노출용 — 비밀번호는 제외하고 접속 후보 목록만 반환."""
        targets = self._resolve_terminal_targets()
        return [{"name": t["name"], "aliases": t.get("aliases", []), "ip": t["ip"], "port": t["port"],
                  "auth_method": t["auth_method"]} for t in targets]

    def connect_terminal_sessions(self, device_names=None):
        """지정 장비(없으면 활성화된 전체)로 SSH 세션을 새로 연다. 반환: [{session_id, device, ok, error}]

        장비별 접속을 스레드로 병렬 처리한다 — 순차로 하면 응답 없는 장비 하나가
        connect/banner/auth 타임아웃(각 10초)을 다 채울 때까지 뒤의 장비들이 아예
        시도조차 못 하고 대기하게 되어, 죽은 장비 하나 때문에 전체 접속이 오래
        멈춘 것처럼 보이는 문제가 있었다. 실패한 장비는 error를 채운 채 결과에 남기고
        나머지 장비 접속에는 영향을 주지 않는다.
        """
        from engine import device_inventory as di
        targets = self._resolve_terminal_targets()
        if device_names:
            wanted = set(device_names)
            targets = [t for t in targets if t["name"] in wanted]
        results = [None] * len(targets)

        def worker(idx, t):
            session_id = f"{t['name']}_{int(time.time() * 1000)}"
            # 계정이 비어 있는 장비는 로그인 시도 자체를 하지 않는다(예전엔 admin/admin 으로 시도)
            problem = di.credential_problem(t["username"], t["password"], di.uses_key_auth(t))
            ok, err = (False, problem) if problem else _open_session(session_id, t)
            results[idx] = {"session_id": session_id, "device": t["name"], "ok": ok, "error": err}

        threads = [threading.Thread(target=worker, args=(i, t), daemon=True) for i, t in enumerate(targets)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        return results

    def _get_terminal_output(self, session_id):
        with _sessions_lock:
            sess = _sessions.get(session_id)
        if sess is None:
            return {"data": "", "connected": False, "error": "세션 없음(닫혔거나 존재하지 않음)"}
        with sess["read_lock"]:
            data = "".join(sess["buffer"])
            sess["buffer"] = []
            name_sync = sess.pop("name_event", None)
        out = {"data": data, "connected": sess["connected"], "error": sess["error"]}
        if name_sync:   # 장비 이름을 호스트명으로 바꿨다(또는 바꾸지 못했다) — 화면이 탭·장비 목록을 고친다
            out["name_sync"] = name_sync
        return out

    def get_terminal_output_multi(self, session_ids):
        """열려있는 모든 탭을 한 번의 js_api 왕복으로 폴링하기 위한 배치 버전.
        탭마다 get_terminal_output을 순차 await하면 왕복 지연이 탭 수만큼 누적되는데
        (열린 세션이 늘수록 폴링 주기가 점점 길어지는 원인), 여기서 한 번에 묶어 반환한다."""
        self._sync_session_names(session_ids)
        return {sid: self._get_terminal_output(sid) for sid in session_ids}

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

    def _claim_hostname(self, session_id, settled=False):
        """이름을 맞출 차례인 세션이면 (세션, 호스트명) — 세션마다 한 번만 돌려준다(이름이 같으면 None).

        장비가 프롬프트에서 멈춰 있을 때만 본다(마지막 출력이 프롬프트이고 _NAME_SETTLE_SEC 넘게 조용함) — 배너나 명령
        출력에서 '>'·'#' 로 끝나는 줄을 호스트명으로 읽지 않으려는 것이다. settled=True 는 부르는 쪽(세션 점검)이 장비가
        프롬프트로 돌아온 것을 이미 확인했다는 뜻이다."""
        with _sessions_lock:
            sess = _sessions.get(session_id)
        if sess is None:
            return None
        with sess["read_lock"]:
            if sess.get("name_checked"):
                return None
            quiet = settled or time.time() - sess.get("last_data_at", 0.0) >= _NAME_SETTLE_SEC
            match = _PROMPT_TAIL_RE.search(strip_ansi(sess["accumulated_tail"])) if quiet else None
            if not match:
                return None
            sess["name_checked"] = True
        hostname = match.group(1)
        return None if hostname == sess["device"] else (sess, hostname)

    def _sync_session_names(self, session_ids, settled=False):
        """세션들의 장비 이름을 장비가 프롬프트로 알려 준 호스트명으로 맞춘다 — 세션마다 한 번(접속 직후).
        장비 목록의 이름을 바꾸고(api/inventory_api._rename_devices — 옛 이름은 별칭으로 남는다) 세션도 새 이름으로 잇는다
        — 점검 로그 파일명이 세션의 장비 이름이다. 결과는 다음 출력 폴링이 화면에 전한다(name_sync).
        반환: {session_id: {"from", "to"} 또는 바꾸지 않은 이유 {"note"}} — 아직 판단할 수 없거나, 이미 봤거나, 이름이
        같은 세션은 빠진다."""
        claims = {}
        for session_id in session_ids:
            claim = self._claim_hostname(session_id, settled)
            if claim:
                claims[session_id] = claim
        if not claims:
            return {}
        problems = self._rename_devices([(sess["device"], hostname) for sess, hostname in claims.values()])
        events = {}
        for (session_id, (sess, hostname)), problem in zip(claims.items(), problems):
            event = self._session_renamed(sess, hostname, problem)
            with sess["read_lock"]:
                sess["name_event"] = event
            events[session_id] = event
        return events

    def _sync_session_name(self, session_id, settled=False):
        """세션 하나 — 세션 점검이 로그를 저장하기 전에 부른다(api/terminal_inspection_api)."""
        return self._sync_session_names([session_id], settled).get(session_id)

    @staticmethod
    def _session_renamed(sess, hostname, problem):
        old_name = sess["device"]
        if problem:
            return {"note": f"{old_name} → {hostname}: 이름을 바꾸지 않았습니다 — {problem}"}
        sess["device"] = hostname
        return {"from": old_name, "to": hostname}

    def auto_rename_device_from_session(self, session_id):
        """'이름 동기화' 버튼 — 접속 직후 자동으로 맞추지 못한 세션(이름이 겹친 장비를 고친 뒤 등)을 다시 맞춘다.
        반환: {success, old_name, new_name} 또는 {success: False, error[, unchanged]}."""
        with _sessions_lock:
            sess = _sessions.get(session_id)
        if not sess:
            return {"success": False, "error": "세션이 존재하지 않습니다."}
        last_hostname = sess.get("last_hostname")
        if not last_hostname:
            return {"success": False,
                    "error": f"{sess['device']}: 아직 프롬프트(호스트명)를 감지하지 못했습니다 — 엔터 키를 한 번 눌러 보세요."}
        if sess["device"] == last_hostname:
            return {"success": False, "unchanged": True, "error": f"이미 호스트명({last_hostname})과 일치합니다."}
        (problem,) = self._rename_devices([(sess["device"], last_hostname)])
        result = self._session_renamed(sess, last_hostname, problem)
        if "note" in result:
            return {"success": False, "error": result["note"]}
        return {"success": True, "old_name": result["from"], "new_name": result["to"]}
