"""고객사·벤더별 보고서 템플릿 — IMPROVEMENT_PLAN 4-10f.

  1. 고객사 템플릿(data/<고객사>/report_template.yaml)은 앱 템플릿 위에 얹힌다 — meta 는 적은 키만, check_items 는
     이름이 같으면 적은 필드만 고치고(자리 그대로) 새 이름은 뒤에 더하고, remove_items 는 뺀다. 번호는 다시 매긴다.
  2. 항목의 vendors 에 없는 플랫폼의 장비는 그 항목이 '해당없음' — 'cisco' 는 Cisco 전체. 플랫폼을 모르면 점검한다.
  3. 화면용 상태(고친·더한·뺀 항목, 기본에 없는 이름, 읽지 못한 이유)와 첫 파일 만들기. 보고서·회차 비교가 같은 템플릿을 쓴다.
"""
import yaml

from engine import inspection_evaluator as ev
from report.inspection_status import STATUS_SKIP
from test_round_compare import root, rounds  # noqa: F401 — fixture

LAYER = {
    "meta": {"site_name": "본사 IDC", "manager": {"name": "홍길동"}},
    "check_items": [
        {"name": "CPU 사용률", "criteria": "70% 미만"},
        {"name": "NTP 동기화", "group": "기타", "commands": ["show ntp status"], "criteria": "synchronised",
         "evaluator": "keyword_ok", "vendors": ["arista"]},
    ],
    "remove_items": ["FAN 상태 확인", "없는 항목"],
}


def _base():
    return ev.load_template()


def test_the_customer_layer_edits_adds_and_removes_items():
    base = _base()
    merged = ev.apply_customer_layer(base, LAYER)
    names = [item["name"] for item in merged["check_items"]]
    assert "FAN 상태 확인" not in names and names[-1] == "NTP 동기화"
    assert names.index("CPU 사용률") == [i["name"] for i in base["check_items"]].index("CPU 사용률") - 1
    cpu = next(item for item in merged["check_items"] if item["name"] == "CPU 사용률")
    assert cpu["criteria"] == "70% 미만" and cpu["evaluator"] == "cpu"             # 적은 필드만 바뀐다
    assert [item["no"] for item in merged["check_items"]] == list(range(1, len(names) + 1))
    assert merged["meta"]["site_name"] == "본사 IDC" and merged["meta"]["manager"]["name"] == "홍길동"
    assert len(base["check_items"]) == 22 and "FAN 상태 확인" in [i["name"] for i in base["check_items"]]


def test_items_for_other_vendors_are_not_applicable():
    template = {"check_items": [
        {"name": "MLAG", "commands": ["show mlag"], "evaluator": "keyword_ok", "vendors": ["arista"]},
        {"name": "vPC", "commands": ["show vpc"], "evaluator": "keyword_ok", "vendors": ["cisco"]},
        {"name": "공통", "commands": ["show version"], "evaluator": "keyword_ok"},
    ]}
    sections = {"show mlag": "state : Active", "show vpc": "vPC domain id : 10", "show version": "ok"}
    arista = {row["name"]: row for row in ev.evaluate_device(sections, template, platform="arista_eos")}
    assert arista["vPC"]["status"] == STATUS_SKIP and "이 벤더의 점검 항목이 아님" in arista["vPC"]["value"]
    assert arista["MLAG"]["status"] != STATUS_SKIP and arista["공통"]["status"] != STATUS_SKIP
    nexus = {row["name"]: row for row in ev.evaluate_device(sections, template, platform="cisco_nxos")}
    assert nexus["MLAG"]["status"] == STATUS_SKIP and nexus["vPC"]["status"] != STATUS_SKIP
    unknown = {row["name"]: row for row in ev.evaluate_device(sections, template, platform=None)}
    assert all(row["status"] != STATUS_SKIP for row in unknown.values())          # 모르면 점검한다


def _write_layer(customer, layer):
    path = ev.customer_template_path(customer)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(layer, allow_unicode=True), encoding="utf-8")
    return path


def test_load_template_reads_the_customer_file_and_status_explains_it(root):
    assert ev.customer_template_status("ABC")["exists"] is False
    _write_layer("ABC", LAYER)
    names = [item["name"] for item in ev.load_template(customer="ABC")["check_items"]]
    assert "NTP 동기화" in names and "FAN 상태 확인" not in names
    assert "FAN 상태 확인" in [item["name"] for item in ev.load_template(customer="XYZ")["check_items"]]
    status = ev.customer_template_status("ABC")
    assert (status["changed"], status["added"], status["removed"], status["unknown_removed"],
            status["vendor_items"]) == (["CPU 사용률"], ["NTP 동기화"], ["FAN 상태 확인"], ["없는 항목"], ["NTP 동기화"])


def test_an_unreadable_customer_file_falls_back_and_says_why(root):
    path = ev.customer_template_path("ABC")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("check_items: [\n  - name: 깨짐", encoding="utf-8")
    assert len(ev.load_template(customer="ABC")["check_items"]) == 22
    status = ev.customer_template_status("ABC")
    assert status["exists"] and status["readable"] is False and status["error"]


def test_the_starter_file_is_valid_and_not_overwritten(root):
    path = ev.create_customer_template("ABC")
    text = path.read_text(encoding="utf-8")
    assert "ABC 보고서 템플릿" in text and yaml.safe_load(text) == {"meta": {}, "check_items": [], "remove_items": []}
    path.write_text("meta: {site_name: 고친 값}\n", encoding="utf-8")
    ev.create_customer_template("ABC")
    assert "고친 값" in path.read_text(encoding="utf-8")


def test_reports_use_the_customer_template(rounds):
    from engine import inspection_report_builder as builder
    _write_layer("ABC", {"remove_items": ["System Uptime"], "meta": {"site_name": "ABC 본사"}})
    context = builder.build_context("ABC", "2026-11", project_id=rounds["2026-11"])
    device = next(d for d in context["devices"] if d["items"])
    assert "System Uptime" not in [item["name"] for item in device["items"]]
    assert context["site_name"] == "ABC 본사"
