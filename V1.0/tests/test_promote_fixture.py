"""실출력 -> 골든 픽스처 도구(tools/promote_fixture) — IMPROVEMENT_PLAN 4-10.

도구가 지켜야 하는 것 셋:
  1. 파서가 그대로 읽는다 — 가명화 전후로 플랫폼·명령·보고서 판정·로그 규칙이 같고, 장비 정보·구성도는 바꾼 값만 다르다.
  2. 같은 값은 늘 같은 가짜 — 프롬프트·hostname·LLDP/CDP 이웃·FQDN 의 장비 이름, 같은 /24, 같은 MAC(표기가 달라도),
     온전한 설명과 상태 표에 잘려 적힌 설명.
  3. 원래 값이 남으면 쓰지 않는다 — 도구가 모르는 이름이 다른 낱말 속에 남은 것까지.
여기의 입력은 도구를 시험하려고 만든 짧은 로그다. 파서를 시험하는 골든 픽스처는 실출력만 쓴다(tests/test_golden_fixtures).
"""
import json

import pytest

from tools import promote_fixture as pf

EOS_LOG = "\r\n".join([
    "Seoul-Core1#show version",
    "Arista DCS-7050SX3-48YC8",
    "Hardware version: 11.01",
    "Serial number: JPE19281234",
    "Hardware MAC address: 001c.7311.2233",
    "System MAC address: 001c.7311.2233",
    "",
    "Software image version: 4.28.3M",
    "Uptime: 2 weeks, 1 day, 3 hours and 4 minutes",
    "Total memory: 8000000 kB",
    "Free memory: 4000000 kB",
    "",
    "Seoul-Core1#show processes top once",
    "%Cpu(s):  2.0 us,  1.0 sy,  0.0 ni, 95.0 id,  0.0 wa",
    "KiB Mem :  8000000 total,  4000000 free,  3000000 used",
    "Seoul-Core1#show lldp neighbors",
    "Port       Neighbor Device ID       Neighbor Port ID    TTL",
    "Et1        Seoul-Agg1.abc.co.kr     Ethernet1           120",
    "Et2        Seoul-Acc1               Ethernet1           120",
    "Ma1        00:50:56:ab:cd:ef        Gi0/1               120",
    "Seoul-Core1#show interfaces status",
    "Port       Name        Status       Vlan     Duplex Speed  Type",
    "Et1        to-Seoul-Ag connected    in Po1   full   10G    10GBASE-SR",
    "Et2        ABC-uplink  connected    trunk    full   10G    10GBASE-SR",
    "Seoul-Core1#show interfaces description",
    "Interface  Status  Protocol  Description",
    "Et1        up      up        to-Seoul-Agg1-Et1",
    "Et2        up      up        ABC-uplink",
    "Seoul-Core1#show ip interface brief",
    "Interface       IP Address        Status   Protocol  MTU",
    "Management1     10.20.30.41/24    up       up        1500",
    "Vlan100         10.20.31.1/24     up       up        1500",
    "Seoul-Core1#show running-config",
    "! device: Seoul-Core1 (DCS-7050SX3-48YC8, EOS-4.28.3M)",
    "hostname Seoul-Core1",
    "ip domain-name abc.co.kr",
    "username admin privilege 15 role network-admin secret sha512 $6$abcdefgh$ijklmnop",
    "snmp-server community S3cr3tComm ro",
    "snmp-server location Seoul IDC 3F Rack 12",
    "ntp server 10.20.30.1",
    "ntp server 2001:abcd:12:1::5",
    "vlan 100",
    "   name ABC_Finance",
    "interface Ethernet1",
    "   description to-Seoul-Agg1-Et1",
    "   ip address 10.20.32.1 255.255.255.252",
    "router ospf 1",
    "   network 10.20.0.0 0.0.255.255 area 0",
    "banner motd",
    "ABC Corp authorized users only",
    "EOF",
    "Seoul-Core1#show logging",
    "Oct 29 10:00:00 Seoul-Core1 ConfigAgent: %SYS-5-CONFIG_E: Enter configuration mode from console by admin"
    " on vty3 (10.20.30.99)",
    "Seoul-Core1#",
]) + "\r\n"

IOS_LOG = "\n".join([
    "Branch-SW1#show version",
    "Cisco IOS Software, C2960X Software (C2960X-UNIVERSALK9-M), Version 15.2(7)E4, RELEASE SOFTWARE (fc2)",
    "Branch-SW1 uptime is 3 weeks, 2 days, 4 hours, 10 minutes",
    "Processor board ID FOC1234X5YZ",
    "Model number                    : WS-C2960X-48FPD-L",
    "System serial number            : FOC1234X5YZ",
    "Branch-SW1#show inventory",
    'NAME: "1", DESCR: "WS-C2960X-48FPD-L"',
    "PID: WS-C2960X-48FPD-L , VID: V05  , SN: FOC1234X5YZ",
    'NAME: "Switch 1 - FlexStackPlus Module", DESCR: "Stacking Module"',
    "PID: C2960X-STACK      , VID: V02  , SN: FOC2222Y1AB",
    "Branch-SW1#show cdp neighbors detail",
    "-------------------------",
    "Device ID: Core-N9K-1(FDO21120U8N)",
    "Entry address(es):",
    "  IP address: 172.16.10.1",
    "Platform: N9K-C93180YC-EX,  Capabilities: Router Switch IGMP Filtering",
    "Interface: GigabitEthernet1/0/49,  Port ID (outgoing port): Ethernet1/1",
    "Holdtime : 150 sec",
    "Branch-SW1#show running-config",
    "hostname Branch-SW1",
    "banner motd ^C",
    "Property of Example Customer Inc.",
    "^C",
    "username netadmin privilege 15 secret 9 $9$abcdefghijk$lmnopqrst",
    "Branch-SW1#",
]) + "\n"


def _promote_source(tmp_path, text, name="device.log"):
    source = tmp_path / name
    source.write_bytes(text.encode("utf-8"))
    return source


def test_parsers_read_the_pseudonymized_output_the_same_way():
    pseudonymizer = pf.Pseudonymizer(words=["ABC"])
    before, after = pf.describe(EOS_LOG), pf.describe(pseudonymizer.run(EOS_LOG))
    for key in ("platform", "commands", "report_items", "log_findings"):
        assert after[key] == before[key], key
    assert before["platform"] == "arista_eos" and before["log_findings"] and before["report_items"]
    assert after["device"] == {**before["device"], "serial": "XXX00000001", "ip": "198.18.0.41"}
    topology_before, topology_after = before["topology"], after["topology"]
    for key in ("po", "status", "mlag", "l3"):
        assert topology_after[key] == topology_before[key], key
    assert [(n["local_port"], n["neighbor_port"]) for n in topology_after["neighbors"]] == \
        [(n["local_port"], n["neighbor_port"]) for n in topology_before["neighbors"]]
    assert [n["neighbor_device"] for n in topology_after["neighbors"]] == \
        ["DEVICE-2.example.net", "DEVICE-3", "02:00:00:00:00:02"]


def test_the_same_value_always_gets_the_same_fake():
    out = pf.Pseudonymizer(words=["ABC"]).run(EOS_LOG)
    lines = out.split("\r\n")
    assert out.count("\r\n") == EOS_LOG.count("\r\n")                    # 줄바꿈(CRLF)과 줄 수 그대로
    for gone in ("Seoul", "abc.co.kr", "ABC", "JPE19281234", "001c.7311", "00:50:56", "10.20.", "2001:abcd",
                 "S3cr3tComm", "$6$abcdefgh", "authorized", "admin privilege", "by admin"):
        assert gone not in out, gone
    assert "DEVICE-1#show version" in lines and "hostname DEVICE-1" in lines
    assert "! device: DEVICE-1 (DCS-7050SX3-48YC8, EOS-4.28.3M)" in lines            # 모델·버전은 남긴다
    assert "ip domain-name example.net" in lines
    # 같은 /24 는 같은 /24(호스트 자리 그대로), 넷마스크·와일드카드는 그대로
    assert "Management1     198.18.0.41/24    up       up        1500" in lines
    assert "ntp server 198.18.0.1" in lines and "Vlan100         198.18.1.1/24     up       up        1500" in lines
    assert "   ip address 198.18.2.1 255.255.255.252" in lines and "   network 198.18.3.0 0.0.255.255 area 0" in lines
    assert "ntp server 2001:db8:1::1" in lines
    # 같은 MAC 은 같은 가짜, 표기는 원래대로
    assert lines.count("Hardware MAC address: 0200.0000.0001") == 1 and "System MAC address: 0200.0000.0001" in lines
    # 온전한 설명과 상태 표에 잘린 설명이 같은 가짜, 'network-admin' 같은 역할 이름은 그대로
    assert "Et1        DESC-1 connected    in Po1   full   10G    10GBASE-SR" in lines
    assert "   description DESC-1" in lines and "Et2        up      up        DESC-2" in lines
    assert "username user1 privilege 15 role network-admin secret sha512 $*$********$********" in lines
    assert "snmp-server community ********** ro" in lines and "snmp-server location LOCATION-1" in lines
    assert "   name VLAN_NAME-1" in lines and "*** **** ********** ***** ****" in lines
    assert any(line.endswith("by user1 on vty3 (198.18.0.99)") for line in lines)


def test_cisco_neighbors_serials_and_banners():
    pseudonymizer = pf.Pseudonymizer()
    out = pseudonymizer.run(IOS_LOG)
    for gone in ("Branch-SW1", "Core-N9K-1", "FOC1234X5YZ", "FOC2222Y1AB", "FDO21120U8N", "172.16.10.1",
                 "Example Customer", "netadmin", "$9$abcdefghijk"):
        assert gone not in out, gone
    before, after = pf.describe(IOS_LOG), pf.describe(out)
    assert before["platform"] == after["platform"] == "cisco_ios"
    assert after["report_items"] == before["report_items"] and after["commands"] == before["commands"]
    serial = pseudonymizer.serials["FOC1234X5YZ"]
    assert after["device"]["serial"] == serial and len(serial) == 11 and serial.startswith("XXX")
    assert after["topology"]["neighbors"] == [{"local_port": "GigabitEthernet1/0/49", "neighbor_device": "DEVICE-2",
                                               "neighbor_port": "Ethernet1/1"}]
    assert f"Device ID: DEVICE-2({pseudonymizer.serials['FDO21120U8N']})" in out
    assert "banner motd ^C\n******** ** ******* ******** ****\n^C" in out


def test_a_name_left_inside_another_word_stops_the_promotion(tmp_path):
    text = EOS_LOG.replace("by admin on vty3", "by admin on vty3 via seoul-core1_backup")   # 도구가 모르는 이름
    source = _promote_source(tmp_path, text)
    with pytest.raises(pf.LeakError) as caught:
        pf.promote(source, "eos_core", root=tmp_path / "golden")
    assert [(leak["what"], leak["value"]) for leak in caught.value.leaks] == [("장비 이름", "seoul-core1")]
    assert caught.value.leaks[0]["lines"] == [text.split("\r\n").index(next(
        line for line in text.split("\r\n") if "core1_backup" in line)) + 1]
    assert not (tmp_path / "golden").exists()                          # 아무것도 쓰지 않았다
    made = pf.promote(source, "eos_core", root=tmp_path / "golden", names=["seoul-core1_backup"])
    assert "via DEVICE-" in (tmp_path / "golden" / "eos_core" / "session.log").read_text(encoding="utf-8")
    assert made["meta"]["replaced"]["장비 이름"] == 4


def test_promote_writes_the_fixture_and_refresh_rewrites_only_expected(tmp_path):
    source = _promote_source(tmp_path, EOS_LOG)
    golden = tmp_path / "golden"
    made = pf.promote(source, "eos_core", root=golden, words=["ABC"])
    folder = golden / "eos_core"
    assert sorted(p.name for p in folder.iterdir()) == ["expected.json", "fixture.json", "session.log"]
    assert source.read_bytes() == EOS_LOG.encode("utf-8")                     # 원본은 읽기만 한다
    session = (folder / "session.log").read_bytes()
    assert session.count(b"\r\n") == EOS_LOG.count("\r\n")
    expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
    assert expected == pf.describe(session.decode("utf-8")) == made["expected"]
    meta_text = (folder / "fixture.json").read_text(encoding="utf-8")
    assert not [word for word in ("Seoul", "abc", "JPE", "admin", "10.20") if word.lower() in meta_text.lower()]
    meta = json.loads(meta_text)
    assert meta["platform"] == "arista_eos" and meta["platform_override"] is None
    assert meta["replaced"]["장비 이름"] == 3 and meta["replaced"]["IPv4 /24"] == 4 and meta["replaced"]["지정한 낱말"] == 1

    with pytest.raises(FileExistsError):
        pf.promote(source, "eos_core", root=golden)
    with pytest.raises(ValueError):
        pf.promote(source, "Bad Name", root=golden)
    assert pf.fixtures(golden) == [folder] and pf.compare(folder) == []

    tampered = dict(expected, platform="cisco_ios")
    (folder / "expected.json").write_text(json.dumps(tampered), encoding="utf-8")
    assert pf.compare(folder) == ["platform"]
    assert pf.refresh(root=golden) == ["eos_core"] and pf.compare(folder) == []
    assert pf.refresh(root=golden) == []                                     # 그대로면 다시 쓰지 않는다
    assert (folder / "session.log").read_bytes() == session


def test_residual_leaks_judge_committed_text_by_the_fake_ranges():
    text = "\n".join(["ntp server 198.18.0.1", "ntp server 10.0.0.1", "mac 0200.0000.0001", "mac 001c.7300.0001",
                      "lldp DEVICE-1.example.net", "lldp core1.abc.co.kr", "snmp-server community public ro",
                      "mask 255.255.255.0", "lldp-mc 0180.c200.000e", "ntp server 2001:db8:1::1",
                      "ntp server 2001:abcd::1"])
    found = {(leak["what"], leak["value"]): leak["lines"] for leak in pf.residual_leaks(text)}
    assert found == {("IPv4", "10.0.0.1"): [2], ("MAC", "001c.7300.0001"): [4], ("FQDN", "core1.abc.co.kr"): [6],
                     ("비밀(SNMP Community)", "(값은 적지 않는다)"): [7], ("IPv6", "2001:abcd::1"): [11]}
    assert pf.residual_leaks(text.replace("lldp core1.abc.co.kr", "lldp x"), keep=["10.0.0.1"]) and \
        ("IPv4", "10.0.0.1") not in {(leak["what"], leak["value"])
                                     for leak in pf.residual_leaks(text, keep=["10.0.0.1"])}


def test_command_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pf, "GOLDEN_DIR", tmp_path / "golden")
    monkeypatch.setattr(pf, "_use_scratch_app_data", lambda: None)          # 테스트는 이미 임시 데이터 폴더를 쓴다
    source = _promote_source(tmp_path, EOS_LOG)
    assert pf.main(["promote", str(source), "--name", "eos_core", "--word", "ABC", "--out", str(tmp_path / "try")]) == 0
    assert (tmp_path / "try" / "eos_core" / "session.log").is_file() and not (tmp_path / "golden").exists()
    assert pf.main(["promote", str(source), "--name", "eos_core", "--word", "ABC"]) == 0
    assert "픽스처를 만들었습니다" in capsys.readouterr().out
    assert pf.main(["check"]) == 0 and "픽스처 1개, 실패 0개" in capsys.readouterr().out
    assert pf.main(["refresh"]) == 0 and "없음(모두 그대로)" in capsys.readouterr().out
    leaky = _promote_source(tmp_path, EOS_LOG.replace("by admin on vty3", "by admin via seoul-core1_old"), "x.log")
    assert pf.main(["promote", str(leaky), "--name", "eos_leaky"]) == 1
    assert "seoul-core1" in capsys.readouterr().err and not (tmp_path / "golden" / "eos_leaky").exists()
