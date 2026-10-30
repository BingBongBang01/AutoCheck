"""
Format-preserving 마스킹 — 구조적 구분자(마침표/콜론/슬래시 등)는 그대로 두고
그 구분자 사이의 실제 값(영숫자)만 원본과 정확히 같은 글자 수의 '*'로 치환한다.

예: 192.20.1.3        -> ***.**.*.*      (마침표 유지, 숫자만 자릿수만큼 마스킹)
    5001.004b.6277    -> ****.****.****  (Cisco MAC 표기 — 4자리 hex 그룹 3개, 마침표 유지)

체크리스트 8종(IP/MAC/Password Hash/Account Name/Device Hostname/OS Version/
Internal VLAN Name/Domain Name)을 사용자가 원하는 것만 선택해 적용할 수 있도록
각 항목을 독립된 규칙(정규식 + 보존할 구분자 문자 집합)으로 정의한다.

값을 잡는 정규식은 [ \\t] 로 한 줄 안에서만 찾는다 — \\s 를 쓰면 줄 끝의 키워드(예: 로그의
"... invalid password") 다음 줄 첫 단어를 '값'으로 잡아 엉뚱한 것을 가리고, 잔여 비밀값 검사
(find_unmasked_secrets)가 거짓 경고를 낸다.
"""
import os
import re
import glob
from pathlib import Path

_HEX4 = r"[0-9A-Fa-f]{1,4}"
# IPv6 — 8마디 전체 또는 '::' 축약형만. 마디 수가 모자라고 '::' 도 없는 것(MAC aa:bb:cc:dd:ee:ff,
# 시각 10:15:30)은 IPv6 가 아니다.
_IPV6 = (
    r"(?<![\w:])(?:"
    rf"(?:{_HEX4}:){{7}}{_HEX4}"
    rf"|(?:{_HEX4}:){{1,7}}:"
    rf"|(?:{_HEX4}:){{1,6}}:{_HEX4}"
    rf"|(?:{_HEX4}:){{1,5}}(?::{_HEX4}){{1,2}}"
    rf"|(?:{_HEX4}:){{1,4}}(?::{_HEX4}){{1,3}}"
    rf"|(?:{_HEX4}:){{1,3}}(?::{_HEX4}){{1,4}}"
    rf"|(?:{_HEX4}:){{1,2}}(?::{_HEX4}){{1,5}}"
    rf"|{_HEX4}:(?::{_HEX4}){{1,6}}"
    rf"|:(?::{_HEX4}){{1,7}}"
    r")(?![\w:])"
)

# 순서 = UI 체크리스트에 표시할 순서(과제에서 요구한 정확한 8개 항목/순서 그대로).
MASK_RULES = {
    "ip_address": {
        "label": "IP Address",
        # IPv6 도 같은 항목에서 가린다 — 'IP Address' 를 고른 사용자가 IPv6 만 남는 것을 기대할 리 없다.
        "pattern": re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|" + _IPV6),
        "delimiters": ".:",
    },
    "mac_address": {
        "label": "MAC Address",
        "pattern": re.compile(
            r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"      # aa:bb:cc:dd:ee:ff / aa-bb-...
            r"|\b(?:[0-9A-Fa-f]{4}\.){2}[0-9A-Fa-f]{4}\b"        # Cisco 표기: aabb.ccdd.eeff
        ),
        "delimiters": ".:-",
    },
    "password_hash": {
        "label": "Password Hash",
        # Cisco/Arista는 "secret 5 <hash>"(숫자 타입)뿐 아니라 "secret sha512 <hash>"처럼
        # 알고리즘 이름(sha512/md5/scrypt 등)을 타입 표시로 쓰기도 한다. 예전 정규식은
        # 숫자 타입만 건너뛰도록 되어 있어서 "secret sha512 $6$..." 같은 줄을 만나면
        # 정작 해시가 아니라 그 앞의 "sha512"라는 알고리즘 이름 자체를 캡처해서 가려버리고
        # 진짜 보호해야 할 해시값(`$6$...`)은 그대로 노출시켰다.
        # -> 타입 표시(숫자든 알고리즘 이름이든)는 전부 건너뛰고, 그 다음에 오는
        #    실제 해시/비밀번호 토큰을 캡처하도록 수정.
        "pattern": re.compile(
            r"(?:password|secret|hash)[ \t]+(?:(?:\d+|sha256|sha512|md5|scrypt|crypt)[ \t]+)?(\S+)",
            re.IGNORECASE,
        ),
        "group": 1,
        "delimiters": "$./",
    },
    "account_name": {
        "label": "Account Name",
        "pattern": re.compile(r"(?:username|user)[ \t]+(\S+)", re.IGNORECASE),
        "group": 1,
        "delimiters": "",
    },
    "device_hostname": {
        "label": "Device Hostname",
        "pattern": re.compile(r"hostname[ \t]+(\S+)", re.IGNORECASE),
        "group": 1,
        "delimiters": "-",
    },
    "os_version": {
        "label": "OS Version",
        "pattern": re.compile(r"(?:[Vv]ersion|image version:)\s*([A-Za-z0-9()./-]+)"),
        "group": 1,
        "delimiters": "().-/",
    },
    "vlan_name": {
        "label": "Internal VLAN Name",
        # "show vlan" 테이블 행("100  Develop  active")뿐 아니라 running-config 안에서
        # vlan 블록의 자식으로 나오는 "   name Develop" 줄도 내부 VLAN 이름이다 — 원래는
        # 테이블 행 형태만 잡아서 config 파일 쪽 이름은 그대로 노출되고 있었다.
        "pattern": re.compile(
            r"^\d{1,4}\s+(?P<tbl>\S+)\s+(?:active|suspend)"
            r"|^\s*name\s+(?P<cfg>\S+)\s*$",
            re.IGNORECASE | re.MULTILINE,
        ),
        "group": ("tbl", "cfg"),
        "delimiters": "_-",
    },
    "domain_name": {
        "label": "Domain Name",
        # 예전엔 "글자.글자" 형태면 전부 도메인으로 간주해서 "show spanning-tree" 표의
        # 헤더 "Prio.Nbr" 같은 일반 텍스트까지 도메인으로 오인해 마스킹했다(과잉 마스킹).
        # -> 실제 알려진 TLD로 끝나는 점 표기만 도메인으로 인정하고,
        #    MLAG의 "domain-id LAB"처럼 점이 없는 순수 도메인/식별자 값은
        #    "domain-id"/"domain-name" 키워드 뒤 토큰으로 별도로 잡는다.
        "pattern": re.compile(
            # "domain-id LAB"(설정) / "domain-id  :  LAB"(show mlag의 콜론 정렬 표) 둘 다 커버.
            r"\bdomain-id\s*:?\s+(?P<kw1>\S+)"
            r"|\bdomain-name\s*:?\s+(?P<kw2>\S+)"
            r"|\b(?P<fqdn>(?:[A-Za-z0-9-]+\.)+"
            r"(?:com|net|org|edu|gov|mil|int|io|co|kr|jp|cn|de|uk|info|biz|local|lan|corp|internal))\b",
            re.IGNORECASE,
        ),
        "group": ("kw1", "kw2", "fqdn"),
        "delimiters": ".",
    },
    # 아래 4종은 LogManager(engine/log_manager.py)의 masked/ 산출물 요구사항(SNMP community,
    # Secrets/API Key/Token, Private Key)을 채우기 위해 추가됐다 — 기존 8종의 순서/동작은
    # 그대로 두고 MASK_KEY_ORDER 뒤에만 덧붙여 하위 호환을 유지한다.
    "snmp_community": {
        "label": "SNMP Community",
        # 커뮤니티는 'snmp-server community X' 뿐 아니라 trap 수신처 줄 끝에도 그대로 나온다:
        #   snmp-server host 10.1.1.1 [vrf V] [traps|informs] [version 2c] public
        "pattern": re.compile(
            r"(?:snmp-server[ \t]+community|community-string)[ \t]+(?P<c>\S+)"
            r"|snmp-server[ \t]+host[ \t]+\S+(?:[ \t]+vrf[ \t]+\S+)?(?:[ \t]+(?:traps|informs))?"
            r"(?:[ \t]+version[ \t]+\S+(?:[ \t]+(?:auth|noauth|priv))?)?[ \t]+(?P<host>\S+)",
            re.IGNORECASE,
        ),
        "group": ("c", "host"),
        "delimiters": "",
    },
    "secret_token": {
        "label": "Secrets / API Key / Token",
        # JSON/YAML 형태("api_key": "abc", api_key=abc)도 잡는다 — 따옴표·구분자는 값에서 뺀다.
        "pattern": re.compile(
            r"(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|bearer)[\"']?[ \t]*[:=]?[ \t]*[\"']?"
            r"([^\s\"',]+)",
            re.IGNORECASE,
        ),
        "group": 1,
        "delimiters": "",
    },
    "private_key": {
        "label": "Private Key",
        # PEM 블록 전체(헤더/바디/푸터)를 통째로 마스킹 — 줄바꿈만 보존해서 블록이
        # 있었다는 사실은 남기고 내용은 전부 가린다.
        "pattern": re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----[\s\S]+?"
            r"-----END (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----"
        ),
        "delimiters": "\n",
    },
    "auth_key": {
        "label": "Auth Key (TACACS/RADIUS·OSPF/NTP·SNMPv3·IPsec)",
        # 장비 설정에 들어 있는 인증 키들 — 'password'/'secret' 이라는 단어를 쓰지 않아서
        # password_hash 규칙으로는 하나도 가려지지 않던 것들이다. 숫자 타입(0/7)·알고리즘은 건너뛴다.
        "pattern": re.compile(
            r"\bkey-string[ \t]+(?:\d[ \t]+)?(?P<ks>\S+)"                                # key chain
            r"|\b(?:tacacs-server|radius-server)\b[^\n]*?\bkey[ \t]+(?:\d[ \t]+)?(?P<srv>\S+)"
            r"|^[ \t]+key[ \t]+[0-7][ \t]+(?P<blk>\S+)"                                # IOS 'tacacs server' 블록
            r"|\bauthentication-key[ \t]+(?:\d+[ \t]+)?(?:(?:md5|sha\d*)[ \t]+)?(?:\d[ \t]+)?(?P<ak>\S+)"  # OSPF/NTP
            r"|\bmessage-digest-key[ \t]+\d+[ \t]+md5[ \t]+(?:\d[ \t]+)?(?P<md>\S+)"
            r"|\b(?:pre-shared-key(?:[ \t]+(?:local|remote))?|isakmp[ \t]+key)[ \t]+(?:\d[ \t]+)?(?P<psk>\S+)"
            r"|\bauthentication[ \t]+(?:text|key)[ \t]+(?:\d[ \t]+)?(?P<txt>\S+)"        # VRRP/HSRP/IS-IS
            r"|\bauth[ \t]+(?:md5|sha\d*)[ \t]+(?P<a3>\S+)"                            # SNMPv3 user
            r"|\bpriv[ \t]+(?:(?:des\S*|3des|aes[\w-]*)[ \t]+)?(?:\d{3}[ \t]+)?"
            r"(?!(?:read|write|notify|access|context|match)\b)(?P<p3>\S+)",
            re.IGNORECASE | re.MULTILINE,
        ),
        "group": ("ks", "srv", "blk", "ak", "md", "psk", "txt", "a3", "p3"),
        "delimiters": "",
    },
}

MASK_KEY_ORDER = ["ip_address", "mac_address", "password_hash", "account_name",
                  "device_hostname", "os_version", "vlan_name", "domain_name",
                  "snmp_community", "secret_token", "private_key", "auth_key"]

# 공유 전에 반드시 가려져야 하는 비밀값 규칙 — 마스킹 뒤에도 남아 있으면 경고한다.
SECRET_KEYS = ("password_hash", "snmp_community", "secret_token", "private_key", "auth_key")

# hostname 선언에서 실제 장비명을 뽑아내는 패턴 — "hostname X" 설정 줄뿐 아니라
# running-config 맨 위 주석("! device: X (...)")에도 같은 장비명이 나온다.
_HOSTNAME_DECL_RE = re.compile(r"(?:^\s*hostname\s+|^!\s*device:\s*)(\S+)", re.IGNORECASE | re.MULTILINE)


def get_mask_options():
    """'Log Masking' 탭 체크리스트용 — [{"key","label"}, ...] (표시 순서 고정)."""
    return [{"key": key, "label": MASK_RULES[key]["label"]} for key in MASK_KEY_ORDER]


def _mask_span(token, delimiters):
    """delimiters에 포함된 문자만 그대로 두고 나머지는 전부 '*'로 — 원본과 길이가 항상 같다."""
    return "".join(ch if ch in delimiters else "*" for ch in token)


def _matched_group(m, group):
    """group이 튜플/리스트(중첩된 alternation의 named group들)면 실제로 매치된 첫 이름을 찾아 반환.
    단일 int/str이면 그대로 사용."""
    if isinstance(group, (tuple, list)):
        for name in group:
            if m.group(name) is not None:
                return name
        return None
    return group


def _apply_rule(text, rule):
    pattern = rule["pattern"]
    delimiters = set(rule.get("delimiters", ""))
    group = rule.get("group", 0)

    def repl(m):
        whole = m.group(0)
        if not group:
            return _mask_span(whole, delimiters)
        resolved = _matched_group(m, group)
        if resolved is None:
            return whole
        target = m.group(resolved)
        rel_start = m.start(resolved) - m.start(0)
        rel_end = m.end(resolved) - m.start(0)
        return whole[:rel_start] + _mask_span(target, delimiters) + whole[rel_end:]

    return pattern.sub(repl, text)


def _declared_hostnames(text):
    """"hostname X"/"! device: X" 선언에서 장비명 X 들 — 규칙이 선언 줄을 가리기 **전에** 읽어야 한다."""
    return {m.group(1).rstrip(",()") for m in _HOSTNAME_DECL_RE.finditer(text)}


def _mask_hostname_everywhere(text, names=None):
    """"hostname X"/"! device: X"에서 뽑아낸 실제 장비명 X를, 그 선언 줄뿐 아니라
    프롬프트("X(config)#"), syslog 줄("... X ..."), 상단 주석 등 텍스트 전체에서
    같은 토큰으로 등장하는 곳까지 전부 같은 글자 수의 '*'로 마스킹한다.
    device_hostname 규칙 하나만으론 "hostname X" 줄 자체만 가려지고 X라는 값이
    본문 다른 곳에 그대로 노출되는 문제가 있어서 별도 후처리로 보완.

    names 는 마스킹 전에 읽은 선언(_declared_hostnames) — 예전엔 규칙이 'hostname X' 를 'hostname *****'로 바꾼 뒤에
    읽어서 이름을 찾지 못했고, Arista 의 '! device: X' 주석이 없는 로그(Cisco 설정 등)는 프롬프트의 장비명이 그대로
    남았다(IMPROVEMENT_PLAN 4-10 의 누출 검사가 찾았다)."""
    names = _declared_hostnames(text) if names is None else names
    for name in names:
        if not name or not re.search(r"[A-Za-z0-9]", name):
            continue
        text = re.sub(r"\b" + re.escape(name) + r"\b",
                       lambda m: _mask_span(m.group(0), ""), text, flags=re.IGNORECASE)
    return text


def _compile_custom_rule(rule: dict) -> dict:
    """JSON에서 온 사용자 정의 규칙({"pattern": "정규식 문자열", "group": ..., "delimiters": ...})을
    compile된 patttern을 쓰는 내부 규칙 형태로 변환한다. 이미 컴파일된 pattern이면 그대로 둔다."""
    pattern = rule.get("pattern")
    if isinstance(pattern, str):
        rule = {**rule, "pattern": re.compile(pattern, re.IGNORECASE if rule.get("ignorecase", True) else 0)}
    return rule


def apply_masking(text, selected_keys, extra_rules: dict = None):
    """selected_keys에 있는 규칙만, 정의 순서대로 순차 적용.
    extra_rules: LogManager가 프로파일별 설정(settings.json의 masking_rules 등)에서 읽어온
    사용자 정의 규칙 {key: {"label","pattern"(str/regex),"group","delimiters"}} — 내장 8+3종을
    건드리지 않고 그 뒤에 이어 붙여 적용한다(설정 가능한 마스킹 규칙 요구사항)."""
    order = list(MASK_KEY_ORDER)
    rules = MASK_RULES
    if extra_rules:
        rules = dict(MASK_RULES)
        for key, rule in extra_rules.items():
            rules[key] = _compile_custom_rule(rule)
            if key not in order:
                order.append(key)
    # 비밀값 규칙을 먼저 적용한다. 비밀값은 앞뒤 키워드(문맥)로 찾는데, 일반 규칙이 먼저 그 문맥을
    # 바꾸면 못 찾는다 — os_version 이 'version 2c' 를 'version **' 로 바꿔서 snmp-server host 줄의
    # 커뮤니티 대신 'version' 이라는 단어를 가렸었다. 화면 표시 순서(MASK_KEY_ORDER)는 그대로다.
    order = [key for key in order if key in SECRET_KEYS] + [key for key in order if key not in SECRET_KEYS]
    declared = _declared_hostnames(text) if "device_hostname" in selected_keys else set()
    for key in order:
        if key in selected_keys and key in rules:
            text = _apply_rule(text, rules[key])
    if "device_hostname" in selected_keys:
        text = _mask_hostname_everywhere(text, declared)
    return text


def _unmasked_values(text, key):
    """규칙 key 가 text 에서 잡는 값 중 아직 가려지지 않은 것들('*'와 구분자뿐인 값은 이미 가린 것)."""
    rule = MASK_RULES[key]
    group = rule.get("group", 0)
    values = []
    for m in rule["pattern"].finditer(text):
        resolved = _matched_group(m, group) if group else 0
        if resolved is not None and re.search(r"[A-Za-z0-9]", m.group(resolved)):
            values.append(m.group(resolved))
    return values


def count_matches(text, keys):
    """규칙마다 아직 가려지지 않은 값의 수 {key: n}(0 은 뺀다) — 마스킹 미리보기의 '가릴 값'과 누출 검사가 쓴다."""
    found = {}
    for key in keys:
        if key in MASK_RULES:
            count = len(_unmasked_values(text, key))
            if count:
                found[key] = count
    return found


def find_unmasked_secrets(text):
    """텍스트에 아직 가려지지 않은 비밀값이 있으면 규칙별 건수 {key: n} 를 돌려준다.

    마스킹 결과를 외부에 넘기기 전에 쓴다. 사용자가 비밀 항목을 선택하지 않았거나 규칙이 놓친
    경우를 알린다. '*' 와 구분자만 남은 값은 이미 가려진 것이므로 세지 않는다."""
    return count_matches(text, SECRET_KEYS)


def leak_report(masked_text, selected_keys, device_names=()):
    """마스킹 결과에 남은 것(IMPROVEMENT_PLAN 4-10 누출 검사) — 비밀만이 아니라 규칙이 잡는 모든 값과 장비 이름.

    rules: [{key, label, count, samples(앞 3개), secret, selected}] — 셋으로 읽는다:
      secret               비밀값이 남았다 — 공유하기 전에 가려야 한다
      selected(비밀 아님)  골랐는데 남았다 — 규칙이 놓친 모양이다(드물다)
      그 밖               고르지 않아 그대로 남는다 — 남겨도 되는지 확인할 것
    names: [{name, count}] — 장비 목록의 이름과 프롬프트에 찍힌 이름 중 결과에 그대로 남은 것('hostname X' 선언이
    없는 로그에서는 장비명 규칙만으로 가려지지 않는다)."""
    selected = set(selected_keys or ())
    rules = []
    for key in MASK_KEY_ORDER:
        values = _unmasked_values(masked_text, key)
        if values:
            rules.append({"key": key, "label": MASK_RULES[key]["label"], "count": len(values),
                          "samples": list(dict.fromkeys(values))[:3], "secret": key in SECRET_KEYS,
                          "selected": key in selected})
    names = {name for name in device_names if name and len(name) > 1} | set(_PROMPT_NAME_RE.findall(masked_text))
    found = []
    for name in sorted(names, key=str.lower):
        count = len(re.findall(r"\b" + re.escape(name) + r"\b", masked_text, re.IGNORECASE))
        if count:
            found.append({"name": name, "count": count})
    return {"rules": rules, "names": found,
            "secrets": sum(r["count"] for r in rules if r["secret"]),
            "missed": sum(r["count"] for r in rules if r["selected"] and not r["secret"])}


def preview_masking(text, selected_keys, device_names=(), max_lines=40):
    """한 파일을 마스킹해 보면 무엇이 바뀌고 무엇이 남는지 — 파일은 쓰지 않는다.

    마스킹은 글자 수를 지키므로(PEM 블록도 줄바꿈을 남긴다) 줄이 그대로 맞물린다. 반환: {lines, changed_lines,
    counts(규칙마다 가릴 값의 수), sample([{no, before, after}] 앞 max_lines 줄), leaks(leak_report)}."""
    selected = set(selected_keys or ())
    masked = apply_masking(text, selected)
    before, after = text.splitlines(), masked.splitlines()
    changed = [{"no": i + 1, "before": b, "after": a} for i, (b, a) in enumerate(zip(before, after)) if b != a]
    return {"lines": len(before), "changed_lines": len(changed), "counts": count_matches(text, selected),
            "sample": changed[:max_lines], "leaks": leak_report(masked, selected, device_names)}


def describe_unmasked(found):
    """find_unmasked_secrets() 결과 -> 사용자 경고 문구(없으면 None)."""
    if not found:
        return None
    parts = ", ".join(f"{MASK_RULES[key]['label']} {count}건" for key, count in found.items())
    return (f"마스킹 결과에 아직 가려지지 않은 비밀값이 있습니다 — {parts}. "
            "해당 항목을 선택해 다시 마스킹한 뒤 공유하세요.")


# 이 PC 밖의 AI(클라우드 제공자)로 보내기 전에 '항상' 적용하는 항목 — 사용자 선택과 무관하다.
# 비밀값 전부 + 사람·장비·주소 식별자. OS 버전·VLAN 이름은 남긴다: 분석 품질에 필요하고(알려진
# 버그 판정 등) 그 자체로 비밀이 아니다.
EXTERNAL_AI_KEYS = frozenset(SECRET_KEYS) | {"ip_address", "mac_address", "account_name",
                                             "device_hostname", "domain_name"}

# 프롬프트에 찍힌 장비명: "Core1#show version", "Core1(config-if-Et1)#", "Core1>"
_PROMPT_NAME_RE = re.compile(r"^[ \t]*([A-Za-z][\w.\-]{0,62})(?:\([^)\r\n]{0,40}\))?[#>]", re.MULTILINE)


def mask_for_external_ai(text, device_names=()):
    """외부 AI 로 나가는 텍스트의 강제 마스킹. device_names 와 프롬프트에 찍힌 장비명도 전부 가린다.

    device_hostname 규칙은 'hostname X' 선언이 같은 텍스트에 있어야 장비명을 안다. AI 로 보내는 것은
    원본 로그 중 의심 구간만이라 선언이 빠지는 일이 흔해서, 호출부가 아는 장비명(이 로그의 장비, 장비 목록의
    다른 장비 — 설명·LLDP 에 이웃으로 나온다)과 프롬프트에서 읽은 이름을 함께 가린다. 장비 목록이 수백 대여도
    텍스트를 한 번만 훑도록 긴 이름부터 하나의 패턴으로 묶는다(긴 이름이 먼저 맞아 'Agg1-mgmt' 가 통째로 가려진다)."""
    names = {name for name in device_names if name} | set(_PROMPT_NAME_RE.findall(text))
    masked = apply_masking(text, EXTERNAL_AI_KEYS)
    if not names:
        return masked
    pattern = re.compile(
        r"\b(?:" + "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True)) + r")\b",
        re.IGNORECASE)
    return pattern.sub(lambda m: _mask_span(m.group(0), ""), masked)


def run_masking(source_dir, masking_dir, selected_keys, device_names=()):
    """source_dir(원본/이상탐지 로그 폴더)의 모든 .txt를 마스킹해 masking_dir에 저장.
    쓰기는 StorageService(원자적 쓰기 + 로깅)로 위임한다. 읽기는 임의 폴더의 .txt를 모두
    훑어야 해서(단일 상대경로가 아님) 여기서는 직접 읽는다 — 디코딩은 다른 로그 읽기 경로와
    같은 규칙(core/text_io: UTF-8 → cp949)을 쓴다. 예전에는 UTF-8 고정이라 cp949 레거시 로그의
    한글이 깨진 채로 마스킹·저장됐다.
    반환: [{"source","output","unmasked_secrets","leaks"}] — unmasked_secrets 는 결과에 남은 비밀값 건수, leaks 는
    결과에 남은 모든 것(leak_report — device_names 는 장비 목록의 이름)."""
    from core.storage_service import storage_service, PathTarget
    from core.text_io import read_log_text
    masking_target = PathTarget(path=Path(masking_dir))
    results = []
    for path in sorted(glob.glob(os.path.join(source_dir, "*.txt"))):
        masked_text = apply_masking(read_log_text(path), selected_keys)
        out_name = os.path.splitext(os.path.basename(path))[0] + "_masked.txt"
        storage_service.save_text(masking_target, out_name, masked_text)
        results.append({"source": os.path.basename(path), "output": out_name,
                        "unmasked_secrets": find_unmasked_secrets(masked_text),
                        "leaks": leak_report(masked_text, selected_keys, device_names)})
    return results
