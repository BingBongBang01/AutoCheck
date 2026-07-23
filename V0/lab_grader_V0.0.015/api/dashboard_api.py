"""DashboardApiMixin — Health Score/KPI 집계만 담당."""
from engine import project_manager as pm


class DashboardApiMixin:
    def get_dashboard(self):
        project_id = pm.get_active_project()
        from engine.history import load_latest
        latest = load_latest(project_id) if project_id else None

        from engine import device_inventory as di
        paths = pm.project_paths(project_id) if project_id else None
        inv = self._load_inventory(paths) if project_id else {"devices": [], "defaults": {}}
        total_devices = len(inv["devices"])
        enabled_devices = di.get_enabled_devices(inv)

        if not latest:
            return {"kpi": {"health": 0, "critical": 0, "warning": 0,
                             "total_devices": total_devices, "reachable": 0, "offline": 0,
                             "running": len(enabled_devices), "sessions": 0},
                    "stages": [], "ai_summary": "아직 채점 이력이 없습니다.", "device_scores": {}}

        stages = latest["stages"]
        findings = latest.get("findings", [])

        from core.health_score import score_project
        if findings:
            health_result = score_project(findings)
            health = round(health_result["project_score"])
            # "(network-wide)"는 실제 장비가 아니라 STP root 교차검증용 가짜 device — 표시에서 제외
            device_scores = {k: v for k, v in health_result["device_scores"].items() if k != "(network-wide)"}
        else:
            # v0.0.10 이전 세션(findings 없음) — 예전 방식(PASS 비율)으로 하위호환 폴백
            total_pass = sum(s["pass"] for s in stages)
            total_all = sum(s["total"] for s in stages)
            health = round(100 * total_pass / total_all) if total_all else 0
            device_scores = {}

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
            "device_scores": device_scores,
        }
