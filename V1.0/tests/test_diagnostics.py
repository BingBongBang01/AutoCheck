"""진단 번들 — IMPROVEMENT_PLAN 4-10b.

  1. zip 하나에 앱 정보·작업공간 수·점검·최근 앱 로그·설정(사용자 사본)·manifest 를 담는다. 고객사 목록·이력은 담지 않는다.
  2. 비밀(API 키·토큰·봉인 값·비밀번호 해시·설정의 비밀 필드)은 언제나 지운다.
  3. 기본으로 장비·고객사·프로파일 이름과 IP 도 가린다 — 숫자뿐인 프로파일 이름('2026-10')은 날짜를 망가뜨려 가리지 않는다.
"""
import json
import zipfile

import pytest
import yaml

from core.paths import AppPaths
from test_round_compare import root  # noqa: F401 — fixture

API_KEY = "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAA"
HASH = "$1$abcd$Xyz123456"
LOG_LINES = [
    "2026-10-28 10:00:01 [INFO] 점검 시작 — 장비 Core1 (10.1.2.3)",
    f"2026-10-28 10:00:02 [ERROR] Claude 호출 실패: x-api-key: {API_KEY}",
    f"2026-10-28 10:00:03 [INFO] Core1(config)#username admin secret 5 {HASH}",
    "2026-10-28 10:00:04 [INFO] ABC 고객사 / 2026-10 정기점검 보고서 저장",
]


@pytest.fixture
def workspace(root):
    """고객사 ABC 의 정기점검 2026-10(장비 Core1·Agg1), 앱 로그 하나, 설정 몇 개."""
    from api.customer_profile_api import CustomerProfileApiMixin
    from api.project_core_api import ProjectCoreApiMixin
    from engine import device_inventory as di
    from engine import project_manager as pm

    class Api(CustomerProfileApiMixin, ProjectCoreApiMixin):
        pass

    api = Api()
    api.create_customer("ABC")
    cid = next(c["id"] for c in api.get_customer_profiles())
    api.create_inspection_profile(cid, "2026-10", inspection_date="2026-10-15")
    project = next(p["id"] for c in api.get_customer_profiles() for p in c["profiles"])
    di.save_inventory({"defaults": {}, "devices": [
        {"name": n, "management_ip": f"10.1.2.{i}", "enabled": True} for i, n in enumerate(("Core1", "Agg1"), 3)]},
        pm.project_paths(project)["device_inventory"])
    (AppPaths.logs_root() / "app_full_20261028_100000.log").write_text("\n".join(LOG_LINES) + "\n", encoding="utf-8")
    (AppPaths.user_data_root() / "ai_config.yaml").write_text(yaml.safe_dump({
        "providers": [{"type": "cloud_apis", "entries": [
            {"name": "main", "provider": "anthropic", "api_key": API_KEY},
            {"name": "sealed", "provider": "openai", "api_key": "vault:dpapi:v1:QUJD"}]}],
        "local": {"endpoint": "http://localhost:13305", "model": "qwen"}}), encoding="utf-8")
    (AppPaths.config_root() / "alarm.yaml").write_text(yaml.safe_dump({
        "channels": [{"type": "smtp", "host": "mail.example.com", "password": "plainpw", "port": 25}]}),
        encoding="utf-8")
    (AppPaths.config_root() / "broken.yaml").write_text(f"token: {API_KEY}\n  : [", encoding="utf-8")
    return project


def _bundle(tmp_path, **kwargs):
    from engine import diagnostics
    result = diagnostics.build_bundle(tmp_path / "diag.zip", **kwargs)
    with zipfile.ZipFile(result["path"]) as archive:
        files = {name: archive.read(name).decode("utf-8") for name in archive.namelist()}
    return result, files


def test_the_bundle_holds_what_helps_and_leaves_out_customer_lists(workspace, tmp_path):
    result, files = _bundle(tmp_path)
    assert set(files) == {"about.json", "workspace.json", "checks.json", "logs/app_full_20261028_100000.log",
                          "config/ai_config.yaml", "config/alarm.yaml", "config/broken.yaml", "manifest.txt"}
    assert result["files"] == list(files) or set(result["files"]) == set(files)
    about = json.loads(files["about.json"])
    assert about["app_version"] and about["python"] and "packages" in about
    counts = json.loads(files["workspace.json"])
    assert (counts["customers"], counts["profiles"], counts["devices"]) == (1, 1, 2)
    checks = json.loads(files["checks.json"])
    assert checks["rules"]["clean"] is True and checks["data_folder_writable"] is True
    assert "customers.yaml" not in " ".join(files)


def test_secrets_are_always_removed(workspace, tmp_path):
    for mask in (True, False):
        _result, files = _bundle(tmp_path, mask_identifiers=mask)
        text = "\n".join(files.values())
        assert API_KEY not in text and HASH not in text and "plainpw" not in text and "QUJD" not in text
        config = yaml.safe_load(files["config/ai_config.yaml"])
        entries = config["providers"][0]["entries"]
        assert entries[0]["api_key"] == "[가림]" and entries[1]["api_key"] == "[봉인된 값]"   # 왜 비었는지 읽힌다
        assert config["local"]["model"] == "qwen"                            # 비밀이 아닌 설정은 그대로
        assert yaml.safe_load(files["config/alarm.yaml"])["channels"][0]["port"] == 25


def test_names_and_addresses_are_masked_by_default_but_dates_survive(workspace, tmp_path):
    result, files = _bundle(tmp_path)
    log = files["logs/app_full_20261028_100000.log"]
    assert "Core1" not in log and "10.1.2.3" not in log and "ABC" not in log
    assert "2026-10-28 10:00:01" in log and "2026-10 정기점검" in log      # 날짜·숫자뿐인 프로파일 이름은 남는다
    assert result["masked_identifiers"] is True and result["names_masked"] >= 3
    alarm = yaml.safe_load(files["config/alarm.yaml"])                   # 값마다 가려 다시 써서 YAML 로 읽힌다
    assert "example.com" not in alarm["channels"][0]["host"] and alarm["channels"][0]["host"].startswith("*")
    assert "이름" in files["manifest.txt"] and "가린 것" in files["manifest.txt"]


def test_masking_names_can_be_turned_off(workspace, tmp_path):
    result, files = _bundle(tmp_path, mask_identifiers=False)
    log = files["logs/app_full_20261028_100000.log"]
    assert "Core1" in log and "10.1.2.3" in log and result["masked_identifiers"] is False
    assert "가리지 않았다" in files["manifest.txt"]


def test_long_logs_keep_only_the_tail(workspace, tmp_path, monkeypatch):
    from engine import diagnostics
    monkeypatch.setattr(diagnostics, "LOG_TAIL_BYTES", 200)
    (AppPaths.logs_root() / "app_full_20261028_100000.log").write_text(
        "\n".join(f"line {i:04d}" for i in range(500)) + "\n", encoding="utf-8")
    _result, files = _bundle(tmp_path, mask_identifiers=False)
    log = files["logs/app_full_20261028_100000.log"]
    assert log.startswith("[앞 ") and "line 0499" in log and "line 0000" not in log
