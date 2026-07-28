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
            # 채점 이력이 없어도 세션 터미널 점검 로그가 있으면 그 결과를 KPI에 반영한다
            # (요구사항 5 — Dashboard를 로그 수집/이상 징후와 실시간 연동).
            # self는 main.py의 Api에서 ReportApiMixin/LogViewerApiMixin과 함께 합성되므로
            # get_raw_log_findings/list_log_files를 그대로 호출할 수 있다.
            raw_findings = self.get_raw_log_findings() if project_id else []
            inspected_devices = len(self.get_report_devices()) if project_id else 0
            critical = sum(1 for d in raw_findings for f in d["findings"]
                            if f["keyword"] in ("FAIL", "ERROR", "CRITICAL", "DOWN", "TIMEOUT", "UNREACHABLE"))
            warning = sum(1 for d in raw_findings for f in d["findings"]
                           if f["keyword"] in ("CRC", "DROPS", "ERR-DISABLED"))
            health = 0
            if inspected_devices:
                devices_with_findings = len(raw_findings)
                clean_devices = inspected_devices - devices_with_findings
                health = round(100 * clean_devices / inspected_devices)
            sessions_count = len(self.list_log_files()) if project_id else 0
            summary = (f"점검 로그 기반 — {inspected_devices}대 장비 점검, Critical {critical}건/Warning {warning}건 발견."
                       if inspected_devices else "아직 채점 이력/점검 로그가 없습니다.")
            return {"kpi": {"health": health, "critical": critical, "warning": warning,
                             "total_devices": total_devices, "reachable": 0, "offline": 0,
                             "running": len(enabled_devices), "sessions": sessions_count},
                    "stages": [], "ai_summary": summary, "device_scores": {}, "top_priority_anomalies": raw_findings[:5]}

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
            "top_priority_anomalies": ai_result.get("top_priority", []),
        }
