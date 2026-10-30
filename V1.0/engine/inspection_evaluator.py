"""
정기점검 보고서의 항목별 판정 — 원본 로그를 커맨드별로 자르고(split_transcript), 양식
(config/inspection_report_template.yaml)의 점검 항목마다 값을 뽑아 정상/확인필요/미수집/해당없음을
정한다(evaluate_device). 엑셀·PDF 렌더러(report/)는 그 결과를 그리기만 한다.

예전엔 report/inspection_excel.py 안에 있었다. 판정은 엑셀과 무관한 비즈니스 로직인데 렌더러에 있어서
report 층이 engine(규칙 판정 engine.log_analysis)을 거꾸로 불렀고, 판정만 쓰는 구성도(api/topology_api)
까지 openpyxl 을 끌어왔다(IMPROVEMENT_PLAN 3-6).

점검 항목의 목록·순서·기준값은 양식이 정의하고(코드 수정 없이 양식 변경 가능), 각 항목의 "값을 어떻게
뽑을지"는 EVALUATORS 가 담당한다.

원본로그는 두 가지 포맷을 모두 받는다:
  1) engine/collector.py가 쓴 "--- {cmd} ---" 섹션 포맷
  2) 세션 터미널이 그대로 저장한 프롬프트 트랜스크립트("Core1(config)#show version" 형태)
     — split_transcript()가 프롬프트 줄을 커맨드 경계로 삼아 잘라낸다.
"""
import re
from pathlib import Path

import yaml

from core.app_config import load_layered_yaml
from engine import vendor_commands
from report.inspection_status import STATUS_NA, STATUS_OK, STATUS_SKIP, STATUS_WARN

__all__ = ["load_template", "split_transcript", "evaluate_device", "EVALUATORS",
           "apply_customer_layer", "customer_template_status", "create_customer_template"]

_TEMPLATE_PATH = "inspection_report_template.yaml"

# 세션 터미널 트랜스크립트의 커맨드 에코 줄: "Core1#show version", "Core1(config)#show mlag".
# 프롬프트 이름은 영문/숫자/점/하이픈/밑줄만 허용해 syslog 줄이 잘못 걸리는 걸 막고,
# (config)·(config-if-Et1) 같은 모드 표시는 선택적으로 흡수한다.
_PROMPT_LINE_RE = re.compile(r"^([\w][\w.\-]{0,60})(?:\([^)\r\n]{0,40}\))?\s*[#>]\s*(.*)$")
# "--- show version ---" 커맨드 헤더. 줄 단위로 매칭하므로 CRLF/LF를 가리지 않는다.
_SECTION_HEADER_RE = re.compile(r"^---\s+(.+?)\s+---\s*$")



# --------------------------------------------------------------------------- 템플릿

# 고객사 템플릿(IMPROVEMENT_PLAN 4-10) — 앱 템플릿 위에 얹는 고객사별 차이. 고객사 데이터 폴더에 둔다.
CUSTOMER_TEMPLATE = "report_template.yaml"
_CUSTOMER_TEMPLATE_STARTER = """\
# {customer} 보고서 템플릿 — 앱 기본 템플릿(config/inspection_report_template.yaml) 위에 얹힌다.
# 적은 것만 바뀐다. 이 파일을 지우면 기본 템플릿으로 돌아간다.
#
# meta: 표지·서명란 값(적은 키만 바뀐다)
#   report_title: 네트워크 스위치 정기점검
#   site_name: 본사 IDC
#   manager: {{company: {customer}, name: 담당자}}
#
# check_items: 이름이 같은 기본 항목은 적은 필드만 고치고, 이름이 새로우면 뒤에 더한다.
#   vendors: [arista, cisco] 처럼 적으면 그 벤더 장비에서만 점검하고 다른 장비에서는 '해당없음'이 된다
#   (arista / cisco(전체) / cisco_ios / cisco_xe / cisco_nxos).
#   - name: CPU Utilization
#     criteria: 70% 미만
#   - name: NTP 동기화
#     group: 기타
#     commands: [show ntp status]
#     criteria: synchronised
#     evaluator: keyword_ok
#     vendors: [arista]
#
# remove_items: 기본 항목 중 이 고객사 보고서에서 뺄 항목 이름
#   - Transceiver
meta: {{}}
check_items: []
remove_items: []
"""


def customer_template_path(customer):
    from engine.profile_manager import profile_manager
    return profile_manager.customer_dir(customer) / CUSTOMER_TEMPLATE


def _read_customer_layer(customer):
    """고객사 템플릿 dict. 없거나 읽을 수 없으면 {} — 보고서는 기본 템플릿으로 만든다(화면이 이유를 보인다)."""
    try:
        path = customer_template_path(customer)
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def apply_customer_layer(base, layer):
    """고객사 템플릿을 앱 템플릿 위에 얹은 새 dict — meta 는 적은 키만, check_items 는 이름이 같은 항목을 적은 필드만
    고치고(자리는 그대로) 새 이름은 뒤에 더한다, remove_items 의 이름은 뺀다. 번호(no)는 다시 매긴다."""
    from core.app_config import merge_layers
    out = dict(base)
    if isinstance(layer.get("meta"), dict):
        out["meta"] = merge_layers(base.get("meta") or {}, layer["meta"])
    items = [dict(item) for item in base.get("check_items") or [] if isinstance(item, dict)]
    position = {item.get("name"): i for i, item in enumerate(items)}
    for item in layer.get("check_items") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        if item["name"] in position:
            items[position[item["name"]]] = merge_layers(items[position[item["name"]]], item)
        else:
            position[item["name"]] = len(items)
            items.append(dict(item))
    removed = {str(name) for name in layer.get("remove_items") or []}
    kept = [item for item in items if item.get("name") not in removed]
    for i, item in enumerate(kept, start=1):
        item["no"] = i
    out["check_items"] = kept
    return out


def customer_template_status(customer):
    """화면용 — {path, exists, readable, error, changed, added, removed, unknown_removed, vendor_items}."""
    path = customer_template_path(customer)
    status = {"path": str(path), "exists": path.is_file(), "readable": True, "error": None,
              "changed": [], "added": [], "removed": [], "unknown_removed": [], "vendor_items": []}
    if not status["exists"]:
        return status
    try:
        with path.open(encoding="utf-8") as f:
            layer = yaml.safe_load(f) or {}
        if not isinstance(layer, dict):
            raise ValueError("최상위가 { ... } 형태가 아닙니다")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        status.update(readable=False, error=f"{type(exc).__name__}: {exc}")
        return status
    base_names = {item.get("name") for item in load_layered_yaml(_TEMPLATE_PATH).get("check_items") or []
                  if isinstance(item, dict)}
    for item in layer.get("check_items") or []:
        if isinstance(item, dict) and item.get("name"):
            (status["changed"] if item["name"] in base_names else status["added"]).append(item["name"])
            if item.get("vendors"):
                status["vendor_items"].append(item["name"])
    for name in layer.get("remove_items") or []:
        (status["removed"] if name in base_names else status["unknown_removed"]).append(str(name))
    return status


def create_customer_template(customer):
    """고객사 템플릿 첫 파일(설명 주석과 빈 칸)을 만든다. 이미 있으면 그대로 둔다. 반환: 경로."""
    path = customer_template_path(customer)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_CUSTOMER_TEMPLATE_STARTER.format(customer=customer), encoding="utf-8")
    return path


def _applies_to(item, platform):
    """항목의 vendors 가 이 장비의 플랫폼을 포함하는가. vendors 가 없거나 플랫폼을 모르면 점검한다.
    'cisco' 하나는 Cisco 전체(IOS·IOS-XE·NX-OS)다 — core/vendors.platform_of('cisco') 는 IOS 하나라서 따로 본다."""
    wanted = item.get("vendors")
    if not wanted or not platform:
        return True
    from core import vendors
    for entry in wanted if isinstance(wanted, (list, tuple)) else [wanted]:
        word = str(entry).strip().lower()
        if word == "cisco" and platform.startswith("cisco"):
            return True
        if vendors.platform_of(word) == platform:
            return True
    return False

def load_template(path=None, customer=None) -> dict:
    """config/inspection_report_template.yaml을 읽어 dict로 반환.
    path 를 주지 않으면 번들 기본값 위에 사용자 사본을 얹어 읽는다(core/app_config.py) — 사용자
    사본만 읽던 때는 처음 설치할 때 복사된 사본이 굳어서, 새 버전이 항목에 더한 필드(virtual_na 등)가
    기존 사용자에게 적용되지 않았다. path 를 주면 그 파일 하나만 읽는다.
    customer 를 주면 그 고객사의 템플릿(data/<고객사>/report_template.yaml)을 그 위에 한 번 더 얹는다
    (apply_customer_layer — IMPROVEMENT_PLAN 4-10).
    파일이 없거나 깨져 있으면 최소 동작 가능한 기본값을 돌려준다 — 보고서 생성이
    설정 파일 하나 때문에 통째로 실패하지 않게 하기 위함."""
    if path is None:
        data = load_layered_yaml(_TEMPLATE_PATH)
        if customer:
            layer = _read_customer_layer(customer)
            if layer:
                data = apply_customer_layer(data, layer)
    else:
        try:
            with Path(path).open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            data = {}
    meta = data.get("meta") or {}
    items = data.get("check_items") or []
    if not items:
        items = [{"group": "기본", "name": "System Uptime",
                  "commands": ["show version"], "criteria": "Uptime 확인", "evaluator": "uptime"}]
    # no를 비워둔 항목에 순번을 채운다(템플릿을 손으로 편집할 때 번호를 다시 매기지 않도록).
    for i, item in enumerate(items, start=1):
        item.setdefault("no", i)
        item.setdefault("group", "")
        # 사람이 손으로 고치는 파일이라 'dir flash:'처럼 콜론으로 끝나는 커맨드를 인용부호 없이
        # 쓰면 YAML이 dict으로 읽어버린다 — 그 값이 그대로 셀에 들어가 저장이 깨지므로 여기서
        # 문자열로 정규화한다.
        item["commands"] = [
            next(iter(c)) if isinstance(c, dict) and c else str(c)
            for c in (item.get("commands") or [])
        ]
        item.setdefault("criteria", "")
        item.setdefault("evaluator", "keyword_ok")
    meta.setdefault("title_suffix", "정기점검 보고서")
    meta.setdefault("manager", {})
    meta.setdefault("inspector", {})
    # PDF 보고서(report/inspection_pdf.py)의 표지 전용 필드 — 표준 양식엔 있지만 엑셀
    # 파이프라인엔 없던 개념이라 기본값만 여기서 채우고(코드 수정 없이 yaml로 바꿀 수 있게),
    # 엑셀 렌더러(_build_cover)는 그대로 쓰지 않는다.
    meta.setdefault("confirmer", {})
    meta.setdefault("vendor", "")
    meta.setdefault("report_title", "네트워크 스위치 정기점검")
    meta.setdefault("site_name", "")
    return {"meta": meta, "check_items": items}


# --------------------------------------------------------------------------- 로그 파싱

def split_transcript(raw_text) -> dict:
    """원본로그 한 파일을 {커맨드: 출력} dict로 분해.

    두 가지 저장 포맷을 모두 받는다:
      1) "--- {cmd} ---" 헤더 포맷 (engine/collector.py, 세션 터미널의 커맨드 실행 기록)
      2) 프롬프트 트랜스크립트 ("Core1(config)#show mlag") — 헤더 없이 화면 그대로 저장된 것

    헤더가 하나라도 있으면 헤더를 경계로 삼고, 없을 때만 프롬프트 줄을 경계로 쓴다.
    같은 커맨드가 여러 번 실행됐으면 출력이 있는 마지막 결과를 남긴다 — 페이징 해제 직후처럼
    첫 실행이 빈 출력인 경우 그 빈 값이 실제 출력을 덮어쓰면 안 되기 때문.

    report.textfsm_parser.split_raw_log()를 쓰지 않는 이유: 그 정규식은 헤더 뒤에 "\\n"이
    바로 오는 것을 전제하는데, 실제 cache/original_log/의 파일은 CRLF(\\r\\n)로 저장돼 있어
    한 건도 매칭되지 않는다. 여기서는 splitlines()로 줄을 나눠 개행 방식과 무관하게 처리한다.
    """
    if not raw_text:
        return {}

    lines = raw_text.splitlines()
    result = {}
    current_cmd = None
    buffer = []

    def flush():
        if current_cmd is not None:
            body = "\n".join(buffer).strip("\n")
            if body or current_cmd not in result:
                result[current_cmd] = body

    has_headers = any(_SECTION_HEADER_RE.match(line) for line in lines)

    for line in lines:
        if has_headers:
            match = _SECTION_HEADER_RE.match(line)
            command = match.group(1).strip() if match else None
        else:
            match = _PROMPT_LINE_RE.match(line)
            command = match.group(2).strip() if match else None
            # 프롬프트만 있고 커맨드가 없는 줄과, 구분자로 쓰이는 "!"는 경계가 아니다.
            if match and not command:
                continue
            if command == "!":
                continue

        if command:
            flush()
            current_cmd = command
            buffer = []
            continue
        if match:  # 헤더/프롬프트 줄 자체는 출력에 넣지 않는다.
            continue
        if current_cmd is not None:
            # 헤더 포맷에서는 헤더 바로 다음 줄에 커맨드가 그대로 에코돼 있으므로 건너뛴다.
            if has_headers and not buffer and line.strip() == current_cmd:
                continue
            buffer.append(line)
    flush()
    return result


# 장비가 받지 않은 명령의 출력 — 첫 줄이 CLI 거부 문구다.
_REJECTED_OUTPUT_RE = re.compile(r"^%\s*(?:Invalid input|Incomplete command|Unavailable command|Ambiguous command|"
                                 r"Unrecognized)", re.IGNORECASE)


def _rejected(output) -> bool:
    first = next((line.strip() for line in str(output or "").splitlines() if line.strip()), "")
    return bool(_REJECTED_OUTPUT_RE.match(first))


def _matching_sections(sections: dict, patterns) -> list:
    """patterns(부분 문자열 목록) 순서대로 걸리는 커맨드 섹션 [(커맨드, 출력)] — 같은 섹션은 한 번만."""
    lowered = {cmd.lower(): cmd for cmd in sections}
    found = []
    for pattern in patterns or []:
        needle = str(pattern).lower()
        for low, original in lowered.items():
            if needle in low and all(original != command for command, _output in found):
                found.append((original, sections[original]))
    return found


def _find_section(sections: dict, patterns) -> tuple:
    """patterns(부분 문자열 목록)에 가장 먼저 걸리는 커맨드 섹션을 (커맨드, 출력)으로 반환.
    커맨드 문자열은 장비마다 옵션이 조금씩 달라(`show environment power detail` 등)
    완전 일치가 아니라 부분 일치로 찾는다. 장비가 거부한 명령(% Invalid input …)의 출력보다 다른 패턴으로 찾은
    실제 출력을 먼저 쓴다 — 새 EOS 는 show environment cooling 을 거부하고 show system environment cooling 만 받는다
    (실장비 25대 모두). 모두 거부됐으면 처음 찾은 것을 쓴다(미수집 사유가 보이게)."""
    found = _matching_sections(sections, patterns)
    usable = next((pair for pair in found if not _rejected(pair[1])), None)
    return usable or (found[0] if found else (None, None))


# --------------------------------------------------------------------------- 평가기(비즈니스 로직)

# 판정 결과를 '비정상'이 아닌 쪽으로 갈라내는 두 축.
#
# 왜 필요한가: 예전에는 규칙 엔진이 무언가 잡기만 하면 전부 '확인필요'가 됐다. 그래서
# vEOS-lab 같은 가상 장비에서 아래 세 가지가 전부 장애로 보고됐다 —
#   * "% Unavailable command (...)"        명령을 지원하지 않는 플랫폼   -> 해당없음
#   * "There seem to be no power supplies" PSU/FAN/센서가 없는 가상장비  -> 해당없음
#   * "% BGP inactive"                     BGP 를 아예 안 쓰는 장비      -> 해당없음
#   * "% Invalid input"                    명령이 깨져서 못 읽음          -> 미수집
# 실기 장비(정상 판독)와 대조해 보면 이건 전부 '점검 결과'가 아니라 '점검 불가/대상 아님'이다.
#
# category_tag 는 config/log_rules.json 의 signature 가 붙인다 — 코드에는 '어떻게 해석할지'만
# 두고 '무엇이 그런 종류인지'는 규칙 파일에 남긴다.
NA_CATEGORIES = frozenset({"collection"})        # 봐야 하는데 못 봤다  -> 미수집
SKIP_CATEGORIES = frozenset({"not_applicable"})  # 볼 것이 없다        -> 해당없음
# 가상환경 프로파일에서 virtual_na 항목에 찍히는 값 — 셀만 보고도 '왜 비었는지' 알 수 있게.
VIRTUAL_NA_VALUE = f"{STATUS_SKIP} (가상환경 — 해당 하드웨어 없음)"
# 항목을 '확인필요'로 올릴 심각도. minor/info(설정변경 이력 %SYS-5-CONFIG_I, SVI
# lowerlayerdown 등)는 참고로만 남기고 비정상으로 세지 않는다.
WARN_SEVERITIES = frozenset({"major", "critical"})


def _anomaly_lines(text):
    """이상 징후로 판정된 줄을 상세 정보와 함께 반환 — 판정 기준을 원본로그분석 탭과 동일하게
    유지하려고 engine.log_analysis의 규칙(config/log_rules.json)을 그대로 재사용한다.

    반환: [{"keyword", "severity", "category", "reason", "line"}]
    예전에는 (키워드, 줄)만 돌려줬는데, 그러면 호출부가 '명령 미지원'과 '실제 장애'를
    구분할 방법이 없어 둘 다 확인필요가 됐다. 심각도와 분류를 함께 넘긴다."""
    from engine.log_analysis import classify_lines_detailed
    hits = []
    lines = (text or "").splitlines()
    for line, verdict in zip(lines, classify_lines_detailed(lines)):
        if verdict:
            hits.append({
                "keyword": verdict["keyword"], "severity": verdict.get("severity", "major"),
                "category": verdict.get("category_tag", "general"),
                "reason": verdict.get("reason", ""), "line": line.strip(),
            })
    return hits


def _preview(hits, limit=3):
    return "; ".join(f"{h['keyword']}: {h['line']}" for h in hits[:limit])


def _short_reason(hit):
    """판정 사유의 앞 절만 — 셀에 들어갈 짧은 꼬리표.

    규칙의 title 은 '왜 그런지'까지 설명하는 한 문장이라(예: "해당 하드웨어 리소스 없음(가상
    장비) — 점검 항목 미적용") 셀에 통째로 넣으면 표가 읽히지 않는다. 전문은 '특이사항'
    블록(detail)에 그대로 남는다."""
    return (hit.get("reason") or "").split(" — ")[0].strip() or hit.get("keyword", "")


def _split_hits(hits):
    """판정된 줄들을 (비정상, 미수집, 해당없음, 참고) 네 묶음으로 나눈다."""
    warn, na, skip, note = [], [], [], []
    for hit in hits:
        if hit["category"] in NA_CATEGORIES:
            na.append(hit)
        elif hit["category"] in SKIP_CATEGORIES:
            skip.append(hit)
        elif hit["severity"] in WARN_SEVERITIES:
            warn.append(hit)
        else:
            note.append(hit)
    return warn, na, skip, note


def _classify_hits(hits):
    """줄 단위 판정들을 항목 하나의 상태로 접는다.

    우선순위는 비정상 > 미수집 > 해당없음 > 정상이다. 비정상이 가장 앞인 이유: 명령 일부가
    거부돼도 나머지 출력에서 실제 장애가 보였다면 그건 봐야 하는 장애다(미수집이라는 이유로
    묻히면 안 된다). 참고(minor/info)만 남으면 정상이되 건수는 값에 남긴다 — 조용히
    사라지면 운영자가 되짚을 수 없기 때문."""
    warn, na, skip, note = _split_hits(hits)
    if warn:
        value = f"{STATUS_WARN} ({len(warn)}건)"
        if note:
            value += f" / 참고 {len(note)}건"
        return {"value": value, "status": STATUS_WARN,
                "detail": _preview(warn + note, 5)}
    if na:
        return {"value": f"{STATUS_NA} ({_short_reason(na[0])})", "status": STATUS_NA,
                "detail": _preview(na)}
    if skip:
        return {"value": f"{STATUS_SKIP} ({_short_reason(skip[0])})", "status": STATUS_SKIP,
                "detail": _preview(skip)}
    if note:
        return {"value": f"{STATUS_OK} (참고 {len(note)}건)", "status": STATUS_OK,
                "detail": _preview(note, 5)}
    return {"value": STATUS_OK, "status": STATUS_OK}


def _eval_keyword_ok(text, item):
    return _classify_hits(_anomaly_lines(text))


def _eval_uptime(text, item):
    # EOS 'Uptime: 2 weeks, ...', Cisco 'R1 uptime is 3 weeks, ...'·NX-OS 'Kernel uptime is 120 day(s), ...'
    match = re.search(r"[Uu]ptime\s*(?:is\s+|:\s*)?(.+)", text or "")
    if not match:
        # show processes top의 첫 줄 형식: " 10:28:11 up 8 days,  1:23, ..."
        match = re.search(r"\bup\s+([^,]+(?:,\s*[^,]+)?)", text or "")
    if not match:
        return {"value": STATUS_NA, "status": STATUS_NA}
    return {"value": match.group(1).strip().rstrip("."), "status": STATUS_OK}


# Cisco IOS·IOS-XE `show processes cpu sorted` 첫 줄
_IOS_CPU_RE = re.compile(r"CPU utilization for five seconds:\s*[\d.]+%(?:/[\d.]+%)?;\s*one minute:\s*[\d.]+%;"
                         r"\s*five minutes:\s*([\d.]+)%", re.IGNORECASE)


def _eval_cpu(text, item):
    """show processes top의 CPU 행에서 idle을 찾아 사용률(100-idle)을 계산한다.
    idle이 없으면 us(user) 값이라도 쓴다 — 대한전선 보고서가 '5.4 us'로 기록한 방식.
    Cisco IOS·IOS-XE 는 'CPU utilization for five seconds: …; five minutes: 5%' 의 5분 평균을 쓰고,
    NX-OS(show system resources)의 'CPU states : … 94.50% idle' 은 idle 규칙이 그대로 읽는다."""
    ios = _IOS_CPU_RE.search(text or "")
    idle = None if ios else re.search(r"([\d.]+)\s*%?\s*id", text or "")
    if ios:
        used = round(float(ios.group(1)), 1)
    elif idle:
        used = round(100.0 - float(idle.group(1)), 1)
    else:
        user = re.search(r"([\d.]+)\s*%?\s*us", text or "")
        if not user:
            return {"value": STATUS_NA, "status": STATUS_NA}
        used = round(float(user.group(1)), 1)
    threshold = item.get("warn_at", 70)
    status = STATUS_WARN if used >= float(threshold) else STATUS_OK
    return {"value": f"{used}%", "status": status, "number": used}


def _eval_memory_free(text, item):
    """Free Memory 비율. EOS show version 의 Total/Free memory(kB)를 먼저 쓴다 — LGES 보고서가 Total/Free 바이트로 비율을
    산출한 방식이고, EOS 가 다시 쓸 수 있는 메모리로 센 값이다. 없으면 show processes top 의 Mem 행 — 예전엔 이것을 먼저
    봤는데 top 의 free 는 버퍼/캐시를 뺀 값이라 실장비(Arista 25대)에서 2~49% 로 나와 18대가 '확인필요'로 잘못 올라갔다
    (show version 으로는 37~80%). 그래서 Swap 줄의 'avail Mem'(새 top), 없으면 free + buff/cache, 예전 top 은
    free + buffers + 'cached Mem' 을 쓴다. 그다음 Cisco IOS·IOS-XE, NX-OS.
    evaluate_device 가 양식의 명령에 걸리는 출력(show processes top·show version)을 모두 이어 붙여 넘긴다.

    값은 두 가지로 내보낸다:
      value  = "12.6%"  사람이 읽는 문자열. PDF/요약/전월값 스냅샷이 이걸 그대로 쓴다.
      number = 0.126    엑셀 셀에 넣는 수치(number_format "0.0%"가 곱해 보여준다).
    예전에는 value 에도 비율(0.126)을 넣어서 PDF 의 '(Free / Total) * 100' 칸에 0.126 이
    찍혔다 — *100 이 빠진 것처럼 보이던 증상의 원인이다. 계산식 자체는 맞았다."""
    total = free = None
    total_m = re.search(r"Total memory:\s*([\d]+)", text or "")
    free_m = re.search(r"Free memory:\s*([\d]+)", text or "")
    if total_m and free_m:
        total, free = float(total_m.group(1)), float(free_m.group(1))
    mem_row = None if total is not None else re.search(r"(?:KiB|MiB|GiB)\s+Mem\s*:?\s*(.+)", text or "")
    if mem_row:
        row = mem_row.group(1)
        total_m = re.search(r"([\d.]+)\s*total", row)
        free_m = re.search(r"([\d.]+)\s*free", row)
        if total_m and free_m:
            total, free = float(total_m.group(1)), float(free_m.group(1))
            available = re.search(r"Swap\s*:?.*?([\d.]+)\s*avail\s+Mem", text or "")
            cache = re.search(r"([\d.]+)\s*buff/cache", row)
            buffers = re.search(r"([\d.]+)\s*buffers", row)
            cached = re.search(r"Swap\s*:?.*?([\d.]+)\s*cached\s+Mem", text or "")
            if available:
                free = float(available.group(1))
            elif cache:
                free += float(cache.group(1))
            elif buffers and cached:
                free += float(buffers.group(1)) + float(cached.group(1))
    if total is None:   # Cisco IOS·IOS-XE show processes memory: 'Processor Pool Total: N Used: N Free: N'
        pool = re.search(r"Processor Pool\s+Total:\s*(\d+)\s+Used:\s*\d+\s+Free:\s*(\d+)", text or "")
        if pool:
            total, free = float(pool.group(1)), float(pool.group(2))
    if total is None:   # NX-OS show system resources: 'Memory usage: 16400084K total, 5947316K used, 10452768K free'
        usage = re.search(r"Memory usage:\s*(\d+)K total,\s*\d+K used,\s*(\d+)K free", text or "")
        if usage:
            total, free = float(usage.group(1)), float(usage.group(2))
    if not total:
        return {"value": STATUS_NA, "status": STATUS_NA}
    ratio = round(free / total, 4)
    threshold = float(item.get("warn_at", 0.3))
    # 임계값을 퍼센트(30)로 적어둔 템플릿도 받아준다 — 0.3 과 30 이 섞여 들어오면
    # 한쪽은 항상 정상, 다른 쪽은 항상 경고가 되어 판정이 조용히 무의미해진다.
    if threshold > 1:
        threshold /= 100.0
    status = STATUS_WARN if ratio < threshold else STATUS_OK
    return {"value": f"{round(ratio * 100, 1)}%", "status": status,
            "number": ratio, "number_format": "0.0%"}


_ERROR_COUNTER_HEADER_RE = re.compile(r"^\s*Port\s+(.*(?:FCS|CRC|Align|Symbol|Runts|Giants).*)$", re.IGNORECASE)
# 표의 포트 열 — Et3/4·Po10(EOS), Gi1/0/1·Te1/1/1·Fa0/1(IOS), Eth1/1·mgmt0(NX-OS). 예전엔 EOS 접두어만 받아서
# Cisco 포트 줄을 모두 건너뛰고 오류가 있어도 '정상'이 됐을 것이다.
_COUNTER_PORT_RE = re.compile(r"^[A-Za-z][A-Za-z\-]*\d[\d/.:]*$")


def _eval_interface_error(text, item):
    """`show interfaces counters errors`의 표를 읽어 0이 아닌 카운터가 있는 포트만 요약한다.
    "Et3/4 FCS 10802"처럼 포트+에러종류+카운트를 붙여 만드는 게 레거시 보고서의 표기 방식."""
    lines = (text or "").splitlines()
    columns = None
    problems = []
    for line in lines:
        header = _ERROR_COUNTER_HEADER_RE.match(line)
        if header:
            columns = header.group(1).split()
            continue
        if not columns:
            continue
        parts = line.split()
        if len(parts) < 2 or not _COUNTER_PORT_RE.match(parts[0]):
            continue
        port, values = parts[0], parts[1:]
        for name, raw in zip(columns, values):
            if not re.fullmatch(r"\d+", raw) or int(raw) == 0:
                continue
            problems.append(f"{port} {name} {raw}")
    if columns is None:
        return {"value": STATUS_NA, "status": STATUS_NA}
    if not problems:
        return {"value": STATUS_OK, "status": STATUS_OK}
    return {"value": ", ".join(problems[:6]) + (" ..." if len(problems) > 6 else ""),
            "status": STATUS_WARN, "detail": "\n".join(problems)}


_ROUTE_PROTOCOLS = ("connected", "static", "ospf", "bgp", "isis", "rip", "eigrp")
# Cisco IOS·IOS-XE: 'Route Source  Networks  Subnets  Replicates  Overhead  Memory (bytes)' 표 —
# 'ospf 1   0   10   0 …' 처럼 프로세스 번호가 숫자 열 앞에 붙는 줄이 있다.
_IOS_ROUTE_HEADER_RE = re.compile(r"^\s*Route Source\s+Networks\s+Subnets(.*)$", re.IGNORECASE | re.MULTILINE)
_IOS_ROUTE_LINE_RE = re.compile(rf"^\s*({'|'.join(_ROUTE_PROTOCOLS)})\b(.*)$", re.IGNORECASE | re.MULTILINE)
# NX-OS: 'Best paths per protocol:' 아래 '  direct : 4', '  ospf-1 : 2'(태그), VRF 마다 반복된다.
_NXOS_ROUTE_LINE_RE = re.compile(r"^\s*(direct|static|ospf|bgp|isis|rip|eigrp)(?:-\S+)?\s*:\s*(\d+)\b",
                                 re.IGNORECASE | re.MULTILINE)


def _cisco_route_counts(text):
    """Cisco 출력이면 {프로토콜: 라우트 수}, 아니면 None. IOS 는 Networks+Subnets, NX-OS 는 VRF 를 모두 더한다."""
    header = _IOS_ROUTE_HEADER_RE.search(text)
    if header:
        columns = 2 + len(re.findall(r"\b[A-Za-z]+\b", header.group(1).replace("(bytes)", "")))
        counts = {}
        for proto, rest in _IOS_ROUTE_LINE_RE.findall(text):
            numbers = re.findall(r"\b\d+\b", rest)
            if len(numbers) == columns + 1:
                numbers = numbers[1:]                  # 'ospf 1' 의 프로세스 번호
            if len(numbers) >= 2:
                counts[proto.lower()] = counts.get(proto.lower(), 0) + int(numbers[0]) + int(numbers[1])
        return counts
    if "paths per protocol" in text.lower():
        counts = {}
        for proto, number in _NXOS_ROUTE_LINE_RE.findall(text):
            proto = "connected" if proto.lower() == "direct" else proto.lower()
            counts[proto] = counts.get(proto, 0) + int(number)
        return counts
    return None


def _eval_route_summary(text, item):
    """`show ip route summary`의 프로토콜별 라우트 수를 "connected:11 / static:57 / ospf:46"로.
    증감 비교(전월 대비)는 값이 문자열로 그대로 남으므로 보고서에서 눈으로 바로 확인된다."""
    counts = _cisco_route_counts(text or "")
    if counts is None:
        counts = {}
        for proto in ("connected", "static", "ospf", "bgp", "isis", "rip"):
            match = re.search(rf"^\s*{proto}\b[^\d\n]*(\d+)", text or "", re.IGNORECASE | re.MULTILINE)
            if match:
                counts[proto] = int(match.group(1))
    if not counts:
        return {"value": STATUS_NA, "status": STATUS_NA}
    return {"value": "\n".join(f"{k}:{v}" for k, v in counts.items()), "status": STATUS_OK}


def _eval_log_scan(text, item):
    """show logging 스캔 — '특이 로그'는 major/critical 만 센다.

    예전에는 규칙에 걸린 모든 줄을 셌기 때문에 %SYS-5-CONFIG_I(콘솔 로그인) / %SYS-5-CONFIG_E
    (설정모드 진입) 같은 정상 운영 로그가 장비당 30건씩 '특이 로그'로 올라왔다. syslog
    facility 심각도 5 이하는 정보성이므로 참고 건수로만 남기고 판정에서 뺀다."""
    hits = _anomaly_lines(text)
    warn, na, skip, note = _split_hits(hits)
    if not hits:
        return {"value": "없음", "status": STATUS_OK}
    if not warn:
        if na and not note:
            return {"value": f"{STATUS_NA} ({_short_reason(na[0])})", "status": STATUS_NA,
                    "detail": _preview(na)}
        value = "없음" if not note else f"없음 (참고 {len(note)}건)"
        return {"value": value, "status": STATUS_OK,
                "detail": "\n".join(h["line"] for h in note[:20])}
    value = f"특이 로그 {len(warn)}건"
    if note:
        value += f" (참고 {len(note)}건)"
    return {"value": value, "status": STATUS_WARN,
            "detail": "\n".join(h["line"] for h in (warn + note)[:20])}


EVALUATORS = {
    "uptime": _eval_uptime,
    "cpu": _eval_cpu,
    "memory_free": _eval_memory_free,
    "interface_error": _eval_interface_error,
    "route_summary": _eval_route_summary,
    "log_scan": _eval_log_scan,
    "keyword_ok": _eval_keyword_ok,
}


def evaluate_device(sections: dict, template: dict, *, is_virtual: bool = False, platform: str = None) -> list:
    """장비 1대의 {커맨드: 출력}을 템플릿의 점검 항목 순서대로 판정해 행 목록을 만든다.
    반환 각 항목: {no, group, name, method, criteria, value, status, detail}.
    해당 커맨드를 아예 수집하지 않았으면 '미수집'으로 남긴다 — 빈칸으로 두면 정상인지
    안 봤는지 구분이 안 되기 때문.

    is_virtual: 프로파일이 '가상환경'으로 설정된 경우 True. 템플릿에 virtual_na: true 로
    표시된 항목(PSU/FAN/온도/모듈/트랜시버)은 로그를 보지 않고 곧바로 '해당없음'이 된다 —
    가상 플랫폼에는 그 하드웨어가 없으므로 '미수집(다음 회차 재점검)'도 아니고, 출력 문구가
    벤더/버전마다 달라 규칙에 걸리지 않아도 오탐이 남지 않는다.

    platform: 장비 플랫폼(core/vendors). Cisco 면 양식의 EOS 명령 패턴 뒤에 같은 내용을 담은 Cisco 명령을 이어서 찾고
    (engine/vendor_commands.report_patterns), 그 플랫폼에 없는 기능(IOS 의 MLAG 등)은 '해당없음'으로 둔다."""
    rows = []
    for item in template["check_items"]:
        if not _applies_to(item, platform):
            # 고객사 템플릿 등에서 vendors 로 벤더를 정한 항목 — 다른 벤더 장비에는 점검 대상이 아니다(4-10).
            rows.append({
                "no": item.get("no"), "group": item.get("group", ""), "name": item.get("name", ""),
                "method": (item.get("commands") or [""])[0], "criteria": item.get("criteria", ""),
                "value": f"{STATUS_SKIP} (이 벤더의 점검 항목이 아님)", "status": STATUS_SKIP,
                "detail": f"양식의 vendors({', '.join(map(str, item.get('vendors') or []))})에 이 장비가 없습니다.",
                "number": None, "number_format": None,
            })
            continue
        if is_virtual and item.get("virtual_na"):
            command = (item.get("commands") or [""])[0]
            result = {"value": VIRTUAL_NA_VALUE, "status": STATUS_SKIP,
                      "detail": "프로파일이 가상환경으로 설정되어 있습니다 — 해당 하드웨어가 없어 점검 대상이 아닙니다."}
            rows.append({
                "no": item.get("no"), "group": item.get("group", ""), "name": item.get("name", ""),
                "method": command, "criteria": item.get("criteria", ""),
                "value": result["value"], "status": result["status"], "detail": result["detail"],
                "number": None, "number_format": None,
            })
            continue
        patterns = vendor_commands.report_patterns(item.get("commands"), item.get("evaluator"), platform)
        command, output = _find_section(sections, patterns)
        absent = vendor_commands.not_applicable(item.get("commands"), platform) if command is None else None
        if absent:
            result = {"value": f"{STATUS_SKIP} ({absent})", "status": STATUS_SKIP}
        elif command is None:
            result = {"value": f"{STATUS_NA} (해당 명령 출력 없음)", "status": STATUS_NA}
        else:
            evaluator = EVALUATORS.get(item.get("evaluator"), _eval_keyword_ok)
            if item.get("evaluator") == "memory_free":
                # show version 의 Total/Free memory 를 먼저 쓰려면 그 출력도 봐야 한다 — 양식 순서상 show processes top 이
                # 먼저 걸린다(_eval_memory_free).
                output = "\n".join(text for _command, text in _matching_sections(sections, patterns))
            result = evaluator(output, item)
        rows.append({
            "no": item.get("no"), "group": item.get("group", ""), "name": item.get("name", ""),
            "method": command or (item.get("commands") or [""])[0],
            "criteria": item.get("criteria", ""),
            "value": result.get("value"), "status": result.get("status"),
            "detail": result.get("detail", ""),
            "number": result.get("number"), "number_format": result.get("number_format"),
        })
    return rows
