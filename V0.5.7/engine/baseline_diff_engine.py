"""BaselineDiffEngine — 실시간 CLI 스트림 한 줄씩을 Baseline과 대조해 이상 징후를 판정.

입력은 CRTStreamWatcher가 넘겨주는 차분 텍스트(여러 줄일 수 있음)이고, 출력은 UI 토스트로 그대로
쓸 수 있는 alert dict 리스트다.

    {"device": "Core1", "severity": "CRITICAL", "type": "CONFIG_REMOVED",
     "message": "Baseline 등록 VLAN 100 삭제 명령 감지!", "raw_line": "no vlan 100"}

'Stateful'인 이유 세 가지:
  1) `configure` / `interface Ethernet1` 처럼 문맥을 만드는 줄이 뒤 줄의 의미를 바꾼다.
     (`shutdown` 한 줄만 봐서는 어느 인터페이스인지 알 수 없다.)
  2) 같은 경고가 스트림에 반복 등장(터미널 에코 + 로그 재출력)하므로 짧은 시간 창 안의 중복은 접는다.
  3) (v0.5.4) 구성요소별 '지금 문제 상태인가'를 StateTracker에 들고 있어서, 복구 이벤트가
     들어오면 앞서 낸 경고를 취소(resolve)한다. `shutdown` 뒤에 `no shutdown`을 치면
     CRITICAL 토스트가 화면에 남아 있을 이유가 없다 — 남아 있으면 작업자가 '아직 장애'로 읽는다.

취소(cancellation) 설계
    문제 이벤트는 (device, component_id) 키에 condition을 세우고 alert_id를 매달아 둔다.
    복구 이벤트는 같은 키의 condition을 걷어내면서 매달려 있던 alert_id들을 resolution으로
    내보낸다. 호출부(api/log_analysis_run_api.py)가 drain_resolutions()로 가져가
    UI에 window.onRealtimeDiffAlertResolved(...)로 push한다.

    condition을 '경고 종류'가 아니라 '구성요소의 상태'로 잡은 이유: shutdown(명령 에코)과
    LINEPROTO DOWN(syslog)은 서로 다른 alert지만 같은 인터페이스의 같은 장애 하나다.
    `no shutdown` 한 번에 둘 다 사라져야 한다.

시간은 호출부에서 주입할 수 있게 clock 인자로 받는다(테스트 용이성).
"""
import re
import time
import itertools

from core.ansi_sanitizer import strip_ansi
from engine.baseline_store import normalize_interface

CRITICAL = "CRITICAL"
MAJOR = "MAJOR"
WARNING = "WARNING"

# ---------- 상태 조건(condition) — 구성요소가 '지금 문제인지'를 나타내는 이름 ----------
COND_INTERFACE_DOWN = "interface_down"
COND_MLAG_PEER_DOWN = "mlag_peer_down"
COND_ADJACENCY_LOST = "routing_adjacency_lost"
COND_CONFIG_REMOVED = "config_removed"

# alert type -> (condition, 복구 이벤트인가)
# 여기 없는 type은 상태를 만들지 않는다(예: DESTRUCTIVE_COMMAND는 '되돌리는 이벤트'가 없다).
_CONDITION_MAP = {
    "INTERFACE_SHUTDOWN": (COND_INTERFACE_DOWN, False),
    "LINK_DOWN": (COND_INTERFACE_DOWN, False),
    "LINK_UP": (COND_INTERFACE_DOWN, True),
    "INTERFACE_NOSHUT": (COND_INTERFACE_DOWN, True),
    "NEIGHBOR_DOWN": (COND_ADJACENCY_LOST, False),
    "NEIGHBOR_UP": (COND_ADJACENCY_LOST, True),
    "MLAG_PEER_DOWN": (COND_MLAG_PEER_DOWN, False),
    "MLAG_PEER_UP": (COND_MLAG_PEER_DOWN, True),
    "CONFIG_REMOVED": (COND_CONFIG_REMOVED, False),
    "CONFIG_RESTORED": (COND_CONFIG_REMOVED, True),
}

# ---------- 설정 변경(명령어 입력) ----------
_NO_VLAN_RE = re.compile(r'^\s*no\s+vlan\s+([\d,\-\s]+)\s*$', re.IGNORECASE)
_NO_ROUTE_RE = re.compile(r'^\s*no\s+ip(?:v6)?\s+route\s+(.*)$', re.IGNORECASE)
_NO_INTERFACE_RE = re.compile(r'^\s*no\s+interface\s+(\S+)', re.IGNORECASE)
_NO_NEIGHBOR_RE = re.compile(r'^\s*no\s+neighbor\s+(\d{1,3}(?:\.\d{1,3}){3})', re.IGNORECASE)
_INTERFACE_CTX_RE = re.compile(r'^\s*interface\s+(\S+)\s*$', re.IGNORECASE)
_SHUTDOWN_RE = re.compile(r'^\s*shutdown\s*$', re.IGNORECASE)
_NO_SHUTDOWN_RE = re.compile(r'^\s*no\s+shutdown\s*$', re.IGNORECASE)
# 'no vlan 100' 뒤에 'vlan 100'을 다시 치면 삭제 경고를 취소해야 한다.
# _NO_VLAN_RE보다 뒤에서 검사하므로 'no vlan'이 여기 걸릴 일은 없지만, 방어적으로 no를 제외한다.
_VLAN_ADD_RE = re.compile(r'^\s*vlan\s+([\d,\-\s]+)\s*$', re.IGNORECASE)
_ROUTER_BGP_RE = re.compile(r'^\s*router\s+bgp\s+(\d+)', re.IGNORECASE)
_NO_ROUTER_BGP_RE = re.compile(r'^\s*no\s+router\s+bgp\s+(\d+)', re.IGNORECASE)
_EXIT_RE = re.compile(r'^\s*(?:exit|end|!)\s*$', re.IGNORECASE)
_DESTRUCTIVE_RE = re.compile(
    r'^\s*(reload|write\s+erase|erase\s+startup-config|delete\s+flash|copy\s+\S+\s+startup-config)\b',
    re.IGNORECASE)
_CIDR_RE = re.compile(r'(\d{1,3}(?:\.\d{1,3}){3})(?:/(\d{1,2})|\s+(\d{1,3}(?:\.\d{1,3}){3}))?')

# ---------- 상태 변화(syslog 출력) ----------
# MLAG peer-link는 일반 MLAG_STATE보다 먼저 검사한다 — 취소 대상이 되는 별개 condition이고,
# 'active-full'(복구)에는 down 단어가 없어서 아래 is_down 휴리스틱만으로는 방향을 못 가린다.
_MLAG_PEERLINK_DOWN_RE = re.compile(
    r'(?:mlag.*peer-?link.*\b(?:down|fail(?:ed|ure)?|inactive)\b'
    r'|MLAG-\d-\w*PEER_?LINK\w*.*\b(?:DOWN|FAIL)'
    r'|peer-?link\s+status\s*:?\s*down'
    r'|\bdual-?active\b)', re.IGNORECASE)
_MLAG_PEERLINK_UP_RE = re.compile(
    r'(?:peer-?link.*\bactive-full\b|\bactive-full\b.*peer-?link'
    r'|mlag.*peer-?link.*\b(?:up|active-full|established)\b'
    r'|peer-?link\s+status\s*:?\s*(?:up|active-full))', re.IGNORECASE)
# OSPF/BGP 인접 '수립' — LINEPROTO up과 달리 별도 단어를 봐야 한다.
_ADJ_ESTABLISHED_RE = re.compile(
    r'(?:\b(?:Established|ESTABLISHED)\b'
    r'|ADJCHG.*\bto\s+FULL\b'
    r'|changed\s+state\s+to\s+(?:Established|Full)\b'
    r'|Neighbor\s+\S+\s+is\s+(?:up|Established))', re.IGNORECASE)
_NEIGHBOR_IP_RE = re.compile(r'\b(\d{1,3}(?:\.\d{1,3}){3})\b')

_SYSLOG_PATTERNS = (
    (re.compile(r'BGP-\d-ADJCHANGE.*?(?:neighbor\s+)?(\d{1,3}(?:\.\d{1,3}){3}).*?\b(Down|Up)\b', re.IGNORECASE),
     "NEIGHBOR_STATE"),
    # Cisco/Arista의 실제 mnemonic은 %OSPF-5-ADJCHG다 — 예전 패턴은 ADJCHANGE만 봐서
    # OSPF 인접 변화를 전부 놓치고 있었다(BGP는 ADJCHANGE가 맞다).
    (re.compile(r'OSPF-\d-ADJCH(?:G|ANGE).*?\b(?:from|to)\b.*', re.IGNORECASE), "NEIGHBOR_STATE"),
    (re.compile(r'LINEPROTO-\d-UPDOWN.*?Line protocol on Interface\s+(\S+?),\s*changed state to (\w+)', re.IGNORECASE),
     "LINK_STATE"),
    (re.compile(r'LINK-\d-CHANGED.*?Interface\s+(\S+?),\s*changed state to (\w+)', re.IGNORECASE), "LINK_STATE"),
    (re.compile(r'MLAG-\d-\w*STATE.*', re.IGNORECASE), "MLAG_STATE"),
    (re.compile(r'STP-\d-\w+.*', re.IGNORECASE), "STP_CHANGE"),
)
_DOWN_WORDS = ("down", "notconnect", "errdisabled", "inactive", "disabled")

# 설정 변경이 뒤따르는 DOWN의 '원인'으로 인정되는 시간창(초).
# 90초로 잡은 근거: 인터페이스를 내리면 LINEPROTO는 1~2초 내 뜨지만, BGP/OSPF hold timer가
# 만료되며 나오는 인접 상실은 기본 홀드타임(BGP 90s / OSPF dead 40s)만큼 늦게 온다.
_ROOT_CAUSE_WINDOW = 90.0
# 설정 변경으로 간주하는 alert type — 이 뒤에 온 DOWN에 '작업이 원인' 주석을 붙인다.
_CAUSE_TYPES = ("CONFIG_REMOVED", "INTERFACE_SHUTDOWN", "CONFIG_CHANGED", "DESTRUCTIVE_COMMAND")
_EFFECT_TYPES = ("LINK_DOWN", "NEIGHBOR_DOWN", "MLAG_PEER_DOWN", "STP_CHANGE", "MLAG_STATE")


class StateTracker:
    """(device, component_id) -> {condition: 열린 경고 정보} 상태표.

    '지금 이 인터페이스가 내려가 있나'를 기억하는 것이 전부다. 이 표가 없으면
    `no shutdown`을 봤을 때 '무엇을 취소해야 하는지' 알 수 없다.

    UI가 개별 토스트/이력 행을 지울 수 있어야 하므로 열린 경고의 alert_id를 전부 모아 둔다
    (하나의 장애에 shutdown 에코 + LINEPROTO DOWN 두 alert가 붙는 게 정상이다).
    """

    def __init__(self, clock=time.time):
        self._clock = clock
        self._open = {}   # {(device, component_id): {condition: {"alert_ids": [..], "since": ts, "raw": str}}}

    def open(self, device, component_id, condition, alert_id, raw_line):
        """문제 상태를 세운다. 같은 condition이 이미 열려 있으면 alert_id만 덧붙인다."""
        if not component_id:
            return
        slot = self._open.setdefault((device, component_id), {})
        entry = slot.get(condition)
        if entry is None:
            entry = {"alert_ids": [], "since": self._clock(), "raw": raw_line}
            slot[condition] = entry
        if alert_id and alert_id not in entry["alert_ids"]:
            entry["alert_ids"].append(alert_id)

    def close(self, device, component_id, condition, raw_line):
        """복구 이벤트 — 열린 문제 상태를 걷어내고 취소할 alert_id 목록을 반환.

        열린 게 없으면 빈 리스트다(감시 시작 전부터 내려가 있던 인터페이스를 올린 경우 등).
        """
        if not component_id:
            return []
        key = (device, component_id)
        slot = self._open.get(key)
        if not slot:
            return []
        entry = slot.pop(condition, None)
        if not slot:
            self._open.pop(key, None)
        if entry is None:
            return []
        return [{
            "alert_id": aid,
            "device": device,
            "component_id": component_id,
            "condition": condition,
            "opened_by": entry["raw"],
            "resolved_by": raw_line.strip(),
            "duration_sec": round(max(0.0, self._clock() - entry["since"]), 1),
            "ts": time.strftime("%H:%M:%S"),
        } for aid in entry["alert_ids"]]

    def is_open(self, device, component_id, condition):
        return condition in (self._open.get((device, component_id)) or {})

    def open_conditions(self, device=None):
        """진단/상태 표시용 — 지금 열려 있는 문제 상태 목록."""
        out = []
        for (dev, component), slot in self._open.items():
            if device is not None and dev != device:
                continue
            for condition, entry in slot.items():
                out.append({"device": dev, "component_id": component, "condition": condition,
                            "since": entry["since"], "raw": entry["raw"],
                            "alert_ids": list(entry["alert_ids"])})
        return out

    def reset(self, device=None):
        if device is None:
            self._open.clear()
        else:
            for key in [k for k in self._open if k[0] == device]:
                self._open.pop(key, None)


class BaselineDiffEngine:
    """Baseline 스냅샷과 실시간 스트림을 대조하는 판정기.

    baseline_store: engine.baseline_store.BaselineStore 인스턴스.
    dedupe_window: 같은 (장비, 종류, 대상) 경고를 몇 초간 접을지.
    """

    def __init__(self, baseline_store, dedupe_window=10.0, clock=time.time):
        self.baseline_store = baseline_store
        self.dedupe_window = dedupe_window
        self._clock = clock
        self._ctx = {}       # {device: {"interface": str|None, "bgp": str|None}}
        self._recent = {}    # {dedupe_key: 마지막 발행 시각}
        self.state = StateTracker(clock=clock)
        self._resolutions = []   # drain_resolutions()로 호출부가 가져간다
        # alert_id는 프로세스 내에서만 유효하면 된다(UI가 화면에 뜬 토스트를 지우는 용도).
        # 감시 스레드 하나에서만 발급되므로 락 없이 count()로 충분하다.
        self._ids = itertools.count(1)
        self._recent_causes = {}  # {(device, component_id): {"raw":.., "ts":.., "type":..}}

    # ---------- 진입점 ----------
    def analyze_stream(self, device, text):
        """차분 텍스트(여러 줄)를 분석해 alert 리스트 반환. 이상 없으면 빈 리스트.

        복구로 취소된 경고는 이 반환값에 들어가지 않는다 — drain_resolutions()로 따로 가져간다.
        """
        alerts = []
        for raw_line in strip_ansi(text or "").splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                continue
            alerts.extend(self._analyze_line(device, line))
        accepted = [a for a in alerts if self._accept(a)]
        for alert in accepted:
            # id는 중복 억제를 통과한 것에만 발급한다 — 버려진 alert의 id를 UI가 볼 일이 없다.
            if not alert.get("alert_id"):
                alert["alert_id"] = f"{device or 'unknown'}#{next(self._ids)}"
            self._track_state(alert)
            self._annotate_root_cause(alert)
        # 상태 추적이 끝난 뒤 걸러낸다 — 복구 alert도 state.close()를 거쳐야 하기 때문이다.
        # 복구 자체는 '경고'가 아니므로 이력/토스트로 올리지 않고 resolution으로만 흘린다.
        return [a for a in accepted if not a.pop("_is_recovery", False)]

    def drain_resolutions(self):
        """이번 분석에서 발생한 '경고 취소' 목록을 꺼내 비운다."""
        out, self._resolutions = self._resolutions, []
        return out

    def open_conditions(self, device=None):
        return self.state.open_conditions(device)

    def reset_context(self, device=None):
        """세션이 끊겼거나 로그가 rotate됐을 때 config 모드 문맥을 비운다.

        상태표도 함께 비운다 — 세션이 끊긴 시점의 '열린 장애'는 새 세션에서 취소될 수 없고,
        남겨 두면 재접속 후 첫 `no shutdown`이 엉뚱한 옛 경고를 지운다.
        """
        if device is None:
            self._ctx.clear()
            self.state.reset()
            self._recent_causes.clear()
        else:
            self._ctx.pop(device, None)
            self.state.reset(device)
            for key in [k for k in self._recent_causes if k[0] == device]:
                self._recent_causes.pop(key, None)

    # ---------- 상태 추적 / 취소 ----------
    def _track_state(self, alert):
        """alert 하나를 상태표에 반영. 복구 이벤트면 resolution을 쌓고 _is_recovery를 표시한다."""
        mapping = _CONDITION_MAP.get(alert.get("type"))
        if not mapping:
            return
        condition, is_recovery = mapping
        component = alert.get("component_id")
        if not component:
            return
        device = alert.get("device")
        if is_recovery:
            resolved = self.state.close(device, component, condition, alert.get("raw_line", ""))
            self._resolutions.extend(resolved)
            alert["resolved_count"] = len(resolved)
            # 취소한 게 없으면(원래 열린 장애가 없었음) 정보성 경고로 그대로 보여준다 —
            # '누가 no shutdown을 쳤다'는 사실 자체는 작업 이력으로 유용하다.
            alert["_is_recovery"] = bool(resolved)
        else:
            self.state.open(device, component, condition, alert.get("alert_id"), alert.get("raw_line", ""))

    def _annotate_root_cause(self, alert):
        """설정 변경 직후의 DOWN에 '작업이 원인' 주석을 붙인다(Module 3 요구사항).

        같은 구성요소에서 최근 _ROOT_CAUSE_WINDOW초 안에 설정 변경이 있었으면 그것을 지목하고,
        없으면 같은 장비의 아무 설정 변경이라도 지목한다 — VLAN 삭제가 SVI를 내리는 것처럼
        원인과 결과의 구성요소가 다른 연쇄가 실제로 더 흔하다.
        """
        atype = alert.get("type")
        device = alert.get("device")
        component = alert.get("component_id")
        now = self._clock()

        if atype in _CAUSE_TYPES:
            if component:
                self._recent_causes[(device, component)] = {
                    "raw": alert.get("raw_line", ""), "ts": now, "type": atype}
            # 장비 단위로도 하나 남긴다(연쇄의 시작점을 구성요소 없이도 찾을 수 있게).
            self._recent_causes[(device, "*")] = {
                "raw": alert.get("raw_line", ""), "ts": now, "type": atype,
                "component": component}
            return

        if atype not in _EFFECT_TYPES:
            return

        cause = self._recent_causes.get((device, component))
        scope = component
        if cause is None or (now - cause["ts"]) > _ROOT_CAUSE_WINDOW:
            cause = self._recent_causes.get((device, "*"))
            scope = (cause or {}).get("component")
        if cause is None or (now - cause["ts"]) > _ROOT_CAUSE_WINDOW:
            return

        where = f" on interface {scope}" if scope else ""
        alert["root_cause"] = {
            "raw_line": cause["raw"],
            "type": cause["type"],
            "component_id": scope,
            "elapsed_sec": round(now - cause["ts"], 1),
            "intent": f"Triggered by recent configuration change{where}",
        }
        alert["message"] = (f"{alert.get('message', '')} "
                            f"— 직전 작업이 원인으로 추정됩니다: '{cause['raw']}'").strip()

    # ---------- 줄 단위 판정 ----------
    def _analyze_line(self, device, line):
        baseline = self.baseline_store.get_device_baseline(device)
        ctx = self._ctx.setdefault(device, {"interface": None, "bgp": None})
        body = _strip_prompt(line)
        out = []

        # --- 문맥 갱신 ---
        if _EXIT_RE.match(body):
            ctx["interface"] = None
            ctx["bgp"] = None
            return out
        ictx = _INTERFACE_CTX_RE.match(body)
        if ictx:
            ctx["interface"] = normalize_interface(ictx.group(1))
            ctx["bgp"] = None
            return out
        if _ROUTER_BGP_RE.match(body) and not _NO_ROUTER_BGP_RE.match(body):
            ctx["bgp"] = _ROUTER_BGP_RE.match(body).group(1)
            ctx["interface"] = None
            return out

        # --- 설정 삭제 계열 ---
        m = _NO_VLAN_RE.match(body)
        if m:
            for vid in _expand_vlans(m.group(1)):
                if vid in baseline["vlans"]:
                    out.append(_alert(device, CRITICAL, "CONFIG_REMOVED",
                                      f"Baseline 등록 VLAN {vid} 삭제 명령 감지!", line, target=f"vlan:{vid}"))
                else:
                    out.append(_alert(device, WARNING, "CONFIG_REMOVED",
                                      f"VLAN {vid} 삭제 명령 감지 (Baseline 미등록)", line, target=f"vlan:{vid}"))
            return out

        m = _NO_ROUTE_RE.match(body)
        if m:
            cidr = _first_cidr(m.group(1))
            known = cidr and cidr in baseline["routes"]
            out.append(_alert(device, CRITICAL if known else WARNING, "CONFIG_REMOVED",
                              f"Baseline 등록 라우트 {cidr} 삭제 명령 감지!" if known
                              else f"ip route 삭제 명령 감지: {m.group(1).strip()}",
                              line, target=f"route:{cidr or m.group(1).strip()}"))
            return out

        m = _NO_INTERFACE_RE.match(body)
        if m:
            iface = normalize_interface(m.group(1))
            known = iface in baseline["interfaces"]
            out.append(_alert(device, CRITICAL if known else MAJOR, "CONFIG_REMOVED",
                              f"Baseline 등록 인터페이스 {iface} 설정 삭제 명령 감지!" if known
                              else f"인터페이스 {iface} 설정 삭제 명령 감지",
                              line, target=f"interface:{iface}"))
            return out

        m = _NO_NEIGHBOR_RE.match(body)
        if m:
            peer = m.group(1)
            known = peer in baseline["bgp_neighbors"]
            out.append(_alert(device, CRITICAL if known else MAJOR, "CONFIG_REMOVED",
                              f"Baseline 등록 BGP 네이버 {peer} 삭제 명령 감지!" if known
                              else f"BGP 네이버 {peer} 삭제 명령 감지",
                              line, target=f"bgp:{peer}"))
            return out

        m = _NO_ROUTER_BGP_RE.match(body)
        if m:
            out.append(_alert(device, CRITICAL, "CONFIG_REMOVED",
                              f"BGP 프로세스(AS {m.group(1)}) 전체 삭제 명령 감지!", line,
                              target=f"router-bgp:{m.group(1)}"))
            return out

        # --- 인터페이스 shutdown / no shutdown ---
        if _SHUTDOWN_RE.match(body):
            iface = ctx.get("interface")
            if iface:
                known = iface in baseline["interfaces"]
                out.append(_alert(device, CRITICAL if known else MAJOR, "INTERFACE_SHUTDOWN",
                                  f"Baseline 등록 인터페이스 {iface} shutdown 명령 감지!" if known
                                  else f"인터페이스 {iface} shutdown 명령 감지",
                                  line, target=f"shutdown:{iface}"))
            else:
                out.append(_alert(device, MAJOR, "INTERFACE_SHUTDOWN",
                                  "shutdown 명령 감지 (대상 인터페이스 문맥 불명)", line, target="shutdown:?"))
            return out
        if _NO_SHUTDOWN_RE.match(body) and ctx.get("interface"):
            # type을 INTERFACE_NOSHUT으로 둔 이유: _CONDITION_MAP이 이걸 interface_down의
            # 복구로 인식해 앞서 낸 shutdown/LINK_DOWN 경고를 취소한다.
            # target prefix는 noshut: 그대로 둬야 체크리스트가 'interface' 항목으로 집계한다.
            out.append(_alert(device, WARNING, "INTERFACE_NOSHUT",
                              f"인터페이스 {ctx['interface']} 활성화(no shutdown) 명령 감지",
                              line, target=f"noshut:{ctx['interface']}"))
            return out

        # --- VLAN 재등록(삭제 취소) ---
        # _NO_VLAN_RE를 이미 지나쳐 왔으므로 여기 오는 'vlan N'은 삭제가 아닌 생성/진입이다.
        m = _VLAN_ADD_RE.match(body)
        if m:
            for vid in _expand_vlans(m.group(1)):
                if self.state.is_open(device, f"vlan:{vid}", COND_CONFIG_REMOVED):
                    out.append(_alert(device, WARNING, "CONFIG_RESTORED",
                                      f"VLAN {vid} 재등록 — 앞선 삭제 경고를 해제합니다",
                                      line, target=f"vlan:{vid}"))
            return out

        # --- 파괴적 운영 명령 ---
        m = _DESTRUCTIVE_RE.match(body)
        if m:
            out.append(_alert(device, CRITICAL, "DESTRUCTIVE_COMMAND",
                              f"위험 명령 실행 감지: {body.strip()}", line,
                              target=f"cmd:{m.group(1).lower()}"))
            return out

        # --- syslog 상태 변화 ---
        out.extend(self._match_syslog(device, line, baseline))
        return out

    def _match_syslog(self, device, line, baseline):
        # MLAG peer-link는 일반 규칙보다 먼저 — 'active-full'에는 down 단어가 없어서
        # 아래 is_down 휴리스틱에 맡기면 복구가 경고로 뒤집힌다.
        # UP을 먼저 보는 이유: 'peer-link ... down -> active-full' 처럼 한 줄에 두 상태가
        # 같이 적히는 로그가 있고, 그 줄의 결론은 뒤쪽(복구)이다.
        if _MLAG_PEERLINK_UP_RE.search(line):
            return [_alert(device, WARNING, "MLAG_PEER_UP",
                           "MLAG peer-link 정상(active-full) 복구", line,
                           target="mlag:peer-link")]
        if _MLAG_PEERLINK_DOWN_RE.search(line):
            return [_alert(device, CRITICAL, "MLAG_PEER_DOWN",
                           "MLAG peer-link 이상 — 이중화 상실 / split-brain 위험", line,
                           target="mlag:peer-link")]

        for pattern, kind in _SYSLOG_PATTERNS:
            m = pattern.search(line)
            if not m:
                continue
            low = line.lower()
            is_down = any(word in low for word in _DOWN_WORDS)
            groups = [g for g in m.groups() if g]
            subject = groups[0] if groups else kind

            if kind == "LINK_STATE":
                iface = normalize_interface(subject)
                known = iface in baseline["interfaces"]
                if is_down:
                    return [_alert(device, CRITICAL if known else MAJOR, "LINK_DOWN",
                                   f"인터페이스 {iface} Line protocol DOWN 감지!", line,
                                   target=f"link:{iface}")]
                return [_alert(device, WARNING, "LINK_UP",
                               f"인터페이스 {iface} UP 복구", line, target=f"link:{iface}")]

            if kind == "NEIGHBOR_STATE":
                # OSPF ADJCHG 패턴은 네이버 IP를 캡처하지 않아 subject가 'NEIGHBOR_STATE'로
                # 떨어진다 — 그러면 모든 OSPF 네이버가 같은 component로 뭉쳐 서로의 경고를
                # 취소해 버린다. 줄에서 IP를 직접 뽑아 구성요소를 분리한다.
                peer = subject if _NEIGHBOR_IP_RE.fullmatch(subject or "") else None
                if peer is None:
                    ip = _NEIGHBOR_IP_RE.search(line)
                    peer = ip.group(1) if ip else (subject or "unknown")
                # 'from LOADING to FULL' 은 down 단어가 없지만, 'from FULL to DOWN' 처럼
                # 상태 전이가 한 줄에 다 적히는 형식에서는 결론(to X)만 봐야 한다.
                established = bool(_ADJ_ESTABLISHED_RE.search(line))
                known = peer in baseline["bgp_neighbors"]
                if is_down and not established:
                    return [_alert(device, CRITICAL if known else MAJOR, "NEIGHBOR_DOWN",
                                   f"라우팅 네이버 {peer} 인접관계 DOWN 감지!", line,
                                   target=f"peer:{peer}")]
                return [_alert(device, WARNING, "NEIGHBOR_UP",
                               f"라우팅 네이버 {peer} 인접관계 복구(Established/FULL)", line,
                               target=f"peer:{peer}")]

            severity = CRITICAL if is_down else WARNING
            return [_alert(device, severity, kind, f"{kind} 로그 감지: {line.strip()[:160]}",
                           line, target=f"{kind}:{subject}")]
        return []

    # ---------- 중복 억제 ----------
    def _accept(self, alert):
        key = (alert["device"], alert["type"], alert.get("target"))
        now = self._clock()
        last = self._recent.get(key)
        if last is not None and (now - last) < self.dedupe_window:
            return False
        self._recent[key] = now
        if len(self._recent) > 2000:
            cutoff = now - self.dedupe_window
            self._recent = {k: v for k, v in self._recent.items() if v >= cutoff}
        return True


# ---------- 헬퍼 ----------
_PROMPT_RE = re.compile(r'^\s*[\w.\-]+(?:\([^)]*\))?\s*[#>]\s*')


def _strip_prompt(line):
    """'Core1(config-if-Et1)# shutdown' -> 'shutdown'"""
    return _PROMPT_RE.sub("", line, count=1)


def _expand_vlans(token):
    out = []
    for chunk in token.replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk:
            lo, _, hi = chunk.partition("-")
            if lo.isdigit() and hi.isdigit() and int(lo) <= int(hi) and int(hi) - int(lo) <= 4094:
                out.extend(str(v) for v in range(int(lo), int(hi) + 1))
        elif chunk.isdigit():
            out.append(str(int(chunk)))
    return out


def _first_cidr(text):
    """'10.0.0.0/24 ...' 또는 '10.0.0.0 255.255.255.0 ...' 에서 정규화된 prefix 추출."""
    m = _CIDR_RE.search(text or "")
    if not m:
        return None
    net, prefix, mask = m.group(1), m.group(2), m.group(3)
    if prefix:
        return f"{net}/{prefix}"
    if mask:
        return f"{net}/{_mask_to_prefix(mask)}"
    return net


def _mask_to_prefix(mask):
    try:
        return sum(bin(int(o)).count("1") for o in mask.split("."))
    except ValueError:
        return 32


# target prefix -> 이 alert가 가리키는 구성요소의 종류.
# component_id는 '취소 대상을 찾는 키'이므로 prefix를 떼고 뒤쪽 실체만 남긴다 —
# 'shutdown:Et1'(명령)과 'link:Et1'(syslog)이 같은 Et1 하나를 가리켜야 한다.
_INTERFACE_TARGET_PREFIXES = ("interface", "shutdown", "noshut", "link")


def _component_of(target):
    """target -> component_id. 'shutdown:Et1' -> 'Et1', 'vlan:100' -> 'vlan:100'.

    인터페이스 계열만 prefix를 벗긴다. VLAN/route/peer는 prefix가 붙어 있어야
    'vlan:100'과 'route:100'이 섞이지 않는다.
    """
    if not target:
        return None
    prefix, _, rest = target.partition(":")
    if not rest or rest == "?":
        return None
    if prefix.lower() in _INTERFACE_TARGET_PREFIXES:
        return rest
    return target


def _alert(device, severity, type_, message, raw_line, target=None, alert_id=None,
           component_id=None):
    return {
        # alert_id: UI가 '이 경고를 지워라'를 받았을 때 어느 토스트/행인지 찾는 키.
        "alert_id": alert_id,
        "device": device,
        "severity": severity,
        "type": type_,
        "message": message,
        "raw_line": raw_line.strip(),
        "target": target,
        "component_id": component_id if component_id is not None else _component_of(target),
        "ts": time.strftime("%H:%M:%S"),
    }
