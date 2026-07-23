"""EVE-NG topology XML parser."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

TEMPLATE_TO_DEVICE_TYPE = {
    "veos": "arista_eos",
    "iol": "cisco_iosxe",
}

ROLE_KEYWORDS = {
    "core": "core",
    "spine": "core",
    "leaf": "leaf",
    "access": "leaf",
}

DEFAULT_ROLE = "leaf"


def _infer_role(node_name: str) -> str:
    lowered = node_name.lower()
    for keyword, role in ROLE_KEYWORDS.items():
        if keyword in lowered:
            return role
    return DEFAULT_ROLE


def _infer_ip(node_elem: ET.Element) -> str:
    for attr in ("ip", "management_ip", "mgmt"):
        value = node_elem.get(attr)
        if value:
            return value
    return ""


class EveNgTopologyParser:
    """Parses an EVE-NG lab .unl/.xml topology file into inventory + mesh links."""

    def __init__(self, xml_content: str) -> None:
        self._root = ET.fromstring(xml_content)

    def _iter_nodes(self):
        return self._root.findall(".//node")

    def _build_interfaces(self, node_elem: ET.Element) -> List[str]:
        interfaces = []
        for iface_elem in node_elem.findall(".//interface"):
            name = iface_elem.get("name")
            if name:
                interfaces.append(name)
        return interfaces

    def parse_inventory(self) -> List[Dict[str, Any]]:
        inventory = []
        for node_elem in self._iter_nodes():
            hostname = node_elem.get("name", "")
            template = node_elem.get("template", "")
            device_type = TEMPLATE_TO_DEVICE_TYPE.get(template, "unknown")

            inventory.append(
                {
                    "hostname": hostname,
                    "ip": _infer_ip(node_elem),
                    "device_type": device_type,
                    "role": _infer_role(hostname),
                    "interfaces": self._build_interfaces(node_elem),
                }
            )
        return inventory

    def parse_mesh_links(self) -> List[Dict[str, str]]:
        network_to_endpoints: Dict[str, List[Dict[str, str]]] = {}

        for node_elem in self._iter_nodes():
            node_name = node_elem.get("name", "")
            for iface_elem in node_elem.findall(".//interface"):
                network_id = iface_elem.get("network_id")
                if not network_id:
                    continue
                network_to_endpoints.setdefault(network_id, []).append(
                    {
                        "node": node_name,
                        "intf": iface_elem.get("name", ""),
                    }
                )

        mesh_links: List[Dict[str, str]] = []
        for network_id, endpoints in network_to_endpoints.items():
            if len(endpoints) != 2:
                # Only true point-to-point networks (exactly two attached interfaces) qualify as mesh links.
                continue
            src, dst = endpoints
            mesh_links.append(
                {
                    "src_node": src["node"],
                    "src_intf": src["intf"],
                    "dst_node": dst["node"],
                    "dst_intf": dst["intf"],
                }
            )

        return mesh_links

    def parse(self) -> Dict[str, Any]:
        return {
            "inventory": self.parse_inventory(),
            "mesh_links": self.parse_mesh_links(),
        }


def parse_topology_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read()
    return EveNgTopologyParser(content).parse()


import pytest

SAMPLE_XML = """
<lab name="fabric-lab">
    <topology>
        <node id="1" name="core-sw1" template="veos">
            <interface name="Ethernet1" network_id="net-1"/>
            <interface name="Ethernet2" network_id="net-2"/>
        </node>
        <node id="2" name="leaf-sw1" template="veos">
            <interface name="Ethernet1" network_id="net-1"/>
        </node>
        <node id="3" name="access-rtr1" template="iol">
            <interface name="Gi0/0" network_id="net-2"/>
        </node>
        <node id="4" name="unknown-dev1" template="mystery_os">
            <interface name="eth0" network_id="net-3"/>
        </node>
    </topology>
</lab>
"""

def test_parse_inventory_maps_templates_and_roles():
    parser = EveNgTopologyParser(SAMPLE_XML)
    inventory = parser.parse_inventory()

    by_name = {item["hostname"]: item for item in inventory}
    assert by_name["core-sw1"]["device_type"] == "arista_eos"
    assert by_name["core-sw1"]["role"] == "core"
    assert by_name["leaf-sw1"]["role"] == "leaf"
    assert by_name["access-rtr1"]["device_type"] == "cisco_iosxe"
    assert by_name["unknown-dev1"]["device_type"] == "unknown"
    assert by_name["core-sw1"]["interfaces"] == ["Ethernet1", "Ethernet2"]

def test_parse_mesh_links_pairs_shared_network_id():
    parser = EveNgTopologyParser(SAMPLE_XML)
    links = parser.parse_mesh_links()

    assert len(links) == 2
    net1_link = next(link for link in links if link["src_intf"] == "Ethernet1" and link["src_node"] == "core-sw1")
    assert net1_link["dst_node"] == "leaf-sw1"
    assert net1_link["dst_intf"] == "Ethernet1"

def test_parse_mesh_links_ignores_non_p2p_networks():
    parser = EveNgTopologyParser(SAMPLE_XML)
    links = parser.parse_mesh_links()
    involved_nodes = {link["src_node"] for link in links} | {link["dst_node"] for link in links}
    assert "unknown-dev1" not in involved_nodes

def test_parse_returns_full_schema():
    parser = EveNgTopologyParser(SAMPLE_XML)
    result = parser.parse()
    assert "inventory" in result
    assert "mesh_links" in result
    assert len(result["inventory"]) == 4

def test_empty_topology_returns_empty_collections():
    empty_xml = "<lab><topology></topology></lab>"
    parser = EveNgTopologyParser(empty_xml)
    result = parser.parse()
    assert result["inventory"] == []
    assert result["mesh_links"] == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
