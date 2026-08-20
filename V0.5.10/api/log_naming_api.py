"""LogNamingApiMixin — 세션 로그 파일명 템플릿 설정/저장/미리보기 API."""
from typing import Dict, Any

from core.log_naming import (
    get_log_naming_config,
    save_log_naming_config,
    preview_log_naming,
)


class LogNamingApiMixin:
    def get_log_naming_settings(self) -> Dict[str, Any]:
        """현재 설정된 로그 파일명 템플릿 및 프리셋 목록 반환."""
        return get_log_naming_config()

    def save_log_naming_settings(self, template: str) -> Dict[str, Any]:
        """새 로그 파일명 템플릿 검증 및 저장."""
        return save_log_naming_config(template)

    def preview_log_naming(self, template: str = None) -> Dict[str, Any]:
        """입력된 템플릿의 실시간 파일명 렌더링 예시 및 파싱 결과 반환."""
        return preview_log_naming(template)
