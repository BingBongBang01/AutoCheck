"""장비 이름 맞추기 — LLDP 이웃 이름·세션 로그 파일명·프롬프트·점검 로그의 장비명을 장비 목록의 장비명으로
(IMPROVEMENT_PLAN 4-10).

장비는 여러 이름으로 불린다: 장비 목록의 이름(Core1), 장비가 스스로 말하는 호스트명(core1, LLDP 의 core1.lab.local),
SecureCRT 가 파일명에 넣는 이름, 바꾸기 전의 옛 이름. 예전엔 곳곳에서 따로 맞췄다 — 구성도는 대소문자 무시 + 도메인
떼기, 실시간 감시의 파일 판정은 대소문자 무시만, 보고서는 대소문자·공백 무시. 같은 장비가 화면마다 다르게 묶였다.
이제 이 색인 하나가 정한다:
  1. 장비명과 별칭(장비 목록의 aliases) — 대소문자 무시
  2. 그 이름들의 도메인을 뗀 짧은 이름('core1.lab.local' -> 'core1') — 두 장비가 같은 짧은 이름을 가지면 그 이름으로는
     맞추지 않는다(엉뚱한 장비에 붙이느니 못 맞췄다고 하는 편이 낫다)
주소(IP·MAC)처럼 생긴 이름은 도메인을 떼지 않는다 — '10.0.0.1' 의 앞부분 '10' 은 이름이 아니다.
"""
import re

_ADDRESS_RE = re.compile(
    r"^(?:\d{1,3}(?:\.\d{1,3}){3}"                    # IPv4
    r"|[0-9a-f]{4}(?:\.[0-9a-f]{4}){2}"               # Cisco MAC 표기
    r"|(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}"             # MAC
    r"|[0-9a-f:]*:[0-9a-f:]+)$", re.IGNORECASE)        # IPv6


def is_address(name):
    return bool(_ADDRESS_RE.match((name or "").strip()))


def clean(name):
    return str(name or "").strip().rstrip(".")


def short_name(name):
    """도메인을 뗀 이름 — 주소처럼 생겼으면 그대로."""
    name = clean(name)
    return name if is_address(name) else name.split(".")[0]


def aliases_of(device):
    """장비 목록 항목의 별칭(목록 또는 쉼표로 이은 글)."""
    value = (device or {}).get("aliases")
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    return [clean(a) for a in items if clean(a)]


class DeviceNameIndex:
    """devices: 장비 목록 항목(dict — name, aliases) 또는 장비명 문자열들."""

    def __init__(self, devices=()):
        self._exact = {}          # {소문자 이름·별칭: 장비명} — 두 장비의 별칭이 겹치면 None
        self._short = {}          # {소문자 짧은 이름: 장비명} — 두 장비가 겹치면 None
        records = []
        for device in devices or ():
            record = device if isinstance(device, dict) else {"name": device}
            name = clean(record.get("name"))
            if name:
                records.append((name, aliases_of(record)))
        names = {name.lower() for name, _ in records}
        for name, _aliases in records:                       # 장비명이 다른 장비의 별칭보다 먼저다
            self._exact.setdefault(name.lower(), name)
        for name, aliases in records:
            for alias in aliases:
                key = alias.lower()
                if key in names:
                    continue
                self._exact[key] = name if self._exact.get(key, name) == name else None
        for name, _aliases in records:                       # 짧은 이름도 장비명이 먼저다
            key = short_name(name).lower()
            self._short[key] = name if self._short.get(key, name) == name else None
        owned = set(self._short)
        for name, aliases in records:
            for alias in aliases:
                key = short_name(alias).lower()
                if key not in owned:
                    self._short[key] = name if self._short.get(key, name) == name else None

    def resolve(self, raw):
        """이름 -> 장비 목록의 장비명. 못 맞추면 None."""
        name = clean(raw)
        if not name:
            return None
        hit = self._exact.get(name.lower())
        if hit:
            return hit
        if is_address(name):
            return None
        return self._short.get(short_name(name).lower())

    def ambiguous(self):
        """두 장비 이상이 같은 별칭·짧은 이름을 가져 그 이름으로는 맞추지 않는 것들 — 화면이 알릴 수 있게."""
        return sorted({key for table in (self._exact, self._short) for key, value in table.items() if value is None})
