"""InspectionReportApiMixin — '보고서' 탭. 정기점검 보고서 미리보기/생성(Excel·PDF)/목록/삭제.

데이터 조립·판정·렌더링은 전부 engine/inspection_report_builder.py(포맷 레지스트리)와
report/inspection_excel.py·inspection_pdf.py에 있고, 이 클래스는 웹 UI에 필요한 형태로 얇게 감싸기만 한다.
활성 고객사/프로파일 이름은 CustomerProfileApiMixin._resolve_active_customer_profile_names()로
얻으므로, 항상 그 mixin과 함께 Api 클래스에 조합되어야 한다.
"""
import os

from engine import log_storage
from api.realtime_watch_api import flush_realtime_state
from engine import inspection_report_builder as builder
from engine.inspection_report_builder import InspectionReportError


class InspectionReportApiMixin:
    def _report_target(self):
        """(고객사명, 프로파일명) — 활성 프로파일이 없으면 None."""
        try:
            self._project()
        except RuntimeError:
            return None
        return self._resolve_active_customer_profile_names()

    def _report_log_dirs(self, customer, profile, run_id):
        """보고서가 읽을 원본 로그 폴더 — run_id 회차의 raw 만(화면에서 고른 점검), 없으면 None(모든 회차에서 장비마다
        가장 최근 로그). 고른 회차가 없어졌으면 InspectionReportError."""
        if not run_id:
            return None
        run = log_storage.get_run_log_paths(customer, profile, run_id)
        if run is None:
            from api.log_analysis_run_api import NO_RUN_ERROR
            raise InspectionReportError(NO_RUN_ERROR)
        return [run["original"]]

    def get_inspection_report_context(self, run_id=None):
        """보고서 탭 진입 시 미리보기 데이터 — 어떤 장비가 몇 개 항목으로, 무엇이 확인필요인지.
        엑셀 파일은 만들지 않는다. 실제 값까지 그대로 담아 UI에서 표로 그릴 수 있게 한다.
        run_id: 이 점검 회차의 로그만으로 만든다(화면에서 고른 것). 없으면 모든 회차에서 장비마다 가장 최근 로그."""
        target = self._report_target()
        if target is None:
            return {"error": "활성 고객사/정기점검 프로파일이 없습니다. 워크스페이스에서 먼저 선택하세요."}
        customer, profile = target
        try:
            log_dirs = self._report_log_dirs(customer, profile, run_id)
            context = builder.build_context(customer, profile, project_id=self._project(), log_dirs=log_dirs)
        except InspectionReportError as exc:
            return {"error": str(exc), "customer": customer, "profile": profile,
                     "filename": builder.build_filename(customer, profile),
                     "reports_dir": str(builder.reports_dir(customer, profile))}
        # template 전체(설정 원문)는 UI에서 쓰지 않으므로 항목 수만 넘긴다 — 브리지 전송량 절약.
        from engine import inspection_evaluator
        return {
            "customer": customer, "profile": profile,
            # 고객사 템플릿(IMPROVEMENT_PLAN 4-10) — 쓰는지, 무엇을 바꿨는지, 읽지 못했는지.
            "template": inspection_evaluator.customer_template_status(customer),
            # 가상환경 프로파일이면 하드웨어 항목이 '해당없음'으로 나온다 — 미리보기에서
            # 왜 그런지 보이도록 플래그를 그대로 내려준다.
            "is_virtual": context["is_virtual"],
            "title": f"{customer} {profile}",
            "inspection_date": context["inspection_date"],
            "generated_at": context["generated_at"],
            "manager": context["manager"], "inspector": context["inspector"],
            "check_item_count": len(context["template"]["check_items"]),
            "log_count": context["log_count"],
            "previous_available": context["previous_available"],
            "filename": builder.build_filename(customer, profile,
                                                date=context["inspection_date"]),
            "reports_dir": str(builder.reports_dir(customer, profile)),
            # 고른 회차(없으면 최신 회차)의 원본 로그 폴더(없으면 빈 문자열) — 'Open Folder' 버튼용.
            "original_log_dir": (log_dirs[0] if log_dirs
                                 else next((str(d) for d in builder.original_log_dirs(customer, profile)), "")),
            "run_id": run_id or "",
            "devices": [
                {k: device[k] for k in ("name", "model", "ip", "serial", "os_version", "role",
                                         "location", "warranty",
                                         "unreachable", "collected_at", "command_count",
                                         "warn_count", "na_count", "overall_status", "remarks")}
                for device in context["devices"]
            ],
            "device_items": {
                device["name"]: [
                    {"no": item["no"], "group": item["group"], "name": item["name"],
                     "method": item["method"], "criteria": item["criteria"],
                     "previous": item.get("previous", ""), "value": item["value"],
                     "status": item["status"]}
                    for item in device["items"]
                ]
                for device in context["devices"]
            },
        }

    # options 키 -> build_context() 인자. 빈 값은 프로파일 기본값을 쓰도록 None 으로 넘긴다.
    _REPORT_OPTIONS = {"inspection_date": "inspection_date", "manager": "manager", "inspector": "inspector",
                       "confirmer": "confirmer", "site_name": "site_name", "vendor": "vendor",
                       "report_title": "report_title", "devices": "devices_filter"}

    def _export_inspection(self, format_id, options, viewer):
        target = self._report_target()
        if target is None:
            return {"error": "활성 고객사/정기점검 프로파일이 없습니다."}
        customer, profile = target
        options = options or {}
        kwargs = {arg: options.get(key) or None for key, arg in self._REPORT_OPTIONS.items()}
        flush_realtime_state(self)
        try:
            log_dirs = self._report_log_dirs(customer, profile, options.get("run_id"))
            result = builder.export(customer, profile, format_id, project_id=self._project(),
                                    filename=options.get("filename") or None, log_dirs=log_dirs, **kwargs)
        except InspectionReportError as exc:
            return {"error": str(exc)}
        except RuntimeError as exc:
            # register_fonts()가 한글 TTF를 못 찾으면 여기로 온다(맑은 고딕/나눔고딕 미설치 환경).
            return {"error": str(exc)}
        except OSError as exc:
            # 파일이 열려 있으면 PermissionError가 난다 — 사용자가 바로 알 수 있게 전달.
            return {"error": f"보고서 파일을 저장할 수 없습니다({exc}). 같은 이름의 파일이 "
                              f"{viewer}에서 열려 있으면 닫고 다시 시도하세요."}
        return {"ok": True, **result}

    def export_inspection_report(self, options=None):
        """'보고서 생성' 버튼 — data/<고객사>/<프로파일>/reports/ 에 엑셀을 저장한다.
        options: {inspection_date, manager:{company,name,contact}, inspector:{...},
                   devices:[장비명...], filename, run_id(이 점검 회차의 로그만)} — 전부 생략 가능."""
        return self._export_inspection("excel", options, "엑셀")

    def export_inspection_pdf_report(self, options=None):
        """'PDF 보고서 생성' 버튼 — 표준 양식 PDF 를 같은 reports/ 에 저장한다. 데이터 조립은 엑셀과
        같고(build_context) 표지/장비목록/지원이력 등 표준 전용 필드만 옵션으로 더 받는다.
        options: export_inspection_report()의 옵션 전부 + {site_name, vendor, report_title,
                  confirmer:{company,name}}."""
        return self._export_inspection("pdf", options, "PDF 뷰어")

    def create_customer_report_template(self):
        """'고객사 템플릿 만들기' — 활성 고객사 폴더에 report_template.yaml(설명 주석과 빈 칸)을 만들고(이미 있으면 그대로)
        그 폴더를 연다. 편집은 텍스트 편집기로 한다 — 앱 템플릿과 같은 YAML 이다."""
        target = self._report_target()
        if target is None:
            return {"error": "활성 고객사/정기점검 프로파일이 없습니다."}
        from engine import inspection_evaluator
        path = inspection_evaluator.create_customer_template(target[0])
        log_storage.open_in_file_explorer(str(path.parent))
        return {"ok": True, "path": str(path), **inspection_evaluator.customer_template_status(target[0])}

    def list_inspection_reports(self):
        """reports/의 생성된 보고서 목록(최신순)."""
        target = self._report_target()
        if target is None:
            return []
        return builder.list_reports(*target)

    def open_inspection_report_folder(self):
        """reports/ 폴더를 OS 파일 탐색기로 연다(폴더가 없으면 만들고 나서)."""
        target = self._report_target()
        if target is None:
            return {"error": "활성 고객사/정기점검 프로파일이 없습니다."}
        path = builder.reports_dir(*target)
        log_storage.open_in_file_explorer(str(path))
        return {"ok": True, "path": str(path)}

    def delete_inspection_report(self, name):
        """보고서 파일 삭제 — 경로 탈출을 막기 위해 파일명만 받는다.
        list_reports()가 프로파일 reports/와 회차별 runs/<run_id>/reports/를 함께 보여주므로,
        삭제도 같은 폴더 집합에서 찾아야 한다(목록에는 있는데 지워지지 않는 항목 방지)."""
        target = self._report_target()
        if target is None:
            return {"error": "활성 고객사/정기점검 프로파일이 없습니다."}
        filename = os.path.basename(str(name or ""))
        if not filename.lower().endswith(tuple(f["extension"] for f in builder.report_formats())):
            return {"error": "허용되지 않은 파일입니다."}
        for row in builder.list_reports(*target):
            if os.path.basename(row["path"]) != filename:
                continue
            try:
                os.remove(row["path"])
            except OSError as exc:
                return {"error": str(exc)}
            return {"ok": True}
        return {"error": "파일이 존재하지 않습니다."}
