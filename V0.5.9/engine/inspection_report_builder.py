"""
정기점검 보고서 데이터 조립 + 파일 출력.

report/inspection_excel.py가 "엑셀을 어떻게 그릴지"만 담당하는 것과 짝을 이뤄, 이 모듈은
"무엇을 그릴지"(어느 로그를 읽고, 무엇과 조인하고, 어디에 어떤 이름으로 저장할지)를 담당한다.

데이터 흐름:
    data/<고객사>/<프로파일>/cache/original_log/AutoCheck_<장비>_<날짜>_<시각>.txt   (원본로그)
        -> report.inspection_excel.split_transcript()      커맨드별 구간 분리
        -> report.inspection_excel.evaluate_device()        항목별 판정(비즈니스 로직)
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
import json
import re
from pathlib import Path

from core import log_naming
from core.paths import sanitize_component
from core.text_io import read_log_text
from engine.profile_manager import profile_manager
from report.inspection_status import (
    NOT_JUDGED_STATUSES, STATUS_NA, STATUS_OK, STATUS_SKIP, STATUS_UNREACHABLE, STATUS_WARN,
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


def latest_logs_by_device(customer_name: str, profile_name: str) -> dict:
    """점검 회차 폴더(runs/<run_id>/raw)에서 장비별 최신 로그 1개씩 —
    {장비명: {"path", "collected_at", "text"}}.

    파일명 해석은 core/log_naming.py 단일 출처를 쓴다. 예전에는 이 함수가 레거시
    'AutoCheck_<장비>_<날짜>_<시각>.txt' 만 아는 자체 정규식을 갖고 있어서, 현재 규칙인
    '<날짜>_<시각>_raw_<장비>.txt' 가 하나도 매칭되지 않았다. 그러면 fallback 으로 파일명
    전체가 장비명이 되어 보고서 Hostname 에 '20260805_095518_raw_Agg1' 이 찍히고, 그 이름은
    장비목록의 'Agg1' 과 매칭되지 않으니 IP·모델·용도 열이 전부 빈칸으로 남았다.
    규칙에 맞지 않는 파일(사용자가 손으로 넣은 로그)은 여전히 확장자를 뗀 이름을 쓴다."""
    latest = {}
    for directory in original_log_dirs(customer_name, profile_name):
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


# --------------------------------------------------------------------------- 전월 스냅샷

def _snapshot_path(customer_name: str, profile_name: str) -> Path:
    return reports_dir(customer_name, profile_name) / SNAPSHOT_FILE


def save_snapshot(customer_name: str, profile_name: str, devices: list) -> Path:
    """이번 회차의 항목별 값을 저장 — 다음 회차의 '전월 점검값'이 된다."""
    payload = {
        "customer": customer_name, "profile": profile_name,
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "devices": {
            device["name"]: {item["name"]: item.get("value") for item in device.get("items", [])}
            for device in devices
        },
    }
    path = _snapshot_path(customer_name, profile_name)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_previous_snapshot(customer_name: str, profile_name: str) -> dict:
    """같은 고객사의 '이전 회차' 스냅샷 — 자기 자신을 제외하고, 저장 시각이 가장 최근인 것.
    회차 폴더명(예: '2026-07')이 시간순 정렬이 안 되는 경우도 있어 폴더명 대신
    스냅샷 안의 saved_at을 기준으로 고른다. 없으면 빈 dict."""
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
        if best is None or (data.get("saved_at") or "") > (best.get("saved_at") or ""):
            best = data
    return best or {}


# --------------------------------------------------------------------------- 지원 이력 (PDF 표준 양식 05_지원이력)

def _history_path(customer_name: str, profile_name: str) -> Path:
    """지원이력은 프로파일(회차)이 아니라 고객사 단위로 누적한다 — 표준 양식의 '05_지원이력'은
    회차가 바뀌어도 계속 쌓이는 목록이라, 스냅샷(회차별 1개)과 달리 고객사 루트 폴더에 둔다."""
    return profile_manager.profile_dir(customer_name, profile_name).parent / HISTORY_FILE


def _read_history_rows(customer_name: str, profile_name: str) -> list:
    path = _history_path(customer_name, profile_name)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except (OSError, json.JSONDecodeError):
        return []


def append_support_history(customer_name: str, profile_name: str, *, date: str, note: str,
                            remark: str = "") -> list:
    """PDF 보고서를 생성할 때마다 '이번 회차 정기점검' 한 줄을 지원이력에 추가한다.
    같은 (날짜, 내역) 조합이 이미 있으면 덮어쓴다 — 같은 회차로 PDF를 다시 만들어도 줄이
    늘어나지 않되, 비고(점검 대수/확인필요 건수)는 최신 결과로 갱신되어야 하기 때문."""
    rows = _read_history_rows(customer_name, profile_name)
    for row in rows:
        if row.get("date") == date and row.get("note") == note:
            if remark:
                row["remark"] = remark
            break
    else:
        rows.append({"date": date, "note": note, "remark": remark, "profile": profile_name})
    rows.sort(key=lambda r: r.get("date") or "")
    path = _history_path(customer_name, profile_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
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


def load_support_history(customer_name: str, profile_name: str) -> list:
    """[순번, 일자, 지원 내역, 비고] 행 목록(오래된 순) — PDF의 05_지원이력 표에 그대로 들어간다.
    기록된 이력에 지난 회차 산출물에서 되짚은 줄을 합쳐 돌려준다(같은 내역은 기록 쪽이 이긴다)."""
    rows = _read_history_rows(customer_name, profile_name)
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
    """장비명으로 장비목록 항목을 찾는다 — 정확히 일치하지 않으면 대소문자/공백을 무시하고
    다시 찾는다. 로그 파일명(장비가 알려준 hostname)과 장비목록에 손으로 적은 이름은
    'Agg1' / 'agg1' / 'AGG1' 처럼 표기만 다른 경우가 흔한데, 그때마다 IP·모델 열이 통째로
    비면 보고서가 쓸모없어진다."""
    if name in inventory:
        return inventory[name]
    key = str(name or "").strip().lower()
    for candidate, record in inventory.items():
        if str(candidate).strip().lower() == key:
            return record
    return {}


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


def _os_version(sections: dict) -> str:
    for output in sections.values():
        match = re.search(r"Software image version:\s*(\S+)", output or "")
        if match:
            return match.group(1)
    return ""


def _serial(sections: dict) -> str:
    for output in sections.values():
        match = re.search(r"Serial number:\s*(\S+)", output or "")
        if match:
            return match.group(1)
    return ""


def _model(sections: dict) -> str:
    for output in sections.values():
        match = re.search(r"^Arista\s+(\S+)", output or "", re.MULTILINE)
        if match:
            return match.group(1)
    return ""


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
                   devices_filter=None, template_path=None, is_virtual=None) -> dict:
    """보고서 렌더링에 필요한 모든 데이터를 한 dict으로 조립한다(파일은 쓰지 않는다).

    devices_filter: 장비명 목록을 주면 그 장비만 포함한다(부분 보고서용, None이면 전체).
    장비목록에는 있는데 원본로그가 없는 장비는 '접속 불가'로 포함해 빈 시트를 남긴다 —
    보고서에서 아예 빠지면 점검 누락과 구분이 안 되기 때문(LGES 보고서의 '접속 불가' 표기 방식).

    is_virtual: 가상환경 여부. None이면 프로파일 설정(profile/profile.json 의 is_virtual)에서
    읽는다 — 호출부(API/테스트)가 명시로 덮어쓸 수 있게만 열어둔다.
    """
    if is_virtual is None:
        is_virtual = profile_manager.is_virtual(customer_name, profile_name)
    template = _excel().load_template(template_path)
    meta = template["meta"]
    logs = latest_logs_by_device(customer_name, profile_name)
    inventory = _inventory_by_name(project_id)
    previous = (load_previous_snapshot(customer_name, profile_name).get("devices") or {})

    names = sorted(set(logs) | set(inventory))
    if devices_filter:
        wanted = {str(n) for n in devices_filter}
        names = [n for n in names if n in wanted]
    if not names:
        raise InspectionReportError(
            "보고서를 만들 장비가 없습니다. 세션 터미널에서 점검을 실행해 원본로그를 남기거나 "
            "장비 목록을 먼저 등록하세요.")

    collected_dates = sorted(info["collected_at"].date().isoformat() for info in logs.values())
    devices = []
    for name in names:
        log = logs.get(name)
        record = _inventory_record(inventory, name)
        sections = _excel().split_transcript(log["text"]) if log else {}
        items = _excel().evaluate_device(sections, template, is_virtual=is_virtual) if sections else []
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

def export_report(customer_name: str, profile_name: str, *, filename=None, **kwargs) -> dict:
    """보고서 엑셀을 data/<고객사>/<프로파일>/reports/ 에 저장하고 결과를 반환한다.
    폴더가 없으면 만든다. 반환: {path, filename, device_count, warn_count}."""
    context = build_context(customer_name, profile_name, **kwargs)
    workbook = _excel().build_workbook(context)
    target_dir = reports_dir(customer_name, profile_name)
    name = filename or build_filename(customer_name, profile_name,
                                       date=context["inspection_date"])
    if not name.lower().endswith(".xlsx"):
        name += ".xlsx"
    path = target_dir / name
    try:
        workbook.save(path)
    finally:
        workbook.close()
    save_snapshot(customer_name, profile_name, context["devices"])
    return {
        "path": str(path), "filename": name, "dir": str(target_dir),
        "device_count": len(context["devices"]),
        "warn_count": sum(d["warn_count"] for d in context["devices"]),
        "na_count": sum(d["na_count"] for d in context["devices"]),
        "unreachable_count": sum(1 for d in context["devices"] if d["unreachable"]),
        "sheets":["표지", "장비현황", "점검요약"] + [d["name"] for d in context["devices"]],
    }


def export_pdf_report(customer_name: str, profile_name: str, *, filename=None, **kwargs) -> dict:
    """표준 양식(report/inspection_pdf.py) PDF를 data/<고객사>/<프로파일>/reports/ 에 저장한다.
    엑셀 파이프라인(export_report)과 데이터 조립은 완전히 같고(build_context 재사용),
    출력 포맷만 다르다 — 같은 회차에 대해 엑셀/PDF를 둘 다 만들 수 있다."""
    from report.inspection_pdf import build_pdf

    context = build_context(customer_name, profile_name, **kwargs)
    target_dir = reports_dir(customer_name, profile_name)
    name = filename or build_filename(customer_name, profile_name, date=context["inspection_date"])
    if name.lower().endswith(".xlsx"):
        name = name[: -len(".xlsx")]
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    path = target_dir / name

    # 장비목록 페이지: 위치/Warranty 는 장비목록(Device Inventory)의 값을 그대로 쓴다.
    # 예전에는 '위치'에 role 을, Warranty 에 빈 문자열을 넣어 두 열이 항상 공란이었다.
    equipment = [[d["name"], d.get("model", ""), d.get("serial", ""),
                  d.get("location") or d.get("role", ""), d.get("warranty", "")]
                 for d in context["devices"]]

    warn_devices = sum(1 for d in context["devices"] if d.get("warn_count"))
    remark = f"점검 장비 {len(context['devices'])}대"
    if warn_devices:
        remark += f" / 확인필요 {warn_devices}대"
    append_support_history(
        customer_name, profile_name, date=context["inspection_date"],
        note=f"{context['target_month']} 정기점검", remark=remark)
    history = load_support_history(customer_name, profile_name)

    build_pdf(context, path, equipment=equipment, history=history)
    save_snapshot(customer_name, profile_name, context["devices"])
    return {
        "path": str(path), "filename": name, "dir": str(target_dir),
        "device_count": len(context["devices"]),
        "warn_count": sum(d["warn_count"] for d in context["devices"]),
        "na_count": sum(d["na_count"] for d in context["devices"]),
        "unreachable_count": sum(1 for d in context["devices"] if d["unreachable"]),
    }


def list_reports(customer_name: str, profile_name: str) -> list:
    """보고서(.xlsx/.pdf) 목록(최신순) — 프로파일 reports/ + 각 회차 runs/<run_id>/reports/.
    조회만으로 폴더를 만들지 않는다(빈 폴더가 생기면 '보고서 있음'으로 오해된다)."""
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
        for pattern in ("*.xlsx", "*.pdf"):
            for path in directory.glob(pattern):
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
