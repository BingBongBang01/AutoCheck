"""TerminalApiMixin — SecureCRT 스타일 멀티 SSH 터미널 브릿지 (조합).

세부 구현은 도메인별로 분리되어 있다:
  - TerminalSessionApiMixin (api/terminal_session_api.py): SSH 세션 접속/출력/입력/종료
  - TerminalInspectionApiMixin (api/terminal_inspection_api.py): 커맨드 카탈로그 점검 실행/중지
"""
from api.terminal_session_api import TerminalSessionApiMixin
from api.terminal_inspection_api import TerminalInspectionApiMixin


class TerminalApiMixin(TerminalSessionApiMixin, TerminalInspectionApiMixin):
    pass
