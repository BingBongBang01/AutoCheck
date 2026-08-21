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


# Cisco `show cdp neighbors` 는 한 이웃이 두 줄로 쪼개지는 경우가 흔하다:
#     Switch2
#                      Gig 1/0/1         151     R S I     WS-C3750  Gig 1/0/2
# 그래서 LLDP 표와 같은 파서로는 읽을 수 없다. 지금은 LLDP 만 쓰고(두 벤더 모두 지원한다),
# CDP 는 필요해질 때 별도 함수로 추가한다 — 반쯤 동작하는 파서를 두면 '이웃이 일부만 보인다'가
# 되고 그것은 구성도에서 가장 나쁜 실패 방식이다.
