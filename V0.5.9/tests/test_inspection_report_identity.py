"""보고서의 장비 신원(Hostname/IP) — 로그 파일명과 장비목록을 잇는 규칙을 고정한다.

증상이었던 것: 보고서 Hostname 에 '20260805_095518_raw_Agg1' 같은 raw 타임스탬프가 그대로
들어갔고, 그 이름은 장비목록의 'Agg1' 과 매칭되지 않아 IP 열이 전 행 공란이 됐다.
원인은 engine/inspection_report_builder.py 가 레거시 파일명(AutoCheck_*)만 아는 자체
정규식을 갖고 있었던 것 — 현재 규칙은 core/log_naming.py 에 있다.
"""
import datetime

import pytest

from core import log_naming
from engine import inspection_report_builder as builder


def test_current_log_name_yields_hostname_not_timestamp():
    device, stamp = log_naming.parse_inspection_log_name("20260805_095518_raw_Agg1.txt")
    assert device == "Agg1"
    assert datetime.datetime.strptime(stamp, "%Y%m%d_%H%M%S").hour == 9


def test_latest_logs_by_device_uses_hostname(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "20260805_095518_raw_Agg1.txt").write_text("Agg1#show version\n", encoding="utf-8")
    (raw / "20260805_101000_raw_Agg1.txt").write_text("Agg1#show version\nlater\n", encoding="utf-8")
    (raw / "AutoCheck_Core1_20260805_095600.txt").write_text("Core1#show version\n", encoding="utf-8")
    monkeypatch.setattr(builder, "original_log_dirs", lambda *a, **k: [raw])

    logs = builder.latest_logs_by_device("고객사", "2026-08")

    assert set(logs) == {"Agg1", "Core1"}, "파일명 전체가 장비명으로 새어 들어갔다"
    # 같은 장비의 로그가 여러 개면 가장 최근 수집분을 쓴다.
    assert "later" in logs["Agg1"]["text"]


@pytest.mark.parametrize("inventory_name", ["Agg1", "agg1", " Agg1 "])
def test_inventory_join_ignores_case_and_padding(inventory_name):
    inventory = {inventory_name: {"name": inventory_name, "management_ip": "10.0.0.11"}}
    assert builder._inventory_record(inventory, "Agg1")["management_ip"] == "10.0.0.11"


def test_ip_falls_back_to_management_interface_in_log():
    """장비목록에 IP가 없어도 원본로그의 관리 인터페이스에서 주워 온다 —
    IP 열이 통째로 비면 보고서만 봐서는 장비를 특정할 수 없다."""
    sections = {"show ip interface brief": (
        "Interface       IP Address       Status     Protocol\n"
        "Management1     10.10.20.31/24   up         up\n"
        "Vlan10          unassigned       down       lowerlayerdown\n")}
    assert builder._ip_from_sections(sections) == "10.10.20.31"


def test_ip_fallback_returns_blank_without_management_interface():
    assert builder._ip_from_sections({"show version": "Arista vEOS"}) == ""


def test_status_counts_separates_not_judged():
    """요약표의 세 번째 숫자 — 판정하지 못한 항목은 정상 쪽에 합산되면 안 된다."""
    items = [{"status": "정상"}, {"status": "정상"}, {"status": "확인필요"},
             {"status": "미수집"}, {"status": "해당없음"}]
    counts = builder.status_counts(items)
    assert counts["total"] == 5
    assert counts["정상"] == 2
    assert counts["확인필요"] == 1
    assert counts["not_judged"] == 2
