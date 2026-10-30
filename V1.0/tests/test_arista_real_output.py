"""실장비(Arista 25대, EOS 4.23~4.36) 로그로 찾은 오판 — 실제 출력의 모양만 본떠 고정한다(고객 데이터는 담지 않는다).

거의 모든 장비가 '확인필요'였던 원인은 장비마다 반복되는 형식 줄이었다: show logging 머리말의 'level errors', BGP 요약 표
머리말의 'Up/Down', 온도 표 머리말의 'Alert Critical', 정상 접속 기록(%SECURITY-6-…), 쓰지 않는 포트의 VLAN 칸 'inactive',
일부러 끈 admin down. 반대로 PSU 전원 없음(Power Loss), BGP 이웃 미성립(Active·Idle), 로그인 실패(%AAA-4-LOGIN_FAILED)는
놓쳤다. 메모리는 top 의 free(버퍼/캐시 뺀 값)로 셌고, 새 EOS 가 거부하는 show environment cooling·temperature 만 봤다.
"""
import pytest

from engine import command_catalog as cc
from engine import inspection_evaluator as ev
from engine import vendor_commands
from engine.log_rule_engine import ContextTracker, RuleEngine, load_rules
from report.inspection_status import STATUS_NA, STATUS_OK, STATUS_WARN


@pytest.fixture(scope="module")
def engine():
    return RuleEngine(load_rules())


def _verdict(engine, line):
    found = engine.evaluate(line, ContextTracker())
    return (found["rule_id"], found["severity"]) if found else None


@pytest.mark.parametrize("line", [
    "Console logging: level errors",
    "Monitor logging: level errors",
    "Neighbor         V AS           MsgRcvd   MsgSent  InQ OutQ  Up/Down State   PfxRcd PfxAcc PfxAdv",
    "  Description              Neighbor      V AS           MsgRcvd   MsgSent  InQ OutQ  Up/Down State   PfxRcd PfxAcc",
    "                                        Alert  Critical",
    "Oct 30 10:00:00 sw1 ConfigAgent: %SECURITY-6-SSH_CLIENT_CONNECTING: admin is connecting a SSH session",
    "Oct 30 10:00:00 sw1 SuperServer: %SECURITY-6-SSL_PROFILE_VALID: SSL profile 'ARISTA_DEFAULT_PROFILE' is valid",
    "Et7                     notconnect   inactive full   10G    Not Present",
    "  192.0.2.1      4 65001          1234      1240    0    0   10d02h Estab   12     12",
    "  192.0.2.3      4 65003             0         0    0    0   00:01:05 Idle(Admin)",
    "1      PWR-511-AC-RED     500W   0.43A   6.45A  77.1W Ok         10 days, 11:44:22",
])
def test_format_lines_are_not_findings(engine, line):
    assert _verdict(engine, line) is None


@pytest.mark.parametrize("line, rule", [
    ("2      PWR-511-AC-RED     500W   0.00A   0.00A   0.0W Power Loss           Offline", "psu_power_loss"),
    ("  192.0.2.2      4 65002             0         0    0    0   00:01:05 Active", "bgp_peer_not_established"),
    ("  to-spine2      192.0.2.4  4 4200000000      0         0    0    0  283d19h Idle(NoIf)", "bgp_peer_not_established"),
    ("Oct 29 03:12:44 sw1 Aaa: %AAA-4-LOGIN_FAILED: Login from 192.0.2.10 failed for user baduser", "auth_failure"),
    ("Oct 29 03:12:44 sw1 Sshd: authentication failure for user root", "auth_failure"),
    ("Ethernet5           192.0.2.5/31        down             down                     9214", "interface_oper_down"),
])
def test_real_problems_are_findings(engine, line, rule):
    assert _verdict(engine, line)[0] == rule
    assert _verdict(engine, line)[1] in ("major", "critical")


def test_intentional_admin_down_is_only_a_note(engine):
    assert _verdict(engine, "Vlan10              192.0.2.1/24        admin down       down          1500") == (
        "interface_admin_down_brief", "info")


# ---------- 보고서 항목 판정 ----------

DEPRECATED = "% Unavailable command (This command is deprecated by 'show system environment cooling')"
COOLING = "System cooling status is: Ok\nFan Tray  Status  Config Speed  Actual Speed\n1         Ok      50%           49%\n"


def _item(name, commands, evaluator="keyword_ok"):
    return {"no": 1, "group": "H/W", "name": name, "commands": commands, "evaluator": evaluator, "criteria": ""}


def test_rejected_output_gives_way_to_the_new_eos_command():
    sections = {"show environment cooling": DEPRECATED, "show system environment cooling": COOLING}
    assert ev._find_section(sections, ["show environment cooling", "show system environment cooling"])[0] == \
        "show system environment cooling"
    only_rejected = {"show environment cooling": DEPRECATED}
    assert ev._find_section(only_rejected, ["show environment cooling"])[0] == "show environment cooling"


def test_an_old_template_copy_still_reads_the_new_eos_command():
    """사용자 PC 의 예전 양식 사본(새 명령이 없는 commands)이 번들 양식을 이긴다 — 별칭으로 새 명령을 찾는다."""
    old_copy = {"check_items": [_item("FAN 상태 확인", ["show environment cooling", "show environment all"])]}
    sections = {"show environment cooling": DEPRECATED, "show system environment cooling": COOLING}
    (row,) = ev.evaluate_device(sections, old_copy, platform="arista_eos")
    assert row["method"] == "show system environment cooling" and row["status"] == STATUS_OK
    (row,) = ev.evaluate_device({"show environment cooling": DEPRECATED}, old_copy, platform="arista_eos")
    assert row["status"] == STATUS_NA                     # 새 명령을 보내지 않았으면 미수집(사유가 보인다)


def test_psu_without_input_power_needs_attention():
    power = ("Power                           Input  Output  Output\n"
             "Supply Model          Capacity Current Current  Power Status                Uptime\n"
             "------ -------------- -------- ------- ------- ------ ---------- -----------------\n"
             "1      PWR-511-AC-RED     500W   0.43A   6.45A  77.1W Ok         10 days, 11:44:22\n"
             "2      PWR-511-AC-RED     500W   0.00A   0.00A   0.0W Power Loss           Offline\n")
    (row,) = ev.evaluate_device({"show environment power": power}, {"check_items": [
        _item("Power 점검", ["show environment power", "show environment all"])]}, platform="arista_eos")
    assert row["status"] == STATUS_WARN and "Power Loss" in row["detail"]


@pytest.mark.parametrize("rows, status", [
    (["  192.0.2.1      4 65001          1234      1240    0    0   10d02h Estab   12     12"], STATUS_OK),
    (["  spine-1                  192.0.2.4  4 4200000000      0         0    0    0  283d19h Active"], STATUS_WARN),
])
def test_bgp_summary_judges_peer_states_not_the_header(rows, status):
    summary = "\n".join(["BGP summary information for VRF default",
                         "Router identifier 192.0.2.254, local AS number 65000",
                         "  Description              Neighbor      V AS           MsgRcvd   MsgSent  InQ OutQ  Up/Down State"
                         "   PfxRcd PfxAcc"] + rows)
    (row,) = ev.evaluate_device({"show ip bgp summary": summary},
                                {"check_items": [_item("BGP Neighbor", ["show ip bgp summary"])]}, platform="arista_eos")
    assert row["status"] == status


@pytest.mark.parametrize("text, expected", [
    # show version 이 있으면 그것(EOS 가 다시 쓸 수 있는 메모리로 센 값 — 기준 보고서의 방식)
    ("MiB Mem :   7848.6 total,    223.5 free,   3446.0 used,   4179.1 buff/cache\n"
     "Total memory:           8037360 kB\nFree memory:            5034180 kB", "62.6%"),
    # 새 top — Swap 줄의 avail Mem
    ("MiB Mem :   7848.6 total,    223.5 free,   3446.0 used,   4179.1 buff/cache\n"
     "MiB Swap:      0.0 total,      0.0 free,      0.0 used.   4402.6 avail Mem", "56.1%"),
    # 새 top — avail Mem 이 없으면 free + buff/cache
    ("MiB Mem :   1000.0 total,    100.0 free,    300.0 used,    600.0 buff/cache", "70.0%"),
    # 예전 top — free + buffers + Swap 줄의 cached Mem
    ("KiB Mem:   4000000 total,  3800000 used,   200000 free,   100000 buffers\n"
     "KiB Swap:        0 total,        0 used,        0 free.  2000000 cached Mem", "57.5%"),
])
def test_free_memory_counts_reclaimable_cache(text, expected):
    assert ev.EVALUATORS["memory_free"](text, {"warn_at": 0.3})["value"] == expected


def test_memory_item_reads_show_version_even_when_top_comes_first():
    sections = {"show processes top once": "MiB Mem :   7848.6 total,    223.5 free,   3446.0 used,   4179.1 buff/cache",
                "show version": "Arista DCS-7050SX3-48YC8\nTotal memory:           8037360 kB\nFree memory:            5034180 kB"}
    (row,) = ev.evaluate_device(sections, {"check_items": [
        _item("Free Memory", ["show processes top", "show version"], "memory_free") | {"warn_at": 0.3}]},
        platform="arista_eos")
    assert (row["value"], row["status"]) == ("62.6%", STATUS_OK)


def test_default_catalog_sends_the_new_eos_environment_commands():
    by_command = {item["command"]: item for item in cc._make_default_catalog()}
    for old, new in (("show environment cooling", "show system environment cooling"),
                     ("show environment temperature", "show system environment temperature")):
        assert by_command[new]["enabled"] and by_command[new]["check_id"] == by_command[old]["check_id"]
    assert vendor_commands.report_patterns(["show environment cooling"], "keyword_ok", "arista_eos") == [
        "show environment cooling", "show system environment cooling"]
