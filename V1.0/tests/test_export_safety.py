"""내보내기 — IMPROVEMENT_PLAN 1-5.

  1. 워크스페이스 내보내기가 이전 내보내기 zip(archive/workspace_export_*, run 의 exports/)을 다시
     담아서 내보낼 때마다 크기가 두 배가 되던 것 — 세 번 연달아 내보내도 내용이 같아야 한다.
  2. 계정 정보(profile/credential.json, inventory/ 의 장비 목록 사본)는 기본으로 빠지고, 요청할 때만 담긴다.
  3. zip 을 메모리에 다 만들지 않고 임시 파일에 써서 교체한다 — 실패하면 기존 파일이 그대로 남는다.
  4. 프로젝트 zip 은 장비 비밀번호·개인키를 비워서 담는다(요청하면 그대로 + 경고).
"""
import zipfile

import pytest
import yaml

from core.secret_redaction import scrub_secret_fields
from core.storage_service import PathTarget, StorageService
from engine.profile_manager import Profile, RunHandle
from engine.report_manager import ReportManager

WORKSPACE_FILES = {
    "profile/profile.json": "{}",
    "profile/credential.json": '{"username": "admin", "password": "P@ss"}',
    "inventory/device_inventory.xlsx": "장비 목록(비밀번호 열 포함)",
    "commands/catalog.yaml": "items: []",
    "runs/r1/raw/Core1.txt": "show version",
    "runs/r1/raw/.Core2.txt.abc.tmp": "쓰는 중",
    "runs/r1/exports/run_r1.zip": "이전 내보내기",
    "archive/r0/raw/Core1.txt": "보관된 실행",
    "archive/r0/exports/logs_r0.zip": "이전 내보내기",
    "archive/workspace_export_2026-10-01_000000.zip": "이전 워크스페이스 내보내기",
}
EXPECTED_DEFAULT = ["archive/r0/raw/Core1.txt", "commands/catalog.yaml", "profile/profile.json",
                    "runs/r1/raw/Core1.txt"]


@pytest.fixture
def workspace(tmp_path):
    pdir = tmp_path / "고객사" / "2026-10"
    for rel, text in WORKSPACE_FILES.items():
        path = pdir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Profile(customer="고객사", name="2026-10", path=pdir)


def _names(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        return sorted(zf.namelist())


def test_workspace_export_leaves_out_old_exports_and_credentials(workspace):
    path = ReportManager().export_workspace(workspace)
    assert path.parent == workspace.path / "archive"
    assert _names(path) == EXPECTED_DEFAULT


def test_workspace_export_does_not_grow(workspace):
    manager = ReportManager()
    sizes = []
    for i in range(3):
        path = manager.export_workspace(workspace)
        assert _names(path) == EXPECTED_DEFAULT
        sizes.append(path.stat().st_size)
        # 다음 회차에는 '이전 내보내기'로 남는다(같은 초에 만들어져 덮어써지지 않게 이름만 바꾼다)
        path.rename(path.with_name(f"workspace_export_prev{i}.zip"))
    assert len(set(sizes)) == 1


def test_workspace_export_includes_credentials_only_when_asked(workspace):
    manager = ReportManager()
    assert manager.credential_files(workspace) == ["inventory/device_inventory.xlsx", "profile/credential.json"]
    names = _names(manager.export_workspace(workspace, include_credentials=True))
    assert names == sorted(EXPECTED_DEFAULT + ["inventory/device_inventory.xlsx", "profile/credential.json"])


def test_run_and_log_exports_leave_out_exports_and_temp_files(workspace):
    run = RunHandle(profile=workspace, run_id="r1", path=workspace.path / "runs" / "r1")
    manager = ReportManager()
    for _ in range(2):   # 두 번째 내보내기도 첫 번째 zip 을 담지 않는다
        path = manager.export_run(run)
        assert path == run.exports_dir / "run_r1.zip"
        assert _names(path) == ["raw/Core1.txt"]
    assert _names(manager.export_logs(run, categories=("raw",))) == ["raw/Core1.txt"]


def test_save_zip_keeps_the_old_file_when_it_fails(tmp_path):
    target = PathTarget(path=tmp_path)
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    out = tmp_path / "out" / "x.zip"
    out.parent.mkdir()
    out.write_bytes(b"old")
    with pytest.raises(OSError):
        StorageService().save_zip(target, "out/x.zip", [("a.txt", tmp_path / "a.txt"),
                                                        ("b.txt", tmp_path / "missing.txt")])
    assert out.read_bytes() == b"old"
    assert [p.name for p in out.parent.iterdir()] == ["x.zip"]   # 임시 파일도 남지 않는다


def test_save_zip_writes_entries(tmp_path):
    (tmp_path / "a.txt").write_text("가나다", encoding="utf-8")
    path = StorageService().save_zip(PathTarget(path=tmp_path), "out/x.zip", [("dir/a.txt", tmp_path / "a.txt")])
    with zipfile.ZipFile(path) as zf:
        assert zf.read("dir/a.txt").decode("utf-8") == "가나다"


def test_scrub_secret_fields():
    data = {"defaults": {"default_username": "admin", "default_password": "admin"},
            "devices": [{"name": "Core1", "username": "u", "password": "p", "key_content": "-----BEGIN",
                         "key_passphrase": ""}]}
    scrubbed, count = scrub_secret_fields(data)
    assert count == 3
    assert scrubbed["defaults"] == {"default_username": "admin", "default_password": ""}
    assert scrubbed["devices"][0] == {"name": "Core1", "username": "u", "password": "", "key_content": "",
                                      "key_passphrase": ""}
    assert data["devices"][0]["password"] == "p"   # 원본은 바꾸지 않는다


# --------------------------------------------------------------------------- 프로젝트 zip

@pytest.fixture
def project_zip(tmp_path, monkeypatch):
    from api import window_ref
    from engine import project_manager as pm
    proj = tmp_path / "labs" / "proj1"
    proj.mkdir(parents=True)
    (proj / "project_meta.yaml").write_bytes("# 주석은 그대로\ndisplay_name: P1\n".encode("utf-8"))
    inventory = {"defaults": {"default_username": "admin", "default_password": "s3cret"},
                 "devices": [{"name": "Core1", "password": "dev-pass", "key_content": "-----BEGIN KEY-----",
                              "key_passphrase": "pp"}]}
    (proj / "device_inventory.yaml").write_text(yaml.safe_dump(inventory, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(pm, "LABS_DIR", str(tmp_path / "labs"))
    out = tmp_path / "out.zip"
    window = type("Window", (), {"create_file_dialog": lambda self, *a, **k: str(out)})()
    monkeypatch.setattr(window_ref, "_window", window)
    return out


def _read_zip(path):
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name).decode("utf-8") for name in zf.namelist()}


def test_project_export_blanks_device_secrets_by_default(project_zip):
    from api.project_core_api import ProjectCoreApiMixin
    result = ProjectCoreApiMixin().export_project("proj1")
    assert result["ok"] and "notice" in result and "warning" not in result
    files = _read_zip(project_zip)
    inventory = yaml.safe_load(files["proj1/device_inventory.yaml"])
    assert inventory["defaults"] == {"default_username": "admin", "default_password": ""}
    assert inventory["devices"][0] == {"name": "Core1", "password": "", "key_content": "", "key_passphrase": ""}
    assert files["proj1/project_meta.yaml"] == "# 주석은 그대로\ndisplay_name: P1\n"   # 계정 정보 없는 파일은 그대로
    assert "s3cret" not in "".join(files.values()) and "dev-pass" not in "".join(files.values())


def test_project_export_keeps_secrets_only_when_asked(project_zip):
    from api.project_core_api import ProjectCoreApiMixin
    result = ProjectCoreApiMixin().export_project("proj1", True)
    assert "warning" in result
    inventory = yaml.safe_load(_read_zip(project_zip)["proj1/device_inventory.yaml"])
    assert inventory["devices"][0]["password"] == "dev-pass"
