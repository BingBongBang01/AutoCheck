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

from core.paths import sanitize_component
from engine.profile_manager import profile_manager
from report.inspection_excel import (
    STATUS_NA, STATUS_OK, STATUS_UNREACHABLE, STATUS_WARN,
    build_workbook, evaluate_device, load_template, split_transcript,
)

REPORTS_SUBDIR = "reports"
SNAPSHOT_FILE = "_snapshot.json"
HISTORY_FILE = "_support_history.json"

_LOG_NAME_RE = re.compile(r"^AutoCheck_(?P<device>.+)_(?P<date>\d{8})_(?P<time>\d{6})\.txt$")


class InspectionReportError(Exception):
    """보고서 생성에 필요한 전제(원본로그 등)가 없을 때."""


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
    """레거시 로그가 cp949로 저장된 경우까지 감안해서 읽는다(api.log_file_browser_api와 동일 규칙)."""
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def latest_logs_by_device(customer_name: str, profile_name: str) -> dict:
    """점검 회차 폴더(runs/<run_id>/raw)에서 장비별 최신 로그 1개씩 —
    {장비명: {"path", "collected_at", "text"}}.
    파일명이 AutoCheck_ 규칙을 안 따르면 확장자를 뗀 이름을 장비명으로 쓴다(수동으로 넣은 로그 대응)."""
    latest = {}
    for directory in original_log_dirs(customer_name, profile_name):
        for path in sorted(directory.glob("*.txt")):
            match = _LOG_NAME_RE.match(path.name)
            if match:
                device = match.group("device")
                collected = datetime.datetime.strptime(
                    match.group("date") + match.group("time"), "%Y%m%d%H%M%S")
            else:
                device = path.stem
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


def append_support_history(customer_name: str, profile_name: str, *, date: str, note: str) -> list:
    """PDF 보고서를 생성할 때마다 '이번 회차 정기점검' 한 줄을 지원이력에 추가한다.
    같은 (날짜, 내역) 조합이 이미 있으면 중복 추가하지 않는다(같은 회차로 PDF를 여러 번
    다시 만들어도 이력이 매번 늘어나지 않도록)."""
    path = _history_path(customer_name, profile_name)
    try:
        rows = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except (OSError, json.JSONDecodeError):
        rows = []
    if not any(r.get("date") == date and r.get("note") == note for r in rows):
        rows.append({"date": date, "note": note, "remark": ""})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows


def load_support_history(customer_name: str, profile_name: str) -> list:
    """[순번, 일자, 지원 내역, 비고] 행 목록(오래된 순) — PDF의 05_지원이력 표에 그대로 들어간다."""
    path = _history_path(customer_name, profile_name)
    try:
        rows = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except (OSError, json.JSONDecodeError):
        rows = []
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
    반환: (요약 한 줄 목록 문자열, 원본 출력까지 붙인 상세 문자열)."""
    lines, detail = [], []
    for item in items:
        if item.get("status") != STATUS_WARN:
            continue
        lines.append(f"# {item['name']}: {item.get('value')}")
        detail.append(f"# {item['name']} ({item.get('method')})\n{item.get('detail') or item.get('value')}")
    return "\n".join(lines), "\n\n".join(detail)


def build_context(customer_name: str, profile_name: str, *, project_id=None,
                   inspection_date=None, manager=None, inspector=None, confirmer=None,
                   site_name=None, vendor=None, report_title=None,
                   devices_filter=None, template_path=None) -> dict:
    """보고서 렌더링에 필요한 모든 데이터를 한 dict으로 조립한다(파일은 쓰지 않는다).

    devices_filter: 장비명 목록을 주면 그 장비만 포함한다(부분 보고서용, None이면 전체).
    장비목록에는 있는데 원본로그가 없는 장비는 '접속 불가'로 포함해 빈 시트를 남긴다 —
    보고서에서 아예 빠지면 점검 누락과 구분이 안 되기 때문(LGES 보고서의 '접속 불가' 표기 방식).
    """
    template = load_template(template_path)
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
        record = inventory.get(name, {})
        sections = split_transcript(log["text"]) if log else {}
        items = evaluate_device(sections, template) if sections else []
        previous_values = previous.get(name, {})
        for item in items:
            item["previous"] = previous_values.get(item["name"], "")

        remarks, remarks_detail = _remarks(items)
        warn_count = sum(1 for item in items if item.get("status") == STATUS_WARN)
        if log is None:
            overall = STATUS_UNREACHABLE
        elif warn_count:
            overall = STATUS_WARN
        elif not items:
            overall = STATUS_NA
        else:
            overall = STATUS_OK

        devices.append({
            "name": name,
            "model": record.get("model") or _model(sections),
            "ip": record.get("management_ip", ""),
            "serial": _serial(sections),
            "os_version": _os_version(sections),
            "role": record.get("role") or record.get("zone") or "",
            "memo": record.get("memo", ""),
            "site": record.get("site", ""),
            "unreachable": log is None,
            "collected_at": log["collected_at"].isoformat(timespec="seconds") if log else "",
            "command_count": len(sections),
            "items": items,
            # PDF 보고서(report/inspection_pdf.py)가 서버 CPU/Memory를 Linux 원본 로그에서
            # 직접 재계산할 때 쓴다 — YAML evaluator는 Arista show-명령 전용이라 서버 로그엔
            # 매칭되지 않기 때문에 원본 섹션을 그대로 넘겨준다.
            "sections": sections,
            "warn_count": warn_count,
            "overall_status": overall,
            "remarks": remarks,
            "remarks_detail": remarks_detail,
            "opinion": "" if overall == STATUS_OK else "확인 필요",
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
    workbook = build_workbook(context)
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
        "unreachable_count": sum(1 for d in context["devices"] if d["unreachable"]),
        "sheets": ["표지", "장비현황", "점검요약"] + [d["name"] for d in context["devices"]],
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

    equipment = [[d["name"], d.get("model", ""), d.get("serial", ""), d.get("role", ""), ""]
                 for d in context["devices"]]
    history = append_support_history(
        customer_name, profile_name, date=context["inspection_date"],
        note=f"{context['target_month']} 정기점검")

    build_pdf(context, path, equipment=equipment, history=history)
    save_snapshot(customer_name, profile_name, context["devices"])
    return {
        "path": str(path), "filename": name, "dir": str(target_dir),
        "device_count": len(context["devices"]),
        "warn_count": sum(d["warn_count"] for d in context["devices"]),
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
