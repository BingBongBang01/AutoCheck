"""
내장 규칙 엔진 — 원본 로그(터미널 세션 전문)를 '맥락을 아는' 방식으로 판정한다.

기존 engine/log_analysis.py는 키워드 하나만 걸리면 무조건 이상으로 올렸다. 실제 로그를
대조해 보면 그 방식은 두 방향으로 동시에 틀린다:

  오탐(False Positive)
    "Number of table drops    : 0"            -> 값이 0인데 DROPS 키워드로 검출
    "hitless-reload-down   Disabled   300"    -> errdisable 기능 목록표인데 DOWN으로 검출
    "Interfaces that will be enabled at the next timeout:"  -> 안내문인데 TIMEOUT으로 검출
    "   U - In Use    D - Down"               -> port-channel 플래그 범례인데 DOWN으로 검출

  미탐(False Negative)
    "NTP is disabled."                        -> 키워드가 하나도 없어서 통과
    "% Invalid input" / "% Unavailable command" -> 점검 명령 자체가 실패했는데 통과
    "The system rebooted due to unknown reasons." -> 통과
    "Et4   12   0   0 ..." (counters errors 표의 0이 아닌 칸) -> 통과
    같은 인터페이스에서 반복되는 up/down (플래핑) -> 개별 줄로만 보여 심각도 판단 불가

원인은 판정에 '맥락'이 없기 때문이다. 그래서 이 엔진은 판정을 4개 층으로 나눈다:

  1층 ContextTracker — 지금 어떤 명령의 출력을 읽고 있는지(프롬프트 줄 파싱), 표의
      헤더/구분선/범례 안인지를 추적한다. 이게 없으면 2~3층 규칙을 쓸 수가 없다.
  2층 Suppressor    — 맥락상 정상인 줄을 걷어낸다(0인 카운터, 기능 목록표, 설정 원문, 범례).
  3층 Signature     — 키워드가 없어도 잡아야 하는 패턴을 정규식 서명으로 잡고, syslog
      facility 심각도(%FAC-N-NAME의 N)를 읽어 등급을 매긴다. 카운터 표는 헤더와 열을
      맞춰 0이 아닌 칸만 잡는다.
  4층 Correlator    — 개별 findings를 인터페이스/이벤트 단위로 묶어 중복을 접고(throttle),
      "errdisable 직후 link down" 같은 복합 조건을 상위 심각도 finding으로 승격시킨다.

모든 판정 데이터(키워드/억제/서명/상관규칙/심각도)는 config/log_rules.json에 있고 코드에는
'해석 방법'만 있다 — 새 장비 벤더를 붙일 때 코드 수정 없이 JSON만 늘리면 되게 하기 위함.
"""
import os
import re
import json

# --------------------------------------------------------------------------- 심각도

SEVERITY_ORDER = ["info", "minor", "major", "critical"]

# --------------------------------------------------------------------------- 줄 단위 메모
# 판정 핫패스(match_signature / find_keyword)는 같은 줄을 반복해서 본다 — 장비마다 같은
# 인터페이스 표가 나오기 때문이다. 메모는 RuleEngine 인스턴스에 붙고, get_engine() 이 프로세스
# 싱글턴이므로 **점검 회차 전체(장비 N대 파일)가 하나의 메모를 공유**한다. 이게 핵심이다:
# 파일마다 메모를 새로 만들면 이득이 1.05배로 사라지고(한 파일 안에서는 포트 번호가 달라
# 줄이 대부분 고유하다), 회차를 공유하면 장비 8대에서 3.2배, 20대에서 3.9배가 된다.
_MEMO_MISS = object()

# 메모 엔트리 상한. 줄 하나가 평균 100~200바이트라고 보면 5만 줄은 수~십 MB 규모로,
# 로그 전문을 이미 메모리에 들고 있는 이 앱에서는 부담이 되지 않는다. 상한이 없으면
# 고유 줄이 수백만인 로그(타임스탬프가 전부 다른 syslog 덤프)에서 메모리가 계속 늘어난다.
_MEMO_MAX_ENTRIES = 50_000


def severity_rank(sev):
    try:
        return SEVERITY_ORDER.index(sev)
    except ValueError:
        return SEVERITY_ORDER.index("major")


def max_severity(a, b):
    return a if severity_rank(a) >= severity_rank(b) else b


# --------------------------------------------------------------------------- 규칙 로딩

_DEFAULT_RULES = {
    "anomaly_keywords": ["FAIL", "ERROR", "CRITICAL", "DOWN", "CRC", "DROPS",
                         "ERR-DISABLED", "TIMEOUT", "UNREACHABLE"],
    "benign_phrases": ["action error", "level critical", "idle-timeout"],
    "keyword_severity": {},
    "default_severity": "major",
    "counter_columns": [],
    "suppressions": [],
    "signatures": [],
    "correlation_rules": [],
    "throttle": {"max_samples_per_group": 3},
}


def _rule_file_path():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config", "log_rules.json")


def load_rules(path=None):
    """config/log_rules.json을 읽어 기본값과 병합한 dict를 반환.
    파일이 없거나 깨져도 기본 키워드 세트로 동작해야 하므로 예외를 삼킨다."""
    merged = dict(_DEFAULT_RULES)
    try:
        with open(path or _rule_file_path(), "r", encoding="utf-8-sig") as f:
            merged.update(json.load(f) or {})
    except Exception:
        pass
    return merged


def _compile_patterns(entries):
    """[{pattern, scope, ...}] -> [(compiled_pattern, compiled_scope|None, entry)]
    scope는 '이 규칙이 어떤 show 명령의 output에서만 유효한지'를 나타내는 정규식이다.

    예전에는 이 함수가 `except Exception: continue` 로 모든 실패를 삼켰다. 그래서 두 가지가
    구별되지 않았다:

      * 주석 엔트리 — config/log_rules.json 의 signatures 배열에는 `{"_comment": "..."}` 만
        담긴 항목이 2개 있다. 배열 순서가 곧 우선순위이므로(먼저 맞는 것이 이긴다) 그 순서
        제약을 **그 위치에서** 설명하는 문서다. 배열 밖으로 옮기면 어느 규칙에 대한 설명인지
        알 수 없게 되므로 그대로 두고, 여기서 조용히 건너뛴다.
      * 정규식 오타 — 이쪽은 조용히 사라지면 안 된다. 규칙 하나가 아무 신호 없이 빠지고,
        그 규칙이 잡던 장애를 앱이 '이상 없음'으로 보고한다. 점검 도구에서 가장 나쁜 실패다.

    그래서 주석은 건너뛰고, 컴파일 실패는 경고를 남긴다. print 를 쓰는 이유는
    core/app_logger.py 의 install_print_capture() 가 print 를 앱 로그로 캡처하기 때문이다.
    """
    compiled = []
    for entry in entries or []:
        if "pattern" not in entry:
            # 주석 전용 항목 — 위치로 순서 제약을 설명한다. 경고할 일이 아니다.
            continue
        try:
            rx = re.compile(entry["pattern"], re.IGNORECASE)
        except re.error as exc:
            print(f"[규칙 오류] 서명 '{entry.get('id', '(id 없음)')}' 의 pattern 을 컴파일할 수 "
                  f"없어 이 규칙을 건너뜁니다: {exc} / pattern={entry['pattern']!r}")
            continue

        scope = None
        if entry.get("scope"):
            try:
                scope = re.compile(entry["scope"], re.IGNORECASE)
            except re.error as exc:
                # scope 가 None 이 되면 규칙이 '모든 명령'에 적용된다 — 좁히려던 규칙이
                # 넓어지는 것이므로 조용히 넘기면 오탐이 늘어난다.
                print(f"[규칙 오류] 서명 '{entry.get('id', '(id 없음)')}' 의 scope 를 컴파일할 수 "
                      f"없어 명령 범위 제한 없이 적용됩니다: {exc} / scope={entry['scope']!r}")
        compiled.append((rx, scope, entry))
    return compiled


# --------------------------------------------------------------------------- 1층: 맥락 추적

# 터미널 전문의 명령 경계는 "Core1(config)#show interfaces" 같은 프롬프트 줄이다.
# 예전 FSM은 '--- cmd ---' 헤더만 명령 경계로 봤기 때문에 실제 수집 로그에서는 명령 맥락이
# 항상 None이었고, 그래서 명령별 규칙을 아예 쓸 수 없었다. 둘 다 인식한다.
_PROMPT_RE = re.compile(r"^[A-Za-z0-9][\w.\-]*(?:\([^)]*\))?\s*[>#]\s*(.*)$")
_LEGACY_HEADER_RE = re.compile(r"^--- (.+?) ---\s*$")
# "----- ----- -----" 같은 표 구분선(열 사이 공백 포함). 이 줄 바로 위가 헤더다.
_SEPARATOR_RE = re.compile(r"^\s*[-=+]{3,}[\s\-=+|]*$")
# "   U - In Use" 같은 플래그 범례 줄.
_LEGEND_RE = re.compile(r"^\s{2,}\S{1,3}\s+-\s+\S")
_SYSLOG_RE = re.compile(r"%(?P<facility>[A-Z0-9_]+)-(?P<sev>[0-7])-(?P<mnemonic>[A-Z0-9_]+)\s*:?")
_SYSLOG_TS_RE = re.compile(r"^\w{3}\s+\d{1,2}\s+(\d{2}):(\d{2}):(\d{2})\s+(\S+)\s+")
_INTERFACE_RE = re.compile(
    r"\b((?:Ethernet|Et|Port-Channel|Po|Vlan|Vl|Management|Ma|GigabitEthernet|Gi|TenGigabitEthernet|Te)\s?[\d/.:]+)\b",
    re.IGNORECASE)

# syslog facility 심각도(0=emerg .. 7=debug) -> 내부 등급.
_SYSLOG_SEVERITY_MAP = {0: "critical", 1: "critical", 2: "critical",
                        3: "major", 4: "major", 5: "minor", 6: "info", 7: "info"}


class ContextTracker:
    """줄 단위로 먹여주면 '지금 어디를 읽고 있는지'를 알려주는 상태기계.

    command       : 현재 출력의 원본 명령(프롬프트에서 파싱). 없으면 None.
    is_config     : running-config / startup-config 출력 안인가(= 설정 원문이라 상태가 아님).
    header_tokens : 직전 구분선 위 헤더 줄의 토큰들(카운터 표 열 정렬용). 없으면 None.
    """

    _CONFIG_CMD_RE = re.compile(r"(running-config|startup-config)", re.IGNORECASE)

    def __init__(self):
        self.command = None
        self.is_config = False
        self.header_tokens = None
        self._prev_line = None
        self._last_nonempty_indent0 = None

    def is_command_line(self, line):
        return bool(_LEGACY_HEADER_RE.match(line) or _PROMPT_RE.match(line))

    def feed(self, line):
        """줄을 하나 소비하고 True를 반환하면 '이 줄 자체는 판정 대상이 아님'(명령/구분선/헤더)."""
        legacy = _LEGACY_HEADER_RE.match(line)
        prompt = _PROMPT_RE.match(line)
        if legacy or prompt:
            cmd = (legacy.group(1) if legacy else prompt.group(1)).strip()
            # "Core1(config)#!" 처럼 명령이 비었거나 '!'만 있는 줄은 구분자일 뿐 명령이 아니다.
            if cmd and cmd != "!":
                self.command = cmd
                self.is_config = bool(self._CONFIG_CMD_RE.search(cmd))
            self.header_tokens = None
            self._prev_line = line
            return True

        if _SEPARATOR_RE.match(line) and line.strip():
            # 구분선 위 줄이 헤더 -> 카운터 표 열 정렬에 쓴다.
            self.header_tokens = (self._prev_line or "").split() or None
            self._prev_line = line
            return True

        self._prev_line = line
        if not line.strip():
            self.header_tokens = None
        return False

    def is_legend(self, line):
        return bool(_LEGEND_RE.match(line))


# --------------------------------------------------------------------------- 2층: 억제

_NUM_RE = re.compile(r"^-?[\d,]+$")


def _as_number(token):
    if not _NUM_RE.match(token or ""):
        return None
    try:
        return int(token.replace(",", ""))
    except ValueError:
        return None


class Suppressor:
    """'키워드는 걸렸지만 맥락상 정상'인 줄을 걸러낸다. 반환값은 억제 사유(str) 또는 None."""

    # "0 input errors", "Number of table drops : 0" 처럼 카운터 이름 앞뒤에 붙은 수치를 읽는다.
    _COUNTER_BEFORE = r"(?P<n>\d[\d,]*)\s+(?:\w+[\s-]+){0,2}%s\b"
    _COUNTER_AFTER = r"%s[^:=\d\n]{0,24}[:=]\s*(?P<n>\d[\d,]*)"
    # "lacp-no-portid   Disabled   N/A" 같은 기능 상태 목록표 행.
    _FEATURE_ROW_RE = re.compile(r"^\s*[\w.\-]+\s+(?:Disabled|Enabled|Inactive|Not\s+configured)\s+"
                                 r"(?:\d+|N/?A|-)\s*$", re.IGNORECASE)

    def __init__(self, rules):
        self.benign_phrases = [p.lower() for p in rules.get("benign_phrases", [])]
        self.patterns = _compile_patterns(rules.get("suppressions"))
        self.counter_columns = {c.upper() for c in rules.get("counter_columns", [])}

    def counter_value(self, line, keyword):
        """줄에서 keyword에 결합된 카운터 수치를 읽는다. 카운터 표현이 아니면 None.
        여러 개면 최댓값(하나라도 0이 아니면 실제 문제이므로)."""
        if keyword.upper() not in self.counter_columns:
            return None
        kw = re.escape(keyword)
        values = []
        for tmpl in (self._COUNTER_BEFORE, self._COUNTER_AFTER):
            for m in re.finditer(tmpl % kw, line, re.IGNORECASE):
                v = _as_number(m.group("n"))
                if v is not None:
                    values.append(v)
        return max(values) if values else None

    def check(self, line, keyword=None, ctx=None, include_config_scope=True):
        """억제 사유를 반환(없으면 None).

        include_config_scope=False로 부르면 'running-config 구간이라 억제'만 건너뛴다 —
        명시 서명(3층)은 스스로 scope 조건을 갖고 있으므로 설정 구간이라는 이유만으로
        지워버리면 안 되지만, 0인 카운터·기능 목록표·범례 같은 구조적 억제는 서명에도
        똑같이 적용되어야 하기 때문이다("Ports errdisabled : False" 같은 줄)."""
        lowered = line.lower()
        for phrase in self.benign_phrases:
            if phrase in lowered:
                return f"정상 문구 '{phrase}' 포함"

        if ctx is not None and ctx.is_legend(line):
            return "플래그 범례 줄"

        if self._FEATURE_ROW_RE.match(line):
            return "기능 상태 목록표 행(Disabled/Enabled 열)"

        if keyword:
            value = self.counter_value(line, keyword)
            if value == 0:
                return f"카운터 '{keyword}' 값이 0"

        if include_config_scope and ctx is not None and ctx.is_config:
            # running-config는 '지금 상태'가 아니라 '설정 의도'다. 여기서 나온 shutdown/errdisable
            # 문구를 장애로 올리면 정상 장비도 매번 경고가 뜬다.
            return "설정 원문(running-config/startup-config) 구간"

        command = (ctx.command if ctx else "") or ""
        for rx, scope, entry in self.patterns:
            if scope and not scope.search(command):
                continue
            if rx.search(line):
                return entry.get("reason", entry.get("id", "억제 규칙 일치"))
        return None


# --------------------------------------------------------------------------- 3층: 서명 판정

class SignatureMatcher:
    """키워드 유무와 무관하게 잡아야 하는 패턴 + syslog 심각도 + 카운터 표 열 검사."""

    def __init__(self, rules):
        self.signatures = _compile_patterns(rules.get("signatures"))
        self.counter_columns = {c.upper() for c in rules.get("counter_columns", [])}
        self.keyword_severity = {k.upper(): v for k, v in (rules.get("keyword_severity") or {}).items()}
        self.default_severity = rules.get("default_severity", "major")

        # --- 줄 단위 메모 (핫패스) ---
        # match_signature 는 줄마다 서명 31개를 순차로 훑는다. 실측으로 evaluate() 시간의
        # 약 56% 를 여기서 쓴다. 실제 수집 로그는 장비마다 같은 표(포트 48개)를 반복하므로
        # 중복률이 높고(합성 현실 코퍼스 73%), 같은 줄을 다시 판정하는 것이 대부분이다.
        #
        # 안전성: 이 함수가 ctx 를 읽는 곳은 `if scope and not scope.search(ctx.command)` 뿐이다.
        # 모든 서명의 scope 가 None 이면 그 분기는 절대 실행되지 않으므로 '줄 문자열만의 순수
        # 함수'가 되어 메모가 안전하다. scope 를 가진 서명이 하나라도 생기면 메모를 끈다.
        self._memo = {}
        # scope 를 가진 서명이 하나라도 있으면 메모가 정확하지 않다 — 그때는 아예 못 켜게 한다.
        self._memo_safe = all(scope is None for _rx, scope, _entry in self.signatures)
        # 실제로 메모를 쓸지. 정확성 게이트(_memo_safe)와 사용자 스위치를 곱한 값이다.
        self._memo_requested = True

    @property
    def memo_enabled(self):
        return self._memo_safe and self._memo_requested

    def set_memo_enabled(self, enabled):
        """벤치마크/테스트용 스위치. _memo_safe 가 False 면 켜도 켜지지 않는다."""
        self._memo_requested = bool(enabled)

    def match_signature(self, line, ctx):
        if not self.memo_enabled:
            return self._match_signature_uncached(line, ctx)
        hit = self._memo.get(line, _MEMO_MISS)
        if hit is _MEMO_MISS:
            hit = self._match_signature_uncached(line, ctx)
            # 상한에 닿으면 더 담지 않는다. 캐시를 비우지 않는 이유: 고유 줄이 아주 많은
            # 로그에서 비우고 다시 채우기를 반복하면(진동) 캐시 없는 것보다 느려진다.
            if len(self._memo) < _MEMO_MAX_ENTRIES:
                self._memo[line] = hit
        return hit

    def _match_signature_uncached(self, line, ctx):
        for rx, scope, entry in self.signatures:
            if scope and not scope.search(ctx.command or ""):
                continue
            if rx.search(line):
                return {
                    "rule_id": entry.get("id", "signature"),
                    "severity": entry.get("severity", self.default_severity),
                    "category_tag": entry.get("category", "general"),
                    "reason": entry.get("title", entry.get("id", "서명 일치")),
                }
        return None

    def syslog_severity(self, line):
        """'%LINEPROTO-5-UPDOWN' 의 5를 읽어 등급으로 환산. syslog 줄이 아니면 None."""
        m = _SYSLOG_RE.search(line)
        if not m:
            return None
        return {
            "severity": _SYSLOG_SEVERITY_MAP.get(int(m.group("sev")), "minor"),
            "facility": m.group("facility"),
            "mnemonic": m.group("mnemonic"),
        }

    def counter_row(self, line, ctx):
        """'show interfaces counters errors' 처럼 헤더에 카운터 이름이 늘어선 표에서
        0이 아닌 칸만 골라낸다. 키워드가 줄에 없어서 예전 로직이 통째로 놓치던 미탐 구간.
        반환: [(열이름, 값)] 또는 []"""
        header = ctx.header_tokens
        if not header:
            return []
        if sum(1 for h in header if h.upper() in self.counter_columns) < 2:
            return []
        tokens = line.split()
        if len(tokens) != len(header):
            return []
        hits = []
        for name, tok in zip(header, tokens):
            if name.upper() not in self.counter_columns:
                continue
            v = _as_number(tok)
            if v:
                hits.append((name, v))
        return hits

    def keyword_sev(self, keyword):
        return self.keyword_severity.get(keyword.upper(), self.default_severity)


# --------------------------------------------------------------------------- 4층: 상관분석

def _normalize_for_dedupe(text):
    """중복 판정을 위해 타임스탬프/호스트명/숫자를 지운 뼈대만 남긴다 —
    같은 원인이 시간만 바꿔가며 수백 줄 반복되는 걸 하나로 접기 위함."""
    text = _SYSLOG_TS_RE.sub("", text.strip())
    return re.sub(r"\d+", "#", text)


def extract_interface(text):
    m = _INTERFACE_RE.search(text or "")
    if not m:
        return None
    return re.sub(r"\s+", "", m.group(1)).lower()


def _seconds_of(text):
    m = _SYSLOG_TS_RE.match(text or "")
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))


class Correlator:
    """개별 finding들을 묶는 층. 두 가지 일을 한다.

    1) 스로틀링 — (rule_id, 정규화된 문구) 가 같은 findings를 하나로 접고 반복 횟수만 남긴다.
       보고서가 지적한 '경보 피로'의 직접적 원인이 이 반복이었다.
    2) 복합 규칙 — config의 correlation_rules에 따라 "같은 인터페이스에서 A와 B가 시간창
       안에 함께 발생" 조건을 검사해 상위 심각도의 복합 finding을 만든다.
    """

    def __init__(self, rules):
        self.max_samples = int((rules.get("throttle") or {}).get("max_samples_per_group", 3))
        self.correlation_rules = rules.get("correlation_rules") or []

    def throttle(self, findings):
        groups = {}
        ordered = []
        for f in findings:
            # 명령을 키에 포함한다 — "% Invalid input"처럼 문구가 같아도 서로 다른 show
            # 명령에서 나왔다면 원인이 다른 별개 문제이므로 하나로 접으면 안 된다.
            key = (f.get("command"), f["rule_id"],
                   _normalize_for_dedupe(f["block"][0] if f["block"] else ""))
            if key in groups:
                g = groups[key]
                g["repeat"] += 1
                g["last_line_no"] = f["line_no"]
                g["severity"] = max_severity(g["severity"], f["severity"])
                continue
            f = dict(f, repeat=1, last_line_no=f["line_no"])
            groups[key] = f
            ordered.append(f)
        return ordered

    def correlate(self, findings):
        """복합 조건에 걸린 findings를 찾아 새 finding들을 만들어 앞에 붙인다."""
        composites = []
        for rule in self.correlation_rules:
            composites.extend(self._apply(rule, findings))
        return composites

    def _apply(self, rule, findings):
        """한 상관규칙을 적용. 지원하는 조건 표현:

          all_of      : 이 rule_id/category가 전부 있어야 한다 (기존)
          any_of      : 이 중 하나라도 있으면 그 자리를 채운 것으로 본다 (신규)
                        — VLAN 삭제가 SVI를 내리는 연쇄에서 '게이트웨이 영향'은 VRRP일 수도
                          VARP일 수도 있어서, 둘 중 하나만 나와도 성립해야 한다.
          min_count   : 묶음의 총 반복 횟수 하한 (기존, 플래핑 탐지용)
          scope       : "interface"(기본) | "global"
                        — 오버레이/피어링 장애는 인터페이스 이름이 안 붙는 줄이 많아,
                          인터페이스별로 쪼개면 조건이 절대 모이지 않는다. global은 파일 전체를
                          한 묶음으로 본다.

        all_of와 any_of는 함께 쓸 수 있다(all_of를 만족하고 any_of 중 하나도 있어야 성립).
        """
        required = rule.get("all_of") or []
        any_of = rule.get("any_of") or []
        if not required and not any_of:
            return []
        window = int(rule.get("window_seconds", 300))
        min_count = int(rule.get("min_count", 1))
        global_scope = (rule.get("scope") or "interface") == "global"
        watched = set(required) | set(any_of)

        # 인터페이스별(기본)로 모아 조건을 본다. scope:global이면 전부 한 묶음.
        buckets = {}
        for f in findings:
            tags = {f["rule_id"], f.get("category_tag", "")}
            if not (tags & watched):
                continue
            key = "(전역)" if global_scope else (
                extract_interface(f["block"][0] if f["block"] else "") or "(전역)")
            buckets.setdefault(key, []).append(f)

        out = []
        for key, group in buckets.items():
            present = {f["rule_id"] for f in group} | {f.get("category_tag", "") for f in group}
            if required and not set(required).issubset(present):
                continue
            if any_of and not (set(any_of) & present):
                continue
            times = [t for t in (_seconds_of(f["block"][0] if f["block"] else "") for f in group)
                     if t is not None]
            if times and (max(times) - min(times)) > window:
                continue
            if sum(f.get("repeat", 1) for f in group) < min_count:
                continue
            # 어떤 서명들이 이 결론을 뒷받침했는지 남긴다 — 상관 결과만 보여주면
            # 왜 그렇게 판정했는지 되짚을 수 없다.
            matched = sorted(present & watched)
            reason = f"{rule.get('title', '복합 조건 성립')} (대상: {key}"
            reason += f" / 근거: {', '.join(matched)})" if matched else ")"
            out.append({
                "command": group[0].get("command"),
                "category": group[0].get("category"),
                "keyword": rule.get("id", "CORRELATED"),
                "rule_id": rule.get("id", "correlated"),
                "category_tag": rule.get("category", "correlation"),
                "severity": rule.get("severity", "critical"),
                "reason": reason,
                # Module 3 — 실시간 경고에 붙일 '원인 의도' 문구. config에서 지정할 수 있게 하고
                # 없으면 title을 쓴다.
                "root_cause_intent": rule.get("root_cause_intent") or rule.get("title", ""),
                "matched_rules": matched,
                "line_no": min(f["line_no"] for f in group),
                "last_line_no": max(f.get("last_line_no", f["line_no"]) for f in group),
                "repeat": 1,
                "is_correlated": True,
                "block": [f["block"][0] for f in group if f["block"]][:6],
            })
        return out


# --------------------------------------------------------------------------- 통합 판정기

class RuleEngine:
    """1~4층을 조립한 진입점. analyze_text()가 이걸 쓴다."""

    def __init__(self, rules=None):
        self.rules = rules or load_rules()
        self.anomaly_keywords = self.rules.get("anomaly_keywords", [])
        self.keyword_res = {
            # \b는 밑줄을 단어문자로 봐서 "NEIGHBOR_TIMEOUT"의 TIMEOUT을 놓친다 — 앞뒤 알파벳
            # 여부만 보는 룩어라운드로 대체(SHUTDOWN 속 DOWN은 여전히 제외).
            kw: re.compile(r"(?<![A-Za-z])" + re.escape(kw) + r"(?![A-Za-z])", re.IGNORECASE)
            for kw in self.anomaly_keywords
        }
        self.suppressor = Suppressor(self.rules)
        self.signatures = SignatureMatcher(self.rules)
        self.correlator = Correlator(self.rules)

        # find_keyword 는 ctx 를 아예 받지 않으므로 언제나 줄만의 순수 함수다 — 조건 없이
        # 메모할 수 있다(서명 쪽 _memo_safe 게이트가 필요 없는 이유).
        self._keyword_memo = {}
        self._keyword_memo_enabled = True

    def find_keyword(self, line):
        if not self._keyword_memo_enabled:
            return next((kw for kw, rx in self.keyword_res.items() if rx.search(line)), None)
        hit = self._keyword_memo.get(line, _MEMO_MISS)
        if hit is _MEMO_MISS:
            hit = next((kw for kw, rx in self.keyword_res.items() if rx.search(line)), None)
            if len(self._keyword_memo) < _MEMO_MAX_ENTRIES:
                self._keyword_memo[line] = hit
        return hit

    def memo_stats(self):
        """메모 상태 — 진단/테스트용. 상한에 닿았는지 확인할 수 있어야 한다."""
        return {
            "signature_entries": len(self.signatures._memo),
            "signature_memo_enabled": self.signatures.memo_enabled,
            "keyword_entries": len(self._keyword_memo),
            "keyword_memo_enabled": self._keyword_memo_enabled,
            "max_entries": _MEMO_MAX_ENTRIES,
        }

    def clear_memo(self):
        """메모를 비운다 — 벤치마크가 '파일 단위'와 '회차 공유'를 구분해 재려면 필요하다."""
        self.signatures._memo.clear()
        self._keyword_memo.clear()

    def set_memo_enabled(self, enabled):
        """메모를 켜고 끈다 — 벤치마크의 before/after 비교와 결과 동일성 테스트용.

        운영 코드가 부를 일은 없다. 이 스위치가 있어야 "메모가 판정을 바꾸지 않는다"를
        같은 엔진으로 직접 대조할 수 있고, 벤치마크도 '메모 없는 기준선'을 정직하게 잴 수 있다.
        끄면 메모도 비운다 — 껐다 켰을 때 낡은 항목이 남아 있으면 안 된다.

        주의: 서명 메모는 scope 를 가진 서명이 하나라도 있으면 enabled=True 로도 켜지지 않는다
        (정확성 게이트가 우선한다).
        """
        self._keyword_memo_enabled = bool(enabled)
        self.signatures.set_memo_enabled(enabled)
        self.clear_memo()

    def evaluate(self, line, ctx):
        """한 줄을 판정. 이상이면 dict, 아니면 None.
        반환 dict: {keyword, severity, rule_id, category_tag, reason}

        판정 순서: 구조적 억제(0인 값·목록표·범례) -> 서명 -> 카운터 표 -> 키워드.
        구조적 억제는 서명에도 적용하되, 'running-config 구간' 억제만은 서명 다음으로
        미룬다 — 명시 서명은 스스로 scope 조건을 갖고 있기 때문."""
        if not line.strip():
            return None

        if self.suppressor.check(line, None, ctx, include_config_scope=False):
            return None

        sig = self.signatures.match_signature(line, ctx)
        if sig:
            return dict(sig, keyword=sig["rule_id"])

        counter_hits = self.signatures.counter_row(line, ctx)
        if counter_hits:
            detail = ", ".join(f"{n}={v}" for n, v in counter_hits)
            return {"keyword": "COUNTER", "severity": "major", "rule_id": "counter_nonzero",
                    "category_tag": "interface",
                    "reason": f"카운터 표에서 0이 아닌 값 검출 ({detail})"}

        keyword = self.find_keyword(line)
        if not keyword:
            return None

        suppressed = self.suppressor.check(line, keyword, ctx)
        if suppressed:
            return None

        severity = self.signatures.keyword_sev(keyword)
        reason = f"키워드 '{keyword}' 검출"

        # syslog 줄이면 facility 심각도가 키워드 추정보다 정확하다 — 그쪽을 신뢰한다.
        syslog = self.signatures.syslog_severity(line)
        if syslog:
            severity = syslog["severity"]
            reason = (f"syslog {syslog['facility']}-{syslog['mnemonic']} "
                      f"(심각도 {severity}) / 키워드 '{keyword}'")

        value = self.suppressor.counter_value(line, keyword)
        if value:
            reason = f"카운터 '{keyword}' 값 {value} (0 초과)"

        return {"keyword": keyword, "severity": severity, "rule_id": f"keyword_{keyword.lower()}",
                "category_tag": "general", "reason": reason}


_engine = None


def get_engine(reload=False):
    """프로세스 단위 싱글턴 — 규칙 정규식 컴파일을 매 파일마다 반복하지 않기 위함.
    설정 화면에서 log_rules.json을 고친 뒤에는 reload=True로 다시 읽는다."""
    global _engine
    if _engine is None or reload:
        _engine = RuleEngine()
    return _engine
