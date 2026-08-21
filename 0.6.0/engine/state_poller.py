"""StatePoller — 장비에 직접 물어서 링크·라우팅 인접·MLAG 상태를 관측하는 두 번째 입력원.

왜 필요한가. 실시간 감시의 원래 입력은 SecureCRT 세션 로그 tail 하나였다. 그것으로는
'작업자가 무엇을 쳤는가'는 알 수 있지만 '지금 장비가 어떤 상태인가'는 알 수 없다 — 링크
DOWN·인접 상실·MLAG 이상은 syslog 에서만 나오고, syslog 는 세션에 `terminal monitor` 가 걸려
있을 때만 터미널로 에코된다. 실제 워크스페이스의 CRT 세션 로그 60여 개(5일치, 2,977줄 파일
포함)에는 **syslog 가 한 줄도 없었다.** 즉 체크리스트 7항목 중 3개가 구조적으로 판정 불가였다.

이 모듈은 그 의존을 끊는다. 작업자의 터미널 설정과 무관하게 우리가 직접 SSH 로 조회한다.

설계에서 중요한 것 네 가지:

1. **읽기 전용 명령만 낸다.** `show interfaces status` / `show ip bgp summary` / `show ip ospf
   neighbor` / `show mlag`. 설정을 바꾸는 명령은 어떤 경우에도 내지 않는다. 감시가 감시 대상을
   변경하는 일은 있어서는 안 된다.

2. **절대 상태가 아니라 전이를 보고한다.** 랩/현장 장비에는 원래 내려가 있는 포트가 흔하다.
   매 폴링마다 그것을 CRITICAL 로 올리면 화면이 즉시 무의미해진다. 첫 폴링은 기준을 세우고,
   그때 이미 이상인 것은 `history` 로 표시해 토스트를 띄우지 않는다(CRTStreamWatcher 의 seed
   와 같은 규칙 — 판정에는 쓰되 '방금 일어난 일'로는 세지 않는다).

3. **판정은 BaselineDiffEngine 에 넘긴다**(ingest_state_events). 같은 (device, component_id)
   축을 써야 출처가 달라도 서로 취소된다 — 폴링이 잡은 LINK_DOWN 을 나중에 도착한 syslog 의
   LINK_UP 이 해제하고, 그 반대도 된다. 여기서 alert 를 직접 만들면 두 개의 평행 세계가 된다.

4. **기본값은 꺼짐이다.** 이 기능은 주기적으로 운영 장비에 SSH 접속을 만든다 — 장비 부하와
   인증 로그가 남는 외부 동작이므로 사용자가 켜야 시작한다(config/realtime_watch.yaml 의
   state_poll.enabled).
"""
import re
import threading
import time

# 읽기 전용 조회만. (키, 명령, 파서) — 파서는 지연 import 한다(paramiko/parsers 비용 회피).
POLL_COMMANDS = (
    ("link", "show interfaces status"),
    ("bgp", "show ip bgp summary"),
    ("ospf", "show ip ospf neighbor"),
    ("mlag", "show mlag"),
)
DEFAULT_INTERVAL = 60.0
# 한 장비에서 이 개수를 넘는 링크 전이가 한 번에 잡히면 개별 경고로 올리지 않는다.
# 장비 재부팅·모듈 리셋이면 포트 48개가 동시에 내려가는데, 그것을 48건으로 쪼개면 목록이
# 그것만으로 가득 찬다(원인은 하나다).
_MASS_TRANSITION = 8
# '정상'으로 보는 인터페이스 상태. 나머지(notconnect/errdisabled/disabled)는 down 취급.
_LINK_UP_STATES = ("connected",)
# 관리자가 내려 둔 포트는 장애가 아니다 — 의도된 상태이므로 전이만 보고 경고로 올리지 않는다.
_ADMIN_DOWN_STATES = ("disabled",)


class StatePoller:
    """주기적으로 장비 상태를 조회해 '전이'만 골라 넘기는 백그라운드 스레드.

    on_events(device, events) 로 결과를 넘긴다. events 는
    [{"kind", "subject", "down", "detail", "source"}] 이며 BaselineDiffEngine.
    ingest_state_events() 가 그대로 받는다.

    targets_provider() 는 **이미 자격증명이 해석된** 접속 대상을 돌려준다 —
    [{"name","ip","port","username","password","auth_method",...}], 즉
    api/terminal_session_api.py 의 _resolve_terminal_targets() 와 같은 모양이고
    engine/ssh_client.connect() 가 그대로 받는 모양이다. 매 주기 다시 부른다 — 장비 목록이나
    감시 대상 선택이 바뀌면 다음 주기부터 따라가야 하므로 붙들지 않는다.
    """

    def __init__(self, targets_provider, on_events,
                 interval=DEFAULT_INTERVAL, on_error=None, max_workers=4,
                 connect_timeout=6):
        self.targets_provider = targets_provider
        self.on_events = on_events
        self.interval = float(interval or DEFAULT_INTERVAL)
        self.on_error = on_error
        self.max_workers = max_workers
        self.connect_timeout = connect_timeout

        self._prev = {}          # {device: {kind: {subject: state str}}} — 직전 폴링 결과
        self._seeded = set()     # 기준을 세운 장비(첫 폴링을 마친 장비)
        self._last_poll = {}     # {device: epoch} — 진단 표시용
        self._errors = {}        # {device: 마지막 실패 사유}
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.RLock()

    # ---------- 라이프사이클 ----------
    def start(self):
        if self.is_running():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="StatePoller")
        self._thread.start()
        return True

    def stop(self, join_timeout=8.0):
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            # SSH 접속 하나가 타임아웃까지 매달릴 수 있으므로 넉넉히 기다린다.
            thread.join(timeout=join_timeout)
        self._thread = None

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def status(self):
        with self._lock:
            return {
                "running": self.is_running(),
                "interval": self.interval,
                "polled_devices": sorted(self._seeded),
                "last_poll": dict(self._last_poll),
                "errors": dict(self._errors),
                "commands": [cmd for _kind, cmd in POLL_COMMANDS],
            }

    # ---------- 루프 ----------
    def _loop(self):
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:      # 폴링 스레드는 어떤 이유로도 죽지 않아야 한다
                self._report(exc)
            self._stop.wait(self.interval)

    def poll_once(self):
        """한 바퀴 — 테스트와 '지금 한 번 확인' 버튼이 직접 부를 수 있게 공개해 둔다."""
        targets = list(self.targets_provider() or [])
        if not targets:
            return 0
        from core.worker_pool import WorkerPool

        pool = WorkerPool(max_workers=self.max_workers, item_count=len(targets))
        emitted = 0
        for _target, result in pool.run(targets, self._poll_device):
            if not result:
                continue      # 접속/인증 실패 — status()의 errors 에 남는다
            device, events = result
            emitted += len(events)
            # 전이가 없어도 부른다. '조용한 폴링'은 아무것도 못 본 것이 아니라 보고 정상인
            # 것이므로, 그 사실이 수신부에 도달해야 체크리스트가 '판정 불가'에서 풀린다.
            try:
                self.on_events(device, events)
            except Exception as exc:
                self._report(exc)
        return emitted

    def _poll_device(self, target):
        device = (target or {}).get("name") or ""
        if not device or self._stop.is_set():
            return None
        try:
            observed = self._collect(target)
        except Exception as exc:
            with self._lock:
                self._errors[device] = str(exc)
            return None
        with self._lock:
            self._errors.pop(device, None)
            self._last_poll[device] = time.time()
            first = device not in self._seeded
            events = self._diff(device, observed, first=first)
            self._prev[device] = observed
            self._seeded.add(device)
        return (device, events)

    # ---------- 수집 ----------
    def _collect(self, target):
        """장비 하나에 접속해 읽기 전용 조회를 돌리고 {kind: {subject: state}} 로 정규화."""
        from engine.ssh_client import connect

        if not target.get("ip"):
            raise RuntimeError("IP 미설정")
        client = connect(target, timeout=self.connect_timeout)
        try:
            raw = {}
            for kind, command in POLL_COMMANDS:
                if self._stop.is_set():
                    break
                raw[kind] = self._run(client, command)
            return _normalize(raw)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _run(self, client, command):
        try:
            _, stdout, _ = client.exec_command(command, timeout=self.connect_timeout)
            return stdout.read().decode("utf-8", errors="replace")
        except Exception:
            # 이 장비가 그 기능을 안 쓰거나(BGP 미구성) 명령을 거부한 경우 — 다른 항목은 계속 본다.
            return ""

    # ---------- 전이 판정 ----------
    def _diff(self, device, observed, first):
        """직전 관측과 비교해 전이만 events 로 만든다.

        first=True(첫 폴링)면 '이미 이상인 것'만 history 로 넘긴다 — 기준을 세우는 중이므로
        토스트를 띄우지 않고, 화면에는 지금 상태가 보여야 한다.
        """
        previous = self._prev.get(device) or {}
        events = []
        for kind, states in observed.items():
            before = previous.get(kind) or {}
            transitions = []
            for subject, state in states.items():
                was = before.get(subject)
                if first:
                    if _is_down(kind, state):
                        transitions.append((subject, state, True))
                    continue
                if was == state:
                    continue
                now_down, was_down = _is_down(kind, state), _is_down(kind, was)
                if was is None:
                    # 이번에 처음 보인 대상(포트 투입 등) — 이상일 때만 알린다.
                    if now_down:
                        transitions.append((subject, state, True))
                elif now_down != was_down:
                    transitions.append((subject, state, now_down))
            events.extend(self._to_events(kind, transitions, first))
        return events

    def _to_events(self, kind, transitions, first):
        if not transitions:
            return []
        event_kind = "neighbor" if kind in ("bgp", "ospf") else kind
        source = dict(POLL_COMMANDS).get(kind, "polled")
        downs = [t for t in transitions if t[2]]
        # 한 번에 대량으로 내려갔다 = 원인이 하나다(재부팅·모듈 리셋). 개별로 쪼개지 않는다.
        if event_kind == "link" and len(downs) >= _MASS_TRANSITION:
            subjects = ", ".join(sorted(s for s, _st, _d in downs)[:6])
            merged = [{
                "kind": "link", "subject": "다수 인터페이스", "down": True,
                "detail": f"{len(downs)}개 포트가 동시에 DOWN ({subjects} …)",
                "source": source, "history": first,
            }]
            ups = [t for t in transitions if not t[2]]
            return merged + [self._event(event_kind, t, source, first) for t in ups]
        return [self._event(event_kind, t, source, first) for t in transitions]

    @staticmethod
    def _event(event_kind, transition, source, first):
        subject, state, down = transition
        return {"kind": event_kind, "subject": subject, "down": down,
                "detail": state or "", "source": source, "history": first}

    def _report(self, exc):
        if self.on_error:
            try:
                self.on_error(exc)
            except Exception:
                pass

    def reset(self):
        """감시를 다시 시작할 때 — 기준을 버려 다음 폴링이 다시 seed 로 동작하게 한다."""
        with self._lock:
            self._prev.clear()
            self._seeded.clear()
            self._last_poll.clear()
            self._errors.clear()


# ---------- 파싱 / 정규화 ----------
# 라우팅 인접만 여기서 따로 읽는다. parsers/show_routing_neighbor.py 를 재사용하지 않는 이유는
# 요구가 다르기 때문이다 — 그쪽은 '마지막 컬럼이 PfxRcd'라는 전제로 줄 끝($)에 고정돼 있어서
# 컬럼 수가 다른 레이아웃에는 매치되지 않는다(실제 Arista `show ip ospf neighbor` 는 IP 다음이
# VRF 이름이라 그 정규식이 요구하는 숫자가 오지 않는다). 여기서 필요한 것은 컬럼 정렬이 아니라
# '이 피어가 정상 상태인가' 하나뿐이므로, 상태 단어의 유무로 판정한다 — 컬럼이 몇 개든 통한다.
_PEER_LINE_RE = re.compile(r'^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+\S')
_BGP_BAD_STATE_RE = re.compile(r'\b(Idle|Active|Connect|OpenSent|OpenConfirm)\b', re.IGNORECASE)
_BGP_GOOD_STATE_RE = re.compile(r'\b(Estab\w*)\b', re.IGNORECASE)
_OSPF_STATE_RE = re.compile(r'\b(FULL|2WAY|EXSTART|EXCHANGE|LOADING|INIT|DOWN|ATTEMPT)\b'
                            r'(?:/[\w-]+)?', re.IGNORECASE)
_TRAILING_NUMBER_RE = re.compile(r'(\d+)\s*$')


def _parse_bgp_peers(text):
    """{peer_ip: state} — 'Estab*' 이나 끝자리 숫자(PfxRcd)면 정상, Idle/Active 등이면 비정상."""
    out = {}
    for line in (text or "").splitlines():
        m = _PEER_LINE_RE.match(line)
        if not m:
            continue
        bad = _BGP_BAD_STATE_RE.search(line)
        if bad and not _BGP_GOOD_STATE_RE.search(line):
            out[m.group(1)] = bad.group(1)
        elif _BGP_GOOD_STATE_RE.search(line) or _TRAILING_NUMBER_RE.search(line):
            out[m.group(1)] = "Established"
    return out


def _parse_ospf_peers(text):
    """{neighbor_id: state} — FULL 이면 정상, 나머지 전이 상태는 비정상."""
    out = {}
    for line in (text or "").splitlines():
        m = _PEER_LINE_RE.match(line)
        if not m:
            continue
        state = _OSPF_STATE_RE.search(line)
        if state:
            out[m.group(1)] = state.group(1).upper()
    return out


def _normalize(raw):
    """조회 출력들을 {kind: {subject: state}} 로 정규화."""
    from parsers.show_interfaces_status import parse_status
    from parsers.show_inventory_mlag_vrrp import parse_mlag

    out = {}
    if raw.get("link"):
        # 관리자가 내려 둔 포트(disabled)는 의도된 상태다 — 감시 대상에서 뺀다.
        out["link"] = {iface: state for iface, state in parse_status(raw["link"]).items()
                       if state not in _ADMIN_DOWN_STATES}
    if raw.get("bgp"):
        peers = _parse_bgp_peers(raw["bgp"])
        if peers:
            out["bgp"] = peers
    if raw.get("ospf"):
        peers = _parse_ospf_peers(raw["ospf"])
        if peers:
            out["ospf"] = peers
    if raw.get("mlag"):
        mlag = parse_mlag(raw["mlag"])
        if mlag.get("state"):
            # peer-link 하나만 본다 — 이 축의 component_id 는 'mlag:peer-link' 하나다.
            out["mlag"] = {"peer-link": _mlag_state(mlag)}
    return out


def _mlag_state(mlag):
    """show mlag 필드들을 한 문자열로 — 전이 비교는 문자열 동등성으로 한다."""
    parts = [mlag.get("state", ""), mlag.get("negotiation_status", ""),
             mlag.get("peer_link_status", "")]
    return "/".join(p for p in parts if p) or "unknown"


def _is_down(kind, state):
    """이 상태 문자열이 '이상'인가. state 가 None(관측 없음)이면 이상이 아니다."""
    if not state:
        return False
    low = state.lower()
    if kind == "link":
        return low not in _LINK_UP_STATES
    if kind == "bgp":
        # parse_bgp_summary 는 정상일 때 'Established' 를 채운다(마지막 컬럼이 숫자인 경우).
        return low != "established"
    if kind == "ospf":
        return low != "full"
    if kind == "mlag":
        return not (low.startswith("active") and "connected" in low)
    return False
