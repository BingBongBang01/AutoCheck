"""
기존 함수(main.py의 adapt_raw_to_collected, engine.scorer.score_all, ai_analysis.router,
report.markdown_report)를 전혀 안 바꾸고 PipelineStep으로 감싼다.
main.py의 grade()가 이 Step들을 순서대로 호출하던 걸 Pipeline이 대신 하게 됨.
"""
import time

try:
    from pipeline.step import PipelineStep
    from plugins.vendors.base import get_driver
    import plugins.vendors.arista  # noqa: F401 (import 자체가 register() 부작용을 일으킴)
    import plugins.vendors.cisco  # noqa: F401
    from plugins.parsers.cache import parse_cached
    from rule_engine.rules import run_vlan_stp_rules, run_extended_stage_rules, run_inspection_rules, findings_to_verdicts
    from rule_engine import registry as rule_registry
    from engine.scorer import score_all, print_scoreboard
    from engine.history import save_history, load_previous, compare_sessions, compare_check_level, compare_findings
    from core.structured_log import log_event as slog_event
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from pipeline.step import PipelineStep
    from plugins.vendors.base import get_driver
    import plugins.vendors.arista  # noqa: F401
    import plugins.vendors.cisco  # noqa: F401
    from plugins.parsers.cache import parse_cached
    from rule_engine.rules import run_vlan_stp_rules, run_extended_stage_rules, run_inspection_rules, findings_to_verdicts
    from rule_engine import registry as rule_registry
    from engine.scorer import score_all, print_scoreboard
    from engine.history import save_history, load_previous, compare_sessions, compare_check_level, compare_findings
    from core.structured_log import log_event as slog_event


from pipeline.base import AbstractCollector, AbstractParser

# check_id -> collected dict 키. registry에 등록된 stage들의 collected_key와 1:1로 맞춰,
# ParserStep이 어떤 stage 데이터를 만드는지 rule_engine/registry.py 한 곳만 보면 알 수 있게 한다.
_CHECK_ID_TO_COLLECTED_KEY = {
    "port_channel_status": "lacp", "mlag_status": "mlag", "ospf_neighbor": "ospf",
    "vrrp_status": "vrrp", "acl_status": "acl", "ntp_status": "ntp", "snmp_status": "snmp",
}


class CollectorStep(PipelineStep):
    name = "collector"

    def __init__(self, collector: AbstractCollector):
        self.collector = collector

    def run(self, ctx):
        started = time.time()
        raw = self.collector.collect()
        ctx.raw_by_device = raw
        ctx.data["collect_elapsed"] = time.time() - started
        return ctx


class DefaultParser(AbstractParser):
    def __init__(self, device_vendors=None, default_vendor="arista"):
        self.device_vendors = device_vendors or {}
        self.default_vendor = default_vendor

    def _vendor_for(self, device):
        return self.device_vendors.get(device, self.default_vendor)

    def parse(self, raw_by_device):
        collected_vlan, collected_stp = {}, {}
        collected_extended = {key: {} for key in set(_CHECK_ID_TO_COLLECTED_KEY.values())}
        parsed_count, skipped_count = 0, 0

        for device, cmds in raw_by_device.items():
            vendor = self._vendor_for(device)
            driver = get_driver(vendor)
            reverse_map = driver.reverse_command_map()
            for cmd_text, raw in cmds.items():
                check_id = reverse_map.get(cmd_text)
                if check_id is None:
                    continue
                parsed = parse_cached(vendor, check_id, raw)
                if parsed is None:
                    skipped_count += 1
                    continue
                parsed_count += 1
                if check_id == "vlan_status":
                    collected_vlan[device] = parsed
                elif check_id == "stp_status":
                    collected_stp[device] = parsed
                elif check_id in _CHECK_ID_TO_COLLECTED_KEY:
                    collected_extended[_CHECK_ID_TO_COLLECTED_KEY[check_id]][device] = parsed

        return {
            "collected_vlan": collected_vlan,
            "collected_stp": collected_stp,
            "collected_extended": collected_extended,
            "parsed_count": parsed_count,
            "skipped_count": skipped_count
        }

class ParserStep(PipelineStep):
    """
    collector 원본 CLI 출력을 comparator가 기대하는 구조화된 형태로 변환한다.
    Parser Plugin 원칙: raw CLI 텍스트 -> check_id는 VendorDriver.reverse_command_map()으로,
    check_id -> 파싱 결과는 plugins/parsers/registry.py(+cache)로 찾는다.
    이 Step은 "무엇을 파싱하는지"만 알고 "어떻게 파싱하는지"는 전혀 모른다 —
    벤더/체크 추가 시 이 파일은 안 건드리고 plugins/vendors, plugins/parsers/registry만 늘리면 됨.
    Parser는 여기서 SSH에 연결하지 않는다 — CollectorStep이 이미 수집해둔 raw 텍스트만 받는다.
    """
    name = "parser"

    def __init__(self, parser: AbstractParser):
        self.parser = parser

    def run(self, ctx):
        if ctx.raw_by_device is None:
            return ctx  # 수집 실패 — 뒤 스텝들이 각자 None 체크

        started = time.time()
        parsed_data = self.parser.parse(ctx.raw_by_device)
        
        ctx.data["collected_vlan"] = parsed_data.get("collected_vlan", {})
        ctx.data["collected_stp"] = parsed_data.get("collected_stp", {})
        ctx.data["collected_extended"] = parsed_data.get("collected_extended", {})
        
        parsed_count = parsed_data.get("parsed_count", 0)
        skipped_count = parsed_data.get("skipped_count", 0)
        
        slog_event("parser", "parse_step", job_id=ctx.session_id,
                   duration_ms=round((time.time() - started) * 1000, 1),
                   message=f"parsed={parsed_count} skipped(파서 미등록)={skipped_count} devices={len(ctx.raw_by_device)}")
        return ctx


class RuleEngineStep(PipelineStep):
    """rule_engine.rules를 호출해 Finding 객체를 만든다 — 판정(PASS/FAIL)이 확정되는 지점."""
    name = "rule_engine"

    def __init__(self, target_state):
        self.target_state = target_state

    def run(self, ctx):
        if ctx.raw_by_device is None:
            return ctx

        if ctx.project.mode == "inspection":
            findings = run_inspection_rules(
                ctx.project.project_id, ctx.session_id,
                ctx.project.meta.get("inspection_rules", []),
                ctx.data.get("collected_vlan", {}),
            )
            ctx.findings.extend(findings)
            ctx.data["verdicts_by_stage"] = {}
            return ctx

        started = time.time()
        result = run_vlan_stp_rules(
            ctx.project.project_id, ctx.session_id, self.target_state,
            ctx.data.get("collected_vlan", {}), ctx.data.get("collected_stp", {}),
        )
        extended = run_extended_stage_rules(
            ctx.project.project_id, ctx.session_id, self.target_state,
            ctx.data.get("collected_extended", {}),
        )
        result.update(extended)
        for stage_findings in result.values():
            ctx.findings.extend(stage_findings)
        ctx.data["verdicts_by_stage"] = {k: findings_to_verdicts(v) for k, v in result.items()}

        total_duration_ms = round((time.time() - started) * 1000, 1)
        for stage_id, stage_findings in result.items():
            slog_event("rule", "rule_eval", job_id=ctx.session_id, rule=stage_id,
                       duration_ms=total_duration_ms, message=f"findings={len(stage_findings)}")
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
        
        try:
            from report.text_renderer import render_text_report
            if getattr(ctx, "findings", None):
                print(render_text_report(ctx.findings))
            else:
                print_scoreboard(ctx.scored, session_label=f"(Pipeline 실행, {elapsed:.1f}초 소요)")
        except ImportError:
            print_scoreboard(ctx.scored, session_label=f"(Pipeline 실행, {elapsed:.1f}초 소요)")
            
        return ctx


class HistoryStep(PipelineStep):
    name = "history"

    def run(self, ctx):
        if ctx.raw_by_device is None:
            return ctx
        project_id = ctx.project.project_id
        prev = load_previous(project_id)

        if ctx.project.mode == "inspection":
            findings_dicts = [f.to_dict() if hasattr(f, "to_dict") else f for f in ctx.findings]
            diff = compare_findings(prev, {"findings": findings_dicts}) if prev else None
            ctx.data["diff"] = diff
            path = save_history(project_id, ctx.scored, ctx.elapsed_sec, findings=ctx.findings, diff=diff)
            print(f"\n[저장됨] {path}")
            if diff:
                print(f"\n[직전 회차({prev['session']}) 대비 Finding 변화]")
                print(f"  신규(NEW): {len(diff['new'])}건, 재발/유지(PERSISTENT): {len(diff['persistent'])}건, 해소(RESOLVED): {len(diff['resolved'])}건")
                for f in diff["new"]:
                    print(f"  [NEW] {f['device']} {f['check_id']}: {f['result']}")
                for f in diff["resolved"]:
                    print(f"  [RESOLVED] {f['device']} {f['check_id']}")
            return ctx

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


class AlarmStep(PipelineStep):
    """Critical Finding 발생 시 등록된 AlarmHandler 전체로 즉시 통보 — result/severity는 안 건드림."""
    name = "alarm"

    def run(self, ctx):
        if ctx.raw_by_device is None:
            return ctx
        import alarm  # noqa: F401 (import 자체가 핸들러 register() 부작용을 일으킴)
        from alarm.base import notify_all
        from core.finding import SEVERITY_CRITICAL

        critical = [f.to_dict() for f in ctx.findings if f.severity == SEVERITY_CRITICAL]
        notify_all(ctx.project.project_id, critical)
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
        ai_result = ctx.data.get("ai_result")

        if ctx.project.mode == "inspection":
            import report.reporters  # noqa: F401 (import 자체가 InspectionReporter register() 부작용을 일으킴)
            from report.base_reporter import get_reporter
            reporter = get_reporter("inspection")
            reporter.build(ctx.project.project_id, ctx.scored, ai_result, self.report_path,
                            findings=ctx.findings, diff=ctx.data.get("diff"))
            print(f"[보고서 생성됨] {self.report_path}")
            return ctx

        from report.markdown_report import save_markdown_report
        save_markdown_report(ctx.project.project_id, ctx.scored, ai_result, self.report_path)
        print(f"[보고서 생성됨] {self.report_path}")
        return ctx
