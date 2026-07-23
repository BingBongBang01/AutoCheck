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

_COMMAND_HEADER_RE = re.compile(r"^--- (.+?) ---\s*$")
_CATEGORY_NODE_RE = re.compile(r"^(VLAN\s*0*\d+|VL\s*0*\d+)\s*$", re.IGNORECASE)


def _find_error_keyword(line):
    upper = line.upper()
    return next((kw for kw in ANOMALY_KEYWORDS if kw in upper), None)


def analyze_text(raw_text):
    """
    raw_text를 FSM으로 훑어 에러가 발견될 때마다 하나의 '문제 블록'을 만든다.
    반환: [{"command": str|None, "category": str|None, "keyword": str, "line_no": int,
             "block": [에러줄부터 다음 형제 카테고리/헤더 직전까지의 원본 줄들]}]
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

        category_m = _CATEGORY_NODE_RE.match(line.strip())
        if category_m:
            # 새 카테고리 노드 = 이전에 열려있던 블록 입장에서 "다음 형제 노드" 경계 -> 블록을 닫는다.
            close_block()
            current_category = line.strip()
            continue

        if open_block is not None:
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
    """문제 블록 하나를 01_problem_log 텍스트로 렌더링 — 커맨드 헤더/카테고리/에러줄/후속출력 순."""
    lines = []
    if finding["command"]:
        lines.append(f"--- {finding['command']} ---")
    if finding["category"]:
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
