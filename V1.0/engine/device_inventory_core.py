"""
Device Inventory 핵심 — 프로젝트의 장비 정보(IP·계정·역할·벤더 등)를 관리하는 유일한 출처.

기존엔 lab_meta.yaml(name/role)과 ip_allocation.yaml(ip/계정)로 쪼개져 있었고,
Collection이 그 두 파일을 직접 읽어 SSH 접속했음 — 이게 "IP가 코드 여기저기 흩어짐" 문제의 원인.
이제 device_inventory.yaml 하나로 통합하고, Collection은 이 모듈이 제공하는
get_enabled_devices()만 사용해서 접속 대상을 얻는다 (IP를 직접 다루지 않음).

기존 프로젝트(lab_meta.yaml + ip_allocation.yaml만 있던 것)는 최초 load 시 자동 마이그레이션.

Import/Export는 device_inventory_import_export.py, 도달가능성 체크는
device_inventory_reachability.py로 분리되어 있다.

비밀값(장비 비밀번호·붙여넣은 개인키·passphrase, 기본 비밀번호)은 디스크에 봉인해서 둔다(core/secret_vault —
IMPROVEMENT_PLAN 4-1). load_inventory 가 풀어서 돌려주고 save_inventory 가 봉인해서 쓰므로, 메모리의 inventory 는
평문이고 접속하는 쪽(수집·터미널·연결 확인)은 바뀌지 않는다. 화면(JS)에는 public_device()/public_defaults() 로
비밀값 대신 저장 여부(has_*)만 보내고, 화면이 보낸 목록은 merge_screen_devices() 가 장비 id 로 기존 비밀값을 잇는다.
"""
import hashlib
import os
import re
import yaml
from core import vendors
from core.atomic_io import dump_yaml_atomic
from core.device_names import aliases_of
from core.secret_vault import VaultError, reveal, seal

# location/warranty 는 정기점검 보고서(PDF '장비 목록' 페이지의 위치·Warranty 열)를 위해
# 추가된 필드다. 예전에는 그 두 열이 채울 데이터 자체가 없어 항상 공란으로 출력됐다.
# id: 장비마다 고정된 식별자 — 화면은 비밀값을 모르므로, 저장할 때 이 id 로 기존 비밀값을 잇는다(이름은 바뀐다).
# vendor: 접속 방식(netmiko device_type)을 고른다 — core/vendors.platform_of. enable_secret: enable(특권 모드) 비밀번호
# (Cisco 처럼 로그인 뒤 '>' 에서 시작하는 장비용, IMPROVEMENT_PLAN 4-5).
DEVICE_FIELDS = ["id", "name", "aliases", "role", "management_ip", "ssh_port", "username", "password", "enable_secret",
                  "vendor", "model", "zone", "site", "location", "warranty",
                  "tag", "memo", "enabled",
                  "auth_method", "key_path", "key_content", "key_passphrase"]

# 디스크에는 봉인하고 화면에는 보내지 않는 칸.
DEVICE_SECRET_FIELDS = ("password", "key_content", "key_passphrase", "enable_secret")
DEFAULTS_SECRET_FIELDS = ("default_password", "default_enable_secret")
# 메모리에만 있는 표시 — 봉인을 이 PC 에서 풀지 못한 칸 {칸: 원래 봉인 문자열}. 저장할 때 원래 값을 되돌려 쓴다.
UNREADABLE_KEY = "unreadable_secrets"

DEFAULT_DEVICE = {
    # aliases: 이 장비의 다른 이름(FQDN·옛 호스트명·LLDP 시스템 이름 — core/device_names, IMPROVEMENT_PLAN 4-10)
    "id": "", "name": "", "aliases": [], "role": "", "management_ip": "", "ssh_port": 22,
    "username": "", "password": "", "enable_secret": "", "vendor": "Arista", "model": "",
    "zone": "", "site": "", "location": "", "warranty": "",
    "tag": [], "memo": "", "enabled": True,
    "auth_method": "password", "key_path": "", "key_content": "", "key_passphrase": "",
}

# 기본 계정은 비워 둔다 — 예전 admin/admin 기본값은 비밀번호를 넣지 않은 장비를 admin/admin 으로
# 접속하게 만들었다(장비 N대 × 재시도 3회의 로그인 실패 → AAA 계정 잠금 위험).
DEFAULT_PROJECT_DEFAULTS = {
    "management_network": "",
    "default_ssh_port": 22,
    "default_username": "",
    "default_password": "",
    "default_enable_secret": "",
    "ip_pool": {"prefix": "", "start": 0, "end": 0},
}


def normalize_device(raw):
    """장비 dict 하나를 표준 스키마로 정규화 — 모르는 키는 버리고 빠진 키는 기본값으로 채운다.

    UI에서 넘어온 dict에는 화면 전용 필드(_rid 등)가 섞여 있으므로 저장 전에 반드시 거쳐야 한다.
    api/ 계층에서도 쓰기 때문에 밑줄 없는 공개 이름이다 — 예전엔 `_normalize_device`만 있어서
    facade(device_inventory.py)가 재노출하지 않았고, 그걸 호출하던 save_devices()가
    AttributeError로 죽어 장비목록 저장이 통째로 안 됐다.
    """
    d = dict(DEFAULT_DEVICE)
    d.update({k: v for k, v in raw.items() if k in DEVICE_FIELDS})
    # 파일에서 온 값의 형태를 맞춘다. 엑셀 내보내기는 enabled 를 'TRUE'/'FALSE' 문자열로, tag 를 'a,b' 로,
    # 포트를 22.0 처럼 쓴다. 'FALSE' 를 문자열 그대로 두면 참으로 읽혀서 꺼 둔 장비가 수집 대상이 됐다
    # (get_enabled_devices). 이 함수가 저장·불러오기·가져오기 모두의 입구라 여기서 한 번에 고친다.
    d["enabled"] = _parse_bool(d.get("enabled"), True)
    d["tag"] = _parse_tags(d.get("tag"))
    own = str(d.get("name") or "").strip().lower()
    d["aliases"] = list(dict.fromkeys(a for a in _parse_tags(d.get("aliases")) if a.lower() != own))
    d["ssh_port"] = _parse_port(d.get("ssh_port"))
    return d


_TRUE_WORDS = {"true", "1", "y", "yes", "o", "on", "사용", "예", "checked"}
_FALSE_WORDS = {"false", "0", "n", "no", "x", "off", "미사용", "아니오", "unchecked"}


def _parse_bool(value, default):
    """엑셀·CSV·YAML 칸 값 -> bool. 빈 칸과 알 수 없는 값은 default."""
    if isinstance(value, bool):
        return value
    if value is None or (isinstance(value, float) and value != value):    # 빈 칸(None/NaN)
        return default
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    return default


def _parse_tags(value):
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [str(t).strip() for t in value if str(t).strip()]
    return [t.strip() for t in str(value).split(",") if t.strip()]


def _parse_port(value):
    """'22'·22.0 -> 22. 빈 칸은 ''(프로젝트 기본 포트를 쓰게), 숫자가 아니면 그대로 둔다."""
    if value is None or (isinstance(value, float) and value != value) or value == "":
        return ""
    if isinstance(value, bool):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return int(number) if number.is_integer() else value


_normalize_device = normalize_device  # 기존 내부 호출부 호환


def _migrate_from_legacy(lab_meta_path, ip_allocation_path):
    """구버전(lab_meta.yaml + ip_allocation.yaml)에서 device_inventory 구조로 변환."""
    devices = []
    lab_meta, ip_alloc = {}, {}
    if os.path.exists(lab_meta_path):
        with open(lab_meta_path, encoding="utf-8") as f:
            lab_meta = yaml.safe_load(f) or {}
    if os.path.exists(ip_allocation_path):
        with open(ip_allocation_path, encoding="utf-8") as f:
            ip_alloc = yaml.safe_load(f) or {}

    alloc_by_name = {a.get("node_name"): a for a in ip_alloc.get("allocations", [])}
    # ip_allocation.yaml 의 비밀번호도 업그레이드 단계에서 봉인된다(engine/secret_files).
    open_secret = _reveal_or_empty
    for d in lab_meta.get("devices", []):
        alloc = alloc_by_name.get(d["name"], {})
        devices.append(_normalize_device({
            "name": d["name"], "role": d.get("role", ""),
            "management_ip": alloc.get("ip", ""),
            "username": alloc.get("username", "") or "",
            "password": open_secret(alloc.get("password", "") or ""),
            "vendor": "Arista", "model": d.get("platform", ""),
            "enabled": True,
        }))

    defaults = dict(DEFAULT_PROJECT_DEFAULTS)
    creds = ip_alloc.get("default_credentials", {})
    if creds:
        defaults["default_username"] = creds.get("username", "") or ""
        defaults["default_password"] = open_secret(creds.get("password", "") or "")

    return {"defaults": defaults, "devices": devices}


def load_inventory(inventory_path, lab_meta_path=None, ip_allocation_path=None):
    """
    device_inventory.yaml이 있으면 그대로 로드.
    없고 구버전 파일이 있으면 자동 마이그레이션(파일도 새로 저장해서 다음부턴 바로 로드).
    """
    if os.path.exists(inventory_path):
        with open(inventory_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        data.setdefault("defaults", dict(DEFAULT_PROJECT_DEFAULTS))
        _reveal_record(data["defaults"], DEFAULTS_SECRET_FIELDS)
        devices = [_normalize_device(d) for d in data.get("devices", [])]
        for device in devices:
            _reveal_record(device, DEVICE_SECRET_FIELDS)
        data["devices"] = ensure_device_ids(devices)
        return data

    if lab_meta_path and ip_allocation_path:
        migrated = _migrate_from_legacy(lab_meta_path, ip_allocation_path)
        save_inventory(migrated, inventory_path)
        return migrated

    return {"defaults": dict(DEFAULT_PROJECT_DEFAULTS), "devices": []}


def save_inventory(inventory, inventory_path):
    """디스크에는 비밀값을 봉인해서 쓴다 — 메모리의 inventory 는 평문 그대로 둔다(장비 id 는 여기서 정해진다)."""
    os.makedirs(os.path.dirname(inventory_path), exist_ok=True)
    sealed = dict(inventory)
    sealed["defaults"] = _sealed_record(inventory.get("defaults") or {}, DEFAULTS_SECRET_FIELDS)
    sealed["devices"] = [_sealed_record(d, DEVICE_SECRET_FIELDS)
                         for d in ensure_device_ids(inventory.get("devices") or [])]
    dump_yaml_atomic(sealed, inventory_path)


# ---------- 비밀값: 봉인·해제와 화면용 보기 ----------
def _reveal_or_empty(value):
    try:
        return reveal(value)
    except VaultError:
        return ""


def _reveal_record(record, fields):
    """record 의 비밀 칸을 제자리에서 푼다. 이 PC 에서 풀 수 없는 칸은 빈 값으로 두고 원래 봉인 문자열을
    UNREADABLE_KEY 에 기억한다 — 접속하는 쪽은 '비밀번호 없음'으로, 화면은 '다시 입력'으로 안내한다."""
    unreadable = {}
    for field in fields:
        value = record.get(field)
        try:
            record[field] = reveal(value) if value is not None else ""
        except VaultError:
            record[field] = ""
            unreadable[field] = value
    if unreadable:
        record[UNREADABLE_KEY] = unreadable
    return record


def _sealed_record(record, fields):
    """디스크에 쓸 사본 — 비밀 칸을 봉인한다. 풀지 못했던 칸이 그대로 비어 있으면 원래 봉인 문자열을 되돌려 쓴다
    (다른 PC 에서 옮겨 온 파일을 열었다고 그 값을 지우지 않는다)."""
    out = {k: v for k, v in record.items() if k != UNREADABLE_KEY}
    unreadable = record.get(UNREADABLE_KEY) or {}
    for field in fields:
        value = out.get(field)
        if not value and field in unreadable:
            out[field] = unreadable[field]
        elif value:
            out[field] = seal(value)
    return out


def _derived_id(name):
    return "n" + hashlib.sha1((name or "").encode("utf-8")).hexdigest()[:16]


def ensure_device_ids(devices):
    """장비마다 고정 id 를 둔다(제자리). 없으면 이름에서 만든다 — 같은 파일을 몇 번 읽어도 같은 id 라서, 저장하기 전에
    화면에 준 id 가 다음 읽기와 맞는다. 겹치면 순번을 붙인다. 저장할 때(save_inventory) 파일에 남는다."""
    seen = set()
    for device in devices:
        device_id = str(device.get("id") or "")
        if not device_id or device_id in seen:
            base = _derived_id(device.get("name"))
            device_id, number = base, 2
            while device_id in seen:
                device_id, number = f"{base}-{number}", number + 1
        device["id"] = device_id
        seen.add(device_id)
    return devices


def _public(record, fields):
    out = {k: v for k, v in record.items() if k not in fields and k != UNREADABLE_KEY}
    for field in fields:
        out[f"has_{field}"] = bool(record.get(field))
    out[UNREADABLE_KEY] = sorted(record.get(UNREADABLE_KEY) or {})
    return out


def public_device(device):
    """화면(JS)에 줄 장비 — 비밀값 대신 저장 여부(has_password·has_key_content·has_key_passphrase·has_enable_secret)와,
    이 PC 에서 풀지 못한 칸 이름(unreadable_secrets)만. 예전엔 비밀번호·개인키 평문이 브리지와 DOM 으로 나갔다.
    platform 은 vendor 칸을 읽은 결과(core/vendors.platform_of — 모르는 벤더면 None) — 저장할 때는 버려진다."""
    return dict(_public(device, DEVICE_SECRET_FIELDS), platform=vendors.platform_of(device.get("vendor")))


def public_defaults(defaults):
    """public_device 와 같은 규칙의 프로젝트 기본값(has_default_password·has_default_enable_secret)."""
    return _public(defaults, DEFAULTS_SECRET_FIELDS)


def apply_secret_changes(incoming, previous, fields):
    """화면이 보낸 레코드(비밀값 없음)에 기존 비밀값을 이은 사본. 화면 규칙: 칸에 새 값을 넣으면 그 값,
    clear_secrets 에 칸 이름을 넣으면 지움, 둘 다 아니면 기존 값 그대로(이 PC 에서 풀지 못한 값도 그대로)."""
    out = {k: v for k, v in incoming.items() if k not in ("clear_secrets", UNREADABLE_KEY)}
    for field in fields:
        if f"has_{field}" in out:
            del out[f"has_{field}"]
    clear = set(incoming.get("clear_secrets") or ())
    previous = previous or {}
    kept_unreadable = {}
    for field in fields:
        value = incoming.get(field)
        if isinstance(value, str) and value != "":
            out[field] = value
        elif field in clear:
            out[field] = ""
        else:
            out[field] = previous.get(field, "")
            if field in (previous.get(UNREADABLE_KEY) or {}):
                kept_unreadable[field] = previous[UNREADABLE_KEY][field]
    if kept_unreadable:
        out[UNREADABLE_KEY] = kept_unreadable
    return out


def with_stored_secrets(device, stored_devices):
    """화면에서 편집 중인 장비 하나(연결 확인용)에 같은 id 의 저장된 비밀값을 잇는다."""
    previous = next((d for d in stored_devices if device.get("id") and d.get("id") == device.get("id")), None)
    merged = apply_secret_changes(device, previous, DEVICE_SECRET_FIELDS)
    unreadable = merged.pop(UNREADABLE_KEY, None)
    out = _normalize_device(merged)
    if unreadable:
        out[UNREADABLE_KEY] = unreadable
    return out


def merge_screen_devices(incoming_devices, stored_devices):
    """화면이 보낸 장비 목록(이름 없는 행은 버림)을 저장할 목록으로 — 같은 id 의 기존 비밀값을 잇는다.
    id 가 겹치면(복사한 행) 뒤의 것은 새 장비로 본다. id 가 없는 행(예전 화면·다른 호출부)은 이름에서 만든 id 로
    한 번 더 찾는다 — 화면은 새 행에 처음부터 id 를 붙이지만, 그러지 않은 호출부도 비밀값을 잃지 않게."""
    stored = {d.get("id"): d for d in stored_devices if d.get("id")}
    merged, used = [], set()
    for raw in incoming_devices:
        if not raw.get("name"):
            continue
        device_id = str(raw.get("id") or "")
        if not device_id and _derived_id(raw.get("name")) in stored:
            device_id = _derived_id(raw.get("name"))
        if device_id in used:
            device_id = ""
        previous = stored.get(device_id) if device_id else None
        device = with_stored_secrets(dict(raw, id=device_id), [previous] if previous else [])
        merged.append(device)
        if device_id:
            used.add(device_id)
    return ensure_device_ids(merged)


def resolve_credentials(device, defaults):
    """장비별 값이 비어있으면 프로젝트 기본값을 적용. 둘 다 비어 있으면 빈 문자열 그대로 —
    'admin' 으로 채우지 않는다. 접속하는 쪽은 credential_problem() 으로 확인하고 접속하지 않는다."""
    ip = device.get("management_ip") or ""
    port = device.get("ssh_port") or defaults.get("default_ssh_port", 22)
    username = device.get("username") or defaults.get("default_username") or ""
    password = device.get("password") or defaults.get("default_password") or ""
    return ip, port, username, password


def resolve_enable_secret(device, defaults):
    """enable 비밀번호 — 장비별 값, 없으면 프로젝트 기본값, 둘 다 없으면 빈 문자열. 로그인 비밀번호로 대신하지 않는다
    (틀린 값으로 enable 을 시도하면 장비에 인증 실패가 남는다)."""
    return device.get("enable_secret") or (defaults or {}).get("default_enable_secret") or ""


def uses_key_auth(device):
    """개인키로 접속하는 장비인지 — auth_method 가 public_key 이고 키 파일이나 붙여넣은 키가 있을 때."""
    return device.get("auth_method") == "public_key" and bool(device.get("key_path") or device.get("key_content"))


def credential_problem(username, password, uses_key=False):
    """접속 전에 계정이 갖춰졌는지 본다 — 빠졌으면 화면에 보일 이유 문장, 갖춰졌으면 None.

    예전엔 비어 있으면 admin/admin 으로 접속했다. 수집·연결 확인·터미널 접속이 모두 이 함수로
    먼저 걸러서, 계정이 없는 장비에는 로그인 시도 자체를 하지 않는다."""
    if not username:
        return "사용자명이 비어 있어 접속하지 않았습니다 — 장비 목록에서 사용자명(또는 기본 사용자명)을 입력하세요"
    if not password and not uses_key:
        return "비밀번호가 비어 있어 접속하지 않았습니다 — 장비 목록에서 비밀번호(또는 기본 비밀번호·개인키)를 입력하세요"
    return None


def get_enabled_devices(inventory):
    """Collection이 사용할 접속 대상 — enabled=True인 것만."""
    return [d for d in inventory["devices"] if d.get("enabled", True)]


def add_device(inventory, device_dict):
    inventory["devices"].append(_normalize_device(device_dict))


def remove_device(inventory, name):
    inventory["devices"] = [d for d in inventory["devices"] if d["name"] != name]


def update_device(inventory, name, fields):
    for d in inventory["devices"]:
        if d["name"] == name:
            d.update({k: v for k, v in fields.items() if k in DEVICE_FIELDS})
            return True
    return False


# 장비명으로 받는 호스트명 — 장비명은 화면·점검 로그 파일명에 그대로 쓰인다. 장비가 돌려준 값(engine/device_probe)과
# 터미널 프롬프트의 호스트명(api/terminal_session_api)을 장비명으로 쓰기 전에 이 규칙으로 거른다.
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
# IP 자동 생성(auto_allocate_ips)이 붙이는 자리표시 이름 — 'AUTO-31', 겹치면 'AUTO-31_', IPv6 면 'AUTO-fe80::1'.
_PLACEHOLDER_NAME_RE = re.compile(r"^AUTO-[\w:]+$", re.IGNORECASE)


def is_placeholder_name(name):
    return bool(_PLACEHOLDER_NAME_RE.match(str(name or "").strip()))


def rename_device(inventory, old_name, new_name):
    """장비 이름을 장비가 알려 준 호스트명으로 바꾼다(제자리). 바꿨으면 None, 바꾸지 않았으면 그 이유.

    옛 이름은 별칭으로 남긴다 — 옛 이름으로 저장된 점검 로그·SecureCRT 로그도 계속 이 장비로 묶인다(core/device_names).
    IP 자동 생성의 자리표시 이름(AUTO-n)은 남기지 않는다. 다른 장비가 대소문자만 다른 같은 이름을 쓰고 있으면 바꾸지
    않는다 — 장비명이 점검 로그 파일명이 되는데, Windows 는 대소문자를 가리지 않아 두 장비의 로그가 한 파일에 덮어쓰인다."""
    new_name = str(new_name or "").strip()
    if not HOSTNAME_RE.match(new_name):
        return f"장비명으로 쓸 수 없는 호스트명입니다('{new_name[:40]}')"
    wanted = new_name.lower()
    device = next((d for d in inventory["devices"] if d.get("name") == old_name), None)
    if device is None:
        if any(str(d.get("name") or "").lower() == wanted for d in inventory["devices"]):
            return None     # 이미 그 이름이다 — 같은 장비에 연 다른 세션이 먼저 바꿨다
        return f"장비 목록에 '{old_name}' 장비가 없습니다"
    if any(d is not device and str(d.get("name") or "").lower() == wanted for d in inventory["devices"]):
        return f"이미 '{new_name}' 이름을 쓰는 장비가 있습니다"
    aliases = [a for a in aliases_of(device) if a.lower() != wanted]
    if old_name and not is_placeholder_name(old_name) and old_name.lower() != wanted:
        aliases.append(old_name)
    device["name"] = new_name
    device["aliases"] = list(dict.fromkeys(aliases))
    return None


# ---------- IP Pool 자동 할당 ----------
def generate_ip_pool(prefix, start, end):
    return [f"{prefix}{i}" for i in range(start, end + 1)]


def auto_allocate_ips(inventory, prefix=None, start=None, end=None):
    """
    management_ip가 비어있는 장비들에게 순서대로 IP Pool을 할당.
    인자를 안 주면 inventory["defaults"]["ip_pool"] 사용.
    반환: 실제로 할당된 개수.
    """
    pool_cfg = inventory.get("defaults", {}).get("ip_pool", {})
    prefix = prefix or pool_cfg.get("prefix", "")
    start = start if start is not None else pool_cfg.get("start", 0)
    end = end if end is not None else pool_cfg.get("end", 0)

    pool = generate_ip_pool(prefix, start, end)
    used_ips = {d["management_ip"] for d in inventory["devices"] if d.get("management_ip")}
    available = [ip for ip in pool if ip not in used_ips]

    allocated = 0
    for d in inventory["devices"]:
        if not d.get("management_ip") and available:
            d["management_ip"] = available.pop(0)
            allocated += 1

    # 빈 IP 행을 다 채우고도 남은 IP는 새 장비 행으로 생성한다.
    # 기존에는 여기서 남은 IP를 버렸기 때문에, 장비가 없거나 부족한 상태에서
    # 자동 생성을 누르면 테이블에 아무것도 채워지지 않는 것처럼 보였다.
    existing_names = {d["name"] for d in inventory["devices"]}
    for ip in available:
        suffix = ip.rsplit(".", 1)[-1] if "." in ip else ip
        name = f"AUTO-{suffix}"
        while name in existing_names:
            suffix += "_"
            name = f"AUTO-{suffix}"
        existing_names.add(name)
        add_device(inventory, {"name": name, "management_ip": ip, "enabled": True})
        allocated += 1

    return allocated
