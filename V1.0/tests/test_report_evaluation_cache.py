"""보고서 장비 판정 캐시 — 같은 로그·같은 양식·같은 규칙이면 다시 판정하지 않는다.

보고서 탭은 열 때마다 장비 전부의 로그를 다시 판정했다(실장비 25대에 4초, 처음엔 13초). 판정 결과를 로그 파일 신원
(경로·수정 시각·크기)으로 기억해 두고, 점검이 로그를 새로 쓰면 저절로 다시 판정한다.
"""
from engine import inspection_evaluator
from engine import inspection_report_builder as builder

LOG = ("Core1#show version\nArista DCS-7050SX3-48YC8\n"
       "Core1#show logging\nOct 30 10:00:00 Core1 Ebra: %LINEPROTO-5-UPDOWN: Line protocol on Interface Ethernet1, "
       "changed state to down\n")


def _build(raw):
    return builder.build_context("고객사A", "2026-10", is_virtual=False, log_dirs=[raw])


def test_report_reuses_verdicts_until_the_log_changes(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    log = raw / "20261030_101646_raw_Core1.txt"
    log.write_text(LOG, encoding="utf-8")
    calls = []
    real = inspection_evaluator.evaluate_device
    monkeypatch.setattr(inspection_evaluator, "evaluate_device", lambda *a, **k: calls.append(1) or real(*a, **k))
    builder._evaluation_cache.clear()

    first = _build(raw)
    second = _build(raw)
    assert len(calls) == 1, "바뀐 것이 없는데 다시 판정했다"
    assert first["devices"][0]["items"] == second["devices"][0]["items"]

    second["devices"][0]["items"][0]["value"] = "고친 값"          # 돌려준 항목을 고쳐도 기억한 판정은 그대로다
    assert _build(raw)["devices"][0]["items"][0]["value"] != "고친 값"

    log.write_text(LOG + "Oct 30 10:00:05 Core1 Ebra: %LINEPROTO-5-UPDOWN: Ethernet2 changed state to down\n",
                   encoding="utf-8")
    _build(raw)
    assert len(calls) == 2, "로그가 바뀌었는데 기억한 판정을 썼다"
