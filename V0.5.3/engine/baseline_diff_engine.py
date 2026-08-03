"""BaselineDiffEngine — 실시간 CLI 스트림 한 줄씩을 Baseline과 대조해 이상 징후를 판정.

입력은 CRTStreamWatcher가 넘겨주는 차분 텍스트(여러 줄일 수 있음)이고, 출력은 UI 토스트로 그대로
쓸 수 있는 alert dict 리스트다.

    {"device": "Core1", "severity": "CRITICAL", "type": "CONFIG_REMOVED",
     "message": "Baseline 등록 VLAN 100 삭제 명령 감지!", "raw_line": "no vlan 100"}

'Stateful'인 이유 두 가지:
  1) `configure` / `interface Ethernet1` 처럼 문맥을 만드는 줄이 뒤 줄의 의미를 바꾼다.
     (`shutdown` 한 줄만 봐서는 어느 인터페이스인지 알 수 없다.)
  2) 같은 경고가 스트림에 반복 등장(터미널 에코 + 로그 재출력)하므로 짧은 시간 창 안의 중복은 접는다.

시간은 호출부에서 주입할 수 있게 clock 인자로 받는다(테스트 용이성).
"""
import re
import time

from core.ansi_sanitizer import strip_ansi
from engine.baseline_store import normalize_interface

CRITICAL = "CRITICAL"
MAJOR = "MAJOR"
WARNING = "WARNING"

# ---------- 설정 변경(명령어 입력) ----------
_NO_VLAN_RE = re.compile(r'^\s*no\s+vlan\s+([\d,\-\s]+)\s*$', re.IGNORECASE)
_NO_ROUTE_RE = re.compile(r'^\s*no\s+ip(?:v6)?\s+route\s+(.*)$', re.IGNORECASE)
_NO_INTERFACE_RE = re.compile(r'^\s*no\s+interface\s+(\S+)', re.IGNORECASE)
_NO_NEIGHBOR_RE = re.compile(r'^\s*no\s+neighbor\s+(\d{1,3}(?:\.\d{1,3}){3})', re.IGNORECASE)
_INTERFACE_CTX_RE = re.compile(r'^\s*interface\s+(\S+)\s*$', re.IGNORECASE)
_SHUTDOWN_RE = re.compile(r'^\s*shutdown\s*$', re.IGNORECASE)
_NO_SHUTDOWN_RE = re.compile(r'^\s*no\s+shutdown\s*$', re.IGNORECASE)
_ROUTER_BGP_RE = re.compile(r'^\s*router\s+bgp\s+(\d+)', re.IGNORECASE)
_NO_ROUTER_BGP_RE = re.compile(r'^\s*no\s+router\s+bgp\s+(\d+)', re.IGNORECASE)
_EXIT_RE = re.compile(r'^\s*(?:exit|end|!)\s*$', re.IGNORECASE)
_DESTRUCTIVE_RE = re.compile(
    r'^\s*(reload|write\s+erase|erase\s+startup-config|delete\s+flash|copy\s+\S+\s+startup-config)\b',
    re.IGNORECASE)
_CIDR_RE = re.compile(r'(\d{1,3}(?:\.\d{1,3}){3})(?:/(\d{1,2})|\s+(\d{1,3}(?:\.\d{1,3}){3}))?')

# ---------- 상태 변화(syslog 출력) ----------
_SYSLOG_PATTERNS = (
    (re.compile(r'BGP-\d-ADJCHANGE.*?(?:neighbor\s+)?(\d{1,3}(?:\.\d{1,3}){3}).*?\b(Down|Up)\b', re.IGNORECASE),
     "NEIGHBOR_STATE"),
    (re.compile(r'OSPF-\d-ADJCHANG[EF].*?\b(?:from|to)\b.*', re.IGNORECASE), "NEIGHBOR_STATE"),
    (re.compile(r'LINEPROTO-\d-UPDOWN.*?Line protocol on Interface\s+(\S+?),\s*changed state to (\w+)', re.IGNORECASE),
     "LINK_STATE"),
    (re.compile(r'LINK-\d-CHANGED.*?Interface\s+(\S+?),\s*changed state to (\w+)', re.IGNORECASE), "LINK_STATE"),
    (re.compile(r'MLAG-\d-\w*STATE.*', re.IGNORECASE), "MLAG_STATE"),
    (re.compile(r'STP-\d-\w+.*', re.IGNORECASE), "STP_CHANGE"),
)
_DOWN_WORDS = ("down", "notconnect", "errdisabled", "inactive", "disabled")


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

    # ---------- 진입점 ----------
    def analyze_stream(self, device, text):
        """차분 텍스트(여러 줄)를 분석해 alert 리스트 반환. 이상 없으면 빈 리스트."""
        alerts = []
        for raw_line in strip_ansi(text or "").splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                continue
            alerts.extend(self._analyze_line(device, line))
        return [a for a in alerts if self._accept(a)]

    def reset_context(self, device=None):
        """세션이 끊겼거나 로그가 rotate됐을 때 config 모드 문맥을 비운다."""
        if device is None:
            self._ctx.clear()
        else:
            self._ctx.pop(device, None)

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
            out.append(_alert(device, WARNING, "CONFIG_CHANGED",
                              f"인터페이스 {ctx['interface']} 활성화(no shutdown) 명령 감지",
                              line, target=f"noshut:{ctx['interface']}"))
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
                known = subject in baseline["bgp_neighbors"]
                if is_down:
                    return [_alert(device, CRITICAL if known else MAJOR, "NEIGHBOR_DOWN",
                                   f"라우팅 네이버 {subject} 인접관계 DOWN 감지!", line,
                                   target=f"peer:{subject}")]
                return [_alert(device, WARNING, "NEIGHBOR_UP",
                               f"라우팅 네이버 {subject} 인접관계 복구", line, target=f"peer:{subject}")]

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


def _alert(device, severity, type_, message, raw_line, target=None):
    return {
        "device": device,
        "severity": severity,
        "type": type_,
        "message": message,
        "raw_line": raw_line.strip(),
        "target": target,
        "ts": time.strftime("%H:%M:%S"),
    }
