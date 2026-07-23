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
        "pattern": re.compile(r"(?:password|secret|hash)\s+(?:\d\s+)?(\S+)", re.IGNORECASE),
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
        "pattern": re.compile(r"^\d{1,4}\s+(\S+)\s+(?:active|suspend)", re.IGNORECASE | re.MULTILINE),
        "group": 1,
        "delimiters": "_-",
    },
    "domain_name": {
        "label": "Domain Name",
        "pattern": re.compile(r"\b[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
        "delimiters": ".",
    },
}

MASK_KEY_ORDER = ["ip_address", "mac_address", "password_hash", "account_name",
                  "device_hostname", "os_version", "vlan_name", "domain_name"]


def get_mask_options():
    """'Log Masking' 탭 체크리스트용 — [{"key","label"}, ...] (표시 순서 고정)."""
    return [{"key": key, "label": MASK_RULES[key]["label"]} for key in MASK_KEY_ORDER]


def _mask_span(token, delimiters):
    """delimiters에 포함된 문자만 그대로 두고 나머지는 전부 '*'로 — 원본과 길이가 항상 같다."""
    return "".join(ch if ch in delimiters else "*" for ch in token)


def _apply_rule(text, rule):
    pattern = rule["pattern"]
    delimiters = set(rule.get("delimiters", ""))
    group = rule.get("group", 0)

    def repl(m):
        whole = m.group(0)
        if not group:
            return _mask_span(whole, delimiters)
        target = m.group(group)
        rel_start = m.start(group) - m.start(0)
        rel_end = m.end(group) - m.start(0)
        return whole[:rel_start] + _mask_span(target, delimiters) + whole[rel_end:]

    return pattern.sub(repl, text)


def apply_masking(text, selected_keys):
    """selected_keys에 있는 규칙만, 정의 순서대로 순차 적용."""
    for key in MASK_KEY_ORDER:
        if key in selected_keys and key in MASK_RULES:
            text = _apply_rule(text, MASK_RULES[key])
    return text


def run_masking(source_dir, masking_dir, selected_keys):
    """source_dir(00_orignal_log 또는 01_problem_log)의 모든 .txt를 마스킹해 masking_dir에 저장.
    반환: [{"source","output"}]."""
    results = []
    for path in sorted(glob.glob(os.path.join(source_dir, "*.txt"))):
        with open(path, encoding="utf-8", errors="replace") as f:
            raw_text = f.read()
        masked_text = apply_masking(raw_text, selected_keys)
        os.makedirs(masking_dir, exist_ok=True)
        out_name = os.path.splitext(os.path.basename(path))[0] + "_masked.txt"
        with open(os.path.join(masking_dir, out_name), "w", encoding="utf-8") as f:
            f.write(masked_text)
        results.append({"source": os.path.basename(path), "output": out_name})
    return results
