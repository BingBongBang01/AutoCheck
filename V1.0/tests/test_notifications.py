"""알림 채널 — IMPROVEMENT_PLAN 4-4.

예전 알람은 FAIL 이 High 까지만 매겨지는데 Critical 만 보내서 사실상 울리지 않았고, 핸들러는 콘솔·파일뿐이었다
(3-1 에서 퇴역). 새 채널(Teams·Slack·웹훅·메일)을 여기서 고정한다:
  - 웹훅 주소(경로가 곧 토큰)·SMTP 비밀번호는 디스크에 봉인되고 화면에 가지 않는다.
  - 보내기 전에 가린다(기본: 비밀값·IP·MAC·계정 — 장비명은 남긴다).
  - 같은 사건은 정한 시간에 한 번만, 채널마다 시간당 한도(넘친 건수는 다음 메시지에 적는다).
  - 실시간 CRITICAL·무인 점검 실패·수집 실패가 실제로 채널까지 간다. 실패해도 부른 쪽은 멈추지 않는다.
실제 웹훅·메일 서버로는 보내지 않는다(urlopen·smtplib 을 가짜로 바꾼다).
"""
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
from pathlib import Path

import pytest
import yaml

from core import secret_vault as vault
from core.paths import AppPaths
from core.secret_redaction import redact_secrets
from engine import notifications as notify

TEAMS_URL = "https://prod-12.koreacentral.logic.azure.com/workflows/abc/triggers/manual/paths/invoke?api-version=1&sig=TOPSECRETSIG"
SLACK_URL = "https://hooks.slack.com/services/T000/B000/SLACKSECRETTOKEN"
SMTP_PASSWORD = "smtp-pass-1"
LOG = "Core1#show run\nsnmp-server community PUBLICRO ro\n   ip address 10.1.1.10/24\nEthernet1 changed state to down"


@pytest.fixture
def root():
    original = AppPaths._user_data_root
    AppPaths._user_data_root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths.forget_ensured()
    yield AppPaths._user_data_root
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


class Recorder:
    """채널로 실제로 보내는 대신 (채널 이름, 메시지)를 모은다. error 를 주면 그 문구로 실패한다."""

    def __init__(self, error=None):
        self.sent, self.error = [], error

    def __call__(self, channel, message):
        self.sent.append((channel["name"], message))
        return self.error


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def channels(root):
    """Teams·이메일 채널 두 개를 저장한 설정."""
    settings = notify.load_settings()
    notify.apply_channel(settings, {"type": "teams", "name": "운영팀", "webhook_url": TEAMS_URL})
    notify.apply_channel(settings, {"type": "email", "name": "메일", "smtp_host": "smtp.example.com",
                                    "security": "starttls", "username": "ops", "password": SMTP_PASSWORD,
                                    "sender": "autocheck@example.com", "recipients": "a@example.com; b@example.com"})
    notify.save_settings(settings)
    return settings


@pytest.fixture
def recorder(monkeypatch):
    """모듈의 notifier 를 가짜 시계·가짜 전송의 새 것으로 바꾼다(실시간 감시·무인 점검이 이것을 쓴다)."""
    rec, clock = Recorder(), Clock()
    monkeypatch.setattr(notify, "notifier", notify.Notifier(clock=clock, send=rec))
    rec.clock = clock
    return rec


# --------------------------------------------------------------------------- 1. 설정과 비밀값

def test_webhook_urls_and_passwords_are_sealed_and_never_shown(channels):
    text = Path(AppPaths.config_root(), "alarm.yaml").read_text(encoding="utf-8")
    assert [s for s in ("TOPSECRETSIG", "logic.azure.com", SMTP_PASSWORD) if s in text] == []
    assert text.count(vault.PREFIX) == 2
    loaded = notify.load_settings()
    assert [c.get("webhook_url") or c.get("password") for c in loaded["channels"]] == [TEAMS_URL, SMTP_PASSWORD]
    public = notify.public_settings(loaded)
    dumped = json.dumps(public, ensure_ascii=False)
    assert "TOPSECRETSIG" not in dumped and SMTP_PASSWORD not in dumped
    teams, mail = public["channels"]
    assert (teams["has_webhook_url"], teams["webhook_host"]) == (True, "prod-12.koreacentral.logic.azure.com")
    assert mail["has_password"] is True and mail["recipients"] == ["a@example.com", "b@example.com"]


def test_blank_secret_fields_keep_the_stored_value_and_clear_removes_it(channels):
    settings = notify.load_settings()
    teams_id, mail_id = (c["id"] for c in settings["channels"])
    notify.apply_channel(settings, {"id": teams_id, "type": "teams", "name": "운영팀2", "webhook_url": ""})
    assert settings["channels"][0]["webhook_url"] == TEAMS_URL and settings["channels"][0]["name"] == "운영팀2"
    notify.apply_channel(settings, {"id": mail_id, "type": "email", "clear_secrets": ["password"]})
    assert settings["channels"][1]["password"] == "" and settings["channels"][1]["smtp_host"] == "smtp.example.com"
    notify.apply_channel(settings, {"id": teams_id, "type": "slack", "webhook_url": SLACK_URL})
    assert settings["channels"][0]["webhook_url"] == SLACK_URL                  # 종류를 바꾸면 새 주소만


def test_the_startup_sealer_knows_the_alarm_file(root):
    from engine.secret_files import SECRET_FILE_NAMES, seal_secret_file
    assert "alarm.yaml" in SECRET_FILE_NAMES
    path = Path(root, "alarm.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"channels": [{"id": "c1", "type": "slack", "webhook_url": SLACK_URL,
                                                  "password": "pw-2"}]}), encoding="utf-8")
    assert seal_secret_file(path) == 2 and "SLACKSECRETTOKEN" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("fields, message", [
    ({"type": "fax"}, "종류"),
    ({"type": "slack"}, "웹훅 주소"),
    ({"type": "slack", "webhook_url": "http://hooks.example.com/x"}, "https"),
    ({"type": "webhook", "webhook_url": "ftp://relay.example.com"}, "https"),
    ({"type": "email", "sender": "a@example.com", "recipients": "b@example.com"}, "SMTP 서버"),
    ({"type": "email", "smtp_host": "smtp", "sender": "a@example.com", "recipients": "nobody"}, "받는 사람"),
    ({"type": "email", "smtp_host": "smtp", "sender": "", "recipients": "b@example.com"}, "보내는 사람"),
    ({"type": "email", "smtp_host": "smtp", "smtp_port": 70000, "sender": "a@example.com",
      "recipients": "b@example.com"}, "포트"),
    ({"type": "email", "smtp_host": "smtp", "security": "none", "password": "pw-3", "sender": "a@example.com",
      "recipients": "b@example.com"}, "암호화되지 않은"),
])
def test_unusable_channels_are_refused_with_a_reason(root, fields, message):
    with pytest.raises(notify.NotificationError, match=message):
        notify.apply_channel(notify.load_settings(), fields)


def test_a_relay_on_this_pc_may_use_plain_http_and_an_internal_relay_may_skip_tls(root):
    settings = notify.load_settings()
    assert notify.apply_channel(settings, {"type": "webhook", "webhook_url": "http://localhost:8080/hook"})
    relay = notify.apply_channel(settings, {"type": "email", "smtp_host": "relay.corp", "security": "none",
                                            "sender": "a@example.com", "recipients": "b@example.com"})
    assert relay["smtp_port"] == 25


@pytest.mark.parametrize("options, message", [
    ({"mask": "some"}, "가림"), ({"dedupe_minutes": -1}, "중복 억제"), ({"max_per_hour": 0}, "시간당"),
    ({"events": {"realtime_min_severity": "WARNING"}}, "CRITICAL 또는 MAJOR"),
])
def test_bad_options_are_refused(root, options, message):
    with pytest.raises(notify.NotificationError, match=message):
        notify.apply_options(notify.load_settings(), options)


# --------------------------------------------------------------------------- 2. 메시지

def _event(lines=(LOG,)):
    return {"kind": "realtime", "severity": "CRITICAL", "customer": "ABC", "profile": "P1",
            "title": "실시간 감시 경고 1건 — ABC / P1", "lines": list(lines), "devices": ["Core1"]}


@pytest.mark.parametrize("level, hidden, kept", [
    ("secrets", ["PUBLICRO", "10.1.1.10"], ["Core1", "changed state to down"]),
    ("all", ["PUBLICRO", "10.1.1.10", "Core1"], ["changed state to down"]),
    ("none", [], ["PUBLICRO", "10.1.1.10", "Core1"]),
])
def test_messages_are_masked_before_they_leave(level, hidden, kept):
    text = notify.render(_event(), {**notify.DEFAULTS, "mask": level})["text"]
    assert [s for s in hidden if s in text] == [] and [s for s in kept if s not in text] == []


def test_each_channel_gets_its_own_payload():
    message = notify.render(_event(["<b>Ethernet1</b> down & out"]), {**notify.DEFAULTS, "mask": "none"})
    slack = notify.payload("slack", message)["text"]
    assert "&lt;b&gt;Ethernet1&lt;/b&gt; down &amp; out" in slack and slack.startswith("*[AutoCheck] 실시간")
    card = notify.payload("teams", message)
    assert card["type"] == "message"
    content = card["attachments"][0]
    assert content["contentType"] == "application/vnd.microsoft.card.adaptive"
    blocks = content["content"]["body"]
    assert blocks[0]["text"].startswith("[AutoCheck]") and blocks[0]["color"] == "Attention"
    assert any("Ethernet1" in block["text"] for block in blocks[1:])
    generic = notify.payload("webhook", message)
    assert {k: generic[k] for k in ("source", "kind", "severity", "customer")} == {
        "source": "AutoCheck", "kind": "realtime", "severity": "CRITICAL", "customer": "ABC"}


# --------------------------------------------------------------------------- 3. 중복 억제·한도·사건 선택

CRITICAL = {"device": "Core1", "severity": "CRITICAL", "type": "LINK_DOWN", "message": "Ethernet1 DOWN",
            "raw_line": "Ethernet1 changed state to down"}


def test_the_same_realtime_alert_is_sent_once_per_window(channels, recorder):
    assert notify.notifier.notify_realtime([CRITICAL], "ABC", "P1") == 1
    assert notify.notifier.notify_realtime([dict(CRITICAL)], "ABC", "P1") == 0   # 10분 안의 같은 경고
    assert notify.notifier.flush()
    assert [name for name, _ in recorder.sent] == ["운영팀", "메일"]
    assert recorder.sent[0][1]["title"] == "[AutoCheck] 실시간 감시 경고 1건 — ABC / P1"
    recorder.clock.now += 11 * 60
    assert notify.notifier.notify_realtime([CRITICAL], "ABC", "P1") == 1
    assert notify.notifier.flush() and len(recorder.sent) == 4


def test_severity_floor_history_and_switches_decide_what_goes(channels, recorder):
    major = {**CRITICAL, "severity": "MAJOR", "type": "STP_CHANGE"}
    assert notify.notifier.notify_realtime([major, {**CRITICAL, "history": True}]) == 0
    settings = notify.load_settings()
    notify.apply_options(settings, {"events": {"realtime_min_severity": "MAJOR"}})
    notify.save_settings(settings)
    assert notify.notifier.notify_realtime([major]) == 1
    notify.apply_options(settings, {"events": {"realtime": False}})
    notify.save_settings(settings)
    assert notify.notifier.notify_realtime([{**CRITICAL, "type": "OTHER"}]) == 0
    notify.apply_options(settings, {"events": {"realtime": True}, "enabled": False})
    notify.save_settings(settings)
    assert notify.notifier.notify_realtime([{**CRITICAL, "type": "THIRD"}]) == 0
    assert notify.notifier.flush() and len(recorder.sent) == 2                  # MAJOR 한 번 × 채널 둘


def test_the_hourly_cap_drops_and_then_reports_how_many(channels, recorder):
    settings = notify.load_settings()
    notify.apply_options(settings, {"max_per_hour": 2})
    for n in range(3):
        notify.notifier.dispatch({**_event([f"사건 {n}"]), "key": f"k{n}"}, settings)
    per_channel = [m for name, m in recorder.sent if name == "운영팀"]
    assert len(per_channel) == 2
    recorder.clock.now += 3601
    notify.notifier.dispatch({**_event(["사건 3"]), "key": "k3"}, settings)
    latest = [m for name, m in recorder.sent if name == "운영팀"][-1]
    assert "앞서 보내지 못한 알림 1건" in latest["text"]


def test_a_failing_channel_is_reported_not_raised(channels, monkeypatch):
    monkeypatch.setattr(notify, "notifier", notify.Notifier(send=Recorder(error="HTTP 500")))
    assert notify.notifier.dispatch({**_event(), "key": "x"}) == {
        c["id"]: "HTTP 500" for c in notify.load_settings()["channels"]}
    status = notify.public_settings()["channels"][0]["status"]
    assert status["ok"] is False and status["error"] == "HTTP 500"


# --------------------------------------------------------------------------- 4. 실제 보내는 방식

class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"ok"


def test_webhooks_post_json_and_errors_never_carry_the_url(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen.update(url=request.full_url, method=request.get_method(), timeout=timeout,
                    body=json.loads(request.data), headers={k.lower(): v for k, v in request.header_items()})
        return _Response()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    channel = {"type": "slack", "name": "s", "webhook_url": SLACK_URL}
    message = notify.render(_event(["x"]), notify.DEFAULTS)
    assert notify.deliver(channel, message) is None
    assert (seen["url"], seen["method"]) == (SLACK_URL, "POST") and seen["timeout"] == notify.SEND_TIMEOUT_SEC
    assert seen["headers"]["content-type"].startswith("application/json") and "text" in seen["body"]

    def unreachable(request, timeout):
        raise urllib.error.URLError(f"cannot reach {SLACK_URL}")

    monkeypatch.setattr(notify.urllib.request, "urlopen", unreachable)
    error = notify.deliver(channel, message)
    assert "SLACKSECRETTOKEN" not in error and "hooks.slack.com" in error

    def server_error(request, timeout):
        raise urllib.error.HTTPError(SLACK_URL, 500, "x", {}, None)

    monkeypatch.setattr(notify.urllib.request, "urlopen", server_error)
    assert notify.deliver(channel, message) == "HTTP 500"


def test_mail_uses_tls_and_logs_in_only_with_credentials(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            calls.append(("connect", type(self).__name__, host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            calls.append(("starttls",))

        def login(self, user, password):
            calls.append(("login", user))

        def send_message(self, mail):
            calls.append(("send", mail["Subject"], mail["To"]))

    class FakeSMTP_SSL(FakeSMTP):
        pass

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", FakeSMTP_SSL)
    message = notify.render(_event(["x"]), notify.DEFAULTS)
    channel = {"type": "email", "smtp_host": "smtp.example.com", "smtp_port": 587, "security": "starttls",
               "username": "ops", "password": "pw-4", "sender": "a@example.com", "recipients": ["b@example.com"]}
    assert notify.deliver(channel, message) is None
    assert calls == [("connect", "FakeSMTP", "smtp.example.com", 587), ("starttls",), ("login", "ops"),
                     ("send", message["title"], "b@example.com")]
    calls.clear()
    assert notify.deliver({**channel, "security": "ssl", "smtp_port": 465, "username": ""}, message) is None
    assert [c[0] for c in calls] == ["connect", "send"] and calls[0][1] == "FakeSMTP_SSL"


def test_webhook_tokens_are_redacted_from_logs():
    for url, secret in ((SLACK_URL, "SLACKSECRETTOKEN"), (TEAMS_URL, "TOPSECRETSIG"),
                        ("https://corp.webhook.office.com/webhookb2/aaa@bbb/IncomingWebhook/ccc/ddd", "IncomingWebhook")):
        assert secret not in redact_secrets(f"POST {url} 실패")


# --------------------------------------------------------------------------- 5. 사건 출처

@pytest.mark.parametrize("record, kind, severity", [
    ({"status": "failed", "message": "사전점검 실패"}, "scheduled_failed", "CRITICAL"),
    ({"status": "partial", "devices_failed": {"Agg1": "timeout"}}, "collection_failed", "MAJOR"),
    ({"status": "partial", "report_errors": {"pdf": "글꼴 없음"}}, "scheduled_failed", "MAJOR"),
    ({"status": "success"}, None, None), ({"status": "skipped"}, None, None),
])
def test_unattended_results_map_to_events(record, kind, severity):
    event = notify.scheduled_event({"customer": "ABC", "profile": "P1", "schedule_id": "s1", **record})
    assert (event or {}).get("kind") == kind and (event or {}).get("severity") == severity


def test_an_unattended_failure_reaches_the_channels(channels, recorder):
    from engine import scheduled_inspection
    record = scheduled_inspection.run_scheduled_inspection("missing01")
    assert record["status"] == "failed"
    assert [name for name, _ in recorder.sent] == ["운영팀", "메일"]            # 그 자리에서(백그라운드 아님)
    title = recorder.sent[0][1]["title"]
    assert title.startswith("[AutoCheck] 무인 정기점검 실패")


def test_realtime_alerts_reach_the_channels_but_post_inspection_findings_do_not(channels, recorder):
    from engine.realtime_watch_service import RealtimeWatchService

    class Host:
        pushed = []

        def push_js(self, fn_name, *args):
            Host.pushed.append(fn_name)

        def active_names(self):
            return "ABC", "P1"

    service = RealtimeWatchService(Host())
    service.push_alerts([CRITICAL])
    assert notify.notifier.flush()
    assert recorder.sent and recorder.sent[0][1]["title"].endswith("ABC / P1")
    sent = len(recorder.sent)
    service.push_alerts([{**CRITICAL, "type": "LOG_ANALYSIS"}], notify=False)    # apply_external_alerts 경로
    assert notify.notifier.flush() and len(recorder.sent) == sent
    assert Host.pushed == ["onRealtimeDiffAlert", "onRealtimeDiffAlert"]


# --------------------------------------------------------------------------- 6. 화면 API·카드

@pytest.fixture
def api():
    from api.notification_api import NotificationApiMixin

    class Api(NotificationApiMixin):
        pass

    return Api()


def test_the_settings_card_round_trip(root, api, recorder):
    view = api.save_notification_channel({"type": "slack", "name": "알림", "webhook_url": SLACK_URL})
    assert view["ok"] and "SLACKSECRETTOKEN" not in json.dumps(view, ensure_ascii=False)
    channel_id = view["channel_id"]
    assert api.save_notification_settings({"mask": "all", "dedupe_minutes": 5})["mask"] == "all"
    assert api.save_notification_channel({"type": "slack", "webhook_url": "http://x"})["error"]
    result = api.test_notification_channel(channel_id)
    assert result == {"ok": True, "detail": "보냈습니다 — 받는 쪽에서 확인하세요."}
    assert recorder.sent[0][1]["kind"] == "test"
    status = api.get_notification_settings()["channels"][0]["status"]
    assert status["ok"] is True and status["kind"] == "test"
    assert api.remove_notification_channel(channel_id)["channels"] == []
    assert "error" in api.test_notification_channel(channel_id)


JS = Path(__file__).resolve().parent.parent / "web_ui" / "js"
NASTY = "<img src=x onerror=\"alert('x')\">"


def _declaration(name, start):
    lines = (JS / name).read_text(encoding="utf-8").splitlines()
    begin = next(i for i, line in enumerate(lines) if line.startswith(start))
    if lines[begin].rstrip().endswith(";"):
        return lines[begin]
    end = next(i for i in range(begin, len(lines)) if lines[i].rstrip() in ("}", "};"))
    return "\n".join(lines[begin:end + 1])


@pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 동작 검증을 건너뜁니다")
def test_the_settings_card_escapes_everything_it_shows():
    settings = {"enabled": True, "mask": "secrets", "dedupe_minutes": 10, "max_per_hour": 30,
                "events": {"realtime": True, "realtime_min_severity": "CRITICAL", "scheduled_failed": True,
                           "collection_failed": True},
                "channel_types": notify.CHANNEL_TYPES, "mask_levels": notify.MASK_LEVELS,
                "channels": [{"id": "c1", "type": "teams", "name": NASTY, "enabled": True, "has_webhook_url": True,
                              "webhook_host": NASTY, "status": {"ok": False, "at": "2026-10-28T10:00:00", "error": NASTY}},
                             {"id": "c2", "type": "email", "name": "메일", "enabled": False, "smtp_host": NASTY,
                              "recipients": [NASTY], "status": {}}]}
    script = "\n".join([
        _declaration("core.js", "const _ESC_MAP"), _declaration("core.js", "function esc("),
        _declaration("settings-notifications.js", "const NOTIFY_SECURITY"),
        *[_declaration("settings-notifications.js", f"function {name}(") for name in (
            "notifyStatusText", "notifyTarget", "notifySecretPlaceholder", "notifyChannelFormHtml", "notifyCardHtml")],
        f"const s = {json.dumps(settings, ensure_ascii=False)};",
        "console.log(JSON.stringify([notifyCardHtml(s, null), notifyCardHtml(s, 'c2')]));",
    ])
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        path = handle.name
    try:
        done = subprocess.run(["node", path], capture_output=True, timeout=30)
    finally:
        os.unlink(path)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    listing, editing = json.loads(done.stdout.decode("utf-8"))
    assert "<img" not in listing + editing
    assert listing.count("&lt;img") == 5 and "마지막 전송 실패" in listing
    assert 'id="notify-ch-host" value="&lt;img' in editing and "채널 추가" not in editing
