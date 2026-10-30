"""외부 AI 전송 미리보기·승인 — IMPROVEMENT_PLAN 4-2(D3: 클라우드는 강제 마스킹과 미리보기 승인을 거친 뒤에만 쓴다).

이 PC 밖으로 나가는 분석은 '보낼 내용'을 먼저 보여 주고, 승인한 바로 그 내용만 보낸다. 승인과 결과는
ai_transmissions.jsonl 에 남는다(내용은 남기지 않고 SHA-256 만). 여기서는 전송 함수를 가로채 실제로 나간 것을
본다. 함께: 같은 곳으로 가는 키끼리의 로테이션, 연결 테스트가 404/5xx 를 실패로 보는지, Anthropic 요청 모양
(현행 모델·API 버전·thinking 을 감안한 max_tokens·대체 모델·거절 처리), 승인 창의 이스케이프.
"""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
from pathlib import Path

import pytest

from ai_analysis import raw_log_analyzer as rla
from ai_analysis import transmission
from core import cloud_providers
from core.app_settings import ai_transmission_log_path
from core.paths import AppPaths
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
  Neighbor         V  AS           MsgRcvd   MsgSent  InQ OutQ  Up/Down State   PfxRcd PfxAcc
  10.0.12.2        4  65002              0         0    0    0 00:10:01 Active
Core1#show logging
Oct 28 10:00:01 Core1 Ebra: %LINEPROTO-5-UPDOWN: Line protocol on Interface Ethernet1, changed state to down
"""
# 비밀값·주소, 이 로그의 장비명(Core1), 설명에 이웃으로 적힌 장비 목록의 장비명(Agg1).
LEAKS = ["PUBLICRO", "070C285F4D06", "10.1.1.10", "10.0.12.1", "10.0.12.2", "Core1", "Agg1"]
LOG_NAME = "20261028_101500_raw_Core1.txt"
KEY_A = "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def root():
    original = AppPaths._user_data_root
    AppPaths._user_data_root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths.forget_ensured()
    yield AppPaths._user_data_root
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def _entry(entry_id, name, key, provider="anthropic", model="", enabled=True, **extra):
    return {"id": entry_id, "name": name, "provider": provider, "model": model, "api_key": key,
            "enabled": enabled, **extra}


@pytest.fixture
def api(root, tmp_path, monkeypatch):
    from api.log_analysis_run_api import LogAnalysisRunApiMixin
    from engine.profile_manager import profile_manager

    run = tmp_path / "run"
    (run / "original").mkdir(parents=True)
    (run / "problem").mkdir()
    (run / "original" / LOG_NAME).write_text(LOG, encoding="utf-8")
    monkeypatch.setattr(profile_manager, "is_virtual", lambda *names: False)

    class Stub(LogAnalysisRunApiMixin):
        def __init__(self):
            self.run = run
            # 모델을 비워 둔 항목 — 제공자 표의 기본 모델(현행 Claude)을 쓴다.
            self.ai_cfg = {"providers": [{"type": "cloud_apis", "entries": [_entry("a1", "main", KEY_A)]}]}
            self.local_cfg = {"endpoint": "http://localhost:13305", "model": "qwen2.5-7b"}
            # 활성 프로파일의 장비 목록 — None 이면 읽지 못하는 경우.
            self.devices = [{"name": "Core1"}, {"name": "Agg1"}, {"name": "12"}, {"name": "R"}]

        def _paths(self):
            return {"device_inventory": "device_inventory.yaml"}

        def _load_inventory(self, paths):
            if self.devices is None:
                raise OSError("device_inventory.yaml 을 읽을 수 없다")
            return {"defaults": {}, "devices": [dict(d) for d in self.devices]}

        def _active_profile_log_paths(self, create=False, run_id=None):
            return {"root": str(run), "run_id": "20261028_101500", "original": str(run / "original"),
                    "problem": str(run / "problem")}

        def _active_names(self):
            return "고객사", "2026-10 정기점검"

        def _load_ai_config(self):
            return copy.deepcopy(self.ai_cfg)

        def get_local_ai_config(self):
            return dict(self.local_cfg)

        def _ensure_lemonade_model_loaded(self, endpoint, model):
            return {"ok": True}

    return Stub()


@pytest.fixture
def sent(monkeypatch):
    """네 가지 전송 함수를 가로채 실제로 나간 (프롬프트, 항목 이름)을 모은다."""
    calls = []

    def capture(prompt, api_cfg):
        calls.append((prompt, api_cfg.get("name") or api_cfg.get("model")))
        return "ok"

    for name in ("_call_api_raw_text", "_call_gemini_raw_text", "_call_openai_raw_text",
                 "_call_local_npu_raw_text"):
        monkeypatch.setattr(rla, name, capture)
    monkeypatch.setattr(rla.time, "sleep", lambda seconds: None)
    return calls


def _finish(api, mode):
    deadline = time.time() + 10
    while api._jobs().is_running(mode):
        assert time.time() < deadline, "분석이 끝나지 않았다"
        time.sleep(0.01)
    status = api._jobs().status(mode)
    assert status["status"] == "done", status
    return status


def _records():
    path = Path(ai_transmission_log_path())
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _http_error(code, body=""):
    exc = urllib.error.HTTPError("https://api.example/v1", code, "x", {}, None)
    exc.body_text = body
    return exc


# --------------------------------------------------------------------------- 1. 미리보기·승인·이력

def test_the_preview_shows_what_leaves_the_pc_and_sends_nothing(api, sent):
    preview = api.preview_ai_transmission("cloud")
    assert preview["needs_approval"] is True and sent == []
    dest = preview["destination"]
    assert (dest["label"], dest["host"], dest["model"]) == ("Anthropic (Claude)", "api.anthropic.com", "claude-opus-5-5")
    assert preview["totals"] == {"files": 1, "requests": 1, "chars": preview["files"][0]["chars"]}
    assert preview["files"][0]["unmasked"] == [] and "IP Address" in preview["masking"]
    # 장비 목록 4대 중 '12'·'R' 은 가릴 이름에서 빠진다(숫자뿐·한 글자).
    assert "Device Name (장비 목록 2개·파일명·프롬프트)" in preview["masking"] and preview["warnings"] == []
    assert KEY_A not in json.dumps(preview, ensure_ascii=False)          # 키 값은 화면에 가지 않는다

    text = api.get_ai_transmission_part(preview["preview_id"], 0)["text"]
    assert [value for value in LEAKS if value in text] == []
    assert "show ip bgp summary" in text and "Active" in text            # 분석에 필요한 내용은 남는다
    assert "error" in api.get_ai_transmission_part("0" * 32, 0)


def test_every_inventory_name_is_masked_in_one_pass_but_not_numbers_or_single_letters():
    devices = [{"name": "Agg1"}, {"name": "Agg1-mgmt"}, {"name": "SW"}, {"name": "12"}, {"name": "R"},
               {"name": " Agg1 "}, {"name": ""}, {}]
    names = transmission.neighbor_names(devices)
    assert names == ("Agg1", "Agg1-mgmt", "SW")
    text = "Et1 to Agg1-mgmt, Et2 to agg1, sw uplink\nR    10.0.0.0/8 via Agg1\n12 packets input\n"
    masked = mask_for_external_ai(text, names)
    assert "Agg1" not in masked and "agg1" not in masked and "sw uplink" not in masked
    assert "to *********," in masked                                      # 긴 이름이 통째로 가려진다
    assert "12 packets input" in masked and masked.splitlines()[1].startswith("R ")


def test_the_preview_warns_when_the_inventory_cannot_be_read(api, sent):
    api.devices = None
    preview = api.preview_ai_transmission("cloud")
    assert len(preview["warnings"]) == 1 and "장비 목록을 읽지 못해" in preview["warnings"][0]
    assert "Device Name (장비 목록 0개·파일명·프롬프트)" in preview["masking"]
    text = api.get_ai_transmission_part(preview["preview_id"], 0)["text"]
    assert "Agg1" in text and "Core1" not in text                         # 이 로그의 장비명은 그래도 가린다


def test_cloud_analysis_does_not_start_without_an_approved_preview(api, sent):
    assert "미리보기" in api.start_ai_log_analysis("cloud")["error"]
    assert "만료" in api.start_ai_log_analysis("cloud", "0" * 32)["error"]
    assert sent == [] and not api._jobs().is_running("cloud") and _records() == []


def test_approval_sends_exactly_the_previewed_payload_and_is_recorded(api, sent):
    preview = api.preview_ai_transmission("cloud")
    approved_text = api.get_ai_transmission_part(preview["preview_id"], 0)["text"]
    # 미리보기를 본 뒤 로그가 바뀌어도, 승인하지 않은 내용은 나가지 않는다.
    (api.run / "original" / LOG_NAME).write_text(
        LOG + "snmp-server community NEWSECRET ro\nEthernet9 changed state to down\n", encoding="utf-8")

    assert api.start_ai_log_analysis("cloud", preview["preview_id"]) == {"ok": True}
    _finish(api, "cloud")
    prompts = [prompt for prompt, _ in sent]
    assert prompts == [approved_text] and "NEWSECRET" not in prompts[0]
    assert (api.run / "problem" / "CloudAI_20261028_101500_Core1_problems.txt").read_text(encoding="utf-8") == "ok"

    approved, finished = _records()
    assert approved["event"] == "approved" and approved["preview_id"] == preview["preview_id"]
    assert (approved["provider"], approved["host"], approved["model"]) == (
        "anthropic", "api.anthropic.com", "claude-opus-5-5")
    assert (approved["customer"], approved["profile"], approved["run_id"]) == (
        "고객사", "2026-10 정기점검", "20261028_101500")
    assert approved["files"] == [LOG_NAME] and approved["keys"] == ["main"] and "user" in approved
    assert (approved["requests"], approved["chars"]) == (1, len(prompts[0]))
    assert approved["sha256"] == hashlib.sha256(prompts[0].encode("utf-8") + b"\0").hexdigest()
    assert finished == {**finished, "event": "finished", "preview_id": preview["preview_id"],
                        "requests_sent": 1, "parts_failed": 0, "keys_used": ["main"]}
    raw = Path(ai_transmission_log_path()).read_text(encoding="utf-8")
    assert "show ip bgp" not in raw and KEY_A not in raw                  # 내용·키는 이력에 남기지 않는다

    # 같은 승인으로 두 번 보내지 않는다.
    assert "만료" in api.start_ai_log_analysis("cloud", preview["preview_id"])["error"]


def test_changing_the_destination_after_the_preview_voids_it(api, sent):
    preview = api.preview_ai_transmission("cloud")
    api.ai_cfg["providers"][0]["entries"][0]["model"] = "claude-sonnet-5-5"
    assert "설정" in api.start_ai_log_analysis("cloud", preview["preview_id"])["error"]
    assert sent == [] and _records() == []


def test_previews_expire_are_single_use_and_replaced_per_mode():
    now = [0.0]
    store = transmission.PreviewStore(ttl=10, clock=lambda: now[0])
    first = store.put("cloud", {"n": 1})
    second = store.put("cloud", {"n": 2})                    # 새 미리보기가 앞의 것을 대신한다
    local = store.put("local", {"n": 3})
    assert store.get(first) is None and store.get(second) == {"n": 2}
    assert store.take(second, "local") is None               # 다른 모드로는 승인할 수 없다
    assert store.take(second, "cloud") == {"n": 2} and store.take(second, "cloud") is None
    now[0] = 11
    assert store.get(local) is None and store.take(local, "local") is None


def test_local_ai_on_this_pc_starts_without_approval(api, sent):
    assert api.preview_ai_transmission("local") == {"needs_approval": False}
    assert api.start_ai_log_analysis("local") == {"ok": True}
    _finish(api, "local")
    assert "PUBLICRO" in "".join(prompt for prompt, _ in sent)           # 이 PC 안이라 원문 그대로
    assert _records() == []


def test_local_ai_on_another_host_needs_approval_and_is_masked(api, sent):
    api.local_cfg["endpoint"] = "http://192.0.2.50:13305"
    assert "미리보기" in api.start_ai_log_analysis("local")["error"]
    preview = api.preview_ai_transmission("local")
    assert preview["destination"]["host"] == "192.0.2.50"
    assert api.start_ai_log_analysis("local", preview["preview_id"]) == {"ok": True}
    _finish(api, "local")
    payload = "".join(prompt for prompt, _ in sent)
    assert payload and [value for value in LEAKS if value in payload] == []
    assert [r["event"] for r in _records()] == ["approved", "finished"]


def test_content_built_for_this_pc_cannot_be_sent_outside(sent):
    local_only = rla.build_prompts(LOG, "local", {"endpoint": "http://localhost:13305"})
    assert local_only.masked is False
    with pytest.raises(ValueError):
        rla.send_prompts(local_only, "cloud", [{"provider": "anthropic", "api_key": "k"}])
    with pytest.raises(TypeError):
        rla.send_prompts(("raw prompt",), "cloud", [{"provider": "anthropic", "api_key": "k"}])
    assert sent == []


# --------------------------------------------------------------------------- 2. 키 로테이션

def test_only_keys_for_the_same_destination_are_rotated():
    cfg = {"providers": [{"type": "cloud_apis", "entries": [
        _entry("a", "first", "k1"),
        _entry("o", "openai", "k2", provider="openai"),
        _entry("b", "second", "k3", model="claude-sonnet-5-5"),
        _entry("c", "off", "k4", enabled=False),
        _entry("d", "no key", ""),
        _entry("x", "gateway", "k5", endpoint="https://llm.example.com/v1/messages"),
    ]}]}
    ring = transmission.cloud_key_ring(cfg)
    assert [e["id"] for e in ring] == ["a", "b"]                         # 다른 제공자·다른 주소로는 넘어가지 않는다
    dest = transmission.destination("cloud", ring)
    assert dest["keys"] == [{"name": "first", "model": "claude-opus-5-5"},
                            {"name": "second", "model": "claude-sonnet-5-5"}]


def test_a_rejected_key_is_dropped_and_a_rate_limited_key_goes_to_the_back(monkeypatch):
    outcomes = {"A": [401], "B": [429], "C": [None, None, None]}
    calls = []

    def fake(prompt, api_cfg):
        calls.append(api_cfg["name"])
        code = outcomes[api_cfg["name"]].pop(0)
        if code:
            raise _http_error(code)
        return f"ok-{api_cfg['name']}"

    monkeypatch.setattr(rla, "_call_api_raw_text", fake)
    monkeypatch.setattr(rla.time, "sleep", lambda seconds: None)
    ring = rla.KeyRing([{"name": n, "provider": "anthropic", "api_key": n} for n in "ABC"], "cloud")
    first = rla.send_prompts(rla.PreparedRequests(("p1", "p2"), True), "cloud", ring)
    second = rla.send_prompts(rla.PreparedRequests(("p3",), True), "cloud", ring)   # 다음 파일도 같은 KeyRing
    assert calls == ["A", "B", "C", "C", "C"]
    assert first.texts == ["ok-C", "ok-C"] and second.texts == ["ok-C"]
    assert (first.sent, first.failed, first.keys_used) == (2, 0, ["C"])


def test_when_every_key_is_rejected_the_rest_is_not_sent(monkeypatch):
    calls = []

    def fake(prompt, api_cfg):
        calls.append(api_cfg["name"])
        raise _http_error(401 if api_cfg["name"] == "A" else 403)

    monkeypatch.setattr(rla, "_call_api_raw_text", fake)
    ring = rla.KeyRing([{"name": "A", "api_key": "a"}, {"name": "B", "api_key": "b"}], "cloud")
    result = rla.send_prompts(rla.PreparedRequests(("p1", "p2"), True), "cloud", ring)
    assert calls == ["A", "B"]
    assert "인증 실패(HTTP 403)" in result.texts[0] and "보내지 않았습니다" in result.texts[1]
    assert (result.sent, result.failed) == (0, 2)


def test_server_errors_are_retried_then_reported(monkeypatch):
    calls = []

    def fake(prompt, api_cfg):
        calls.append(prompt)
        raise _http_error(529)

    monkeypatch.setattr(rla, "_call_api_raw_text", fake)
    monkeypatch.setattr(rla.time, "sleep", lambda seconds: None)
    out = rla.analyze_raw_log_text("show version", "cloud", {"provider": "anthropic", "api_key": "k"})
    assert len(calls) == 3 and "HTTP 529" in out and "파트 1" in out


# --------------------------------------------------------------------------- 3. Anthropic 요청 모양

class _Response:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


@pytest.fixture
def wire(monkeypatch):
    """Anthropic 호출이 만드는 요청(주소·본문·헤더)을 모으고, 정해 둔 응답을 차례로 돌려준다."""
    seen, replies = [], []

    def fake(req, timeout, label):
        seen.append({"url": req.full_url, "body": json.loads(req.data), "timeout": timeout,
                     "headers": {k.lower(): v for k, v in req.header_items()}})
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _Response(reply)

    monkeypatch.setattr(rla, "_urlopen_with_context", fake)
    monkeypatch.setattr(rla.time, "sleep", lambda seconds: None)
    return seen, replies


TEXT_REPLY = {"stop_reason": "end_turn", "content": [
    {"type": "thinking", "thinking": "", "signature": "sig"},                # 현행 모델은 생각 블록이 앞에 온다
    {"type": "text", "text": "Ethernet1 down"}]}


def test_anthropic_requests_use_the_current_model_and_api_version(wire):
    seen, replies = wire
    replies.append(TEXT_REPLY)
    assert rla._call_api_raw_text("hi", {"api_key": "k", "max_tokens": 1500}) == "Ethernet1 down"
    request = seen[0]
    assert request["url"] == "https://api.anthropic.com/v1/messages"
    assert request["body"]["model"] == "claude-opus-5-5"
    # 예전 기본값 1500 은 생각하는 데 다 쓰일 수 있다 — 하한까지 올려서 보낸다.
    assert request["body"]["max_tokens"] == 16000 and request["timeout"] >= 300
    assert request["headers"]["anthropic-version"] == "2026-10-21"
    assert request["body"]["fallbacks"] == "default"
    assert request["headers"]["anthropic-beta"] == cloud_providers.ANTHROPIC_FALLBACK_BETA
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(request["body"])


def test_larger_limits_are_kept_and_fallback_goes_only_to_models_that_take_it(wire):
    seen, replies = wire
    replies.append(TEXT_REPLY)
    rla._call_api_raw_text("hi", {"api_key": "k", "model": "claude-haiku-5-5", "max_tokens": 32000})
    assert seen[0]["body"]["max_tokens"] == 32000
    assert "fallbacks" not in seen[0]["body"] and "anthropic-beta" not in seen[0]["headers"]


def test_an_account_without_the_fallback_beta_still_gets_an_answer(wire):
    seen, replies = wire
    replies += [_http_error(400, "Unexpected value(s) `server-side-fallback-2026-07-01` for the `anthropic-beta` header."),
                TEXT_REPLY]
    assert rla._call_api_raw_text("hi", {"api_key": "k"}) == "Ethernet1 down"
    assert "fallbacks" in seen[0]["body"] and "fallbacks" not in seen[1]["body"]
    assert "anthropic-beta" not in seen[1]["headers"]


def test_other_bad_requests_are_not_retried(wire):
    seen, replies = wire
    replies.append(_http_error(400, "max_tokens: 999999 > 128000"))
    with pytest.raises(urllib.error.HTTPError):
        rla._call_api_raw_text("hi", {"api_key": "k"})
    assert len(seen) == 1


def test_refusals_and_cut_off_answers_are_visible_in_the_result(wire):
    seen, replies = wire
    replies.append({"stop_reason": "refusal", "stop_details": {"type": "refusal", "category": "cyber"}, "content": []})
    out = rla.analyze_raw_log_text("show version", "cloud", {"provider": "anthropic", "api_key": "k"})
    assert out.startswith("[AI 분석 거절 - 파트 1]") and "cyber" in out
    assert len(seen) == 1                                                  # 같은 내용은 다시 보내지 않는다
    replies.append({"stop_reason": "max_tokens", "content": [{"type": "thinking", "thinking": "", "signature": "s"}]})
    out = rla.analyze_raw_log_text("show version", "cloud", {"provider": "anthropic", "api_key": "k"})
    assert "출력 한도(max_tokens=16000)" in out


# --------------------------------------------------------------------------- 4. 연결 테스트·설정 화면

@pytest.fixture
def cloud_api():
    from api.cloud_ai_api import CloudAiApiMixin

    class Stub(CloudAiApiMixin):
        MODEL_PARAM_DEFAULTS = {}

        def __init__(self):
            self.cfg = {"providers": [{"type": "cloud_apis", "entries": [
                _entry("a1", "main", KEY_A, max_tokens=1500),
                _entry("o1", "gpt", "sk-openai", provider="openai", model="gpt-4o-mini", max_tokens=1000)]}]}

        def _load_ai_config(self):
            return copy.deepcopy(self.cfg)

        def _save_ai_config(self, cfg):
            self.cfg = copy.deepcopy(cfg)

    return Stub()


@pytest.mark.parametrize("code, warn", [(401, False), (403, False), (404, False), (429, False),
                                        (500, False), (529, False), (405, True)])
def test_the_connection_test_passes_only_on_success(cloud_api, monkeypatch, code, warn):
    seen = {}

    def fake_urlopen(req, timeout):
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = cloud_api.test_cloud_api("a1")
    assert result["ok"] is False and bool(result.get("warn")) is warn and str(code) in result["detail"]
    assert seen["headers"]["anthropic-version"] == cloud_providers.ANTHROPIC_VERSION


def test_the_connection_test_passes_on_2xx(cloud_api, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: _Response({"data": []}))
    assert cloud_api.test_cloud_api("a1") == {"ok": True, "detail": "인증 성공"}


def test_small_max_tokens_are_raised_for_claude_only(cloud_api):
    shown = {e["id"]: e["max_tokens"] for e in cloud_api.get_cloud_apis()["entries"]}
    assert shown == {"a1": 16000, "o1": 1000}
    cloud_api.update_cloud_api("a1", {"max_tokens": 2000})
    cloud_api.update_cloud_api("o1", {"max_tokens": 2000})
    stored = {e["id"]: e["max_tokens"] for e in cloud_api.cfg["providers"][0]["entries"]}
    assert stored == {"a1": 16000, "o1": 2000}
    assert cloud_providers.default_model("anthropic") == "claude-opus-5-5"
    # 예전 세대 모델도 목록에 남아 있어야 저장된 선택이 '직접입력'으로 바뀌어 보이지 않는다.
    assert {"claude-sonnet-4-6", "claude-opus-5", "claude-haiku-4-5"} <= set(cloud_providers.models_of("anthropic"))


# --------------------------------------------------------------------------- 5. 승인 창

JS_DIR = Path(__file__).resolve().parent.parent / "web_ui" / "js"
NASTY = "<img src=x onerror=\"alert('x')\">"


def _declaration(name, start):
    lines = (JS_DIR / name).read_text(encoding="utf-8").splitlines()
    begin = next(i for i, line in enumerate(lines) if line.startswith(start))
    if lines[begin].rstrip().endswith(";"):
        return lines[begin]
    end = next(i for i in range(begin, len(lines)) if lines[i].rstrip() == "}")
    return "\n".join(lines[begin:end + 1])


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 동작 검증을 건너뜁니다")
def test_the_approval_dialog_escapes_everything_it_shows():
    preview = {
        "mode": "cloud", "preview_id": "p1", "masking": ["IP Address"],
        "destination": {"label": NASTY, "host": "api.anthropic.com", "model": "claude-opus-5-5", "fallback": True,
                        "keys": [{"name": NASTY, "model": "m1"}, {"name": "second", "model": "m2"}]},
        "files": [{"index": 0, "source": NASTY, "requests": 2, "chars": 12345, "unmasked": [NASTY]}],
        "skipped": [NASTY], "warnings": [NASTY],
        "totals": {"files": 1, "requests": 2, "chars": 12345}, "expires_in_sec": 600,
    }
    script = "\n".join([
        _declaration("core.js", "const _ESC_MAP"), _declaration("core.js", "function esc("),
        _declaration("collection-log-analysis.js", "function aiTransmissionHtml("),
        f"console.log(JSON.stringify(aiTransmissionHtml({json.dumps(preview)})));",
    ])
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        path = handle.name
    try:
        done = subprocess.run(["node", path], capture_output=True, timeout=30)
    finally:
        os.unlink(path)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    html = json.loads(done.stdout.decode("utf-8"))
    assert "<img" not in html and html.count("&lt;img") == 6
    assert "1. &lt;img" in html and "2. second(m2)" in html and "data-ai-tx-approve" in html
