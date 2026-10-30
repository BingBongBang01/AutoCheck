"""회차 비교 — 같은 고객사의 두 점검(회차, 또는 회차 안의 Run)을 점검 항목·장비 정보·구성도 링크로 비교한다
(IMPROVEMENT_PLAN 4-7).

정기점검 보고서는 '전월 값' 칸으로 지난 회차의 값을 보여 주지만, 무엇이 달라졌는지는 사람이 두 표를 대조해야 했고,
보고서를 만들지 않은 회차와는 비교할 수 없었다(전월 값은 보고서를 만들 때 남기는 스냅샷이다). 여기서는 두 점검의
원본 로그에서 상태를 직접 만든다 — 보고서 판정(inspection_report_builder.build_context)과 구성도
(topology_builder.build_topology)를 그대로 써서, 보고서·구성도 탭이 보여 준 판정끼리 비교한다.

점검 하나(source)는 {"profile": 회차 이름, "run_id": 없으면 None}:
  run_id 가 없으면 그 회차의 장비별 최신 로그(보고서·구성도와 같다), 있으면 그 Run 에서 수집한 로그만.

비교 규칙:
  - 항목: 상태가 나빠짐/좋아짐(정상·해당없음 < 미수집 < 확인필요), 상태가 같으면 값이 바뀐 것. 실측값 항목(CPU·메모리)은
    회차마다 당연히 바뀌므로 상태가 바뀔 때만, 가동 시간은 두 점검 사이에 재부팅했을 때만 알린다.
  - 장비 정보: OS 버전(업그레이드)·S/N(장비 교체)·모델.
  - 링크: 물리 링크(양 끝 장비·포트) 단위로 새로 생김/사라짐/상태 바뀜. 한쪽 끝도 이웃 정보를 내지 못한 점검에서는
    '없다'고 말할 근거가 없으므로 '확인 불가'로 따로 둔다 — 로그가 없는 장비의 링크를 '사라짐'으로 알리면 단선
    경보가 쏟아진다.
"""
import datetime
import os
import re

from engine import inspection_report_builder as report_builder
from engine import log_storage
from engine.profile_manager import profile_manager
from report.inspection_status import STATUS_NA, STATUS_OK, STATUS_SKIP, STATUS_WARN

# 상태의 나쁨 정도 — 커지면 '나빠짐', 작아지면 '좋아짐'. 해당없음은 정상과 같다(볼 것이 없다).
_RANK = {STATUS_OK: 0, STATUS_SKIP: 0, STATUS_NA: 1, STATUS_WARN: 2}
# 회차마다 값이 바뀌는 실측값 — 상태가 바뀔 때만 알린다.
_MEASURED_EVALUATORS = {"cpu", "memory_free"}
_FACTS = (("os_version", "OS 버전"), ("serial", "S/N"), ("model", "모델"))
_UPTIME_UNITS = {"year": 365 * 86400, "week": 7 * 86400, "day": 86400, "hour": 3600, "minute": 60, "second": 1}
_UPTIME_RE = re.compile(r"(\d+)\s*(year|week|day|hour|minute|second)", re.IGNORECASE)
_UPTIME_CLOCK_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")        # Linux top 의 'up 8 days,  1:23'
_REBOOT_SLACK_SEC = 3600                                      # 가동 시간 표기·수집 시각의 어긋남을 넉넉히 봐 준다


class CompareError(Exception):
    """비교할 점검을 찾지 못했다 — 화면에 그대로 보일 문장."""


# ---------------------------------------------------------------------------- 점검 찾기

def project_of(customer, profile):
    """회차 -> 그 회차의 프로젝트 id(장비 목록이 있는 labs/<id>). 없으면 None — 로그만으로도 비교한다."""
    from engine import project_manager as pm
    for project in pm.list_projects():
        if log_storage.resolve_names_from_project(project["id"]) == (customer, profile):
            return project["id"]
    return None


def _has_logs(path):
    try:
        return any(name.lower().endswith(".txt") for name in os.listdir(path))
    except OSError:
        return False


def sources(customer):
    """비교할 수 있는 점검 — 로그가 있는 회차마다 {"profile", "inspection_date", "runs": [로그가 있는 Run, 최신 먼저]}.
    회차는 점검일(없으면 이름) 순, 오래된 것부터."""
    rounds = []
    for profile in profile_manager.list_profiles(customer):
        name = profile.get("name")
        if not name:
            continue
        dirs = [entry for entry in log_storage.iter_log_dirs(customer, name, "original") if _has_logs(entry["path"])]
        if not dirs:
            continue
        rounds.append({"profile": name, "inspection_date": str(profile.get("inspection_date") or ""),
                       "runs": [entry["run_id"] for entry in dirs if entry["run_id"]]})
    rounds.sort(key=lambda r: (r["inspection_date"] or "9999", r["profile"]))
    return rounds


def default_pair(customer, profile, run_id=None):
    """(기준, 비교) 기본값. run_id 가 없으면 이 회차와 바로 앞 회차, 있으면 그 Run 과 같은 회차의 바로 앞 Run(없으면 앞
    회차). 비교할 이전 점검이 없으면 기준이 None."""
    rounds = sources(customer)
    names = [r["profile"] for r in rounds]
    target = {"profile": profile, "run_id": run_id}
    if run_id:
        runs = next((r["runs"] for r in rounds if r["profile"] == profile), [])
        older = runs[runs.index(run_id) + 1:] if run_id in runs else []
        if older:
            return {"profile": profile, "run_id": older[0]}, target
    if profile in names and names.index(profile) > 0:
        return {"profile": names[names.index(profile) - 1], "run_id": None}, target
    if not run_id:
        runs = next((r["runs"] for r in rounds if r["profile"] == profile), [])
        if len(runs) >= 2:
            return {"profile": profile, "run_id": runs[1]}, {"profile": profile, "run_id": runs[0]}
    return None, target


def label(source):
    return source["profile"] + (f" · {source['run_id']}" if source.get("run_id") else "")


# ---------------------------------------------------------------------------- 점검 하나의 상태

def round_state(customer, source):
    """점검 하나의 상태 — {"source", "label", "collected_at", "devices": {장비: {items, os_version, serial, model}},
    "links": {키: 링크}, "observers": [이웃 정보를 낸 장비], "item_order": [항목 이름]}."""
    from engine import inspection_evaluator

    profile, run_id = source.get("profile"), source.get("run_id")
    log_dirs = _log_dirs(customer, source)
    project_id = project_of(customer, profile)
    try:
        ctx = report_builder.build_context(customer, profile, project_id=project_id, log_dirs=log_dirs)
    except report_builder.InspectionReportError as exc:
        raise CompareError(f"{label(source)}: {exc}") from exc

    template = inspection_evaluator.load_template(customer=customer)
    evaluators = {item["name"]: item.get("evaluator") for item in template["check_items"]}
    devices, sections = {}, {}
    for device in ctx["devices"]:
        if device.get("unreachable"):
            continue
        devices[device["name"]] = {
            "items": {item["name"]: {"value": item.get("value"), "status": item.get("status"),
                                     "evaluator": evaluators.get(item["name"])} for item in device.get("items") or []},
            "os_version": device.get("os_version") or "", "serial": device.get("serial") or "",
            "model": device.get("model") or "", "collected_at": device.get("collected_at") or "",
        }
        sections[device["name"]] = device.get("sections") or {}
    if not devices:
        raise CompareError(f"점검 로그가 있는 장비가 없습니다: {label(source)}")

    topology = topology_of(project_id, sections)
    return {
        "source": {"profile": profile, "run_id": run_id}, "label": label(source),
        "collected_at": max(d["collected_at"] for d in devices.values()),
        "devices": devices, "links": links_of(topology),
        "observers": observers_of(topology, sections),
        "item_order": [item["name"] for item in template["check_items"]],
    }


def round_links(customer, source):
    """구성도 링크만 — {"label", "links", "observers"}. 구성도 탭의 '지난 회차와 비교'용이라 항목은 판정하지 않는다."""
    from engine.inspection_evaluator import split_transcript

    logs = report_builder.latest_logs_by_device(customer, source["profile"], dirs=_log_dirs(customer, source))
    if not logs:
        raise CompareError(f"점검 로그를 찾을 수 없습니다: {label(source)}")
    sections = {name: split_transcript(info["text"]) for name, info in logs.items()}
    topology = topology_of(project_of(customer, source["profile"]), sections)
    return {"label": label(source), "links": links_of(topology), "observers": observers_of(topology, sections)}


def log_files_key(customer, source):
    """그 점검의 로그 파일 목록과 수정 시각 — 바뀌지 않았으면 다시 계산하지 않아도 된다(구성도 탭 캐시의 키)."""
    dirs = _log_dirs(customer, source) or [entry["path"] for entry in
                                           log_storage.iter_log_dirs(customer, source["profile"], "original")]
    key = []
    for directory in dirs:
        try:
            names = sorted(name for name in os.listdir(directory) if name.lower().endswith(".txt"))
        except OSError:
            continue
        for name in names:
            try:
                key.append((str(directory), name, os.path.getmtime(os.path.join(directory, name))))
            except OSError:
                continue
    return tuple(key)


def _log_dirs(customer, source):
    """Run 이면 그 Run 의 원본 로그 폴더 하나, 회차면 None(장비별 최신 로그 — 보고서와 같다)."""
    if not source.get("run_id"):
        return None
    run = next((p for p in log_storage.list_run_dirs(customer, source["profile"])
                if p["run_id"] == source["run_id"]), None)
    if run is None or not _has_logs(run["original"]):
        raise CompareError(f"점검 로그를 찾을 수 없습니다: {label(source)}")
    return [run["original"]]


def topology_of(project_id, sections):
    """그 회차의 장비 목록 + 로그가 있는 장비로 구성도를 만든다(장비 목록에 없는 장비의 링크도 센다)."""
    from engine.topology_builder import build_topology

    inventory = {d.get("name"): d for d in _inventory(project_id)}
    return build_topology([inventory.get(name) or {"name": name}
                           for name in sorted(set(inventory) | set(sections)) if name], sections)


def observers_of(topology, sections):
    """이웃 정보를 낸 장비 — 로그가 있고 LLDP·CDP 출력이 비지 않은 장비. 이 장비가 말하지 않은 링크만 '없다'고 할 수 있다."""
    silent = {name for warning in topology.get("warnings") or [] if warning.get("kind") == "no_lldp"
              for name in warning.get("devices") or []}
    return sorted(set(sections) - silent)


def mark_link_changes(topology, previous, observers):
    """구성도(지금)에 지난 점검의 링크(round_links)를 견준다 — 제자리에서 고친다.

    지난 점검에 없던 물리 링크는 그 멤버와 edge 에 change='new'(묶음의 일부만 새것이면 edge 는 'partly_new')를 붙이고,
    지난 점검에만 있던 링크는 state='lost' edge 로 더한다. 양 끝 장비가 지금 구성도에 있어야 그릴 수 있다 — 없으면
    숫자로만 센다. 어느 쪽 점검에서든 양 끝 모두 이웃 정보를 내지 못했으면 '확인 불가'로 센다(compare 와 같은 규칙).
    반환: {"new", "lost", "lost_not_drawn", "unverified"}."""
    base_links, base_observers = previous["links"], set(previous["observers"])
    observers = set(observers)
    node_ids = {node["id"] for node in topology.get("nodes") or []}
    new = unverified = lost = hidden = 0
    current_keys = set()
    for edge in topology.get("edges") or []:
        flags = []
        for member in edge.get("members") or []:
            key = link_key(edge["a"], member.get("a_port") or "", edge["b"], member.get("b_port") or "")
            current_keys.add(key)
            if key in base_links:
                flags.append(False)
            elif edge["a"] in base_observers or edge["b"] in base_observers:
                member["change"] = "new"
                new += 1
                flags.append(True)
            else:
                unverified += 1
                flags.append(False)
        if flags and all(flags):
            edge["change"] = "new"
        elif any(flags):
            edge["change"] = "partly_new"
    for key, link in sorted(base_links.items()):
        if key in current_keys:
            continue
        if link["a"] not in observers and link["b"] not in observers:
            unverified += 1
            continue
        lost += 1
        if link["a"] not in node_ids or link["b"] not in node_ids:
            hidden += 1
            continue
        topology["edges"].append({
            "id": f"lost|{key}", "a": link["a"], "b": link["b"], "a_port": link["a_port"], "b_port": link["b_port"],
            "bundle": None, "bundle_a": None, "bundle_b": None, "count": 1, "label": "", "one_sided": False,
            "members": [{"a_port": link["a_port"], "b_port": link["b_port"], "state": "lost", "observed_from": []}],
            "state": "lost", "change": "lost",
        })
    return {"new": new, "lost": lost, "lost_not_drawn": hidden, "unverified": unverified}


def _inventory(project_id):
    if not project_id:
        return []
    try:
        from engine import device_inventory as di
        from engine import project_manager as pm
        paths = pm.project_paths(project_id)
        return di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"]).get("devices") or []
    except Exception:
        return []


def link_key(a, a_port, b, b_port):
    """물리 링크 하나의 이름 — 어느 쪽에서 봤든 같다(양 끝을 정렬)."""
    ends = sorted([(a, a_port), (b, b_port)])
    return f"{ends[0][0]}:{ends[0][1]}|{ends[1][0]}:{ends[1][1]}"


def links_of(topology):
    """구성도 edge(묶음은 멤버 여러 개)를 물리 링크 {키: {a, a_port, b, b_port, state, bundle}} 로 편다."""
    out = {}
    for edge in topology.get("edges") or []:
        for member in edge.get("members") or [{"a_port": edge.get("a_port"), "b_port": edge.get("b_port"),
                                                "state": edge.get("state")}]:
            ends = sorted([(edge["a"], member.get("a_port") or ""), (edge["b"], member.get("b_port") or "")])
            out[link_key(edge["a"], member.get("a_port") or "", edge["b"], member.get("b_port") or "")] = {
                "a": ends[0][0], "a_port": ends[0][1], "b": ends[1][0], "b_port": ends[1][1],
                "state": member.get("state") or edge.get("state") or "unknown", "bundle": edge.get("bundle") or "",
            }
    return out


# ---------------------------------------------------------------------------- 비교

def uptime_seconds(text):
    """'3 weeks, 2 days, 4 hours, 10 minutes'·'120 day(s), 3 hour(s)'·'8 days,  1:23' -> 초. 못 읽으면 None."""
    text = str(text or "")
    total, found = 0, False
    for number, unit in _UPTIME_RE.findall(text):
        total += int(number) * _UPTIME_UNITS[unit.lower()]
        found = True
    clock = _UPTIME_CLOCK_RE.search(text)
    if clock:
        total += int(clock.group(1)) * 3600 + int(clock.group(2)) * 60
        found = True
    return total if found else None


def _when(value):
    try:
        return datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _item_change(before, after, elapsed):
    rank_before, rank_after = _RANK.get(before["status"], 1), _RANK.get(after["status"], 1)
    if rank_after > rank_before:
        return "worse"
    if rank_after < rank_before:
        return "better"
    evaluator = after.get("evaluator")
    if evaluator == "uptime":
        # 재부팅 없이 지났다면 가동 시간은 '기준 값 + 두 점검 사이 시간'쯤이어야 한다 — 그보다 짧으면 그 사이에 재부팅했다.
        up_before, up_after = uptime_seconds(before["value"]), uptime_seconds(after["value"])
        if up_before is None or up_after is None or elapsed is None or elapsed < 0:
            return None
        return "rebooted" if up_after + _REBOOT_SLACK_SEC < up_before + elapsed else None
    if evaluator in _MEASURED_EVALUATORS:
        return None
    return "changed" if before["value"] != after["value"] else None


def compare(base, target):
    """두 상태(round_state) -> 달라진 것. 반환 형태는 화면(workspace-compare.js)이 그대로 그린다."""
    common = [name for name in sorted(set(base["devices"]) & set(target["devices"]))]
    items, facts = [], []
    order = {name: index for index, name in enumerate(target["item_order"])}
    for name in common:
        before_device, after_device = base["devices"][name], target["devices"][name]
        start, end = _when(before_device["collected_at"]), _when(after_device["collected_at"])
        elapsed = (end - start).total_seconds() if start and end else None
        for item_name in sorted(after_device["items"], key=lambda n: order.get(n, len(order))):
            before, after = before_device["items"].get(item_name), after_device["items"][item_name]
            if before is None:
                continue
            kind = _item_change(before, after, elapsed)
            if kind:
                items.append({"device": name, "item": item_name, "kind": kind,
                              "before": before["value"], "before_status": before["status"],
                              "after": after["value"], "after_status": after["status"]})
        for field, field_label in _FACTS:
            old, new = before_device[field], after_device[field]
            if old and new and old != new:
                facts.append({"device": name, "field": field_label, "before": old, "after": new})

    links = {"new": [], "lost": [], "changed": [], "unverified": []}
    base_observers, target_observers = set(base["observers"]), set(target["observers"])
    for key in sorted(set(target["links"]) - set(base["links"])):
        link = target["links"][key]
        verified = link["a"] in base_observers or link["b"] in base_observers
        links["new" if verified else "unverified"].append(dict(link, key=key, side="target"))
    for key in sorted(set(base["links"]) - set(target["links"])):
        link = base["links"][key]
        verified = link["a"] in target_observers or link["b"] in target_observers
        links["lost" if verified else "unverified"].append(dict(link, key=key, side="base"))
    for key in sorted(set(base["links"]) & set(target["links"])):
        old, new = base["links"][key]["state"], target["links"][key]["state"]
        if old != new and "unknown" not in (old, new):
            links["changed"].append(dict(target["links"][key], key=key, before=old, after=new))

    devices = {"only_base": sorted(set(base["devices"]) - set(target["devices"])),
               "only_target": sorted(set(target["devices"]) - set(base["devices"]))}
    counts = {kind: sum(1 for item in items if item["kind"] == kind) for kind in ("worse", "better", "changed", "rebooted")}
    counts.update({"facts": len(facts), **{f"links_{kind}": len(rows) for kind, rows in links.items()},
                   "only_base": len(devices["only_base"]), "only_target": len(devices["only_target"])})
    return {
        "base": _summary(base), "target": _summary(target), "common_devices": len(common),
        "items": items, "facts": facts, "links": links, "devices": devices, "counts": counts,
        "unchanged": not (items or facts or any(links.values()) or devices["only_base"] or devices["only_target"]),
    }


def _summary(state):
    return {"label": state["label"], "profile": state["source"]["profile"], "run_id": state["source"]["run_id"],
            "collected_at": state["collected_at"], "device_count": len(state["devices"]),
            "link_count": len(state["links"])}


def compare_sources(customer, base, target):
    """화면에서 고른 두 점검을 비교한다 — CompareError 는 그대로 올린다."""
    if not base or not target:
        raise CompareError("비교할 두 점검을 고르세요.")
    if (base.get("profile"), base.get("run_id") or None) == (target.get("profile"), target.get("run_id") or None):
        raise CompareError("같은 점검끼리는 비교할 수 없습니다 — 기준과 비교 대상을 다르게 고르세요.")
    # 화면이 보낸 이름은 이 고객사의 점검 목록에 있는 것만 받는다(폴더 경로를 만드는 값이다).
    known = {r["profile"]: r["runs"] for r in sources(customer)}
    for source in (base, target):
        runs = known.get(source.get("profile"))
        if runs is None or (source.get("run_id") and source["run_id"] not in runs):
            raise CompareError(f"점검 로그를 찾을 수 없습니다: {label(source)}")
    return compare(round_state(customer, base), round_state(customer, target))
