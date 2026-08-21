"""
AI Service Factory and Manager Module.
Orchestrates AI analysis, prompt routing, and provider fallback.
"""
from typing import Dict, Any, Optional
from core.ai.provider import BaseAIProvider, CloudAIProvider, LocalAIProvider


class AIService:
    """Unified AI Orchestration Service for AutoCheck."""

    def __init__(self):
        self._providers: Dict[str, BaseAIProvider] = {}
        self._default_provider_name: str = "cloud"

    def register_provider(self, name: str, provider: BaseAIProvider):
        """Register an AI provider."""
        self._providers[name] = provider

    def get_provider(self, name: Optional[str] = None) -> BaseAIProvider:
        """Get registered provider by name or fallback to default."""
        target_name = name or self._default_provider_name
        if target_name not in self._providers:
            # Fallback lazy initialization
            if target_name == "local":
                self._providers["local"] = LocalAIProvider()
            else:
                self._providers["cloud"] = CloudAIProvider()
        return self._providers.get(target_name, CloudAIProvider())

    def analyze_log_findings(self, summary_data: Dict[str, Any], provider_name: Optional[str] = None) -> Dict[str, Any]:
        """Analyze log findings and generate actionable recommendations."""
        provider = self.get_provider(provider_name)
        prompt = f"다음은 네트워크 점검 결과 요약 데이터입니다:\n{summary_data}\n분석 및 조치 권고안을 제시해주세요."
        try:
            response_text = provider.generate_text(prompt)
            return {"status": "success", "analysis": response_text}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}


# Global singleton instance
ai_service = AIService()
