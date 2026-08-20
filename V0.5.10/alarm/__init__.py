"""
plugins.vendors/report.reporters와 동일한 패턴 — 이 패키지를 import하면
등록된 AlarmHandler들이 자동으로 로드된다.
"""
from alarm import console_handler, file_handler  # noqa: F401 (import 자체가 register() 부작용을 일으킴)
