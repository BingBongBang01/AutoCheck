"""
ParserRegistry — (vendor, check_id) 키로 알맞은 파서 함수를 찾는다.
기존 parsers/*.py 의 함수(show_vlan.parse, show_spanning_tree.parse_combined 등)를
그대로 등록만 해서 감싼다 — 파서 내부 로직(정규식 등)은 전혀 안 건드림.
"""

_REGISTRY = {}


def register(vendor, check_id, parse_fn):
    _REGISTRY[(vendor.lower(), check_id)] = parse_fn


def get_parser(vendor, check_id):
    return _REGISTRY.get((vendor.lower(), check_id))


def list_registered():
    return list(_REGISTRY.keys())


def _register_arista_defaults():
    """기존 parsers/*.py 함수들을 arista 벤더로 등록 — 로직 재사용, 등록만 신규."""
    from parsers import show_vlan, show_spanning_tree, show_processes, show_environment
    from parsers import show_interfaces_status, show_port_channel, show_inventory_mlag_vrrp
    from parsers import show_routing_neighbor

    register("arista", "vlan_status", show_vlan.parse)
    register("arista", "stp_status", show_spanning_tree.parse_combined)
    register("arista", "cpu_usage", show_processes.parse)
    register("arista", "power_status", show_environment.parse_power)
    register("arista", "cooling_status", show_environment.parse_cooling)
    register("arista", "temperature_status", show_environment.parse_temperature)
    register("arista", "interface_status", show_interfaces_status.parse_status)
    register("arista", "interface_errors", show_interfaces_status.parse_counters_errors)
    register("arista", "port_channel_status", show_port_channel.parse)
    register("arista", "inventory_status", show_inventory_mlag_vrrp.parse_inventory)
    register("arista", "mlag_status", show_inventory_mlag_vrrp.parse_mlag)
    register("arista", "vrrp_status", show_inventory_mlag_vrrp.parse_vrrp)
    register("arista", "ospf_neighbor", show_routing_neighbor.parse_ospf_neighbor)
    register("arista", "bgp_summary", show_routing_neighbor.parse_bgp_summary)
    register("arista", "evpn_summary", show_routing_neighbor.parse_evpn_summary)
    register("arista", "vxlan_vtep", show_routing_neighbor.parse_vxlan_vtep)


_register_arista_defaults()


if __name__ == "__main__":
    print("등록된 (vendor, check_id) 목록:")
    for key in list_registered():
        print(" ", key)

    sample_vlan = """
VLAN  Name                             Status    Ports
----- -------------------------------- --------- -------------------------------
100   USER                             active    Et1
"""
    parser = get_parser("arista", "vlan_status")
    print("\nvlan_status 파서 실행 결과:", parser(sample_vlan))
    print("존재 안 하는 조합:", get_parser("cisco", "vlan_status"))
