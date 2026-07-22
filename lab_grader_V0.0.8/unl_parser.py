"""
unl_parser.py
EVE-NG .unl 랩 파일을 분석해 토폴로지/이미지버전/장비 역할을 자동 파악한다.
- 장비 접속(SSH) 불필요. 파일 하나만 있으면 동작.
- VLAN 번호, 특정 장비명 등을 코드에 하드코딩하지 않는다 (범용 파서 원칙).
"""

import xml.etree.ElementTree as ET
from collections import defaultdict
import json
import sys

# 알려진 위험 이미지 버전 (프로젝트 트러블슈팅 이력 기반) — 실측/기록으로 갱신되는 참조 테이블
KNOWN_IMAGE_RISKS = {
    "veos-4.35.4M": {
        "risk": "부팅 실패 이력 있음 (EVE-NG 환경 특이사항으로 추정, Arista EOS 자체 결함 아님)",
        "recommended_alternative": "veos-4.31.5M (정상 부팅 확인됨)",
    }
}


def parse_unl(unl_path):
    tree = ET.parse(unl_path)
    root = tree.getroot()

    nodes = []
    for node in root.findall(".//node"):
        interfaces = []
        for iface in node.findall("interface"):
            interfaces.append({
                "id": iface.get("id"),
                "name": iface.get("name"),
                "network_id": iface.get("network_id"),
            })
        nodes.append({
            "id": node.get("id"),
            "name": node.get("name"),
            "image": node.get("image"),
            "cpu": node.get("cpu"),
            "ram": node.get("ram"),
            "left": int(node.get("left", 0)),
            "top": int(node.get("top", 0)),
            "ethernet_declared": int(node.get("ethernet", 0)),
            "interfaces": interfaces,
        })

    networks = {}
    for net in root.findall(".//network"):
        networks[net.get("id")] = {
            "name": net.get("name"),
            "type": net.get("type"),
        }

    return nodes, networks


def find_physical_links(nodes, networks):
    """같은 network_id를 공유하는 인터페이스 2개 = 물리 링크 1개."""
    bridge_members = defaultdict(list)
    for node in nodes:
        for iface in node["interfaces"]:
            net_id = iface["network_id"]
            if networks.get(net_id, {}).get("type") == "bridge":
                bridge_members[net_id].append((node["name"], iface["name"]))

    links = []
    unmatched = []
    for net_id, members in bridge_members.items():
        if len(members) == 2:
            (a_dev, a_if), (b_dev, b_if) = members
            links.append({"a": a_dev, "a_port": a_if, "b": b_dev, "b_port": b_if})
        else:
            unmatched.append({"network_id": net_id, "members": members})
    return links, unmatched


def infer_tiers_by_position(nodes, tolerance_px=30):
    """
    left/top 좌표만으로 계층을 추론 (특정 장비명에 의존하지 않는 범용 로직).
    top 좌표를 tolerance_px 이내로 묶어 클러스터링 후, 위→아래 순서로 tier_1, tier_2... 라벨링.
    (완전 일치만 인정하면 캔버스에서 노드를 몇 px만 어긋나게 옮겨도 계층이 잘못 나뉘는 문제가 있어
     허용오차 기반 클러스터링으로 처리한다.)
    노드 이름에 흔한 역할 키워드가 있으면 참고용으로 병기(있으면 부가정보, 없어도 무방).
    """
    sorted_tops = sorted(set(n["top"] for n in nodes))

    # 허용오차 이내로 인접한 값끼리 하나의 클러스터로 묶음
    clusters = []
    for t in sorted_tops:
        if clusters and t - clusters[-1][-1] <= tolerance_px:
            clusters[-1].append(t)
        else:
            clusters.append([t])

    tier_of_top = {}
    for i, cluster in enumerate(clusters):
        for t in cluster:
            tier_of_top[t] = i + 1

    role_keywords = ["core", "spine", "agg", "distribution", "dist", "access", "leaf", "edge", "border"]

    result = []
    for n in nodes:
        tier = tier_of_top[n["top"]]
        name_lower = n["name"].lower()
        matched_keyword = next((kw for kw in role_keywords if kw in name_lower), None)
        result.append({
            "name": n["name"],
            "tier": f"tier_{tier}",
            "name_hint": matched_keyword,  # 참고용, 없으면 None
            "top": n["top"],
        })
    return result


def check_image_risks(nodes):
    findings = []
    for n in nodes:
        risk = KNOWN_IMAGE_RISKS.get(n["image"])
        if risk:
            findings.append({"device": n["name"], "image": n["image"], **risk})
    return findings


def check_interface_utilization(nodes):
    result = []
    for n in nodes:
        used = len(n["interfaces"])
        declared = n["ethernet_declared"]
        result.append({
            "device": n["name"],
            "declared": declared,
            "used": used,
            "spare": declared - used,
        })
    return result


def run_discovery(unl_path, output_json_path=None):
    nodes, networks = parse_unl(unl_path)
    links, unmatched = find_physical_links(nodes, networks)
    tiers = infer_tiers_by_position(nodes)
    image_risks = check_image_risks(nodes)
    iface_util = check_interface_utilization(nodes)

    report = {
        "lab_file": unl_path,
        "node_count": len(nodes),
        "nodes": [{"name": n["name"], "image": n["image"], "cpu": n["cpu"], "ram": n["ram"]} for n in nodes],
        "physical_links": links,
        "unmatched_networks": unmatched,
        "inferred_tiers": tiers,
        "image_risks": image_risks,
        "interface_utilization": iface_util,
    }

    print_report(report)

    if output_json_path:
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n[저장됨] {output_json_path}")

    return report


def print_report(report):
    print("=" * 60)
    print(f"Discovery 결과 — {report['lab_file']}")
    print("=" * 60)

    print(f"\n[노드 {report['node_count']}대]")
    for n in report["nodes"]:
        print(f"  - {n['name']:10s} image={n['image']}  cpu={n['cpu']}  ram={n['ram']}")

    print(f"\n[물리 링크 {len(report['physical_links'])}개]")
    for link in report["physical_links"]:
        print(f"  - {link['a']}:{link['a_port']}  <-->  {link['b']}:{link['b_port']}")

    if report["unmatched_networks"]:
        print(f"\n[경고] 멤버가 1개 또는 3개 이상인 네트워크(브릿지) {len(report['unmatched_networks'])}건")
        for u in report["unmatched_networks"]:
            print(f"  - network_id={u['network_id']}  members={u['members']}")

    print(f"\n[계층(tier) 추론 — 좌표 기반, 이름에 하드코딩 의존 안 함]")
    tier_groups = defaultdict(list)
    for t in report["inferred_tiers"]:
        tier_groups[t["tier"]].append(t)
    for tier_name in sorted(tier_groups.keys(), key=lambda x: int(x.split("_")[1])):
        members = tier_groups[tier_name]
        names = ", ".join(f"{m['name']}({m['name_hint']})" if m["name_hint"] else m["name"] for m in members)
        print(f"  - {tier_name}: {names}")

    if report["image_risks"]:
        print(f"\n[!!] 이미지 버전 위험 감지 — {len(report['image_risks'])}건")
        for r in report["image_risks"]:
            print(f"  - {r['device']}: {r['image']}")
            print(f"      사유: {r['risk']}")
            print(f"      권장 대안: {r['recommended_alternative']}")
    else:
        print("\n[OK] 알려진 위험 이미지 버전 없음")

    print(f"\n[인터페이스 사용률]")
    for u in report["interface_utilization"]:
        print(f"  - {u['device']:10s} 선언={u['declared']}  사용중={u['used']}  여유={u['spare']}")

    print("=" * 60)


if __name__ == "__main__":
    unl_path = sys.argv[1] if len(sys.argv) > 1 else "labs/lab1_campus/04_TEST.unl"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "labs/lab1_campus/discovery_result.json"
    run_discovery(unl_path, out_path)
