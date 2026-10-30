"""
정기점검 보고서 PDF 렌더러 (reportlab).

7개 실제 정기점검 PDF를 분석해 만든 표준 양식(다운로드 폴더의 build_inspection_report.py /
정기점검_보고서_양식.xlsx)을 이 프로젝트의 보고서 파이프라인에 맞게 이식한 모듈이다.

report/inspection_excel.py + engine/inspection_report_builder.py가 만드는 컨텍스트
(engine.inspection_report_builder.build_context()의 반환값)를 입력으로 받아 표준 6섹션
PDF(표지/요약/스위치 상세/서버 상세/장비목록/지원이력)를 만든다. 엑셀 파이프라인(21개 항목,
4-state 상태)을 대체하는 게 아니라 같은 데이터를 표준 스키마(14개 고정 항목, 정상/비정상
2-state)로 다시 그리는 두 번째 출력 포맷이다.

표준과 현재 파이프라인의 차이(그리고 이 모듈이 메우는 간극):
  - 점검항목 21개 중 표준 14개만 본문 표에 쓰고, 나머지는 "추가 점검항목(참고)"로 덧붙인다
    (삭제하면 정보 손실이므로 유지하되 표준 레이아웃을 그대로 지킨다).
  - 4-state(정상/확인필요/미수집/접속 불가) 중 확인필요만 표준의 "비정상"에 대응하므로,
    표에는 상태를 그대로 노출하고(정보 손실 방지) 정상/비정상 집계는 "정상만 정상"으로 센다.
  - 스위치/서버 구분이 없던 걸 role/model/hostname 휴리스틱으로 나눈다(_is_server).
  - 서버는 원본 커맨드가 Linux 계열(vmstat/free)이라 YAML의 Arista show-명령 평가기로는
    값을 못 뽑는다 — 이 모듈이 sections에서 직접 CPU/Memory를 재계산한다.
"""
import datetime
import os
import re
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Group, Line, Path as GraphicPath, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import registerFont, registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

from report.inspection_status import NOT_JUDGED_STATUSES, STATUS_OK, STATUS_WARN

FONT_R = "ReportKR"
FONT_B = "ReportKR-Bold"

def _font_candidates():
    """한글 TTF 후보(정규, 굵게). 예전엔 'C:/Windows/Fonts' 고정이라 Windows 가 다른 드라이브에 있거나
    나눔고딕을 사용자 단위로만 설치한 PC 에서 폰트를 못 찾았다. 앱 폴더의 fonts/ 를 먼저 본다(번들용 자리)."""
    from core.paths import AppPaths
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or "C:/Windows"
    dirs = [str(AppPaths.bundle_root() / "fonts"), os.path.join(windir, "Fonts")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))   # 사용자 단위 설치
    dirs.append("/usr/share/fonts/truetype/nanum")
    pairs = [("malgun.ttf", "malgunbd.ttf"), ("NanumGothic.ttf", "NanumGothicBold.ttf")]
    return [(os.path.join(d, regular), os.path.join(d, bold)) for d in dirs for regular, bold in pairs]


FONT_CANDIDATES = _font_candidates()

GRID = colors.HexColor("#404040")
HEAD_BG = colors.HexColor("#DCE6F1")
TITLE_BG = colors.HexColor("#BDD7EE")

# 표준 14개 항목(성능 7 + 인터페이스/라우팅 7) — yaml check_items의 name과 맞춰 값을 끌어온다.
PERF_STD = [
    ("CPU 사용률", "show processes top once", "CPU 사용률"),
    ("Free Memory", "show version<br/>(Free / Total) * 100", "Free Memory"),
    ("System Uptime", "show version", "System Uptime"),
    ("Power", "show system environment power", "Power 점검"),
    ("Fan", "show system environment cooling", "FAN 상태 확인"),
    ("Temperature", "show system environment temperature", "Temperature 상태 확인"),
    ("Log", "show log", "Log 확인"),
]
INTF_STD = [
    ("Interface", "show interface status", "Interface 상태"),
    ("Err/CRC", "show interface | in CRC<br/>show interface counters errors", "Interface Error"),
    ("Port-channel", "show port-channel summary", "Port-channel"),
    ("VARP", "show ip virtual-router", "VARP/VRRP 상태"),
    ("Mlag", "show mlag detail<br/>show mlag interfaces detail", "MLAG 상태"),
    ("Route table", "show ip route", "Route Table"),
    ("System Time", "show clock", "System Time"),
]
# 판정(정상/비정상) 집계에 들어가는 11개 — cpu/mem/uptime은 실측값이라 집계에서 뺀다(표준 그대로).
JUDGE_LABELS = {label for label, _, _ in PERF_STD + INTF_STD} - {"CPU 사용률", "Free Memory", "System Uptime"}

SERVER_ITEMS = [
    ("CPU", 'echo "CPU Usage: "$[100-$(vmstat 1 2|tail -1|awk \'{print $15}\')]"%"'),
    ("Memory", "free | grep Mem | awk '{print sprintf(\"%.2f%\",$3/$2*100)}' 또는 free -m"),
]

_SERVER_HINTS = re.compile(
    # 'hp'·'dell' 은 낱말 앞에서만 — 예전엔 'php-sw1' 같은 이름의 끝 'hp' 가 걸려 스위치가 서버 표로 갔다.
    r"server|서버|^cvp|\bdell|\bhpe?\b|supermicro|proliant|poweredge|linux|ubuntu|centos|redhat",
    re.IGNORECASE,
)


def register_fonts() -> None:
    for regular, bold in _font_candidates():
        if not Path(regular).exists():
            continue
        try:
            registerFont(TTFont(FONT_R, regular))
            registerFont(TTFont(FONT_B, bold if Path(bold).exists() else regular))
        except Exception:
            continue
        registerFontFamily(FONT_R, normal=FONT_R, bold=FONT_B)
        return
    raise RuntimeError("한글 TTF 폰트(맑은 고딕/나눔고딕)를 찾지 못해 PDF 보고서를 만들 수 없습니다.")


def style(name, size, font=FONT_R, align=TA_CENTER):
    return ParagraphStyle(name, fontName=font, fontSize=size, leading=size * 1.35, alignment=align)


def txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


def ptext(value: Any) -> str:
    """Paragraph 에 넣을 데이터 문자열. ReportLab Paragraph 는 내용을 마크업으로 파싱해서, 'Core<->Agg' 나
    'A&B' 같은 원문(장비명·비고·점검값)을 그대로 넣으면 PDF 생성이 통째로 실패했다. &<> 를 바꾸고
    줄바꿈은 <br/> 로 — 여러 줄 비고가 한 줄로 뭉치지 않게. (고정 문구의 <br/> 마크업은 그대로 둔다.)"""
    return escape(txt(value)).replace("\r\n", "\n").replace("\n", "<br/>")


# --------------------------------------------------------------------------- 장비 분류/항목 매핑

def _is_server(device: dict) -> bool:
    haystack = " ".join(txt(device.get(k)) for k in ("role", "model", "name", "memo"))
    return bool(_SERVER_HINTS.search(haystack))


def _item_by_name(items: list, name: str) -> dict:
    for item in items or []:
        if item.get("name") == name:
            return item
    return {}


def _binary(item: dict) -> str:
    """표준 표의 '결과' 칸 — 정상이면 정상, 그 외(확인필요/미수집/해당없음/접속 불가)는
    있는 그대로 노출. 확인필요만 표준의 '비정상'과 같은 뜻이라 별도 표기하고, 나머지는
    정보 손실을 막기 위해 원래 상태 문구를 그대로 남긴다."""
    status = item.get("status")
    if status == STATUS_OK:
        return "정상"
    if status is None:
        return ""
    if status == STATUS_WARN:
        return "비정상"
    return status


def _device_counts(device: dict, total: int) -> tuple:
    """(정상, 비정상, 미수집) — 표준 14개 항목 기준 요약표 숫자.

    미수집(미수집/해당없음/접속 불가)을 따로 세는 이유: 예전에는 passed = total - fail 이라
    판정하지 못한 항목이 전부 '정상'으로 합산됐다. 가상 장비처럼 절반이 미수집인 경우
    보고서만 보면 전부 정상 점검된 것처럼 보인다.

    실측 항목(CPU/Memory/Uptime)도 값을 못 뽑았으면 미수집으로 센다 — 판정 대상이 아니라는
    것과 값을 못 봤다는 것은 다르다."""
    rows = _switch_rows(device)
    fail = na = 0
    for label, _cmd, _yaml in PERF_STD + INTF_STD:
        value = rows.get(label)
        if value == "비정상":
            fail += 1
        elif value in NOT_JUDGED_STATUSES:
            na += 1
    return total - fail - na, fail, na


def _switch_rows(device: dict) -> dict:
    """표준 14개 항목의 표시값 — {표준 라벨: 결과 문자열}.

    실측 항목(CPU/Free Memory/System Uptime)만 측정값을 그대로 쓰고, 나머지는 판정 문구
    (정상/비정상/미수집/해당없음)로 적는다. 예전에는 PERF_STD 전체가 값을 그대로 썼는데,
    Power/Fan/Temperature/Log 의 값은 "해당없음 (이 하드웨어 플랫폼이 …)" 같은 문장이라
    좁은 결과 칸을 넘쳤고, 무엇보다 요약표 집계가 그 문자열을 '비정상'과도 '미수집'과도
    맞추지 못해 판정하지 못한 항목이 조용히 정상으로 세어졌다."""
    items = device.get("items", [])
    row = {}
    for label, _cmd, yaml_name in PERF_STD + INTF_STD:
        item = _item_by_name(items, yaml_name)
        if not item:
            row[label] = ""
        elif label in JUDGE_LABELS or item.get("status") in NOT_JUDGED_STATUSES:
            # 실측 항목(CPU 등)이라도 값을 못 뽑았으면 사유 문장 대신 상태만 적는다 —
            # 그래야 아래 집계가 '판정하지 못함'으로 알아본다.
            row[label] = _binary(item)
        else:
            row[label] = txt(item.get("value"))
    return row


def _extra_rows(device: dict) -> list:
    """표준 14개에 없는 항목(현재 템플릿엔 21개가 있음) — 참고용으로 별도 표에 남긴다."""
    std_names = {yaml_name for _, _, yaml_name in PERF_STD + INTF_STD}
    return [item for item in device.get("items", []) if item.get("name") not in std_names]


_VMSTAT_IDLE_RE = re.compile(r"vmstat[\s\S]{0,400}")
_FREE_MEM_RE = re.compile(r"Mem:?\s+(\d+)\s+(\d+)\s+(\d+)")


def _server_metric(sections: dict, kind: str) -> str:
    """서버 CPU/Memory — Linux 계열 원본 로그(vmstat/free)에서 직접 계산한다.
    YAML 템플릿의 evaluator는 Arista show-명령 전용이라 서버 로그엔 매칭되지 않기 때문."""
    text = ""
    for cmd, output in (sections or {}).items():
        low = cmd.lower()
        if kind == "cpu" and ("vmstat" in low or "cpu usage" in low):
            text = output or ""
            break
        if kind == "mem" and ("free" in low and "grep" not in low or low.strip() == "free -m"):
            text = output or ""
            break
        if kind == "mem" and "free" in low:
            text = output or ""

    if kind == "cpu":
        match = re.search(r"CPU Usage:\s*([\d.]+)\s*%", text)
        if match:
            return f"{match.group(1)} %"
        rows = [l for l in text.splitlines() if l.strip() and not l.strip().lower().startswith("procs")]
        for line in rows:
            parts = line.split()
            if len(parts) >= 15 and parts[0].isdigit():
                try:
                    idle = float(parts[14])
                    return f"{round(100 - idle, 2)} %"
                except (ValueError, IndexError):
                    continue
        return ""
    match = _FREE_MEM_RE.search(text)
    if match:
        total, used = float(match.group(1)), float(match.group(2))
        if total:
            return f"{round(used / total * 100, 2)} %"
    return ""


# --------------------------------------------------------------------------- 스타일/공통 테이블

def base_table(data, widths) -> Table:
    t = Table(data, colWidths=widths, hAlign="CENTER")
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


# --------------------------------------------------------------------------- 페이지 빌더

def cover_page(ctx: dict) -> list:
    meta = ctx["template"]["meta"]
    site = ctx.get("site_name") or ctx["customer"]
    title = ctx.get("report_title") or meta.get("report_title", "네트워크 스위치 정기점검")
    vendor = ctx.get("vendor") or meta.get("vendor", "")
    head = f"{ptext(site)} {ptext(title)}"
    if vendor:
        head = f"{head}<br/>({ptext(vendor)})"
    box = Table([[Paragraph(head, style("cover", 20, FONT_B))]], colWidths=[150 * mm], rowHeights=[40 * mm])
    box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.2, GRID),
        ("BACKGROUND", (0, 0), (-1, -1), TITLE_BG),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    inspector = ctx["inspector"]
    confirmer = ctx.get("confirmer") or {}
    info = [
        [Paragraph("점검자", style("k", 9, FONT_B)), Paragraph(ptext(inspector.get("company")), style("v", 9)),
         Paragraph(ptext(inspector.get("name")), style("v", 9)), Paragraph("(서명)", style("v", 9))],
        ["", Paragraph("연락처", style("k", 9, FONT_B)), Paragraph(ptext(inspector.get("contact")), style("v", 9)), ""],
        [Paragraph("확인자", style("k", 9, FONT_B)), Paragraph(ptext(confirmer.get("company")), style("v", 9)),
         Paragraph(ptext(confirmer.get("name")), style("v", 9)), Paragraph("(서명)", style("v", 9))],
        [Paragraph("점검일자", style("k", 9, FONT_B)), Paragraph(ptext(ctx["inspection_date"]), style("v", 9)), "", ""],
    ]
    t = base_table(info, [30 * mm, 45 * mm, 45 * mm, 25 * mm])
    t.setStyle(TableStyle([
        ("SPAN", (0, 0), (0, 1)), ("SPAN", (3, 0), (3, 1)), ("SPAN", (1, 3), (3, 3)),
        ("BACKGROUND", (0, 0), (0, -1), HEAD_BG),
        ("ROWBACKGROUNDS", (1, 1), (1, 1), [HEAD_BG]),
    ]))
    month_text = ctx.get("target_month", "")
    return [
        Spacer(1, 45 * mm), box, Spacer(1, 22 * mm),
        Paragraph(month_text, style("ym", 14, FONT_B)),
        Spacer(1, 45 * mm), t, PageBreak(),
    ]


def summary_page(ctx: dict, switches: list, servers: list) -> list:
    # 열이 하나 늘었다: 예전 헤더는 (점검항목/정상/비정상) 3개였는데 실제로는 숫자가
    # (14 12 2)처럼 세 칸에 걸쳐 찍혀서, 세 번째 숫자가 '비정상'인지 '미수집'인지 표만 보고는
    # 알 수 없었다. 판정하지 못한 항목을 자기 열로 분리한다.
    head = [Paragraph(h, style("h", 8.5, FONT_B)) for h in
            ("Page", "HOSTNAME", "점검<br/>항목", "정상", "비정상", "미수집<br/>해당없음", "요 약")]
    data = [head]
    page = 3
    for device in switches + servers:
        total = 14 if device in switches else 2
        if device in switches:
            passed, fail, na = _device_counts(device, total)
        else:
            passed, fail, na = total, 0, 0
        data.append([
            Paragraph(str(page), style("c", 8.5)), Paragraph(ptext(device["name"]), style("c", 8.5)),
            Paragraph(str(total), style("c", 8.5)), Paragraph(str(passed), style("c", 8.5)),
            Paragraph(str(fail) if fail else "", style("c", 8.5)),
            Paragraph(str(na) if na else "", style("c", 8.5)),
            Paragraph(ptext(device.get("remarks")), style("c", 8.5, align=TA_LEFT)),
        ])
        page += 1
    data.append([Paragraph("비 고", style("h", 8.5, FONT_B)), "", "", "", "", "", ""])
    t = base_table(data, [12 * mm, 38 * mm, 13 * mm, 13 * mm, 13 * mm, 16 * mm, 65 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("SPAN", (0, len(data) - 1), (0, len(data) - 1)),
        ("SPAN", (1, len(data) - 1), (6, len(data) - 1)),
        ("BACKGROUND", (0, len(data) - 1), (0, len(data) - 1), HEAD_BG),
    ]))
    site = ctx.get("site_name") or ctx["customer"]
    return [Paragraph(f"{ptext(site)} 네트워크 점검결과 요약", style("t", 14, FONT_B)),
            Spacer(1, 8 * mm), t, PageBreak()]


def _std_check_table(rows: list) -> Table:
    data = [[Paragraph(h, style("h", 8.5, FONT_B)) for h in ("점검 항목", "Command", "결 과")]]
    for label, command, value in rows:
        data.append([Paragraph(label, style("c", 8.5)), Paragraph(command, style("c", 8)),
                     Paragraph(ptext(value), style("c", 8.5))])
    t = base_table(data, [35 * mm, 85 * mm, 50 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), HEAD_BG)]))
    return t


def _extra_check_table(items: list) -> list:
    """표준 14개 밖의 추가 점검항목(참고) — 현재 템플릿이 표준보다 넓어서(21개) 잃지 않고 덧붙인다."""
    if not items:
        return []
    data = [[Paragraph(h, style("h", 8.5, FONT_B)) for h in ("구분", "점검 항목", "결 과")]]
    for item in items:
        data.append([Paragraph(ptext(item.get("group")), style("c", 8)),
                     Paragraph(ptext(item.get("name")), style("c", 8.5)),
                     Paragraph(ptext(_binary(item) or item.get("value")), style("c", 8.5))])
    t = base_table(data, [30 * mm, 90 * mm, 50 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), HEAD_BG)]))
    return [Spacer(1, 5 * mm), Paragraph("4. 추가 점검항목 (참고)", style("s", 10, FONT_B, TA_LEFT)),
            Spacer(1, 2 * mm), t]


def _system_table(device: dict, ctx: dict, server: bool) -> Table:
    site = ctx.get("site_name") or ctx["customer"]
    if server:
        pairs = [
            ("사이트", site), ("IP", txt(device.get("ip"))),
            ("모델명", txt(device.get("model"))), ("점검 날짜", ctx["inspection_date"]),
            ("Hostname", txt(device["name"])), ("Software Version", txt(device.get("os_version"))),
            ("점검 업체", ctx["inspector"].get("company", "")), ("점검 자", ctx["inspector"].get("name", "")),
        ]
    else:
        pairs = [
            ("사이트", site), ("장비 위치", txt(device.get("location") or device.get("role"))),
            ("모델명", txt(device.get("model"))), ("점검 날짜", ctx["inspection_date"]),
            ("Hostname", txt(device["name"])), ("Software Version", txt(device.get("os_version"))),
            ("Serial Number", txt(device.get("serial"))), ("IP", txt(device.get("ip"))),
            ("점검 업체", ctx["inspector"].get("company", "")), ("점검 자", ctx["inspector"].get("name", "")),
        ]
    data = []
    for i in range(0, len(pairs), 2):
        left, right = pairs[i], pairs[i + 1]
        data.append([Paragraph(left[0], style("k", 8.5, FONT_B)), Paragraph(ptext(left[1]), style("v", 8.5)),
                     Paragraph(right[0], style("k", 8.5, FONT_B)), Paragraph(ptext(right[1]), style("v", 8.5))])
    t = base_table(data, [35 * mm, 50 * mm, 35 * mm, 50 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), HEAD_BG), ("BACKGROUND", (2, 0), (2, -1), HEAD_BG)]))
    return t


def _note_table(text: str) -> Table:
    t = base_table([[Paragraph("특이 사항 및 진단 평가", style("k", 8.5, FONT_B)),
                     Paragraph(ptext(text), style("v", 8.5, align=TA_LEFT))]], [35 * mm, 135 * mm])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), HEAD_BG),
                           ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    return t


def device_page(device: dict, ctx: dict, index: int, server: bool) -> list:
    site = ctx.get("site_name") or ctx["customer"]
    month_text = ctx.get("target_month_short", "")
    tag = "#CVP" if server else f"#{index}"
    story = [
        Paragraph(f"{ptext(site)} ( {ptext(month_text)} )월 정기 점검표{tag}", style("t", 13, FONT_B)),
        Spacer(1, 5 * mm), Paragraph("1. 시스템 확인", style("s", 10, FONT_B, TA_LEFT)),
        Spacer(1, 2 * mm), _system_table(device, ctx, server), Spacer(1, 5 * mm),
    ]
    if server:
        sections = device.get("sections") or {}
        rows = [(label, cmd, _server_metric(sections, "cpu" if label == "CPU" else "mem"))
                for label, cmd in SERVER_ITEMS]
        story += [Paragraph("2. CPU, Memory 상태 점검", style("s", 10, FONT_B, TA_LEFT)),
                  Spacer(1, 2 * mm), _std_check_table(rows)]
    else:
        rows = _switch_rows(device)
        perf_rows = [(label, cmd, rows.get(label, "")) for label, cmd, _ in PERF_STD]
        intf_rows = [(label, cmd, rows.get(label, "")) for label, cmd, _ in INTF_STD]
        story += [
            Paragraph("2. Performance 및 Log 점검", style("s", 10, FONT_B, TA_LEFT)),
            Spacer(1, 2 * mm), _std_check_table(perf_rows), Spacer(1, 5 * mm),
            Paragraph("3. Interface 및 Routing 상태 점검", style("s", 10, FONT_B, TA_LEFT)),
            Spacer(1, 2 * mm), _std_check_table(intf_rows),
        ]
        story += _extra_check_table(_extra_rows(device))
    story += [Spacer(1, 4 * mm), _note_table(device.get("remarks_detail") or device.get("remarks")),
              PageBreak()]
    return story


def _list_page(title, headers, widths, rows, align_left) -> list:
    data = [[Paragraph(h, style("h", 8.5, FONT_B)) for h in headers]]
    for row in rows:
        data.append([Paragraph(ptext(v), style("c", 8.5, align=TA_LEFT if i in align_left else TA_CENTER))
                     for i, v in enumerate(row)])
    t = base_table(data, widths)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), HEAD_BG)]))
    return [Paragraph(title, style("t", 13, FONT_B)), Spacer(1, 6 * mm), t, PageBreak()]


# --------------------------------------------------------------------------- 부록: 네트워크 구성도
# IMPROVEMENT_PLAN 4-7. engine/topology_svg.figure() 가 구성도 탭의 SVG 와 같은 배치·선 모양을 평범한 값으로 넘겨준다
# (이 층은 engine 을 가져올 수 없다). 장비 기호는 상자 + 종류 글자로 그린다 — 화면 기호와 같은 뜻이고 흑백 인쇄에서도
# 읽힌다. 색으로만 나누지 않는다: DOWN 은 ✕, 사라진 링크는 점선과 '사라짐', 새 링크는 후광.
_KIND_MARK = {"l3switch": "L3", "l2switch": "L2", "router": "R", "firewall": "FW"}
_HALO = colors.HexColor("#BFDBFE")


def _hex(value):
    return colors.HexColor(value) if str(value).startswith("#") else colors.HexColor("#94A3B8")


def _edge_path(edge, flip, color, width, dash=None):
    path = GraphicPath(strokeColor=color, strokeWidth=width, fillColor=None, strokeDashArray=dash, strokeLineCap=1)
    x1, y1, x2, y2 = edge["x1"], flip(edge["y1"]), edge["x2"], flip(edge["y2"])
    path.moveTo(x1, y1)
    if edge.get("cx") is None:
        path.lineTo(x2, y2)
    else:   # SVG 의 2차 베지어(Q)를 같은 모양의 3차(C)로
        cx, cy = edge["cx"], flip(edge["cy"])
        path.curveTo(x1 + (cx - x1) * 2 / 3, y1 + (cy - y1) * 2 / 3, x2 + (cx - x2) * 2 / 3, y2 + (cy - y2) * 2 / 3, x2, y2)
    return path


def _edge_mid(edge, flip):
    if edge.get("cx") is None:
        return (edge["x1"] + edge["x2"]) / 2, flip((edge["y1"] + edge["y2"]) / 2)
    return ((edge["x1"] + 2 * edge["cx"] + edge["x2"]) / 4, flip((edge["y1"] + 2 * edge["cy"] + edge["y2"]) / 4))


def topology_drawing(figure: dict, max_w: float, max_h: float) -> Drawing:
    """figure(engine/topology_svg.figure) -> 쪽에 맞춰 줄인 reportlab Drawing. 좌표는 SVG(아래로 커짐)를 뒤집는다."""
    p = figure["palette"]
    width, height, icon = figure["width"], figure["height"], figure["icon"]
    scale = min(max_w / width, max_h / height, 1.0)

    def flip(y):
        return height - y

    group = Group()
    for row in figure["rows"]:
        y = flip(row["y"] - 14)
        group.add(Line(16, y, width - 16, y, strokeColor=colors.HexColor("#CBD5E1"), strokeWidth=0.8,
                       strokeDashArray=[2, 6]))
        group.add(String(20, y + 6, row["label"], fontName=FONT_B, fontSize=11, fillColor=_hex(p["sub"])))
    for pair in figure["pairs"]:
        group.add(Rect(pair["left"], flip(pair["bottom"]), pair["right"] - pair["left"], pair["bottom"] - pair["top"],
                       rx=8, ry=8, fillColor=None, strokeWidth=1.2, strokeDashArray=[5, 4],
                       strokeColor=_hex(p["sub"] if pair["healthy"] else p["down"])))
        group.add(String(pair["left"] + 6, flip(pair["top"] - 4), pair["label"], fontName=FONT_B, fontSize=9,
                         fillColor=_hex(p["sub"])))
    for edge in figure["edges"]:
        state = edge["state"]
        width_ = 3.4 if edge["bundle"] else 1.6
        if edge["change"] in ("new", "partly_new"):
            group.add(_edge_path(edge, flip, _HALO, width_ + 7))
        color = _hex(p.get("down" if state == "lost" else state, p["unknown"]))
        dash = [3, 5] if state == "lost" else ([7, 4] if edge["one_sided"] else None)
        group.add(_edge_path(edge, flip, color, width_, dash))
        mx, my = _edge_mid(edge, flip)
        if state == "down":
            group.add(Line(mx - 5, my - 5, mx + 5, my + 5, strokeColor=_hex(p["down"]), strokeWidth=2))
            group.add(Line(mx - 5, my + 5, mx + 5, my - 5, strokeColor=_hex(p["down"]), strokeWidth=2))
        elif state == "lost":   # 선 위에 얹히는 글자 — 흰 바탕을 깔아 읽히게
            group.add(Rect(mx - 15, my - 6, 30, 12, fillColor=colors.white, strokeColor=None))
            group.add(String(mx, my - 3, "사라짐", fontName=FONT_B, fontSize=9, textAnchor="middle",
                             fillColor=_hex(p["down"])))
    for node in figure["nodes"]:
        x, top = node["x"], node["y"]
        group.add(Rect(x - icon / 2, flip(top + icon), icon, icon, rx=6, ry=6, fillColor=colors.white,
                       strokeColor=_hex(p["text"] if node["registered"] else p["sub"]), strokeWidth=1.2,
                       strokeDashArray=None if node["registered"] else [4, 3]))
        mark = _KIND_MARK.get(node["kind"], "?")
        group.add(String(x, flip(top + icon / 2 + 5), mark, fontName=FONT_B, fontSize=13, textAnchor="middle",
                         fillColor=_hex(p["text"] if node["registered"] else p["sub"])))
        group.add(String(x, flip(top + icon + 14), node["name"], fontName=FONT_B, fontSize=12, textAnchor="middle",
                         fillColor=_hex(p["text"])))
        label = node["ip"] or ("" if node["registered"] else "미등록")
        if label:
            group.add(String(x, flip(top + icon + 27), label, fontName=FONT_R, fontSize=10, textAnchor="middle",
                             fillColor=_hex(p["sub"])))
    group.transform = (scale, 0, 0, scale, 0, 0)
    drawing = Drawing(width * scale, height * scale)
    drawing.add(group)
    return drawing


def _topology_legend(figure: dict, changes: bool, width: float) -> Drawing:
    p = figure["palette"]
    rows = [("링크 정상", p["up"], 1.6, None, False), ("Port-Channel 묶음", p["up"], 3.4, None, False),
            ("DOWN(✕)", p["down"], 1.6, None, False), ("이중화 저하", p["degraded"], 3.4, None, False),
            ("판정 불가", p["unknown"], 1.6, None, False), ("한쪽만 관측", p["up"], 1.6, [7, 4], False)]
    if changes:
        rows += [("새 링크(앞 회차 대비)", p["up"], 1.6, None, True), ("사라진 링크", p["down"], 1.6, [3, 5], False)]
    per_row, cell_h = 4, 12
    drawing = Drawing(width, cell_h * ((len(rows) + per_row - 1) // per_row) + 2)
    cell_w = width / per_row
    for index, (label, color, line_w, dash, halo) in enumerate(rows):
        col, row = index % per_row, index // per_row
        x, y = col * cell_w + 4, drawing.height - (row + 1) * cell_h + 3
        if halo:
            drawing.add(Line(x, y + 3, x + 24, y + 3, strokeColor=_HALO, strokeWidth=7))
        drawing.add(Line(x, y + 3, x + 24, y + 3, strokeColor=_hex(color), strokeWidth=line_w, strokeDashArray=dash))
        drawing.add(String(x + 29, y, label, fontName=FONT_R, fontSize=7.5, fillColor=_hex(p["sub"])))
    return drawing


def topology_page(appendix: dict, site: str) -> list:
    """부록 — 네트워크 구성도(구성도 탭과 같은 배치) + 앞 회차 대비 링크 변화."""
    width = A4[0] - 40 * mm
    changes = appendix.get("changes")
    story = [Paragraph(f"부록. {site} 네트워크 구성도", style("t", 13, FONT_B)), Spacer(1, 3 * mm),
             Paragraph("점검 로그(LLDP·CDP 이웃, 포트 상태, 이중화 설정)로 복원한 물리 구성입니다. 링크 색은 점검 시점의 "
                       "포트 상태입니다.", style("s", 8.5, align=TA_LEFT)), Spacer(1, 3 * mm),
             topology_drawing(appendix["figure"], width, 165 * mm), Spacer(1, 2 * mm),
             _topology_legend(appendix["figure"], bool(changes), width)]
    if changes:
        text = (f"앞 회차({ptext(changes['base'])}) 대비 — 새 링크 {changes['new']}개 · 사라진 링크 {changes['lost']}개"
                + (f" · 양쪽 모두 이웃 정보가 없어 확인하지 못한 링크 {changes['unverified']}개" if changes["unverified"] else ""))
        story += [Spacer(1, 5 * mm), Paragraph(text, style("s", 9, FONT_B, align=TA_LEFT))]
        if changes["rows"]:
            data = [[Paragraph(h, style("h", 8.5, FONT_B)) for h in ("구분", "한쪽 끝", "다른 끝")]]
            data += [[Paragraph(ptext(v), style("c", 8.5)) for v in row] for row in changes["rows"]]
            table = base_table(data, [30 * mm, 70 * mm, 70 * mm])
            table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), HEAD_BG)]))
            story += [Spacer(1, 2 * mm), table]
    story.append(PageBreak())
    return story


# --------------------------------------------------------------------------- 부록: 실시간 감시 이력
# IMPROVEMENT_PLAN 4-8. engine/realtime_audit.audit() 가 프로파일의 실시간 감시 저장본에서 만든 행을 그대로 그린다 —
# 언제 무엇이 났고 어떻게 닫혔는지(자동 복구·수동 해결·무시·미해결). 비밀값은 그쪽에서 이미 가렸다.
_AUDIT_RED = colors.HexColor("#C00000")


def _audit_summary(audit: dict) -> str:
    counts = audit["counts"]
    parts = [f"경고 {counts['total']}건(심각 {counts['CRITICAL']} · 주요 {counts['MAJOR']} · 경고 {counts['WARNING']})"]
    if counts["total"]:
        parts.append(f"자동 복구 {counts['auto']} · 수동 해결 {counts['manual']} · 무시 {counts['ignored']} · "
                     f"미해결 {counts['open']}")
    if audit.get("period"):
        parts.append(f"기록 {audit['period'][0]} ~ {audit['period'][1]}")
    parts.append(f"감시 장비 {audit['watched']}대(syslog 를 받은 장비 {audit['syslog_devices']}대)")
    if audit.get("excluded"):
        parts.append(f"보고서에 넣지 않은 장비의 경고 {audit['excluded']}건 제외")
    return " / ".join(parts)


def audit_page(audit: dict, site: str) -> list:
    """부록 — 실시간 감시 이력(감사 기록): 요약, 작성 시점에 열려 있던 장애, 경고마다의 처리."""
    story = [Paragraph(f"부록. {site} 실시간 감시 이력", style("t", 13, FONT_B)), Spacer(1, 3 * mm),
             Paragraph("점검 중 실시간 감시(세션 로그·상태 폴링)가 남긴 경고와 그 처리입니다. 자동 복구는 장비의 복구 "
                       "기록(no shutdown, 링크 up 등)으로 해제된 것이고, 수동 해결·무시는 작업자가 처리한 것입니다.",
                       style("s", 8.5, align=TA_LEFT)), Spacer(1, 2 * mm),
             Paragraph(ptext(_audit_summary(audit)), style("s", 8.5, FONT_B, align=TA_LEFT))]
    if audit.get("open_faults"):
        faults = ", ".join(f"{f['device']} {f['component_id']} {f['label']}({f['since']}부터)"
                           for f in audit["open_faults"])
        story += [Spacer(1, 2 * mm),
                  Paragraph(f"<font color='#C00000'>작성 시점에 열려 있던 장애</font>: {ptext(faults)}",
                            style("s", 8.5, align=TA_LEFT))]
    rows = audit["rows"]
    if rows:
        cell = style("c", 7.5)
        left = style("l", 7.5, align=TA_LEFT)
        small = "<font size='6.5' color='#666666'>"
        data = [[Paragraph(h, style("h", 8, FONT_B)) for h in ("발생", "장비", "등급", "내용", "처리", "처리 시각")]]
        for row in rows:
            content = ptext(row["message"]) + (f"<br/>{small}근거: {ptext(row['raw'])}</font>" if row["raw"] else "")
            if row["earlier"]:
                content += f"<br/>{small}감시 시작 전 기록</font>"
            outcome = ptext(row["outcome_label"])
            if row["duration_sec"] is not None:
                outcome += f" ({ptext(_duration_text(row['duration_sec']))})"
            if row["note"]:
                outcome += f"<br/>{small}{ptext(row['note'])}</font>"
            if row["outcome"] == "open":
                outcome = f"<font color='#C00000'><b>{outcome}</b></font>"
            data.append([Paragraph(ptext(row["at"]), cell), Paragraph(ptext(row["device"]), cell),
                         Paragraph(ptext(row["severity_label"]), cell), Paragraph(content, left),
                         Paragraph(outcome, left), Paragraph(ptext(row["closed_at"]), cell)])
        table = base_table(data, [21 * mm, 22 * mm, 11 * mm, 66 * mm, 31 * mm, 19 * mm])
        table.repeatRows = 1
        commands = [("BACKGROUND", (0, 0), (-1, 0), HEAD_BG)]
        commands += [("TEXTCOLOR", (2, i), (2, i), _AUDIT_RED)
                     for i, row in enumerate(rows, start=1) if row["severity"] == "CRITICAL"]
        table.setStyle(TableStyle(commands))
        story += [Spacer(1, 3 * mm), table]
    else:
        story += [Spacer(1, 3 * mm), Paragraph("감시하는 동안 경고가 없었습니다.", style("s", 9, align=TA_LEFT))]
    story.append(PageBreak())
    return story


def _duration_text(seconds) -> str:
    try:
        seconds = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ""
    if seconds < 60:
        return f"{seconds}초"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}분 {seconds}초" if seconds else f"{minutes}분"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}시간 {minutes}분"


# --------------------------------------------------------------------------- 엔트리포인트

def build_pdf(ctx: dict, pdf_path, *, equipment: list = None, history: list = None, topology: dict = None,
              audit: dict = None) -> None:
    """ctx(engine.inspection_report_builder.build_context()의 반환값 + 표지/장비목록/지원이력
    보강 필드)로 표준 6섹션 PDF를 만들어 pdf_path에 저장한다. topology(engine/inspection_report_builder
    ._topology_appendix)가 있으면 부록으로 네트워크 구성도를, audit(._audit_appendix)가 있으면 실시간 감시 이력을
    붙인다."""
    register_fonts()
    devices = ctx["devices"]
    switches = [d for d in devices if not _is_server(d)]
    servers = [d for d in devices if _is_server(d)]

    site = ctx.get("site_name") or ctx["customer"]
    story = []
    story += cover_page(ctx)
    story += summary_page(ctx, switches, servers)
    for i, device in enumerate(switches, start=1):
        story += device_page(device, ctx, i, server=False)
    for device in servers:
        story += device_page(device, ctx, 0, server=True)

    equipment = equipment if equipment is not None else [
        [d["name"], d.get("model", ""), d.get("serial", ""),
         d.get("location") or d.get("role", ""), d.get("warranty", "")] for d in devices
    ]
    if equipment:
        story += _list_page(f"{site} 네트워크 장비 목록",
                            ["HOSTNAME", "Product", "Serial", "위치", "Warranty"],
                            [42 * mm, 45 * mm, 33 * mm, 30 * mm, 20 * mm], equipment, set())
    if history:
        story += _list_page(f"{site} 지원 목록", ["순번", "일자", "지원 내역", "비고"],
                            [15 * mm, 28 * mm, 97 * mm, 30 * mm], history, {2})
    if topology:
        story += topology_page(topology, site)
    if audit:
        story += audit_page(audit, site)
    if story and isinstance(story[-1], PageBreak):
        story.pop()

    doc = BaseDocTemplate(str(pdf_path), pagesize=A4,
                          leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                          title=f"{site} 정기점검 보고서", author=ctx["inspector"].get("company", ""))
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont(FONT_R, 8)
        canvas.drawCentredString(A4[0] / 2, 10 * mm, str(canvas.getPageNumber()))
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=footer)])
    doc.build(story)


def month_strings(inspection_date: str) -> tuple:
    """'2026-07-28' -> ('2026년 7월', '7') — 표지/장비 페이지 제목에 쓰는 두 가지 표기."""
    try:
        d = datetime.date.fromisoformat(inspection_date)
        return f"{d.year}년 {d.month}월", str(d.month)
    except (ValueError, TypeError):
        return "", ""
