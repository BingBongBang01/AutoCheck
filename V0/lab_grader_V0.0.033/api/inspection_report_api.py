"""InspectionReportApiMixin — '보고서' 탭. 정기점검 보고서 엑셀 미리보기/생성/목록/삭제.

데이터 조립·판정·엑셀 렌더링은 전부 engine/inspection_report_builder.py와
report/inspection_excel.py에 있고, 이 클래스는 웹 UI에 필요한 형태로 얇게 감싸기만 한다.
활성 고객사/프로파일 이름은 CustomerProfileApiMixin.resolve_active_customer_profile_names()로
얻으므로, 항상 그 mixin과 함께 Api 클래스에 조합되어야 한다.
"""
import os

from engine import log_storage
from engine import inspection_report_builder as builder
from engine.inspection_report_builder import InspectionReportError


class InspectionReportApiMixin:
    def _report_target(self):
        """(고객사명, 프로파일명) — 활성 프로파일이 없으면 None."""
        try:
            self._project()
        except RuntimeError:
            return None
        return self.resolve_active_customer_profile_names()

    def get_inspection_report_context(self):
        """보고서 탭 진입 시 미리보기 데이터 — 어떤 장비가 몇 개 항목으로, 무엇이 확인필요인지.
        엑셀 파일은 만들지 않는다. 실제 값까지 그대로 담아 UI에서 표로 그릴 수 있게 한다."""
        target = self._report_target()
        if target is None:
            return {"error": "활성 고객사/정기점검 프로파일이 없습니다. 워크스페이스에서 먼저 선택하세요."}
        customer, profile = target
        try:
            context = builder.build_context(customer, profile, project_id=self._project())
        except InspectionReportError as exc:
            return {"error": str(exc), "customer": customer, "profile": profile,
                     "filename": builder.build_filename(customer, profile),
                     "reports_dir": str(builder.reports_dir(customer, profile))}
        # template 전체(설정 원문)는 UI에서 쓰지 않으므로 항목 수만 넘긴다 — 브리지 전송량 절약.
        return {
            "customer": customer, "profile": profile,
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
            "original_log_dir": str(builder.original_log_dir(customer, profile)),
            "devices": [
                {k: device[k] for k in ("name", "model", "ip", "serial", "os_version", "role",
                                         "unreachable", "collected_at", "command_count",
                                         "warn_count", "overall_status", "remarks")}
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

    def export_inspection_report(self, options=None):
        """'보고서 생성' 버튼 — data/<고객사>/<프로파일>/reports/ 에 엑셀을 저장한다.
        options: {inspection_date, manager:{company,name,contact}, inspector:{...},
                   devices:[장비명...], filename} — 전부 생략 가능."""
        target = self._report_target()
        if target is None:
            return {"error": "활성 고객사/정기점검 프로파일이 없습니다."}
        customer, profile = target
        options = options or {}
        try:
            result = builder.export_report(
                customer, profile,
                project_id=self._project(),
                filename=options.get("filename") or None,
                inspection_date=options.get("inspection_date") or None,
                manager=options.get("manager") or None,
                inspector=options.get("inspector") or None,
                devices_filter=options.get("devices") or None,
            )
        except InspectionReportError as exc:
            return {"error": str(exc)}
        except OSError as exc:
            # 파일이 엑셀에서 열려 있으면 PermissionError가 난다 — 사용자가 바로 알 수 있게 전달.
            return {"error": f"보고서 파일을 저장할 수 없습니다({exc}). 같은 이름의 파일이 "
                              f"엑셀에서 열려 있으면 닫고 다시 시도하세요."}
        return {"ok": True, **result}

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
        """reports/<name>.xlsx 삭제 — 경로 탈출을 막기 위해 파일명만 받아 reports/ 안에서만 지운다."""
        target = self._report_target()
        if target is None:
            return {"error": "활성 고객사/정기점검 프로파일이 없습니다."}
        directory = builder.reports_dir(*target)
        candidate = (directory / os.path.basename(str(name or ""))).resolve()
        if candidate.parent != directory.resolve() or candidate.suffix.lower() != ".xlsx":
            return {"error": "허용되지 않은 파일입니다."}
        if not candidate.is_file():
            return {"error": "파일이 존재하지 않습니다."}
        try:
            candidate.unlink()
        except OSError as exc:
            return {"error": str(exc)}
        return {"ok": True}
