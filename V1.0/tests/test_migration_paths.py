"""마이그레이션 산출물 위치 — IMPROVEMENT_PLAN 0-2.

예전에는 개발 트리(python main.py)에서 실행할 때마다 소스 트리에 migration_backup/ ·
_workspace_global/ · migration_report.json 이 쌓였다. 원인은 두 가지였다:

  1. app_root/config/*.yaml(번들 템플릿)만 있어도 '레거시 데이터'로 판정했다 — 개발 트리에는
     그 폴더가 항상 있으므로 새 사용자 데이터 폴더마다 마이그레이션이 돌았다.
  2. 백업·보고서·전역 설정 보관본을 app_root(프로그램 폴더)에 썼다.

레거시 원본은 app_root 에서 읽되, 산출물은 사용자 데이터 폴더에 써야 한다.
"""
import json

import pytest

from core.paths import AppPaths
from engine import migration_manager as mm


@pytest.fixture
def roots(tmp_path):
    """app_root(프로그램 폴더)와 user_data_root(사용자 데이터 폴더)를 각각 임시 폴더로 바꾼다.

    app_root 에는 개발 트리처럼 번들 config/ 를 미리 둔다.
    """
    original_app, original_user = AppPaths._root, AppPaths._user_data_root
    app_root = tmp_path / "app"
    user_root = tmp_path / "user"
    (app_root / "config").mkdir(parents=True)
    (app_root / "config" / "customers.yaml").write_text("[]\n", encoding="utf-8")
    AppPaths._root = app_root
    AppPaths._user_data_root = user_root
    AppPaths.forget_ensured()
    try:
        yield app_root, user_root
    finally:
        AppPaths._root, AppPaths._user_data_root = original_app, original_user
        AppPaths.forget_ensured()


def _add_legacy_lab(app_root):
    lab = app_root / "labs" / "lab1"
    lab.mkdir(parents=True)
    (lab / "device_inventory.yaml").write_text("devices: []\n", encoding="utf-8")
    return lab


def test_bundled_config_alone_is_not_legacy(roots):
    """개발 트리의 번들 config/ 만으로는 마이그레이션을 돌리지 않는다."""
    app_root, _ = roots
    assert not mm._has_legacy_data(app_root)
    assert not mm.needs_migration()


def test_lab_folder_is_legacy(roots):
    app_root, _ = roots
    _add_legacy_lab(app_root)
    assert mm._has_legacy_data(app_root)
    assert mm.needs_migration()


def test_outputs_go_to_user_data_root_not_app_root(roots):
    """백업·보고서·전역 설정 보관본이 프로그램 폴더를 더럽히지 않는다."""
    app_root, user_root = roots
    lab = _add_legacy_lab(app_root)

    result = mm.run_migration()

    assert result["success"], result["failed_files"]
    for leftover in ("migration_backup", "_workspace_global", "migration_report.json"):
        assert not (app_root / leftover).exists(), f"프로그램 폴더에 {leftover} 가 생겼다"

    backups = list((user_root / "migration_backup").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "labs" / "lab1" / "device_inventory.yaml").is_file()
    assert (user_root / "_workspace_global" / "legacy_config" / "customers.yaml").is_file()

    saved = json.loads((user_root / "migration_report.json").read_text(encoding="utf-8"))
    assert saved["success"] is True
    assert (user_root / "data" / "legacy_import" / "lab1" / "inventory" / "device_inventory.yaml").is_file()
    assert (lab / "device_inventory.yaml").is_file(), "레거시 원본은 그대로 남아야 한다"


def test_rollback_reads_report_from_user_data_root(roots):
    app_root, user_root = roots
    _add_legacy_lab(app_root)
    mm.run_migration()
    migrated = user_root / "data" / "legacy_import" / "lab1" / "inventory" / "device_inventory.yaml"
    assert migrated.is_file()

    assert mm.rollback_migration() is True
    assert not migrated.exists()
    assert not mm._version_path().exists(), "이전 버전이 0이면 버전 파일도 지운다"
