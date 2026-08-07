"""
AristaDriver — 지금까지 collector.py/stages.yaml/command_catalog.py 여기저기 흩어져
있던 "show vlan brief" 같은 리터럴 커맨드 문자열을 이 한 곳으로 격리한다.
Cisco/Juniper 등 추가 시 이 파일과 같은 형태로 하나씩 추가하면 되고,
Collector/Parser/Comparator는 전혀 안 건드림.
"""
try:
    from plugins.vendors.base import VendorDriver, register
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from plugins.vendors.base import VendorDriver, register


class AristaDriver(VendorDriver):
    vendor_name = "arista"
    netmiko_device_type = "arista_eos"

    COMMAND_MAP = {
        # LAB1 채점(VLAN/STP)에서 이미 실사용 중인 것 — stages.yaml에 있던 리터럴을 그대로 이관
        "vlan_status": "show vlan brief",
        # VLAN 목록을 커맨드에 박지 않는다. 예전에는 "show spanning-tree vlan 1,100,200,999"
        # 처럼 특정 랩의 VLAN 번호가 하드코딩돼 있었는데, 그 VLAN 이 없는 장비(vEOS-lab 의
        # 10/20/200/4000 구성 등)에서는 EOS 가 "% Invalid input" 을 돌려준다. 그러면 STP 를
        # 한 줄도 못 읽은 채로 '비정상' 판정이 나가서 Finding 자체가 무효였다.
        # 인자 없이 실행하면 모든 VLAN 섹션이 한 번에 나오고, parsers/show_spanning_tree.py 의
        # split_combined_vlan_output() 이 VLAN 헤더 기준으로 잘라준다(이미 지원하는 형식).
        "stp_status": "show spanning-tree",

        # Command Catalog 필수/선택 목록에서 이관 (engine/command_catalog.py DEFAULT_ESSENTIAL/OPTIONAL과 1:1 대응)
        "version_info": "show version",
        "power_status": "show environment power",
        "cooling_status": "show environment cooling",
        "temperature_status": "show environment temperature",
        "cpu_usage": "show processes top once",
        "log_check": "show log",
        "interface_status": "show interface status",
        "interface_errors": "show interfaces counters errors",
        "interface_transceiver": "show interfaces transceiver",
        "interface_brief": "show ip interface brief",
        "clock_status": "show clock",
        "module_status": "show module",
        "port_channel_status": "show port-channel summary",
        "mlag_status": "show mlag",
        "vrrp_status": "show vrrp brief",
        "varp_status": "show ip virtual-router",
        "bgp_summary": "show ip bgp summary",
        "ospf_neighbor": "show ip ospf neighbor",
        "evpn_summary": "show bgp evpn summary",
        "vxlan_vtep": "show interfaces vxlan 1",
        "arp_status": "show ip arp vrf all",
        "inventory_status": "show inventory",
        "running_config": "show running-config",
        "reload_cause": "show reload cause",
        "ntp_status": "show ntp status",
        "snmp_status": "show snmp",
        "interface_rates": "show interfaces counters rates",
        "interface_description": "show interfaces description",
        "lldp_neighbors": "show lldp neighbors",
        "acl_status": "show ip access-lists",
    }

    def command_for(self, check_id):
        return self.COMMAND_MAP.get(check_id)

    def supported_check_ids(self):
        return list(self.COMMAND_MAP.keys())


register(AristaDriver())


if __name__ == "__main__":
    from plugins.vendors.base import get_driver, list_vendors
    driver = get_driver("arista")
    print("등록된 벤더:", list_vendors())
    print("vlan_status ->", driver.command_for("vlan_status"))
    print("stp_status ->", driver.command_for("stp_status"))
    print("존재 안 하는 check_id ->", driver.command_for("nonexistent"))
    print("지원 check_id 개수:", len(driver.supported_check_ids()))
