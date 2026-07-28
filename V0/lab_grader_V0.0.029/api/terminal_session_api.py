"""TerminalSessionApiMixin — SecureCRT 스타일 멀티 SSH 터미널 세션 라이프사이클(접속/출력/입력/종료).

paramiko로 실제 대화형 쉘(invoke_shell)을 열어 세션마다 읽기 스레드를 하나씩 띄우고,
pywebview의 js_api는 항상 동기 요청-응답이라 서버가 먼저 push할 수 없으므로
JS가 짧은 주기로 폴링(get_terminal_output)해서 새로 쌓인 출력을 가져가는 방식을 쓴다
(다른 탭의 진행률 폴링과 동일 패턴).

세션 레지스트리(_sessions)는 모듈 전역 — Api 인스턴스가 여러 개 생성될 일이 없고
pywebview 프로세스 안에서 탭을 오가도 세션이 계속 살아있어야 하므로 의도적으로 모듈 전역에 둠.
terminal_inspection_api.py가 이 모듈의 _sessions/_sessions_lock을 그대로 가져다 쓴다.
"""
import re
import time
import threading

from core.ansi_sanitizer import strip_ansi
from engine import ssh_client

# 커맨드 출력 끝에서 프롬프트로 복귀했는지 확인하는 패턴(벤더 무관 일반화):
# 줄 시작에 호스트명/디바이스명, 선택적으로 (config)/(config-if) 등의 모드 표시,
# 마지막에 #(enable) 또는 >(user) — 그 뒤로는 공백만 있고 그게 버퍼의 끝이어야 함.
_PROMPT_TAIL_RE = re.compile(r"[\r\n]\s*([\w][\w.\-]*)(?:\([^\r\n)]{0,40}\))?[#>]\s*$")

_sessions = {}   # session_id -> dict(client, channel, device, buffer, read_lock, connected, error, accumulated_tail, last_hostname)
_sessions_lock = threading.Lock()


def _open_session(session_id, target):
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
                "accumulated_tail": "", "last_hostname": None,
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
                    sess["inspect_buffer"].append(data)
                    sess["accumulated_tail"] = (sess["accumulated_tail"] + data)[-400:]
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
        """지정 장비(없으면 활성화된 전체)로 SSH 세션을 새로 연다. 반환: [{session_id, device, ok, error}]

        장비별 접속을 스레드로 병렬 처리한다 — 순차로 하면 응답 없는 장비 하나가
        connect/banner/auth 타임아웃(각 10초)을 다 채울 때까지 뒤의 장비들이 아예
        시도조차 못 하고 대기하게 되어, 죽은 장비 하나 때문에 전체 접속이 오래
        멈춘 것처럼 보이는 문제가 있었다. 실패한 장비는 error를 채운 채 결과에 남기고
        나머지 장비 접속에는 영향을 주지 않는다.
        """
        targets = self._resolve_terminal_targets()
        if device_names:
            wanted = set(device_names)
            targets = [t for t in targets if t["name"] in wanted]
        results = [None] * len(targets)

        def worker(idx, t):
            session_id = f"{t['name']}_{int(time.time() * 1000)}"
            ok, err = _open_session(session_id, t)
            results[idx] = {"session_id": session_id, "device": t["name"], "ok": ok, "error": err}

        threads = [threading.Thread(target=worker, args=(i, t), daemon=True) for i, t in enumerate(targets)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
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

    def auto_rename_device_from_session(self, session_id):
        """세션에 쌓인 프롬프트 문자열을 기반으로 장비 이름을 자동 변경한다."""
        with _sessions_lock:
            sess = _sessions.get(session_id)
        if not sess:
            return {"success": False, "error": "세션이 존재하지 않습니다."}
        
        last_hostname = sess.get("last_hostname")
        if not last_hostname:
            return {"success": False, "error": "아직 프롬프트(호스트명)를 감지하지 못했습니다. 엔터 키를 한 번 눌러보세요."}
        
        old_name = sess["device"]
        if old_name == last_hostname:
            return {"success": False, "error": f"이미 호스트명({last_hostname})과 일치합니다."}
        
        # inventory_api.py의 rename_device 메서드를 호출하여 이름 변경
        if hasattr(self, 'rename_device'):
            result = self.rename_device(old_name, last_hostname)
            if result.get("success"):
                with _sessions_lock:
                    if session_id in _sessions:
                        _sessions[session_id]["device"] = last_hostname
                return {"success": True, "old_name": old_name, "new_name": last_hostname}
            return result
        return {"success": False, "error": "rename_device 메서드를 찾을 수 없습니다."}
