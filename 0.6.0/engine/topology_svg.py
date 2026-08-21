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
    pair_markup, pair_labels = _pair_boxes(topology, nodes)
    parts.append(pair_markup)
    parts.append(_links(topology, nodes, pair_labels))
    parts.append(_nodes(topology))
    parts.append(_legend(topology, width, height))
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
.tp-node-name{{fill:{p['text']};font-size:12px;font-weight:700;
  paint-order:stroke;stroke:{p['bg']};stroke-width:3px;stroke-linejoin:round}}
.tp-node-ip{{fill:{p['sub']};font-size:10px;
  paint-order:stroke;stroke:{p['bg']};stroke-width:3px;stroke-linejoin:round}}
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
.tp-port,.tp-bundle-label,.tp-link-desc,.tp-pair-label{{
  paint-order:stroke;stroke:{p['bg']};stroke-width:2.5px;stroke-linejoin:round}}
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
    """이중화 쌍을 점선 상자로 감싼다 — 두 장비가 하나의 논리 장비처럼 동작한다는 표시.

    반환: (마크업, 쌍 이름표가 차지한 자리) — 자리는 링크 라벨이 그 위에 겹치지 않게 넘겨준다.
    """
    out, reserved = [], []
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
        # 쌍이 나란히 있지 않으면(계층 판정이 엇갈렸거나 사용자가 끌어 옮겼다) 상자가 사이의
        # 다른 장비까지 감싼다 — 그러면 '이 셋이 한 쌍'으로 읽힌다. 그럴 땐 상자를 그리지 않는다.
        others = [(n["x"] - NODE_W / 2, n["y"], NODE_W, NODE_H) for n in nodes.values()
                  if n["id"] not in (pair.get("a"), pair.get("b")) and "x" in n]
        if any(_boxes_overlap((left, top, right - left, bottom - top), box) for box in others):
            continue
        bad = "" if pair.get("healthy", True) else " tp-pair-box-bad"
        label = f"MLAG {pair.get('domain') or ''}".strip()
        reserved.append(_text_box(label, 9, left + 6 + _text_box(label, 9, 0, 0)[2] / 2, top - 4))
        out.append(
            f'<g class="tp-pair" data-tp-pair={quoteattr(f"{pair.get('a')}|{pair.get('b')}")}>'
            f'<rect class="tp-pair-box{bad}" x="{left:.0f}" y="{top:.0f}" '
            f'width="{right - left:.0f}" height="{bottom - top:.0f}" rx="8"/>'
            f'<text class="tp-pair-label" x="{left + 6:.0f}" y="{top - 4:.0f}">{escape(label)}</text>'
            f'</g>')
    return "".join(out), reserved


# ---------- 링크 ----------
def _links(topology, nodes, reserved=()):
    geometry = _link_geometry(topology, nodes)
    placer = _LabelPlacer(topology, reserved)
    out = []
    for edge in topology.get("edges") or []:
        geom = geometry.get(edge["id"])
        if geom is None:
            continue
        state = edge.get("state") or "unknown"
        classes = ["tp-link", f"tp-link-{state}"]
        if edge.get("count", 1) > 1:
            classes.append("tp-link-bundle")
        if edge.get("one_sided"):
            classes.append("tp-link-onesided")
        body = [f'<path class="{" ".join(classes)}" d="{_path_d(geom)}"/>']
        if edge.get("count", 1) > 1:
            body.append(_bracket(geom, state))
        body.append(_port_labels(edge, geom, placer))
        body.append(_midpoint_label(edge, geom, placer))
        body.append(_state_mark(state, geom))
        out.append(f'<g class="tp-link-group" data-tp-link={quoteattr(edge["id"])}>'
                   + "".join(p for p in body if p) + "</g>")
    return "".join(out)


_FAN_SPREAD = 34        # 위/아래로 나가는 링크를 펼칠 폭
_FAN_SPREAD_LEVEL = 20  # 좌/우로 나가는 링크를 펼칠 높이
_AVOID_MARGIN = 15      # 남의 기호를 비껴갈 때 남기는 여유
_MAX_BOW = 260          # 아무리 멀리 돌아가도 이만큼까지 — 화면 밖으로 나가면 더 나쁘다


def _link_geometry(topology, nodes):
    """각 링크의 기하. 반환: {edge_id: (x1, y1, x2, y2, cx, cy)} — cx 가 None 이면 직선.

    같은 노드에서 같은 방향으로 나가는 링크들을 **기호 가장자리에 부채꼴로 펼친다.**
    한 점에서 모두 나가면 선이 겹쳐 몇 개인지 보이지 않고, 포트 라벨도 같은 자리에 쌓인다
    (이중화 구성에서는 거의 항상 그렇게 된다 — Core1 에서 Agg1·Agg2 로 두 줄이 나간다).
    실제 도면에서 연결점을 장비 면에 나눠 그리는 것과 같은 이유다.

    펼치는 순서는 '상대 노드의 x 좌표' 순이다 — 그래야 선이 불필요하게 교차하지 않는다.

    그리고 **직선이 남의 장비를 관통하면 그 장비를 비껴 휘게 한다**(_route). 계층형 배치는
    위아래 이웃 계층 사이만 곧게 이을 수 있다. 링·풀메시처럼 같은 계층끼리 잇는 구성이나
    계층을 건너뛰는 직결(Core→Access, 방화벽→관리망)에서는 직선이 중간 장비 위를 그대로
    지나가 '어디에 붙은 선인지' 읽을 수 없게 된다 — 실제 도면에서 선을 돌려 그리는 이유다.
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
        # 피해 갈 대상은 **기호 상자만** 이다. 장비명 줄까지 피하게 하면 촘촘한 Access 줄에서
        # 선이 크게 돌다가 오히려 다른 기호를 지나간다(실측). 이름 위를 지나가는 선은
        # 이름에 배경색 테두리를 둘러 읽히게 한다(style.css 의 .tp-node-name).
        blockers = [_icon_box(n) for n in positioned.values()
                    if n["id"] not in (edge["a"], edge["b"])]
        cx, cy = _route(x1, y1, x2, y2, blockers)
        geometry[edge["id"]] = (x1, y1, x2, y2, cx, cy)
    return geometry


def _icon_box(node):
    return (node["x"] - _ICON_HALF, node["y"], ICON_SIZE, ICON_SIZE)


def _node_boxes(node):
    """기호 상자 + 그 아래 장비명·IP 줄. 선과 라벨은 둘 다 피해야 한다 —
    이름 위로 선이 지나가면 이름을 못 읽고, 이름은 기호보다 넓은 경우가 대부분이다."""
    bx, by, bw, bh = _icon_box(node)
    text_w = max(_text_box(node.get("name") or "", 12, 0, 0)[2],
                 _text_box(node.get("ip") or "", 10, 0, 0)[2], ICON_SIZE) + 8
    return [(bx, by, bw, bh), (node["x"] - text_w / 2, by + bh, text_w, 30)]


def _route(x1, y1, x2, y2, blockers):
    """직선이 남의 기호를 지나면 비껴갈 2차 베지어 제어점을 돌려준다. 안 지나면 (None, None).

    2차 베지어는 t 에서 현(弦)으로부터 `2t(1-t)·(C-M)` 만큼 휜다(M 은 현의 중점). 그래서
    '이 장비를 이만큼 비켜야 한다'는 요구를 제어점 거리로 바로 환산할 수 있다. 어느 쪽으로
    휘는지는 **덜 휘어도 되는 쪽**으로 정한다 — 결정적이고, 불필요하게 크게 돌지 않는다.
    """
    import math

    length = math.hypot(x2 - x1, y2 - y1)
    if length < 1:
        return None, None
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    nx, ny = -uy, ux
    need_pos = need_neg = 0.0
    for box in blockers:
        if not _segment_hits_box(x1, y1, x2, y2, box):
            continue
        bx, by, bw, bh = box
        mx, my = bx + bw / 2, by + bh / 2
        along = ((mx - x1) * ux + (my - y1) * uy) / length
        along = min(0.88, max(0.12, along))
        profile = 2 * along * (1 - along)               # 그 지점에서의 휨 비율
        side = (mx - x1) * nx + (my - y1) * ny          # 부호 있는 거리
        reach = abs(nx) * bw / 2 + abs(ny) * bh / 2 + _AVOID_MARGIN
        need_pos = max(need_pos, (side + reach) / profile)
        need_neg = max(need_neg, (reach - side) / profile)
    if not need_pos and not need_neg:
        return None, None
    bow = need_pos if need_pos <= need_neg else -need_neg
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    # 휘게 했더니 **다른** 장비를 지나는 경우가 있다(피한 쪽에 또 장비가 있는 배치).
    # 곡선을 실제로 훑어 보고, 아직 걸리면 조금 더 크게 돌린다.
    for _ in range(4):
        bow = max(-_MAX_BOW, min(_MAX_BOW, bow))
        cx, cy = mx + nx * bow, my + ny * bow
        if not _curve_hits_any((x1, y1, x2, y2, cx, cy), blockers):
            return cx, cy
        if abs(bow) >= _MAX_BOW:
            break
        bow += 46 if bow >= 0 else -46
    return cx, cy


def _curve_hits_any(geom, blockers):
    """곡선을 훑어 남의 기호 안으로 들어가는지 본다(제어점 하나짜리라 촘촘히 볼 필요는 없다)."""
    points = [_point_at(geom, i / 24) for i in range(1, 24)]
    for bx, by, bw, bh in blockers:
        if any(bx <= px <= bx + bw and by <= py <= by + bh for px, py in points):
            return True
    return False


def _segment_hits_box(x1, y1, x2, y2, box):
    """선분이 사각형을 지나는가 — Liang-Barsky 절단."""
    bx, by, bw, bh = box
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - bx), (dx, bx + bw - x1), (-dy, y1 - by), (dy, by + bh - y1)):
        if p == 0:
            if q < 0:
                return False
            continue
        r = q / p
        if p < 0:
            if r > t1:
                return False
            t0 = max(t0, r)
        else:
            if r < t0:
                return False
            t1 = min(t1, r)
    return t0 <= t1


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


# ---------- 곡선 위의 점 ----------
def _path_d(geom):
    x1, y1, x2, y2, cx, cy = geom
    if cx is None:
        return f"M{x1:.1f} {y1:.1f} L{x2:.1f} {y2:.1f}"
    return f"M{x1:.1f} {y1:.1f} Q{cx:.1f} {cy:.1f} {x2:.1f} {y2:.1f}"


def _point_at(geom, t):
    x1, y1, x2, y2, cx, cy = geom
    if cx is None:
        return x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
    k = 1 - t
    return (k * k * x1 + 2 * k * t * cx + t * t * x2,
            k * k * y1 + 2 * k * t * cy + t * t * y2)


def _unit_at(geom, t):
    """그 지점에서의 진행 방향(단위 벡터)."""
    import math

    x1, y1, x2, y2, cx, cy = geom
    if cx is None:
        dx, dy = x2 - x1, y2 - y1
    else:
        k = 1 - t
        dx = 2 * k * (cx - x1) + 2 * t * (x2 - cx)
        dy = 2 * k * (cy - y1) + 2 * t * (y2 - cy)
    length = math.hypot(dx, dy) or 1
    return dx / length, dy / length


def _chord(geom):
    import math

    return math.hypot(geom[2] - geom[0], geom[3] - geom[1]) or 1


def _bracket(geom, state):
    """묶음 표시 — 선의 양 끝에 짧은 직교 눈금을 넣는다(LAG 관례)."""
    ticks = []
    for t in (0.12, 0.88):
        px, py = _point_at(geom, t)
        ux, uy = _unit_at(geom, t)
        nx, ny = -uy * 5, ux * 5
        ticks.append(f'<line class="tp-link tp-link-{state}" x1="{px - nx:.0f}" y1="{py - ny:.0f}" '
                     f'x2="{px + nx:.0f}" y2="{py + ny:.0f}"/>')
    return "".join(ticks)


# ---------- 라벨 자리 잡기 ----------
# 라벨은 선 위 어디에 놓아도 되지만, **겹치면 하나도 못 읽는다.** 팬아웃이 큰 구성
# (Core 1대에 Access 24대)에서는 고정 위치로는 반드시 쌓인다. 그래서 후보 지점을 선을 따라
# 여러 개 만들어 두고 이미 놓인 라벨·기호와 겹치지 않는 첫 자리를 고른다. 순서가 고정돼 있어
# (edges 는 빌더에서 정렬돼 온다) 같은 입력에 같은 그림이 나온다.
_PORT_LABEL_OFFSET = 6      # 선에서 직각으로 비키는 거리
# 끝점에서 선을 따라 들어가는 거리 후보 — 앞쪽이 우선. 팬아웃이 큰 장비(Core 1대에 Access
# 24대)에서는 가까운 자리가 금방 차므로 멀리까지 후보를 둔다.
_PORT_LABEL_STEPS = (16, 27, 38, 49, 60, 72, 86, 100, 116, 134, 154)
_MID_LABEL_SLOTS = (0.5, 0.42, 0.58, 0.34, 0.66, 0.28, 0.72, 0.22, 0.78)


class _LabelPlacer:
    """이미 놓인 글자 상자와 장비 기호를 기억했다가 겹치지 않는 자리를 고른다."""

    def __init__(self, topology, reserved=()):
        self.taken = list(reserved)
        for node in topology.get("nodes") or []:
            if "x" in node:
                # 기호와 장비명·IP 줄 — 라벨이 이 위에 얹히면 둘 다 못 읽는다.
                self.taken.extend(_node_boxes(node))

    def place(self, candidates):
        """candidates: [(x, y, w, h)] 우선순위 순. 겹치지 않는 첫 상자를 잡아 돌려준다.

        전부 겹치면 첫 후보를 그대로 쓴다 — 라벨을 지우면 '어느 포트인지'를 잃는다.
        겹쳐서라도 그리는 편이 낫고, 그 상태는 노드를 끌어 배치를 고치면 풀린다.
        """
        for box in candidates:
            if not any(_boxes_overlap(box, other) for other in self.taken):
                self.taken.append(box)
                return box
        self.taken.append(candidates[0])
        return candidates[0]


def _boxes_overlap(a, b):
    return (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
            and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])


def _text_box(text, size, cx, baseline):
    """가운데 정렬된 글자의 대략적인 상자. 한글은 폭이 글자 크기와 거의 같다."""
    width = sum(size * (1.0 if ord(ch) > 0x2E80 else 0.56) for ch in text)
    return (cx - width / 2, baseline - size * 0.8, width, size + 2)


def _port_labels(edge, geom, placer):
    """양 끝 인터페이스명 — 어느 포트에 꽂혀 있는지가 도면의 핵심 정보다.

    끝점에 그대로 쓰면 장비 기호 위에 얹혀 둘 다 안 읽힌다. 선을 따라 안쪽으로 들어온
    지점에 놓고, 선과 겹치지 않게 직각 방향으로 비킨다. 그 자리가 이미 찼으면 더 안쪽으로
    물러난다 — 한 장비에서 여러 링크가 나가면 첫 자리는 서로 겹칠 수밖에 없다.
    """
    chord = _chord(geom)
    out = []
    for port, from_start in ((edge.get("a_port"), True), (edge.get("b_port"), False)):
        if not port:
            continue
        text = _short_port(port)
        candidates = []
        for step in _PORT_LABEL_STEPS:
            if step > chord * 0.45 and candidates:
                break
            t = min(0.45, step / chord)
            t = t if from_start else 1 - t
            px, py = _point_at(geom, t)
            ux, uy = _unit_at(geom, t)
            # 선의 양쪽, 두 가지 거리 — 팬아웃이 큰 장비에서는 한쪽 줄만으로는 자리가 모자란다.
            for offset in (_PORT_LABEL_OFFSET, -_PORT_LABEL_OFFSET,
                           _PORT_LABEL_OFFSET + 9, -_PORT_LABEL_OFFSET - 9):
                cx = px - uy * offset
                cy = py + ux * offset
                # 거의 수평인 선은 글자가 선에 닿으므로 위로 한 번 더 올린다(글자는 baseline 기준).
                if abs(uy) < 0.35:
                    cy -= 3 if offset > 0 else -3
                candidates.append(_text_box(text, 9, cx, cy))
        box = placer.place(candidates)
        out.append(f'<text class="tp-port" x="{box[0] + box[2] / 2:.0f}" '
                   f'y="{box[1] + 7.2:.0f}" text-anchor="middle">{escape(text)}</text>')
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


def _midpoint_label(edge, geom, placer):
    """묶음 이름과 링크 설명을 선 위에 — 둘 다 있으면 두 줄.

    Po 이름이 없어도 병렬 링크면 개수(×N)는 반드시 적는다. 병렬 링크는 한 선으로 접히므로
    개수가 없으면 굵은 선 하나로 보이고, 이중화가 몇 가닥인지 도면에서 사라진다. 시스코의
    `show interfaces status` 에는 Po 소속 열이 없어 **묶음 이름 없이 접히는 경우가 흔하다.**
    """
    lines = []
    count = edge.get("count", 1)
    if edge.get("bundle"):
        lines.append(("tp-bundle-label", 10, f"{_short_port(edge['bundle'])} ×{count}"))
    elif count > 1:
        lines.append(("tp-bundle-label", 10, f"×{count}"))
    if edge.get("label"):
        lines.append(("tp-link-desc", 9, edge["label"]))
    if not lines:
        return ""

    block_h = 11 * (len(lines) - 1) + 12
    candidates = []
    for slot in _MID_LABEL_SLOTS:
        cx, cy = _point_at(geom, slot)
        widest = max(_text_box(text, size, cx, cy)[2] for _cls, size, text in lines)
        candidates.append((cx - widest / 2, cy - 11, widest, block_h))
    box = placer.place(candidates)
    cx, top = box[0] + box[2] / 2, box[1]
    out = []
    for index, (cls, _size, text) in enumerate(lines):
        out.append(f'<text class="{cls}" x="{cx:.0f}" y="{top + 8 + index * 11:.0f}" '
                   f'text-anchor="middle">{escape(text)}</text>')
    return "".join(out)


def _state_mark(state, geom):
    """DOWN 은 ✕, 일부 DOWN 은 반쪽 원 — 색만으로 구별하면 색약/흑백 인쇄에서 사라진다."""
    cx, cy = _point_at(geom, 0.5)
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


def _legend(topology, width, height):
    """범례 — 기호를 쓰는 그림에 이것이 없으면 규칙이 전달되지 않는다.

    자리는 왼쪽 아래가 기본이지만 **장비를 덮으면 다른 모서리로 옮긴다.** 노드를 끌어 배치할 수
    있으므로 어느 모서리든 비어 있다는 보장이 없다 — 범례가 장비를 가리면 둘 다 못 읽는다.
    """
    col_w, row_h = 168, 17
    rows_per_col = 5
    cols = (len(_LEGEND_ROWS) + rows_per_col - 1) // rows_per_col
    box_w, box_h = col_w * cols + 16, row_h * rows_per_col + 24
    occupied = [(n["x"] - NODE_W / 2, n["y"], NODE_W, NODE_H)
                for n in topology.get("nodes") or [] if "x" in n]
    corners = [(16, height - box_h - 12), (width - box_w - 16, height - box_h - 12),
               (width - box_w - 16, 16), (16, 16)]
    x0, y0 = corners[0]
    for corner in corners:
        if not any(_boxes_overlap((corner[0], corner[1], box_w, box_h), box)
                   for box in occupied):
            x0, y0 = corner
            break
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
