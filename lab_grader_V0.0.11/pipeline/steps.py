"""
기존 함수(main.py의 adapt_raw_to_collected, engine.scorer.score_all, ai_analysis.router,
report.markdown_report)를 전혀 안 바꾸고 PipelineStep으로 감싼다.
main.py의 grade()가 이 Step들을 순서대로 호출하던 걸 Pipeline이 대신 하게 됨.
"""
import time

try:
    from pipeline.step import PipelineStep
    from parsers import show_vlan, show_spanning_tree
    from rule_engine.rules import run_vlan_stp_rules, findings_to_verdicts
    from engine.scorer import score_all, print_scoreboard
    from engine.history import save_history, load_previous, compare_sessions, compare_check_level
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from pipeline.step import PipelineStep
    from parsers import show_vlan, show_spanning_tree
    from rule_engine.rules import run_vlan_stp_rules, findings_to_verdicts
    from engine.scorer import score_all, print_scoreboard
    from engine.history import save_history, load_previous, compare_sessions, compare_check_level


class CollectorStep(PipelineStep):
    name = "collector"

    def __init__(self, collect_fn):
        self.collect_fn = collect_fn

    def run(self, ctx):
        started = time.time()
        raw = self.collect_fn()
        ctx.raw_by_device = raw
        ctx.data["collect_elapsed"] = time.time() - started
        return ctx


class ParserStep(PipelineStep):
    """collector 원본 출력을 comparator가 기대하는 형태로 변환 (기존 adapt_raw_to_collected 로직 그대로)."""
    name = "parser"

    def run(self, ctx):
        if ctx.raw_by_device is None:
            return ctx  # 수집 실패 — 뒤 스텝들이 각자 None 체크

        collected_vlan, collected_stp = {}, {}
        for device, cmds in ctx.raw_by_device.items():
            for cmd_text, raw in cmds.items():
                if cmd_text.startswith("show vlan"):
                    collected_vlan[device] = show_vlan.parse(raw)
                elif cmd_text.startswith("show spanning-tree vlan"):
                    collected_stp[device] = show_spanning_tree.parse_combined(raw)
        ctx.data["collected_vlan"] = collected_vlan
        ctx.data["collected_stp"] = collected_stp
        return ctx


class RuleEngineStep(PipelineStep):
    """rule_engine.rules를 호출해 Finding 객체를 만든다 — 판정(PASS/FAIL)이 확정되는 지점."""
    name = "rule_engine"

    def __init__(self, target_state):
        self.target_state = target_state

    def run(self, ctx):
        if ctx.raw_by_device is None:
            return ctx

        result = run_vlan_stp_rules(
            ctx.project.project_id, ctx.session_id, self.target_state,
            ctx.data.get("collected_vlan", {}), ctx.data.get("collected_stp", {}),
        )
        for stage_findings in result.values():
            ctx.findings.extend(stage_findings)
        ctx.data["verdicts_by_stage"] = {k: findings_to_verdicts(v) for k, v in result.items()}
        return ctx


class ScorerStep(PipelineStep):
    """기존 scorer.score_all()을 그대로 재사용 — Finding에서 역변환한 verdict로 stage 집계."""
    name = "scorer"

    def __init__(self, stages_cfg):
        self.stages_cfg = stages_cfg

    def run(self, ctx):
        if ctx.raw_by_device is None:
            return ctx
        ctx.scored = score_all(self.stages_cfg, ctx.data.get("verdicts_by_stage", {}))
        return ctx


class ScoreboardPrintStep(PipelineStep):
    name = "print_scoreboard"

    def run(self, ctx):
        if ctx.raw_by_device is None:
            print("수집 실패 또는 중단됨 — 채점 건너뜀")
            return ctx
        elapsed = ctx.data.get("collect_elapsed", 0.0)
        ctx.elapsed_sec = elapsed
        print_scoreboard(ctx.scored, session_label=f"(Pipeline 실행, {elapsed:.1f}초 소요)")
        return ctx


class HistoryStep(PipelineStep):
    name = "history"

    def run(self, ctx):
        if ctx.raw_by_device is None:
            return ctx
        project_id = ctx.project.project_id
        prev = load_previous(project_id)
        path = save_history(project_id, ctx.scored, ctx.elapsed_sec, findings=ctx.findings)
        print(f"\n[저장됨] {path}")

        if prev:
            curr_payload = {"session": "current", "stages": ctx.scored}
            stage_diff = compare_sessions(prev, curr_payload)
            check_diff = compare_check_level(prev, curr_payload)
            print(f"\n[직전 회차({prev['session']}) 대비 변화]")
            for d in stage_diff:
                print(f"  {d['stage']}: {d['prev_pass']}/{d['prev_total']} -> {d['curr_pass']}/{d['curr_total']}  ({d['trend']})")
            for c in check_diff:
                print(f"  [{c['stage']}] {c['check']}: {c['from']} -> {c['to']}")
            if not stage_diff and not check_diff:
                print("  변화 없음")
        return ctx


class AIAnalysisStep(PipelineStep):
    """Rule Engine이 이미 확정한 findings/scored를 보고 요약·조치권고만 생성 — PASS/FAIL은 절대 안 건드림."""
    name = "ai_analysis"

    def run(self, ctx):
        if ctx.raw_by_device is None:
            return ctx
        from ai_analysis.router import analyze as ai_analyze
        ai_result = ai_analyze(ctx.scored, ai_config=None)
        ctx.data["ai_result"] = ai_result
        print(f"\n[AI 분석 — {ai_result['source']}] {ai_result['summary']}")

        # Finding에 recommendation만 채움 (result는 절대 안 바뀜 — Finding.with_recommendation 참고)
        rec_by_check = {a["check"]: a.get("suggested_action", "") for a in ai_result.get("all_anomalies", [])}
        for f in ctx.findings:
            if f.check_id in rec_by_check:
                f.with_recommendation(rec_by_check[f.check_id], source=f"ai_{ai_result['source']}")
        return ctx


class ReportStep(PipelineStep):
    name = "report"

    def __init__(self, report_path):
        self.report_path = report_path

    def run(self, ctx):
        if ctx.raw_by_device is None:
            return ctx
        from report.markdown_report import save_markdown_report
        ai_result = ctx.data.get("ai_result")
        save_markdown_report(ctx.project.project_id, ctx.scored, ai_result, self.report_path)
        print(f"[보고서 생성됨] {self.report_path}")
        return ctx
