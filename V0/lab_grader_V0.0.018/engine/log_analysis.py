"""
'00_orignal_log' 원본을 상태기계(FSM)로 한 줄씩 훑어 에러 휴리스틱을 찾고,
발견 시 그 주변 계층 구조(커맨드 헤더 + 부모 카테고리 노드 + 에러 줄 + 다음 형제
카테고리 노드 직전까지의 후속 출력)를 통째로 보존해 '01_problem_log'에 기록한다.

FSM 상태 전이:
  커맨드 헤더('--- cmd ---')를 만나면      -> 현재 커맨드 갱신, 열려있던 문제 블록을 닫음
  카테고리 노드(예: VLAN0010/VL10)를 만나면 -> 현재 카테고리 갱신, 열려있던 문제 블록을 닫음
                                             (= 이전 블록 입장에서 "다음 형제 카테고리 노드")
  에러 키워드가 있는 줄을 만나면            -> 문제 블록을 새로 열고 그 줄부터 흡수 시작
  문제 블록이 열려있는 동안의 모든 줄       -> 다음 헤더/카테고리 전까지 그 블록에 계속 흡수

report/report_api.py의 이상 징후 하이라이트와 동일한 키워드 세트를 쓴다(정의는 여기가 원본).
"""
import os
import re
import glob

ANOMALY_KEYWORDS = ["FAIL", "ERROR", "CRITICAL", "DOWN", "CRC", "DROPS", "ERR-DISABLED", "TIMEOUT", "UNREACHABLE"]

# 정상 설정 구문에 흔한 표현인데 키워드와 우연히 겹쳐서 이상 징후로 오탐지되던 것들.
# 단어 경계(\b) 매칭으로 바꿔도(아래 _KEYWORD_RES) "no shutdown" 안의 "DOWN"처럼 같은
# 단어에 파묻힌 경우는 저절로 걸러지지만, 아래는 키워드 자체가 독립된 단어/토큰으로 등장하는
# 정상 설정 값이라 화이트리스트로 명시적으로 제외해야 한다.
#   - "action error": system l1의 "unsupported ... action error" 옵션(정상 설정 값이지 실제 에러 아님)
#   - "level critical": logging buffered/console/synchronous의 심각도 등급 옵션(정상 설정 값)
#   - "idle-timeout": management console/line의 idle-timeout 설정(정상 타임아웃 "값" 설정이지 실제 타임아웃 발생 아님)
BENIGN_PHRASES = ["action error", "level critical", "idle-timeout"]

_COMMAND_HEADER_RE = re.compile(r"^--- (.+?) ---\s*$")
# Cisco/Arista 계열 show running-config는 최상위 설정 블록을 "!" 단독 줄로 구분하고,
# 각 블록의 자식 설정은 들여쓰기(공백)로 표현한다 — 이 두 신호만으로 계층을 일반적으로 추적할 수 있고,
# "VLAN ###" 같은 특정 카테고리 이름을 하드코딩할 필요가 없다.
_SECTION_END_RE = re.compile(r"^!\s*$")
# 파이썬 \b는 밑줄(_)을 "단어 문자"로 취급해서 "NEIGHBOR_TIMEOUT" 같은 syslog 이벤트 이름의
# "TIMEOUT"까지 걸러버린다(밑줄 앞뒤라 경계로 안 잡힘) — 그래서 \b 대신 "앞뒤로 알파벳 문자가
# 붙어있지 않으면 매치"라는 룩어라운드를 직접 정의한다. 이러면 "SHUTDOWN"에 파묻힌 "DOWN"은
# 여전히 걸러지면서(앞이 알파벳 T), "NEIGHBOR_TIMEOUT"의 "TIMEOUT"은 정상적으로 잡힌다
# (앞이 알파벳이 아닌 밑줄).
_KEYWORD_RES = {
    kw: re.compile(r"(?<![A-Za-z])" + re.escape(kw) + r"(?![A-Za-z])", re.IGNORECASE)
    for kw in ANOMALY_KEYWORDS
}


def _find_error_keyword(line):
    lowered = line.lower()
    if any(phrase in lowered for phrase in BENIGN_PHRASES):
        return None
    return next((kw for kw, rx in _KEYWORD_RES.items() if rx.search(line)), None)


def _indent_width(line):
    return len(line) - len(line.lstrip(" \t"))


def analyze_text(raw_text):
    """
    raw_text를 FSM으로 훑어 에러가 발견될 때마다 하나의 '문제 블록'을 만든다.

    계층 추적: 들여쓰기가 없는(최상위) 줄을 만나면 그게 곧 새 설정 블록의 시작이므로
    현재 카테고리를 갱신하고, 이전에 열려있던 문제 블록은 (그 블록을 연 줄보다 얕거나 같은
    들여쓰기까지 돌아왔다는 뜻이므로) 여기서 닫는다. "!" 구분자를 만나도 즉시 닫는다.
    이렇게 해야 예를 들어 "vlan 999" 아래 있던 문제 블록이 "vrf instance MGMT"나
    "management console" 같은, VLAN이 아닌 다음 최상위 블록의 자식 줄(예: idle-timeout)까지
    잘못 흡수하지 않는다 — 예전엔 "VLAN ###" 형태의 줄만 경계로 인식해서 그 사이에 낀
    무관한 최상위 블록들의 자식 줄이 전부 이전 블록에 이어 붙는 버그가 있었음.

    반환: [{"command": str|None, "category": str|None, "keyword": str, "line_no": int,
             "block": [에러줄부터 같은 계층이 끝나는 지점까지의 원본 줄들]}]
    """
    findings = []
    current_command = None
    current_category = None
    open_block = None  # 지금 컨텍스트를 흡수 중인 문제 블록(없으면 None)

    def close_block():
        nonlocal open_block
        if open_block is not None:
            findings.append(open_block)
            open_block = None

    for line_no, line in enumerate(raw_text.splitlines(), start=1):
        header_m = _COMMAND_HEADER_RE.match(line)
        if header_m:
            close_block()
            current_command = header_m.group(1).strip()
            current_category = None
            continue

        stripped = line.strip()

        if _SECTION_END_RE.match(stripped):
            close_block()
            continue

        is_top_level = bool(stripped) and _indent_width(line) == 0
        if is_top_level:
            # 최상위 줄 = 새 설정 블록의 시작 -> 이전 블록이 이 시점까지 열려 있었다면 여기서 경계.
            close_block()
            current_category = stripped
        elif open_block is not None:
            open_block["block"].append(line)
            continue

        keyword = _find_error_keyword(line)
        if keyword:
            open_block = {
                "command": current_command, "category": current_category,
                "keyword": keyword, "line_no": line_no, "block": [line],
            }

    close_block()
    return findings


def format_finding(finding):
    """문제 블록 하나를 01_problem_log 텍스트로 렌더링 — 탐지 사유 주석/커맨드 헤더/카테고리/에러줄/후속출력 순.

    카테고리(부모 컨텍스트 줄)가 에러 줄 자신과 같은 줄이면(= 에러 키워드가 들여쓰기 없는
    최상위 줄 자체에 있었던 경우, 예: syslog 한 줄짜리 이벤트) 같은 내용이 중복 출력되므로 생략."""
    lines = [f"# 탐지 사유: 키워드 '{finding['keyword']}' 검출 (원본 {finding['line_no']}행)"]
    if finding["command"]:
        lines.append(f"--- {finding['command']} ---")
    if finding["category"] and (not finding["block"] or finding["category"] != finding["block"][0]):
        lines.append(finding["category"])
    lines.extend(finding["block"])
    return "\n".join(lines)


def run_analysis(original_dir, problem_dir):
    """00_orignal_log의 모든 .txt를 분석해 01_problem_log에 저장.
    반환: [{"source", "problem_count", "output"}] — mtime 순서 무관, 파일명순."""
    results = []
    for path in sorted(glob.glob(os.path.join(original_dir, "*.txt"))):
        with open(path, encoding="utf-8", errors="replace") as f:
            raw_text = f.read()
        findings = analyze_text(raw_text)
        out_name = None
        if findings:
            os.makedirs(problem_dir, exist_ok=True)
            out_name = os.path.splitext(os.path.basename(path))[0] + "_problems.txt"
            body = "\n\n".join(format_finding(f) for f in findings)
            header = f"=== 이상탐지 결과 — {os.path.basename(path)} ({len(findings)}건) ===\n\n"
            with open(os.path.join(problem_dir, out_name), "w", encoding="utf-8") as f:
                f.write(header + body + "\n")
        results.append({"source": os.path.basename(path), "problem_count": len(findings), "output": out_name})
    return results
