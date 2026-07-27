"""LogViewerApiMixin — 점검 로그 탭 조합 (파일 브라우징 + AI/규칙기반 분석 실행).

세부 구현은 도메인별로 분리되어 있다:
  - LogFileBrowserApiMixin (api/log_file_browser_api.py): 로그 목록/열람/삭제/폴더열기
  - LogAnalysisRunApiMixin (api/log_analysis_run_api.py): 규칙기반/AI 로그 분석 실행
"""
from api.log_file_browser_api import LogFileBrowserApiMixin
from api.log_analysis_run_api import LogAnalysisRunApiMixin


class LogViewerApiMixin(LogFileBrowserApiMixin, LogAnalysisRunApiMixin):
    pass
