"""
Material Design 3 스타일 웹 UI(web_ui/)를 pywebview로 띄우는 진입점.
기능(백엔드 로직)은 전혀 안 바꾸고, 기존 모듈(project_manager/command_catalog/main/history/ai_analysis/report)을
그대로 호출만 하는 순수 브리지 레이어.

실행: python gui_web.py
필요: pip install pywebview
Windows는 시스템 내장 Edge WebView2를 자동으로 사용함(추가 설치 보통 불필요).
"""
import os
import io
import contextlib
import webview

from engine import project_manager as pm
from engine import command_catalog as cc


class Api:
    # ---------- 프로젝트 ----------
    def list_projects(self):
        return pm.list_projects()

    def get_active_project(self):
        return pm.get_active_project()

    def create_project(self, name):
        new_id = pm.create_project(name)
        pm.set_active_project(new_id)
        return new_id

    def set_active_project(self, project_id):
        pm.set_active_project(project_id)
        return True

    def rename_project(self, project_id, new_name):
        pm.rename_project(project_id, new_name)
        return True

    def delete_project(self, project_id):
        pm.delete_project(project_id)
        return True

    # ---------- 대시보드 ----------
    def get_dashboard(self):
        project_id = pm.get_active_project()
        from engine.history import load_latest
        latest = load_latest(project_id) if project_id else None

        from engine import device_inventory as di
        paths = pm.project_paths(project_id) if project_id else None
        inv = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"]) if project_id else {"devices": [], "defaults": {}}
        total_devices = len(inv["devices"])
        enabled_devices = di.get_enabled_devices(inv)

        if not latest:
            return {"kpi": {"health": 0, "critical": 0, "warning": 0,
                             "total_devices": total_devices, "reachable": 0, "offline": 0,
                             "running": len(enabled_devices), "sessions": 0},
                    "stages": [], "ai_summary": "아직 채점 이력이 없습니다."}

        stages = latest["stages"]
        total_pass = sum(s["pass"] for s in stages)
        total_all = sum(s["total"] for s in stages)
        health = round(100 * total_pass / total_all) if total_all else 0

        from ai_analysis.rule_based import analyze
        ai_result = analyze(stages)
        critical = sum(1 for a in ai_result["all_anomalies"] if a["result"] == "FAIL")
        warning = sum(1 for a in ai_result["all_anomalies"] if a["result"] == "UNKNOWN")

        import glob
        sessions_count = len(glob.glob(f"history/{project_id}/*.json")) if project_id else 0

        return {
            "kpi": {"health": health, "critical": critical, "warning": warning,
                    "total_devices": total_devices, "reachable": None, "offline": None,
                    "running": len(enabled_devices), "sessions": sessions_count},
            "stages": [{"label": s["label"], "pass": s["pass"], "total": s["total"], "status": s["status"]} for s in stages],
            "ai_summary": ai_result["summary"],
        }

    # ---------- Discovery ----------
    def run_discovery(self):
        result = window.create_file_dialog(webview.OPEN_DIALOG, file_types=("EVE-NG lab (*.unl)", "All files (*.*)"))
        if not result:
            return None
        path = result[0]
        from unl_parser import run_discovery, parse_unl
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run_discovery(path)
        nodes, _ = parse_unl(path)
        return {"text": buf.getvalue(), "node_names": [n["name"] for n in nodes]}

    # ---------- 채점/보고서/이력 ----------
    def generate_report(self):
        project_id = pm.get_active_project()
        from engine.history import load_latest
        latest = load_latest(project_id) if project_id else None
        if not latest:
            return None
        from ai_analysis.router import analyze as ai_analyze
        from report.markdown_report import build_markdown_report, save_markdown_report
        ai_result = ai_analyze(latest["stages"], ai_config=None)
        md = build_markdown_report(project_id, latest["stages"], ai_result)
        paths = pm.project_paths(project_id)
        out_path = paths["target_state"].replace("target_state.yaml", "report_latest.md")
        save_markdown_report(project_id, latest["stages"], ai_result, out_path)
        return md

    def list_history(self):
        project_id = pm.get_active_project()
        if not project_id:
            return []
        import glob, json
        files = sorted(glob.glob(f"history/{project_id}/*.json"))
        result = []
        for fp in files:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
            result.append({"session": data["session"], "elapsed_sec": data["elapsed_sec"],
                           "stage_count": len(data["stages"])})
        return result

    # ---------- Command Catalog ----------
    def get_catalog(self):
        project_id = pm.get_active_project()
        if not project_id:
            return []
        paths = pm.project_paths(project_id)
        return cc.load_catalog(paths["commands_catalog"])

    def add_catalog_command(self, command, description):
        project_id = pm.get_active_project()
        paths = pm.project_paths(project_id)
        catalog = cc.load_catalog(paths["commands_catalog"])
        cc.add_command(catalog, command, description)
        cc.save_catalog(catalog, paths["commands_catalog"])
        return True

    def remove_catalog_command(self, command_id):
        project_id = pm.get_active_project()
        paths = pm.project_paths(project_id)
        catalog = cc.load_catalog(paths["commands_catalog"])
        cc.remove_command(catalog, command_id)
        cc.save_catalog(catalog, paths["commands_catalog"])
        return True

    def save_catalog_toggles(self, toggles):
        project_id = pm.get_active_project()
        paths = pm.project_paths(project_id)
        catalog = cc.load_catalog(paths["commands_catalog"])
        for item in catalog:
            if item["id"] in toggles:
                item["enabled"] = toggles[item["id"]]
        cc.save_catalog(catalog, paths["commands_catalog"])
        return True

    # ---------- 장비/IP 설정 (Device Inventory 기반, IP는 여기서만 관리) ----------
    def get_devices(self):
        project_id = pm.get_active_project()
        if not project_id:
            return []
        from engine import device_inventory as di
        paths = pm.project_paths(project_id)
        inv = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
        return inv["devices"]

    def save_devices(self, devices):
        project_id = pm.get_active_project()
        if not project_id:
            return False
        from engine import device_inventory as di
        paths = pm.project_paths(project_id)
        inv = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
        inv["devices"] = [di._normalize_device(d) for d in devices if d.get("name")]
        di.save_inventory(inv, paths["device_inventory"])
        return True

    def get_inventory_defaults(self):
        project_id = pm.get_active_project()
        if not project_id:
            return {}
        from engine import device_inventory as di
        paths = pm.project_paths(project_id)
        inv = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
        return inv["defaults"]

    def save_inventory_defaults(self, defaults):
        project_id = pm.get_active_project()
        if not project_id:
            return False
        from engine import device_inventory as di
        paths = pm.project_paths(project_id)
        inv = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
        inv["defaults"].update(defaults)
        di.save_inventory(inv, paths["device_inventory"])
        return True

    def auto_allocate_ips(self, prefix, start, end):
        project_id = pm.get_active_project()
        if not project_id:
            return 0
        from engine import device_inventory as di
        paths = pm.project_paths(project_id)
        inv = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
        inv["defaults"]["ip_pool"] = {"prefix": prefix, "start": int(start), "end": int(end)}
        allocated = di.auto_allocate_ips(inv, prefix, int(start), int(end))
        di.save_inventory(inv, paths["device_inventory"])
        return allocated

    def import_devices(self, overwrite=False):
        """파일 선택 다이얼로그로 CSV/YAML/JSON/Excel import."""
        result = window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=("Device files (*.csv;*.yaml;*.yml;*.json;*.xlsx)", "All files (*.*)"),
        )
        if not result:
            return None
        path = result[0]

        from engine import device_inventory as di
        ext = os.path.splitext(path)[1].lower()
        importers = {".csv": di.import_csv, ".yaml": di.import_yaml, ".yml": di.import_yaml,
                     ".json": di.import_json, ".xlsx": di.import_excel}
        importer = importers.get(ext)
        if not importer:
            return {"error": f"지원 안 하는 형식: {ext}"}

        imported = importer(path)
        project_id = pm.get_active_project()
        paths = pm.project_paths(project_id)
        inv = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
        result = di.merge_imported(inv, imported, overwrite=overwrite)
        di.save_inventory(inv, paths["device_inventory"])
        return result

    def check_reachability(self):
        project_id = pm.get_active_project()
        if not project_id:
            return {}
        from engine import device_inventory as di
        paths = pm.project_paths(project_id)
        inv = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
        return di.check_reachability(inv["devices"], inv["defaults"], timeout=2)

    def register_discovered_devices(self, node_names):
        """Discovery(.unl)에서 찾은 노드명을 Device Inventory에 등록 (IP는 비워둔 채, 비활성 상태로)."""
        project_id = pm.get_active_project()
        if not project_id:
            return 0
        from engine import device_inventory as di
        paths = pm.project_paths(project_id)
        inv = di.load_inventory(paths["device_inventory"], paths["lab_meta"], paths["ip_allocation"])
        existing = {d["name"] for d in inv["devices"]}
        added = 0
        for name in node_names:
            if name not in existing:
                di.add_device(inv, {"name": name, "enabled": False})
                added += 1
        di.save_inventory(inv, paths["device_inventory"])
        return added

    def get_connection_settings(self):
        import yaml
        if not os.path.exists("connection.yaml"):
            return {}
        with open("connection.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        net = cfg.get("network", {})
        ssh = cfg.get("ssh", {})
        thread = cfg.get("thread", {})
        return {
            "check_target_node": net.get("check_target_node", "Core1"),
            "retry_count": ssh.get("retry_count", 1),
            "retry_delay_sec": ssh.get("retry_delay_sec", 5),
            "ssh_timeout": ssh.get("connect_timeout_sec", 20),
            "max_parallel_workers": thread.get("max_parallel_workers", ""),
        }

    def save_connection_settings(self, payload):
        import yaml
        cfg = {
            "network": {
                "mode": "internal", "pre_flight_check": True,
                "check_target_node": payload.get("check_target_node", "Core1"),
                "check_port": 22, "check_timeout_sec": 3,
            },
            "ssh": {
                "connect_timeout_sec": int(payload.get("ssh_timeout", 20)),
                "retry_count": int(payload.get("retry_count", 1)),
                "retry_delay_sec": int(payload.get("retry_delay_sec", 5)),
            },
            "thread": {
                "max_parallel_workers": int(payload["max_parallel_workers"]) if payload.get("max_parallel_workers") else None,
            },
        }
        with open("connection.yaml", "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
        return True


window = None

if __name__ == "__main__":
    api = Api()
    web_ui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_ui", "index.html")
    window = webview.create_window("LAB 자동채점 프로그램", web_ui_path, js_api=api, width=1280, height=800, min_size=(1000, 640))
    webview.start(debug=False)
