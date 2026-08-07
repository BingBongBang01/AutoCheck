"""구성도 SVG 렌더러 — 표준 네트워크 다이어그램 기호와 규칙을 따른다.

임의의 박스-선 그림이 아니다. 네트워크 도면에는 관례가 있고, 그 관례를 지켜야 처음 보는
사람도 읽을 수 있다. Cisco 의 실제 아이콘 아트워크는 저작권/상표 대상이라 복제하지 않고,
**같은 의미를 갖는 관례적 기하 도형**을 직접 그린다.

기호(장비)
    L2 스위치   직사각형 + 좌우로 나란한 화살표 4개   (프레임을 좌우로 넘긴다는 관례)
    L3 스위치   위 도형 + 라우터의 방사형 화살표      (스위칭 + 라우팅)
    라우터      원 + 방사형 화살표 4개
    방화벽      벽돌 패턴 직사각형
    미등록      점선 윤곽 + '?'                       (LLDP 에만 보이는 장비)

연결선
    단일 물리 링크     실선 (얇게)
    Port-Channel 묶음  굵은 실선 + 양단 브래킷 + 'Po2048 ×2' 라벨
    MLAG peer 쌍       쌍을 점선 상자로 감싼다
    DOWN               빨간 선 + 중앙 ✕
    일부 DOWN(묶음)    주황 선 + 중앙 ◐   (이중화가 이미 깎였다는 뜻)
    판정 불가          회색 선            (정상이라고 말하지 않는다)

라벨: 노드는 장비명 + 관리 IP, 링크는 양 끝 인터페이스명과 묶음 이름.
**범례를 항상 그린다** — 기호를 쓰는 그림에 범례가 없으면 규칙이 전달되지 않는다.

색은 클래스로만 지정하고 실제 값은 두 곳에서 온다:
  * 앱 화면  -> web_ui/style.css (라이트/다크 토큰을 따라간다)
  * 파일 저장 -> standalone=True 일 때 <style> 을 SVG 안에 심는다. 내보낸 파일은 앱 밖에서
    열리므로 CSS 변수를 해석해 줄 주체가 없다 — 그래서 값을 인라인으로 넣어야 한다.
한 함수가 두 경우를 다 만든다. 렌더러를 두 개로 나누면 '화면과 내보낸 파일이 다르다'가 된다.
"""
from xml.sax.saxutils import escape, quoteattr

from engine.topology_layout import ICON_SIZE, NODE_H, NODE_W

# 파일로 내보낼 때 쓰는 값 — web_ui/style.css 의 **라이트 테마**와 같은 색이다.
# 문서에 붙여 넣는 용도라 밝은 배경을 전제한다.
EXPORT_PALETTE = {
    "bg": "#FFFFFF", "text": "#0F172A", "sub": "#64748B",
    "border": "rgba(15, 23, 42, 0.18)", "card": "#FFFFFF",
    "up": "#16A34A", "degraded": "#D97706", "down": "#DC2626", "unknown": "#94A3B8",
    "accent": "#2563EB",
}
_ICON_HALF = ICON_SIZE / 2


def render_svg(topology, layout_info, *, standalone=False, title=""):
    """topology + layout(engine/topology_layout.layout 결과) -> SVG 문자열."""
    width, height = layout_info["width"], layout_info["height"]
    nodes = {n["id"]: n for n in topology.get("nodes") or []}
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" class="tp-svg" '
        f'viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="네트워크 구성도">'
    ]
    if title:
        parts.append(f"<title>{escape(title)}</title>")
    if standalone:
        parts.append(_export_style())
        parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" '
                     f'fill="{EXPORT_PALETTE["bg"]}"/>')
    parts.append(_defs())
    parts.append(_tier_bands(layout_info))
    # 쌍 상자 -> 링크 -> 노드 순서. 노드가 마지막이어야 선이 기호 밑으로 지나간다.
    parts.append(_pair_boxes(topology, nodes))
    parts.append(_links(topology, nodes))
    parts.append(_nodes(topology))
    parts.append(_legend(height))
    parts.append("</svg>")
    return "".join(p for p in parts if p)


# ---------- 기호 정의 ----------
def _defs():
    """장비 기호를 <symbol> 로 한 번만 정의하고 <use> 로 배치한다(파일 크기와 일관성)."""
    s = ICON_SIZE
    half, third = s / 2, s / 3
    return f"""<defs>
<symbol id="tp-sym-l2switch" viewBox="0 0 {s} {s}">
  <rect class="tp-icon-body" x="2" y="{third * 0.7:.1f}" width="{s - 4}" height="{s - third * 1.4:.1f}" rx="3"/>
  <g class="tp-icon-glyph">
    <path d="M{third * 0.7:.1f} {half - 6} H{s - third * 0.7:.1f} m-5 -3.5 l5 3.5 l-5 3.5"/>
    <path d="M{third * 0.7:.1f} {half - 1} H{s - third * 0.7:.1f} m-5 -3.5 l5 3.5 l-5 3.5"/>
    <path d="M{s - third * 0.7:.1f} {half + 4} H{third * 0.7:.1f} m5 -3.5 l-5 3.5 l5 3.5"/>
    <path d="M{s - third * 0.7:.1f} {half + 9} H{third * 0.7:.1f} m5 -3.5 l-5 3.5 l5 3.5"/>
  </g>
</symbol>
<symbol id="tp-sym-l3switch" viewBox="0 0 {s} {s}">
  <rect class="tp-icon-body" x="2" y="{third * 0.7:.1f}" width="{s - 4}" height="{s - third * 1.4:.1f}" rx="3"/>
  <g class="tp-icon-glyph">
    <path d="M{third * 0.7:.1f} {half + 3} H{s - third * 0.7:.1f} m-5 -3.5 l5 3.5 l-5 3.5"/>
    <path d="M{s - third * 0.7:.1f} {half + 9} H{third * 0.7:.1f} m5 -3.5 l-5 3.5 l5 3.5"/>
    <path d="M{half - 9} {half - 8} h18 m-4 -3 l4 3 l-4 3"/>
    <path d="M{half + 9} {half - 3} h-18 m4 -3 l-4 3 l4 3"/>
  </g>
</symbol>
<symbol id="tp-sym-router" viewBox="0 0 {s} {s}">
  <circle class="tp-icon-body" cx="{half}" cy="{half}" r="{half - 3}"/>
  <g class="tp-icon-glyph">
    <path d="M{half - 10} {half - 5} h20 m-4 -3 l4 3 l-4 3"/>
    <path d="M{half + 10} {half + 2} h-20 m4 -3 l-4 3 l4 3"/>
    <path d="M{half - 4} {half + 10} v-20 m-3 4 l3 -4 l3 4"/>
    <path d="M{half + 4} {half - 10} v20 m-3 -4 l3 4 l3 -4"/>
  </g>
</symbol>
<symbol id="tp-sym-firewall" viewBox="0 0 {s} {s}">
  <rect class="tp-icon-body" x="2" y="{third * 0.5:.1f}" width="{s - 4}" height="{s - third:.1f}" rx="2"/>
  <g class="tp-icon-glyph">
    <path d="M2 {half - 6} H{s - 2} M2 {half + 2} H{s - 2}"/>
    <path d="M{half} {third * 0.5:.1f} V{half - 6} M{half - 10} {half - 6} V{half + 2}
             M{half + 10} {half - 6} V{half + 2} M{half} {half + 2} V{s - third * 0.5:.1f}"/>
  </g>
</symbol>
<symbol id="tp-sym-unknown" viewBox="0 0 {s} {s}">
  <rect class="tp-icon-body tp-icon-dashed" x="2.5" y="{third * 0.7:.1f}"
        width="{s - 5}" height="{s - third * 1.4:.1f}" rx="5"/>
  <text class="tp-icon-question" x="{half}" y="{half + 6}" text-anchor="middle">?</text>
</symbol>
</defs>"""


def _export_style():
    """내보낸 파일이 앱 밖에서도 같은 모양으로 보이게 값을 심는다."""
    p = EXPORT_PALETTE
    return f"""<style>
.tp-svg{{font-family:'Segoe UI','Malgun Gothic',sans-serif}}
.tp-icon-body{{fill:{p['card']};stroke:{p['text']};stroke-width:1.4}}
.tp-icon-dashed{{stroke-dasharray:4 3;stroke:{p['sub']}}}
.tp-icon-glyph{{fill:none;stroke:{p['text']};stroke-width:1.3;stroke-linecap:round;stroke-linejoin:round}}
.tp-icon-question{{fill:{p['sub']};font-size:20px;font-weight:700}}
.tp-node-name{{fill:{p['text']};font-size:12px;font-weight:700}}
.tp-node-ip{{fill:{p['sub']};font-size:10px}}
.tp-node-unregistered .tp-node-name{{fill:{p['sub']}}}
.tp-tier-label{{fill:{p['sub']};font-size:11px;font-weight:600;letter-spacing:.04em}}
.tp-tier-line{{stroke:{p['border']};stroke-width:1;stroke-dasharray:2 6}}
.tp-link{{fill:none;stroke:{p['up']};stroke-width:1.6;stroke-linecap:round}}
.tp-link-bundle{{stroke-width:3.4}}
.tp-link-down{{stroke:{p['down']}}}
.tp-link-degraded{{stroke:{p['degraded']}}}
.tp-link-unknown{{stroke:{p['unknown']}}}
.tp-link-onesided{{stroke-dasharray:7 4}}
.tp-bracket{{fill:none;stroke:inherit;stroke-width:1.4}}
.tp-port{{fill:{p['sub']};font-size:9px}}
.tp-bundle-label{{fill:{p['text']};font-size:10px;font-weight:600}}
.tp-link-desc{{fill:{p['sub']};font-size:9px;font-style:italic}}
.tp-mark-down{{stroke:{p['down']};stroke-width:2;fill:none}}
.tp-mark-degraded{{stroke:{p['degraded']};stroke-width:2;fill:{p['bg']}}}
.tp-pair-box{{fill:none;stroke:{p['sub']};stroke-width:1.2;stroke-dasharray:5 4}}
.tp-pair-label{{fill:{p['sub']};font-size:9px;font-weight:600}}
.tp-pair-box-bad{{stroke:{p['down']}}}
.tp-legend-box{{fill:{p['card']};stroke:{p['border']};stroke-width:1}}
.tp-legend-title{{fill:{p['text']};font-size:10px;font-weight:700}}
.tp-legend-text{{fill:{p['sub']};font-size:9px}}
</style>"""


# ---------- 계층 띠 ----------
def _tier_bands(layout_info):
    out = []
    for row in layout_info.get("rows") or []:
        if not row.get("label"):
            continue
        y = row["y"] - 14
        out.append(f'<g class="tp-tier">'
                   f'<line class="tp-tier-line" x1="16" y1="{y}" x2="{layout_info["width"] - 16}" y2="{y}"/>'
                   f'<text class="tp-tier-label" x="20" y="{y - 6}">{escape(row["label"])}</text>'
                   f'</g>')
    return "".join(out)


# ---------- MLAG 쌍 상자 ----------
def _pair_boxes(topology, nodes):
    """이중화 쌍을 점선 상자로 감싼다 — 두 장비가 하나의 논리 장비처럼 동작한다는 표시."""
    out = []
    for pair in topology.get("pairs") or []:
        a, b = nodes.get(pair.get("a")), nodes.get(pair.get("b"))
        if not a or not b or "x" not in a or "x" not in b:
            continue
        # 아래쪽 여백을 더 준다 — 노드 라벨(장비명 + IP)이 NODE_H 를 거의 다 쓰므로
        # 같은 값으로 감싸면 장비명이 상자 선 위에 얹혀 둘 다 안 읽힌다.
        pad, pad_bottom = 12, 28
        left = min(a["x"], b["x"]) - NODE_W / 2 - pad
        right = max(a["x"], b["x"]) + NODE_W / 2 + pad
        top = min(a["y"], b["y"]) - pad
        bottom = max(a["y"], b["y"]) + NODE_H + pad_bottom
        bad = "" if pair.get("healthy", True) else " tp-pair-box-bad"
        label = f"MLAG {pair.get('domain') or ''}".strip()
        out.append(
            f'<g class="tp-pair" data-tp-pair={quoteattr(f"{pair.get('a')}|{pair.get('b')}")}>'
            f'<rect class="tp-pair-box{bad}" x="{left:.0f}" y="{top:.0f}" '
            f'width="{right - left:.0f}" height="{bottom - top:.0f}" rx="8"/>'
            f'<text class="tp-pair-label" x="{left + 6:.0f}" y="{top - 4:.0f}">{escape(label)}</text>'
            f'</g>')
    return "".join(out)


# ---------- 링크 ----------
def _links(topology, nodes):
    geometry = _link_geometry(topology, nodes)
    label_slots = _label_slots(geometry)
    out = []
    for edge in topology.get("edges") or []:
        coords = geometry.get(edge["id"])
        if coords is None:
            continue
        x1, y1, x2, y2 = coords
        state = edge.get("state") or "unknown"
        classes = ["tp-link", f"tp-link-{state}"]
        if edge.get("count", 1) > 1:
            classes.append("tp-link-bundle")
        if edge.get("one_sided"):
            classes.append("tp-link-onesided")
        body = [f'<line class="{" ".join(classes)}" x1="{x1:.0f}" y1="{y1:.0f}" '
                f'x2="{x2:.0f}" y2="{y2:.0f}"/>']
        if edge.get("count", 1) > 1:
            body.append(_bracket(x1, y1, x2, y2, state))
        body.append(_port_labels(edge, x1, y1, x2, y2))
        body.append(_midpoint_label(edge, x1, y1, x2, y2, label_slots.get(edge["id"], 0.5)))
        body.append(_state_mark(state, x1, y1, x2, y2))
        out.append(f'<g class="tp-link-group" data-tp-link={quoteattr(edge["id"])}>'
                   + "".join(p for p in body if p) + "</g>")
    return "".join(out)


_FAN_SPREAD = 34        # 위/아래로 나가는 링크를 펼칠 폭
_FAN_SPREAD_LEVEL = 20  # 좌/우로 나가는 링크를 펼칠 높이


def _link_geometry(topology, nodes):
    """각 링크의 시작·끝 좌표. 반환: {edge_id: (x1, y1, x2, y2)}

    같은 노드에서 같은 방향으로 나가는 링크들을 **기호 가장자리에 부채꼴로 펼친다.**
    한 점에서 모두 나가면 선이 겹쳐 몇 개인지 보이지 않고, 포트 라벨도 같은 자리에 쌓인다
    (이중화 구성에서는 거의 항상 그렇게 된다 — Core1 에서 Agg1·Agg2 로 두 줄이 나간다).
    실제 도면에서 연결점을 장비 면에 나눠 그리는 것과 같은 이유다.

    펼치는 순서는 '상대 노드의 x 좌표' 순이다 — 그래야 선이 불필요하게 교차하지 않는다.
    """
    positioned = {n["id"]: n for n in (topology.get("nodes") or []) if "x" in n}
    edges = [e for e in (topology.get("edges") or [])
             if e["a"] in positioned and e["b"] in positioned]

    # (노드, 방향) -> 그 방향으로 나가는 링크들
    slots = {}
    for edge in edges:
        a, b = positioned[edge["a"]], positioned[edge["b"]]
        for near, far, end in ((a, b, "a"), (b, a, "b")):
            slots.setdefault((near["id"], _direction(near, far)), []).append((far["x"], edge["id"], end))
    for key in slots:
        slots[key].sort()

    geometry = {}
    for edge in edges:
        a, b = positioned[edge["a"]], positioned[edge["b"]]
        x1, y1 = _anchor(a, b, slots, edge["id"], "a")
        x2, y2 = _anchor(b, a, slots, edge["id"], "b")
        geometry[edge["id"]] = (x1, y1, x2, y2)
    return geometry


def _direction(near, far):
    if abs(near["y"] - far["y"]) < 1:
        return "right" if far["x"] > near["x"] else "left"
    return "down" if far["y"] > near["y"] else "up"


def _anchor(near, far, slots, edge_id, end):
    """near 노드의 기호 가장자리 위 연결점."""
    direction = _direction(near, far)
    members = slots.get((near["id"], direction)) or []
    total = len(members)
    index = next((i for i, (_x, eid, e) in enumerate(members) if eid == edge_id and e == end), 0)
    center_y = near["y"] + _ICON_HALF

    if direction in ("down", "up"):
        spread = min(_FAN_SPREAD, ICON_SIZE - 8)
        offset = 0 if total <= 1 else (index / (total - 1) - 0.5) * spread
        y = near["y"] + ICON_SIZE if direction == "down" else near["y"]
        return near["x"] + offset, y
    spread = min(_FAN_SPREAD_LEVEL, ICON_SIZE - 12)
    offset = 0 if total <= 1 else (index / (total - 1) - 0.5) * spread
    x = near["x"] + _ICON_HALF if direction == "right" else near["x"] - _ICON_HALF
    return x, center_y + offset


def _label_slots(geometry):
    """중앙 라벨이 겹치지 않게 선 위에서의 위치(0~1)를 어긋나게 정한다.

    이중화 구성에서는 대각선 두 개가 정확히 같은 점에서 교차한다(Core1→Agg2 와 Core2→Agg1).
    두 라벨이 같은 자리에 겹쳐 찍히면 둘 다 못 읽으므로, 중점이 같은 칸에 떨어지는 링크들은
    각자의 선 위에서 조금씩 다른 지점에 라벨을 놓는다. 결정적이어야 하므로 edge_id 정렬 순서를
    쓴다(무작위 흔들기는 폴링마다 그림이 달라진다).
    """
    buckets = {}
    for edge_id, (x1, y1, x2, y2) in geometry.items():
        cell = (round((x1 + x2) / 2 / 56), round((y1 + y2) / 2 / 34))
        buckets.setdefault(cell, []).append(edge_id)
    slots = {}
    for members in buckets.values():
        members.sort()
        total = len(members)
        for index, edge_id in enumerate(members):
            if total == 1:
                slots[edge_id] = 0.5
            else:
                # 0.36 ~ 0.68 사이에 고르게 — 양 끝의 노드 라벨과 포트 라벨을 피하는 범위다.
                slots[edge_id] = 0.36 + (index / (total - 1)) * 0.32
    return slots


def _bracket(x1, y1, x2, y2, state):
    """묶음 표시 — 선의 양 끝에 짧은 직교 눈금을 넣는다(LAG 관례)."""
    import math

    length = math.hypot(x2 - x1, y2 - y1) or 1
    nx, ny = -(y2 - y1) / length * 5, (x2 - x1) / length * 5
    ticks = []
    for t in (0.12, 0.88):
        cx, cy = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
        ticks.append(f'<line class="tp-link tp-link-{state}" x1="{cx - nx:.0f}" y1="{cy - ny:.0f}" '
                     f'x2="{cx + nx:.0f}" y2="{cy + ny:.0f}"/>')
    return "".join(ticks)


_PORT_LABEL_INSET = 18      # 끝점에서 선을 따라 안쪽으로 들어가는 거리
_PORT_LABEL_OFFSET = 6      # 선에서 직각으로 비키는 거리


def _port_labels(edge, x1, y1, x2, y2):
    """양 끝 인터페이스명 — 어느 포트에 꽂혀 있는지가 도면의 핵심 정보다.

    끝점에 그대로 쓰면 장비 기호 위에 얹혀 둘 다 안 읽힌다. 선을 따라 안쪽으로 조금
    들어온 지점에 놓고, 선과 겹치지 않게 직각 방향으로 비킨다.
    """
    import math

    length = math.hypot(x2 - x1, y2 - y1) or 1
    ux, uy = (x2 - x1) / length, (y2 - y1) / length     # 단위 방향 벡터
    nx, ny = -uy, ux                                    # 직각 방향
    # 라벨을 선의 어느 쪽에 둘지는 링크마다 일정해야 한다(양 끝이 반대쪽이면 지그재그로 보인다).
    inset = min(_PORT_LABEL_INSET, length / 2 - 2) if length > 8 else 0
    out = []
    for port, (px, py), direction in ((edge.get("a_port"), (x1, y1), 1),
                                      (edge.get("b_port"), (x2, y2), -1)):
        if not port:
            continue
        cx = px + ux * inset * direction + nx * _PORT_LABEL_OFFSET
        cy = py + uy * inset * direction + ny * _PORT_LABEL_OFFSET
        # 거의 수평인 선은 글자가 선에 닿으므로 위로 한 번 더 올린다(글자는 baseline 기준).
        if abs(uy) < 0.35:
            cy -= 3
        out.append(f'<text class="tp-port" x="{cx:.0f}" y="{cy:.0f}" '
                   f'text-anchor="middle">{escape(_short_port(port))}</text>')
    return "".join(out)


def _short_port(port):
    """'Ethernet3' -> 'Et3'. 도면에서는 짧아야 읽힌다(관례적 축약)."""
    for long, short in (("Port-Channel", "Po"), ("TenGigabitEthernet", "Te"),
                        ("GigabitEthernet", "Gi"), ("FortyGigE", "Fo"),
                        ("Ethernet", "Et"), ("Management", "Ma"), ("Vlan", "Vl"),
                        ("Loopback", "Lo")):
        if port.startswith(long):
            return short + port[len(long):]
    return port


def _midpoint_label(edge, x1, y1, x2, y2, slot=0.5):
    """묶음 이름과 링크 설명을 선 위에 — 둘 다 있으면 두 줄. slot 은 겹침을 피한 위치(0~1)."""
    lines = []
    if edge.get("bundle"):
        lines.append(("tp-bundle-label", f"{_short_port(edge['bundle'])} ×{edge.get('count', 1)}"))
    if edge.get("label"):
        lines.append(("tp-link-desc", edge["label"]))
    if not lines:
        return ""
    cx, cy = x1 + (x2 - x1) * slot, y1 + (y2 - y1) * slot
    out = []
    for index, (cls, text) in enumerate(lines):
        out.append(f'<text class="{cls}" x="{cx:.0f}" y="{cy + index * 11 - 3:.0f}" '
                   f'text-anchor="middle">{escape(text)}</text>')
    return "".join(out)


def _state_mark(state, x1, y1, x2, y2):
    """DOWN 은 ✕, 일부 DOWN 은 반쪽 원 — 색만으로 구별하면 색약/흑백 인쇄에서 사라진다."""
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    if state == "down":
        r = 5
        return (f'<g class="tp-mark-down">'
                f'<line x1="{cx - r}" y1="{cy - r}" x2="{cx + r}" y2="{cy + r}"/>'
                f'<line x1="{cx + r}" y1="{cy - r}" x2="{cx - r}" y2="{cy + r}"/></g>')
    if state == "degraded":
        return (f'<circle class="tp-mark-degraded" cx="{cx}" cy="{cy}" r="5"/>'
                f'<path class="tp-mark-degraded" d="M{cx} {cy - 5} A5 5 0 0 1 {cx} {cy + 5} Z"/>')
    return ""


# ---------- 노드 ----------
def _nodes(topology):
    out = []
    for node in topology.get("nodes") or []:
        if "x" not in node:
            continue
        x, y = node["x"], node["y"]
        symbol = _symbol_id(node)
        classes = ["tp-node"]
        if not node.get("registered", True):
            classes.append("tp-node-unregistered")
        if not node.get("has_log", True):
            classes.append("tp-node-nolog")
        name_y = y + ICON_SIZE + 14
        body = [
            f'<use href="#{symbol}" x="{x - _ICON_HALF:.0f}" y="{y:.0f}" '
            f'width="{ICON_SIZE}" height="{ICON_SIZE}"/>',
            f'<text class="tp-node-name" x="{x:.0f}" y="{name_y:.0f}" '
            f'text-anchor="middle">{escape(node.get("name") or "")}</text>',
        ]
        if node.get("ip"):
            body.append(f'<text class="tp-node-ip" x="{x:.0f}" y="{name_y + 12:.0f}" '
                        f'text-anchor="middle">{escape(node["ip"])}</text>')
        elif not node.get("registered", True):
            body.append(f'<text class="tp-node-ip" x="{x:.0f}" y="{name_y + 12:.0f}" '
                        f'text-anchor="middle">미등록</text>')
        out.append(f'<g class="{" ".join(classes)}" data-tp-node={quoteattr(node["id"])} '
                   f'transform="translate(0,0)">' + "".join(body) + "</g>")
    return "".join(out)


def _symbol_id(node):
    return {
        "l2switch": "tp-sym-l2switch", "l3switch": "tp-sym-l3switch",
        "router": "tp-sym-router", "firewall": "tp-sym-firewall",
    }.get(node.get("kind"), "tp-sym-unknown")


# ---------- 범례 ----------
_LEGEND_ROWS = (
    ("symbol", "tp-sym-l3switch", "L3 스위치"),
    ("symbol", "tp-sym-l2switch", "L2 스위치"),
    ("symbol", "tp-sym-router", "라우터"),
    ("symbol", "tp-sym-unknown", "미등록 장비"),
    ("line", "tp-link tp-link-up", "링크 정상"),
    ("line", "tp-link tp-link-up tp-link-bundle", "Port-Channel 묶음"),
    ("line", "tp-link tp-link-degraded tp-link-bundle", "묶음 일부 DOWN"),
    ("line", "tp-link tp-link-down", "링크 DOWN"),
    ("line", "tp-link tp-link-unknown", "판정 불가"),
    ("line", "tp-link tp-link-up tp-link-onesided", "한쪽만 관측"),
)


def _legend(height):
    """범례 — 기호를 쓰는 그림에 이것이 없으면 규칙이 전달되지 않는다."""
    col_w, row_h = 168, 17
    rows_per_col = 5
    cols = (len(_LEGEND_ROWS) + rows_per_col - 1) // rows_per_col
    box_w, box_h = col_w * cols + 16, row_h * rows_per_col + 24
    x0, y0 = 16, height - box_h - 12
    out = ['<g class="tp-legend">',
           f'<rect class="tp-legend-box" x="{x0}" y="{y0}" width="{box_w}" height="{box_h}" rx="6"/>',
           f'<text class="tp-legend-title" x="{x0 + 10}" y="{y0 + 15}">범례</text>']
    for index, (kind, ref, label) in enumerate(_LEGEND_ROWS):
        col, row = divmod(index, rows_per_col)
        x = x0 + 10 + col * col_w
        y = y0 + 30 + row * row_h
        if kind == "symbol":
            out.append(f'<use href="#{ref}" x="{x}" y="{y - 9}" width="12" height="12"/>')
        else:
            out.append(f'<line class="{ref}" x1="{x}" y1="{y - 3}" x2="{x + 14}" y2="{y - 3}"/>')
        out.append(f'<text class="tp-legend-text" x="{x + 20}" y="{y}">{escape(label)}</text>')
    out.append("</g>")
    return "".join(out)
