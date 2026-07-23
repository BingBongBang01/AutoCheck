"""AnalysisApiMixin — 최신 세션의 Parser/Rule/Finding/AI/Health를 한 화면에 종합."""


class AnalysisApiMixin:
    def get_analysis(self):
        try:
            project_id = self._project()
        except RuntimeError:
            return None
        from engine.history import load_latest
        latest = load_latest(project_id)
        if not latest:
            return None

        stages = latest["stages"]
        findings = latest.get("findings", [])

        # Rule 단계별 breakdown
        rule_breakdown = [{"stage": s["label"], "status": s["status"], "pass": s["pass"], "total": s["total"]} for s in stages]

        # Evidence 샘플(FAIL/UNKNOWN 중 상위 5개)
        evidence_samples = [
            {"device": f["device"], "check_id": f["check_id"], "evidence": f.get("evidence", ""),
             "expected": f.get("expected"), "actual": f.get("actual")}
            for f in findings if f.get("result") in ("FAIL", "UNKNOWN")
        ][:5]

        # AI 최신 판단 — Settings 탭에서 드래그로 정한 제공자 우선순위를 그대로 반영
        from ai_analysis.router import analyze as ai_analyze
        ai_order = self.get_ai_settings()["order"]
        type_map = {"api": "api", "gemini": "gemini", "local": "local"}
        providers = [{"type": type_map[i]} for i in ai_order if i in type_map]
        ai_result = ai_analyze(stages, ai_config={"providers": providers})

        # Health Score
        from core.health_score import score_project
        health = score_project(findings) if findings else {"project_score": None, "device_scores": {}}

        # 등록된 Vendor/Parser 현황(참고용, 정적 정보)
        from plugins.vendors.base import list_vendors
        from plugins.parsers.registry import list_registered
        vendor_info = {"vendors": list_vendors(), "parser_count": len(list_registered())}

        return {
            "rule_breakdown": rule_breakdown,
            "evidence_samples": evidence_samples,
            "ai_source": ai_result["source"],
            "ai_summary": ai_result["summary"],
            "health": health,
            "vendor_info": vendor_info,
            "session": latest["session"],
        }
