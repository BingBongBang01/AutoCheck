"""커맨드 카탈로그를 메모장처럼 — 한 줄에 명령 하나, 명령 다음 줄에 '!'(세션 점검이 명령 사이에 넣어 보내는 모양).

예전 화면은 행마다 체크박스·카테고리·설명을 고치고 Excel 로 내보냈다. 이제 글 하나를 고치고 .txt 로 내보내고
불러온다(예전 Excel 도 불러온다). 장비 설정을 바꿀 수 있는 명령이 있으면 저장하지 않는다.
"""
import openpyxl
import pytest

from api import window_ref
from api.catalog_api import CatalogApiMixin
from engine import command_catalog as cc

EXAMPLE = "enable\n!\nterminal length 0\n!\nshow version\n!\nshow clock\n!\nshow ntp status\n!\n"


def test_text_is_one_command_per_line_each_followed_by_a_bang_line():
    commands = ["enable", "terminal length 0", "show version", "show clock", "show ntp status"]
    assert cc.commands_to_text(commands) == EXAMPLE
    assert [command for _number, command in cc.parse_catalog_text(EXAMPLE)] == commands


def test_bang_lines_blank_lines_and_spaces_are_not_commands():
    text = "  enable  \n\n!\n! 주석은 장비도 무시한다\nshow version\nshow clock\r\n   !   \n"
    assert cc.parse_catalog_text(text) == [(1, "enable"), (5, "show version"), (6, "show clock")]
    commands, blocked, warnings = cc.check_catalog_text(text)
    assert cc.commands_to_text(commands) == "enable\n!\nshow version\n!\nshow clock\n!\n"   # 저장하면 '!' 를 채운다
    assert blocked == [] and warnings == []


def test_blocked_lines_are_named_with_their_line_numbers():
    _commands, blocked, _warnings = cc.check_catalog_text("show version\n!\nconf t\n!\nreload\n!\n")
    assert [(line["line"], line["command"]) for line in blocked] == [(3, "conf t"), (5, "reload")]


def test_korean_typos_and_repeats_are_flagged_but_kept():
    """예전 기본 카탈로그를 받은 고객사 파일에 남은 'show spanning-tree 피무'(한/영 전환한 vlan)."""
    commands, blocked, warnings = cc.check_catalog_text("show spanning-tree 피무\n!\nshow version\n!\nshow version\n!\n")
    assert commands == ["show spanning-tree 피무", "show version", "show version"] and blocked == []
    assert [(line["line"], "한/영" in line["reason"]) for line in warnings] == [(1, True), (5, False)]
    assert "앞에 같은 명령" in warnings[1]["reason"] and [line["index"] for line in warnings] == [0, 2]


def test_saved_items_are_all_on_in_written_order_with_check_ids():
    catalog = cc.catalog_from_commands(["show version", "show hardware capacity"])
    assert [(item["id"], item["command"], item["enabled"]) for item in catalog] == [
        ("cmd_0", "show version", True), ("cmd_1", "show hardware capacity", True)]
    assert catalog[0]["check_id"] == "version_info" and "check_id" not in catalog[1]
    assert cc.split_enabled_commands(catalog) == (["show version", "show hardware capacity"], [])


def test_reset_catalog_is_the_default_spreadsheet():
    catalog = cc.default_catalog()
    assert [item["command"] for item in catalog] == [item["command"] for item in cc._make_default_catalog()]
    assert all(item["enabled"] and item.get("check_id") for item in catalog)


@pytest.mark.parametrize("raw", [
    "﻿enable\r\n!\r\nshow version\r\n!\r\n".encode("utf-8"),     # 메모장 UTF-8(BOM)·CRLF
    "enable\n!\nshow version\n! 버전\n".encode("cp949"),                # 한글 Windows 메모장 ANSI
])
def test_text_files_from_notepad_are_read(tmp_path, raw):
    path = tmp_path / "catalog.txt"
    path.write_bytes(raw)
    assert cc.read_catalog_file(str(path)) == ["enable", "show version"]


def test_old_excel_exports_are_read_keeping_only_commands_that_were_on(tmp_path):
    path = tmp_path / "commands_catalog.xlsx"
    workbook = openpyxl.Workbook()
    for row in (["순서", "카테고리", "사용여부", "커맨드", "설명", "체크ID"], [1, "필수", "TRUE", "enable", "", ""],
                [2, "필수", "FALSE", "conf", "", ""], [3, "필수", "TRUE", "show version", "버전", "version_info"]):
        workbook.active.append(row)
    workbook.save(str(path))
    assert cc.read_catalog_file(str(path)) == ["enable", "show version"]


def test_a_file_too_big_to_be_a_catalog_is_refused(tmp_path):
    path = tmp_path / "big.txt"
    path.write_bytes(b"show version\n" * (cc.MAX_CATALOG_FILE_BYTES // 13 + 1))
    with pytest.raises(ValueError):
        cc.read_catalog_file(str(path))


# ---------- 화면 API: 저장·내보내기·불러오기 ----------

class _Api(CatalogApiMixin):
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.catalog_path = tmp_path / "commands_catalog.yaml"

    def _paths(self):
        return {"commands_catalog": str(self.catalog_path)}

    def _load_catalog(self, paths):
        return cc.load_catalog(paths["commands_catalog"])

    def _catalog_dir(self):
        return str(self.tmp_path / "commands")


class _Window:
    def __init__(self, result):
        self.result, self.kwargs = result, None

    def create_file_dialog(self, *args, **kwargs):
        self.kwargs = kwargs
        return self.result


@pytest.fixture
def api(tmp_path):
    api = _Api(tmp_path)
    cc.save_catalog(cc.catalog_from_commands(["enable", "show version"]), str(api.catalog_path))
    return api


def test_saving_normalizes_the_text_and_reports_warnings(api):
    result = api.save_catalog_text("enable\nterminal length 0\nshow spanning-tree 피무\n")
    assert result["ok"] and result["count"] == 3
    assert result["text"] == "enable\n!\nterminal length 0\n!\nshow spanning-tree 피무\n!\n"
    assert result["warnings"][0]["line"] == 3
    assert api.get_catalog_text()["text"] == result["text"]


def test_an_empty_text_is_not_saved(api):
    assert api.save_catalog_text("!\n\n!\n")["error"]
    assert cc.split_enabled_commands(cc.load_catalog(str(api.catalog_path)))[0] == ["enable", "show version"]


def test_export_writes_a_notepad_text_file_into_the_catalog_folder(api, monkeypatch, tmp_path):
    target = tmp_path / "out" / "mine"
    target.parent.mkdir()
    window = _Window(str(target))
    monkeypatch.setattr(window_ref, "get_window", lambda: window)
    result = api.export_catalog_text("enable\nshow version\n")
    assert result == {"path": str(target) + ".txt", "count": 2}
    assert (tmp_path / "out" / "mine.txt").read_bytes() == b"enable\r\n!\r\nshow version\r\n!\r\n"
    assert window.kwargs["directory"] == str(tmp_path / "commands")
    assert window.kwargs["save_filename"] == "commands_catalog.txt"


def test_import_returns_the_text_for_the_editor_without_saving(api, monkeypatch, tmp_path):
    source = tmp_path / "picked.txt"
    source.write_bytes(b"enable\r\n!\r\nshow clock\r\n!\r\n")
    monkeypatch.setattr(window_ref, "get_window", lambda: _Window([str(source)]))
    result = api.import_catalog_text()
    assert result == {"text": "enable\n!\nshow clock\n!\n", "count": 2, "path": str(source)}
    assert cc.split_enabled_commands(cc.load_catalog(str(api.catalog_path)))[0] == ["enable", "show version"]


def test_cancelled_dialogs_change_nothing(api, monkeypatch):
    monkeypatch.setattr(window_ref, "get_window", lambda: _Window(None))
    assert api.export_catalog_text("show version\n") is None
    assert api.import_catalog_text() is None
