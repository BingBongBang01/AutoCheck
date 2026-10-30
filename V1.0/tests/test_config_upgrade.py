"""사용자 설정 업그레이드 체계 — IMPROVEMENT_PLAN 2-3.

  1. 출처 정책(core/app_config.py): 보고서 템플릿은 번들 기본값 위에 사용자 사본을 얹어 읽는다.
     사용자 사본만 읽던 때는 처음 설치할 때 복사된 사본이 굳어서, 새 버전이 점검 항목에 더한
     필드(virtual_na — 가상환경이면 '해당없음')가 기존 사용자에게 적용되지 않았다.
  2. 첫 실행 복사는 사용자가 고칠 수 있는 템플릿만, 그리고 폴더가 이미 있어도 빠진 템플릿은 채운다.
  3. 업그레이드 단계(UPGRADE_FUNCS)는 레거시 데이터가 없어도 시작할 때마다 확인하고, 단계마다 기록한다.
  4. 버전 파일을 다시 쓸 때 다른 키(upgrade_version)를 지우지 않는다.
  5. 마이그레이션 되돌리기는 복사한 뒤 손대지 않은 파일만 지운다(사용자가 그 뒤에 쌓은 기록은 남긴다).
"""
from pathlib import Path

import pytest
import yaml

from core.app_config import merge_layers
from core.paths import AppPaths
from engine import migration_manager as mm

BUNDLED_TEMPLATE = {
    "meta": {"title_suffix": "정기점검 보고서", "manager": {"company": "", "name": ""}},
    "check_items": [
        {"name": "System Uptime", "commands": ["show version"], "evaluator": "uptime"},
        {"name": "Power Supply", "commands": ["show environment power"], "evaluator": "keyword_ok",
         "virtual_na": True},
        {"name": "NTP", "commands": ["show ntp status"], "evaluator": "keyword_ok"},   # 새 버전에 추가된 항목
    ],
}
OLD_USER_COPY = {   # 예전 버전에서 복사된 뒤 사용자가 조금 고친 사본
    "meta": {"title_suffix": "월간 점검", "manager": {"company": "고객사"}},
    "check_items": [
        {"name": "Power Supply", "commands": ["show environment power"], "evaluator": "keyword_ok",
         "criteria": "전원 정상"},
        {"name": "System Uptime", "commands": ["show version"], "evaluator": "uptime"},
        {"name": "사용자 항목", "commands": ["show clock"], "evaluator": "keyword_ok"},
    ],
}


@pytest.fixture
def roots(tmp_path):
    original_app, original_user = AppPaths._root, AppPaths._user_data_root
    app_root, user_root = tmp_path / "app", tmp_path / "user"
    (app_root / "config").mkdir(parents=True)
    AppPaths._root = app_root
    AppPaths._user_data_root = user_root
    AppPaths.forget_ensured()
    try:
        yield app_root, user_root
    finally:
        AppPaths._root, AppPaths._user_data_root = original_app, original_user
        AppPaths.forget_ensured()


def _write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


# --------------------------------------------------------------------------- 1. 병합

def test_user_values_win_and_missing_fields_come_from_the_bundle():
    merged = merge_layers(BUNDLED_TEMPLATE, OLD_USER_COPY, {"check_items": "name"})
    assert merged["meta"] == {"title_suffix": "월간 점검", "manager": {"company": "고객사", "name": ""}}
    names = [item["name"] for item in merged["check_items"]]
    assert names == ["Power Supply", "System Uptime", "사용자 항목"]       # 사용자의 선택·순서 그대로
    power = merged["check_items"][0]
    assert power["virtual_na"] is True and power["criteria"] == "전원 정상"   # 새 필드 + 사용자 값
    assert "NTP" not in names                                              # 지운 항목을 되살리지 않는다
    assert "virtual_na" not in OLD_USER_COPY["check_items"][0]             # 입력은 바꾸지 않는다


def test_empty_user_values_fall_back_to_the_bundle():
    merged = merge_layers({"meta": {"a": 1}, "items": [1]}, {"meta": None, "items": None})
    assert merged == {"meta": {"a": 1}, "items": [1]}


def test_report_template_gets_new_fields_for_old_copies(roots):
    app_root, _ = roots
    from engine.inspection_evaluator import load_template
    _write_yaml(app_root / "config" / "inspection_report_template.yaml", BUNDLED_TEMPLATE)
    _write_yaml(AppPaths.config_root() / "inspection_report_template.yaml", OLD_USER_COPY)
    template = load_template()
    power = next(item for item in template["check_items"] if item["name"] == "Power Supply")
    assert power["virtual_na"] is True
    assert template["meta"]["title_suffix"] == "월간 점검"


def test_report_template_without_a_user_copy_is_the_bundle(roots):
    app_root, _ = roots
    from engine.inspection_evaluator import load_template
    _write_yaml(app_root / "config" / "inspection_report_template.yaml", BUNDLED_TEMPLATE)
    assert [i["name"] for i in load_template()["check_items"]] == ["System Uptime", "Power Supply", "NTP"]


def test_real_bundled_template_marks_virtual_items():
    """저장소의 실제 템플릿이 병합 경로로 읽혀도 virtual_na 항목이 있다(가상환경 판정의 전제)."""
    from engine.inspection_evaluator import load_template
    assert any(item.get("virtual_na") for item in load_template()["check_items"])


# --------------------------------------------------------------------------- 2. 첫 실행 복사

def test_first_run_copies_only_user_editable_templates(roots):
    app_root, user_root = roots
    _write_yaml(app_root / "config" / "inspection_report_template.yaml", BUNDLED_TEMPLATE)
    (app_root / "config" / "log_rules.json").write_text("{}", encoding="utf-8")
    (user_root / "config").mkdir(parents=True)                             # 폴더는 이미 있다(예전 설치)
    AppPaths._init_user_data_templates(user_root)
    assert (user_root / "config" / "inspection_report_template.yaml").exists()
    assert not (user_root / "config" / "log_rules.json").exists()          # 앱 소유 규칙 — 번들에서만 읽는다


def test_first_run_does_not_overwrite_a_user_template(roots):
    app_root, user_root = roots
    _write_yaml(app_root / "config" / "inspection_report_template.yaml", BUNDLED_TEMPLATE)
    _write_yaml(user_root / "config" / "inspection_report_template.yaml", OLD_USER_COPY)
    AppPaths._init_user_data_templates(user_root)
    data = yaml.safe_load((user_root / "config" / "inspection_report_template.yaml").read_text(encoding="utf-8"))
    assert data == OLD_USER_COPY


# --------------------------------------------------------------------------- 3·4. 업그레이드 단계

def test_upgrades_run_without_legacy_data_and_record_each_step(roots, monkeypatch):
    calls = []
    state = {"fail": True}

    def step(n):
        def run(app_root):
            if n == 2 and state["fail"]:
                raise OSError("잠긴 파일")
            calls.append(n)
        return run

    monkeypatch.setattr(mm, "UPGRADE_FUNCS", {1: step(1), 2: step(2), 3: step(3)})
    result = mm.migrate_if_needed()
    assert result["skipped"] is True                                       # 레거시 데이터 없음
    assert result["upgrades"]["applied"] == [1] and "업그레이드 2 실패" in result["upgrades"]["error"]
    assert mm.read_version_state()["upgrade_version"] == 1

    state["fail"] = False
    assert mm.apply_pending_upgrades()["applied"] == [2, 3]
    assert mm.apply_pending_upgrades()["applied"] == []                     # 다시 돌지 않는다
    assert calls == [1, 2, 3]
    version_dir = mm._version_path().parent
    assert not [p for p in version_dir.iterdir() if p.name.endswith(".tmp")]


def _add_legacy_logs(app_root):
    lab = app_root / "labs" / "lab1"
    lab.mkdir(parents=True)
    (lab / "device_inventory.yaml").write_text("devices: []\n", encoding="utf-8")
    session = app_root / "raw_logs" / "lab1" / "s1"
    session.mkdir(parents=True)
    (session / "Core1.txt").write_text("show version\n", encoding="utf-8")
    (session / "Core2.txt").write_text("show version\n", encoding="utf-8")


def test_migration_keeps_the_upgrade_version(roots, monkeypatch):
    app_root, _ = roots
    _add_legacy_logs(app_root)
    monkeypatch.setattr(mm, "UPGRADE_FUNCS", {1: lambda root: None})
    mm.apply_pending_upgrades()
    result = mm.run_migration()
    assert result["success"] is True
    state = mm.read_version_state()
    assert state["upgrade_version"] == 1 and state["migration_version"] == mm.MIGRATION_VERSION


# --------------------------------------------------------------------------- 5. 되돌리기

def test_rollback_removes_only_untouched_copies(roots):
    app_root, _ = roots
    _add_legacy_logs(app_root)
    result = mm.run_migration()
    copied = next(entry["destination"] for entry in result["migrated_files"] if entry["destination"].endswith("raw_logs"))
    session = Path(copied) / "lab1" / "s1"
    (session / "Core2.txt").write_text("사용자가 고친 내용\n", encoding="utf-8")
    (session / "new_after_migration.txt").write_text("새 기록\n", encoding="utf-8")

    assert mm.rollback_migration(result) is True
    assert not (session / "Core1.txt").exists()                            # 복사한 그대로 — 지운다
    assert (session / "Core2.txt").read_text(encoding="utf-8") == "사용자가 고친 내용\n"
    assert (session / "new_after_migration.txt").exists()
    assert (app_root / "raw_logs" / "lab1" / "s1" / "Core1.txt").exists()   # 원본은 그대로
    assert mm.read_version_state()["migration_version"] == 0                # 이전 상태(없음)로 돌아간다
