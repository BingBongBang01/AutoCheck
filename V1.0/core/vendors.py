"""지원하는 장비 플랫폼 — 인벤토리의 vendor 칸에서 접속 방식(netmiko device_type)을 정하는 단일 출처(IMPROVEMENT_PLAN 4-5).

vendor 칸은 사람이 적는 글자라('Arista', 'Cisco', 'Nexus 9300' …) 여기서 플랫폼 id 하나로 읽는다. 비어 있으면 예전처럼
Arista EOS 로 본다 — 이 칸을 읽기 전의 앱은 모든 장비를 arista_eos 로 접속했다. 모르는 벤더는 None 이다: 접속하는 쪽은
그런 장비에 Arista 명령을 보내지 않고 '지원하지 않는 벤더'로 알린다.
"""
import re

ARISTA_EOS = "arista_eos"
CISCO_IOS = "cisco_ios"
CISCO_XE = "cisco_xe"
CISCO_NXOS = "cisco_nxos"

# 플랫폼 id -> 화면 표시, netmiko device_type, 장비 목록의 벤더 선택이 vendor 칸에 저장하는 글자,
# blank_enable: enable 비밀번호 없이 enable 을 시도해 볼지 — EOS 는 기본으로 enable 비밀번호가 없다. Cisco 는 '>' 에서
# 비밀번호 없이 enable 하면 실패만 남으므로(장비에 인증 실패 기록) 시도하지 않고 비밀번호를 넣으라고 알린다.
PLATFORMS = {
    ARISTA_EOS: {"label": "Arista EOS", "netmiko": "arista_eos", "vendor": "Arista", "blank_enable": True},
    CISCO_IOS: {"label": "Cisco IOS", "netmiko": "cisco_ios", "vendor": "Cisco IOS", "blank_enable": False},
    CISCO_XE: {"label": "Cisco IOS-XE", "netmiko": "cisco_xe", "vendor": "Cisco IOS-XE", "blank_enable": False},
    CISCO_NXOS: {"label": "Cisco NX-OS", "netmiko": "cisco_nxos", "vendor": "Cisco NX-OS", "blank_enable": False},
}
DEFAULT_PLATFORM = ARISTA_EOS

# 'Cisco' 가 들어 있어도 IOS 계열 명령이 통하지 않는 제품 — Cisco IOS 로 읽어 접속하지 않는다.
_OTHER_CISCO_WORDS = {"xr", "iosxr", "asa", "ftd", "firepower", "wlc", "aireos", "meraki", "ucs"}

UNSUPPORTED_TEXT = ("지원하지 않는 벤더입니다 — 장비 목록의 벤더를 "
                    + "·".join(p["label"] for p in PLATFORMS.values()) + " 중 하나로 고르세요")


def platform_of(vendor):
    """vendor 칸 -> 플랫폼 id(PLATFORMS 의 키). 비어 있으면 Arista EOS, 모르는 벤더면 None."""
    raw = str(vendor or "").strip().lower()
    if not raw:
        return DEFAULT_PLATFORM
    if raw in PLATFORMS:
        return raw
    words = re.findall(r"[a-z0-9]+", raw)          # 'Cisco IOS-XE' -> ['cisco', 'ios', 'xe']
    joined = "".join(words)
    if "arista" in joined or "eos" in words:
        return ARISTA_EOS
    if "nxos" in joined or "nexus" in joined:
        return CISCO_NXOS
    if _OTHER_CISCO_WORDS.intersection(words) or "iosxr" in joined:
        return None
    if "iosxe" in joined:
        return CISCO_XE
    if {"cisco", "ios", "catalyst"}.intersection(words):
        return CISCO_IOS
    return None


def netmiko_type(platform):
    """플랫폼 id -> netmiko device_type. 모르는 플랫폼이면 None."""
    entry = PLATFORMS.get(platform)
    return entry["netmiko"] if entry else None


def enable_without_secret(platform):
    """enable 비밀번호가 없을 때도 enable 을 시도해 볼 플랫폼인지(PLATFORMS 의 blank_enable)."""
    entry = PLATFORMS.get(platform)
    return bool(entry and entry["blank_enable"])


def label(platform):
    entry = PLATFORMS.get(platform)
    return entry["label"] if entry else "지원하지 않는 벤더"
