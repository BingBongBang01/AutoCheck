"""show lldp neighbors / show cdp neighbors — 이웃 장비와 포트를 읽는다.

네트워크 구성도(engine/topology_builder.py)의 유일한 연결 정보 출처다. 이 표가 없으면
'무엇이 무엇에 붙어 있는지'를 알 방법이 없다.

Arista/Cisco 의 `show lldp neighbors` 출력 형태(실제 수집 로그에서 가져옴):

    Last table change time   : 8 days, 17:13:19 ago
    Number of table inserts  : 6
    ...
    Port Neighbor Device ID Neighbor Port ID TTL
    ---- ------------------ ---------------- ---
    Et1  Core2              Ethernet1        120
    Et3  Agg1               Ethernet3        120

머리말의 `Last table change time` / `Number of table ...` 요약 줄과 헤더·구분선을 이웃으로
잘못 읽지 않는 것이 이 파서의 일 대부분이다. 그래서 '4열 이상 + 마지막 열이 숫자(TTL)'라는
구조를 요구한다 — 열 위치(칼럼 정렬)에 의존하면 벤더/장비마다 깨진다.

**인터페이스명은 원문 그대로 둔다.** `Et1` ↔ `Ethernet1` 정규화는 호출부(빌더)가
engine.baseline_store.normalize_interface() 로 한 곳에서 한다 — 그 함수가 이미 그 축의
단일 출처이고, 파서마다 각자 정규화하면 규칙이 갈린다.
"""
import re

# 이웃 한 줄: 로컬포트 이웃장비 이웃포트 ... TTL(숫자)
# 이웃 장비 ID 에 공백이 들어가는 경우(설명형 system name)가 있어 중간을 느슨하게 잡고,
# **마지막 열이 숫자(TTL)** 인 것을 이 표의 서명으로 쓴다.
_NEIGHBOR_LINE_RE = re.compile(
    r'^(?P<local>\S+)\s+(?P<middle>.+?)\s+(?P<ttl>\d+)\s*$')
# 표 머리말/요약 줄 — 콜론으로 값을 적는 형태는 전부 이웃이 아니다.
_SUMMARY_LINE_RE = re.compile(r'^\s*\S[^:]*:\s')
_SEPARATOR_RE = re.compile(r'^[\s\-=+]+$')
# 헤더 줄('Port Neighbor Device ID ...')
_HEADER_RE = re.compile(r'^\s*(?:Port|Local\s+Int|Device\s+ID|Local\s+Intf)\b', re.IGNORECASE)
# 인터페이스처럼 생긴 토큰 — 로컬 포트 열의 최소 조건.
_PORT_LIKE_RE = re.compile(r'^[A-Za-z][\w./\-]*\d[\w./\-]*$')


def parse_lldp_neighbors(raw_output):
    """반환: [{"local_port", "neighbor_device", "neighbor_port"}] (표에 나온 순서대로)

    같은 로컬 포트에 이웃이 여러 개 보이면(허브를 거친 경우 등) 전부 남긴다 — 여기서 줄이면
    '왜 링크가 안 보이는지'를 위에서 알 수 없다.
    """
    out = []
    for line in (raw_output or "").splitlines():
        stripped = line.strip()
        if not stripped or _SEPARATOR_RE.match(stripped) or _HEADER_RE.match(stripped):
            continue
        if _SUMMARY_LINE_RE.match(stripped):
            continue        # 'Last table change time : ...' 류
        m = _NEIGHBOR_LINE_RE.match(stripped)
        if not m:
            continue
        local = m.group("local")
        if not _PORT_LIKE_RE.match(local):
            continue        # 포트명처럼 생기지 않았다 — 표 밖의 줄이다
        parts = m.group("middle").split()
        if len(parts) < 2:
            continue        # 이웃 장비명과 이웃 포트가 둘 다 있어야 링크다
        # 이웃 포트는 마지막 토큰, 장비명은 그 앞 전부(공백 포함 이름을 허용).
        neighbor_port = parts[-1]
        neighbor_device = " ".join(parts[:-1]).strip()
        if not neighbor_device:
            continue
        out.append({"local_port": local,
                    "neighbor_device": neighbor_device,
                    "neighbor_port": neighbor_port})
    return out


# ---------- Cisco: 표 대신 상세(detail) 출력을 읽는다(IMPROVEMENT_PLAN 4-5) ----------
# Cisco `show cdp neighbors`(표)는 한 이웃이 두 줄로 쪼개지는 경우가 흔하다:
#     Switch2
#                      Gig 1/0/1         151     R S I     WS-C3750  Gig 1/0/2
# 열 위치로 읽으면 반쯤만 맞는다 — 반쯤 동작하는 파서는 '이웃이 일부만 보인다'가 되고 그것은 구성도에서 가장 나쁜
# 실패 방식이다. 그래서 Cisco 에는 이웃마다 '항목: 값' 줄로 나오는 detail 을 수집하고(engine/vendor_commands) 그것을
# 읽는다. Cisco 는 CDP 가 기본이고 LLDP 는 켜야 돈다 — 둘 다 읽고, 같은 링크는 빌더가 양 끝으로 접는다.
# 아래 형식은 Cisco 명령 레퍼런스의 예시로 맞췄고 실제 장비 출력으로는 확인하지 않았다.
_CDP_DEVICE_RE = re.compile(r"^\s*Device ID\s*:\s*(.+?)\s*$", re.IGNORECASE)
_CDP_SYSTEM_NAME_RE = re.compile(r"^\s*System Name\s*:\s*(\S+)", re.IGNORECASE)
_CDP_PORTS_RE = re.compile(r"^\s*Interface\s*:\s*([^,]+?)\s*,\s*Port ID \(outgoing port\)\s*:\s*(\S+)", re.IGNORECASE)
_LLDP_DETAIL_FIELD_RE = re.compile(r"^\s*(Local Intf|Local Port id|Chassis id|Port id|System Name)\s*:\s*(.*?)\s*$",
                                   re.IGNORECASE)
_NOT_ADVERTISED_RE = re.compile(r"^-?\s*not advertised$", re.IGNORECASE)


def parse_cdp_neighbors_detail(raw_output):
    """`show cdp neighbors detail`(IOS·IOS-XE·NX-OS) -> parse_lldp_neighbors 와 같은 모양.

    이웃마다 'Device ID:' 로 시작하는 묶음이다:
        Device ID: SW2.lab.local
        Interface: GigabitEthernet1/0/1,  Port ID (outgoing port): GigabitEthernet1/0/2
    NX-OS 는 Device ID 에 일련번호를 괄호로 붙이고('N9K-2(FDO21120U8N)') System Name 을 따로 준다 — 있으면 그것을 쓴다.
    """
    out, current = [], None

    def flush():
        if current and current.get("local_port") and current.get("neighbor_port") and current.get("neighbor_device"):
            out.append({key: current[key] for key in ("local_port", "neighbor_device", "neighbor_port")})

    for line in (raw_output or "").splitlines():
        m = _CDP_DEVICE_RE.match(line)
        if m:
            flush()
            current = {"neighbor_device": re.sub(r"\([^)]*\)$", "", m.group(1)).strip()}
            continue
        if current is None:
            continue
        m = _CDP_SYSTEM_NAME_RE.match(line)
        if m:
            current["neighbor_device"] = m.group(1)
            continue
        m = _CDP_PORTS_RE.match(line)
        if m:
            current["local_port"], current["neighbor_port"] = m.group(1).strip(), m.group(2).strip()
    flush()
    return out


def parse_lldp_neighbors_detail(raw_output):
    """`show lldp neighbors detail`(IOS·IOS-XE·NX-OS) -> parse_lldp_neighbors 와 같은 모양.

    이웃마다 'Local Intf:'(IOS)·'Local Port id:'(NX-OS), 'Chassis id:', 'Port id:', 'System Name:' 줄이 한 묶음이다.
    같은 항목이 다시 나오면 다음 이웃으로 본다(NX-OS 는 구분선 없이 이어진다). System Name 이 없으면(서버 등)
    Chassis id 를 장비 이름으로 쓴다 — 미등록 이웃으로라도 남긴다.
    """
    out, current = [], {}

    def flush():
        local = current.get("local intf") or current.get("local port id")
        port = current.get("port id")
        device = current.get("system name") or current.get("chassis id")
        if local and port and device:
            out.append({"local_port": local, "neighbor_device": device, "neighbor_port": port})

    for line in (raw_output or "").splitlines():
        m = _LLDP_DETAIL_FIELD_RE.match(line)
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2)
        if key in current:
            flush()
            current = {}
        if value and not _NOT_ADVERTISED_RE.match(value):
            current[key] = value
        else:
            current.setdefault(key, "")
    flush()
    return out
