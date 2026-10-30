"""LogViewerApiMixin — 점검 로그 탭 조합 (파일 브라우징 + AI/규칙기반 분석 실행 + 실시간 감시).

세부 구현은 도메인별로 분리되어 있다:
  - LogFileBrowserApiMixin (api/log_file_browser_api.py): 로그 목록/열람/삭제/폴더열기
  - LogAnalysisRunApiMixin (api/log_analysis_run_api.py): 규칙기반/AI 로그 분석 실행
  - RealtimeWatchApiMixin (api/realtime_watch_api.py): 실시간 감시 — 예전엔 분석 실행 믹스인 안에 있었다.
    조합 순서(MRO)에서 같은 자리를 지키도록 분석 실행 바로 뒤에 둔다.
"""
from api.log_file_browser_api import LogFileBrowserApiMixin
from api.log_analysis_run_api import LogAnalysisRunApiMixin
from api.realtime_watch_api import RealtimeWatchApiMixin


class LogViewerApiMixin(LogFileBrowserApiMixin, LogAnalysisRunApiMixin, RealtimeWatchApiMixin):
    pass
