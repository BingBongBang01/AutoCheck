"""
정기점검 보고서 데이터 조립 + 파일 출력 — 앱의 보고서 경로는 이것 하나다(IMPROVEMENT_PLAN 3-3).

report/inspection_excel.py·inspection_pdf.py 가 "어떻게 그릴지", engine/inspection_evaluator.py 가
"항목을 어떻게 판정할지"를 담당하는 것과 짝을 이뤄, 이 모듈은
"무엇을 그릴지"(어느 로그를 읽고, 무엇과 조인하고, 어디에 어떤 이름으로 저장할지)를 담당한다.
데이터는 build_context() 한 벌, 포맷은 레지스트리(register_format — Excel·PDF)로 고른다. 보고서 탭과
워크스페이스 탭의 '리포트 생성'이 같은 export() 를 거쳐 같은 폴더(프로파일 reports/)에 저장한다.

데이터 흐름:
    data/<고객사>/<프로파일>/cache/original_log/AutoCheck_<장비>_<날짜>_<시각>.txt   (원본로그)
        -> engine.inspection_evaluator.split_transcript()   커맨드별 구간 분리
        -> engine.inspection_evaluator.evaluate_device()     항목별 판정(비즈니스 로직)
        + 장비목록(engine.device_inventory)                 모델/IP/S/N/용도 조인
        + 직전 회차 스냅샷(같은 고객사의 다른 프로파일)      '전월 점검값' 열
        -> report.inspection_excel.build_workbook()
        -> data/<고객사>/<프로파일>/reports/<파일명>.xlsx

파일명은 요구사항대로 고객사명과 프로파일(회차)명으로 조립한다:
    <고객사>_<프로파일>_정기점검보고서_<YYYYMMDD>.xlsx

보고서를 만들 때마다 reports/_snapshot.json에 항목별 당월 값을 남긴다 — 다음 회차
프로파일에서 이 파일을 찾아 '전월 점검값' 열을 자동으로 채운다.
"""
import datetime
import hashlib
import json
import os
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core import log_naming, vendors
from core.paths import safe_filename, sanitize_component
from core.atomic_io import write_text_atomic
from core.text_io import read_log_text
from engine import inspection_evaluator
from engine.profile_manager import profile_manager
from report.inspection_status import (
    STATUS_NA, STATUS_OK, STATUS_SKIP, STATUS_UNREACHABLE, STATUS_WARN,
)

REPORTS_SUBDIR = "reports"
SNAPSHOT_FILE = "_snapshot.json"
HISTORY_FILE = "_support_history.json"


class InspectionReportError(Exception):
    """보고서 생성에 필요한 전제(원본로그 등)가 없을 때."""


def _excel():
    """report.inspection_excel 을 필요할 때 가져온다.

    그 모듈은 최상단에서 openpyxl 을 import 한다(스타일 상수를 모듈 레벨에서 만들기 때문에
    미룰 수 없다). 여기서 모듈 레벨로 import 하면 api/inspection_report_api.py 를 거쳐
    앱 시작 경로가 openpyxl 전체를 끌어온다(이전 측정(Windows): 269 ms).
    보고서를 실제로 만들 때만 필요하므로 그때 가져온다.
    """
    from report import inspection_excel

    return inspection_excel


# --------------------------------------------------------------------------- 경로

def reports_dir(customer_name: str, profile_name: str) -> Path:
    """data/<고객사>/<프로파일>/reports/ — 없으면 만들어서 반환한다.
    프로파일 생성 시 ProfileManager.repair_profile()이 이미 만들어 두지만, 레거시 프로파일이나
    사용자가 폴더를 지운 경우에도 내보내기가 실패하지 않도록 여기서 한 번 더 보장한다."""
    path = profile_manager.repair_profile(customer_name, profile_name) / REPORTS_SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def original_log_dirs(customer_name: str, profile_name: str) -> list:
    """점검 원본 로그가 들어있는 실존 폴더들(최신 run 우선) — 없으면 빈 리스트."""
    from engine import log_storage
    return [Path(entry["path"])
            for entry in log_storage.iter_log_dirs(customer_name, profile_name, "original")]


def build_filename(customer_name: str, profile_name: str, *, date=None, suffix="정기점검보고서") -> str:
    """보고서 엑셀 파일명 — 고객사명 + 프로파일명 + 구분자 + 날짜."""
    stamp = (date or datetime.date.today().isoformat()).replace("-", "")
    parts = [sanitize_component(customer_name), sanitize_component(profile_name), suffix, stamp]
    return "_".join(p for p in parts if p) + ".xlsx"


# --------------------------------------------------------------------------- 원본로그 수집

def _read_text(path: Path) -> str:
    """레거시 로그가 cp949로 저장된 경우까지 감안해서 읽는다 — 규칙은 core/text_io.py 단일 출처."""
    return read_log_text(path)


def latest_logs_by_device(customer_name: str, profile_name: str, dirs=None) -> dict:
    """점검 회차 폴더(runs/<run_id>/raw)에서 장비별 최신 로그 1개씩 —
    {장비명: {"path", "collected_at", "text"}}.

    파일명 해석은 core/log_naming.py 단일 출처를 쓴다. 예전에는 이 함수가 레거시
    'AutoCheck_<장비>_<날짜>_<시각>.txt' 만 아는 자체 정규식을 갖고 있어서, 현재 규칙인
    '<날짜>_<시각>_raw_<장비>.txt' 가 하나도 매칭되지 않았다. 그러면 fallback 으로 파일명
    전체가 장비명이 되어 보고서 Hostname 에 '20260805_095518_raw_Agg1' 이 찍히고, 그 이름은
    장비목록의 'Agg1' 과 매칭되지 않으니 IP·모델·용도 열이 전부 빈칸으로 남았다.
    규칙에 맞지 않는 파일(사용자가 손으로 넣은 로그)은 여전히 확장자를 뗀 이름을 쓴다.
    dirs: 이 폴더들만 본다(무인 정기점검 — 이번 회차의 raw 만). None 이면 모든 회차와 예전 폴더."""
    latest = {}
    for directory in (original_log_dirs(customer_name, profile_name) if dirs is None else map(Path, dirs)):
        for path in sorted(directory.glob("*.txt")):
            device, stamp = log_naming.parse_inspection_log_name(path.name)
            try:
                collected = datetime.datetime.strptime(stamp, "%Y%m%d_%H%M%S") if stamp else None
            except ValueError:
                collected = None
            if collected is None:
                collected = datetime.datetime.fromtimestamp(path.stat().st_mtime)
            previous = latest.get(device)
            if previous is None or collected > previous["collected_at"]:
                latest[device] = {"path": path, "collected_at": collected}
    for info in latest.values():
        info["text"] = _read_text(info["path"])
    return latest


# --------------------------------------------------------------------------- 장비 판정 캐시
# 같은 로그·같은 양식·같은 규칙이면 판정은 같다. 보고서 탭을 열 때마다 장비 25대의 show logging 6만 줄을 다시 판정해
# 열 때마다 몇 초씩 기다렸다 — 판정 결과(items)만 기억해 둔다. 키는 로그 파일 신원(경로·수정 시각·크기 —
# engine/log_cache 와 같다)이라 점검이 로그를 새로 쓰면 저절로 다시 판정한다. 명령별 출력(sections)은 크고(PDF 가 쓴다)
# 다시 나누는 값이 싸서 기억하지 않는다.
_EVALUATION_CACHE_MAX = 512
_evaluation_cache = OrderedDict()
_evaluation_lock = threading.Lock()


def _template_key(template: dict) -> str:
    return hashlib.sha1(json.dumps(template.get("check_items"), sort_keys=True, ensure_ascii=False,
                                   default=str).encode("utf-8")).hexdigest()


def _evaluate_cached(path, sections, template, template_key, *, is_virtual, platform):
    """evaluate_device 와 같다 — 같은 로그 파일·양식·가상환경·플랫폼·규칙이면 기억해 둔 판정을 돌려준다(항목은 사본 —
    부르는 쪽이 '전월 점검값'을 덧붙인다)."""
    from engine.log_rule_engine import rules_version
    try:
        stat = os.stat(path)
    except OSError:
        stat = None
    key = None if stat is None else (os.path.normcase(os.path.abspath(path)), stat.st_mtime_ns, stat.st_size,
                                     template_key, bool(is_virtual), platform, rules_version())
    if key is not None:
        with _evaluation_lock:
            cached = _evaluation_cache.get(key)
            if cached is not None:
                _evaluation_cache.move_to_end(key)
                return [dict(item) for item in cached]
    items = inspection_evaluator.evaluate_device(sections, template, is_virtual=is_virtual, platform=platform)
    if key is not None:
        with _evaluation_lock:
            _evaluation_cache[key] = [dict(item) for item in items]
            while len(_evaluation_cache) > _EVALUATION_CACHE_MAX:
                _evaluation_cache.popitem(last=False)
    return items


# --------------------------------------------------------------------------- 전월 스냅샷

def _snapshot_path(customer_name: str, profile_name: str) -> Path:
    return reports_dir(customer_name, profile_name) / SNAPSHOT_FILE


def save_snapshot(customer_name: str, profile_name: str, devices: list, inspection_date: str = None) -> Path:
    """이번 회차의 항목별 값을 저장 — 다음 회차의 '전월 점검값'이 된다.
    점검일(inspection_date)을 함께 남긴다 — '전월'을 고를 때 저장 시각이 아니라 점검일을 본다."""
    payload = {
        "customer": customer_name, "profile": profile_name,
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "inspection_date": inspection_date,
        "devices": {
            device["name"]: {item["name"]: item.get("value") for item in device.get("items", [])}
            for device in devices
        },
    }
    path = _snapshot_path(customer_name, profile_name)
    write_text_atomic(str(path), json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def _snapshot_date(data: dict) -> str:
    """스냅샷의 기준 날짜 — 점검일, 없으면(예전 스냅샷) 저장 날짜."""
    return str(data.get("inspection_date") or (data.get("saved_at") or "")[:10])


def load_previous_snapshot(customer_name: str, profile_name: str, before: str = None) -> dict:
    """같은 고객사의 '이전 회차' 스냅샷 — 자기 자신을 제외하고 가장 최근인 것.
    회차 폴더명(예: '2026-07')이 시간순 정렬이 안 되는 경우도 있어 폴더명 대신 스냅샷 안의 날짜를 본다.

    before(이번 보고서의 점검일)를 주면 그보다 앞선 회차만 본다. 예전엔 저장 시각이 가장 최근인 것을
    골라서, 11월 보고서를 만든 뒤 10월 보고서를 다시 만들면 '전월' 칸에 11월 값이 들어갔다. 없으면 빈 dict."""
    best = None
    for profile in profile_manager.list_profiles(customer_name):
        name = profile.get("name")
        if not name or name == profile_name:
            continue
        path = profile_manager.profile_dir(customer_name, name) / REPORTS_SUBDIR / SNAPSHOT_FILE
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if before and not _snapshot_date(data) < str(before):
            continue
        if best is None or (_snapshot_date(data), data.get("saved_at") or "") > \
                (_snapshot_date(best), best.get("saved_at") or ""):
            best = data
    return best or {}


# --------------------------------------------------------------------------- 지원 이력 (PDF 표준 양식 05_지원이력)

def _history_path(customer_name: str, profile_name: str) -> Path:
    """지원이력은 프로파일(회차)이 아니라 고객사 단위로 누적한다 — 표준 양식의 '05_지원이력'은
    회차가 바뀌어도 계속 쌓이는 목록이라, 스냅샷(회차별 1개)과 달리 고객사 루트 폴더에 둔다."""
    return profile_manager.profile_dir(customer_name, profile_name).parent / HISTORY_FILE


def _read_history_rows(customer_name: str, profile_name: str) -> list:
    """고객사 누적 지원이력. 파일이 깨졌으면 <파일>.corrupt-<시각> 으로 옮겨 보존하고 빈 목록으로 시작한다 —
    예전엔 깨진 파일을 [] 로 보고 그 위에 새 한 줄을 덮어써서 고객사 누적 이력이 통째로 사라졌다.
    읽기 자체가 안 되면(권한 등) 보고서 생성을 멈춘다(덮어쓰면 안 되므로)."""
    path = _history_path(customer_name, profile_name)
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InspectionReportError(f"지원이력 파일을 읽을 수 없습니다({exc}) — 파일을 확인한 뒤 다시 시도하세요.")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        preserved = path.with_name(f"{path.name}.corrupt-{datetime.datetime.now():%Y%m%d_%H%M%S}")
        try:
            path.replace(preserved)
        except OSError:
            raise InspectionReportError(f"지원이력 파일이 손상됐습니다({exc}) — 파일을 확인한 뒤 다시 시도하세요.")
        print(f"[보고서] 지원이력 파일이 손상돼 {preserved.name} 으로 보존하고 새로 시작합니다: {exc}")
        return []
    return data if isinstance(data, list) else []


def _with_history_row(rows: list, *, date: str, note: str, remark: str, profile: str) -> list:
    """rows 에 이번 회차 한 줄을 반영한 새 목록(같은 날짜·내역이면 비고만 갱신). 입력은 바꾸지 않는다."""
    rows = [dict(r) for r in rows]
    for row in rows:
        if row.get("date") == date and row.get("note") == note:
            if remark:
                row["remark"] = remark
            break
    else:
        rows.append({"date": date, "note": note, "remark": remark, "profile": profile})
    rows.sort(key=lambda r: r.get("date") or "")
    return rows


def append_support_history(customer_name: str, profile_name: str, *, date: str, note: str,
                            remark: str = "") -> list:
    """PDF 보고서를 생성할 때마다 '이번 회차 정기점검' 한 줄을 지원이력에 추가한다.
    같은 (날짜, 내역) 조합이 이미 있으면 덮어쓴다 — 같은 회차로 PDF를 다시 만들어도 줄이
    늘어나지 않되, 비고(점검 대수/확인필요 건수)는 최신 결과로 갱신되어야 하기 때문.
    원자적으로 쓴다 — 쓰다 죽어 잘린 파일이 다음 번에 '깨진 이력'이 되지 않게."""
    rows = _with_history_row(_read_history_rows(customer_name, profile_name),
                             date=date, note=note, remark=remark, profile=profile_name)
    write_text_atomic(str(_history_path(customer_name, profile_name)),
                      json.dumps(rows, ensure_ascii=False, indent=2))
    return rows


def _past_inspections(customer_name: str, profile_name: str) -> list:
    """같은 고객사의 지난 회차들을 실제 산출물에서 되짚어 지원이력 후보로 만든다.

    지원이력 파일은 PDF를 만든 회차만 기록한다. 그래서 도입 전에 진행한 회차나 엑셀만 만든
    회차는 '05_지원이력' 페이지에서 통째로 빠져, 처음 PDF를 뽑으면 이력이 한 줄뿐인
    placeholder 처럼 보였다. 회차별 reports/_snapshot.json(= 그 회차 보고서를 만든 시각과
    장비 수)을 읽어 빠진 줄을 채운다."""
    derived = []
    for profile in profile_manager.list_profiles(customer_name):
        name = profile.get("name")
        if not name:
            continue
        path = profile_manager.profile_dir(customer_name, name) / REPORTS_SUBDIR / SNAPSHOT_FILE
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        date = (data.get("saved_at") or "")[:10]
        if not date:
            continue
        device_count = len(data.get("devices") or {})
        derived.append({"date": date, "note": f"{name} 정기점검",
                        "remark": f"점검 장비 {device_count}대" if device_count else "",
                        "profile": name})
    return derived


def load_support_history(customer_name: str, profile_name: str, extra: dict = None) -> list:
    """[순번, 일자, 지원 내역, 비고] 행 목록(오래된 순) — PDF의 05_지원이력 표에 그대로 들어간다.
    기록된 이력에 지난 회차 산출물에서 되짚은 줄을 합쳐 돌려준다(같은 내역은 기록 쪽이 이긴다).
    extra({date, note, remark})는 아직 기록하지 않은 이번 회차 줄 — 파일에는 쓰지 않고 표에만 넣는다."""
    rows = _read_history_rows(customer_name, profile_name)
    if extra:
        rows = _with_history_row(rows, date=extra["date"], note=extra["note"],
                                 remark=extra.get("remark", ""), profile=profile_name)
    known = {(r.get("date"), r.get("note")) for r in rows}
    rows = rows + [r for r in _past_inspections(customer_name, profile_name)
                   if (r["date"], r["note"]) not in known]
    rows.sort(key=lambda r: r.get("date") or "")
    return [[i, r.get("date", ""), r.get("note", ""), r.get("remark", "")]
            for i, r in enumerate(rows, start=1)]


# --------------------------------------------------------------------------- 컨텍스트 조립

def _inventory_by_name(project_id) -> dict:
    """장비목록을 {장비명: device dict}로. 활성 프로젝트가 없거나 목록이 비어도 빈 dict를
    돌려주고 보고서 생성 자체는 막지 않는다 — 원본로그만으로도 대부분의 값은 채워진다."""
    if not project_id:
        return {}
    try:
        from engine import project_manager as pm
        from engine import device_inventory as di
        paths = pm.project_paths(project_id)
        inventory = di.load_inventory(paths["device_inventory"], paths["lab_meta"],
                                       paths["ip_allocation"])
    except Exception:
        return {}
    return {d.get("name"): d for d in inventory.get("devices", []) if d.get("name")}


def _inventory_record(inventory: dict, name: str) -> dict:
    """장비명으로 장비목록 항목을 찾는다 — 정확히 일치하지 않으면 core/device_names 로 맞춘다(대소문자 무시,
    별칭, 도메인 뗀 이름). 로그 파일명(장비가 알려준 hostname)과 장비목록에 손으로 적은 이름은 'Agg1' / 'agg1' /
    'agg1.lab.local' 처럼 표기만 다르거나, 장비 이름을 바꾸기 전 회차의 로그라 옛 이름일 수 있다 — 그때마다 IP·모델
    열이 통째로 비면 보고서가 쓸모없어진다(옛 이름은 장비 목록의 별칭에 적는다)."""
    if name in inventory:
        return inventory[name]
    from core.device_names import DeviceNameIndex, clean
    # 색인은 공백을 뗀 이름을 돌려준다 — 장비목록 키(손으로 적어 앞뒤 공백이 있을 수 있다)로 되돌린다.
    by_clean = {clean(key): record for key, record in inventory.items()}
    hit = DeviceNameIndex({**record, "name": key} for key, record in inventory.items()).resolve(name)
    return by_clean.get(hit, {}) if hit else {}


# Management/관리 인터페이스의 IP — 장비목록에 IP가 없을 때 원본로그에서 주워 온다.
_MGMT_IP_RE = re.compile(
    r"^\s*(?:Management|Ma)\s?\d+[\w./]*\s+(\d{1,3}(?:\.\d{1,3}){3})(?:/\d+)?\b",
    re.MULTILINE | re.IGNORECASE)
_IP_ADDRESS_LINE_RE = re.compile(
    r"Internet address is\s+(\d{1,3}(?:\.\d{1,3}){3})", re.IGNORECASE)


def _ip_from_sections(sections: dict) -> str:
    """원본로그에서 관리 IP를 찾는다(장비목록에 없을 때의 폴백).
    'show ip interface brief' 의 Management1 행을 먼저 보고, 없으면 'Internet address is'."""
    for output in (sections or {}).values():
        match = _MGMT_IP_RE.search(output or "")
        if match:
            return match.group(1)
    for output in (sections or {}).values():
        match = _IP_ADDRESS_LINE_RE.search(output or "")
        if match:
            return match.group(1)
    return ""


# 장비 정보는 벤더마다 적는 곳이 다르다 — 위의 규칙부터, 로그 전체에서 먼저 걸리는 값을 쓴다(IMPROVEMENT_PLAN 4-5).
_OS_VERSION_RES = (
    re.compile(r"Software image version:\s*(\S+)"),                                        # Arista EOS
    re.compile(r"Cisco IOS[ -]XE Software,\s*Version\s+([^\s,]+)"),                       # IOS-XE(둘째 줄의 IOS 표기보다 먼저)
    re.compile(r"^\s*(?:NXOS|system):\s+version\s+(\S+)", re.MULTILINE | re.IGNORECASE),   # NX-OS
    re.compile(r"Cisco IOS Software.*?,\s*Version\s+([^\s,]+)"),                          # IOS
)
_SERIAL_RES = (
    re.compile(r"Serial number:\s*(\S+)"),                                                 # Arista EOS
    re.compile(r"System [Ss]erial [Nn]umber\s*:\s*(\S+)"),                                 # Cisco Catalyst
    re.compile(r"Processor [Bb]oard ID\s+(\S+)"),                                          # Cisco IOS·NX-OS show version
    re.compile(r"\bSN:\s*([^\s,]+)"),                                                       # Cisco show inventory 첫 항목(섀시)
)
_MODEL_RES = (
    re.compile(r"^Arista\s+(\S+)", re.MULTILINE),                                          # Arista EOS
    re.compile(r"^\s*Model [Nn]umber\s*:\s*(\S+)", re.MULTILINE),                          # Cisco Catalyst
    re.compile(r"\bPID:\s*([^\s,]+)"),                                                      # Cisco show inventory 첫 항목(섀시)
)
# show version 으로 플랫폼을 알아본다 — 장비 목록의 벤더 칸보다 실제로 수집된 로그가 확실하다.
# IOS-XE 는 둘째 줄에 'Cisco IOS Software' 도 적으므로 XE 를 먼저 본다.
_PLATFORM_HINTS = (
    (re.compile(r"Cisco Nexus Operating System|\bNX-OS\b"), vendors.CISCO_NXOS),
    (re.compile(r"Cisco IOS[ -]XE Software"), vendors.CISCO_XE),
    (re.compile(r"Cisco IOS Software|Cisco Internetwork Operating System"), vendors.CISCO_IOS),
    (re.compile(r"^Arista\s", re.MULTILINE), vendors.ARISTA_EOS),
)


def _first_match(sections: dict, patterns) -> str:
    for pattern in patterns:
        for output in sections.values():
            match = pattern.search(output or "")
            if match:
                return match.group(1)
    return ""


def _os_version(sections: dict) -> str:
    return _first_match(sections, _OS_VERSION_RES)


def _serial(sections: dict) -> str:
    return _first_match(sections, _SERIAL_RES)


def _model(sections: dict) -> str:
    return _first_match(sections, _MODEL_RES)


def _platform(record: dict, sections: dict) -> str:
    """판정에 쓸 플랫폼 — show version 에서 알아보고, 없으면 장비 목록의 벤더 칸(core/vendors.platform_of)."""
    version = next((output for command, output in sections.items() if "show version" in command.lower()), "")
    for pattern, platform in _PLATFORM_HINTS:
        if pattern.search(version or ""):
            return platform
    return vendors.platform_of(record.get("vendor"))


def _remarks(items: list) -> tuple:
    """점검 항목 판정 결과에서 특이사항 문장을 동적으로 만든다.
    반환: (요약 한 줄 목록 문자열, 원본 출력까지 붙인 상세 문자열).

    확인필요는 그대로 올리고, 미수집/해당없음은 '조치 대상'이 아니므로 끝에 한 줄로만
    묶어 남긴다 — 아예 빼면 왜 그 항목이 비었는지 보고서만 봐서는 알 수 없다."""
    lines, detail, aside = [], [], []
    for item in items:
        status = item.get("status")
        if status == STATUS_WARN:
            lines.append(f"# {item['name']}: {item.get('value')}")
            detail.append(
                f"# {item['name']} ({item.get('method')})\n{item.get('detail') or item.get('value')}")
        elif status in (STATUS_NA, STATUS_SKIP):
            aside.append(f"{item['name']}({status})")
    if aside:
        note = "※ 판정 제외: " + ", ".join(aside)
        lines.append(note)
        detail.append(note)
    return "\n".join(lines), "\n\n".join(detail)


def status_counts(items: list) -> dict:
    """항목 목록의 상태별 건수 — 요약표(정상/비정상/미수집)와 소견 문구가 함께 쓴다."""
    counts = {"total": len(items), STATUS_OK: 0, STATUS_WARN: 0, STATUS_NA: 0, STATUS_SKIP: 0}
    for item in items:
        status = item.get("status")
        if status in counts:
            counts[status] += 1
    # 미수집 + 해당없음 = '판정하지 않음'. 요약표의 세 번째 숫자가 이것이다.
    counts["not_judged"] = counts[STATUS_NA] + counts[STATUS_SKIP]
    return counts


def _opinion(overall: str, counts: dict) -> str:
    """'조치 및 점검 소견' 기본 문구. 미수집/해당없음뿐인 장비까지 '확인 필요'로 적으면
    실제로 손봐야 하는 장비와 구분이 안 된다."""
    if overall == STATUS_UNREACHABLE:
        return "접속 불가 — 재점검 필요"
    if counts.get(STATUS_WARN):
        return "확인 필요"
    if counts.get(STATUS_NA):
        return f"이상 없음 (미수집 {counts[STATUS_NA]}항목 재점검 권고)"
    if counts.get(STATUS_SKIP):
        return f"이상 없음 (해당없음 {counts[STATUS_SKIP]}항목 — 미구성/미지원)"
    return ""


def build_context(customer_name: str, profile_name: str, *, project_id=None,
                   inspection_date=None, manager=None, inspector=None, confirmer=None,
                   site_name=None, vendor=None, report_title=None,
                   devices_filter=None, template_path=None, is_virtual=None, log_dirs=None) -> dict:
    """보고서 렌더링에 필요한 모든 데이터를 한 dict으로 조립한다(파일은 쓰지 않는다).

    devices_filter: 장비명 목록을 주면 그 장비만 포함한다(부분 보고서용, None이면 전체).
    장비목록에는 있는데 원본로그가 없는 장비는 '접속 불가'로 포함해 빈 시트를 남긴다 —
    보고서에서 아예 빠지면 점검 누락과 구분이 안 되기 때문(LGES 보고서의 '접속 불가' 표기 방식).
    log_dirs: 원본 로그를 이 폴더들에서만 찾는다. 무인 정기점검은 이번 회차의 raw 만 넘긴다 — 모든 회차를
    섞으면 이번에 수집하지 못한 장비가 지난 회차 로그로 '정상' 보고된다(아무도 지켜보지 않는 실행이라 위험하다).

    is_virtual: 가상환경 여부. None이면 프로파일 설정(profile/profile.json 의 is_virtual)에서
    읽는다 — 호출부(API/테스트)가 명시로 덮어쓸 수 있게만 열어둔다.
    """
    if is_virtual is None:
        is_virtual = profile_manager.is_virtual(customer_name, profile_name)
    template = inspection_evaluator.load_template(template_path, customer=customer_name)
    meta = template["meta"]
    logs = latest_logs_by_device(customer_name, profile_name, dirs=log_dirs)
    inventory = _inventory_by_name(project_id)
    report_date = inspection_date or max((info["collected_at"].date().isoformat() for info in logs.values()),
                                         default=datetime.date.today().isoformat())
    previous = (load_previous_snapshot(customer_name, profile_name, before=report_date).get("devices") or {})

    names = sorted(set(logs) | set(inventory))
    if devices_filter:
        wanted = {str(n) for n in devices_filter}
        names = [n for n in names if n in wanted]
    if not names:
        raise InspectionReportError(
            "보고서를 만들 장비가 없습니다. 세션 터미널에서 점검을 실행해 원본로그를 남기거나 "
            "장비 목록을 먼저 등록하세요.")

    collected_dates = sorted(info["collected_at"].date().isoformat() for info in logs.values())
    template_key = _template_key(template)
    devices = []
    for name in names:
        log = logs.get(name)
        record = _inventory_record(inventory, name)
        sections = inspection_evaluator.split_transcript(log["text"]) if log else {}
        items = (_evaluate_cached(log["path"], sections, template, template_key, is_virtual=is_virtual,
                                  platform=_platform(record, sections))
                 if sections else [])
        previous_values = previous.get(name, {})
        for item in items:
            item["previous"] = previous_values.get(item["name"], "")

        remarks, remarks_detail = _remarks(items)
        counts = status_counts(items)
        warn_count = counts[STATUS_WARN]
        if log is None:
            overall = STATUS_UNREACHABLE
        elif warn_count:
            overall = STATUS_WARN
        elif not items:
            overall = STATUS_NA
        else:
            overall = STATUS_OK

        site = record.get("site", "")
        devices.append({
            "name": name,
            "model": record.get("model") or _model(sections),
            # 장비목록이 단일 출처지만 비어 있으면 원본로그의 관리 IP라도 채운다 —
            # IP 열이 전부 공란인 보고서는 장비를 특정할 수 없어 쓸 수 없다.
            "ip": record.get("management_ip") or _ip_from_sections(sections),
            "serial": _serial(sections),
            "os_version": _os_version(sections),
            "role": record.get("role") or record.get("zone") or "",
            "memo": record.get("memo", ""),
            "site": site,
            # 장비목록의 '위치' 표기 — site > zone > role 순으로 있는 것을 쓴다.
            # PDF 장비목록 페이지의 '위치' 열이 placeholder(공란)로만 나오던 원인.
            "location": record.get("location") or site or record.get("zone") or record.get("role", ""),
            "warranty": record.get("warranty", ""),
            "unreachable": log is None,
            "collected_at": log["collected_at"].isoformat(timespec="seconds") if log else "",
            "command_count": len(sections),
            "items": items,
            "status_counts": counts,
            # PDF 보고서(report/inspection_pdf.py)가 서버 CPU/Memory를 Linux 원본 로그에서
            # 직접 재계산할 때 쓴다 — YAML evaluator는 Arista show-명령 전용이라 서버 로그엔
            # 매칭되지 않기 때문에 원본 섹션을 그대로 넘겨준다.
            "sections": sections,
            "warn_count": warn_count,
            "na_count": counts["not_judged"],
            "overall_status": overall,
            "remarks": remarks,
            "remarks_detail": remarks_detail,
            "opinion": _opinion(overall, counts),
        })

    def _person(key, override):
        base = dict(meta.get(key) or {})
        base.update({k: v for k, v in (override or {}).items() if v})
        return base

    resolved_date = inspection_date or (collected_dates[-1] if collected_dates
                                         else datetime.date.today().isoformat())
    from report.inspection_pdf import month_strings
    target_month, target_month_short = month_strings(resolved_date)

    return {
        "customer": customer_name,
        "profile": profile_name,
        "is_virtual": bool(is_virtual),
        "inspection_date": resolved_date,
        "manager": _person("manager", manager),
        "inspector": _person("inspector", inspector),
        # 확인자(발주사 측 확인 담당자) — 표준 표지 양식의 '확인자 회사/확인자 담당자'.
        # 기존 파이프라인엔 이 개념이 없어서 meta.confirmer 기본값 + 회차별 override로 새로 추가.
        "confirmer": _person("confirmer", confirmer),
        "site_name": site_name or meta.get("site_name") or customer_name,
        "vendor": vendor or meta.get("vendor", ""),
        "report_title": report_title or meta.get("report_title", "네트워크 스위치 정기점검"),
        "target_month": target_month,
        "target_month_short": target_month_short,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "template": template,
        "devices": devices,
        "log_count": len(logs),
        "previous_available": bool(previous),
    }


# --------------------------------------------------------------------------- 출력

def _unused_path(path: Path) -> Path:
    """같은 이름의 보고서가 이미 있으면 '_v2', '_v3' … 를 붙인 새 이름. 예전엔 당일 날짜 파일명으로
    덮어써서, 손으로 보완해 둔 보고서가 경고 없이 사라졌다(엑셀에서 열려 있으면 저장 오류도 났다)."""
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem}_v{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


# --------------------------------------------------------------------------- 포맷 레지스트리
#
# 보고서 데이터는 build_context() 한 벌이고, 포맷은 'context 를 파일로 그리는 방법'만 다르다. 포맷을
# 더하려면 render 함수 하나를 register_format() 으로 등록한다 — 저장 위치(reports/), 이름 충돌(_v2…),
# 다음 회차용 스냅샷은 export() 가 모든 포맷에 똑같이 처리한다.

@dataclass(frozen=True)
class ReportFormat:
    format_id: str
    label: str
    extension: str
    # render(context, path, customer_name, profile_name) -> dict: path 에 파일을 쓰고, 결과에 더할 필드를 돌려준다.
    render: Callable[[dict, Path, str, str], dict]


_FORMATS = {}


def register_format(fmt: ReportFormat) -> None:
    _FORMATS[fmt.format_id] = fmt


def report_formats() -> list:
    """등록된 포맷 [{id, label, extension}] — 등록 순서."""
    return [{"id": f.format_id, "label": f.label, "extension": f.extension} for f in _FORMATS.values()]


def _render_excel(context, path, customer_name, profile_name):
    audit = _audit_appendix(context, customer_name, profile_name)
    workbook = _excel().build_workbook(context, audit=audit)
    try:
        workbook.save(path)
    finally:
        workbook.close()
    return {"sheets": ["표지", "장비현황", "점검요약"] + [d["name"] for d in context["devices"]]
            + (["실시간감시이력"] if audit else [])}


def _render_pdf(context, path, customer_name, profile_name):
    """표준 양식(report/inspection_pdf.py — 실제 정기점검 PDF 7개를 분석해 만든 양식). 표지·장비목록·
    지원이력처럼 PDF 에만 있는 부분의 데이터를 여기서 더 모은다."""
    from report.inspection_pdf import build_pdf

    # 장비목록 페이지: 위치/Warranty 는 장비목록(Device Inventory)의 값을 그대로 쓴다.
    # 예전에는 '위치'에 role 을, Warranty 에 빈 문자열을 넣어 두 열이 항상 공란이었다.
    equipment = [[d["name"], d.get("model", ""), d.get("serial", ""),
                  d.get("location") or d.get("role", ""), d.get("warranty", "")]
                 for d in context["devices"]]

    warn_devices = sum(1 for d in context["devices"] if d.get("warn_count"))
    remark = f"점검 장비 {len(context['devices'])}대"
    if warn_devices:
        remark += f" / 확인필요 {warn_devices}대"
    entry = {"date": context["inspection_date"], "note": f"{context['target_month']} 정기점검", "remark": remark}
    history = load_support_history(customer_name, profile_name, extra=entry)

    build_pdf(context, path, equipment=equipment, history=history,
              topology=_topology_appendix(context, customer_name, profile_name),
              audit=_audit_appendix(context, customer_name, profile_name))
    # 이력은 PDF 가 실제로 만들어진 뒤에 남긴다 — 예전엔 먼저 기록해서, 생성이 실패해도 '정기점검' 한 줄이 남았다.
    append_support_history(customer_name, profile_name, **entry)
    return {}


def _topology_appendix(context, customer_name, profile_name):
    """PDF 부록의 네트워크 구성도(IMPROVEMENT_PLAN 4-7) — 이 보고서의 로그로 만든 구성도(구성도 탭과 같은 빌더·배치,
    사용자가 끌어 옮긴 배치가 있으면 그대로)와 앞선 회차 대비 링크 변화. 장비 사이 링크가 없거나 만들다 실패하면 None
    — 부록 없이 보고서를 만든다(보고서가 구성도 때문에 실패하면 안 된다)."""
    from core.app_logger import log_event
    try:
        from engine import round_compare
        from engine.topology_layout import layout, saved_positions
        from engine.topology_svg import figure
        sections = {d["name"]: d["sections"] for d in context["devices"] if d.get("sections")}
        project_id = context.get("project_id") or round_compare.project_of(customer_name, profile_name)
        topology = round_compare.topology_of(project_id, sections) if sections else None
        if not topology or not topology["edges"]:
            return None
        layout_info = layout(topology, saved_positions(f"id:{project_id}") if project_id else {})
        changes = None
        base = round_compare.default_pair(customer_name, profile_name)[0]
        if base is not None:
            try:
                previous = round_compare.round_links(customer_name, base)
            except round_compare.CompareError:
                previous = None
            if previous is not None:
                summary = round_compare.mark_link_changes(topology, previous,
                                                          round_compare.observers_of(topology, sections))
                rows = [["사라진 링크", f"{e['a']} {e['a_port']}", f"{e['b']} {e['b_port']}"]
                        for e in topology["edges"] if e.get("state") == "lost"]
                rows += [["새 링크", f"{e['a']} {m.get('a_port', '')}", f"{e['b']} {m.get('b_port', '')}"]
                         for e in topology["edges"] if e.get("state") != "lost"
                         for m in e.get("members") or [] if m.get("change") == "new"]
                changes = dict(summary, base=previous["label"], rows=rows)
        return {"figure": figure(topology, layout_info), "changes": changes}
    except Exception as exc:
        log_event(f"PDF 부록의 구성도를 만들지 못해 부록 없이 만듭니다: {exc}", source="report", level="WARNING")
        return None


def _audit_appendix(context, customer_name, profile_name):
    """보고서 부록의 실시간 감시 이력(IMPROVEMENT_PLAN 4-8) — 이 회차에 실시간 감시가 남긴 경고와 처리 결과
    (engine/realtime_audit). 보고서에 넣은 장비의 것만. 감시를 쓰지 않은 회차이거나 읽다 실패하면 None — 부록
    없이 보고서를 만든다."""
    from core.app_logger import log_event
    try:
        from engine import realtime_audit
        return realtime_audit.audit(customer_name, profile_name,
                                    devices=[d["name"] for d in context.get("devices") or []] or None)
    except Exception as exc:
        log_event(f"보고서 부록의 실시간 감시 이력을 만들지 못해 부록 없이 만듭니다: {exc}", source="report",
                  level="WARNING")
        return None


register_format(ReportFormat("excel", "Excel", ".xlsx", _render_excel))
register_format(ReportFormat("pdf", "PDF", ".pdf", _render_pdf))


def _report_filename(customer_name, profile_name, fmt, filename, inspection_date):
    """화면이 준 이름(없으면 표준 이름)을 한 조각으로 다듬고 포맷 확장자로 맞춘다. 화면 입력이
    'reports\\..\\..\\x' 처럼 폴더 밖을 가리키지 못하게 safe_filename 을 거친다."""
    name = (safe_filename(filename) if filename
            else build_filename(customer_name, profile_name, date=inspection_date))
    for other in _FORMATS.values():          # 표준 이름(.xlsx)이나 다른 포맷 확장자를 떼고 붙인다
        if name.lower().endswith(other.extension):
            name = name[: -len(other.extension)]
            break
    return name + fmt.extension


def export(customer_name: str, profile_name: str, format_id: str, *, filename=None, **kwargs) -> dict:
    """정기점검 보고서를 data/<고객사>/<프로파일>/reports/ 에 format_id 포맷으로 저장한다(보고서 탭·
    워크스페이스 공통). kwargs 는 build_context() 옵션. 같은 이름이 있으면 _v2 … 로 저장하고, 다음 회차의
    '전월 점검값'이 될 스냅샷을 남긴다.
    반환: {path, filename, dir, format, device_count, warn_count, na_count, unreachable_count, …포맷별 필드}."""
    fmt = _FORMATS.get(format_id)
    if fmt is None:
        raise InspectionReportError(f"지원하지 않는 보고서 형식입니다: {format_id} ({', '.join(_FORMATS)} 중 하나)")
    context = build_context(customer_name, profile_name, **kwargs)
    target_dir = reports_dir(customer_name, profile_name)
    path = _unused_path(target_dir / _report_filename(customer_name, profile_name, fmt, filename,
                                                      context["inspection_date"]))
    extra = fmt.render(context, path, customer_name, profile_name)
    save_snapshot(customer_name, profile_name, context["devices"], context["inspection_date"])
    devices = context["devices"]
    return {
        "path": str(path), "filename": path.name, "dir": str(target_dir), "format": fmt.format_id,
        "device_count": len(devices),
        "warn_count": sum(d["warn_count"] for d in devices),
        "na_count": sum(d["na_count"] for d in devices),
        "unreachable_count": sum(1 for d in devices if d["unreachable"]),
        **(extra or {}),
    }


def list_reports(customer_name: str, profile_name: str) -> list:
    """보고서 목록(최신순) — 프로파일 reports/ + 각 회차 runs/<run_id>/reports/(예전 버전이 회차 폴더에
    만든 보고서). 등록된 포맷의 확장자만 본다. 조회만으로 폴더를 만들지 않는다(빈 폴더가 생기면
    '보고서 있음'으로 오해된다)."""
    from engine import log_storage
    pdir = log_storage.existing_profile_dir(customer_name, profile_name)
    if pdir is None:
        return []
    dirs = [pdir / REPORTS_SUBDIR]
    runs_dir = pdir / "runs"
    if runs_dir.is_dir():
        for run_path in sorted(runs_dir.iterdir()):
            if run_path.is_dir():
                dirs.append(run_path / "reports")

    seen = set()
    files = []
    for directory in dirs:
        if not directory.exists():
            continue
        for fmt in _FORMATS.values():
            for path in directory.glob(f"*{fmt.extension}"):
                norm = str(path.resolve())
                if norm in seen:
                    continue
                seen.add(norm)
                stat = path.stat()
                files.append({
                    "name": path.name, "path": str(path), "size": stat.st_size,
                    "mtime": stat.st_mtime,
                    "mtime_str": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                })
    return sorted(files, key=lambda f: f["mtime"], reverse=True)
