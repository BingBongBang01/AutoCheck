"""SettingsApiMixin — 설정 탭 전체 조합.

세부 구현은 도메인별로 분리되어 있다:
  - AiOrderApiMixin (api/ai_order_api.py): AI 제공자 우선순위(드래그 재정렬)
  - LocalAiApiMixin (api/local_ai_api.py): 로컬 AI(Lemonade NPU) 모델/배치 설정
  - CloudAiApiMixin (api/cloud_ai_api.py): 클라우드 AI(Anthropic/Gemini/OpenAI) 키/모델 설정
  - TerminalUiSettingsApiMixin (api/terminal_ui_settings_api.py): 터미널 우클릭 동작 설정
"""
from api.ai_order_api import AiOrderApiMixin
from api.local_ai_api import LocalAiApiMixin
from api.cloud_ai_api import CloudAiApiMixin
from api.terminal_ui_settings_api import TerminalUiSettingsApiMixin


class SettingsApiMixin(AiOrderApiMixin, LocalAiApiMixin, CloudAiApiMixin, TerminalUiSettingsApiMixin):
    pass
