"""보고서 데이터 보호 — IMPROVEMENT_PLAN 2-14.

  1. 덮어쓰기: 당일 날짜 파일명으로 저장해서 손으로 보완한 보고서가 경고 없이 사라졌다.
  2. 기록 순서: PDF 를 만들기 전에 지원이력부터 기록해서, 생성이 실패해도 이력 한 줄이 남았다.
  3. 이력 소실: 이력을 비원자적으로 쓰고, JSON 이 깨지면 [] 로 보고 덮어써 누적 이력이 사라졌다.
  4. 전월 오류: '전월' 스냅샷을 저장 시각이 가장 최신인 것으로 골라, 10월 보고서를 다시 만들면 11월 값이 들어갔다.
"""
import json
import tempfile
from pathlib import Path

import pytest

from core.paths import AppPaths

C = "고객사A"
DEVICE = {"name": "Core1", "model": "7050", "serial": "S1", "location": "IDC", "warranty": "",
          "warn_count": 0, "na_count": 0, "unreachable": False, "items": [{"name": "CPU", "value": "5%"}]}


@pytest.fixture
def builder(monkeypatch):
    original = AppPaths._user_data_root
    AppPaths._user_data_root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths.forget_ensured()
    from engine import inspection_report_builder as b
    from engine.profile_manager import profile_manager as prm
    for profile in ("2026-10", "2026-11"):
        prm.create_profile(C, profile)

    class FakeWorkbook:
        def save(self, path):
            Path(path).write_bytes(b"new report")

        def close(self):
            pass

    monkeypatch.setattr(b, "build_context", lambda customer, profile, **k: {
        "inspection_date": "2026-11-15", "target_month": "2026년 11월", "devices": [dict(DEVICE)]})
    monkeypatch.setattr(b, "_excel", lambda: type("X", (), {"build_workbook": staticmethod(lambda c, **kw: FakeWorkbook())}))
    yield b
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def test_a_second_report_on_the_same_day_does_not_overwrite_the_first(builder):
    first = builder.export(C, "2026-11", "excel")
    Path(first["path"]).write_bytes(b"hand-edited")                     # 담당자가 손으로 보완
    second = builder.export(C, "2026-11", "excel")
    assert second["filename"].endswith("_v2.xlsx") and second["path"] != first["path"]
    assert Path(first["path"]).read_bytes() == b"hand-edited"


def test_a_failed_pdf_leaves_no_history_row(builder, monkeypatch):
    import report.inspection_pdf as pdf

    def broken(*args, **kwargs):
        raise ValueError("Paragraph 파싱 실패")

    monkeypatch.setattr(pdf, "build_pdf", broken)
    with pytest.raises(ValueError):
        builder.export(C, "2026-11", "pdf")
    assert builder._read_history_rows(C, "2026-11") == []


def test_a_successful_pdf_records_history_with_this_round_in_the_table(builder, monkeypatch):
    import report.inspection_pdf as pdf
    seen = {}

    def fake_build(context, path, equipment, history, topology=None, audit=None):
        seen["history"] = history
        Path(path).write_bytes(b"%PDF")

    monkeypatch.setattr(pdf, "build_pdf", fake_build)
    builder.export(C, "2026-11", "pdf")
    assert any(row[2] == "2026년 11월 정기점검" for row in seen["history"])        # 표에는 이번 회차가 있다
    assert [r["note"] for r in builder._read_history_rows(C, "2026-11")] == ["2026년 11월 정기점검"]


def test_a_corrupt_history_file_is_preserved_not_overwritten(builder):
    path = builder._history_path(C, "2026-11")
    path.write_text('[{"date": "2026-09-15", "note": "9월 정기점검"', encoding="utf-8")
    rows = builder.append_support_history(C, "2026-11", date="2026-11-15", note="11월 정기점검")
    assert [r["note"] for r in rows] == ["11월 정기점검"]
    corrupt = list(path.parent.glob(path.name + ".corrupt-*"))
    assert len(corrupt) == 1 and "9월 정기점검" in corrupt[0].read_text(encoding="utf-8")


def test_previous_month_is_the_latest_round_before_this_inspection(builder):
    builder.save_snapshot(C, "2026-10", [dict(DEVICE, items=[{"name": "CPU", "value": "10%"}])], "2026-10-15")
    builder.save_snapshot(C, "2026-11", [dict(DEVICE, items=[{"name": "CPU", "value": "11%"}])], "2026-11-15")
    # 11월을 만든 뒤 10월 보고서를 다시 만든다 — 10월의 '전월'은 11월이 아니다.
    assert builder.load_previous_snapshot(C, "2026-10", before="2026-10-15") == {}
    november_prev = builder.load_previous_snapshot(C, "2026-11", before="2026-11-15")
    assert november_prev["devices"]["Core1"]["CPU"] == "10%"


def test_snapshots_written_without_a_date_fall_back_to_the_save_date(builder):
    path = builder._snapshot_path(C, "2026-10")
    path.write_text(json.dumps({"saved_at": "2026-10-20T09:00:00", "devices": {"Core1": {"CPU": "7%"}}}),
                    encoding="utf-8")
    assert builder.load_previous_snapshot(C, "2026-11", before="2026-11-15")["devices"]["Core1"]["CPU"] == "7%"
    assert builder.load_previous_snapshot(C, "2026-11", before="2026-10-01") == {}
