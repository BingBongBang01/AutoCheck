"""실제 장비 출력 -> 골든 픽스처(IMPROVEMENT_PLAN 4-10).

파서는 실제 출력으로 시험해야 한다 — 상상으로 만든 벤더 출력에 맞춘 BGP/OSPF 파서가 실출력을 한 줄도 읽지 못한 일이
있었다(IMPROVEMENT_PLAN '지킬 것'). 하지만 실제 출력에는 고객의 장비 이름·주소·일련번호·계정·비밀이 들어 있어 그대로
저장소에 넣을 수 없고, 앱의 마스킹(engine/log_masking)은 값을 '*'로 덮어서 파서가 더는 읽지 못한다. 이 도구는 값을
같은 모양의 가짜로 바꾼다 — 파서가 그대로 읽고, 같은 값은 늘 같은 가짜가 된다:

  장비 이름   프롬프트·hostname·LLDP/CDP 이웃 -> DEVICE-1, DEVICE-2 …(FQDN 이면 DEVICE-n.example.net)
  IPv4        /24 마다 198.18.0.0/15(장비 시험용 예약 대역)의 /24 하나, 호스트 자리는 그대로 — 같은 서브넷은 같은
              서브넷으로 남는다. 넷마스크·와일드카드·0.0.0.0·루프백·멀티캐스트·링크로컬은 그대로 둔다
  IPv6        2001:db8::/32(문서용 대역)의 /64 마다 하나, 링크로컬은 fe80:: 안에서
  MAC         02:00:00:… (로컬 관리 주소) — 표기(점·콜론·하이픈, 대소문자)는 원래대로. 멀티캐스트·브로드캐스트는 그대로
  일련번호    같은 길이, 글자 자리는 X·숫자 자리는 숫자 — 'JPE19281234' -> 'XXX00000001'
  계정·도메인 user1 / example.net …
  설명·VLAN 이름·SNMP 위치/연락처  DESC-1 / VLAN_NAME-1 / LOCATION-1 / CONTACT-1
  배너        글자를 '*'로
  비밀        앱 마스킹 규칙 그대로 '*'(engine/log_masking.SECRET_KEYS — 해시·커뮤니티·키)

쓰기 전에 누출 검사를 한다 — 바꾼 원래 값이 어디에든 남았거나(이름은 'Core1-mgmt' 처럼 다른 낱말 속에 든 것도),
남은 IP·MAC·FQDN 이 가짜가 아니거나, 비밀 규칙에 걸리는 값이 남았으면 쓰지 않고 무엇이 몇째 줄에 남았는지 알린다.
도구가 모르는 식별자(주석·스크립트·자유 문구 속의 고객사 이름 등)는 사람이 봐야 한다 — --name-of·--word 로 더 바꿀
값을 주고, 커밋하기 전에 session.log 를 한 번 읽는다.

결과: tests/fixtures/golden/<이름>/
  session.log     가명화한 출력(줄바꿈은 원본 그대로 — CRLF 도 그대로 시험된다)
  expected.json   지금 파서가 이 출력에서 읽는 것: 플랫폼·명령 목록·장비 정보·보고서 점검 항목 판정·구성도 사실·로그 규칙
  fixture.json    출처 설명(플랫폼 지정, 바꾼 값의 종류별 수, 남겨 둔 낱말, 만든 시각) — 원래 값은 남기지 않는다
tests/test_golden_fixtures.py 가 픽스처마다 expected.json 과 지금 파서의 결과를 견주고, 남은 식별자를 다시 본다.

  python -m tools.promote_fixture promote <원본 로그> --name cisco_ios_core [--platform cisco_ios]
                                         [--name-of 장비이름 …] [--word 고객사명 …] [--keep 낱말 …] [--force]
                                         [--out <폴더>]   # 먼저 다른 폴더에 만들어 읽어 본다
  python -m tools.promote_fixture refresh [이름 …]   # 파서를 일부러 바꾼 뒤 expected.json 을 다시 만든다
  python -m tools.promote_fixture check              # 테스트와 같은 대조 + 남은 식별자 재검사

앱 데이터 폴더는 쓰지도 읽지도 않는다 — 앱 모듈을 import 하기 전에 AUTOCHECK_HOME 을 임시 폴더로 돌린다(사용자 사본
템플릿·로그 규칙이 기대 결과에 섞이지 않게). 원본 로그는 읽기만 한다.
"""
import argparse
import bisect
import datetime
import ipaddress
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = ROOT / "tests" / "fixtures" / "golden"
TEMPLATE_NAME = "inspection_report_template.yaml"
_FIXTURE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{2,60}$")

# ---------------------------------------------------------------------------- 가짜 값의 자리
_V4_POOLS = (ipaddress.ip_network("198.18.0.0/15"), ipaddress.ip_network("100.64.0.0/10"))
_V6_DOC = ipaddress.ip_network("2001:db8::/32")
_V6_LINK_LOCAL = ipaddress.ip_network("fe80::/10")
_MAC_BASE = 0x020000000000                  # 로컬 관리(locally administered) 유니캐스트
_DOMAINS = ("example.net", "example.org", "example.com")
_PSEUDO_DOMAIN_RE = re.compile(r"(?:^|\.)example\d*\.(?:net|org|com)$", re.IGNORECASE)
_PSEUDO_NAME_RE = re.compile(r"^(?:DEVICE|host)-\d+$")

# ---------------------------------------------------------------------------- 찾는 규칙
_IPV4_RE = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?!\w|\.\d)")
_MAC_RE = re.compile(r"\b[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}\b"
                     r"|\b(?:[0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}\b")
# engine/log_masking 의 domain_name 규칙과 같은 TLD 목록
_FQDN_RE = re.compile(r"\b(?:[A-Za-z0-9-]+\.)+(?:com|net|org|edu|gov|mil|int|io|co|kr|jp|cn|de|uk|info|biz|local"
                      r"|lan|corp|internal)\b", re.IGNORECASE)
_PROMPT_RE = re.compile(r"^([A-Za-z][\w.\-]{0,62})(?:\([^)\r\n]{0,40}\))?[#>]", re.MULTILINE)
_NAME_DECL_RES = (
    re.compile(r"^\s*(?:hostname|switchname)\s+(\S+)", re.IGNORECASE | re.MULTILINE),     # 설정
    re.compile(r"^!\s*device:\s*(\S+)", re.IGNORECASE | re.MULTILINE),                    # EOS running-config 머리 주석
    re.compile(r"^\s*(?:Hostname|Device name)\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE),  # EOS show hostname·NX-OS
)
_SERIAL_RES = (
    re.compile(r"\bserial(?:[ _-]?(?:number|num|no\.?|#))?\s*[:=]\s*([A-Za-z0-9][\w-]{3,})", re.IGNORECASE),
    re.compile(r"\bProcessor board ID\s+([A-Za-z0-9][\w-]{3,})", re.IGNORECASE),
    re.compile(r"\bSN:\s*([A-Za-z0-9][\w-]{3,})"),
    re.compile(r"Device ID\s*:\s*[^\s(]+\(([A-Za-z0-9]{6,})\)", re.IGNORECASE),          # NX-OS CDP: N9K-2(FDO2112…)
)
# 표 머리줄의 일련번호 열 — 뒤에 콜론이 아니라 열 간격(공백 둘 이상)이나 줄 끝이 와야 머리줄이다('Serial number: X' 는 위 규칙)
_SERIAL_COLUMN_RE = re.compile(r"(?:\bSerial(?:\s+(?:Number|No\.?|Num)|\s*#)|\bS/N)(?=\s{2,}|\s*$)", re.IGNORECASE)
_USER_RES = (
    re.compile(r"^\s*username\s+(\S+)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bby\s+([\w.@-]+)\s+on\s+(?:vty|console|tty|ssh|telnet)", re.IGNORECASE),  # Configured … by X on vty0
    re.compile(r"\buser(?:name)?\s*[:=]\s*['\"]?([\w.@-]+)", re.IGNORECASE),
)
_DOMAIN_RES = (
    re.compile(r"^\s*(?:ip\s+)?domain[ -]name\s+(?:vrf\s+\S+\s+)?(\S+)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*dns\s+domain\s+(\S+)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*FQDN\s*:\s*[^.\s]+\.(\S+)", re.IGNORECASE | re.MULTILINE),
)
_DESCRIPTION_RES = (
    re.compile(r"^[ \t]+description[ \t]+(.+?)[ \t]*\r?$", re.IGNORECASE | re.MULTILINE),    # 설정(인터페이스 아래)
    re.compile(r"^[ \t]*(?:Port )?Description[ \t]*:[ \t]*(.+?)[ \t]*\r?$", re.IGNORECASE | re.MULTILINE),
)
_VLAN_NAME_RES = (
    re.compile(r"^\d{1,4}\s+(\S+)\s+(?:active|suspend|act/\w+|sus/\w+)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^[ \t]+name[ \t]+(\S+)[ \t]*\r?$", re.IGNORECASE | re.MULTILINE),
)
_SNMP_RES = (
    ("LOCATION", re.compile(r"^\s*snmp-server\s+location\s+(.+?)[ \t]*\r?$", re.IGNORECASE | re.MULTILINE)),
    ("CONTACT", re.compile(r"^\s*snmp-server\s+contact\s+(.+?)[ \t]*\r?$", re.IGNORECASE | re.MULTILINE)),
)
# show interfaces status 의 Name 열(EOS·Cisco 는 설명을 열 너비로 잘라 적는다) — 파서(parsers/show_interfaces_status)의
# 상태 낱말과 같은 목록
_STATUS_ROW_RE = re.compile(r"^(\S+)[ \t]+(.*?)[ \t]+(?:connected|notconnect(?:ed)?|notconnec|disabled|errdisabled"
                            r"|err-disabled|sfpAbsent|xcvrAbsen)\b", re.IGNORECASE)
_BANNER_EOS_RE = re.compile(r"^banner (?:login|motd)[ \t]*\r?\n(.*?)^EOF[ \t]*\r?$", re.MULTILINE | re.DOTALL)
_BANNER_IOS_RE = re.compile(r"^banner (?:motd|login|exec|incoming)[ \t]+(\^C|\S)(.*?)\1", re.MULTILINE | re.DOTALL)
# 이름이 아니라 흔한 낱말이라 바꾸지 않는 값(설명·VLAN 이름). --keep 으로 더한다
_GENERIC_WORDS = frozenset({"default", "trunk", "access", "uplink", "downlink", "mgmt", "management", "unused",
                            "shutdown", "reserved", "native", "voice", "data", "test", "none"})
_DEFAULT_VLAN_RE = re.compile(r"^VLAN\d{1,4}$", re.IGNORECASE)
# 인터페이스 이름 — LLDP 상세의 'Port Description: Ethernet1' 을 설명으로 잡아 모든 포트 이름을 바꾸면 파서가 깨진다
_INTERFACE_RE = re.compile(r"^(?:Ethernet|Eth|Et|GigabitEthernet|Gi|TenGigabitEthernet|Te|TwentyFiveGigE|Twe"
                           r"|FortyGigabitEthernet|Fo|HundredGigE|Hu|FastEthernet|Fa|Port-Channel|Po|Vlan|Management|Ma"
                           r"|mgmt|Loopback|Lo|Tunnel|Tu|Vxlan|Vx)\s?\d+(?:[/.:]\d+)*$", re.IGNORECASE)
_ADDRESS_LIKE_RE = re.compile(r"^(?:[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}|[0-9a-f]{2}(?:[:-][0-9a-f]{2}){5}"
                              r"|\d{1,3}(?:\.\d{1,3}){3})$", re.IGNORECASE)
_NOT_ADVERTISED_RE = re.compile(r"^-?\s*not advertised$", re.IGNORECASE)


class LeakError(Exception):
    """가명화한 결과에 원래 값이 남았다 — 픽스처를 쓰지 않는다."""

    def __init__(self, leaks):
        self.leaks = leaks
        super().__init__("가명화한 결과에 남은 값:\n" + "\n".join(
            f"  - {leak['what']}: {leak['value']!r} (줄 {', '.join(map(str, leak['lines'][:10]))})" for leak in leaks))


def _special_v4(ip):
    """그대로 둘 IPv4 — 누구의 것도 아닌 값(넷마스크·와일드카드·특수 대역)."""
    n = int(ip)
    inverted = n ^ 0xFFFFFFFF
    netmask = ((inverted + 1) & inverted) == 0          # 앞에서부터 1: 255.255.255.0
    wildcard = ((n + 1) & n) == 0                        # 뒤에서부터 1: 0.0.0.255
    return netmask or wildcard or ip.is_loopback or ip.is_multicast or ip.is_link_local or ip.is_reserved


def _special_mac(value):
    """그대로 둘 MAC — 멀티캐스트(LLDP 01:80:c2 …)·브로드캐스트·0."""
    n = int(re.sub(r"[^0-9A-Fa-f]", "", value), 16)
    return n in (0, 0xFFFFFFFFFFFF) or bool((n >> 40) & 1)


def _special_v6(ip):
    return ip.is_unspecified or ip.is_loopback or ip.is_multicast


def _shaped(original, n):
    """original 과 같은 길이·같은 글자 종류 — 글자 자리는 X(소문자면 x), 숫자 자리에 n 을 앞에 0 을 채워 넣는다.
    숫자 자리가 모자라면 글자 자리에 26진수로 넣는다."""
    out = ["x" if ch.islower() else "X" if ch.isalpha() else ch for ch in original]
    digits = [i for i, ch in enumerate(original) if ch.isdigit()]
    if digits and len(str(n)) <= len(digits):
        for index, digit in zip(digits, str(n).zfill(len(digits))):
            out[index] = digit
        return "".join(out)
    for index in reversed([i for i, ch in enumerate(original) if ch.isalpha()]):
        n, rest = divmod(n, 26)
        out[index] = chr(ord("A") + rest)
    return "".join(out)


def _line_numbers(text, indexes):
    starts = [0] + [m.end() for m in re.finditer(r"\n", text)]
    return sorted({bisect.bisect_right(starts, index) for index in indexes})


def _bounded(body, inside_words=False):
    """낱말 경계 — 기본은 앞뒤가 글자·숫자·_·- 가 아닐 때, inside_words 면 '-'·'_'·'.' 로 이어진 낱말 속에서도
    ('ABC-uplink', 'core1_old')."""
    return (rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])" if inside_words
            else rf"(?<![\w-])(?:{body})(?![\w-])")


def _replace_exact(text, mapping, *, ci=False, inside_words=False):
    """mapping 의 원래 값을 가짜로 — 긴 것부터 한 번에(바꾼 값을 다시 바꾸지 않는다). ci 면 mapping 의 키는 소문자다."""
    if not mapping:
        return text
    body = "|".join(re.escape(key) for key in sorted(mapping, key=len, reverse=True))
    pattern = re.compile(_bounded(body, inside_words), re.IGNORECASE if ci else 0)
    return pattern.sub(lambda m: mapping[m.group(0).lower() if ci else m.group(0)], text)


class Pseudonymizer:
    """같은 값은 늘 같은 가짜로 바꾼다. names·words 는 도구가 찾지 못하는 값을 사람이 더한 것, keep 은 바꾸지 않을 낱말."""

    def __init__(self, names=(), words=(), keep=()):
        from engine import log_masking
        self._log_masking = log_masking
        self._ipv6_re = re.compile(log_masking._IPV6)
        self.keep = {value.lower() for value in keep}
        self.names, self.hosts, self.domains, self.words = {}, {}, {}, {}      # 키는 소문자
        self.serials, self.users, self.texts = {}, {}, {}                     # 키는 원래 값 그대로
        self.v4_nets, self.v6_nets, self.v6, self.macs = {}, {}, {}, {}
        self.produced = {"ipv4": set(), "ipv6": set(), "mac": set()}
        for name in names:
            self._name(name)
        for word in words:
            if word and word.lower() not in self.keep:
                self.words.setdefault(word.lower(), f"WORD-{len(self.words) + 1}")

    # ---- 가짜 만들기 ------------------------------------------------------------------------------------
    def _name(self, raw):
        """장비 이름 하나 — FQDN 이면 첫 마디가 이름, 나머지는 도메인."""
        raw = (raw or "").strip()
        if not raw or raw.lower() in self.keep or _PSEUDO_NAME_RE.match(raw) or _ADDRESS_LIKE_RE.match(raw):
            return
        if _FQDN_RE.fullmatch(raw):
            raw, _, tail = raw.partition(".")
            self._domain(tail)
        self.names.setdefault(raw.lower(), f"DEVICE-{len(self.names) + 1}")

    def _domain(self, raw):
        """도메인 하나 — 마디가 둘 이상인 것만(한 마디 'local' 을 담으면 그 낱말이 전부 누출로 보인다)."""
        key = (raw or "").lower().rstrip(".")
        if "." in key and key not in self.keep and not _PSEUDO_DOMAIN_RE.search(key) and key not in self.domains:
            i = len(self.domains)
            self.domains[key] = _DOMAINS[i] if i < len(_DOMAINS) else f"example{i + 1}.net"

    def _serial(self, raw):
        if (not raw or raw in self.serials or raw.lower() in self.keep or not re.search(r"\d", raw)
                or _INTERFACE_RE.match(raw)):
            return
        n = len(self.serials) + 1
        fake = _shaped(raw, n)
        while fake in self.serials.values() or fake == raw:
            n += 1000
            fake = _shaped(raw, n)
        self.serials[raw] = fake

    def _text(self, raw, kind):
        raw = (raw or "").strip()
        if (len(raw) < 3 or raw in self.texts or raw.lower() in _GENERIC_WORDS or raw.lower() in self.keep
                or _DEFAULT_VLAN_RE.match(raw) or _INTERFACE_RE.match(raw) or _NOT_ADVERTISED_RE.match(raw)):
            return
        used = {value for value in self.texts.values() if value.startswith(kind + "-")}
        self.texts[raw] = f"{kind}-{len(used) + 1}"

    def _v4(self, m):
        value = m.group(0)
        try:
            ip = ipaddress.IPv4Address(value)
        except ValueError:
            return value
        if _special_v4(ip):
            return value
        index = self.v4_nets.setdefault(ip.packed[:3], len(self.v4_nets))
        for pool in _V4_POOLS:
            size = pool.num_addresses // 256
            if index < size:
                fake = str(pool.network_address + index * 256 + ip.packed[3])
                self.produced["ipv4"].add(fake)
                return fake
            index -= size
        raise ValueError("서로 다른 /24 가 너무 많다")

    def _v6_sub(self, m):
        value = m.group(0)
        try:
            ip = ipaddress.IPv6Address(value)
        except ValueError:
            return value
        if _special_v6(ip):
            return value
        if ip not in self.v6:
            if ip in _V6_LINK_LOCAL:
                count = sum(1 for fake in self.v6.values() if fake in _V6_LINK_LOCAL)
                self.v6[ip] = ipaddress.IPv6Address(f"fe80::{count + 1:x}")
            else:
                prefix = int(ip) >> 64
                net = self.v6_nets.setdefault(prefix, len(self.v6_nets) + 1)
                count = sum(1 for real in self.v6 if int(real) >> 64 == prefix and real not in _V6_LINK_LOCAL)
                self.v6[ip] = ipaddress.IPv6Address(f"2001:db8:{net:x}::{count + 1:x}")
        fake = str(self.v6[ip])
        self.produced["ipv6"].add(fake)
        return fake

    def _mac_sub(self, m):
        value = m.group(0)
        if _special_mac(value):
            return value
        key = re.sub(r"[^0-9a-f]", "", value.lower())
        digits = f"{self.macs.setdefault(key, _MAC_BASE + len(self.macs) + 1):012x}"
        if "." in value:
            fake = ".".join(digits[i:i + 4] for i in range(0, 12, 4))
        else:
            fake = value[2].join(digits[i:i + 2] for i in range(0, 12, 2))
        if any(ch.isupper() for ch in value):
            fake = fake.upper()
        self.produced["mac"].add(fake.lower())
        return fake

    def _fqdn_sub(self, m):
        value = m.group(0)
        if _PSEUDO_DOMAIN_RE.search(value) or value.lower() in self.keep:
            return value
        head, _, tail = value.partition(".")
        if "." not in tail and not _PSEUDO_NAME_RE.match(head):      # 두 마디(abc.com) — 전체가 도메인
            self._domain(value)
            return self.domains.get(value.lower(), _DOMAINS[0])
        if not _PSEUDO_NAME_RE.match(head):
            head = self.hosts.setdefault(head.lower(), f"host-{len(self.hosts) + 1}")
        self._domain(tail)
        return f"{head}.{self.domains.get(tail.lower(), _DOMAINS[0])}"

    # ---- 찾기 ------------------------------------------------------------------------------------------
    def discover(self, text):
        """원본에서 바꿀 값을 모은다 — 바꾸기 전에 전부 모아야 한 값이 모든 자리에서 같은 가짜가 된다."""
        from engine.inspection_evaluator import split_transcript
        from parsers.show_interfaces_status import parse_descriptions
        from parsers.show_lldp_neighbors import (parse_cdp_neighbors_detail, parse_lldp_neighbors,
                                                 parse_lldp_neighbors_detail)
        for m in _PROMPT_RE.finditer(text):
            self._name(m.group(1))
        for pattern in _NAME_DECL_RES:
            for m in pattern.finditer(text):
                self._name(m.group(1).rstrip(",()"))
        sections = split_transcript(text)
        for command, output in sections.items():
            low = command.lower()
            neighbors = []
            if "lldp neighbors" in low:
                neighbors = parse_lldp_neighbors_detail(output) if "detail" in low else parse_lldp_neighbors(output)
            elif "cdp neighbors detail" in low:
                neighbors = parse_cdp_neighbors_detail(output)
            for neighbor in neighbors:
                self._name(neighbor["neighbor_device"])
            if "interfaces description" in low or "interface description" in low:
                for description in parse_descriptions(output).values():
                    self._text(description, "DESC")
        for pattern in _DESCRIPTION_RES:
            for m in pattern.finditer(text):
                self._text(m.group(1), "DESC")
        # 상태 표의 Name 열은 설명을 잘라 적는다 — 온전한 설명을 다 모은 뒤에 견줘야 같은 가짜가 된다
        for command, output in sections.items():
            if "interfaces status" in command.lower() or "interface status" in command.lower():
                for line in output.splitlines():
                    m = _STATUS_ROW_RE.match(line.strip())
                    cell = m.group(2).strip() if m else ""
                    full = next((d for d in self.texts if len(cell) >= 4 and d.startswith(cell)), None)
                    if full:
                        self.texts.setdefault(cell, self.texts[full])
                    elif cell:
                        self._text(cell, "DESC")
        for pattern in _VLAN_NAME_RES:
            for m in pattern.finditer(text):
                self._text(m.group(1), "VLAN_NAME")
        for kind, pattern in _SNMP_RES:
            for m in pattern.finditer(text):
                self._text(m.group(1), kind)
        for pattern in _SERIAL_RES:
            for m in pattern.finditer(text):
                self._serial(m.group(1))
        for value in _column_values(text, _SERIAL_COLUMN_RE):
            self._serial(value)
        for pattern in _USER_RES:
            for m in pattern.finditer(text):
                value = m.group(1)
                if value.lower() not in self.keep and value not in self.users:
                    self.users[value] = f"user{len(self.users) + 1}"
        for pattern in _DOMAIN_RES:
            for m in pattern.finditer(text):
                self._domain(m.group(1))

    # ---- 바꾸기 ----------------------------------------------------------------------------------------
    def apply(self, text):
        """discover() 로 모은 값과 주소·MAC 을 가짜로 바꾼 텍스트. 원본의 줄바꿈(CRLF)은 그대로다."""
        out = self._log_masking.apply_masking(text, self._log_masking.SECRET_KEYS)   # 비밀 — 앞뒤 문맥으로 찾으므로 먼저
        out = _mask_banners(out)
        out = _replace_exact(out, self.texts)           # 설명·VLAN 이름·SNMP — 그 안의 장비 이름째로
        out = _replace_exact(out, self.users)
        out = _replace_exact(out, self.serials)
        out = _replace_exact(out, self.names, ci=True)
        out = _replace_exact(out, self.domains, ci=True)
        out = _FQDN_RE.sub(self._fqdn_sub, out)
        out = _MAC_RE.sub(self._mac_sub, out)
        out = self._ipv6_re.sub(self._v6_sub, out)
        out = _IPV4_RE.sub(self._v4, out)
        # 지정한 낱말은 맨 끝에 — 'ABC' 는 16진수이기도 해서 주소(2001:abcd::)를 바꾸기 전에 하면 주소가 깨진다
        return _replace_exact(out, self.words, ci=True, inside_words=True)

    def run(self, text):
        """찾기 -> 바꾸기 -> 누출 검사. 남은 것이 있으면 LeakError."""
        self.discover(text)
        out = self.apply(text)
        leaks = self.leaks(out)
        if leaks:
            raise LeakError(leaks)
        return out

    # ---- 누출 검사 -------------------------------------------------------------------------------------
    def leaks(self, text):
        """바꾼 원래 값이 남았나 + 남은 주소·MAC·FQDN·비밀이 이번에 만든 가짜인가. [{what, value, lines}]"""
        found = []

        def check(what, values, *, ci, inside_words=False):
            for value in values:
                pattern = _bounded(re.escape(value), inside_words)
                hits = [m.start() for m in re.finditer(pattern, text, re.IGNORECASE if ci else 0)]
                if hits:
                    found.append({"what": what, "value": value, "lines": _line_numbers(text, hits)})

        # 이름은 다른 낱말 속('Core1-mgmt' — 도구가 모르는 다른 장비일 수 있다)에 든 것도 찾는다
        check("장비 이름", [name for name in self.names if len(name) >= 3], ci=True, inside_words=True)
        check("도메인", self.domains, ci=True)
        check("일련번호", self.serials, ci=False)
        check("계정", self.users, ci=False)
        check("설명·VLAN 이름·SNMP", [value for value in self.texts if len(value) >= 4], ci=False)
        check("지정한 낱말", self.words, ci=True, inside_words=True)
        return found + residual_leaks(text, produced=self.produced, keep=self.keep)


def residual_leaks(text, produced=None, keep=()):
    """원래 값을 몰라도 알 수 있는 누출 — 가짜가 아닌 IP·MAC·FQDN, 비밀 규칙에 걸리는 값. [{what, value, lines}]
    produced(이번에 만든 가짜)를 주면 그 목록으로, 없으면 가짜 대역으로 본다 — 커밋된 픽스처의 재검사(테스트)는 후자다.
    keep: 남겨 두기로 한 낱말(fixture.json 의 kept)."""
    from engine import log_masking
    keep = {value.lower() for value in keep}
    found = {}

    def add(what, value, index):
        leak = found.setdefault((what, value), {"what": what, "value": value, "lines": []})
        leak["lines"] = sorted(set(leak["lines"]) | set(_line_numbers(text, [index])))

    for m in _IPV4_RE.finditer(text):
        try:
            ip = ipaddress.IPv4Address(m.group(0))
        except ValueError:
            continue
        fake = m.group(0) in produced["ipv4"] if produced else any(ip in pool for pool in _V4_POOLS)
        if not (_special_v4(ip) or fake or m.group(0) in keep):
            add("IPv4", m.group(0), m.start())
    for m in re.finditer(log_masking._IPV6, text):
        try:
            ip = ipaddress.IPv6Address(m.group(0))
        except ValueError:
            continue
        fake = str(ip) in produced["ipv6"] if produced else (ip in _V6_DOC or ip in _V6_LINK_LOCAL)
        if not (_special_v6(ip) or fake or m.group(0).lower() in keep):
            add("IPv6", m.group(0), m.start())
    for m in _MAC_RE.finditer(text):
        value = m.group(0)
        number = int(re.sub(r"[^0-9A-Fa-f]", "", value), 16)
        fake = value.lower() in produced["mac"] if produced else (number >> 24) == (_MAC_BASE >> 24)
        if not (_special_mac(value) or fake or value.lower() in keep):
            add("MAC", value, m.start())
    for m in _FQDN_RE.finditer(text):
        if not (_PSEUDO_DOMAIN_RE.search(m.group(0)) or m.group(0).lower() in keep):
            add("FQDN", m.group(0), m.start())
    for key in log_masking.find_unmasked_secrets(text):
        rule = log_masking.MASK_RULES[key]
        for m in rule["pattern"].finditer(text):
            group = log_masking._matched_group(m, rule["group"]) if rule.get("group") else 0
            if group is not None and re.search(r"[A-Za-z0-9]", m.group(group)):
                add(f"비밀({rule['label']})", "(값은 적지 않는다)", m.start())
    return list(found.values())


def _column_values(text, title_re):
    """표 머리줄에서 title_re 열을 찾아 그 아래 행의 칸 값을 모은다(빈 줄에서 그친다). 칸은 머리 글자 자리에 걸친 낱말이다."""
    values = []
    lines = text.splitlines()
    for i, header in enumerate(lines):
        m = title_re.search(header)
        if not m or len(re.findall(r"\S(?:\S| (?! ))*", header)) < 2:      # 열이 둘 이상인 머리줄만
            continue
        for row in lines[i + 1:]:
            if not row.strip():
                break
            if re.fullmatch(r"[\s\-=+]+", row):
                continue
            for token in re.finditer(r"\S+", row):
                if token.start() <= m.start() + 2 and token.end() > m.start():
                    if re.fullmatch(r"[A-Za-z0-9][\w-]{3,}", token.group(0)):
                        values.append(token.group(0))
                    break
    return values


def _mask_banners(text):
    """배너 본문의 글자를 '*'로 — 고객사 이름·경고문이 들어 있다. 줄바꿈과 구분자는 그대로."""
    def star(m, group):
        whole, start, end = m.group(0), m.start(group) - m.start(0), m.end(group) - m.start(0)
        return whole[:start] + re.sub(r"\S", "*", m.group(group)) + whole[end:]

    text = _BANNER_EOS_RE.sub(lambda m: star(m, 1), text)
    return _BANNER_IOS_RE.sub(lambda m: star(m, 2), text)


# ---------------------------------------------------------------------------- 파서가 읽는 것
def describe(text, platform=None):
    """지금 파서가 이 출력에서 읽는 것 — expected.json. 보고서는 번들 기본 양식으로 판정한다(사용자·고객사 템플릿과 무관)."""
    from core.app_config import bundled_config
    from engine import inspection_evaluator as evaluator
    from engine import inspection_report_builder as builder
    from engine.log_analysis import analyze_text
    from engine.topology_builder import device_facts

    sections = evaluator.split_transcript(text)
    platform = platform or builder._platform({}, sections)
    template = evaluator.load_template(path=bundled_config(TEMPLATE_NAME))
    rows = evaluator.evaluate_device(sections, template, platform=platform)
    result = {
        "platform": platform,
        "commands": sorted(sections),
        "device": {"model": builder._model(sections), "serial": builder._serial(sections),
                   "os_version": builder._os_version(sections), "ip": builder._ip_from_sections(sections)},
        "report_items": [{key: row.get(key) for key in ("name", "method", "status", "value", "number")} for row in rows],
        "topology": device_facts(sections),
        "log_findings": [{"line": f.get("line_no"), "rule": f.get("rule_id"), "severity": f.get("severity"),
                          "tag": f.get("category_tag"), "repeat": f.get("repeat")} for f in analyze_text(text)],
    }
    return json.loads(json.dumps(result, ensure_ascii=False, default=str))      # 튜플·집합 -> JSON 모양


def _dump(data):
    return json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def read_fixture(path):
    """픽스처 폴더 -> (session.log 텍스트, fixture.json)."""
    from core.text_io import read_log_text
    return read_log_text(Path(path) / "session.log"), json.loads((Path(path) / "fixture.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------- 명령
def promote(source, name, *, platform=None, names=(), words=(), keep=(), force=False, root=None):
    """원본 로그 하나 -> root(기본 GOLDEN_DIR)/<name>/(session.log·expected.json·fixture.json).
    누출이 있으면 LeakError 로 아무것도 쓰지 않는다."""
    from core.text_io import read_log_text
    if not _FIXTURE_NAME_RE.match(name or ""):
        raise ValueError("픽스처 이름은 영소문자·숫자·_ 3~61자다(예: cisco_ios_core)")
    target = Path(root or GOLDEN_DIR) / name
    if target.exists() and not force:
        raise FileExistsError(f"{target} 가 이미 있다 — 바꾸려면 --force(파서 결과만 다시 만들려면 refresh)")
    pseudonymizer = Pseudonymizer(names=names, words=words, keep=keep)
    masked = pseudonymizer.run(read_log_text(source))
    expected = describe(masked, platform)
    counts = {"장비 이름": len(pseudonymizer.names), "도메인": len(pseudonymizer.domains),
              "IPv4 /24": len(pseudonymizer.v4_nets), "IPv6": len(pseudonymizer.v6), "MAC": len(pseudonymizer.macs),
              "일련번호": len(pseudonymizer.serials), "계정": len(pseudonymizer.users),
              "설명·VLAN 이름·SNMP": len(set(pseudonymizer.texts.values())), "지정한 낱말": len(pseudonymizer.words)}
    meta = {"platform": expected["platform"], "platform_override": platform, "kept": sorted(pseudonymizer.keep),
            "promoted_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "replaced": {key: value for key, value in counts.items() if value},
            "lines": len(masked.splitlines()), "commands": len(expected["commands"])}
    target.mkdir(parents=True, exist_ok=True)
    (target / "session.log").write_bytes(masked.encode("utf-8"))
    (target / "expected.json").write_text(_dump(expected), encoding="utf-8", newline="\n")
    (target / "fixture.json").write_text(_dump(meta), encoding="utf-8", newline="\n")
    return {"path": str(target), "meta": meta, "expected": expected}


def fixtures(root=None):
    root = Path(root or GOLDEN_DIR)
    return sorted(p for p in root.iterdir() if (p / "session.log").is_file()) if root.is_dir() else []


def compare(path):
    """픽스처 하나의 expected.json 과 지금 파서 결과가 다른 최상위 항목 이름들(같으면 [])."""
    text, meta = read_fixture(path)
    actual = describe(text, meta.get("platform_override"))
    expected = json.loads((Path(path) / "expected.json").read_text(encoding="utf-8"))
    return sorted(key for key in set(actual) | set(expected) if actual.get(key) != expected.get(key))


def refresh(names=(), root=None):
    """파서를 일부러 바꾼 뒤 — expected.json 을 지금 파서 결과로 다시 쓴다(session.log 는 그대로). 바뀐 픽스처 이름들."""
    changed = []
    for path in fixtures(root):
        if names and path.name not in names:
            continue
        text, meta = read_fixture(path)
        dumped = _dump(describe(text, meta.get("platform_override")))
        if (path / "expected.json").read_text(encoding="utf-8") != dumped:
            (path / "expected.json").write_text(dumped, encoding="utf-8", newline="\n")
            changed.append(path.name)
    return changed


def _use_scratch_app_data():
    """앱 모듈을 import 하기 전에 — 데이터 폴더를 임시 폴더로(사용자 사본 템플릿·규칙이 결과에 섞이지 않고, 실제
    데이터 폴더를 만들거나 고치지 않게)."""
    import atexit
    scratch = tempfile.mkdtemp(prefix="autocheck_fixture_")
    os.environ["AUTOCHECK_HOME"] = scratch
    atexit.register(shutil.rmtree, scratch, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="실제 장비 출력 -> 골든 픽스처(가명화 + 파서 결과 고정)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("promote", help="원본 로그 하나를 가명화해 픽스처로")
    p.add_argument("source")
    p.add_argument("--name", required=True, help="픽스처 이름(영소문자·숫자·_)")
    p.add_argument("--platform", help="판정할 플랫폼(arista_eos·cisco_ios·cisco_xe·cisco_nxos) — 기본은 show version 으로")
    p.add_argument("--name-of", dest="names", action="append", default=[], help="도구가 찾지 못한 장비 이름(여러 번)")
    p.add_argument("--word", dest="words", action="append", default=[], help="더 가릴 낱말 — 고객사·사이트 이름 등(여러 번)")
    p.add_argument("--keep", action="append", default=[], help="바꾸지 않을 낱말(여러 번)")
    p.add_argument("--force", action="store_true", help="같은 이름의 픽스처를 덮어쓴다")
    p.add_argument("--out", help="픽스처를 쓸 폴더(기본 tests/fixtures/golden) — 먼저 다른 곳에 만들어 읽어 보려면")
    r = sub.add_parser("refresh", help="파서를 일부러 바꾼 뒤 expected.json 을 다시 만든다")
    r.add_argument("names", nargs="*")
    sub.add_parser("check", help="테스트와 같은 대조 + 남은 식별자 재검사")
    args = parser.parse_args(argv)
    _use_scratch_app_data()

    if args.command == "promote":
        try:
            made = promote(args.source, args.name, platform=args.platform, names=args.names, words=args.words,
                           keep=args.keep, force=args.force, root=args.out)
        except (LeakError, FileExistsError, ValueError, OSError) as exc:
            print(f"[중단] {exc}", file=sys.stderr)
            if isinstance(exc, LeakError):
                print("        --name-of(장비 이름)·--word(그 밖의 낱말)로 더 바꾸거나, 남겨도 되는 낱말이면 --keep",
                      file=sys.stderr)
            return 1
        meta = made["meta"]
        print(f"픽스처를 만들었습니다: {made['path']}")
        print(f"  플랫폼 {meta['platform']} · 줄 {meta['lines']} · 명령 {meta['commands']}개")
        print("  바꾼 값: " + ", ".join(f"{key} {value}" for key, value in meta["replaced"].items()))
        print("  커밋하기 전에 session.log 를 읽어 보세요 — 주석·스크립트·자유 문구 속의 고객 정보는 도구가 모릅니다.")
        return 0
    if args.command == "refresh":
        changed = refresh(args.names)
        print("expected.json 을 다시 쓴 픽스처: " + (", ".join(changed) if changed else "없음(모두 그대로)"))
        return 0
    found = fixtures()
    failed = 0
    for path in found:
        text, meta = read_fixture(path)
        diff, leaks = compare(path), residual_leaks(text, keep=meta.get("kept") or ())
        failed += bool(diff or leaks)
        print(("실패  " if diff or leaks else "통과  ") + path.name
              + (f" — 달라진 항목: {', '.join(diff)}" if diff else "")
              + (f" — 남은 식별자 {len(leaks)}종" if leaks else ""))
    print(f"픽스처 {len(found)}개, 실패 {failed}개")
    return 1 if failed else 0


if __name__ == "__main__":
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    sys.exit(main())
