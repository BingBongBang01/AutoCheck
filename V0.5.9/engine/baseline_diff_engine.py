"""BaselineDiffEngine — 실시간 CLI 스트림 한 줄씩을 Baseline과 대조해 이상 징후를 판정.

입력은 CRTStreamWatcher가 넘겨주는 차분 텍스트(여러 줄일 수 있음)이고, 출력은 UI 토스트로 그대로
쓸 수 있는 alert dict 리스트다.

    {"device": "Core1", "severity": "CRITICAL", "type": "CONFIG_REMOVED",
     "message": "Baseline 등록 VLAN 100 삭제 명령 감지!", "raw_line": "no vlan 100"}

판정 전에 줄을 **'작업자가 입력한 명령'과 '장비가 출력한 텍스트'로 가른다**(classify_line).
이 구분이 없던 동안 세션 로그의 출력이 명령으로 읽혀 두 방향의 사고가 났다:

  * 오탐 — `show reload cause` 출력의 머리글 `Reload Cause:` 가 CRITICAL '위험 명령 실행'으로,
    `?` 자동완성 도움말의 `  reload   Reboot the system` 이 같은 경고로 잡혔다.
  * **오취소(더 심각)** — `show running-config` 출력에 들어 있는 `   no shutdown` / `vlan 200`
    줄이 복구 이벤트로 읽혀, 작업자가 실제로 낸 CRITICAL 경고를 '복구됨'으로 지웠다.
    설정을 한 번 들여다보는 것이 감시를 무력화하는 경로였다.

가르는 근거는 프롬프트다. SecureCRT 세션 로그에서 작업자 입력은 **항상 프롬프트와 같은 줄**에
있고(`Core1(config-if-Et1)#shutdown`), 장비 출력에는 프롬프트가 없다. 덤으로 config 모드 문맥을
프롬프트 괄호에서 직접 읽을 수 있다 — `interface Ethernet1` 이라는 줄을 따라다니는 것보다
훨씬 안전하다(그 줄은 running-config 출력에도 수십 개 들어 있어 문맥을 오염시켰다).

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
import threading
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
# '앞선 경고를 취소하는' type 들 — _CONDITION_MAP 에서 파생시켜 두 곳이 갈라지지 않게 한다.
_RECOVERY_ALERT_TYPES = tuple(t for t, (_cond, is_recovery) in _CONDITION_MAP.items() if is_recovery)

# ---------- 줄 분류: 작업자 입력(COMMAND) vs 장비 출력(OUTPUT) ----------
COMMAND = "COMMAND"
OUTPUT = "OUTPUT"

# 프롬프트로 시작하는 줄만 입력이다. **선행 공백을 허용하지 않는 것이 핵심**이다 —
# 도움말/running-config/테이블 출력은 거의 전부 들여쓰여 있거나 프롬프트가 없다.
#   Core1#show version              -> COMMAND 'show version'
#   Core1(config-if-Et1)#shutdown   -> COMMAND 'shutdown', mode 'config-if-Et1'
#   Core1#                          -> COMMAND ''(엔터만 침) — 판정 대상 아님
#   '  reload   Reboot the system'  -> OUTPUT (도움말)
#   'Reload Cause:'                 -> OUTPUT (show 출력 머리글)
#   '   no shutdown'                -> OUTPUT (running-config 덤프)
# ﻿: SecureCRT 로그 첫 줄에 BOM 이 붙는다.
_PROMPT_LINE_RE = re.compile(
    r'^﻿?(?P<host>[A-Za-z][\w.\-]{0,62})(?:\((?P<mode>[^)]{0,64})\))?(?P<sep>[#>])(?P<cmd>.*)$')
# 프롬프트 괄호에서 인터페이스를 뽑는다: 'config-if-Et1' -> 'Et1', 'config-if-Po10' -> 'Po10'
_MODE_IFACE_RE = re.compile(r'^config-if-(.+)$', re.IGNORECASE)
_MODE_BGP_RE = re.compile(r'^config-router-bgp\b', re.IGNORECASE)
# config session(스테이징) 모드: 'config-s-reset' / 'config-s-reset-if-Et1'.
# 여기서 입력한 설정은 commit 하기 전까지 장비에 적용되지 않는다 — 실제 세션 로그에서
# 작업자가 `conf session reset` 안에서 `no vlan 4093` 을 쳤고, 예전에는 그것이 즉시 CRITICAL
# '삭제 명령 감지'로 올라갔다(아직 아무것도 지워지지 않았는데).
_MODE_SESSION_RE = re.compile(r'^config-s-(?P<name>.+?)(?:-if-(?P<iface>[^-].*))?$', re.IGNORECASE)
# 세션 확정/폐기. 세션 안에서 `commit` / `abort` 하거나, 밖에서 세션 이름을 지정해서 한다.
_COMMIT_RE = re.compile(r'^\s*commit\s*$', re.IGNORECASE)
_ABORT_RE = re.compile(r'^\s*abort\s*$', re.IGNORECASE)
_SESSION_ACTION_RE = re.compile(r'^\s*conf\w*\s+s\w*\s+(\S+)\s+(commit|abort)\b', re.IGNORECASE)
# 예정 변경의 심각도를 한 단계 낮춘다 — 사실이 아니라 의도이므로.
_STAGED_DEMOTION = {CRITICAL: MAJOR, MAJOR: WARNING, WARNING: WARNING}
# syslog 로 보이는 줄 — mnemonic(%LINEPROTO-5-UPDOWN) 또는 syslog 날짜 머리말.
_SYSLOG_SHAPE_RE = re.compile(
    r'(?:%[A-Za-z][\w\-]*-\d-[A-Za-z0-9_]+)'
    r'|(?:\b[A-Z][A-Z0-9]{2,}(?:-[A-Za-z0-9]+)*-\d-[A-Z0-9_]{3,}\b)'
    r'|^\s*\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\b')
# 출력이 '지금 벌어진 일'이 아니라 '과거 기록'인 명령들 — 이 출력에서 나온 경고는 history 로 둔다.
# `show logging` 은 며칠 전 링크 다운을 그대로 다시 뿌리므로, 지금 발생한 것으로 세면 안 된다.
_HISTORY_COMMAND_RE = re.compile(
    r'^\s*(?:sh(?:o|ow)?\w*)\s+(?:logging|log|tech-support|history|reload\s+cause|event-monitor)\b',
    re.IGNORECASE)


# 관측 카운터의 축. 'polled' 은 우리가 직접 물어본 것(engine/state_poller.py)으로,
# 작업자의 `terminal monitor` 설정과 무관하게 링크·인접·MLAG 판정 근거가 된다.
OBSERVED_KEYS = ("commands", "syslog", "output", "polled")
_EMPTY_OBSERVATION = dict.fromkeys(OBSERVED_KEYS, 0)


def classify_line(line):
    """한 줄을 (kind, payload, mode) 로 가른다.

    kind=COMMAND 이면 payload 는 프롬프트를 뗀 명령 문자열, mode 는 괄호 안 문자열(없으면 None).
    kind=OUTPUT  이면 payload 는 줄 전체, mode 는 None.

    판정을 못 한 줄은 **OUTPUT 으로 취급한다**(보수적). 그러면 놓치는 것은 생기지만 없는 일을
    만들어내지는 않는다 — 이 화면은 '정상'이라고 적힌 것을 근거로 점검을 끝내는 데 쓰이므로,
    두 오류의 비용이 대칭이 아니다.
    """
    m = _PROMPT_LINE_RE.match(line)
    if not m:
        return (OUTPUT, line, None)
    return (COMMAND, m.group("cmd").strip(), m.group("mode"))


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

# (패턴, 종류, 상태 캡처 그룹 번호). 세 번째 칸이 있는 이유는 아래 _DOWN_WORDS 주석 참고 —
# 방향(up/down)은 줄 전체가 아니라 '전이 결과'로 판정해야 한다.
_SYSLOG_PATTERNS = (
    (re.compile(r'BGP-\d-ADJCHANGE.*?(?:neighbor\s+)?(\d{1,3}(?:\.\d{1,3}){3}).*?\b(Down|Up)\b', re.IGNORECASE),
     "NEIGHBOR_STATE", 2),
    # Cisco/Arista의 실제 mnemonic은 %OSPF-5-ADJCHG다 — 예전 패턴은 ADJCHANGE만 봐서
    # OSPF 인접 변화를 전부 놓치고 있었다(BGP는 ADJCHANGE가 맞다).
    (re.compile(r'OSPF-\d-ADJCH(?:G|ANGE).*?\b(?:from|to)\b.*', re.IGNORECASE), "NEIGHBOR_STATE", None),
    (re.compile(r'LINEPROTO-\d-UPDOWN.*?Line protocol on Interface\s+(\S+?),\s*changed state to (\w+)', re.IGNORECASE),
     "LINK_STATE", 2),
    (re.compile(r'LINK-\d-CHANGED.*?Interface\s+(\S+?),\s*changed state to (\w+)', re.IGNORECASE),
     "LINK_STATE", 2),
    (re.compile(r'MLAG-\d-\w*STATE.*', re.IGNORECASE), "MLAG_STATE", None),
    # Arista/Cisco 의 실제 mnemonic 은 %SPANTREE-n-… 다. 'STP-\d-' 만 보던 예전 패턴은
    # 두 벤더 어느 쪽에도 매치되지 않아 STP 로그를 통째로 놓치고 있었다(SPANTREE 안의
    # 'TREE-6-' 은 'STP-' 가 아니다). STP- 형태도 남겨 둔다 — 벤더가 더 늘 수 있다.
    (re.compile(r'(?:SPANTREE|STP)-\d-\w+.*', re.IGNORECASE), "STP_CHANGE", None),
)
_DOWN_WORDS = ("down", "notconnect", "errdisabled", "inactive", "disabled")
_UP_WORDS = ("up", "active-full", "established", "full", "forwarding", "connected")
# 방향 판정에서 mnemonic 토큰을 빼기 위한 정규식.
#
# **%LINEPROTO-5-UPDOWN 은 up 이든 down 이든 항상 'down' 이라는 글자를 품고 있다.** 그래서
# 줄 전체로 방향을 재던 예전 로직에서는 링크 복구 syslog
#     %LINEPROTO-5-UPDOWN: Line protocol on Interface Ethernet1, changed state to up
# 가 DOWN 으로 읽혔다 — LINK_UP 이 한 번도 발행되지 않았고, 따라서 앞서 낸 LINK_DOWN
# 경고가 자동으로 해제되는 일도 없었다(작업자가 링크를 되살려도 CRITICAL 이 그대로 남는다).
# 이제 패턴이 잡은 상태 단어를 먼저 보고, 그것으로 판정이 안 될 때만 mnemonic 을 지운 줄로
# 되짚는다('changed state to administratively down' 처럼 단어 하나로는 부족한 경우).
_MNEMONIC_TOKEN_RE = re.compile(r'%?[A-Za-z][\w\-]*-\d-[A-Za-z0-9_]+')

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
        # {device: {"commands": n, "syslog": n, "output": n}} — '무엇을 볼 수 있었는가'.
        # 이게 필요한 이유: 링크/인접/STP 판정은 세션에 syslog 가 에코돼야만 가능하고
        # (SecureCRT 세션에 `terminal monitor` 가 안 걸려 있으면 한 줄도 오지 않는다),
        # 그 사실을 모르면 화면이 '변경 없음 = 정상'이라고 적어 버린다. 실제 워크스페이스의
        # CRT 세션 로그 60여 개에는 syslog 가 단 한 줄도 없었다 — 그 상태로 '이상 없음'을
        # 근거로 점검을 끝내는 것이 이 지표가 막으려는 일이다.
        self._observed = {}
        # {alert_id: 억제된 재발 횟수} — drain_repeats()로 호출부가 가져간다.
        self._repeats = {}
        # {(device, session): [{"alert": dict, "severity": 원래 심각도}]} — config session 안에서
        # 입력됐지만 아직 commit 되지 않은 변경. commit 을 보면 실제 경고로 승격한다.
        self._staged = {}
        # 감시 스레드가 analyze_stream()을 돌리는 동안 다른 스레드(점검 워커·JS 브릿지)가
        # reset_context()/open_conditions()를 부를 수 있다. 상태표와 문맥 dict를 제자리에서
        # 고치므로 락 없이는 사전 크기 변경 중 순회 같은 사고가 난다.
        self._lock = threading.RLock()

    # ---------- 진입점 ----------
    def analyze_stream(self, device, text):
        """차분 텍스트(여러 줄)를 분석해 alert 리스트 반환. 이상 없으면 빈 리스트.

        복구로 취소된 경고는 이 반환값에 들어가지 않는다 — drain_resolutions()로 따로 가져간다.
        """
        accepted = []
        with self._lock:
            # 줄 하나를 **끝까지 처리한 뒤** 다음 줄로 간다. 예전에는 전체를 판정해 모은 다음
            # 일괄로 중복 억제하고 그 다음 일괄로 상태를 추적했는데, 그러면 한 덩어리 안에
            # down -> up -> down 이 함께 들어왔을 때 상태 전이 순서가 무시된다(억제 판정이
            # 전부 끝난 뒤에야 상태가 움직이므로 두 번째 down 이 '중복'으로 버려진다).
            for raw_line in strip_ansi(text or "").splitlines():
                line = raw_line.rstrip()
                if not line.strip():
                    continue
                for alert in self._analyze_line(device, line):
                    if not self._accept(alert):
                        continue
                    # id는 중복 억제를 통과한 것에만 발급한다 — 버려진 alert의 id를 UI가 볼 일이 없다.
                    if not alert.get("alert_id"):
                        alert["alert_id"] = f"{device or 'unknown'}#{next(self._ids)}"
                    self._remember_alert_id(alert)
                    self._track_state(alert)
                    self._annotate_root_cause(alert)
                    accepted.append(alert)
        # 상태 추적이 끝난 뒤 걸러낸다 — 복구 alert도 state.close()를 거쳐야 하기 때문이다.
        # 복구 자체는 '경고'가 아니므로 이력/토스트로 올리지 않고 resolution으로만 흘린다.
        return [a for a in accepted if not a.pop("_is_recovery", False)]

    def ingest_state_events(self, device, events):
        """능동 폴링(engine/state_poller.py)이 관측한 상태 전이를 판정 파이프라인에 넣는다.

        왜 별도 입력구가 필요한가: 세션 로그 tail 로는 링크·인접·MLAG 를 알 수 없다. 그건
        syslog 에서만 나오고, syslog 는 세션에 `terminal monitor` 가 걸려 있어야 에코된다 —
        실제 워크스페이스의 CRT 세션 로그 60여 개에는 syslog 가 한 줄도 없었다.

        왜 폴러가 alert 를 직접 만들지 않는가: 같은 (device, component_id) 축을 써야
        **출처가 달라도 서로 취소된다.** 폴링이 잡은 LINK_DOWN 을 나중에 도착한 syslog 의
        LINK_UP 이 해제하고, 그 반대도 된다. alert_id 발급·중복 억제·원인 주석·상태추적을
        모두 같은 경로로 통과시키는 것이 그 조건이다.

        events: [{"kind": "link"|"neighbor"|"mlag", "subject": str, "down": bool,
                  "detail": str, "source": str}]
                source 는 근거가 된 명령(예: 'show interfaces status') — raw_line 에 남는다.
        """
        accepted = []
        with self._lock:
            # 전이가 없어도(=조용한 폴링) 카운트한다. 조용한 폴링은 '아무것도 못 봤다'가 아니라
            # '봤고 정상이다'라는 근거이고, 그것이 링크/인접/MLAG 를 '판정 불가'에서 풀어 준다.
            self._note_observation(device, "polled")
            for event in events or []:
                alert = self._state_event_alert(device, event)
                if alert is None or not self._accept(alert):
                    continue
                if event.get("history"):
                    # 첫 폴링에서 이미 이상이던 것 — 판정에는 쓰되(상태표는 열린다) '방금 일어난
                    # 일'로는 세지 않는다. 랩/현장에는 원래 내려가 있는 포트가 흔해서, 감시를 켠
                    # 순간 그것들이 토스트로 쏟아지면 알림을 못 믿게 된다.
                    alert["history"] = True
                    alert["ts"] = "--:--:--"
                alert["alert_id"] = f"{device or 'unknown'}#poll{next(self._ids)}"
                self._remember_alert_id(alert)
                self._track_state(alert)
                self._annotate_root_cause(alert)
                accepted.append(alert)
        return [a for a in accepted if not a.pop("_is_recovery", False)]

    def _state_event_alert(self, device, event):
        kind = (event or {}).get("kind")
        subject = str((event or {}).get("subject") or "").strip()
        if not kind or not subject:
            return None
        down = bool(event.get("down"))
        detail = str(event.get("detail") or "").strip()
        source = str(event.get("source") or "polled").strip()
        baseline = self.baseline_store.get_device_baseline(device)
        # raw_line 은 '장비가 실제로 뭐라고 했는지'다. syslog 를 흉내내지 않고 근거가 된 명령과
        # 읽은 값을 그대로 적는다 — 화면에서 출처를 구분할 수 있어야 한다.
        raw_line = f"[{source}] {subject}: {detail or ('down' if down else 'up')}"

        if kind == "link":
            iface = normalize_interface(subject)
            known = iface in baseline["interfaces"]
            if down:
                return _alert(device, CRITICAL if known else MAJOR, "LINK_DOWN",
                              f"인터페이스 {iface} 상태 DOWN 관측 ({detail or 'down'})",
                              raw_line, target=f"link:{iface}", polled=True)
            return _alert(device, WARNING, "LINK_UP",
                          f"인터페이스 {iface} UP 복구 관측", raw_line,
                          target=f"link:{iface}", polled=True)

        if kind == "neighbor":
            known = subject in baseline["bgp_neighbors"]
            if down:
                return _alert(device, CRITICAL if known else MAJOR, "NEIGHBOR_DOWN",
                              f"라우팅 네이버 {subject} 인접 상실 관측 ({detail or 'down'})",
                              raw_line, target=f"peer:{subject}", polled=True)
            return _alert(device, WARNING, "NEIGHBOR_UP",
                          f"라우팅 네이버 {subject} 인접 복구 관측", raw_line,
                          target=f"peer:{subject}", polled=True)

        if kind == "mlag":
            if down:
                return _alert(device, CRITICAL, "MLAG_PEER_DOWN",
                              f"MLAG 이상 관측 ({detail or 'down'}) — 이중화 상실 / split-brain 위험",
                              raw_line, target="mlag:peer-link", polled=True)
            return _alert(device, WARNING, "MLAG_PEER_UP",
                          "MLAG 정상(active-full) 복구 관측", raw_line,
                          target="mlag:peer-link", polled=True)
        return None

    def drain_resolutions(self):
        """이번 분석에서 발생한 '경고 취소' 목록을 꺼내 비운다."""
        with self._lock:
            out, self._resolutions = self._resolutions, []
        return out

    def drain_repeats(self):
        """중복 억제로 화면에 안 올린 재발 횟수 — {alert_id: 누적 횟수}. 꺼내면 비운다.

        억제한 것을 조용히 버리면 '한 번 있었던 일'로 읽힌다. 링크가 30초간 열 번 흔들린 것과
        한 번 내려간 것은 다른 이야기이므로, 접은 몫은 원래 경고의 반복 횟수로 살려 둔다.
        """
        with self._lock:
            out, self._repeats = self._repeats, {}
        return out

    def open_conditions(self, device=None):
        with self._lock:
            return self.state.open_conditions(device)

    def reset_context(self, device=None):
        """세션이 끊겼거나 로그가 rotate됐을 때 config 모드 문맥을 비운다.

        상태표도 함께 비운다 — 세션이 끊긴 시점의 '열린 장애'는 새 세션에서 취소될 수 없고,
        남겨 두면 재접속 후 첫 `no shutdown`이 엉뚱한 옛 경고를 지운다.
        """
        with self._lock:
            if device is None:
                self._ctx.clear()
                self.state.reset()
                self._recent_causes.clear()
                self._staged.clear()
            else:
                self._ctx.pop(device, None)
                self.state.reset(device)
                for key in [k for k in self._recent_causes if k[0] == device]:
                    self._recent_causes.pop(key, None)
                for key in [k for k in self._staged if k[0] == device]:
                    self._staged.pop(key, None)

    # ---------- 상태 추적 / 취소 ----------
    def _track_state(self, alert):
        """alert 하나를 상태표에 반영. 복구 이벤트면 resolution을 쌓고 _is_recovery를 표시한다."""
        mapping = _CONDITION_MAP.get(alert.get("type"))
        if not mapping:
            return
        condition, is_recovery = mapping
        if alert.get("staged"):
            # 아직 적용되지 않은 예정 변경이다 — 존재하지 않는 장애를 '지금 문제'로 세워 두면
            # 되돌릴 복구 이벤트가 없어서 영구히 fail 로 남는다. commit 때 승격되면서 열린다.
            return
        component = alert.get("component_id")
        if not component:
            return
        device = alert.get("device")
        # 상태가 전이됐다 — 이 구성요소의 중복 억제 기록을 버려서 **다음 전이가 반드시 통과**하게
        # 한다. 안 버리면 down -> up -> down 이 10초 안에 벌어질 때 두 번째 down 이 '중복'으로
        # 버려지고, 조건이 다시 열리지 않아 링크가 내려가 있는데 화면은 '복구됨'이 된다.
        # 플랩은 억제할 대상이 아니라 세어야 할 대상이다.
        self._forget_recent(device, component, keep=self._dedupe_key(alert))
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
    def observations(self, device=None):
        """장비별로 지금까지 무엇을 봤는지 — {"commands", "syslog", "output"} 누적 카운트."""
        if device is not None:
            return dict(self._observed.get(device) or _EMPTY_OBSERVATION)
        return {d: dict(v) for d, v in self._observed.items()}

    def _note_observation(self, device, key):
        slot = self._observed.get(device)
        if slot is None:
            slot = self._observed[device] = dict(_EMPTY_OBSERVATION)
        slot[key] += 1

    def _analyze_line(self, device, line):
        ctx = self._ctx.setdefault(device, {"interface": None, "bgp": None,
                                            "last_command": "", "session": None})
        kind, body, mode = classify_line(line)
        if kind is COMMAND and body:
            self._note_observation(device, "commands")
        else:
            self._note_observation(device, "output")
            if _SYSLOG_SHAPE_RE.search(line):
                self._note_observation(device, "syslog")

        if kind is OUTPUT:
            # 장비 출력에는 설정 변경 명령이 있을 수 없다 — syslog 판정만 한다.
            return self._analyze_output(device, line, ctx)

        ctx["last_command"] = body
        self._apply_prompt_mode(ctx, mode)
        if not body:
            return []      # 프롬프트만 찍힌 줄(엔터) — 판정할 명령이 없다
        alerts = self._analyze_command(device, body, ctx, line)
        if alerts:
            # config session 안에서 입력한 설정 변경은 아직 적용되지 않았다 — '예정'으로 낮춰
            # 보여주고 commit 을 기다린다. commit/abort 자체가 만든 경고는 이미 확정된 것이라
            # 다시 스테이징하지 않는다.
            if ctx.get("session") and not any(a.get("_committed") for a in alerts):
                return self._stage_alerts(device, ctx["session"], alerts)
            for alert in alerts:
                alert.pop("_committed", None)
            return alerts
        # 명령으로 해석되지 않았다 — 프롬프트 뒤에 출력이 이어 붙은 경우(작업자가 프롬프트에
        # 있는 동안 syslog 가 끼어들면 같은 줄에 찍힌다). 출력 경로로 한 번 더 본다.
        return self._analyze_output(device, line, ctx)

    def _apply_prompt_mode(self, ctx, mode):
        """프롬프트 괄호로 config 문맥을 갱신한다 — 장비가 스스로 말한 문맥이라 오염되지 않는다.

        명령 텍스트(`interface Ethernet1`)로 잡은 문맥이 이미 있으면 그것을 우선한다:
        Arista 프롬프트는 `config-if-Et1` 처럼 축약형이라 정보가 덜하다. 프롬프트의 역할은
        **문맥을 세우는 것보다 거둬들이는 것**이다 — `Core1#` 이나 `Core1(config)#` 이 찍히면
        인터페이스 설정 모드를 벗어난 것이 확실하므로 낡은 문맥을 즉시 버린다.
        """
        if mode is None:
            # 특권 모드(`Core1#`) — config 모드 밖이다. 세션도 벗어났다(세션은 남아 있지만
            # 지금 치는 명령이 그 세션에 들어가지는 않는다).
            ctx["interface"] = None
            ctx["bgp"] = None
            ctx["session"] = None
            return
        session = _MODE_SESSION_RE.match(mode)
        if session:
            # 'config-s-reset-if-Et1' 처럼 세션 안에서 인터페이스에 들어간 경우도 문맥을 잡는다.
            ctx["session"] = session.group("name") or "session"
            iface_in_session = session.group("iface")
            if iface_in_session:
                if not ctx.get("interface"):
                    ctx["interface"] = normalize_interface(iface_in_session.strip())
                ctx["bgp"] = None
            else:
                ctx["interface"] = None
            return
        ctx["session"] = None
        iface = _MODE_IFACE_RE.match(mode)
        if iface:
            if not ctx.get("interface"):
                ctx["interface"] = normalize_interface(iface.group(1).strip())
            ctx["bgp"] = None
            return
        if _MODE_BGP_RE.match(mode):
            ctx["interface"] = None
            return
        # 'config' / 'config-s-reset' / 'config-vlan-100' 등 — 인터페이스 문맥은 확실히 아니다.
        ctx["interface"] = None

    def _analyze_output(self, device, line, ctx):
        """장비 출력 한 줄 — syslog 판정만 한다.

        syslog 모양이 아닌 출력(=`show mlag` 같은 조회 결과)에서는 **복구 이벤트를 받지 않는다.**
        비대칭인 이유: 틀린 DOWN 은 화면에 보이는 잡음이지만, 틀린 UP 은 이미 잡아 둔 진짜 장애를
        조용히 지운다. `show mlag` 의 'Peer-link status: up' 한 줄이 진행 중인 split-brain 경고를
        해제하면 감시가 무력화된 것을 아무도 모른다.
        """
        baseline = self.baseline_store.get_device_baseline(device)
        alerts = self._match_syslog(device, line, baseline)
        if not alerts:
            return []
        looks_syslog = bool(_SYSLOG_SHAPE_RE.search(line))
        if not looks_syslog:
            alerts = [a for a in alerts if a.get("type") not in _RECOVERY_ALERT_TYPES]
            for alert in alerts:
                # 조회 출력에서 읽은 상태다 — 근거는 유효하지만 '방금 발생'은 아니다.
                alert["from_show_output"] = True
        if _HISTORY_COMMAND_RE.match(ctx.get("last_command") or ""):
            # `show logging` 은 며칠 전 이벤트를 그대로 다시 뿌린다.
            for alert in alerts:
                alert["history"] = True
        return alerts

    # ---------- config session(스테이징) ----------
    def _stage_alerts(self, device, session, alerts):
        """config session 안의 설정 변경을 '예정'으로 낮춘다.

        commit 전까지 장비에는 아무 일도 일어나지 않았다. 그래서 세 가지를 다르게 다룬다:
          * 심각도를 한 단계 낮춘다(사실이 아니라 의도다).
          * 상태추적(StateTracker)을 열지 않는다 — 열면 존재하지 않는 장애가 '지금 문제'로 남고,
            되돌릴 복구 이벤트도 없어서 영구히 fail 로 남는다.
          * 원래 심각도로 따로 보관해 두고 commit 을 보면 실제 경고로 승격한다. 승격하지 않으면
            '예정' 한 줄만 남고 실제 적용은 아무도 모르게 지나간다.

        보관하는 것은 **반환하는 dict 그 자체**다. analyze_stream()이 나중에 alert_id 를 채워
        넣으므로 같은 객체를 들고 있으면 abort 때 그 id 로 화면에서 해제할 수 있다.
        """
        staged = self._staged.setdefault((device, session), [])
        for alert in alerts:
            original = alert.get("severity") or WARNING
            alert["severity"] = _STAGED_DEMOTION.get(original, WARNING)
            alert["staged"] = True
            alert["staged_session"] = session
            alert["message"] = f"[예정 · 세션 {session}] {alert.get('message', '')}".strip()
            staged.append({"alert": alert, "severity": original})
        return alerts

    def _commit_session(self, device, session, line):
        """세션 확정 — 보관해 둔 예정 변경을 실제 경고로 다시 낸다.

        새 dict 로 낸다: alert_id 를 새로 받아야 화면에서 '방금 일어난 일'로 보인다. 예정 경고가
        승격을 억제하지 않는 것은 _dedupe_key() 가 staged 를 키에 넣기 때문이다.
        """
        pending = self._staged.pop((device, session), [])
        out = []
        for entry in pending:
            source = entry["alert"]
            promoted = dict(source, severity=entry["severity"], alert_id=None,
                            staged=False, committed_session=session, raw_line=line.strip(),
                            _committed=True)
            promoted.pop("staged_session", None)
            promoted["message"] = (f"[세션 {session} 확정] "
                                   + _strip_staged_prefix(source.get("message", "")))
            out.append(promoted)
        return out

    def _abort_session(self, device, session):
        """세션 폐기 — 예정이었던 변경은 일어나지 않았다. 화면에 올린 '예정' 경고를 해제한다.

        지우지 않고 해제로 남기는 이유는 이력의 다른 취소와 같다: '위험한 변경을 준비했다가
        되돌렸다'는 사실 자체가 점검 이력이다.
        """
        pending = self._staged.pop((device, session), [])
        for entry in pending:
            alert_id = (entry["alert"] or {}).get("alert_id")
            if not alert_id:
                continue    # 중복 억제로 화면에 오르지 않았다 — 해제할 것이 없다
            self._resolutions.append({
                "alert_id": alert_id,
                "device": device,
                "component_id": entry["alert"].get("component_id"),
                "condition": "staged_change",
                "opened_by": entry["alert"].get("raw_line", ""),
                "resolved_by": f"세션 {session} 폐기(abort) — 적용되지 않았습니다",
                "duration_sec": None,
                "ts": time.strftime("%H:%M:%S"),
            })

    def _analyze_command(self, device, body, ctx, line):
        """작업자가 입력한 명령 한 줄. body 는 프롬프트를 뗀 명령, line 은 화면/이력에 남길 원문."""
        baseline = self.baseline_store.get_device_baseline(device)
        out = []

        # --- config session 확정 / 폐기 ---
        # 다른 무엇보다 먼저 본다. `commit` 은 그 자체로는 아무 패턴에도 안 걸리지만, 이 순간이
        # 예정 변경이 실제 변경으로 바뀌는 시점이다.
        action = _SESSION_ACTION_RE.match(body)
        if action:
            target_session, verb = action.group(1), action.group(2).lower()
            if verb == "commit":
                return self._commit_session(device, target_session, line)
            self._abort_session(device, target_session)
            return out
        if ctx.get("session"):
            if _COMMIT_RE.match(body):
                return self._commit_session(device, ctx["session"], line)
            if _ABORT_RE.match(body):
                self._abort_session(device, ctx["session"])
                ctx["session"] = None
                return out

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

        # 명령으로 해석되지 않았다. syslog 판정은 호출부(_analyze_line)가 출력 경로로 돌린다 —
        # 여기서 같이 하면 '명령'과 '출력'의 구분이 다시 흐려진다.
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

        for pattern, kind, state_group in _SYSLOG_PATTERNS:
            m = pattern.search(line)
            if not m:
                continue
            is_down = _is_down(line, m, state_group)
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
    @staticmethod
    def _dedupe_key(alert):
        # staged 를 키에 넣는다 — '예정'과 '확정'은 같은 (장비, type, target)이지만 다른 사건이다.
        # 넣지 않으면 세션에서 예정으로 잡힌 변경이 그 직후의 실제 변경(commit 승격이든, 세션을
        # 버리고 밖에서 다시 친 것이든)을 dedupe 창 안에서 통째로 삼킨다.
        return (alert["device"], alert["type"], alert.get("target"), bool(alert.get("staged")))

    def _accept(self, alert):
        """같은 이벤트의 에코 중복만 접는다 — 재발(플랩)은 접지 않는다.

        억제한 몫은 원래 경고의 반복 횟수(_repeats)로 남긴다. 조용히 버리면 30초간 열 번
        흔들린 링크가 '한 번 내려갔다'로 읽힌다.
        """
        key = self._dedupe_key(alert)
        now = self._clock()
        entry = self._recent.get(key)
        if entry is not None and (now - entry["ts"]) < self.dedupe_window:
            entry["repeat"] += 1
            if entry.get("alert_id"):
                self._repeats[entry["alert_id"]] = entry["repeat"]
            return False
        self._recent[key] = {"ts": now, "repeat": 0, "alert_id": None}
        if len(self._recent) > 2000:
            cutoff = now - self.dedupe_window
            self._recent = {k: v for k, v in self._recent.items() if v["ts"] >= cutoff}
        return True

    def _remember_alert_id(self, alert):
        """방금 발급한 alert_id를 억제 기록에 붙인다 — 접힌 재발을 어느 경고에 더할지 알아야 한다."""
        entry = self._recent.get(self._dedupe_key(alert))
        if entry is not None and entry.get("alert_id") is None:
            entry["alert_id"] = alert.get("alert_id")

    def _forget_recent(self, device, component, keep=None):
        """상태가 전이된 구성요소의 억제 기록을 버린다(방금 발행한 키는 남긴다)."""
        for key in [k for k in self._recent
                    if k[0] == device and k != keep and _component_of(k[2]) == component]:
            self._recent.pop(key, None)


# ---------- 헬퍼 ----------
# 프롬프트 인식은 이제 classify_line()의 _PROMPT_LINE_RE 하나가 담당한다. 예전에는 여기에
# '프롬프트가 있으면 떼는' 별도 정규식이 있었는데, 선행 공백을 허용해서 들여쓰인 출력까지
# 명령처럼 다뤄졌다 — 두 개를 두면 '무엇이 명령인가'의 기준이 갈린다.


def _strip_staged_prefix(message):
    """'[예정 · 세션 X] …' 머리말을 뗀다 — 승격된 경고에는 그 머리말이 남아 있으면 안 된다."""
    if message.startswith("[예정"):
        _, _, rest = message.partition("] ")
        return rest or message
    return message


def _is_down(line, match, state_group=None):
    """이 syslog 줄이 '문제 발생' 방향인지 — 전이 결과 상태를 우선 본다.

    줄 전체로 재면 안 된다: mnemonic 자체에 'down'/'up' 이 들어 있는 경우가 흔하고
    (%LINEPROTO-5-**UPDOWN**), 그러면 복구가 장애로 뒤집힌다.
    """
    if state_group:
        try:
            state = (match.group(state_group) or "").strip().lower()
        except IndexError:
            state = ""
        if state:
            if any(word in state for word in _DOWN_WORDS):
                return True
            if any(state.startswith(word) for word in _UP_WORDS):
                return False
            # 'administratively' 처럼 단어 하나로는 방향을 못 정하는 경우 — 아래로 내려간다.
    stripped = _MNEMONIC_TOKEN_RE.sub(" ", line).lower()
    return any(word in stripped for word in _DOWN_WORDS)


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
           component_id=None, polled=False):
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
        # polled=True 는 '세션 로그에서 읽은 것이 아니라 우리가 직접 물어본 결과'라는 표시다.
        # 화면이 출처를 구분해야 한다 — 작업자가 친 명령의 결과와 주기 조회의 결과는 다르게 읽힌다.
        "polled": bool(polled),
        "ts": time.strftime("%H:%M:%S"),
    }
