"""구성도 노드에 좌표를 붙인다 — 계층형(Core 위, Access 아래) 배치.

**결정적이어야 한다.** 같은 입력에 같은 그림이 나와야 회차를 비교할 수 있고, 폴링마다 노드가
조금씩 움직이면 화면을 읽을 수 없다. 그래서 힘기반(force-directed) 배치를 쓰지 않는다 —
그건 예쁘지만 매번 다른 그림을 준다.

배치 규칙(네트워크 다이어그램 관례):
  * 계층을 위에서 아래로 쌓는다 — Edge/WAN, Core, Aggregation, Access, 미분류.
  * 이중화 쌍(MLAG)은 반드시 나란히 두고 쌍 사이를 좁힌다 — 쌍이 떨어져 있으면 peer-link 가
    화면을 가로질러 다른 링크와 엉킨다.
  * 계층 안의 순서는 (쌍 → 이름)으로 고정하고, 이름은 자연 정렬한다(Access10 이 Access2 뒤).

**저장된 수동 좌표가 자동 배치를 덮는다.** 계층 자동 판정은 이름에 의존하므로 틀릴 수 있고
(engine/topology_builder.py 의 _assign_tiers 주석 참고), 무엇보다 네트워크 엔지니어는 늘 직접
배치하고 싶어 한다. 그 탈출구가 없으면 자동 배치가 한 번 틀리는 순간 이 화면은 버려진다.
"""
import re

# 노드 하나가 차지하는 칸. 기호(48) + 장비명 + IP 두 줄이 들어간다.
NODE_W, NODE_H = 132, 76
ICON_SIZE = 48
# 계층 사이 간격 — 링크 라벨(포트명 + 묶음명)이 겹치지 않을 만큼 필요하다.
TIER_GAP = 190
# 같은 계층 안의 간격. 쌍 안쪽은 좁게, 쌍 사이는 넓게.
NODE_GAP = 56
PAIR_GAP = 18
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 60, 56, 96   # 아래쪽은 범례 자리
# 끌어 옮긴 노드 때문에 캔버스를 늘릴 때 아래에 남기는 자리 — 장비명 줄 + 범례 상자가 들어간다.
LEGEND_RESERVE = 230


def layout(topology, manual=None):
    """topology(build_topology 결과)의 노드에 x/y 를 넣고 캔버스 크기를 돌려준다.

    manual: {장비명: [x, y]} — 사용자가 끌어 옮긴 좌표. 있으면 그것이 우선한다.
    반환: {"width", "height", "rows": [{"tier", "label", "y", "nodes": [...]}]}
    """
    manual = {str(k): v for k, v in (manual or {}).items() if _is_point(v)}
    nodes = topology.get("nodes") or []
    pairs = topology.get("pairs") or []
    tiers = topology.get("tiers") or {}

    groups_by_tier = {}
    for tier, members in _tier_groups(nodes, pairs).items():
        groups_by_tier[tier] = members

    used_tiers = sorted(groups_by_tier)
    widest = max((_row_width(groups) for groups in groups_by_tier.values()), default=0)
    width = max(720, widest + MARGIN_X * 2)
    height = MARGIN_TOP + max(1, len(used_tiers)) * TIER_GAP + MARGIN_BOTTOM

    by_id = {n["id"]: n for n in nodes}
    rows = []
    for index, tier in enumerate(used_tiers):
        groups = groups_by_tier[tier]
        y = MARGIN_TOP + index * TIER_GAP
        x = (width - _row_width(groups)) / 2
        row_nodes = []
        for group in groups:
            for position, node_id in enumerate(group):
                node = by_id[node_id]
                node["x"], node["y"] = x + NODE_W / 2, y
                node["auto_x"], node["auto_y"] = node["x"], node["y"]
                row_nodes.append(node_id)
                x += NODE_W + (PAIR_GAP if position + 1 < len(group) else NODE_GAP)
        rows.append({"tier": tier, "label": tiers.get(tier) or tiers.get(str(tier)) or "",
                     "y": y, "nodes": row_nodes})

    # 수동 좌표는 마지막에 덮는다 — 자동 배치를 먼저 계산해 두면 '되돌리기'가 가능하다.
    moved = False
    for node in nodes:
        point = manual.get(node["id"])
        if point:
            # 왼쪽/위로 끌면 좌표가 음수가 되는데 SVG 는 (0,0)부터 그리므로 그대로 두면 장비가
            # 화면 밖으로 잘려 사라진다(끌어 옮긴 것이 저장되므로 다음에 열어도 안 보인다).
            # 캔버스를 왼쪽으로 늘릴 수는 없으니 안쪽으로 붙여 세운다.
            node["x"] = max(NODE_W / 2 + 8, float(point[0]))
            node["y"] = max(8.0, float(point[1]))
            node["manual"] = True
            moved = True
        else:
            node["manual"] = False
    if moved:
        # 끌어다 놓은 노드가 캔버스 밖이면 그림이 잘린다 — 캔버스를 늘린다.
        # 아래쪽은 범례 자리까지 함께 확보한다(안 그러면 범례가 그 장비를 덮는다).
        width = max(width, max(n["x"] for n in nodes) + NODE_W / 2 + MARGIN_X)
        height = max(height, max(n["y"] for n in nodes) + NODE_H + LEGEND_RESERVE)

    return {"width": round(width), "height": round(height), "rows": rows}


def _is_point(value):
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(v, (int, float)) for v in value))


def _tier_groups(nodes, pairs):
    """{tier: [[장비], [장비A, 장비B], ...]} — 쌍은 한 그룹으로 묶여 나란히 배치된다."""
    tier_of = {n["id"]: n.get("tier", 0) for n in nodes}
    partner = {}
    for pair in pairs:
        a, b = pair.get("a"), pair.get("b")
        # 계층이 다른 쌍은 묶지 않는다(그러면 한쪽 계층 줄이 어긋난다).
        if a in tier_of and b in tier_of and tier_of[a] == tier_of[b]:
            partner[a] = b
            partner[b] = a

    result = {}
    consumed = set()
    for node in sorted(nodes, key=lambda n: _natural_key(n["id"])):
        node_id = node["id"]
        if node_id in consumed:
            continue
        tier = tier_of[node_id]
        mate = partner.get(node_id)
        if mate and mate not in consumed:
            group = sorted([node_id, mate], key=_natural_key)
            consumed.update(group)
        else:
            group = [node_id]
            consumed.add(node_id)
        result.setdefault(tier, []).append(group)
    return result


def _row_width(groups):
    if not groups:
        return 0
    total = 0
    for index, group in enumerate(groups):
        total += len(group) * NODE_W + (len(group) - 1) * PAIR_GAP
        if index + 1 < len(groups):
            total += NODE_GAP
    return total


def _natural_key(text):
    """'Access10' 이 'Access2' 뒤에 오도록 숫자 구간을 정수로 비교한다."""
    return tuple(int(part) if part.isdigit() else part.lower()
                 for part in re.split(r"(\d+)", str(text)))
