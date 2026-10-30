"""보고서 렌더러 결함 — IMPROVEMENT_PLAN 2-15.

  1. PDF: Paragraph 에 원문을 그대로 넣어 'Core<->Agg'·'A&B' 가 있으면 생성이 통째로 실패했다.
  2. 폰트: 'C:/Windows/Fonts' 고정이라 Windows 가 다른 드라이브에 있거나 사용자 글꼴만 있으면 □ 로 나왔다.
  3. (채점 리포트의 HTML 헤더 미이스케이프 — 그 렌더러는 3-3 에서 퇴역)
  4. 로그 파일명 파서가 밑줄이 3개 이상인 장비명을 잘못 읽었다(SEOUL_CORE_SW_01 → 01).
"""
import pytest
from reportlab.platypus import Paragraph

from core.log_naming import parse_inspection_log_name
from report import inspection_pdf


def test_paragraph_text_is_escaped_and_keeps_line_breaks():
    text = inspection_pdf.ptext("Core<->Agg & A<B>\n두 번째 줄")
    assert text == "Core&lt;-&gt;Agg &amp; A&lt;B&gt;<br/>두 번째 줄"
    Paragraph(text, inspection_pdf.style("v", 8.5, font="Helvetica"))       # 파싱 오류 없이 만들어진다


def test_raw_markup_like_text_used_to_break_paragraphs():
    with pytest.raises(Exception):
        Paragraph("Core<->Agg & A<B>", inspection_pdf.style("v", 8.5, font="Helvetica"))


def test_font_candidates_follow_the_windows_folder_and_user_fonts(monkeypatch):
    monkeypatch.setenv("WINDIR", r"D:\Windows")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
    candidates = [regular for regular, _bold in inspection_pdf._font_candidates()]
    assert any(c.replace("/", "\\").startswith(r"D:\Windows\Fonts") for c in candidates)
    assert any("Microsoft" in c and c.endswith("NanumGothic.ttf") for c in candidates)


@pytest.mark.parametrize("name, expected", [
    ("20261027_101500_raw_SEOUL_CORE_SW_01.txt", ("SEOUL_CORE_SW_01", "20261027_101500")),
    ("SEOUL_CORE_SW_01.txt", ("SEOUL_CORE_SW_01", None)),                   # 규칙 밖 — 이름 전체
    ("AutoCheck_SEOUL_CORE_SW_01_20261027_101500.txt", ("SEOUL_CORE_SW_01", "20261027_101500")),
    ("AutoCheck_A_B_C.txt", ("A_B_C", None)),
    ("20261027_101500_raw_Core1.txt", ("Core1", "20261027_101500")),
])
def test_log_file_names_need_numeric_date_and_time(name, expected):
    assert parse_inspection_log_name(name) == expected
