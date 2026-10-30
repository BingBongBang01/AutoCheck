"""로그 글을 끌어서 고르면 바로 복사한다.

pywebview 는 text_select=False(기본)이면 body 에 user-select:none 을 넣어 앱 안의 글을 끌어서 고를 수 없었다. 앱 전체는
그대로 두고 로그를 보여 주는 자리에만 .selectable-log 를 붙여 다시 고르게 하고, 거기서 고른 글은 core.js 가 바로
클립보드에 복사한다. 실제 끌기·두 번 누르기·다른 곳 누르기는 브라우저에서 확인했다 — 여기서는 연결이 빠지지 않게 본다.
"""
import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web_ui"
JS = WEB / "js"


def test_selectable_log_reenables_text_selection():
    css = (WEB / "style.css").read_text(encoding="utf-8")
    rule = re.search(r"\.selectable-log\s*\{([^}]*)\}", css)
    assert rule and "user-select: text" in rule.group(1) and "cursor: text" in rule.group(1)


@pytest.mark.parametrize("name, markup", [
    ("common.js", '<pre class="mono terminal logview-pre selectable-log"'),          # 점검 로그·원본로그분석·마스킹 결과
    ("logs.js", '<pre id="logs-view" class="selectable-log"'),                       # 전체 로그
    ("collection-log-analysis.js", '<pre class="mono selectable-log" data-ai-tx-text'),   # 클라우드 AI 로 보낼 글
    ("collection-log-masking.js", 'class="mono mask-before selectable-log"'),        # 마스킹 미리보기
    ("collection-log-masking.js", 'class="mono mask-after selectable-log"'),
    ("realtime-monitor-panel.js", '<div class="rtm-log-lines selectable-log">'),     # 실시간 감시 로그 줄
])
def test_log_views_are_selectable(name, markup):
    assert markup in (JS / name).read_text(encoding="utf-8")


def test_selecting_inside_a_log_copies_it():
    core = (JS / "core.js").read_text(encoding="utf-8")
    copy = core[core.index("async function copyText("):core.index("// ===== 저장 완료 토스트")]
    assert "navigator.clipboard.writeText(text)" in copy and "document.execCommand('copy')" in copy
    assert "e.target.closest('.selectable-log')" in copy          # 로그 안에서 누르기 시작했을 때만
    assert "unchanged && !logSelectionChanged" in copy            # 골라 둔 채 다른 곳을 눌렀다 떼면 다시 복사하지 않는다
