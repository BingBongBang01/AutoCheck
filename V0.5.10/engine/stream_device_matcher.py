"""StreamDeviceMatcher — SecureCRT 세션 로그 파일이 '어느 장비'의 것인지 판정.

이게 별도 모듈인 이유: 실시간 감시가 동작하지 않는 사고의 거의 전부가 이 판정 실패였다.
현장의 SecureCRT는 세션 로그 파일명을 장비명이 아니라 **접속 IP**로 남기고(%H가 호스트명이 아닌
접속 주소로 치환된다), 기본 로그 파일명이 그냥 session.log인 경우도 있다. 파일명만 믿으면
'192.168.205.101'이라는 장비를 찾다가 인벤토리에 없으니 조용히 전부 버려진다.

판정 순서(위에서 먼저 성공한 것을 씀):
  1. 파일명 토큰이 인벤토리 장비명과 일치            Core1_20260803.txt      -> Core1
  2. 파일명에 든 IP가 인벤토리 장비 IP와 일치         192.168.205.101_...txt  -> Core1
  3. 파일 내용의 '! device: X (' 헤더                 show running-config 출력에 항상 있다
  4. 파일 내용의 프롬프트 X# / X>                     session.log 같은 무의미한 파일명 구제
  5. 위가 다 실패하면 None — 호출부가 '식별 실패'로 노출한다(조용히 버리지 않는다).

3·4는 인벤토리에 있는 이름과 대조해 확정하지만, 인벤토리에 없더라도 프롬프트에서 얻은 호스트명은
'그 장비가 실제로 말한 이름'이라 신뢰할 수 있으므로 allow_unknown=True면 그대로 채택한다.
"""
import os
import re

_IP_RE = re.compile(r'(\d{1,3}(?:\.\d{1,3}){3})')
# Arista/Cisco 'show running-config' 출력 머리글 — 가장 신뢰도 높은 근거
_DEVICE_HEADER_RE = re.compile(r'^\s*!\s*device:\s*(\S+)', re.IGNORECASE | re.MULTILINE)
_HOSTNAME_RE = re.compile(r'^\s*hostname\s+(\S+)\s*$', re.IGNORECASE | re.MULTILINE)
_PROMPT_RE = re.compile(r'^([A-Za-z][\w.\-]{0,62})(?:\([^)]*\))?[#>]', re.MULTILINE)
_TOKEN_SPLIT_RE = re.compile(r'[_\-\s]+')


class StreamDeviceMatcher:
    """인벤토리(장비명 + IP)를 기준으로 로그 파일 -> 장비명을 판정한다.

    targets: [{"name": "Core1", "ip": "192.168.205.101", ...}, ...]
    """

    def __init__(self, targets=(), allow_unknown=False):
        self.allow_unknown = allow_unknown
        self._by_name = {}
        self._by_ip = {}
        self.set_targets(targets)
        self._cache = {}  # {abs_path: 장비명} — 파일 하나당 판정은 한 번이면 된다

    def set_targets(self, targets):
        self._by_name = {}
        self._by_ip = {}
        for t in targets or ():
            name = (t.get("name") or "").strip()
            if not name:
                continue
            self._by_name[name.lower()] = name
            ip = (t.get("ip") or "").strip()
            if ip:
                self._by_ip[ip] = name
        self._cache = {}

    # ---------- 판정 ----------
    def resolve(self, path, head_text=""):
        cached = self._cache.get(path)
        if cached:
            return cached
        device = (self._from_filename(path)
                  or self._from_content(head_text))
        if device:
            self._cache[path] = device
        return device

    def _from_filename(self, path):
        base = os.path.basename(path)
        if base.lower().endswith((".txt", ".log")):
            base = base.rsplit(".", 1)[0]

        # 1) 장비명 토큰 — 'AutoCheck_Core1_20260803_152204' 처럼 접두어가 붙어도 잡힌다
        for token in _TOKEN_SPLIT_RE.split(base):
            known = self._by_name.get(token.lower())
            if known:
                return known

        # 2) 파일명 속 IP
        for ip in _IP_RE.findall(base):
            if ip in self._by_ip:
                return self._by_ip[ip]
        return None

    def _from_content(self, head_text):
        if not head_text:
            return None
        for pattern in (_DEVICE_HEADER_RE, _HOSTNAME_RE, _PROMPT_RE):
            for candidate in pattern.findall(head_text):
                name = candidate.strip().rstrip(":")
                if not name:
                    continue
                known = self._by_name.get(name.lower())
                if known:
                    return known
                # 프롬프트/헤더에서 얻은 이름은 장비가 스스로 말한 호스트명이다.
                if self.allow_unknown and _looks_like_hostname(name):
                    return name
        return None

    # ---------- 진단 ----------
    def known_names(self):
        return sorted(self._by_name.values())

    def probe(self, path, head_text=""):
        """왜 매칭됐는지/안 됐는지를 사람이 읽을 수 있게 돌려준다(상태 표시·디버깅용)."""
        by_name = self._from_filename(path)
        by_content = self._from_content(head_text)
        return {
            "file": os.path.basename(path),
            "from_filename": by_name,
            "from_content": by_content,
            "resolved": by_name or by_content,
        }


def _looks_like_hostname(name):
    """프롬프트에서 뽑은 토큰이 장비 호스트명처럼 생겼는지 — 'Last', 'Password' 같은 배너 단어 배제."""
    if len(name) < 2 or len(name) > 63:
        return False
    if not re.match(r'^[A-Za-z][\w.\-]*$', name):
        return False
    return name.lower() not in {
        "last", "login", "password", "username", "warning", "error", "connecting",
        "connected", "authentication", "welcome", "unauthorized", "access",
    }
