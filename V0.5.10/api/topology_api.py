"""TopologyApiMixin — '네트워크 구성도' 탭.

구성은 두 곳에서 온다:
  * **구조** — 최신 점검 회차의 수집 로그(runs/<run>/raw/*.txt). LLDP 이웃, Port-Channel 소속,
    MLAG peer-link, 인터페이스 설명. 회차가 바뀌지 않으면 다시 파싱하지 않는다(캐시).
  * **지금 상태** — 실시간 감시의 `BaselineDiffEngine.open_conditions()`. 거기서 component_id 가
    정규화된 인터페이스명(`Ethernet3`)이라 링크의 한쪽 끝을 이름 변환 없이 그대로 찾는다.
    세션 syslog 로 잡힌 DOWN 과 상태 폴링으로 잡힌 DOWN 이 이미 같은 축으로 합쳐져 있어서
    여기서 출처를 구분할 필요가 없다.

'정상'이라고 말할 근거가 없으면 초록으로 칠하지 않는다 — 실시간 감시가 그 장비에서 아무 상태도
관측하지 못했다면 링크는 회색(판정 불가)이다. 이 화면도 점검 근거로 쓰이므로 같은 원칙이다.
"""
import glob
import os
import time


class TopologyApiMixin:
    # ---------- 공개 API ----------
    def get_network_topology(self, live=True):
        """구성도 한 벌. 반환: {ok, run_id, version, svg, width, height,
        nodes, edges, pairs, warnings, live, generated_at}

        version 은 구조+상태의 지문이다. 프론트엔드는 이 값이 바뀔 때만 SVG 를 갈아 끼운다 —
        폴링마다 DOM 을 통째로 교체하면 줌/팬과 선택 강조가 매번 튄다.
        """
        built = self._topology_structure()
        if built.get("error"):
            return {"ok": False, **built}

        topology, run_id = built["topology"], built["run_id"]
        live_state = self._topology_live_state() if live else {}
        _apply_live_state(topology, live_state)

        from engine.topology_layout import layout
        from engine.topology_svg import render_svg

        layout_info = layout(topology, self._topology_layout_positions())
        svg = render_svg(topology, layout_info,
                         title=f"네트워크 구성도 — {run_id or '점검 회차 없음'}")
        return {
            "ok": True,
            "run_id": run_id,
            "version": _fingerprint(topology, layout_info),
            "svg": svg,
            "width": layout_info["width"], "height": layout_info["height"],
            "nodes": topology["nodes"], "edges": topology["edges"],
            "pairs": topology["pairs"], "warnings": topology["warnings"],
            "live": live_state,
            "generated_at": time.time(),
        }

    def save_topology_svg(self):
        """구성도를 프로파일의 reports/ 에 SVG 로 저장하고 폴더를 연다.

        standalone=True 로 렌더한다 — 내보낸 파일은 앱 밖에서 열리므로 CSS 변수를 해석해 줄
        주체가 없다. 화면과 파일이 같은 렌더러를 쓰되 색만 인라인으로 심는다.
        """
        result = self.get_network_topology(live=True)
        if not result.get("ok"):
            return result

        from engine import log_storage
        from engine.topology_layout import layout
        from engine.topology_svg import render_svg
        from core.atomic_io import write_text_atomic

        built = self._topology_structure()
        topology = built["topology"]
        _apply_live_state(topology, self._topology_live_state())
        layout_info = layout(topology, self._topology_layout_positions())
        svg = render_svg(topology, layout_info, standalone=True,
                         title=f"네트워크 구성도 — {built['run_id'] or ''}")

        customer, profile = self.resolve_active_customer_profile_names()
        paths = self._active_profile_log_paths()
        target_dir = (paths or {}).get("reports") or str(
            log_storage.get_profile_dir(customer, profile))
        name = f"topology_{time.strftime('%Y%m%d_%H%M%S')}.svg"
        path = os.path.join(target_dir, name)
        try:
            os.makedirs(target_dir, exist_ok=True)
            write_text_atomic(path, svg)
        except OSError as exc:
            return {"ok": False, "error": f"저장 실패: {exc}"}
        log_storage.open_in_file_explorer(target_dir)
        return {"ok": True, "path": path, "name": name}

    def save_topology_layout(self, positions=None):
        """사용자가 끌어 옮긴 노드 좌표를 저장한다. positions: {장비명: [x, y]}

        빈 dict 를 주면 '자동 배치로 되돌리기'다 — None(=변경 없음)과 구별해야 한다.
        """
        if positions is None:
            return {"ok": True, **self._topology_layout_config()}
        cleaned = {}
        for name, point in (positions or {}).items():
            try:
                cleaned[str(name)] = [round(float(point[0]), 1), round(float(point[1]), 1)]
            except (TypeError, ValueError, IndexError):
                continue
        self._write_topology_config({self._topology_layout_key(): cleaned})
        return {"ok": True, "positions": cleaned}

    def reset_topology_layout(self):
        """'자동 배치로 되돌리기' — 저장된 좌표를 비운다."""
        return self.save_topology_layout({})

    def get_topology_diagnostics(self):
        """'왜 구성도가 비어 있나'를 화면에서 확인하기 위한 진단.

        구성도가 안 그려지는 원인은 거의 전부 (1) 점검 로그가 없다 (2) LLDP 출력이 없다
        (3) LLDP 가 말한 이름이 장비 목록과 다르다 — 셋 중 하나다. 빌더가 warnings 로
        이미 알려 주므로 그것을 장비별 표로 풀어 준다.
        """
        built = self._topology_structure()
        if built.get("error"):
            return {"ok": False, "error": built["error"], "rows": []}
        topology, sections = built["topology"], built["sections"]
        from parsers.show_lldp_neighbors import parse_lldp_neighbors
        from engine.topology_builder import _section

        rows = []
        for node in topology["nodes"]:
            name = node["name"]
            device_sections = sections.get(name) or {}
            lldp_text = _section(device_sections, "lldp neighbors")
            rows.append({
                "device": name,
                "registered": node.get("registered", True),
                "has_log": bool(device_sections),
                "commands": len(device_sections),
                "has_lldp_output": bool(lldp_text.strip()),
                "neighbors": len(parse_lldp_neighbors(lldp_text)),
                "kind": node.get("kind"),
                "tier": node.get("tier"),
            })
        return {"ok": True, "run_id": built["run_id"], "rows": rows,
                "warnings": topology["warnings"],
                "edge_count": len(topology["edges"]),
                "raw_dir": built.get("raw_dir", "")}

    # ---------- 구조 (캐시) ----------
    def _topology_structure(self):
        """최신 회차의 raw 로그에서 구조를 만든다. 회차·파일이 그대로면 캐시를 쓴다.

        캐시가 필요한 이유: 장비 30대 × 3천 줄을 폴링마다 다시 파싱하면 UI 가 밀린다.
        키에 파일 mtime 합을 넣어, 같은 회차 안에서 로그가 다시 쓰인 경우도 잡는다.
        """
        paths = self._active_profile_log_paths()
        if not paths:
            return {"error": "점검 이력이 없습니다. 먼저 점검을 1회 수행하세요."}
        raw_dir = paths["original"]
        files = sorted(glob.glob(os.path.join(raw_dir, "*.txt")))
        if not files:
            return {"error": "점검 로그가 없습니다. 먼저 점검을 1회 수행하세요."}

        stamp = 0.0
        for path in files:
            try:
                stamp += os.path.getmtime(path)
            except OSError:
                pass
        key = (paths.get("run_id"), len(files), round(stamp, 3), self._topology_inventory_key())
        cached = getattr(self, "_topology_cache", None)
        if cached and cached.get("key") == key:
            return cached["value"]

        from engine.topology_builder import build_topology
        from report.inspection_excel import split_transcript
        from core.text_io import read_log_text
        from api.log_file_browser_api import _parse_terminal_session_filename

        sections = {}
        for path in files:
            device = _parse_terminal_session_filename(os.path.basename(path))
            if not device:
                continue
            try:
                text = read_log_text(path)
            except (OSError, UnicodeDecodeError):
                continue
            # 같은 장비의 로그가 여럿이면 나중 파일(=최신)이 이긴다.
            sections[device] = split_transcript(text)

        devices = self._topology_devices()
        value = {"topology": build_topology(devices, sections), "sections": sections,
                 "run_id": paths.get("run_id"), "raw_dir": raw_dir}
        self._topology_cache = {"key": key, "value": value}
        return value

    def _topology_devices(self):
        """구성도에 쓸 '입력된 정보' — 장비 목록의 전체 필드(역할·모델·IP까지).

        _resolve_terminal_targets() 를 쓰지 않는다: 그건 접속용이라 role/model/vendor 가 없다.
        """
        try:
            inventory = self._load_inventory(self._paths())
        except (RuntimeError, KeyError, OSError):
            return []
        return [d for d in (inventory.get("devices") or []) if d.get("enabled", True)]

    def _topology_inventory_key(self):
        """장비 목록이 바뀌면 구조도 다시 만들어야 한다(이름/역할이 배치와 기호를 바꾼다)."""
        return tuple(sorted((d.get("name") or "", d.get("role") or "")
                            for d in self._topology_devices()))

    # ---------- 지금 상태 ----------
    def _topology_live_state(self):
        """{"down": {(장비, 인터페이스)...}, "observed": {장비...}, "running": bool, ...}

        링크 상태의 단일 출처는 실시간 감시의 open_conditions() 다. 여기서 새로 판정하지 않는다 —
        두 곳에서 판정하면 '구성도는 빨간데 실시간 감시는 정상'인 화면이 나온다.
        """
        watcher = getattr(self, "_baseline_stream_watcher", None)
        engine = getattr(self, "_baseline_diff_engine", None)
        state = {"running": bool(watcher and watcher.is_running()),
                 "down": [], "observed": [], "unresolved_alerts": {}}
        if engine is None:
            return state

        for condition in engine.open_conditions():
            if condition.get("condition") != "interface_down":
                continue
            device, component = condition.get("device"), condition.get("component_id")
            if device and component:
                state["down"].append([device, component])

        # 어느 장비의 상태를 볼 수 있었는지 — 못 본 장비의 링크는 '정상'이 아니라 '판정 불가'다.
        try:
            observations = engine.observations()
        except Exception:
            observations = {}
        for device, counts in (observations or {}).items():
            if (counts.get("syslog") or 0) or (counts.get("polled") or 0):
                state["observed"].append(device)

        monitor = getattr(self, "_realtime_monitor_obj", None)
        if monitor is not None:
            for alert in monitor.alerts(limit=400, include_hidden=False):
                if alert.get("resolved") or alert.get("ignored"):
                    continue
                device = alert.get("device")
                if device:
                    state["unresolved_alerts"][device] = \
                        state["unresolved_alerts"].get(device, 0) + 1
        return state

    # ---------- 배치 저장 ----------
    def _topology_config_path(self):
        from core.paths import AppPaths
        return str(AppPaths.config_root() / "topology_layout.yaml")

    def _topology_layout_key(self):
        """프로파일마다 배치가 다르다 — 고객사/회차가 바뀌면 장비도 다르다."""
        try:
            customer, profile = self.resolve_active_customer_profile_names()
        except Exception:
            customer, profile = None, None
        return f"{customer or '_'}/{profile or '_'}"

    def _read_topology_config(self):
        import yaml
        path = self._topology_config_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            return {}

    def _write_topology_config(self, updates):
        """부분 갱신 — 한 프로파일의 배치를 저장할 때 다른 프로파일 것을 날리지 않는다
        (api/log_analysis_run_api.py 의 _write_realtime_watch_config 와 같은 이유)."""
        from core.atomic_io import dump_yaml_atomic
        config = self._read_topology_config()
        config.update(updates)
        path = self._topology_config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        dump_yaml_atomic(config, path)
        return config

    def _topology_layout_config(self):
        return {"positions": self._topology_layout_positions()}

    def _topology_layout_positions(self):
        raw = self._read_topology_config().get(self._topology_layout_key()) or {}
        if not isinstance(raw, dict):
            return {}
        out = {}
        for name, point in raw.items():
            if isinstance(point, (list, tuple)) and len(point) == 2:
                try:
                    out[str(name)] = [float(point[0]), float(point[1])]
                except (TypeError, ValueError):
                    continue
        return out


# ---------- 상태 오버레이 ----------
def _apply_live_state(topology, live):
    """실시간 감시의 판정을 링크에 얹는다.

    규칙 세 가지:
      * open_conditions 에 interface_down 이 있는 끝점을 가진 링크는 down.
      * 묶음은 멤버 단위로 보고, 일부만 down 이면 degraded(이중화가 이미 깎였다).
      * 상태를 관측할 수 없는 장비의 링크는 **점검 시점 값을 그대로 두지 않고** unknown 으로
        내린다 — 실시간 감시가 보고 있지 않은데 초록으로 두면 '지금 정상'으로 읽힌다.
    """
    if not live or not live.get("running"):
        return          # 감시가 꺼져 있으면 점검 시점의 판정을 그대로 보여준다
    down = {(d, i) for d, i in (live.get("down") or [])}
    observed = set(live.get("observed") or [])
    for edge in topology.get("edges") or []:
        states = []
        for member in edge.get("members") or [{"a_port": edge.get("a_port"),
                                               "b_port": edge.get("b_port")}]:
            ends = ((edge["a"], member.get("a_port")), (edge["b"], member.get("b_port")))
            if any(end in down for end in ends):
                member["state"] = "down"
            elif any(end[0] in observed for end in ends):
                member["state"] = "up"
            else:
                member["state"] = "unknown"
            states.append(member["state"])
        if any(s == "down" for s in states):
            edge["state"] = "down" if all(s == "down" for s in states) else "degraded"
        elif all(s == "up" for s in states):
            edge["state"] = "up"
        else:
            edge["state"] = "unknown"


def _fingerprint(topology, layout_info):
    """구조+상태+배치의 짧은 지문 — 프론트엔드가 이 값이 바뀔 때만 SVG 를 갈아 끼운다."""
    import hashlib
    import json

    payload = {
        "nodes": [(n["id"], n.get("kind"), n.get("tier"), round(n.get("x", 0), 1),
                   round(n.get("y", 0), 1)) for n in topology.get("nodes") or []],
        "edges": [(e["id"], e.get("state"), e.get("count")) for e in topology.get("edges") or []],
        "pairs": [(p["a"], p["b"], p.get("healthy")) for p in topology.get("pairs") or []],
        "size": (layout_info["width"], layout_info["height"]),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(encoded.encode("utf-8")).hexdigest()[:16]
