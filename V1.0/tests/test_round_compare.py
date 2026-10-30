"""회차 비교 — IMPROVEMENT_PLAN 4-7.

같은 고객사의 두 점검(회차·Run)을 점검 항목·장비 정보·구성도 링크로 비교한다. 보고서의 '전월 값'은 보고서를 만든
회차와만, 그것도 값만 나란히 보여 줬다. 여기서는 두 점검의 로그를 보고서·구성도와 같은 기준으로 다시 판정해:
  1. 항목이 나빠짐/좋아짐/값 바뀜 — 실측값(CPU·메모리)은 상태가 바뀔 때만, 가동 시간은 재부팅했을 때만
  2. OS 버전·S/N 변화(업그레이드·장비 교체)
  3. 물리 링크의 새로 생김/사라짐 — 양 끝 모두 이웃 정보가 없던 점검에서는 '확인 불가'(단선 경보를 쏟지 않게)
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from core.paths import AppPaths
from engine import round_compare


@pytest.fixture
def root(monkeypatch):
    from core.context_cache import workspace_cache
    from engine import customer_manager as cm
    from engine import project_manager as pm
    original = AppPaths._user_data_root
    data_root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths._user_data_root = data_root
    AppPaths.forget_ensured()
    monkeypatch.setattr(pm, "LABS_DIR", str(data_root / "labs"))
    monkeypatch.setattr(pm, "STATE_FILE", str(data_root / "config" / "active_project.yaml"))
    monkeypatch.setattr(cm, "CUSTOMERS_PATH", str(data_root / "config" / "customers.yaml"))
    workspace_cache.invalidate()
    yield data_root
    workspace_cache.invalidate()
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def eos_log(name, *, uptime, version="4.28.3M", serial="JPE111", neighbors=(), ports=(), fcs=0):
    """EOS 세션 점검 로그(프롬프트 트랜스크립트) — neighbors: [(로컬, 이웃, 이웃 포트)], ports: [(포트, 상태, Po)]."""
    lines = [f"{name}#show version", "Arista DCS-7050SX3-48YC8", f"Serial number: {serial}",
             f"Software image version: {version}", f"Uptime: {uptime}",
             f"{name}#show processes top once",
             "%Cpu(s):  2.0 us,  1.0 sy,  0.0 ni, 95.0 id,  0.0 wa",
             "KiB Mem :  8000000 total,  4000000 free,  3000000 used",
             f"{name}#show lldp neighbors",
             "Port       Neighbor Device ID       Neighbor Port ID    TTL"]
    lines += [f"{local:<10} {peer:<24} {peer_port:<19} 120" for local, peer, peer_port in neighbors]
    lines += [f"{name}#show interfaces status", "Port       Name   Status       Vlan     Duplex Speed Type"]
    lines += [f"{port:<10} link   {state:<12} {('in ' + po) if po else 'trunk':<8} full   10G   10GBASE-SR"
              for port, state, po in ports]
    lines += [f"{name}#show interfaces counters errors",
              "Port         FCS    Align   Symbol       Rx    Runts   Giants    Tx",
              f"Et1     {fcs}    0      0       0     0      0       0",
              f"{name}#show logging", f"{name}#"]
    return "\n".join(lines) + "\n"


def write_log(customer, profile, run_id, device, text):
    from engine.profile_manager import profile_manager
    stamp = run_id.replace("-", "")
    raw = profile_manager.profile_dir(customer, profile) / "runs" / run_id / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / f"{stamp}_raw_{device}.txt").write_bytes(text.encode("utf-8"))


@pytest.fixture
def rounds(root):
    """고객사 ABC 의 회차 2026-10(Run 하나)·2026-11(Run 둘 — 20일에 Agg1 만 다시 점검)."""
    from api.customer_profile_api import CustomerProfileApiMixin
    from api.project_core_api import ProjectCoreApiMixin
    from engine import device_inventory as di
    from engine import project_manager as pm

    class Api(CustomerProfileApiMixin, ProjectCoreApiMixin):
        pass

    api = Api()
    api.create_customer("ABC")
    cid = next(c["id"] for c in api.get_customer_profiles())
    api.create_inspection_profile(cid, "2026-10", inspection_date="2026-10-15")
    api.create_inspection_profile(cid, "2026-11", inspection_date="2026-11-15")
    ids = {p["profile_name"]: p["id"] for c in api.get_customer_profiles() for p in c["profiles"]}
    devices = ("Core1", "Agg1", "Acc1", "Old1")
    for profile in ("2026-10", "2026-11"):
        di.save_inventory({"defaults": {}, "devices": [
            {"name": name, "management_ip": f"10.0.0.{i}", "enabled": True} for i, name in enumerate(devices, 1)]},
            pm.project_paths(ids[profile])["device_inventory"])

    october = "2026-10-15_090000"
    write_log("ABC", "2026-10", october, "Core1", eos_log(
        "Core1", uptime="2 weeks, 1 day, 3 hours and 4 minutes",
        neighbors=[("Et1", "Agg1", "Ethernet1"), ("Et2", "Acc1", "Ethernet1")],
        ports=[("Et1", "connected", ""), ("Et2", "connected", "")]))
    write_log("ABC", "2026-10", october, "Agg1", eos_log(
        "Agg1", uptime="10 days", neighbors=[("Et1", "Core1", "Ethernet1")], ports=[("Et1", "connected", "")]))
    write_log("ABC", "2026-10", october, "Acc1", eos_log(
        "Acc1", uptime="9 days", neighbors=[("Et1", "Core1", "Ethernet2")], ports=[("Et1", "connected", "")]))
    write_log("ABC", "2026-10", october, "Old1", eos_log(
        "Old1", uptime="5 days", neighbors=[("Et1", "X9", "Ethernet7")]))          # 다음 회차에 없는 장비

    november = "2026-11-15_090000"
    write_log("ABC", "2026-11", november, "Core1", eos_log(           # 그 사이 재부팅·OS 업그레이드, Acc1 링크 대신 Agg1 Et3
        "Core1", uptime="3 days, 2 hours", version="4.30.1F",
        neighbors=[("Et1", "Agg1", "Ethernet1"), ("Et3", "Agg1", "Ethernet3")],
        ports=[("Et1", "connected", ""), ("Et3", "connected", "")]))
    write_log("ABC", "2026-11", november, "Agg1", eos_log(
        "Agg1", uptime="41 days", fcs=25,
        neighbors=[("Et1", "Core1", "Ethernet1"), ("Et3", "Core1", "Ethernet3")],
        ports=[("Et1", "connected", ""), ("Et3", "connected", "")]))
    write_log("ABC", "2026-11", november, "Acc1", eos_log("Acc1", uptime="40 days"))   # LLDP 출력 없음
    write_log("ABC", "2026-11", "2026-11-20_090000", "Agg1", eos_log(        # 20일 — Agg1 만 다시 점검, 오류 정리됨
        "Agg1", uptime="46 days", fcs=0,
        neighbors=[("Et1", "Core1", "Ethernet1"), ("Et3", "Core1", "Ethernet3")],
        ports=[("Et1", "connected", ""), ("Et3", "connected", "")]))
    return ids


def _by(rows, **match):
    return [row for row in rows if all(row.get(k) == v for k, v in match.items())]


# --------------------------------------------------------------------------- 고르기

def test_sources_and_default_pairs(rounds):
    listed = round_compare.sources("ABC")
    assert [(r["profile"], r["runs"]) for r in listed] == [
        ("2026-10", ["2026-10-15_090000"]), ("2026-11", ["2026-11-20_090000", "2026-11-15_090000"])]
    assert round_compare.default_pair("ABC", "2026-11") == (
        {"profile": "2026-10", "run_id": None}, {"profile": "2026-11", "run_id": None})
    assert round_compare.default_pair("ABC", "2026-11", "2026-11-20_090000") == (
        {"profile": "2026-11", "run_id": "2026-11-15_090000"}, {"profile": "2026-11", "run_id": "2026-11-20_090000"})
    assert round_compare.default_pair("ABC", "2026-11", "2026-11-15_090000")[0] == {"profile": "2026-10", "run_id": None}
    assert round_compare.default_pair("ABC", "2026-10")[0] is None                 # 앞선 점검이 없다


# --------------------------------------------------------------------------- 회차끼리

def test_round_comparison_finds_items_facts_links_and_devices(rounds):
    result = round_compare.compare_sources("ABC", {"profile": "2026-10"}, {"profile": "2026-11"})
    assert result["base"]["label"] == "2026-10" and result["target"]["label"] == "2026-11"
    assert result["common_devices"] == 3 and result["devices"] == {"only_base": ["Old1"], "only_target": []}

    (reboot,) = _by(result["items"], kind="rebooted")
    assert (reboot["device"], reboot["item"]) == ("Core1", "System Uptime")          # 2주 -> 한 달 뒤 3일
    assert not _by(result["items"], device="Agg1", item="System Uptime")             # 10일 -> 41일: 재부팅 아님
    assert not _by(result["items"], item="CPU 사용률") and not _by(result["items"], item="Free Memory")
    assert not _by(result["items"], device="Agg1", item="Interface Error")           # 회차 최신 로그(20일)는 정상

    assert result["facts"] == [{"device": "Core1", "field": "OS 버전", "before": "4.28.3M", "after": "4.30.1F"}]

    links = result["links"]
    assert [(l["a"], l["a_port"], l["b"], l["b_port"]) for l in links["new"]] == [("Agg1", "Ethernet3", "Core1", "Ethernet3")]
    assert [(l["a"], l["a_port"], l["b"], l["b_port"]) for l in links["lost"]] == [("Acc1", "Ethernet1", "Core1", "Ethernet2")]
    assert [(l["a"], l["b"]) for l in links["unverified"]] == [("Old1", "X9")]      # 양 끝 모두 이웃 정보가 없다
    assert result["counts"]["links_lost"] == 1 and result["unchanged"] is False


def test_runs_inside_a_round_compare_too(rounds):
    base, target = round_compare.default_pair("ABC", "2026-11", "2026-11-20_090000")
    result = round_compare.compare_sources("ABC", base, target)
    (better,) = _by(result["items"], device="Agg1", item="Interface Error")
    assert better["kind"] == "better" and better["before_status"] == "확인필요" and better["after_status"] == "정상"
    assert result["devices"]["only_base"] == ["Acc1", "Core1"]                          # 20일에는 Agg1 만 점검했다
    assert result["links"]["lost"] == []                                                 # Core1 로그가 없으니 '사라짐'이 아니다


def test_identical_rounds_report_nothing(rounds):
    write_log("ABC", "2026-11", "2026-11-21_090000", "Agg1",
              eos_log("Agg1", uptime="47 days", neighbors=[("Et1", "Core1", "Ethernet1"), ("Et3", "Core1", "Ethernet3")],
                      ports=[("Et1", "connected", ""), ("Et3", "connected", "")]))
    result = round_compare.compare_sources("ABC", {"profile": "2026-11", "run_id": "2026-11-20_090000"},
                                           {"profile": "2026-11", "run_id": "2026-11-21_090000"})
    assert result["unchanged"] is True and result["items"] == []


def test_bad_or_same_sources_are_refused(rounds):
    with pytest.raises(round_compare.CompareError, match="같은 점검"):
        round_compare.compare_sources("ABC", {"profile": "2026-11"}, {"profile": "2026-11", "run_id": None})
    with pytest.raises(round_compare.CompareError, match="찾을 수 없습니다"):
        round_compare.compare_sources("ABC", {"profile": "../../etc"}, {"profile": "2026-11"})
    with pytest.raises(round_compare.CompareError, match="찾을 수 없습니다"):
        round_compare.compare_sources("ABC", {"profile": "2026-11", "run_id": "2099-01-01_000000"}, {"profile": "2026-10"})


@pytest.mark.parametrize("text, seconds", [
    ("2 weeks, 1 day, 3 hours and 4 minutes", 2 * 604800 + 86400 + 3 * 3600 + 4 * 60),
    ("3 weeks, 2 days, 4 hours, 10 minutes", 3 * 604800 + 2 * 86400 + 4 * 3600 + 600),
    ("120 day(s), 3 hour(s), 4 minute(s), 50 second(s)", 120 * 86400 + 3 * 3600 + 4 * 60 + 50),
    ("8 days,  1:23", 8 * 86400 + 3600 + 23 * 60),
    ("미수집", None), ("", None),
])
def test_uptime_is_read_from_every_vendor_format(text, seconds):
    assert round_compare.uptime_seconds(text) == seconds


def test_screen_api(rounds):
    from api.round_compare_api import RoundCompareApiMixin

    class Stub(RoundCompareApiMixin):
        def _resolve_active_customer_profile_names(self):
            return "ABC", "2026-11"

    api = Stub()
    sources = api.get_round_compare_sources()
    assert [r["profile"] for r in sources["rounds"]] == ["2026-10", "2026-11"]
    assert sources["base"] == {"profile": "2026-10", "run_id": None}
    assert api.get_round_compare_sources("2026-11-20_090000")["base"]["run_id"] == "2026-11-15_090000"
    assert api.compare_rounds(sources["base"], sources["target"])["counts"]["rebooted"] == 1
    assert "error" in api.compare_rounds({"profile": "nope"}, sources["target"])
    json.dumps(api.compare_rounds(sources["base"], sources["target"]), ensure_ascii=False)   # 화면으로 그대로 보낼 수 있다


# --------------------------------------------------------------------------- 화면(node)

JS_DIR = Path(__file__).resolve().parent.parent / "web_ui" / "js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 동작 검증을 건너뜁니다")


def _block(name, start):
    lines = (JS_DIR / name).read_text(encoding="utf-8").splitlines()
    begin = next(i for i, line in enumerate(lines) if line.startswith(start))
    if lines[begin].rstrip().endswith((";", "}")):
        return lines[begin]
    end = next(i for i in range(begin, len(lines)) if lines[i] in ("}", "};", "];"))
    return "\n".join(lines[begin:end + 1])


@needs_node
def test_result_rendering_escapes_device_values(tmp_path):
    side = {"label": "2026-10", "collected_at": "2026-10-15T09:00:00", "device_count": 2, "link_count": 1}
    data = {
        "base": side, "target": dict(side, label="2026-11"), "common_devices": 1, "unchanged": False,
        "counts": {"worse": 1, "links_lost": 1},
        "items": [{"device": "<img src=x>", "item": "Log 확인", "kind": "worse", "before": "없음", "before_status": "정상",
                   "after": "특이 로그 2건", "after_status": "확인필요"}],
        "facts": [], "devices": {"only_base": [], "only_target": ["<b>New</b>"]},
        "links": {"new": [], "changed": [], "unverified": [],
                  "lost": [{"a": "Acc1", "a_port": "Ethernet1", "b": "Core1", "b_port": "Ethernet2", "bundle": ""}]},
    }
    script = "\n".join([
        _block("core.js", "const _ESC_MAP"), _block("core.js", "function esc("),
        _block("workspace-compare.js", "const WS_COMPARE_KIND"), _block("workspace-compare.js", "const WS_COMPARE_LINK_KIND"),
        _block("workspace-compare.js", "function wsCompareSide("), _block("workspace-compare.js", "function wsCompareLinkEnd("),
        _block("workspace-compare.js", "function wsCompareResultHtml("), _block("workspace-compare.js", "function wsCompareTable("),
        f"const data = {json.dumps(data, ensure_ascii=False)};",
        "console.log(JSON.stringify([wsCompareResultHtml(data), wsCompareResultHtml(Object.assign({}, data, {unchanged: true}))]));",
    ])
    path = tmp_path / "check.js"
    path.write_bytes(script.encode("utf-8"))
    completed = subprocess.run(["node", str(path)], capture_output=True, encoding="utf-8")
    assert completed.returncode == 0, completed.stderr
    html, unchanged = json.loads(completed.stdout.strip().splitlines()[-1])
    assert "<img" not in html and "&lt;img src=x&gt;" in html and "<b>New" not in html
    assert "나빠진 항목 1" in html and "사라진 링크 1" in html and "Ethernet2" in html
    assert "달라진 것이 없습니다" in unchanged


# --------------------------------------------------------------------------- 구성도 탭의 '지난 회차와 비교'(4-7b)

def _current_topology(customer, profile):
    from engine import inspection_report_builder as builder
    from engine.inspection_evaluator import split_transcript
    sections = {name: split_transcript(info["text"])
                for name, info in builder.latest_logs_by_device(customer, profile).items()}
    return round_compare.topology_of(round_compare.project_of(customer, profile), sections), sections


def test_topology_marks_new_and_lost_links_against_the_previous_round(rounds):
    from engine.topology_layout import layout
    from engine.topology_svg import render_svg
    topology, sections = _current_topology("ABC", "2026-11")
    plain_svg = render_svg(topology, layout(topology, {}))
    assert "tp-link-halo" not in plain_svg and "새 링크(지난 회차 대비)" not in plain_svg

    layout_info = layout(topology, {})
    previous = round_compare.round_links("ABC", {"profile": "2026-10", "run_id": None})
    summary = round_compare.mark_link_changes(topology, previous, round_compare.observers_of(topology, sections))
    assert summary == {"new": 1, "lost": 1, "lost_not_drawn": 0, "unverified": 1}
    (core_agg,) = [e for e in topology["edges"] if {e["a"], e["b"]} == {"Agg1", "Core1"}]
    assert core_agg["change"] == "partly_new"                                      # Et1 은 그대로, Et3 만 새것
    assert [m["a_port"] for m in core_agg["members"] if m.get("change") == "new"] == ["Ethernet3"]
    (lost,) = [e for e in topology["edges"] if e["state"] == "lost"]
    assert (lost["a"], lost["a_port"], lost["b"], lost["b_port"]) == ("Acc1", "Ethernet1", "Core1", "Ethernet2")

    svg = render_svg(topology, layout_info)
    assert 'class="tp-link-halo"' in svg and "tp-link-lost" in svg and ">사라짐</text>" in svg
    assert "새 링크(지난 회차 대비)" in svg and "사라진 링크(지난 회차에만)" in svg
    standalone = render_svg(topology, layout_info, standalone=True)
    assert ".tp-link-lost{" in standalone and ".tp-link-halo{" in standalone       # 내보낸 파일에도 색이 심긴다


@pytest.fixture
def topology_api(rounds, tmp_path, monkeypatch):
    from api.base import BaseApiMixin
    from api.topology_api import TopologyApiMixin
    from engine import log_storage
    from engine import project_manager as pm
    monkeypatch.setattr(log_storage, "open_in_file_explorer", lambda path: None)

    class Stub(TopologyApiMixin, BaseApiMixin):
        def _active_names(self):
            return "ABC", "2026-11"

        def _resolve_active_customer_profile_names(self):
            return "ABC", "2026-11"

        def _active_profile_log_paths(self):
            return {"reports": str(tmp_path / "reports")}

        def _paths(self):
            return pm.project_paths(rounds["2026-11"])

    return Stub()


def test_topology_tab_compares_without_touching_its_cache(topology_api, monkeypatch, tmp_path):
    plain = topology_api.get_network_topology(live=False)
    assert "compare" not in plain and not any(e["state"] == "lost" for e in plain["edges"])
    compared = topology_api.get_network_topology(live=False, compare=True)
    assert compared["compare"] == {"base": "2026-10", "new": 1, "lost": 1, "lost_not_drawn": 0, "unverified": 1}
    assert compared["version"] != plain["version"]                                    # 화면이 다시 그린다
    assert [n["x"] for n in compared["nodes"]] == [n["x"] for n in plain["nodes"]]     # 장비 자리는 그대로

    rebuilt = []
    real = round_compare.round_links
    monkeypatch.setattr(round_compare, "round_links", lambda *a: rebuilt.append(a) or real(*a))
    topology_api.get_network_topology(live=False, compare=True)
    assert rebuilt == []                                                              # 지난 회차 로그가 그대로면 캐시

    saved = topology_api.save_topology_svg(compare=True)
    assert saved["ok"] and ">사라짐</text>" in Path(saved["path"]).read_text(encoding="utf-8")
    cached = topology_api._topology_structure()["topology"]
    assert not any(e.get("state") == "lost" or e.get("change") for e in cached["edges"])   # 캐시는 그대로


def test_topology_compare_explains_when_there_is_nothing_before(topology_api, monkeypatch):
    monkeypatch.setattr(type(topology_api), "_active_names", lambda self: ("ABC", "2026-10"))
    monkeypatch.setattr(type(topology_api), "_resolve_active_customer_profile_names", lambda self: ("ABC", "2026-10"))
    result = topology_api.get_network_topology(live=False, compare=True)
    assert result["ok"] and "앞선 점검이 없습니다" in result["compare"]["error"]


# --------------------------------------------------------------------------- PDF 부록의 구성도(4-7c)

def _context(rounds, profile="2026-11"):
    from engine import inspection_report_builder as builder
    return builder.build_context("ABC", profile, project_id=rounds[profile])


def _strings(drawing):
    from reportlab.graphics.shapes import Group, String
    found, stack = [], list(drawing.contents)
    while stack:
        item = stack.pop()
        if isinstance(item, Group):
            stack.extend(item.contents)
        elif isinstance(item, String):
            found.append(item.text)
    return found


def test_pdf_appendix_has_the_topology_and_the_link_changes(rounds):
    from engine import inspection_report_builder as builder
    appendix = builder._topology_appendix(_context(rounds), "ABC", "2026-11")
    figure = appendix["figure"]
    assert {n["name"] for n in figure["nodes"]} >= {"Core1", "Agg1", "Acc1"}
    assert {e["state"] for e in figure["edges"]} >= {"lost"} and any(e["change"] == "partly_new" for e in figure["edges"])
    changes = appendix["changes"]
    assert (changes["base"], changes["new"], changes["lost"], changes["unverified"]) == ("2026-10", 1, 1, 1)
    assert changes["rows"] == [["사라진 링크", "Acc1 Ethernet1", "Core1 Ethernet2"],
                               ["새 링크", "Agg1 Ethernet3", "Core1 Ethernet3"]]

    from reportlab.lib.units import mm
    from report.inspection_pdf import register_fonts, topology_drawing
    register_fonts()
    drawing = topology_drawing(figure, 170 * mm, 165 * mm)
    assert drawing.width <= 170 * mm + 0.01 and drawing.height <= 165 * mm + 0.01
    texts = _strings(drawing)
    assert {"Core1", "Agg1", "Acc1", "사라짐"} <= set(texts)


def test_pdf_appendix_uses_the_layout_the_user_saved(rounds):
    import yaml
    from engine import inspection_report_builder as builder
    from engine.topology_layout import positions_path
    Path(positions_path()).parent.mkdir(parents=True, exist_ok=True)
    Path(positions_path()).write_text(yaml.safe_dump({f"id:{rounds['2026-11']}": {"Core1": [900, 420]}}), encoding="utf-8")
    figure = builder._topology_appendix(_context(rounds), "ABC", "2026-11")["figure"]
    (core,) = [n for n in figure["nodes"] if n["name"] == "Core1"]
    assert (core["x"], core["y"]) == (900, 420)


def test_pdf_report_gets_the_appendix_and_survives_a_broken_topology(rounds, monkeypatch):
    import report.inspection_pdf as pdf
    from engine import inspection_report_builder as builder
    pages = []
    real_page = pdf.topology_page
    monkeypatch.setattr(pdf, "topology_page", lambda appendix, site: pages.append(appendix) or real_page(appendix, site))
    result = builder.export("ABC", "2026-11", "pdf", project_id=rounds["2026-11"])
    path = Path(result["path"])
    assert path.read_bytes()[:4] == b"%PDF" and len(pages) == 1 and pages[0]["changes"]["lost"] == 1

    def broken(*args, **kwargs):
        raise RuntimeError("구성도 실패")

    monkeypatch.setattr(round_compare, "topology_of", broken)
    pages.clear()
    again = builder.export("ABC", "2026-11", "pdf", project_id=rounds["2026-11"])
    assert Path(again["path"]).read_bytes()[:4] == b"%PDF" and pages == []          # 부록 없이 보고서는 만든다


def test_no_links_means_no_appendix(rounds):
    from engine import inspection_report_builder as builder
    context = {"devices": [{"name": "Acc1", "sections": {"show version": "Arista DCS-7050SX3-48YC8"}}]}
    assert builder._topology_appendix(context, "ABC", "2026-11") is None
