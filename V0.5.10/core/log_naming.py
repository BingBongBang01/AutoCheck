"""점검 로그 파일명 규칙의 단일 출처 — 만드는 쪽과 읽는 쪽이 같은 규칙을 쓰게 한다.

사용자 정의 템플릿(동적 네이밍) 및 표준/레거시 규칙을 모두 지원:
    기본 템플릿: {timestamp}_{kind}_{device}.txt      (예: 20260818_140000_raw_Core1.txt)
    레거시 규칙: AutoCheck_{장비}_{YYYYMMDD}_{HHMMSS}.txt (예: AutoCheck_Core1_20260804_105238.txt)

지원 치환 변수:
    {device} / {hostname} : 장비 식별자 (필수)
    {ip}                  : 장비 IP 주소
    {timestamp}           : 고정폭 일시 (%Y%m%d_%H%M%S)
    {date}                : 날짜 (%Y%m%d)
    {time}                : 시간 (%H%M%S)
    {kind}                : 로그 종류 (raw, problem, masked 등)
    {customer}            : 고객사명
    {profile}             : 프로파일명
    {ext}                 : 확장자 (txt, log 등)
"""
import datetime
import os
import re
from pathlib import Path
from typing import Dict, Optional, Tuple, Any

import yaml

from core.paths import AppPaths, sanitize_component
from core.atomic_io import dump_yaml_atomic

__all__ = [
    "LEGACY_PREFIX", "STAMP_FORMAT", "DEFAULT_KIND", "DEFAULT_TEMPLATE",
    "build_inspection_log_name", "parse_inspection_log_name", "device_from_log_name",
    "stamp_or_unknown", "render_log_name", "validate_template", "compile_template_regex",
    "get_log_naming_config", "save_log_naming_config", "preview_log_naming",
]

LEGACY_PREFIX = "AutoCheck_"
STAMP_FORMAT = "%Y%m%d_%H%M%S"
DATE_FORMAT = "%Y%m%d"
TIME_FORMAT = "%H%M%S"
DEFAULT_KIND = "raw"
DEFAULT_TEMPLATE = "{timestamp}_{kind}_{device}.txt"
CONFIG_FILENAME = "log_naming.yaml"

_UNKNOWN_STAMP = "unknown_time"
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')

# 템플릿 정규식 캐시
_REGEX_CACHE: Dict[str, re.Pattern] = {}
_CONFIG_CACHE: Optional[Dict[str, Any]] = None


def _get_config_path() -> Path:
    """설정 파일 경로 (사용자 데이터 config 우선, 번들 config 폴백)."""
    user_cfg = AppPaths.config_root() / CONFIG_FILENAME
    if user_cfg.exists():
        return user_cfg
    bundled = AppPaths.app_root() / "config" / CONFIG_FILENAME
    if bundled.exists():
        return bundled
    return user_cfg


def get_log_naming_config() -> Dict[str, Any]:
    """log_naming.yaml 설정을 읽어온다."""
    global _CONFIG_CACHE
    path = _get_config_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                if isinstance(data, dict):
                    _CONFIG_CACHE = data
                    return data
        except Exception:
            pass

    default_data = {
        "template": DEFAULT_TEMPLATE,
        "presets": [
            {
                "id": "standard",
                "name": "표준 (기본값)",
                "pattern": "{timestamp}_{kind}_{device}.txt",
                "description": "시간순 정렬에 최적화된 기본 규격 (예: 20260818_140000_raw_Core1.txt)",
            },
            {
                "id": "device_first",
                "name": "장비명 우선형",
                "pattern": "{device}_{timestamp}_{kind}.txt",
                "description": "장비명으로 정렬할 때 편리한 규격 (예: Core1_20260818_140000_raw.txt)",
            },
            {
                "id": "date_time_split",
                "name": "날짜·시간 분리형",
                "pattern": "{date}_{time}_{device}_{kind}.log",
                "description": "날짜와 시간을 분리하고 .log 확장자 사용 (예: 20260818_140000_Core1_raw.log)",
            },
            {
                "id": "customer_profile",
                "name": "고객사·프로파일 포함형",
                "pattern": "{customer}_{profile}_{device}_{timestamp}.txt",
                "description": "고객사 및 회차명이 포함된 상세 규격 (예: ACME_202608_Core1_20260818_140000.txt)",
            },
            {
                "id": "device_ip",
                "name": "장비명·IP 결합형",
                "pattern": "{device}_{ip}_{timestamp}.txt",
                "description": "장비명과 IP를 함께 기록하는 규격 (예: Core1_192.168.205.101_20260818_140000.txt)",
            },
        ],
    }
    _CONFIG_CACHE = default_data
    return default_data


def save_log_naming_config(template: str) -> Dict[str, Any]:
    """사용자가 지정한 템플릿을 log_naming.yaml에 원자적으로 저장한다."""
    global _CONFIG_CACHE, _REGEX_CACHE
    valid, err_msg = validate_template(template)
    if not valid:
        return {"error": err_msg}

    cfg = get_log_naming_config()
    cfg["template"] = template.strip()

    target_path = AppPaths.config_root() / CONFIG_FILENAME
    try:
        dump_yaml_atomic(cfg, target_path)
        _CONFIG_CACHE = cfg
        _REGEX_CACHE.clear()
        return {"ok": True, "template": template.strip()}
    except Exception as e:
        return {"error": f"설정 저장 실패: {e}"}


def validate_template(template: Optional[str]) -> Tuple[bool, Optional[str]]:
    """템플릿 유효성 검사 — {device} 또는 {hostname}이 필수이며 위험 문자가 없어야 함."""
    if not template or not isinstance(template, str) or not template.strip():
        return False, "템플릿이 비어 있습니다."

    tpl = template.strip()
    if "{device}" not in tpl and "{hostname}" not in tpl:
        return False, "파일명 템플릿에 장비 식별자 {device} (또는 {hostname}) 변수가 포함되어야 합니다."

    literal_part = re.sub(r'\{[a-zA-Z0-9_]+\}', '', tpl)
    if _INVALID_FILENAME_CHARS.search(literal_part):
        return False, '파일명에 사용할 수 없는 특수문자(\\ / : * ? " < > |)가 포함되어 있습니다.'

    return True, None


def sanitize_filename_part(val: Any, allow_dots: bool = False) -> str:
    """파일명 구성 요소 내의 경로 구분자 및 금지 문자를 밑줄(_)로 치환."""
    s = str(val if val is not None else "").strip()
    if not allow_dots:
        s = _INVALID_FILENAME_CHARS.sub("_", s)
    else:
        s = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', s)
    return s.strip(" .") or "unknown"


def render_log_name(context: Dict[str, Any], template: Optional[str] = None) -> str:
    """컨텍스트 맵을 받아 템플릿에 따라 파일명을 생성한다."""
    if not template:
        template = get_log_naming_config().get("template") or DEFAULT_TEMPLATE

    when = context.get("when")
    if not isinstance(when, datetime.datetime):
        when = datetime.datetime.now()

    device = sanitize_component(context.get("device") or context.get("hostname") or "unknown_device")
    ip = sanitize_filename_part(context.get("ip") or "no_ip", allow_dots=True)
    kind = sanitize_component(context.get("kind") or DEFAULT_KIND)
    customer = sanitize_component(context.get("customer") or "customer")
    profile = sanitize_component(context.get("profile") or "profile")

    stamp = when.strftime(STAMP_FORMAT)
    date_str = when.strftime(DATE_FORMAT)
    time_str = when.strftime(TIME_FORMAT)

    replacements = {
        "{device}": device,
        "{hostname}": device,
        "{ip}": ip,
        "{timestamp}": stamp,
        "{date}": date_str,
        "{time}": time_str,
        "{kind}": kind,
        "{customer}": customer,
        "{profile}": profile,
        "{ext}": "txt",
    }

    result = template
    for token, val in replacements.items():
        result = result.replace(token, val)

    # 남은 알 수 없는 {token} 제거
    result = re.sub(r'\{[a-zA-Z0-9_]+\}', '', result)

    name_clean = _INVALID_FILENAME_CHARS.sub("_", result).strip()
    if "." not in name_clean:
        name_clean += ".txt"

    return name_clean


def build_inspection_log_name(
    device: str,
    when: Optional[datetime.datetime] = None,
    kind: str = DEFAULT_KIND,
    context: Optional[Dict[str, Any]] = None,
    template: Optional[str] = None,
) -> str:
    """점검 로그 파일명을 만든다. 기존 시그니처 100% 하위 호환."""
    ctx = dict(context or {})
    ctx.setdefault("device", device)
    ctx.setdefault("when", when or datetime.datetime.now())
    ctx.setdefault("kind", kind)
    return render_log_name(ctx, template=template)


def compile_template_regex(template: str) -> re.Pattern:
    """템플릿 패턴을 역파싱용 정규식으로 변환하고 캐시한다."""
    cached = _REGEX_CACHE.get(template)
    if cached is not None:
        return cached

    token_patterns = {
        "{timestamp}": r"(?P<timestamp>\d{8}_\d{6})",
        "{date}": r"(?P<date>\d{8}|\d{4}-\d{2}-\d{2})",
        "{time}": r"(?P<time>\d{6}|\d{2}-\d{2}-\d{2})",
        "{kind}": r"(?P<kind>[a-zA-Z0-9]+)",
        "{device}": r"(?P<device>[^\s\\/]+)",
        "{hostname}": r"(?P<device>[^\s\\/]+)",
        "{ip}": r"(?P<ip>\d{1,3}(?:\.\d{1,3}){3}|[a-zA-Z0-9_.-]+)",
        "{customer}": r"(?P<customer>[a-zA-Z0-9_-]+)",
        "{profile}": r"(?P<profile>[a-zA-Z0-9_-]+)",
        "{ext}": r"(?P<ext>txt|log)",
    }

    pattern = template.strip()
    for idx, (token, group_re) in enumerate(token_patterns.items()):
        marker = f"__TOKEN_{idx}__"
        pattern = pattern.replace(token, marker)

    pattern = re.escape(pattern)

    for idx, (token, group_re) in enumerate(token_patterns.items()):
        marker = re.escape(f"__TOKEN_{idx}__")
        pattern = pattern.replace(marker, group_re)

    regex_str = f"^{pattern}$"
    compiled = re.compile(regex_str, re.IGNORECASE)
    _REGEX_CACHE[template] = compiled
    return compiled


def parse_inspection_log_name(
    filename: str,
    manifest: Optional[Dict[str, Any]] = None,
    template: Optional[str] = None,
) -> Tuple[str, Optional[str]]:
    """파일명에서 (장비명, 타임스탬프 문자열)을 뽑는다.
    
    파싱 전략:
      1. 회차 manifest 매핑 우선 조회
      2. 사용자 정의 템플릿 정규식 매칭
      3. 레거시(AutoCheck_<device>_<stamp>.txt) 매칭
      4. 표준({stamp}_{kind}_{device}.txt) 4토큰 매칭
      5. 안전한 폴백: (확장자 뗀 이름 전체, None)
    """
    body = os.path.basename(str(filename))
    
    # 1. 매니페스트 메타데이터 확인
    if manifest and isinstance(manifest, dict):
        file_info = manifest.get("files", {}).get(body) or manifest.get(body)
        if isinstance(file_info, dict) and "device" in file_info:
            return file_info["device"], file_info.get("timestamp")

    # 2. 사용자 정의 템플릿 정규식 매칭
    active_tpl = template or get_log_naming_config().get("template") or DEFAULT_TEMPLATE
    if active_tpl != DEFAULT_TEMPLATE:
        try:
            pattern = compile_template_regex(active_tpl)
            m = pattern.match(body)
            if m:
                groups = m.groupdict()
                device = groups.get("device")
                stamp = groups.get("timestamp")
                if not stamp and groups.get("date") and groups.get("time"):
                    stamp = f"{groups['date']}_{groups['time']}"
                if device:
                    return device, stamp
        except Exception:
            pass

    # 확장자 분리
    name_no_ext = body
    if body.lower().endswith(".txt"):
        name_no_ext = body[:-len(".txt")]
    elif body.lower().endswith(".log"):
        name_no_ext = body[:-len(".log")]

    # 3. 레거시 포맷: AutoCheck_{device}_{stamp}
    if name_no_ext.startswith(LEGACY_PREFIX):
        rest = name_no_ext[len(LEGACY_PREFIX):]
        parts = rest.rsplit("_", 2)
        if len(parts) == 3:
            return parts[0], f"{parts[1]}_{parts[2]}"
        return rest, None

    # 4. 표준 포맷: {stamp_date}_{stamp_time}_{kind}_{device} (단순 4토큰 분할)
    parts = name_no_ext.split("_", 3)
    if len(parts) == 4:
        return parts[3], f"{parts[0]}_{parts[1]}"

    return name_no_ext, None


def device_from_log_name(
    filename: str,
    manifest: Optional[Dict[str, Any]] = None,
    template: Optional[str] = None,
) -> str:
    """장비명만 필요할 때. parse_inspection_log_name()의 앞쪽 값 반환."""
    return parse_inspection_log_name(filename, manifest=manifest, template=template)[0]


def stamp_or_unknown(
    filename: str,
    manifest: Optional[Dict[str, Any]] = None,
    template: Optional[str] = None,
) -> str:
    """분석 결과 파일명을 조립할 때 쓰는 타임스탬프 — 없으면 'unknown_time'."""
    return parse_inspection_log_name(filename, manifest=manifest, template=template)[1] or _UNKNOWN_STAMP


def preview_log_naming(template: Optional[str] = None) -> Dict[str, Any]:
    """설정 UI에서 사용자가 입력한 템플릿의 실시간 렌더링 예시를 반환한다."""
    tpl = template or get_log_naming_config().get("template") or DEFAULT_TEMPLATE
    valid, err = validate_template(tpl)
    
    sample_context = {
        "device": "Core1",
        "hostname": "Core1",
        "ip": "192.168.205.101",
        "kind": "raw",
        "customer": "ACME_Corp",
        "profile": "2026_08_점검",
        "when": datetime.datetime.now(),
    }
    
    preview_name = render_log_name(sample_context, template=tpl)
    parsed_device, parsed_stamp = parse_inspection_log_name(preview_name, template=tpl)
    
    return {
        "valid": valid,
        "error": err,
        "template": tpl,
        "preview": preview_name,
        "parsed_device": parsed_device,
        "parsed_stamp": parsed_stamp,
    }
