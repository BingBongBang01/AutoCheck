"""
'00_orignal_log' 원본을 상태기계(FSM)로 한 줄씩 훑어 이상 징후를 찾고, 발견 시 그 주변
계층 구조(명령 헤더 + 부모 컨텍스트 줄 + 문제 줄 + 다음 형제 노드 직전까지의 후속 출력)를
통째로 보존해 '01_problem_log'에 기록한다.

판정 자체는 engine/log_rule_engine.py(4층 구조: 맥락추적 -> 억제 -> 서명 -> 상관분석)에
위임하고, 이 모듈은 '어디까지를 한 블록으로 묶어 보존할 것인가'라는 구조 문제만 담당한다.
예전에는 이 파일이 판정까지 다 했는데, 그 방식(키워드 하나 걸리면 무조건 이상)은 실제
수집 로그에서 오탐/미탐을 동시에 냈다 — 상세 내역은 log_rule_engine.py 상단 주석 참고.

FSM 상태 전이:
  명령 줄(프롬프트 'Core1(config)#show ...' 또는 '--- cmd ---')  -> 현재 명령 갱신, 블록 닫음
  최상위(들여쓰기 0) 줄 또는 '!' 구분자                          -> 현재 컨텍스트 갱신, 블록 닫음
  이상으로 판정된 줄                                             -> 문제 블록을 새로 열고 흡수 시작
  블록이 열려있는 동안의 들여쓴 줄                               -> 그 블록에 계속 흡수
"""
import os
import re
import glob

from engine import log_cache
from engine.log_rule_engine import (
    ContextTracker, get_engine, load_rules, severity_rank, SEVERITY_ORDER,
)

# report_api.py가 하이라이트용으로 이 이름을 그대로 import 한다 — 유지.
ANOMALY_KEYWORDS = get_engine().anomaly_keywords
BENIGN_PHRASES = get_engine().rules.get("benign_phrases", [])

_SECTION_END_RE = re.compile(r"^!\s*$")

SEVERITY_LABELS = {"critical": "치명", "major": "중대", "minor": "경미", "info": "정보"}


def reload_rules():
    """config/log_rules.json을 다시 읽어 엔진을 재구성 — 설정 화면에서 규칙을 고친 뒤 호출."""
    global ANOMALY_KEYWORDS, BENIGN_PHRASES
    engine = get_engine(reload=True)
    ANOMALY_KEYWORDS = engine.anomaly_keywords
    BENIGN_PHRASES = engine.rules.get("benign_phrases", [])
    return engine


def classify_line(line):
    """한 줄이 이상 징후인지 판정해서 매치된 키워드(없으면 None)를 반환 — 대시보드 집계처럼
    문제 블록 추출까지는 필요 없고 '정상/비정상 줄' 카운트만 필요한 호출자를 위한 공개 래퍼.

    주의: 맥락 없이 한 줄만 보는 호출이므로 명령 스코프/표 헤더에 의존하는 규칙은 적용되지
    않는다. 그래도 0인 카운터·기능 목록표·범례 억제는 줄 자체로 판단 가능하므로 그대로 걸러져
    대시보드 집계의 오탐도 함께 줄어든다. 정밀 판정이 필요하면 analyze_text()를 쓸 것."""
    verdict = get_engine().evaluate(line, ContextTracker())
    return verdict["keyword"] if verdict else None


def classify_line_detailed(line):
    """classify_line의 상세 버전 — {keyword, severity, rule_id, reason} 또는 None."""
    return get_engine().evaluate(line, ContextTracker())


def _indent_width(line):
    return len(line) - len(line.lstrip(" \t"))


def analyze_text(raw_text, correlate=True):
    """
    raw_text를 FSM으로 훑어 이상이 판정될 때마다 하나의 '문제 블록'을 만들고, 마지막에
    중복 접기(throttle)와 복합 조건 상관분석(correlate)을 적용한다.

    계층 추적: 들여쓰기가 없는(최상위) 줄을 만나면 그게 곧 새 블록의 시작이므로 현재 컨텍스트를
    갱신하고, 이전에 열려있던 문제 블록은 여기서 닫는다. '!' 구분자를 만나도 즉시 닫는다.
    이렇게 해야 예컨대 "Port-Channel200 is down ..." 아래의 문제 블록이 다음 인터페이스
    블록의 자식 줄까지 잘못 흡수하지 않는다.

    반환: [{"command", "category", "keyword", "severity", "rule_id", "category_tag",
             "reason", "line_no", "last_line_no", "repeat", "block": [원본 줄들],
             "is_correlated"(복합 finding만)}]
    """
    engine = get_engine()
    ctx = ContextTracker()
    findings = []
    current_category = None
    open_block = None

    def close_block():
        nonlocal open_block
        if open_block is not None:
            findings.append(open_block)
            open_block = None

    for line_no, line in enumerate(raw_text.splitlines(), start=1):
        # 1층: 명령/구분선/헤더 줄이면 여기서 소비되고 판정 대상에서 빠진다.
        if ctx.feed(line):
            if ctx.is_command_line(line):
                close_block()
                current_category = None
            continue

        stripped = line.strip()

        if _SECTION_END_RE.match(stripped):
            close_block()
            continue

        is_top_level = bool(stripped) and _indent_width(line) == 0
        if is_top_level:
            close_block()
            current_category = stripped
        elif open_block is not None:
            open_block["block"].append(line)
            continue

        verdict = engine.evaluate(line, ctx)
        if verdict:
            open_block = {
                "command": ctx.command, "category": current_category,
                "keyword": verdict["keyword"], "severity": verdict["severity"],
                "rule_id": verdict["rule_id"], "category_tag": verdict.get("category_tag", "general"),
                "reason": verdict["reason"], "line_no": line_no, "block": [line],
            }

    close_block()

    if not correlate:
        return findings

    # 4층: 같은 원인의 반복을 접고(경보 피로 억제), 복합 조건을 상위 finding으로 승격.
    throttled = engine.correlator.throttle(findings)
    composites = engine.correlator.correlate(throttled)
    return composites + throttled


def summarize(findings):
    """심각도별 건수 집계 — 보고서 헤더와 대시보드에서 공용."""
    counts = {sev: 0 for sev in SEVERITY_ORDER}
    for f in findings:
        counts[f.get("severity", "major")] = counts.get(f.get("severity", "major"), 0) + 1
    return counts


def format_finding(finding):
    """문제 블록 하나를 01_problem_log 텍스트로 렌더링 —
    심각도/탐지 사유/명령/컨텍스트/문제 줄/후속 출력 순.

    컨텍스트(부모 줄)가 문제 줄 자신과 같으면(= 최상위 줄 자체에 이상이 있었던 경우, 예:
    syslog 한 줄짜리 이벤트) 같은 내용이 중복 출력되므로 생략."""
    sev = finding.get("severity", "major")
    label = SEVERITY_LABELS.get(sev, sev)
    tag = "[복합]" if finding.get("is_correlated") else ""
    repeat = finding.get("repeat", 1)
    repeat_note = f" — 동일 사유 {repeat}회 반복(원본 {finding['line_no']}~{finding.get('last_line_no')}행)" \
        if repeat > 1 else f" (원본 {finding['line_no']}행)"

    lines = [f"# [{label}]{tag} {finding.get('reason', '이상 징후')}{repeat_note}"]
    if finding.get("command"):
        lines.append(f"--- {finding['command']} ---")
    if finding.get("category") and (not finding["block"] or finding["category"] != finding["block"][0]):
        lines.append(finding["category"])
    lines.extend(finding["block"])
    return "\n".join(lines)


def format_report(source_name, findings):
    """01_problem_log 파일 본문 전체를 렌더링 — 심각도 요약 헤더 + 심각도 내림차순 본문.
    운영자가 파일을 열자마자 '지금 당장 볼 것'이 맨 위에 오게 하기 위해 정렬한다."""
    counts = summarize(findings)
    summary = " / ".join(f"{SEVERITY_LABELS[s]} {counts[s]}건"
                         for s in reversed(SEVERITY_ORDER) if counts.get(s))
    header = (f"=== 이상탐지 결과 — {source_name} (총 {len(findings)}건)"
              f"{' : ' + summary if summary else ''} ===\n\n")
    ordered = sorted(findings,
                     key=lambda f: (-severity_rank(f.get("severity", "major")),
                                    not f.get("is_correlated"), f["line_no"]))
    return header + "\n\n".join(format_finding(f) for f in ordered) + "\n"


RULE_CHECK_PREFIX = "RuleCheck_"


def extract_suspicious_context(raw_text, context_before=10, context_after=5, findings=None):
    """
    원본 텍스트에서 문제 블록을 찾고, 그 주변(앞뒤 N줄) 문맥을 포함한 텍스트로 추출하여 반환합니다.
    AI 분석 시 컨텍스트 크기를 줄이기 위해 사용됩니다.

    AI에게 넘길 때는 상관분석으로 만들어진 복합 finding(원본에 없는 합성 블록)은 제외하고
    실제 원문 위치를 가진 것만 쓴다.
    """
    # findings 를 넘기면 재파싱하지 않는다 — AI 분석 경로는 이미 캐시된 판정을 갖고 있다.
    source = findings if findings is not None else analyze_text(raw_text)
    findings = [f for f in source if not f.get("is_correlated")]
    if not findings:
        return ""

    lines = raw_text.splitlines()
    context_blocks = []

    for f in findings:
        line_no = f['line_no'] - 1  # 0-indexed
        start_idx = max(0, line_no - context_before)
        end_idx = min(len(lines), line_no + len(f['block']) + context_after)

        block_text = f"--- Context around {f['keyword']} at line {f['line_no']} ---\n"
        if f['command']:
            block_text += f"Command: {f['command']}\n"
        block_text += "\n".join(lines[start_idx:end_idx])
        context_blocks.append(block_text)

    return "\n\n".join(context_blocks)


def run_analysis(original_dir, problem_dir, prefix=RULE_CHECK_PREFIX, progress_callback=None):
    """original_dir(점검 회차의 raw)의 모든 .txt를 분석해 problem_dir에 저장.
    파일명은 "{접두어}{원본파일명}_problems.txt" — 같은 원본을 규칙기반/로컬AI/클라우드AI로
    각각 분석해도 서로 덮어쓰지 않고 나란히 남게 하기 위함.
    progress_callback(done, total, filename)이 주어지면 파일 하나 처리할 때마다 호출한다
    (설정 화면의 분석 진행바 갱신용 — 백그라운드 스레드에서 호출되므로 콜백은 부수효과만
    수행해야 하며 예외를 던지면 안 된다).
    반환: [{"source", "problem_count", "severity_counts", "output"}] — 파일명순."""
    reload_rules()  # 실행 시점의 config/log_rules.json을 반영(설정 변경 후 재시작 불필요)
    results = []
    paths = sorted(glob.glob(os.path.join(original_dir, "*.txt")))
    total = len(paths)
    for i, path in enumerate(paths):
        # 캐시를 거친다 — 점검 직후 api/terminal_inspection_api.py 가 같은 파일들을 곧바로
        # 다시 파싱하는데(경고 목록 생성), 그 두 번째 패스가 이 캐시로 사실상 공짜가 된다.
        #
        # 디코딩도 여기서 바뀐다: 예전에는 open(encoding="utf-8", errors="replace") 라
        # cp949 로 저장된 레거시 로그를 이 경로만 깨진 문자로 읽었다(다른 읽기 경로는 모두
        # cp949 를 시도한다). core/text_io.py 규칙으로 통일하면 그런 파일의 판정이
        # 달라진다 — 틀렸던 것이 맞게 되는 방향이다.
        findings = log_cache.cached_findings(path)

        out_name = None
        if findings:
            os.makedirs(problem_dir, exist_ok=True)
            fname = os.path.basename(path)
            fname_body = fname[:-4] if fname.endswith(".txt") else fname
            if fname_body.startswith("AutoCheck_"):
                body_no_prefix = fname_body[len("AutoCheck_"):]
                parts = body_no_prefix.rsplit("_", 2)
                if len(parts) == 3:
                    stamp, device = f"{parts[1]}_{parts[2]}", parts[0]
                else:
                    stamp, device = "unknown_time", body_no_prefix
            else:
                parts = fname_body.split("_", 3)
                if len(parts) == 4:
                    stamp, device = f"{parts[0]}_{parts[1]}", parts[3]
                else:
                    stamp, device = "unknown_time", fname_body

            out_name = f"{prefix}{stamp}_{device}_problems.txt"
            with open(os.path.join(problem_dir, out_name), "w", encoding="utf-8") as f:
                f.write(format_report(os.path.basename(path), findings))
        results.append({"source": os.path.basename(path), "problem_count": len(findings),
                        "severity_counts": summarize(findings), "output": out_name})
        if progress_callback:
            try:
                progress_callback(i + 1, total, os.path.basename(path))
            except Exception:
                pass
    return results
