"""수집된 점검 로그 + 장비 목록 -> 네트워크 구성도의 그래프 구조.

새로 수집할 것은 없다. 정기점검이 이미 모으는 네 가지 출력만으로 물리 구성이 복원된다:

  show lldp neighbors      무엇이 무엇의 어느 포트에 붙어 있는가   (연결의 유일한 근거)
  show interfaces status   링크 상태 + Port-Channel 소속           (묶음/색)
  show interfaces description  사람이 붙인 링크 설명               (라벨)
  show mlag                peer-link Po                            (이중화 쌍)

여기서 하는 판정 다섯 가지:

1. **양방향 중복 제거** — 같은 링크가 양쪽 장비의 LLDP 표에 각각 한 번씩 나온다. 한쪽에서만
   보이는 링크는 **버리지 않고** warnings 에 남긴다 — 조용히 지우면 단선을 놓친다.
2. **Port-Channel 묶음** — 병렬 링크를 하나의 굵은 선으로 접는다. 안 접으면 Core↔Agg 사이에
   선 4개가 겹쳐 그려져 아무것도 읽을 수 없다.
3. **MLAG 쌍** — peer-link Po 에 속한 포트의 LLDP 이웃이 peer 장비다.
4. **미등록 이웃** — LLDP 에는 보이는데 장비 목록에 없는 장비. 지우지 않고 점선 노드로 남긴다 —
   **문서화되지 않은 연결을 찾아내는 것이 이 화면의 점검 가치다.**
5. **계층(tier)** — role 필드 -> 이름 키워드 -> 실패하면 '분류 못 함'으로 남기고 알린다.
   추정으로 틀린 계층을 그리는 것보다 모른다고 말하는 편이 낫다(사용자가 끌어서 고칠 수 있다).

인터페이스명 정규화는 engine.baseline_store.normalize_interface() 하나만 쓴다 — 'Et1'과
'Ethernet1'을 잇는 단일 출처가 그것이고, 실시간 감시의 component_id 도 같은 축이라
링크 상태 오버레이가 이름 변환 없이 맞물린다.
"""
import re

from engine.baseline_store import normalize_interface

# 계층 — 위에서 아래로. 숫자가 작을수록 위에 그린다.
TIER_EDGE, TIER_CORE, TIER_AGG, TIER_ACCESS, TIER_UNKNOWN = 0, 1, 2, 3, 4
TIER_LABELS = {
    TIER_EDGE: "Edge / WAN",
    TIER_CORE: "Core",
    TIER_AGG: "Aggregation",
    TIER_ACCESS: "Access",
    TIER_UNKNOWN: "미분류",
}
# 장비명에서 계층을 읽는 키워드. 현장 인벤토리의 role 필드는 비어 있는 경우가 대부분이라
# (실제 워크스페이스가 그랬다) 이름이 사실상 유일한 자동 근거다.
_TIER_KEYWORDS = (
    (TIER_EDGE, ("edge", "wan", "fw", "firewall", "border", "gw", "gateway", "internet")),
    (TIER_CORE, ("core", "spine", "backbone", "bb")),
    (TIER_AGG, ("agg", "aggregation", "dist", "distribution", "middle")),
    (TIER_ACCESS, ("access", "leaf", "tor", "acc", "edge-sw")),
)
# role 필드가 채워져 있으면 그것이 이름보다 우선한다(사용자가 명시한 사실이므로).
_ROLE_TIERS = {
    "firewall": TIER_EDGE, "fw": TIER_EDGE, "router": TIER_EDGE, "edge": TIER_EDGE,
    "core": TIER_CORE, "spine": TIER_CORE,
    "aggregation": TIER_AGG, "agg": TIER_AGG, "distribution": TIER_AGG, "dist": TIER_AGG,
    "access": TIER_ACCESS, "leaf": TIER_ACCESS, "tor": TIER_ACCESS,
}
# 기호를 고르는 종류. 판정 근거는 _node_kind() 참고.
KIND_L2, KIND_L3, KIND_ROUTER, KIND_FIREWALL, KIND_UNKNOWN = (
    "l2switch", "l3switch", "router", "firewall", "unknown")
_ROLE_KINDS = {"router": KIND_ROUTER, "firewall": KIND_FIREWALL, "fw": KIND_FIREWALL}

_IP_ROUTING_RE = re.compile(r"^\s*ip routing\b", re.IGNORECASE | re.MULTILINE)


# ---------- 명령 출력 찾기 ----------
# split_transcript() 가 돌려주는 키는 작업자가 실제로 입력한 명령 문자열이라 축약형·파이프가
# 섞인다('show run', 'show interfaces status'). 부분 문자열로 찾고, 여러 개 걸리면 가장 짧은
# 것(=옵션이 덜 붙은 기본형)을 쓴다 — 'show interfaces status' 와
# 'show interfaces status errdisabled' 가 함께 있으면 앞을 원한다.
def _section(sections, *needles):
    best = None
    for command, output in (sections or {}).items():
        low = command.lower()
        if any(n in low for n in needles):
            if best is None or len(command) < len(best[0]):
                best = (command, output)
    return best[1] if best else ""


def build_topology(devices, sections_by_device):
    """반환: {"nodes", "edges", "pairs", "warnings", "tiers"}

    devices: 장비 목록(인벤토리) [{name, role, management_ip, vendor, model, ...}]
    sections_by_device: {장비명: {명령: 출력}} — report.inspection_excel.split_transcript() 결과
    """
    from parsers.show_interfaces_status import (parse_descriptions,
                                                parse_port_channel_membership, parse_status)
    from parsers.show_inventory_mlag_vrrp import parse_mlag
    from parsers.show_lldp_neighbors import parse_lldp_neighbors

    warnings = []
    inventory = [d for d in (devices or []) if (d or {}).get("name")]
    by_lower = {d["name"].lower(): d["name"] for d in inventory}

    facts = {}      # {장비: {"neighbors", "po", "desc", "status", "mlag", "l3"}}
    for device in inventory:
        name = device["name"]
        sections = sections_by_device.get(name) or {}
        if not sections:
            continue
        status_text = _section(sections, "interfaces status", "interface status")
        facts[name] = {
            "neighbors": parse_lldp_neighbors(_section(sections, "lldp neighbors")),
            "po": {normalize_interface(k): v for k, v in
                   parse_port_channel_membership(status_text).items()},
            "desc": {normalize_interface(k): v for k, v in
                     parse_descriptions(_section(sections, "interfaces description")).items()},
            "status": {normalize_interface(k): v for k, v in parse_status(status_text).items()},
            "mlag": parse_mlag(_section(sections, "show mlag")),
            "l3": _looks_l3(sections),
        }

    missing = [d["name"] for d in inventory if d["name"] not in facts]
    if missing:
        warnings.append({"kind": "no_log", "devices": missing,
                         "message": f"점검 로그가 없어 구성에서 빠진 장비 {len(missing)}대: "
                                    + ", ".join(missing)})
    silent = [name for name, f in facts.items() if not f["neighbors"]]
    if silent:
        warnings.append({"kind": "no_lldp", "devices": silent,
                         "message": f"LLDP 이웃 출력이 없어 연결을 알 수 없는 장비 {len(silent)}대: "
                                    + ", ".join(silent)
                                    + " — 장비에서 LLDP가 켜져 있는지 확인하세요."})

    links = _collect_links(facts, by_lower, warnings)
    nodes = _build_nodes(inventory, facts, links, warnings)
    edges = _bundle(links, facts)
    pairs = _mlag_pairs(facts, edges)
    _assign_tiers(nodes, pairs, warnings)
    return {"nodes": nodes, "edges": edges, "pairs": pairs, "warnings": warnings,
            "tiers": dict(TIER_LABELS)}


def _looks_l3(sections):
    """이 장비가 라우팅을 하는가 — 기호를 L2/L3 스위치 중 무엇으로 그릴지의 근거."""
    if _IP_ROUTING_RE.search(_section(sections, "running-config", "show run")):
        return True
    for needle in ("ip route", "ip ospf neighbor", "ip bgp summary"):
        text = _section(sections, needle)
        # '% BGP inactive' 처럼 기능이 없다는 응답은 근거가 아니다.
        if text and "%" not in text.splitlines()[0]:
            return True
    return False


def _resolve_device(raw_name, by_lower):
    """LLDP 가 말한 이웃 이름을 인벤토리 장비명으로. 못 찾으면 (표시용 이름, False).

    LLDP 는 장비가 스스로 말하는 호스트명을 준다 — 대소문자나 FQDN 여부가 인벤토리와 다를 수
    있다. 두 단계로 맞춰 보고, 그래도 없으면 미등록으로 남긴다(버리지 않는다).
    """
    name = (raw_name or "").strip().rstrip(".")
    if not name:
        return None, False
    hit = by_lower.get(name.lower())
    if hit:
        return hit, True
    short = name.split(".")[0]
    hit = by_lower.get(short.lower())
    if hit:
        return hit, True
    return short, False


def _collect_links(facts, by_lower, warnings):
    """LLDP 표들을 모아 양방향 중복을 접는다.

    반환: {key: {"a","a_port","b","b_port","observed_from": set, "unregistered": set}}
    key 는 (장비, 포트) 두 쌍을 정렬한 것 — 어느 쪽에서 봤든 같은 키가 나온다.
    """
    links = {}
    for device, fact in facts.items():
        for entry in fact["neighbors"]:
            local_port = normalize_interface(entry["local_port"])
            peer, registered = _resolve_device(entry["neighbor_device"], by_lower)
            if not peer or peer == device:
                continue        # 자기 자신으로 보이는 이웃(루프백/미러)은 링크가 아니다
            peer_port = normalize_interface(entry["neighbor_port"])
            ends = sorted([(device, local_port), (peer, peer_port)])
            key = (ends[0], ends[1])
            link = links.get(key)
            if link is None:
                link = links[key] = {
                    "a": ends[0][0], "a_port": ends[0][1],
                    "b": ends[1][0], "b_port": ends[1][1],
                    "observed_from": set(), "unregistered": set(),
                }
            link["observed_from"].add(device)
            if not registered:
                link["unregistered"].add(peer)

    one_sided = [lk for lk in links.values()
                 if len(lk["observed_from"]) == 1 and not lk["unregistered"]]
    if one_sided:
        warnings.append({
            "kind": "one_sided",
            "links": [f"{lk['a']}/{lk['a_port']} ↔ {lk['b']}/{lk['b_port']}" for lk in one_sided],
            "message": f"한쪽에서만 관측된 링크 {len(one_sided)}건 — 반대편 LLDP 에는 없습니다. "
                       "단선이나 LLDP 미설정일 수 있습니다.",
        })
    return links


def _build_nodes(inventory, facts, links, warnings):
    """인벤토리 장비 + LLDP 에만 보이는 미등록 장비."""
    nodes = []
    seen = set()
    for device in inventory:
        name = device["name"]
        seen.add(name)
        fact = facts.get(name) or {}
        nodes.append({
            "id": name, "name": name,
            "ip": device.get("management_ip") or "",
            "vendor": device.get("vendor") or "",
            "model": device.get("model") or "",
            "role": (device.get("role") or "").strip(),
            "kind": _node_kind(device, fact),
            "registered": True,
            "has_log": name in facts,
            "tier": TIER_UNKNOWN,
        })
    extra = sorted({d for link in links.values() for d in link["unregistered"]})
    for name in extra:
        if name in seen:
            continue
        seen.add(name)
        nodes.append({
            "id": name, "name": name, "ip": "", "vendor": "", "model": "", "role": "",
            "kind": KIND_UNKNOWN, "registered": False, "has_log": False,
            "tier": TIER_UNKNOWN,
        })
    if extra:
        warnings.append({
            "kind": "unregistered", "devices": extra,
            "message": f"장비 목록에 없는데 연결돼 있는 장비 {len(extra)}대: " + ", ".join(extra)
                       + " — 문서화되지 않은 연결입니다. 장비 목록에 추가할지 확인하세요.",
        })
    return nodes


def _node_kind(device, fact):
    role = (device.get("role") or "").strip().lower()
    if role in _ROLE_KINDS:
        return _ROLE_KINDS[role]
    if not fact:
        return KIND_UNKNOWN
    return KIND_L3 if fact.get("l3") else KIND_L2


def _bundle(links, facts):
    """같은 양단 + 같은 Port-Channel 인 링크들을 하나의 edge 로 접는다.

    Po 이름은 양쪽이 다를 수 있으므로(한쪽만 LAG 인 경우) 두 값을 함께 키에 넣는다.
    하나의 Po 가 서로 다른 이웃 장비로 갈라지는 것은 정상이다 — MLAG 가 정확히 그 모양이고
    (Agg1 의 Po2048 이 Core1 과 Core2 로 나뉜다), 양단이 키에 있으니 따로 묶인다.
    """
    grouped = {}
    for link in links.values():
        po_a = (facts.get(link["a"]) or {}).get("po", {}).get(link["a_port"])
        po_b = (facts.get(link["b"]) or {}).get("po", {}).get(link["b_port"])
        key = (link["a"], link["b"], po_a, po_b)
        grouped.setdefault(key, []).append(link)

    edges = []
    for (a, b, po_a, po_b), members in grouped.items():
        members.sort(key=lambda m: _port_sort_key(m["a_port"]))
        states = [_link_state(m, facts) for m in members]
        bundle = po_a or po_b
        edges.append({
            "id": f"{a}|{b}|{bundle or members[0]['a_port']}",
            "a": a, "b": b,
            "a_port": members[0]["a_port"], "b_port": members[0]["b_port"],
            "bundle": bundle,
            "bundle_a": po_a, "bundle_b": po_b,
            "members": [{"a_port": m["a_port"], "b_port": m["b_port"],
                         "state": _link_state(m, facts),
                         "observed_from": sorted(m["observed_from"])} for m in members],
            "count": len(members),
            # 묶음 안에 하나라도 down 이면 묶음은 '일부 down' 이다 — 전부 정상인 것과 구별해야
            # 이중화가 이미 깎여 있는 상태를 놓치지 않는다.
            "state": _bundle_state(states),
            "label": _edge_label(members, facts, a, b),
            "one_sided": all(len(m["observed_from"]) == 1 for m in members),
        })
    edges.sort(key=lambda e: (e["a"], e["b"], e["bundle"] or "", e["a_port"]))
    return edges


def _port_sort_key(port):
    """'Ethernet10' 이 'Ethernet2' 뒤에 오도록 숫자 부분으로 정렬."""
    nums = re.findall(r"\d+", port or "")
    return ([int(n) for n in nums], port or "")


def _link_state(link, facts):
    """이 물리 링크가 지금 정상인가 — 점검 시점의 show interfaces status 기준.

    양쪽 상태를 다 보고 하나라도 정상이 아니면 문제로 본다. 근거가 아예 없으면 'unknown' —
    '정상'이라고 말하지 않는다(실시간 감시의 판정 불가와 같은 원칙).
    """
    seen = False
    for device, port in ((link["a"], link["a_port"]), (link["b"], link["b_port"])):
        status = (facts.get(device) or {}).get("status", {}).get(port)
        if status is None:
            continue
        seen = True
        if status.lower() != "connected":
            return "down"
    return "up" if seen else "unknown"


def _bundle_state(states):
    if any(s == "down" for s in states):
        return "down" if all(s == "down" for s in states) else "degraded"
    if all(s == "up" for s in states):
        return "up"
    return "unknown"


def _edge_label(members, facts, a, b):
    """링크 라벨 — 사람이 붙인 인터페이스 설명이 있으면 그것이 가장 유용하다."""
    for member in members:
        for device, port in ((a, member["a_port"]), (b, member["b_port"])):
            desc = (facts.get(device) or {}).get("desc", {}).get(port)
            if desc:
                return desc
    return ""


def _mlag_pairs(facts, edges):
    """MLAG peer 쌍 — peer-link Po 에 속한 포트의 LLDP 이웃이 peer 장비다."""
    found = {}
    for device, fact in facts.items():
        peer_link = (fact.get("mlag") or {}).get("peer_link")
        if not peer_link:
            continue
        peer_link = normalize_interface(peer_link)
        for edge in edges:
            if edge["a"] == device and edge["bundle_a"] == peer_link:
                other = edge["b"]
            elif edge["b"] == device and edge["bundle_b"] == peer_link:
                other = edge["a"]
            else:
                continue
            key = tuple(sorted((device, other)))
            entry = found.setdefault(key, {
                "a": key[0], "b": key[1], "kind": "mlag",
                "peer_link": peer_link, "domain": (fact.get("mlag") or {}).get("domain_id", ""),
                "healthy": True, "confirmed_by": [],
            })
            entry["confirmed_by"].append(device)
            if not (fact.get("mlag") or {}).get("is_active_full"):
                entry["healthy"] = False
    for entry in found.values():
        entry["confirmed_by"] = sorted(set(entry["confirmed_by"]))
    return sorted(found.values(), key=lambda p: (p["a"], p["b"]))


def _assign_tiers(nodes, pairs, warnings):
    """계층을 정한다. role -> 이름 -> 실패하면 미분류로 남기고 알린다.

    그래프 구조로 추정하지 않는 이유: collapsed core/agg 설계에서는 차수만으로 Core 와 Agg 를
    가릴 수 없다(실측 랩에서 Agg 의 이웃이 Core 보다 많다). 틀린 계층을 자동으로 그리면
    사용자는 그림을 믿을 수 없게 되고, 무엇이 틀렸는지도 알 수 없다. 모른다고 말하고
    끌어서 고칠 수 있게 두는 편이 정직하다.
    """
    by_id = {n["id"]: n for n in nodes}
    for node in nodes:
        node["tier"] = _tier_of(node)

    # MLAG 쌍은 같은 계층이다 — 한쪽만 이름으로 잡혔으면 짝에게도 같은 계층을 준다.
    for pair in pairs:
        left, right = by_id.get(pair["a"]), by_id.get(pair["b"])
        if not left or not right:
            continue
        known = [n for n in (left, right) if n["tier"] != TIER_UNKNOWN]
        if len(known) == 1:
            other = right if known[0] is left else left
            other["tier"] = known[0]["tier"]

    unknown = [n["name"] for n in nodes if n["tier"] == TIER_UNKNOWN and n["registered"]]
    if unknown:
        warnings.append({
            "kind": "tier_unknown", "devices": unknown,
            "message": f"계층을 자동으로 정할 수 없는 장비 {len(unknown)}대: " + ", ".join(unknown)
                       + " — 장비 목록의 '역할(role)'을 채우거나 구성도에서 직접 끌어 배치하세요.",
        })


def _tier_of(node):
    role = (node.get("role") or "").strip().lower()
    if role in _ROLE_TIERS:
        return _ROLE_TIERS[role]
    name = (node.get("name") or "").lower()
    for tier, keywords in _TIER_KEYWORDS:
        if any(k in name for k in keywords):
            return tier
    # 미등록 장비는 대개 상위(업링크 상대)다 — 다만 확신할 수 없으므로 미분류로 둔다.
    return TIER_UNKNOWN
