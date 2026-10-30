"""빌드 번들의 비밀 차단 — IMPROVEMENT_PLAN 1-6.

번들한 설정 파일은 첫 실행 때 사용자 폴더로 복사된다. 개발 PC 의 ai_config.yaml(클라우드 AI 키)이
번들에 섞이면 EXE 를 받은 모든 사용자에게 그 키가 퍼지는 구조였다.

  1. AutoCheck.spec 은 ai_config.yaml·labs 를 담지 않고, 담기 전에 비밀 검사를 부른다.
  2. 검사기(tools/bundle_secret_check)는 이름·키 모양·값이 든 비밀 필드를 잡고, 자리표시자는 넘긴다.
  3. 지금 저장소의 번들 대상은 검사를 통과한다(빌드가 막히지 않는다).
  4. 첫 실행 때 번들에 ai_config.yaml 이 있어도 사용자 폴더로 복사하지 않는다.
"""
from pathlib import Path

import pytest

from core.paths import AppPaths
from tools.bundle_secret_check import find_bundle_secrets, scan_file, spec_bundle_sources

ROOT = Path(__file__).resolve().parent.parent
SPEC = (ROOT / "AutoCheck.spec").read_text(encoding="utf-8")
FAKE_GOOGLE_KEY = "AIza" + "x" * 35          # 진짜 키가 아니다 — 모양만 같다


def _spec_bundle_sources():
    return spec_bundle_sources(SPEC)


def test_spec_does_not_bundle_secret_locations():
    fixed, optional = _spec_bundle_sources()
    assert fixed == ["web_ui", "config", "VERSION"]
    assert optional == ["connection.yaml", "ai_settings.yaml"]
    assert "'labs'" not in SPEC and "'jinja2'" not in SPEC
    assert "find_bundle_secrets(" in SPEC


def test_current_bundle_sources_are_clean():
    fixed, optional = _spec_bundle_sources()
    sources = [ROOT / name for name in fixed + optional]
    assert all(path.exists() for path in sources)
    assert find_bundle_secrets(sources) == []


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("rel", ["ai_config.yaml", "x/credential.json", "portable.txt", "autocheck_data/a.txt"])
def test_secret_locations_are_rejected_by_name(tmp_path, rel):
    assert scan_file(_write(tmp_path / rel, "")) != []


@pytest.mark.parametrize("name, text", [
    ("a.yaml", f"providers:\n  - name: gemini\n    api_key: {FAKE_GOOGLE_KEY}\n"),
    ("b.json", '{"devices": [{"name": "Core1", "password": "hunter2"}]}'),
    ("c.yml", "defaults:\n  default_password: admin\n"),
    ("d.js", f"const KEY = '{FAKE_GOOGLE_KEY}';\n"),        # 설정 파일이 아니어도 키 모양은 잡는다
])
def test_secrets_are_found(tmp_path, name, text):
    problems = scan_file(_write(tmp_path / name, text))
    assert problems
    assert not any("hunter2" in p or FAKE_GOOGLE_KEY in p for p in problems)   # 값은 보고하지 않는다


@pytest.mark.parametrize("name, text", [
    ("a.yaml", "providers:\n  - name: gemini\n    api_key: ''\n    token: null\n    password: ~\n"),
    ("b.yaml", "secret: <여기에 키>\nkey_content: |\n"),
    ("c.json", '{"password": "", "api_key": ""}'),
    ("d.js", "body: { password: document.getElementById('pw').value }\n"),   # 화면 코드의 필드 이름
    ("e.yaml", "rules:\n  - pattern: 'Invalid (user|password)'\n"),
])
def test_placeholders_and_code_are_not_flagged(tmp_path, name, text):
    assert scan_file(_write(tmp_path / name, text)) == []


def test_binary_files_are_skipped(tmp_path):
    path = tmp_path / "catalog.xlsx"
    path.write_bytes(b"PK\x03\x04\xff\xfe password: hunter2")
    assert scan_file(path) == []


def test_first_run_does_not_copy_ai_config(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    _write(bundle / "ai_config.yaml", f"providers:\n  - api_key: {FAKE_GOOGLE_KEY}\n")
    _write(bundle / "connection.yaml", "ssh:\n  connect_timeout_sec: 20\n")
    monkeypatch.setattr(AppPaths, "bundle_root", classmethod(lambda cls: bundle))
    user = tmp_path / "user"
    user.mkdir()
    AppPaths._init_user_data_templates(user)
    assert (user / "connection.yaml").exists()
    assert not (user / "ai_config.yaml").exists()
