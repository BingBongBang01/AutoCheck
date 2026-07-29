"""ReportApiMixin — 보고서 생성(Report Plugin 포함)만 담당."""
import os
import glob
import datetime

from core.paths import AppPaths


def _latest_terminal_log_paths_by_device(project_id):
    """labs/{project}/terminal_sessions/AutoCheck_{device}_{timestamp}.txt 중 장비별 최신 파일 1개씩
    {device: (mtime, path)} 로 반환. 본문까지 필요하면 _latest_terminal_logs_by_device()를 쓰고,
    수집 시각(mtime)만 필요한 대시보드 집계는 이 함수를 직접 쓴다."""
    session_dir = str(AppPaths.terminal_sessions_dir(project_id))
    if not os.path.isdir(session_dir):
        return {}
    latest = {}
    for path in glob.glob(os.path.join(session_dir, "AutoCheck_*.txt")):
        fname = os.path.basename(path)
        body = fname[len("AutoCheck_"):-len(".txt")]
        # 파일명 형식: AutoCheck_{device}_{YYYYMMDD}_{HHMMSS}.txt — 끝의 날짜/시간 두 토큰을 떼어내야
        # device 이름에 '_'가 들어있어도(예: Core_Sw1) 안 깨진다.
        parts = body.rsplit("_", 2)
        device = parts[0] if len(parts) == 3 else body
        if not device:
            continue
        mtime = os.path.getmtime(path)
        if device not in latest or mtime > latest[device][0]:
            latest[device] = (mtime, path)
    return latest


def _latest_terminal_logs_by_device(project_id):
    """장비별 최신 점검 로그 원문 — {device: raw_text}. 점검 로그 탭 -> 보고서 탭 흐름의 데이터 소스."""
    result = {}
    for device, (_, path) in _latest_terminal_log_paths_by_device(project_id).items():
        with open(path, encoding="utf-8") as f:
            result[device] = f.read()
    return result


from engine.log_analysis import ANOMALY_KEYWORDS as _ANOMALY_KEYWORDS


def _scan_anomalies(text):
    """텍스트를 줄 단위로 훑어 이상 징후 키워드가 포함된 줄만 뽑아낸다(대소문자 무시)."""
    from report.textfsm_parser import split_raw_log
    sections = split_raw_log(text) or {"(전체)": text}
    findings = []
    for command, output in sections.items():
        for line_no, line in enumerate(output.splitlines(), start=1):
            upper = line.upper()
            hit = next((kw for kw in _ANOMALY_KEYWORDS if kw in upper), None)
            if hit:
                findings.append({"command": command, "line_no": line_no, "line": line.strip(), "keyword": hit})
    return findings


class ReportApiMixin:
    def generate_report(self):
        try:
            project_id = self._project()
        except RuntimeError:
            return None
        from engine.history import load_latest
        latest = load_latest(project_id)
        if not latest:
            return None
        from ai_analysis.router import analyze as ai_analyze
        from report.markdown_report import build_markdown_report
        from report.reporters import MarkdownReporter
        ai_result = ai_analyze(latest["stages"], ai_config=None)
        md = build_markdown_report(project_id, latest["stages"], ai_result)
        paths = self._paths()
        out_path = paths["target_state"].replace("target_state.yaml", "report_latest.md")
        MarkdownReporter().build(project_id, latest["stages"], ai_result, out_path)
        return md

    def generate_report_as(self, format_id):
        """Report Plugin 목록에서 형식 선택해서 생성 — markdown/docx."""
        try:
            project_id = self._project()
        except RuntimeError:
            return {"error": "활성 프로젝트 없음"}
        from engine.history import load_latest
        latest = load_latest(project_id)
        if not latest:
            return {"error": "채점 이력 없음"}
        from ai_analysis.router import analyze as ai_analyze
        from report.reporters import get_reporter
        from report.base_reporter import list_formats
        reporter = get_reporter(format_id)
        if not reporter:
            return {"error": f"지원 안 하는 형식: {format_id} (지원: {list_formats()})"}
        ai_result = ai_analyze(latest["stages"], ai_config=None)
        paths = self._paths()
        out_path = paths["target_state"].replace("target_state.yaml", f"report_latest{reporter.file_extension}")
        result_path = reporter.build(project_id, latest["stages"], ai_result, out_path)
        if not result_path:
            return {"error": f"{format_id} 생성 실패(필요 라이브러리 미설치 가능성)"}
        return {"path": result_path}

    def list_report_formats(self):
        import report.reporters  # 플러그인(reporter)들을 시스템에 등록하기 위해 모듈 로드
        from report.base_reporter import list_formats  # 실제 반환할 함수는 베이스 모듈에서 로드
        return list_formats()

    def get_report_devices(self):
        """점검 로그 탭에서 수집된(=terminal_sessions에 저장된) 장비 목록 — 보고서 탭 대상 선택용."""
        try:
            project_id = self._project()
        except RuntimeError:
            return []
        return sorted(_latest_terminal_logs_by_device(project_id).keys())

    def get_raw_log_findings(self):
        """요구사항 4 — engine.history(채점 이력)와 무관하게, 점검 로그(terminal_sessions)의
        장비별 최신 원본 출력에서 이상 징후 키워드가 있는 줄만 뽑아 보고서 탭에서 바로 보여준다."""
        try:
            project_id = self._project()
        except RuntimeError:
            return []
        raw_logs = _latest_terminal_logs_by_device(project_id)
        result = []
        for device, text in raw_logs.items():
            findings = _scan_anomalies(text)
            if findings:
                result.append({"device": device, "findings": findings})
        return sorted(result, key=lambda r: r["device"])

    def generate_excel_report(self, template_path=None):
        """요구사항 6 — 점검 로그(terminal_sessions)의 최신 장비별 raw output을
        textfsm/ntc-templates로 파싱하고, pandas.DataFrame.T로 축을 뒤집어(장비=열)
        .xlsx(템플릿 있으면 그 서식 유지)로 내보낸다. 접속 실패 장비는 '접속 불가'로 채운다."""
        try:
            project_id = self._project()
        except RuntimeError:
            return {"error": "활성 프로젝트 없음"}
        raw_logs = _latest_terminal_logs_by_device(project_id)
        if not raw_logs:
            return {"error": "점검 로그가 없습니다. 먼저 세션 터미널에서 점검을 실행하세요."}

        from report.textfsm_parser import build_report_dataset
        from report.excel_report import write_into_template

        dataset = build_report_dataset(raw_logs)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = str(AppPaths.labs_root() / project_id / f"AutoCheck_report_{ts}.xlsx")
        result_path = write_into_template(dataset, template_path, out_path)
        return {"path": result_path, "devices": list(dataset.keys())}

    def export_report_excel(self, project_id=None):
        """Excel 리포트 생성용 데이터 취합 — 스타일링은 별도 단계(추후 구현), 여기선 데이터만 반환.
        get_analysis()와 동일한 흐름(engine.history -> AI 분석)을 재사용하고,
        inventory._load_inventory()로 findings에 장비 정보를 조인하며,
        rule_based.analyze()/suggest_action()으로 규칙기반 조치권고를 findings에 덧붙인다."""
        try:
            project_id = project_id or self._project()
        except RuntimeError:
            return {"error": "활성 프로젝트가 없습니다."}

        from engine.history import load_latest
        latest = load_latest(project_id)
        if not latest:
            return {"error": "채점 이력이 없습니다."}

        stages = latest["stages"]
        findings = latest.get("findings", [])

        from engine import project_manager as pm
        paths = pm.project_paths(project_id)
        inv = self._load_inventory(paths)
        devices_by_name = {d["name"]: d for d in inv["devices"]}

        from ai_analysis.rule_based import suggest_action, analyze as rule_based_analyze
        joined_findings = [
            {**f, "device_info": devices_by_name.get(f.get("device"), {}),
             "suggested_action": suggest_action(f.get("check_id", ""))}
            for f in findings
        ]

        # AI 분석 — get_analysis()와 동일한 흐름(Settings에서 정한 provider 우선순위 반영)
        from ai_analysis.router import analyze as ai_analyze
        ai_order = self.get_ai_settings()["order"]
        raw_cfg = self._load_ai_config()
        by_type = {p.get("type"): p for p in raw_cfg.get("providers", [])}
        providers = [by_type[i] for i in ai_order if i in by_type]
        # 배치 설정의 정본 키는 local_batching — save_batching_settings()가 그리로 쓰고 레거시
        # batching 키는 지운다. 예전엔 여기서 batching만 읽어서, 환경 설정에서 조정한
        # batch_chars/max_tokens가 분석·보고서 경로에는 전혀 반영되지 않았다.
        batching = raw_cfg.get("local_batching") or raw_cfg.get("batching", {})
        ai_result = ai_analyze(stages, ai_config={"providers": providers, "batching": batching})

        rule_based_result = rule_based_analyze(stages)

        from core.health_score import score_project
        health = score_project(findings) if findings else {"project_score": None, "device_scores": {}}

        return {
            "project_id": project_id,
            "session": latest["session"],
            "stages": stages,
            "findings": joined_findings,
            "devices": inv["devices"],
            "ai_source": ai_result["source"],
            "ai_summary": ai_result["summary"],
            "rule_based": rule_based_result,
            "health": health,
        }

    def save_report_excel(self, project_id=None):
        """export_report_excel()+report.excel_report.build_full_report_workbook()으로 만든 워크북을
        저장 다이얼로그로 받은 경로에 저장 — 다이얼로그 구현은 logs_api.export_full_log()와 동일 패턴."""
        import webview
        from api.window_ref import get_window

        try:
            project_id = project_id or self._project()
        except RuntimeError:
            return {"success": False, "reason": "활성 프로젝트가 없습니다."}

        window = get_window()
        default_name = f"AutoCheck_Report_{project_id}_{datetime.datetime.now().strftime('%Y%m%d')}.xlsx"
        result = window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=default_name,
            file_types=("Excel files (*.xlsx)",),
        )
        if not result:
            return {"success": False, "reason": "cancelled"}
        dst = result if isinstance(result, str) else result[0]
        if not dst.lower().endswith(".xlsx"):
            dst += ".xlsx"

        data = self.export_report_excel(project_id)
        if "error" in data:
            return {"success": False, "reason": data["error"]}

        from report.excel_report import build_full_report_workbook
        wb = build_full_report_workbook(data)
        try:
            wb.save(dst)
        except OSError as exc:
            return {"success": False, "reason": str(exc)}
        finally:
            wb.close()
        return {"success": True, "path": dst}

    def generate_pptx_report(self, device_name=None, template_path=None):
        """요구사항 6 — 장비 1대(또는 device_name 미지정 시 로그가 1대뿐일 때)의 metrics를
        {{PLACEHOLDER}} 템플릿에 run.text 레벨로만 채워 넣어 원본 서식을 보존한다."""
        try:
            project_id = self._project()
        except RuntimeError:
            return {"error": "활성 프로젝트 없음"}
        raw_logs = _latest_terminal_logs_by_device(project_id)
        if not raw_logs:
            return {"error": "점검 로그가 없습니다. 먼저 세션 터미널에서 점검을 실행하세요."}
        if device_name and device_name not in raw_logs:
            return {"error": f"'{device_name}' 장비의 점검 로그가 없습니다."}

        from report.textfsm_parser import build_report_dataset
        from report.pptx_report import build_placeholder_map, apply_placeholders_to_pptx, build_blank_template

        dataset = build_report_dataset(raw_logs)
        try:
            mapping = build_placeholder_map(dataset, device_name)
        except ValueError as e:
            return {"error": str(e)}

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        target_device = device_name or next(iter(dataset))
        out_path = str(AppPaths.labs_root() / project_id / f"AutoCheck_report_{target_device}_{ts}.pptx")
        if not template_path or not os.path.exists(template_path):
            template_path = str(AppPaths.labs_root() / project_id / "_pptx_template_default.pptx")
            if not os.path.exists(template_path):
                build_blank_template(template_path)
        result_path = apply_placeholders_to_pptx(template_path, out_path, mapping)
        return {"path": result_path, "device": target_device}
