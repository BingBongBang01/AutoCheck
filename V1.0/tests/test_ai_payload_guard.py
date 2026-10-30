"""외부 AI 전송 보호 — IMPROVEMENT_PLAN 1-2.

원본로그 AI 분석은 '마스킹 없이' 클라우드로 보냈다(ai_analysis/raw_log_analyzer.py docstring 에 그렇게
적혀 있었다). running-config 구간의 커뮤니티·키·IP·호스트명이 외부 제공자로 그대로 나갔다. 또 Gemini 는
키를 URL 쿼리에 실었고, 호출이 실패하면 그 URL 을 print 했으며, app_logger 가 print 를 로그 파일과
UI 로그로 캡처했다 — 429 한 번에 API 키가 '전체 로그 내보내기'로 나가는 경로였다.

여기서는 실제로 '무엇이 나가는가'를 본다: 전송 함수를 가로채 프롬프트를 받아 본다.
"""
import urllib.error

import pytest

from ai_analysis import raw_log_analyzer as rla
from core import app_logger, cloud_providers
from core.secret_redaction import redact_secrets
from engine.log_masking import mask_for_external_ai

LOG = """\
Core1#show running-config
hostname Core1
snmp-server community PUBLICRO ro
tacacs-server host 10.1.1.10 key 7 070C285F4D06
interface Ethernet1
   description to Agg1
   ip address 10.0.12.1/30
Core1#show ip bgp summary
  10.0.12.2  4 65002  0  0  Active
"""

LEAKS = ["PUBLICRO", "070C285F4D06", "10.1.1.10", "10.0.12.1", "10.0.12.2", "Core1", "Agg1"]


def test_external_payload_hides_secrets_addresses_and_names():
    masked = mask_for_external_ai(LOG, device_names=("Agg1",))
    assert [value for value in LEAKS if value in masked] == []
    # 분석에 필요한 내용(명령·상태)은 남아야 한다 — 다 가리면 AI 분석이 의미가 없다.
    assert "show ip bgp summary" in masked and "Active" in masked


@pytest.fixture
def sent(monkeypatch):
    """네 가지 전송 함수를 가로채 실제로 나간 프롬프트를 모은다."""
    prompts = []

    def capture(prompt, api_cfg):
        prompts.append(prompt)
        return "ok"

    for name in ("_call_api_raw_text", "_call_gemini_raw_text", "_call_openai_raw_text",
                 "_call_local_npu_raw_text"):
        monkeypatch.setattr(rla, name, capture)
    monkeypatch.setattr(rla.time, "sleep", lambda seconds: None)
    return prompts


@pytest.mark.parametrize("provider", ["anthropic", "gemini", "openai"])
def test_cloud_requests_are_always_masked(sent, provider):
    rla.analyze_raw_log_text(LOG, "cloud", {"provider": provider, "api_key": "k"}, device_names=("Agg1",))
    payload = "".join(sent)
    assert [value for value in LEAKS if value in payload] == []


def test_local_ai_on_this_pc_gets_the_original(sent):
    """로컬 NPU(이 PC)는 데이터가 밖으로 나가지 않으므로 원문 그대로 — 분석 품질을 깎지 않는다."""
    rla.analyze_raw_log_text(LOG, "local", {"endpoint": "http://localhost:13305", "model": "qwen"})
    assert "PUBLICRO" in "".join(sent)


def test_local_ai_pointing_to_another_host_is_masked(sent):
    """'로컬' 설정이라도 다른 호스트를 가리키면 이 PC 밖으로 나가는 것이다."""
    rla.analyze_raw_log_text(LOG, "local", {"endpoint": "http://192.0.2.50:13305", "model": "qwen"})
    assert "PUBLICRO" not in "".join(sent)


def test_gemini_key_goes_in_header_not_url(monkeypatch):
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}'

    def fake_urlopen(req, timeout, label):
        seen["url"], seen["headers"] = req.full_url, dict(req.header_items())
        return Response()

    monkeypatch.setattr(rla, "_urlopen_with_context", fake_urlopen)
    assert rla._call_gemini_raw_text("hi", {"api_key": "AIzaSECRET", "model": "gemini-x"}) == "ok"
    assert "AIzaSECRET" not in seen["url"]
    assert {k.lower(): v for k, v in seen["headers"].items()}["x-goog-api-key"] == "AIzaSECRET"


def test_rate_limited_part_is_reported_not_silently_dropped(monkeypatch):
    def always_429(prompt, api_cfg):
        raise urllib.error.HTTPError("https://api.example/v1", 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(rla, "_call_api_raw_text", always_429)
    monkeypatch.setattr(rla.time, "sleep", lambda seconds: None)
    result = rla.analyze_raw_log_text("show version", "cloud", {"provider": "anthropic"})
    assert "429" in result and "파트 1" in result


@pytest.mark.parametrize("text, secret", [
    ("호출 실패: https://generativelanguage.googleapis.com/v1beta/models/x:generateContent"
     "?key=AIzaSyA1234567890abcdefghijklmnopqrstu -> HTTP 429", "AIzaSyA1234567890abcdefghijklmnopqrstu"),
    ("Authorization: Bearer sk-ant-api03-abcdefghijklmnopqrstuvwxyz", "sk-ant-api03-abcdefghijklmnopqrstuvwxyz"),
    ("headers={'x-api-key': 'abc123secretvalue'}", "abc123secretvalue"),
    ('{"api_key": "plainsecret999"}', "plainsecret999"),
    ("leaked nvapi-ABCDEFGHIJKLMNOPQRSTUVWX here", "nvapi-ABCDEFGHIJKLMNOPQRSTUVWX"),
])
def test_redact_secrets(text, secret):
    assert secret not in redact_secrets(text)


def test_redact_keeps_ordinary_text():
    text = "[AI 분석] 429 Too Many Requests. 22초 대기 후 재시도"
    assert redact_secrets(text) == text


def test_log_buffer_never_stores_api_keys():
    """print 캡처·log_event 어느 쪽으로 들어와도 파일·UI 로그에 키가 남지 않는다."""
    key = "AIzaSyA1234567890abcdefghijklmnopqrstu"
    line = app_logger.log_event(f"요청 실패 https://x.example/v1?key={key}", source="test", level="ERROR")
    assert key not in line
    assert all(key not in recent for recent in app_logger.get_recent_logs(limit=20)["lines"])


@pytest.mark.parametrize("endpoint, allowed", [
    ("https://llm.example.com/v1/chat/completions", True),
    ("http://localhost:8080/v1/chat/completions", True),
    ("http://127.0.0.1:8080/v1", True),
    ("http://llm.example.com/v1", False),
    ("ftp://llm.example.com/v1", False),
    ("llm.example.com/v1", False),
])
def test_custom_endpoint_requires_https(endpoint, allowed):
    assert (cloud_providers.endpoint_problem(endpoint) is None) is allowed
