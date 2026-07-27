"""
ReportManager — 리포트 생성(reports/)과 내보내기(exports/) 전체의 유일한 관리자.

기존 아키텍처(ProfileManager/StorageService/RunManager/LogManager)는 그대로 두고 그 위에서
동작한다. 폴더는 이미 RunManager.create_run()이 만들어놓은 run.reports_dir/run.exports_dir을
쓸 뿐이고, 실제 파일 포맷별 생성 로직은 report/*.py(기존 Markdown/HTML/Docx/PDF/Excel/PPTX
빌더, report/base_reporter.py의 Registry)를 그대로 재사용한다 — 여기서 새로 만드는 건
"그 결과를 어디에 어떤 이름으로/어떤 공통 헤더를 붙여 저장할지"뿐이다.

모든 쓰기는 core.storage_service.StorageService(원자적 쓰기 + 버전 충돌 처리 + 로깅)를 거친다.
report/*.py의 개별 build_*()/save_*() 함수는 여전히 output_path를 직접 받는 시그니처라
(python-docx/openpyxl/python-pptx가 파일 경로 저장을 요구), 이 클래스가 임시 파일에
쓰게 한 뒤 그 바이트/텍스트를 다시 읽어 StorageService로 저장한다 — 그래서 report/*.py의
내부 로직은 전혀 손대지 않으면서도 "모든 리포트 파일은 StorageService를 거친다"는
아키텍처 원칙을 지킨다.
"""
import datetime
import io
import json
import os
import tempfile
import zipfile
from pathlib import Path

from core.paths import AppPaths
from core.storage_service import storage_service, PathTarget
from core.health_score import score_project
from engine.log_manager import log_manager

_TEXT_FORMATS = {"markdown": ".md", "html": ".html", "inspection": ".md"}
_BINARY_FORMATS = {"docx": ".docx", "pdf": ".pdf"}
REPORT_FORMATS = ("markdown", "docx", "pdf", "html", "excel", "json_summary", "inspection")
EXPORT_FORMATS = ("csv", "excel", "json", "zip")


def _app_version() -> str:
    version_file = AppPaths.app_root() / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


class ReportManagerError(Exception):
    """ReportManager 연산이 실패했을 때(지원하지 않는 포맷, 대상 없음 등) 던지는 공통 예외."""


class ReportManager:
    """리포트 생성(Generate/Preview/Validate)과 내보내기(Export *)의 단일 진입점.
    어떤 모듈도 report/*.py를 직접 import해서 파일을 쓰지 않고 이 클래스를 통해야 한다."""

    def __init__(self, storage=None, log_mgr=None):
        self._storage = storage or storage_service
        self._log_manager = log_mgr or log_manager

    # ---- 공통 헤더(모든 리포트 필수 항목) --------------------------------------------

    def build_header(self, run, *, scored=None, findings=None, ai_result=None, execution_time=None) -> dict:
        """모든 리포트에 공통으로 들어가야 하는 항목: Customer/Profile/Run ID/Execution Time/
        Generated Time/Application Version/Summary/Health Score/Device Statistics/
        Failed Devices/Warnings/Recommendations."""
        session = self._storage.load_json(run, "session.json", default={})
        health = score_project(findings) if findings else {"project_score": None, "device_scores": {}}

        failed_devices = sorted({
            f.device if hasattr(f, "device") else f.get("device")
            for f in (findings or [])
            if (f.result if hasattr(f, "result") else f.get("result")) == "FAIL"
        })
        warnings = [
            (f.check_id if hasattr(f, "check_id") else f.get("check_id"))
            for f in (findings or [])
            if (f.result if hasattr(f, "result") else f.get("result")) == "UNKNOWN"
        ]
        recommendations = []
        if ai_result and ai_result.get("top_priority"):
            recommendations = [
                f"{a['device']}/{a['check']}: {a.get('suggested_action', '')}"
                for a in ai_result["top_priority"]
            ]

        return {
            "customer": run.profile.customer, "profile": run.profile.name, "run_id": run.run_id,
            "execution_time": execution_time or session.get("start_time"),
            "generated_time": _now_iso(), "application_version": _app_version(),
            "summary": (ai_result or {}).get("summary", ""),
            "health_score": health,
            "device_statistics": {
                "success_count": session.get("success_count", 0),
                "failed_count": session.get("failed_count", 0),
                "skipped_count": session.get("skipped_count", 0),
            },
            "failed_devices": failed_devices,
            "warnings": warnings,
            "recommendations": recommendations,
        }

    @staticmethod
    def _header_lines(header: dict) -> list:
        h = header
        lines = [
            f"고객사: {h['customer']}  |  프로파일: {h['profile']}  |  Run ID: {h['run_id']}",
            f"실행 시각: {h.get('execution_time') or '-'}  |  생성 시각: {h['generated_time']}  |  "
            f"앱 버전: {h['application_version']}",
            f"Health Score: {h['health_score'].get('project_score')}  |  "
            f"성공/실패/스킵: {h['device_statistics']['success_count']}/"
            f"{h['device_statistics']['failed_count']}/{h['device_statistics']['skipped_count']}",
        ]
        if h["failed_devices"]:
            lines.append(f"실패 장비: {', '.join(h['failed_devices'])}")
        if h["warnings"]:
            lines.append(f"경고: {', '.join(str(w) for w in h['warnings'])}")
        if h["recommendations"]:
            lines.append(f"권고: {' / '.join(h['recommendations'])}")
        branding = h.get("branding") or {}
        if branding.get("company_name") or branding.get("logo_path"):
            lines.append(f"브랜딩: {branding.get('company_name', '-')} "
                          f"(로고: {branding.get('logo_path', '-')})")
        return lines

    # ---- 커스터머 브랜딩(템플릿/로고) --------------------------------------------------

    def _branding(self, run) -> dict:
        """profile/settings.json의 "branding" 키에서 고객사별 템플릿/로고 경로를 읽는다.
        {"company_name": "...", "logo_path": "...", "excel_template": "...", "pptx_template": "..."}
        — 없으면 빈 dict. excel_template/pptx_template이 지정돼 있으면 각 포맷 생성 시
        report/excel_report.write_into_template · report/pptx_report.apply_placeholders_to_pptx가
        기본 템플릿 대신 그 파일을 쓴다(향후 색상/폰트 등 커스텀 브랜딩도 이 dict만 확장하면 됨)."""
        from engine.profile_manager import profile_manager
        settings = profile_manager.load_profile(run.profile.customer, run.profile.name).get("settings", {})
        return settings.get("branding", {}) or {}

    # ---- Generate Report ----------------------------------------------------------

    def generate_report(self, run, format_id: str, *, name: str = None, scored: list = None,
                         findings: list = None, ai_result: dict = None, diff: dict = None,
                         root_causes=None, project_name: str = None, devices: list = None) -> Path:
        """reports/<name> 을 만든다. format_id: markdown/docx/pdf/html/excel/json_summary/inspection.
        project_name 생략 시 프로파일명을 쓴다. 실패(라이브러리 미설치 등)하면
        ReportManagerError를 던진다 — 호출부가 조용히 리포트 없이 넘어가지 않도록."""
        if format_id not in REPORT_FORMATS:
            raise ReportManagerError(f"지원하지 않는 리포트 포맷: {format_id} ({REPORT_FORMATS} 중 하나)")
        project_name = project_name or run.profile.name
        scored = scored or []
        header = self.build_header(run, scored=scored, findings=findings, ai_result=ai_result)
        header["branding"] = self._branding(run)
        filename = name or f"{format_id}_report{_TEXT_FORMATS.get(format_id) or _BINARY_FORMATS.get(format_id) or '.json'}"

        if format_id == "json_summary":
            data = {**header, "scored": scored,
                    "findings": [f.to_dict() if hasattr(f, "to_dict") else f for f in (findings or [])],
                    "ai_result": ai_result}
            return self._storage.save_report(run, filename, json.dumps(data, ensure_ascii=False, indent=2),
                                              overwrite=True)

        if format_id in ("markdown", "inspection"):
            text = self._build_text_report(format_id, project_name, scored, ai_result, findings, diff, root_causes)
            content = "\n".join(self._header_lines(header)) + "\n\n---\n\n" + text
            return self._storage.save_report(run, filename, content, overwrite=True)

        if format_id == "html":
            text = self._build_via_tempfile("html", project_name, scored, ai_result, root_causes, binary=False)
            if text is None:
                raise ReportManagerError("HTML 리포트 생성 실패")
            header_html = "<pre>" + "\n".join(self._header_lines(header)) + "</pre>\n"
            if "<body" in text:
                idx = text.index(">", text.index("<body")) + 1
                text = text[:idx] + header_html + text[idx:]
            else:
                text = header_html + text
            return self._storage.save_report(run, filename, text, overwrite=True)

        if format_id in ("docx", "pdf"):
            data = self._build_via_tempfile(format_id, project_name, scored, ai_result, root_causes, binary=True,
                                             header_lines=self._header_lines(header) if format_id == "pdf" else None)
            if data is None:
                raise ReportManagerError(
                    f"{format_id} 리포트 생성 실패 — 필요한 라이브러리가 설치돼 있는지 확인하세요."
                )
            return self._storage.save_report(run, filename, data, overwrite=True)

        if format_id == "excel":
            wb = self._build_excel_workbook(run, scored, findings, ai_result, header, devices or [])
            buf = io.BytesIO()
            wb.save(buf)
            return self._storage.save_report(run, filename, buf.getvalue(), overwrite=True)

        raise ReportManagerError(f"미구현 포맷: {format_id}")

    def _build_text_report(self, format_id, project_name, scored, ai_result, findings, diff, root_causes):
        if format_id == "inspection":
            from report.inspection_report import build_inspection_report
            return build_inspection_report(project_name, findings=findings, diff=diff, ai_result=ai_result)
        from report.markdown_report import build_markdown_report
        return build_markdown_report(project_name, scored, ai_result, root_causes=root_causes)

    def _build_via_tempfile(self, format_id, project_name, scored, ai_result, root_causes, *, binary,
                             header_lines=None):
        """report/*.py의 build_*()가 output_path를 직접 받아 파일에 쓰는 시그니처라, 임시 파일에
        쓰게 한 뒤 바이트/텍스트를 읽어 돌려준다 — 그 결과를 StorageService로 저장하는 건
        호출부(generate_report) 책임. 실패(미지원 라이브러리)하면 None."""
        suffix = _BINARY_FORMATS.get(format_id) or _TEXT_FORMATS.get(format_id) or ".tmp"
        fd, tmp_name = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        try:
            if format_id == "docx":
                from report.markdown_report import save_docx_report
                result = save_docx_report(project_name, scored, ai_result, tmp_name)
            elif format_id == "pdf":
                from report.pdf_report import save_pdf_report
                result = save_pdf_report(project_name, scored, ai_result, tmp_name, root_causes=root_causes,
                                          header_lines=header_lines)
            elif format_id == "html":
                from report.html_report import build_html_report
                result = build_html_report(project_name, scored, tmp_name, root_causes=root_causes)
            else:
                result = None
            if result is None:
                return None
            path = Path(tmp_name)
            return path.read_bytes() if binary else path.read_text(encoding="utf-8")
        finally:
            try:
                os.remove(tmp_name)
            except OSError:
                pass

    def _build_excel_workbook(self, run, scored, findings, ai_result, header, devices):
        """report/excel_report.build_full_report_workbook()을 그대로 재사용 — health는 이미
        계산해둔 header에서 가져온다(중복 계산 없음). devices는 [{"name","management_ip",
        "vendor",...}, ...] 형태(engine.device_inventory 포맷)를 그대로 넘기면 된다."""
        try:
            from report.excel_report import build_full_report_workbook
        except ImportError as e:
            raise ReportManagerError(f"Excel 리포트에 필요한 라이브러리가 없습니다({e}). "
                                      "pip install pandas openpyxl 후 다시 시도하세요.")
        findings_dicts = [f.to_dict() if hasattr(f, "to_dict") else f for f in (findings or [])]
        data = {
            "project_id": f"{run.profile.customer}/{run.profile.name}", "session": run.run_id,
            "stages": scored, "findings": findings_dicts, "devices": devices,
            "ai_source": (ai_result or {}).get("source"), "ai_summary": (ai_result or {}).get("summary"),
            "rule_based": ai_result, "health": header["health_score"],
        }
        return build_full_report_workbook(data)

    # ---- 조회(GUI의 Recent Reports/Recent Exports 패널용) ---------------------------

    def list_reports(self, run) -> list:
        """reports/의 파일명 목록(최근 생성 순 — mtime 내림차순)."""
        if not run.reports_dir.is_dir():
            return []
        return [p.name for p in sorted(run.reports_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
                if p.is_file()]

    def list_exports(self, run) -> list:
        """exports/의 파일명 목록(최근 생성 순 — mtime 내림차순)."""
        if not run.exports_dir.is_dir():
            return []
        return [p.name for p in sorted(run.exports_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
                if p.is_file()]

    # ---- Preview / Validate --------------------------------------------------------

    def preview_report(self, run, format_id: str, *, scored: list = None, findings: list = None,
                        ai_result: dict = None, project_name: str = None) -> str:
        """파일을 저장하지 않고 내용(텍스트 포맷은 본문, 바이너리 포맷은 '미리보기 불가' 안내)만
        반환한다 — UI에서 "저장 전 확인" 용도."""
        header = self.build_header(run, scored=scored, findings=findings, ai_result=ai_result)
        header_text = "\n".join(self._header_lines(header))
        if format_id in ("markdown", "inspection"):
            body = self._build_text_report(format_id, project_name or run.profile.name, scored or [],
                                            ai_result, findings, None, None)
            return header_text + "\n\n---\n\n" + body
        if format_id == "json_summary":
            return json.dumps(header, ensure_ascii=False, indent=2)
        return header_text + f"\n\n({format_id} 포맷은 바이너리라 미리보기는 헤더 정보만 표시됩니다.)"

    def validate_report(self, run, filename: str) -> dict:
        """reports/<filename>이 실제로 존재하고 비어있지 않은지, 알려진 확장자인지 검사한다.
        반환: {"valid": bool, "errors": [...], "size_bytes": int}."""
        path = run.reports_dir / filename
        errors = []
        if not path.is_file():
            errors.append("파일이 존재하지 않습니다.")
            return {"valid": False, "errors": errors, "size_bytes": 0}
        size = path.stat().st_size
        if size == 0:
            errors.append("파일이 비어있습니다.")
        known_ext = {".md", ".html", ".docx", ".pdf", ".xlsx", ".json"}
        if path.suffix not in known_ext:
            errors.append(f"알 수 없는 확장자: {path.suffix}")
        return {"valid": not errors, "errors": errors, "size_bytes": size}

    # ---- Export Report --------------------------------------------------------------

    def export_report(self, run, report_filename: str, *, as_zip: bool = False) -> Path:
        """이미 생성된 reports/<report_filename>을 exports/로 복사한다(고객 전달용 산출물 분리).
        as_zip=True면 exports/<name>.zip으로 압축해서 내보낸다."""
        src = run.reports_dir / report_filename
        if not src.is_file():
            raise ReportManagerError(f"리포트가 없습니다: {report_filename}")
        if as_zip:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(src, arcname=report_filename)
            return self._storage.save_bytes(run, f"exports/{Path(report_filename).stem}.zip", buf.getvalue())
        written = self._storage.export_files(run, {report_filename: src})
        return written[0] if written else None

    # ---- Export Logs -----------------------------------------------------------------

    def export_logs(self, run, *, categories=("raw", "masked", "parsed"), devices: list = None,
                     fmt: str = "zip") -> Path:
        """개별 로그(카테고리별 raw/masked/parsed)를 exports/로 묶어 내보낸다.
        fmt="zip"(기본)이면 하나의 zip으로, fmt="csv"면 파일 목록(이름/카테고리/크기)만 CSV로."""
        category_dirs = {"raw": run.raw_dir, "masked": run.masked_dir, "parsed": run.parsed_dir,
                          "analysis": run.analysis_dir}
        entries = []  # (arcname, absolute_path)
        for category in categories:
            names = self._log_manager.list_logs(run, category)
            for name in names:
                if devices and Path(name).stem not in devices:
                    continue
                entries.append((f"{category}/{name}", category_dirs[category] / name))

        if fmt == "csv":
            rows = [{"category": arc.split("/")[0], "file": arc.split("/")[1], "size_bytes": p.stat().st_size}
                    for arc, p in entries]
            return self._storage.save_csv(run, "exports/logs_manifest.csv", rows)
        if fmt != "zip":
            raise ReportManagerError(f"export_logs는 zip/csv만 지원합니다: {fmt}")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for arcname, path in entries:
                if path.is_file():
                    zf.write(path, arcname=arcname)
        return self._storage.save_bytes(run, f"exports/logs_{run.run_id}.zip", buf.getvalue())

    # ---- Export Inventory / Commands ------------------------------------------------

    def export_inventory(self, run, devices: list, *, fmt: str = "excel") -> Path:
        """장비목록(Device Inventory)을 exports/로 내보낸다. fmt: excel/csv/json."""
        if fmt == "excel":
            from engine.device_inventory_import_export import export_to_excel
            return self._export_via_tempfile_excel(run, "exports/inventory.xlsx", export_to_excel, devices)
        if fmt == "csv":
            return self._storage.save_csv(run, "exports/inventory.csv", devices)
        if fmt == "json":
            return self._storage.save_json(run, "exports/inventory.json", devices)
        raise ReportManagerError(f"export_inventory는 excel/csv/json만 지원합니다: {fmt}")

    def export_commands(self, run, catalog, *, fmt: str = "excel") -> Path:
        """커맨드 카탈로그(Command Catalog)를 exports/로 내보낸다. fmt: excel/csv/json."""
        if fmt == "excel":
            from engine.command_catalog import export_to_excel
            return self._export_via_tempfile_excel(run, "exports/commands_catalog.xlsx", export_to_excel, catalog)
        if fmt == "csv":
            rows = catalog if isinstance(catalog, list) else catalog.get("items", [])
            return self._storage.save_csv(run, "exports/commands_catalog.csv", rows)
        if fmt == "json":
            return self._storage.save_json(run, "exports/commands_catalog.json", catalog)
        raise ReportManagerError(f"export_commands는 excel/csv/json만 지원합니다: {fmt}")

    def _export_via_tempfile_excel(self, run, rel_path, export_fn, data) -> Path:
        """engine.device_inventory_import_export.export_to_excel/engine.command_catalog.export_to_excel는
        둘 다 (data, path)를 받아 openpyxl로 직접 파일에 저장하는 기존 함수라, 임시 파일에 쓰게 한 뒤
        바이트를 다시 읽어 StorageService로 저장한다(기존 함수는 손대지 않음)."""
        fd, tmp_name = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        try:
            export_fn(data, tmp_name)
            return self._storage.save_bytes(run, rel_path, Path(tmp_name).read_bytes())
        finally:
            try:
                os.remove(tmp_name)
            except OSError:
                pass

    # ---- Export Run / Workspace -----------------------------------------------------

    def export_run(self, run) -> Path:
        """run 전체(raw/masked/parsed/analysis/reports — exports/ 자기 자신은 재귀 방지로 제외)를
        exports/run_<run_id>.zip 하나로 묶는다(고객 전달/이관용 단일 파일)."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in run.path.rglob("*"):
                if not path.is_file() or run.exports_dir in path.parents:
                    continue
                zf.write(path, arcname=str(path.relative_to(run.path)))
        return self._storage.save_bytes(run, f"exports/run_{run.run_id}.zip", buf.getvalue())

    def export_workspace(self, profile) -> Path:
        """프로파일 전체(profile/inventory/commands/baselines/runs/history/cache/archive)를
        archive/workspace_export_<타임스탬프>.zip으로 묶는다("Complete Workspace" 내보내기).
        exports/는 run 하나에만 있으므로(프로파일 레벨엔 없음) 이미 존재하는 archive/ 서브폴더에
        저장한다 — 새 폴더를 만들지 않는다."""
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in profile.path.rglob("*"):
                if path.is_file():
                    zf.write(path, arcname=str(path.relative_to(profile.path)))
        return self._storage.save_bytes(profile, f"archive/workspace_export_{ts}.zip", buf.getvalue())


# 상태 없는 서비스(모든 상태는 파일에 있음)이므로 앱 전역 공유 인스턴스로 충분하다.
report_manager = ReportManager()
