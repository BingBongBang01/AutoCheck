"""정기점검 보고서 포맷 레지스트리 — IMPROVEMENT_PLAN 3-3.

보고서 경로는 하나다: build_context() 로 데이터를 한 번 조립하고, 등록된 포맷(Excel·PDF)으로 그려서
프로파일 reports/ 에 저장한다. 예전엔 워크스페이스 탭이 채점 데이터(scored=[])로 Markdown·Word·HTML 등
빈 보고서를 run 마다 따로 만들었고, 보고서 탭의 Excel·PDF 는 함수가 둘로 갈려 저장 규칙을 각자 들고 있었다.
"""
import tempfile
from pathlib import Path

import pytest

from core.paths import AppPaths

C, P = "고객사A", "2026-11"
DEVICE = {"name": "Core1", "warn_count": 1, "na_count": 0, "unreachable": False}


@pytest.fixture
def builder(monkeypatch):
    original = AppPaths._user_data_root
    AppPaths._user_data_root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths.forget_ensured()
    from engine import inspection_report_builder as b
    from engine.profile_manager import profile_manager as prm
    prm.create_profile(C, P)
    monkeypatch.setattr(b, "build_context", lambda customer, profile, **k: {
        "inspection_date": "2026-11-15", "target_month": "2026년 11월", "devices": [dict(DEVICE)]})
    monkeypatch.setattr(b, "save_snapshot", lambda *a, **k: None)
    yield b
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


@pytest.fixture
def fake_format(builder, monkeypatch):
    """파일 한 줄만 쓰는 포맷을 등록한다 — 저장 규칙(위치·이름·_v2)이 포맷과 무관함을 보려고."""
    rendered = []

    def render(context, path, customer, profile):
        rendered.append((customer, profile, Path(path).name))
        Path(path).write_text("txt", encoding="utf-8")
        return {"lines": 1}

    monkeypatch.setitem(builder._FORMATS, "text", builder.ReportFormat("text", "Text", ".txt", render))
    return rendered


def test_excel_and_pdf_are_registered(builder):
    assert [(f["id"], f["extension"]) for f in builder.report_formats()][:2] == [("excel", ".xlsx"), ("pdf", ".pdf")]


def test_unknown_format_is_refused_before_anything_is_written(builder):
    with pytest.raises(builder.InspectionReportError, match="지원하지 않는 보고서 형식"):
        builder.export(C, P, "docx")
    assert not any(builder.reports_dir(C, P).iterdir())


def test_every_format_shares_the_save_rules(builder, fake_format):
    first = builder.export(C, P, "text")
    second = builder.export(C, P, "text")
    assert Path(first["path"]).parent == builder.reports_dir(C, P)
    assert first["filename"] == f"{C}_{P}_정기점검보고서_20261115.txt"       # 표준 이름(.xlsx)을 포맷 확장자로
    assert second["filename"].endswith("_v2.txt")                            # 같은 이름은 덮어쓰지 않는다
    assert first["format"] == "text" and first["lines"] == 1 and first["warn_count"] == 1
    assert fake_format == [(C, P, first["filename"]), (C, P, second["filename"])]


def test_given_filename_takes_the_format_extension(builder, fake_format):
    assert builder.export(C, P, "text", filename="11월 보고서.xlsx")["filename"] == "11월 보고서.txt"
    assert builder.export(C, P, "text", filename="..\\..\\밖")["filename"] == ".._.._밖.txt"


def test_report_list_shows_registered_formats_only(builder, fake_format):
    reports = builder.reports_dir(C, P)
    (reports / "a.xlsx").write_bytes(b"x")
    (reports / "_snapshot.json").write_text("{}", encoding="utf-8")
    (reports / "memo.md").write_text("x", encoding="utf-8")
    builder.export(C, P, "text")
    assert sorted(r["name"] for r in builder.list_reports(C, P)) == ["a.xlsx", f"{C}_{P}_정기점검보고서_20261115.txt"]
