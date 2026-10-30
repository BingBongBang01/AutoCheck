"""웹 UI 이스케이프 — IMPROVEMENT_PLAN 1-3.

화면(web_ui/js)은 innerHTML 템플릿으로 그린다. 장비명·IP·계정은 장비 hostname 으로 자동 갱신되고
엑셀로도 들어오며, 로그·파일명·AI 응답도 외부 값이다. 이 값이 이스케이프 없이 속성·본문에 들어가면
그 화면에서 스크립트가 돌고, 화면은 pywebview 브리지(장비 계정 조회·SSH 세션 입력)를 부를 수 있다.

  1. esc() 가 다섯 글자(&<>"')를 모두 바꾸는지 — node 로 실제 core.js 함수를 돌린다.
  2. 예전 rtEscape/tpEsc 처럼 따옴표를 남기는 구현이 돌아오지 않았는지. 따옴표를 남기면
     data-rtm-resolve="[…]" 안의 JSON 이 잘려 실시간 감시 '해결/무시' 버튼이 동작하지 않았다.
  3. 정적 검사: 속성값 자리(="${…}")의 보간은 이스케이프 함수를 거치거나, 외부 값이 아닌 것으로
     검토된 형태여야 한다 — 새 화면을 만들며 빠뜨리면 실패한다.
  4. 서버 쪽: 장비가 돌려준 hostname 에 호스트명에 쓸 수 없는 글자가 있으면 장비명으로 쓰지 않는다.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from engine.device_probe import extract_hostname

JS_DIR = Path(__file__).resolve().parent.parent / "web_ui" / "js"
ESCAPERS = ("esc(", "escapeHtml(", "escapeAttr(", "rtEscape(", "tpEsc(", "dashEsc(", "escapeReportText(")
NASTY = "<img src=x onerror=\"alert('x')\">&"
EXPECTED = "&lt;img src=x onerror=&quot;alert(&#39;x&#39;)&quot;&gt;&amp;"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 동작 검증을 건너뜁니다")


def _extract(path, start):
    """파일에서 start 로 시작하는 최상위 선언 하나(한 줄짜리 또는 열 0 의 '}' 까지)를 떼어낸다."""
    lines = (JS_DIR / path).read_text(encoding="utf-8").splitlines()
    begin = next(i for i, line in enumerate(lines) if line.startswith(start))
    end = begin
    while not lines[end].rstrip().endswith(("}", "};")):
        end += 1
    return "\n".join(lines[begin:end + 1])


def _run_js(script):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        path = handle.name
    try:
        completed = subprocess.run(["node", path], capture_output=True, encoding="utf-8")
    finally:
        os.unlink(path)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


@needs_node
def test_escape_helpers_encode_all_five_characters():
    script = "\n".join([
        _extract("core.js", "const _ESC_MAP"), _extract("core.js", "function esc("),
        _extract("core.js", "function escapeHtml("), _extract("core.js", "function escapeAttr("),
        _extract("realtime-baseline-alerts.js", "function rtEscape("), _extract("topology.js", "function tpEsc("),
        _extract("dashboard.js", "function dashEsc("), _extract("inspection-report.js", "function escapeReportText("),
        f"const s = {json.dumps(NASTY)};",
        "console.log(JSON.stringify([esc(s), escapeHtml(s), escapeAttr(s), rtEscape(s), tpEsc(s), dashEsc(s),"
        " escapeReportText(s), esc(null), esc(0)]));",
    ])
    *escaped, empty, zero = _run_js(script)
    assert escaped == [EXPECTED] * 7
    assert (empty, zero) == ("", "0")


@needs_node
def test_alert_ids_survive_a_round_trip_through_a_data_attribute():
    """'해결/무시' 버튼 고장의 재현 — 속성값으로 넣은 JSON 을 브라우저가 다시 읽는 과정을 흉내 낸다."""
    script = "\n".join([
        _extract("core.js", "const _ESC_MAP"), _extract("core.js", "function esc("),
        _extract("realtime-baseline-alerts.js", "function rtEscape("),
        'const html = `<button data-ids="${rtEscape(JSON.stringify(["Core1#12", "Agg1#poll3"]))}">`;',
        # 속성값 = 첫 큰따옴표와 다음 큰따옴표 사이. 엔티티를 풀어 dataset 이 돌려줄 값을 만든다.
        "const raw = html.split('\"')[1];",
        "const decoded = raw.replace(/&quot;/g, '\"').replace(/&#39;/g, \"'\")"
        ".replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');",
        "console.log(JSON.stringify(JSON.parse(decoded)));",
    ])
    assert _run_js(script) == ["Core1#12", "Agg1#poll3"]


# 속성값 자리에서 이스케이프 없이 써도 되는 것으로 검토한 식 — 앱이 만든 id·숫자·클래스 이름·상수.
# 여기에 새 형태를 더하려면, 그 값이 장비·로그·파일·사용자 입력·서버 응답에서 오지 않는지 먼저 확인한다.
_SAFE_ATTR = re.compile(
    r"^(?:"
    r"i|idx|index|rank|cols|did|dx|dy|ratio|attr|key|r|color|filled|len|-offset|maxLines"
    r"|[\w.]*(?:id|Id|idx)"                                  # c.id, s.session_id, p.id … (앱이 만든 id)
    r"|[\w.]+\.(?:length|size|count|interval_sec|batch_chars|max_tokens|batch_segs|status|key|field|color)"
    r"|[\w.]+ \|\| \d+"                                      # ctx.check_item_count || 0
    r"|[\w.\[\]]+ \?\? [\d.]+"                               # localCfg.temperature ?? 0.3
    r"|[^`]*\? '[^']*' : '[^']*'"                            # cond ? 'a' : 'b'
    r"|[^`]*\? '' : `height:\$\{\w+\}px`"                    # 박스 높이 스타일
    r"|[A-Z_]+(?:\[[\w.]+\])?(?: \|\| '[\w-]*')?"            # CUSTOM_MODEL, RTM_SEV_CHIP[sev] || 'warn'
    r"|logFileRowClass\(.*\)|CSS\.escape\(.+\)"               # 클래스 이름, 선택자용 이스케이프
    r"|openFn|fn|toneColor|barColor|badge|sevClass|rowClass|kind"
    r"|\(?[\w.]+ \* \d+\)?\.toFixed\(\d\)"
    r")$"
)


def _attribute_interpolations(text):
    """="${식}" 형태의 보간에서 식만 뽑는다(중괄호 짝을 센다)."""
    for lineno, line in enumerate(text.splitlines(), 1):
        start = 0
        while (pos := line.find('="${', start)) != -1:
            depth, j = 1, pos + 4
            while j < len(line) and depth:
                depth += {"{": 1, "}": -1}.get(line[j], 0)
                j += 1
            yield lineno, line[pos + 4: j - 1].strip()
            start = j


def test_attribute_values_are_escaped():
    violations = []
    for path in sorted(JS_DIR.glob("*.js")):
        for lineno, expr in _attribute_interpolations(path.read_text(encoding="utf-8")):
            if expr.startswith(ESCAPERS) or _SAFE_ATTR.match(expr):
                continue
            violations.append(f"{path.name}:{lineno}: {expr}")
    assert violations == [], "이스케이프 없이 속성값에 들어가는 보간:\n" + "\n".join(violations)


@pytest.mark.parametrize("output, expected", [
    ("Hostname: core1\nFQDN: core1.lab.local", "core1"),
    ("hostname Spine-01", "Spine-01"),
    ('Hostname: <img src=x onerror="alert(1)">', None),
    ("hostname a\"b", None),
])
def test_device_reported_hostname_must_be_a_hostname(output, expected):
    """장비가 돌려준 hostname 이 장비 목록 이름이 된다 — 호스트명에 쓸 수 없는 글자면 받지 않는다."""
    assert extract_hostname(output) == expected
