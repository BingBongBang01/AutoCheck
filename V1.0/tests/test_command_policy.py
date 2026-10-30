"""점검 명령 읽기 전용 정책 — IMPROVEMENT_PLAN 1-1.

기본 카탈로그에 `conf` 가 '필수·활성'으로 들어 있었다. 세션 터미널 점검은 카탈로그를 작업자의
SSH 세션에 그대로 입력하므로, 점검이 끝나면 작업자 터미널이 `(config)#` 에 남았다 — 다음 입력이
곧 설정 변경이 되는 상태. 이 파일은 그 경로들이 막혔는지 확인한다:
  - 정책 자체(core/command_policy.py) — 허용 목록 방식
  - 기본 카탈로그(엑셀)에 정책 위반·오타가 없는지
  - 실행 경로 두 곳(카탈로그 → 세션 점검, SSH 수집기)이 위반 명령을 보내지 않는지
  - 카탈로그 API 가 위반 명령을 추가·활성화하지 못하게 하는지
"""
import pytest

from api.catalog_api import CatalogApiMixin
from core.command_policy import blocked_reason
from core.storage_service import PathTarget
from engine import collector
from engine import command_catalog as cc

ALLOWED = [
    "show version", "sh ver", "SHOW clock", "show running-config | grep 'ip route'",
    "show ip bgp | include >", "show interfaces | include rate > 0",
    "terminal length 0", "term len 0", "enable", "en", "enable 15", "dir flash:", "! 주석", "",
]

BLOCKED = [
    "conf", "config", "configure", "configure terminal", "conf t", "configure session s1",
    "copy running-config startup-config", "write memory", "wr", "reload", "reload in 5",
    "delete flash:startup-config", "erase startup-config", "clear counters", "no shutdown",
    "exit", "end", "bash ls", "enable mypassword",
    "show running-config | redirect flash:a.cfg", "show run | tee flash:x", "show run | append flash:x",
    "show running-config > flash:startup-config",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_read_only_commands_are_allowed(command):
    assert blocked_reason(command) is None


@pytest.mark.parametrize("command", BLOCKED)
def test_state_changing_commands_are_blocked(command):
    assert blocked_reason(command)


def test_config_mode_reason_explains_the_risk():
    assert "설정 모드" in blocked_reason("conf")


def test_default_catalog_is_read_only_and_typo_free():
    """기본 카탈로그(엑셀)에 정책 위반 명령도, 한글이 섞인 오타(`show spanning-tree 피무`)도 없어야 한다."""
    catalog = cc._make_default_catalog()
    assert len(catalog) >= 40
    commands = [item["command"] for item in catalog]
    assert [c for c in commands if blocked_reason(c)] == []
    assert [c for c in commands if not c.isascii()] == []
    assert all(item["enabled"] for item in catalog), "'TRUE' 문자열이 활성으로 읽혀야 한다"


def test_split_enabled_commands_skips_policy_violations():
    """v0.5.9 이전 기본 카탈로그를 받은 사용자 파일에는 `conf` 가 켜진 채 남아 있다."""
    catalog = [
        {"id": "e0", "category": "essential", "command": "enable", "enabled": True},
        {"id": "e1", "category": "essential", "command": "conf", "enabled": True},
        {"id": "e2", "category": "essential", "command": "show version", "enabled": True},
        {"id": "e3", "category": "essential", "command": "show clock", "enabled": False},
    ]
    allowed, skipped = cc.split_enabled_commands(catalog)
    assert allowed == ["enable", "show version"]
    assert [command for command, _ in skipped] == ["conf"]
    assert cc.get_enabled_commands(catalog) == allowed


def test_notepad_shows_only_the_commands_that_run():
    """예전 화면이 남긴 꺼 둔 항목·정책에 걸리는 항목은 메모장에 보이지 않는다(점검에서 실행되지 않던 것)."""
    catalog = [{"id": "c0", "command": "reload", "enabled": True},
               {"id": "c1", "command": "show version", "enabled": True},
               {"id": "c2", "command": "show clock", "enabled": False}]
    assert cc.shown_commands(catalog) == ["show version"]
    assert [command for command, _ in cc.hidden_items(catalog)] == ["reload", "show clock"]


def test_excel_enabled_strings_are_parsed():
    """'FALSE' 문자열을 bool() 로 바꾸면 참이 된다 — 꺼 둔 명령이 실행되던 경로."""
    assert cc._parse_enabled("FALSE", default=True) is False
    assert cc._parse_enabled("TRUE", default=False) is True
    assert cc._parse_enabled(float("nan"), default=True) is True
    assert cc._parse_enabled(None, default=False) is False


# --------------------------------------------------------------------------- SSH 수집기


class FakeConnection:
    """netmiko ConnectHandler 대역 — 장비로 무엇이 갔는지만 기록한다."""

    def __init__(self, **kwargs):
        self.sent = []
        self.enable_called = False
        FakeConnection.last = self

    def check_enable_mode(self):
        return False

    def enable(self):
        self.enable_called = True

    def config_mode(self):
        raise AssertionError("점검이 설정 모드에 들어갔다")

    def send_command_timing(self, text, **kwargs):
        self.sent.append(text)
        return "Core1#show version\nArista vEOS\nCore1#"

    def disconnect(self):
        pass


def test_plan_commands_separates_enable_and_blocked():
    wants_enable, send, skipped = collector._plan_commands(
        ["enable", "conf", "terminal length 0", "show version", "configure terminal", "end"])
    assert wants_enable is True
    assert send == ["terminal length 0", "show version"]
    assert [command for command, _ in skipped] == ["conf", "configure terminal", "end"]


def test_collector_never_sends_config_commands(monkeypatch, tmp_path):
    monkeypatch.setattr(collector, "ConnectHandler", FakeConnection)
    events = []
    name, outputs, error = collector.collect_device(
        "Core1", "192.0.2.1", 22, "user", "pw",
        ["enable", "conf", "show version", "configure terminal", "end"],
        PathTarget(path=tmp_path), "Core1.txt",
        on_update=lambda *event: events.append(event), retry_count=1, retry_delay_sec=0)

    assert error is None
    connection = FakeConnection.last
    assert connection.enable_called
    assert connection.sent == ["show version\n!"], "정책을 통과한 명령만 장비로 가야 한다"
    skipped = [detail for _, status, detail in events if status == "명령 건너뜀"]
    assert len(skipped) == 3 and skipped[0].startswith("conf")
    assert (tmp_path / "Core1.txt").read_text(encoding="utf-8").startswith("Core1#show version")


# --------------------------------------------------------------------------- 카탈로그 API


class FakeCatalogApi(CatalogApiMixin):
    def __init__(self, path):
        self.path = path

    def _paths(self):
        return {"commands_catalog": str(self.path)}

    def _load_catalog(self, paths):
        return cc.load_catalog(paths["commands_catalog"])


@pytest.fixture
def api(tmp_path):
    path = tmp_path / "commands_catalog.yaml"
    cc.save_catalog([
        {"id": "essential_0", "category": "essential", "command": "conf", "description": "", "enabled": True},
        {"id": "essential_1", "category": "essential", "command": "show version", "description": "", "enabled": True},
    ], str(path))
    return FakeCatalogApi(path)


def test_api_refuses_text_with_a_blocked_command_and_names_the_line(api):
    result = api.save_catalog_text("show version\n!\nconfigure terminal\n!\n")
    assert result["error"] and [line["line"] for line in result["blocked"]] == [3]
    assert "설정 모드" in result["blocked"][0]["reason"]
    commands = [item["command"] for item in cc.load_catalog(str(api.path))]
    assert commands == ["conf", "show version"], "거부된 글이 저장되면 안 된다"


def test_api_text_hides_an_old_blocked_item_and_saving_drops_it(api):
    shown = api.get_catalog_text()
    assert shown["text"] == "show version\n!\n" and shown["count"] == 1
    assert shown["hidden"][0]["command"] == "conf"
    assert api.save_catalog_text(shown["text"])["ok"]
    assert [item["command"] for item in cc.load_catalog(str(api.path))] == ["show version"]


def test_api_reset_restores_defaults_without_blocked_commands(api):
    result = api.reset_catalog_defaults()
    saved = cc.load_catalog(str(api.path))
    assert result["count"] == len(saved) >= 40
    assert all(item["enabled"] and not blocked_reason(item["command"]) for item in saved)
    assert result["text"].startswith("enable\n!\nterminal length 0\n!\n")
