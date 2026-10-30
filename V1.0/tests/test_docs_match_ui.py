"""문서와 화면이 맞는지 — IMPROVEMENT_PLAN 5-3(v1.0 완료 정의 4: README 가 설명하는 기능에 화면 진입점이 있다).

README 의 작업 흐름은 사이드바 메뉴 하나에 한 줄이다(5-1). 메뉴를 더하거나 이름을 바꾸고 README 를 잊으면(또는 그
반대면) 여기서 걸린다. README 가 말하는 상단바·상태바의 버튼도 화면에 있어야 한다.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "web_ui" / "index.html").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_workflow_lists_the_sidebar_menus_in_order():
    menus = [label for label in re.findall(r'<span class="nav-label">([^<]+)</span>', HTML) if label != "접기"]
    workflow = README[README.index("## 사용자 작업 흐름 가이드"):README.index("### 상단바와 상태바")]
    documented = re.findall(r"^\* \*\*(\d+)\. ([^*(]+?)(?: \(선택\))?\*\*", workflow, re.MULTILINE)
    assert len(menus) == 14
    assert [int(number) for number, _ in documented] == list(range(1, len(menus) + 1))
    assert [name.strip() for _, name in documented] == menus


def test_readme_top_bar_and_status_bar_buttons_exist():
    bars = README[README.index("### 상단바와 상태바"):]
    assert "상단바의 종 버튼" in bars and 'id="tb-notify"' in HTML
    assert "상태바의 '작업'" in bars and 'id="sb-jobs"' in HTML
