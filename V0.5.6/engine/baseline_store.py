"""BaselineStore — 사전 점검 로그(00_orignal_log)에서 뽑아낸 '정상 설정 스냅샷'을 메모리에 보관.

실시간 Diff(engine/baseline_diff_engine.py)는 스트리밍으로 들어오는 CLI 한 줄과 이 스냅샷을
대조하기만 하므로, 파일 재파싱은 프로파일을 로드할 때 딱 한 번만 일어난다.

스냅샷은 장비별 dict이며 값은 모두 set(자료구조 비교가 아니라 '존재했는가' 판정만 필요):
    {"vlans": {"100", ...}, "interfaces": {"Ethernet1", ...},
     "routes": {"10.0.0.0/24", ...}, "bgp_neighbors": {"10.1.1.2", ...}}
"""
import glob
import os
import re

from core.ansi_sanitizer import clean_terminal_log

# show vlan / running-config 양쪽에서 VLAN ID를 잡는다.
_VLAN_LINE_RE = re.compile(r'^\s*(?:vlan\s+)?(\d{1,4})\b')
_VLAN_CFG_RE = re.compile(r'^\s*vlan\s+([\d,\-\s]+)\s*$', re.IGNORECASE)
_INTERFACE_RE = re.compile(
    r'^\s*(?:interface\s+)?((?:Ethernet|Eth|Port-Channel|Po|Vlan|Management|Ma|Loopback|Lo|'
    r'GigabitEthernet|Gi|TenGigabitEthernet|Te|FortyGigE|Fo)[\d/\.:]+)', re.IGNORECASE)
_ROUTE_RE = re.compile(r'(\d{1,3}(?:\.\d{1,3}){3}/\d{1,2})')
_BGP_NEIGHBOR_RE = re.compile(r'^\s*neighbor\s+(\d{1,3}(?:\.\d{1,3}){3})', re.IGNORECASE)
_BGP_SUMMARY_PEER_RE = re.compile(r'^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+\d+\s+')

_EMPTY = {"vlans": set(), "interfaces": set(), "routes": set(), "bgp_neighbors": set()}


def device_from_filename(filename):
    """로그 파일명에서 장비명 추출.

    지원 형태(수집 경로마다 규칙이 달라 셋 다 쓰인다):
      AutoCheck_<device>_<YYYYMMDD>_<HHMMSS>.txt
      <YYYYMMDD>_<HHMMSS>_..._<device>.txt
      <device>_<무엇이든>.txt  /  <device>.txt
    """
    body = os.path.basename(filename)
    if body.lower().endswith(".txt"):
        body = body[:-4]
    if body.startswith("AutoCheck_"):
        rest = body[len("AutoCheck_"):]
        parts = rest.rsplit("_", 2)
        return parts[0] if len(parts) == 3 else rest
    parts = body.split("_", 3)
    if len(parts) == 4 and parts[0].isdigit() and parts[1].isdigit():
        return parts[3]
    return parts[0]


def _expand_vlan_tokens(token):
    """'10,20,30-32' -> {'10','20','30','31','32'}"""
    out = set()
    for chunk in token.replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk:
            lo, _, hi = chunk.partition("-")
            if lo.isdigit() and hi.isdigit() and int(lo) <= int(hi) and int(hi) - int(lo) <= 4094:
                out.update(str(v) for v in range(int(lo), int(hi) + 1))
        elif chunk.isdigit():
            out.add(str(int(chunk)))
    return out


def parse_baseline_text(raw_text):
    """단일 장비의 사전 점검 로그 원문에서 Baseline 스냅샷 dict를 만든다."""
    snapshot = {"vlans": set(), "interfaces": set(), "routes": set(), "bgp_neighbors": set()}
    in_bgp = False
    for line in clean_terminal_log(raw_text).splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        cfg_vlan = _VLAN_CFG_RE.match(line)
        if cfg_vlan:
            snapshot["vlans"].update(_expand_vlan_tokens(cfg_vlan.group(1)))
        else:
            # show vlan 출력: '100      DATA    active    Et1, Et2'
            m = _VLAN_LINE_RE.match(line)
            if m and ("active" in stripped.lower() or "suspend" in stripped.lower()):
                snapshot["vlans"].add(str(int(m.group(1))))

        m = _INTERFACE_RE.match(line)
        if m:
            snapshot["interfaces"].add(_normalize_interface(m.group(1)))

        low = stripped.lower()
        if low.startswith("ip route") or low.startswith("ipv6 route") or low[:2] in ("b ", "o ", "c ", "s "):
            for cidr in _ROUTE_RE.findall(stripped):
                snapshot["routes"].add(cidr)

        if low.startswith("router bgp"):
            in_bgp = True
        elif in_bgp and stripped and not line.startswith((" ", "\t")):
            in_bgp = False
        nb = _BGP_NEIGHBOR_RE.match(line)
        if nb:
            snapshot["bgp_neighbors"].add(nb.group(1))
        else:
            peer = _BGP_SUMMARY_PEER_RE.match(line)
            if peer and ("estab" in low or "idle" in low or "active" in low or "connect" in low):
                snapshot["bgp_neighbors"].add(peer.group(1))
    return snapshot


def _normalize_interface(name):
    """'Et1' / 'ethernet1' / 'Po10' 등 축약·대소문자 표기를 정규형으로 통일."""
    m = re.match(r'^([A-Za-z\-]+)([\d/\.:]+)$', name)
    if not m:
        return name
    alias, num = m.group(1).lower().replace("-", ""), m.group(2)
    table = {
        "et": "Ethernet", "eth": "Ethernet", "ethernet": "Ethernet",
        "po": "Port-Channel", "portchannel": "Port-Channel",
        "vl": "Vlan", "vlan": "Vlan",
        "ma": "Management", "management": "Management",
        "lo": "Loopback", "loopback": "Loopback",
        "gi": "GigabitEthernet", "gigabitethernet": "GigabitEthernet",
        "te": "TenGigabitEthernet", "tengigabitethernet": "TenGigabitEthernet",
        "fo": "FortyGigE", "fortygige": "FortyGigE",
    }
    return f"{table.get(alias, alias.capitalize())}{num}"


normalize_interface = _normalize_interface


# 점검(SSH) 세션이 남긴 원본 로그의 이름 규칙 — api/terminal_inspection_api.py가
# f"{stamp}_raw_{device}.txt" 로 저장한다. 수동 SecureCRT 세션 로그가
# scan_crt_log_directory()로 같은 00_orignal_log에 복사되기 때문에, 이름으로 둘을 갈라야 한다.
_INSPECTION_FILE_RE = re.compile(r'^\d{8}_\d{6}_raw_.+\.txt$', re.IGNORECASE)


def is_inspection_log(path):
    """이 파일이 '점검 SSH 세션'이 만든 것인가(=Baseline 기준으로 삼을 자격이 있는가)."""
    return bool(_INSPECTION_FILE_RE.match(os.path.basename(path)))


class BaselineStore:
    """고객사/프로파일 단위 Baseline 스냅샷 캐시."""

    def __init__(self):
        self._devices = {}      # {device_name: snapshot dict}
        self._key = None        # (customer, profile) — 중복 로드 방지
        self._source_files = {}  # {device_name: 사용한 로그 파일 경로}
        self._source_kind = None  # "inspection" | "mixed"

    # ---------- 로드 ----------
    def load_baseline(self, customer, profile, original_dir=None, prefer_inspection=True):
        """활성 프로파일의 00_orignal_log/*.txt를 읽어 장비별 스냅샷을 메모리에 올린다.
        같은 장비의 로그가 여럿이면 mtime이 가장 최신인 파일 하나만 기준으로 삼는다.
        반환: {"ok": True, "devices": [장비명...], "loaded": n, "source_kind": ...}

        prefer_inspection=True(기본)이면 점검 SSH 세션 로그({stamp}_raw_{device}.txt)만 쓴다.
        이 격리가 필요한 이유:
            scan_crt_log_directory()가 수동 SecureCRT 세션 로그(CRTlog/)를 같은
            00_orignal_log로 복사한다. 그런데 실시간 감시는 바로 그 CRT 세션 파일을 tail하며
            Baseline과 대조한다. 격리하지 않으면 '지금 감시 중인 세션'이 자기 자신의 Baseline이
            되어, 작업자가 방금 친 `no vlan 100`이 Baseline에 반영돼 버리고 그 다음 대조에서는
            아무 변경도 감지되지 않는다(감시가 조용히 무력화된다).

            점검 로그가 하나도 없으면(사전 점검을 아직 한 번도 안 한 첫 방문) 전체 파일로
            폴백한다 — 그래야 기능이 아예 죽지 않고, source_kind로 그 사실을 알린다.
        """
        if original_dir is None:
            from engine import log_storage
            paths_dict = log_storage.get_profile_log_paths(customer, profile)
            if not paths_dict:
                # 점검 회차가 하나도 없다 = 기준으로 삼을 사전 점검 로그가 없다.
                self._devices, self._source_files, self._source_kind = {}, {}, None
                self._key = (customer, profile)
                return {"ok": True, "devices": [], "loaded": 0, "source_kind": None,
                        "skipped_manual": 0}
            original_dir = paths_dict["original"]

        all_paths = glob.glob(os.path.join(original_dir, "*.txt"))
        inspection_paths = [p for p in all_paths if is_inspection_log(p)]
        if prefer_inspection and inspection_paths:
            paths, source_kind = inspection_paths, "inspection"
        else:
            paths, source_kind = all_paths, "mixed"

        latest = {}  # {device: (mtime, path)}
        for path in paths:
            device = device_from_filename(path)
            if not device:
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            prev = latest.get(device)
            if prev is None or mtime > prev[0]:
                latest[device] = (mtime, path)

        devices, sources = {}, {}
        for device, (_mtime, path) in latest.items():
            try:
                from api.log_file_browser_api import _read_text_auto
                raw = _read_text_auto(path)
            except (OSError, UnicodeDecodeError):
                continue
            devices[device] = parse_baseline_text(raw)
            sources[device] = path

        # 감시 스레드가 get_device_baseline()으로 동시에 읽는다. dict를 제자리에서 고치지 않고
        # 완성된 새 dict를 한 번에 갈아끼우므로(속성 재바인딩은 원자적) 감시를 멈출 필요가 없다.
        self._devices = devices
        self._source_files = sources
        self._source_kind = source_kind
        self._key = (customer, profile)
        return {"ok": True, "devices": sorted(devices), "loaded": len(devices),
                "source_kind": source_kind,
                "skipped_manual": len(all_paths) - len(paths)}

    # ---------- 조회 ----------
    def get_device_baseline(self, device_name):
        """특정 장비의 Baseline dict(없으면 빈 스냅샷). 장비명은 대소문자 무시 매칭.

        지역 변수로 한 번 받아 쓴다 — 읽는 도중 load_baseline()이 스냅샷을 갈아끼워도
        한 호출 안에서는 같은 세대의 데이터만 보게 하기 위함.
        """
        snapshot = self._devices
        if device_name in snapshot:
            return snapshot[device_name]
        low = (device_name or "").lower()
        for name, snap in snapshot.items():
            if name.lower() == low:
                return snap
        return _EMPTY

    def has_device(self, device_name):
        low = (device_name or "").lower()
        return any(name.lower() == low for name in self._devices)

    @property
    def source_kind(self):
        """"inspection"=점검 SSH 로그만 사용(정상) / "mixed"=수동 CRT 로그도 섞임(격리 실패)."""
        return self._source_kind

    @property
    def key(self):
        return self._key

    def device_names(self):
        return sorted(self._devices)

    def summary(self):
        """UI 표시용 — 장비별 항목 개수."""
        return {
            name: {k: len(v) for k, v in snap.items()}
            for name, snap in self._devices.items()
        }

    def clear(self):
        self._devices = {}
        self._source_files = {}
        self._key = None
        self._source_kind = None
