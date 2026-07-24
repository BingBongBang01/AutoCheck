"""
Format-preserving 마스킹 — 구조적 구분자(마침표/콜론/슬래시 등)는 그대로 두고
그 구분자 사이의 실제 값(영숫자)만 원본과 정확히 같은 글자 수의 '*'로 치환한다.

예: 192.20.1.3        -> ***.**.*.*      (마침표 유지, 숫자만 자릿수만큼 마스킹)
    5001.004b.6277    -> ****.****.****  (Cisco MAC 표기 — 4자리 hex 그룹 3개, 마침표 유지)

체크리스트 8종(IP/MAC/Password Hash/Account Name/Device Hostname/OS Version/
Internal VLAN Name/Domain Name)을 사용자가 원하는 것만 선택해 적용할 수 있도록
각 항목을 독립된 규칙(정규식 + 보존할 구분자 문자 집합)으로 정의한다.
"""
import os
import re
import glob
from pathlib import Path

# 순서 = UI 체크리스트에 표시할 순서(과제에서 요구한 정확한 8개 항목/순서 그대로).
MASK_RULES = {
    "ip_address": {
        "label": "IP Address",
        "pattern": re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
        "delimiters": ".",
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
            r"(?:password|secret|hash)\s+(?:(?:\d+|sha256|sha512|md5|scrypt|crypt)\s+)?(\S+)",
            re.IGNORECASE,
        ),
        "group": 1,
        "delimiters": "$./",
    },
    "account_name": {
        "label": "Account Name",
        "pattern": re.compile(r"(?:username|user)\s+(\S+)", re.IGNORECASE),
        "group": 1,
        "delimiters": "",
    },
    "device_hostname": {
        "label": "Device Hostname",
        "pattern": re.compile(r"hostname\s+(\S+)", re.IGNORECASE),
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
        "pattern": re.compile(r"(?:snmp-server\s+community|community-string)\s+(\S+)", re.IGNORECASE),
        "group": 1,
        "delimiters": "",
    },
    "secret_token": {
        "label": "Secrets / API Key / Token",
        "pattern": re.compile(
            r"(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|bearer)\s*[:=]?\s+(\S+)",
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
}

MASK_KEY_ORDER = ["ip_address", "mac_address", "password_hash", "account_name",
                  "device_hostname", "os_version", "vlan_name", "domain_name",
                  "snmp_community", "secret_token", "private_key"]

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


def _mask_hostname_everywhere(text):
    """"hostname X"/"! device: X"에서 뽑아낸 실제 장비명 X를, 그 선언 줄뿐 아니라
    프롬프트("X(config)#"), syslog 줄("... X ..."), 상단 주석 등 텍스트 전체에서
    같은 토큰으로 등장하는 곳까지 전부 같은 글자 수의 '*'로 마스킹한다.
    device_hostname 규칙 하나만으론 "hostname X" 줄 자체만 가려지고 X라는 값이
    본문 다른 곳에 그대로 노출되는 문제가 있어서 별도 후처리로 보완."""
    names = {m.group(1).rstrip(",()") for m in _HOSTNAME_DECL_RE.finditer(text)}
    for name in names:
        if not name:
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
    for key in order:
        if key in selected_keys and key in rules:
            text = _apply_rule(text, rules[key])
    if "device_hostname" in selected_keys:
        text = _mask_hostname_everywhere(text)
    return text


def run_masking(source_dir, masking_dir, selected_keys):
    """source_dir(원본/이상탐지 로그 폴더)의 모든 .txt를 마스킹해 masking_dir에 저장.
    쓰기는 StorageService(원자적 쓰기 + 로깅)로 위임한다. 읽기는 임의 폴더의 .txt를 모두
    훑어야 해서(단일 상대경로가 아님) 여기서는 open()으로 직접 읽는다.
    반환: [{"source","output"}]."""
    from core.storage_service import storage_service, PathTarget
    masking_target = PathTarget(path=Path(masking_dir))
    results = []
    for path in sorted(glob.glob(os.path.join(source_dir, "*.txt"))):
        with open(path, encoding="utf-8", errors="replace") as f:
            raw_text = f.read()
        masked_text = apply_masking(raw_text, selected_keys)
        out_name = os.path.splitext(os.path.basename(path))[0] + "_masked.txt"
        storage_service.save_text(masking_target, out_name, masked_text)
        results.append({"source": os.path.basename(path), "output": out_name})
    return results
