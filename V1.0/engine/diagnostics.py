"""진단 번들 — 문제를 알릴 때 붙일 zip 하나(IMPROVEMENT_PLAN 4-10).

담는 것:
  about.json      앱 버전·실행 형태(EXE/소스)·Python·OS·주요 패키지 버전·폴더 위치(사용자 이름은 가린다)
  workspace.json  고객사·프로파일·Run·장비·보관본·일정 수(이름은 담지 않는다)
  checks.json     지금 점검: 규칙 파일 린트(4-8c), 필요 패키지, 데이터 폴더 쓰기
  logs/           최근 앱 로그(최대 3개, 파일마다 끝 2MB)
  config/         설정 파일(사용자 사본) — 비밀 필드는 비운다
  manifest.txt    무엇을 담았고 무엇을 가렸는지
담지 않는 것: 점검 로그·보고서·장비 목록(주소·계정)·고객사 목록·외부 AI 전송 이력·금고 값.

가림은 두 겹이다. 비밀(비밀번호·API 키·토큰·웹훅·봉인 값·설정 줄의 비밀번호 해시·SNMP 커뮤니티)은 언제나 지운다.
mask_identifiers=True(기본)이면 장비·고객사·프로파일 이름과 IP·MAC·계정·도메인도 같은 글자 수의 '*'로 가린다 —
번들은 이 PC 밖(개발자·지원 창구)으로 나가기 때문이다. 로그 모양은 남으므로 무슨 일이 있었는지는 읽힌다.
"""
import datetime
import getpass
import json
import os
import platform
import re
import sys
import zipfile
from pathlib import Path

from core.paths import AppPaths
from core.secret_redaction import redact_secrets
from core.utils import format_iso

LOG_FILES = 3                    # 최근 앱 로그 파일 수
LOG_TAIL_BYTES = 2 * 1024 * 1024  # 파일마다 끝에서 이만큼
_PACKAGES = ("paramiko", "netmiko", "openpyxl", "reportlab", "pywebview", "PyYAML", "cryptography")
# 사용자 데이터 폴더 바로 아래의 설정(core/app_settings) — 같은 폴더의 이력(*.jsonl)·호스트키는 담지 않는다.
_ROOT_SETTINGS = ("ai_config.yaml", "ai_settings.yaml", "connection.yaml", "schedules.yaml")
# config/ 에서 빼는 것 — 고객사 목록·활성 프로파일은 이름 목록일 뿐 진단에 쓸모가 없다.
_CONFIG_SKIP = {"customers.yaml", "active_project.yaml"}
_SECRET_KEY_RE = re.compile(
    r"pass(word|phrase)?|secret|api[_-]?key|token|webhook|private[_-]?key|key[_-]?content|community|credential"
    r"|^auth|authorization|cookie", re.IGNORECASE)
_REDACTED = "[가림]"
_SEALED = "[봉인된 값]"
_DATE_LIKE = re.compile(r"^[\d\s.\-/_:]+$")


def build_bundle(dest_path, mask_identifiers=True):
    """dest_path 에 zip 을 만든다. 반환: {path, files:[이름…], masked_identifiers, names_masked}."""
    names = _known_names() if mask_identifiers else ()
    clean, mask_value = _cleaner(mask_identifiers, names)
    entries = []                                        # [(zip 안 이름, 바이트)]
    entries.append(("about.json", _json(_about())))
    entries.append(("workspace.json", _json(_workspace())))
    entries.append(("checks.json", _json(_checks())))
    for path in _recent_logs():
        entries.append((f"logs/{path.name}", clean(_tail(path)).encode("utf-8")))
    for label, path in _settings_files():
        text = _scrub_settings(path, mask_value)
        entries.append((f"config/{label}", (text if text is not None else clean(path_text(path))).encode("utf-8")))
    manifest = _manifest([name for name, _ in entries], mask_identifiers, len(names))
    entries.append(("manifest.txt", manifest.encode("utf-8")))
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    os.replace(tmp, dest)
    return {"path": str(dest), "files": [name for name, _ in entries],
            "masked_identifiers": bool(mask_identifiers), "names_masked": len(names)}


def default_name():
    return f"autocheck_diagnostics_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"


# --------------------------------------------------------------------------- 내용

def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def _hide_user(text):
    """경로에 든 Windows 사용자 이름을 가린다(about.json 의 폴더 위치)."""
    user = _user_name()
    return text.replace(user, "<user>") if user and len(user) > 1 else text


def _user_name():
    try:
        return getpass.getuser()
    except Exception:
        return os.path.basename(os.path.expanduser("~"))


def _about():
    from core.version import app_version
    from importlib import metadata
    packages = {}
    for name in _PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "app_version": app_version(),
        "frozen_exe": bool(getattr(sys, "frozen", False)),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "created_at": format_iso(),
        "timezone": list(getattr(__import__("time"), "tzname", ())),
        "paths": {"user_data_root": _hide_user(str(AppPaths.user_data_root())),
                  "app_root": _hide_user(str(AppPaths.app_root()))},
        "packages": packages,
    }


def _workspace():
    """수만 센다 — 이름은 담지 않는다."""
    from engine import customer_manager as cm
    from engine import project_manager as pm
    counts = {"customers": 0, "profiles": 0, "runs": 0, "devices": 0, "archives": 0, "schedules": 0,
              "realtime_states": 0}
    try:
        counts["customers"] = len(cm.load())
    except Exception:
        pass
    try:
        projects = pm.list_projects()
        counts["profiles"] = len(projects)
    except Exception:
        projects = []
    for project in projects:
        counts["devices"] += len(_inventory_names(project))
    data_root = AppPaths.data_root()
    counts["runs"] = sum(1 for _ in data_root.glob("*/*/runs/*") if _.is_dir())
    counts["archives"] = sum(1 for _ in data_root.glob("*/_archived_profiles/*") if _.is_dir())
    counts["realtime_states"] = sum(1 for _ in data_root.glob("*/*/realtime_state.json"))
    try:
        from engine import inspection_schedule
        counts["schedules"] = len(inspection_schedule.load()["schedules"])
    except Exception:
        pass
    return counts


def _checks():
    out = {}
    try:
        from engine.rule_lab import lint_rules
        lint = lint_rules()
        out["rules"] = {"clean": lint["clean"], "readable": lint["readable"], "counts": lint["counts"],
                        "findings": [f"[{f['level']}] {f['where']}: {f['message']}" for f in lint["findings"]]}
    except Exception as exc:
        out["rules"] = {"error": f"{type(exc).__name__}: {exc}"}
    try:
        from core.requirements_check import find_problems, parse_requirements
        req = AppPaths.app_root() / "requirements.txt"
        if req.is_file():
            missing, outdated = find_problems(parse_requirements(req.read_text(encoding="utf-8")))
            out["requirements"] = {"missing": missing, "outdated": [list(o) for o in outdated]}
    except Exception as exc:
        out["requirements"] = {"error": f"{type(exc).__name__}: {exc}"}
    probe = AppPaths.user_data_root() / ".diagnostics_write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        out["data_folder_writable"] = True
    except OSError as exc:
        out["data_folder_writable"] = f"{type(exc).__name__}: {exc}"
    return out


def _recent_logs():
    logs = sorted(AppPaths.logs_root().glob("app_full_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[:LOG_FILES]


def _tail(path):
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > LOG_TAIL_BYTES:
            f.seek(size - LOG_TAIL_BYTES)
            f.readline()                                   # 잘린 첫 줄은 버린다
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    if size > LOG_TAIL_BYTES:
        text = f"[앞 {size - LOG_TAIL_BYTES:,}바이트는 뺐다 — 끝 {LOG_TAIL_BYTES // (1024 * 1024)}MB 만]\n" + text
    return text


def _settings_files():
    """(zip 안 이름, 경로) — 사용자 사본만(번들 기본값은 앱에 있다)."""
    root = AppPaths.user_data_root()
    files = [(name, root / name) for name in _ROOT_SETTINGS if (root / name).is_file()]
    config = AppPaths.config_root()
    for path in sorted(config.iterdir()):
        if path.is_file() and path.suffix.lower() in (".yaml", ".yml", ".json") and path.name not in _CONFIG_SKIP:
            files.append((path.name, path))
    return files


def path_text(path):
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _scrub_settings(path, mask_value):
    """설정 파일을 구조로 읽어 비밀 필드를 비우고 문자열 값마다 이름·주소를 가린 글. 읽을 수 없으면 None(호출부가 원문을
    줄 단위로 가린다). 값마다 가린 뒤에 다시 쓰므로 '*'로 시작하는 값도 따옴표가 붙어 YAML 로 다시 읽힌다."""
    import yaml
    text = path_text(path)
    try:
        data = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError):
        return None
    scrubbed = _scrub(data, mask_value=mask_value)
    if path.suffix.lower() == ".json":
        return json.dumps(scrubbed, ensure_ascii=False, indent=2)
    return yaml.safe_dump(scrubbed, allow_unicode=True, sort_keys=False)


def _scrub(value, key="", mask_value=None):
    """비밀 필드(이름으로)·봉인 값을 비우고, 나머지 문자열은 토큰 모양을 지운 뒤 mask_value 로 이름·주소를 가린다."""
    if isinstance(value, dict):
        return {k: _scrub(v, str(k), mask_value) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, key, mask_value) for v in value]
    if isinstance(value, str):
        if value.startswith("vault:"):
            return _SEALED
        if value and _SECRET_KEY_RE.search(key):
            return _REDACTED
        value = redact_secrets(value)          # 이름이 평범한 필드에 든 토큰 모양(주소의 ?key= 등)
        return mask_value(value) if mask_value else value
    if value is not None and not isinstance(value, bool) and _SECRET_KEY_RE.search(key):
        return _REDACTED
    return value


# --------------------------------------------------------------------------- 가림

def _known_names():
    """가릴 이름 — 고객사·프로파일 이름(고객사 목록과 data 폴더 구조)과 모든 장비 목록의 장비명·호스트명.
    한 글자 이름은 낱말을 망가뜨리므로 뺀다."""
    from engine import customer_manager as cm
    from engine import project_manager as pm
    names = set()
    try:
        names |= {c.get("name") for c in cm.load()}
    except Exception:
        pass
    data_root = AppPaths.data_root()
    for customer in data_root.iterdir():
        if customer.is_dir() and not customer.name.startswith("_"):
            names.add(customer.name)
            names |= {p.name for p in customer.iterdir() if p.is_dir() and not p.name.startswith("_")}
    try:
        projects = pm.list_projects()
    except Exception:
        projects = []
    for project in projects:
        if project.get("display_name"):
            names.add(project["display_name"])
        names |= set(_inventory_names(project))
    # 숫자와 구분자뿐인 이름('2026-10')은 뺀다 — 누구인지 드러내지 않으면서 로그의 날짜('2026-10-28')를 망가뜨린다.
    return tuple(sorted({n for n in names if isinstance(n, str) and len(n.strip()) > 1 and not _DATE_LIKE.match(n)},
                        key=len, reverse=True))


def _inventory_names(project):
    from engine import device_inventory as di
    from engine import project_manager as pm
    project_id = project.get("id") if isinstance(project, dict) else project
    try:
        inventory = di.load_inventory(pm.project_paths(project_id)["device_inventory"])
    except Exception:
        return []
    out = []
    for device in inventory.get("devices") or []:
        for key in ("name", "hostname"):
            if device.get(key):
                out.append(str(device[key]))
    return out


def _cleaner(mask_identifiers, names):
    """(clean, mask_value). clean: 로그·읽지 못한 설정의 글 — 줄 단위로 비밀부터 지우고 이름·주소를 가린다.
    mask_value: 구조로 읽은 설정의 문자열 값 하나 — 비밀 필드는 이미 '[가림]'으로 비웠으므로 비밀 규칙을 다시 돌리지
    않는다(그 표시까지 '*'가 돼서 왜 비었는지 읽을 수 없다). 이름·주소만 가린다."""
    from engine.log_masking import EXTERNAL_AI_KEYS, SECRET_KEYS, apply_masking, mask_for_external_ai
    identifier_keys = set(EXTERNAL_AI_KEYS) - set(SECRET_KEYS)
    name_re = (re.compile(r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b", re.IGNORECASE)
               if names else None)

    def clean(text):
        text = apply_masking(redact_secrets(text), SECRET_KEYS)
        return mask_for_external_ai(text, names) if mask_identifiers else text

    def mask_value(value):
        value = apply_masking(value, identifier_keys)
        return name_re.sub(lambda m: "*" * len(m.group(0)), value) if name_re else value

    return clean, (mask_value if mask_identifiers else None)


def _manifest(files, mask_identifiers, name_count):
    lines = ["AutoCheck 진단 번들", f"만든 시각: {format_iso()}", "",
             "가린 것:",
             " - 비밀번호·API 키·토큰·웹훅 주소·봉인된 값(설정 파일의 비밀 필드는 비웠다)",
             " - 설정 줄의 비밀번호 해시·SNMP 커뮤니티·키"]
    if mask_identifiers:
        lines.append(f" - 장비·고객사·프로파일 이름 {name_count}개와 IP·MAC·계정·도메인(같은 글자 수의 '*')")
    else:
        lines.append(" - (이름·주소는 가리지 않았다 — 밖으로 보내기 전에 확인하세요)")
    lines += ["", "담지 않은 것: 점검 로그·보고서·장비 목록·고객사 목록·외부 AI 전송 이력·호스트키", "", "파일:"]
    lines += [f" - {name}" for name in files]
    return "\n".join(lines) + "\n"
