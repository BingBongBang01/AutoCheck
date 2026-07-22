"""
CiscoDriver — arista.py와 동일한 구조로 Cisco IOS/IOS-XE CLI 커맨드를 매핑한다.
Collector/Parser/Comparator는 전혀 안 건드림.
"""
try:
    from plugins.vendors.base import VendorDriver, register
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from plugins.vendors.base import VendorDriver, register


class CiscoDriver(VendorDriver):
    vendor_name = "cisco"
    netmiko_device_type = "cisco_ios"

    COMMAND_MAP = {
        # AristaDriver.COMMAND_MAP과 1:1 대응되는 check_id에 Cisco IOS 커맨드 매핑
        "vlan_status": "show vlan brief",
        "stp_status": "show spanning-tree vlan 1,100,200,999",

        "version_info": "show version",
        "power_status": "show environment power",
        "cooling_status": "show environment fan",
        "temperature_status": "show environment temperature",
        "cpu_usage": "show processes cpu sorted",
        "log_check": "show logging",
        "interface_status": "show interfaces status",
        "interface_errors": "show interfaces counters errors",
        "interface_transceiver": "show interfaces transceiver",
        "interface_brief": "show ip interface brief",
        "clock_status": "show clock",
        "module_status": "show module",
        "port_channel_status": "show etherchannel summary",
        "mlag_status": None,  # Cisco에는 VSS/StackWise가 대응 개념이나 1:1 커맨드 없음 — 미지원
        "vrrp_status": "show vrrp brief",
        "varp_status": None,  # Arista VARP 대응 기능 없음(HSRP/VRRP로 대체) — 미지원
        "bgp_summary": "show ip bgp summary",
        "ospf_neighbor": "show ip ospf neighbor",
        "evpn_summary": "show bgp l2vpn evpn summary",
        "vxlan_vtep": "show nve peers",
        "arp_status": "show ip arp",
        "inventory_status": "show inventory",
        "running_config": "show running-config",
        "reload_cause": "show version | include reason",
        "ntp_status": "show ntp status",
        "interface_rates": "show interfaces | include rate",
        "interface_description": "show interfaces description",
        "lldp_neighbors": "show lldp neighbors",
        "acl_status": "show ip access-lists",
    }

    def command_for(self, check_id):
        return self.COMMAND_MAP.get(check_id)

    def supported_check_ids(self):
        return [cid for cid, cmd in self.COMMAND_MAP.items() if cmd is not None]


register(CiscoDriver())


if __name__ == "__main__":
    from plugins.vendors.base import get_driver, list_vendors
    driver = get_driver("cisco")
    print("등록된 벤더:", list_vendors())
    print("vlan_status ->", driver.command_for("vlan_status"))
    print("stp_status ->", driver.command_for("stp_status"))
    print("존재 안 하는 check_id ->", driver.command_for("nonexistent"))
    print("지원 check_id 개수:", len(driver.supported_check_ids()))
