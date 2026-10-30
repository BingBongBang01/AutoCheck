"""마스킹 미리보기와 누출 검사 — IMPROVEMENT_PLAN 4-10c.

  1. 미리보기: 파일을 쓰지 않고 한 파일을 마스킹해 규칙마다 가릴 값의 수, 바뀌는 줄의 전/후(글자 수가 같아 줄이 맞물린다).
  2. 누출 검사: 결과에 남은 것을 비밀값 / 골랐는데 남은 값 / 고르지 않아 남는 값으로 나누고, 장비 목록의 이름이 남았는지 본다
     ('hostname X' 선언이 없는 이웃 장비 이름은 장비명 규칙만으로 가려지지 않는다).
  3. 실행 결과에 파일 전체의 합계가 실린다.
"""
import pytest

from engine import log_masking as lm

LOG = """\
Core1#show running-config
hostname Core1
username admin secret 5 $1$abcd$Xyz123
snmp-server community PUBLICRO ro
interface Ethernet1
   description to Agg1
   ip address 10.0.12.1/30
Core1#show lldp neighbors
Et1        Agg1        Ethernet1     120
"""
SELECTED = {"ip_address", "password_hash", "device_hostname"}


def _rules(leaks):
    return {r["key"]: r for r in leaks["rules"]}


def test_preview_counts_what_will_be_masked_and_lines_stay_aligned():
    preview = lm.preview_masking(LOG, SELECTED, device_names=("Core1", "Agg1"))
    assert preview["counts"] == {"ip_address": 1, "password_hash": 1, "device_hostname": 1}
    assert preview["lines"] == 9 and preview["changed_lines"] == len(preview["sample"])
    for row in preview["sample"]:
        assert len(row["before"]) == len(row["after"]) and row["before"] != row["after"]
    numbers = [row["no"] for row in preview["sample"]]
    assert 2 in numbers and 3 in numbers and 7 in numbers                 # hostname·secret·ip 줄


def test_leaks_are_split_into_secret_missed_and_not_selected():
    preview = lm.preview_masking(LOG, SELECTED, device_names=("Core1", "Agg1"))
    rules = _rules(preview["leaks"])
    assert rules["snmp_community"]["secret"] is True and rules["snmp_community"]["selected"] is False
    assert rules["snmp_community"]["samples"] == ["PUBLICRO"]
    assert rules["account_name"]["secret"] is False and rules["account_name"]["selected"] is False
    assert "ip_address" not in rules and "password_hash" not in rules     # 고른 것은 가려졌다
    assert preview["leaks"]["secrets"] == 1 and preview["leaks"]["missed"] == 0
    names = {n["name"]: n["count"] for n in preview["leaks"]["names"]}
    assert names == {"Agg1": 2}                                           # Core1 은 선언이 있어 가려졌다


def test_a_declared_hostname_is_masked_in_prompts_without_an_arista_device_comment():
    """'hostname X' 선언만 있는 로그(Cisco 설정 등) — 예전엔 규칙이 선언 줄을 먼저 가려 이름을 찾지 못하고 프롬프트에 남겼다."""
    masked = lm.apply_masking(LOG, {"device_hostname"})
    assert "Core1" not in masked and "*****#show running-config" in masked
    assert "Agg1" in masked                                               # 선언되지 않은 이웃 이름은 남는다(누출 검사가 알린다)


def test_a_selected_rule_that_misses_is_reported_as_missed():
    masked = lm.apply_masking(LOG, SELECTED)
    # 규칙이 놓친 것처럼: 가려진 결과에 다시 원문 IP 를 끼워 넣는다.
    report = lm.leak_report(masked + "ip route 0.0.0.0/0 192.168.1.1\n", SELECTED)
    assert _rules(report)["ip_address"]["selected"] is True and report["missed"] == 2


def test_nothing_selected_shows_everything_that_would_leave():
    preview = lm.preview_masking(LOG, set(), device_names=("Core1", "Agg1"))
    assert preview["changed_lines"] == 0 and preview["counts"] == {}
    rules = _rules(preview["leaks"])
    assert {"ip_address", "password_hash", "snmp_community", "account_name", "device_hostname"} <= set(rules)
    assert {n["name"] for n in preview["leaks"]["names"]} == {"Core1", "Agg1"}


@pytest.fixture
def api(tmp_path):
    from api.masking_api import MaskingApiMixin

    run = tmp_path / "run"
    for sub in ("original", "problem", "masking"):
        (run / sub).mkdir(parents=True)
    (run / "original" / "20261028_101500_raw_Core1.txt").write_text(LOG, encoding="utf-8")
    swapped = LOG.replace("Core1", "TMP").replace("Agg1", "Core1").replace("TMP", "Agg1")   # Agg1 의 이웃이 Core1
    (run / "original" / "20261028_101500_raw_Agg1.txt").write_text(swapped, encoding="utf-8")

    class Stub(MaskingApiMixin):
        def _active_profile_log_paths(self, create=False, run_id=None):
            return {sub: str(run / sub) for sub in ("original", "problem", "masking")}

        def _paths(self):
            return {}

        def _load_inventory(self, paths):
            return {"devices": [{"name": "Core1"}, {"name": "Agg1"}]}

    return Stub()


def test_preview_api_picks_a_file_and_writes_nothing(api, tmp_path):
    first = api.preview_log_masking("original", sorted(SELECTED))
    assert first["ok"] and first["file"] == "20261028_101500_raw_Agg1.txt"
    assert first["files"] == ["20261028_101500_raw_Agg1.txt", "20261028_101500_raw_Core1.txt"]
    other = api.preview_log_masking("original", sorted(SELECTED), "20261028_101500_raw_Core1.txt")
    assert other["file"] == "20261028_101500_raw_Core1.txt"
    assert list((tmp_path / "run" / "masking").iterdir()) == []
    assert api.preview_log_masking("problem", [])["error"].startswith("마스킹할 로그가 없습니다")
    assert api.preview_log_masking("nope", [])["error"] == "잘못된 소스입니다."


def test_run_reports_what_is_left_across_files(api):
    result = api.run_log_masking("original", sorted(SELECTED))
    assert result["ok"] and len(result["results"]) == 2
    leaks = result["leaks"]
    rules = {r["key"]: r for r in leaks["rules"]}
    assert rules["snmp_community"]["count"] == 2 and leaks["secrets"] == 2
    assert {n["name"]: n["count"] for n in leaks["names"]} == {"Core1": 2, "Agg1": 2}   # 서로의 이웃 이름
    assert "SNMP" in result["warning"]
