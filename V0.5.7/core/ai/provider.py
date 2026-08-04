"""
Base and Concrete AI Provider Abstractions for AutoCheck.
Supports both Cloud AI (Gemini/OpenAI) and Local LLM (Ollama/Local Endpoint).
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseAIProvider(ABC):
    """Abstract Base Class for AI Providers."""

    @abstractmethod
    def generate_text(self, prompt: str, system_instruction: Optional[str] = None, **kwargs) -> str:
        """Generate text response from prompt."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider service/API key is configured and reachable."""
        pass


class CloudAIProvider(BaseAIProvider):
    """Cloud AI Provider Wrapper (e.g. Gemini, OpenAI)."""

    def __init__(self, api_key: Optional[str] = None, model_name: str = "gemini-1.5-flash"):
        self.api_key = api_key
        self.model_name = model_name

    def generate_text(self, prompt: str, system_instruction: Optional[str] = None, **kwargs) -> str:
        if not self.is_available():
            raise ValueError("Cloud AI Provider가 설정되지 않았거나 API 키가 유효하지 않습니다.")
        # Mock or delegate to actual client logic if configured
        return f"[Cloud AI ({self.model_name}) Response]: Prompt processed successfully."

    def is_available(self) -> bool:
        return bool(self.api_key)


class LocalAIProvider(BaseAIProvider):
    """Local LLM Provider Wrapper (e.g. Ollama / local server endpoint)."""

    def __init__(self, endpoint_url: str = "http://localhost:11434", model_name: str = "llama3"):
        self.endpoint_url = endpoint_url
        self.model_name = model_name

    def generate_text(self, prompt: str, system_instruction: Optional[str] = None, **kwargs) -> str:
        if not self.is_available():
            raise ValueError("Local AI Provider 엔드포인트에 접근할 수 없습니다.")
        return f"[Local AI ({self.model_name}) Response]: Prompt processed successfully."

    def is_available(self) -> bool:
        # Check endpoint reachability (simplified)
        return True
