"""장비 목록 왕복 손상 — IMPROVEMENT_PLAN 2-6.

  1. 엑셀 내보내기는 enabled 를 'TRUE'/'FALSE' 문자열로 쓰는데, 가져올 때 문자열 그대로 두어서 'FALSE' 가
     참으로 읽혔다 → 꺼 둔 장비가 수집 대상이 됐다(get_enabled_devices).
  2. 덮어쓰기로 가져오면 파일에 없는 열(key_content 등)·빈 칸이 기존 값을 지웠다 → 붙여넣어 둔 개인키가 사라졌다.
"""
import pytest
import yaml

from engine import device_inventory as di

DEVICES = [
    {"name": "Core1", "management_ip": "10.0.0.1", "username": "ops", "password": "pw1", "enabled": True,
     "tag": ["core", "dc1"], "ssh_port": 22},
    {"name": "Spare", "management_ip": "10.0.0.9", "username": "ops", "password": "pw9", "enabled": False,
     "ssh_port": 2222, "auth_method": "public_key", "key_content": "-----BEGIN OPENSSH PRIVATE KEY-----"},
]


def test_excel_round_trip_keeps_disabled_devices_disabled(tmp_path):
    path = tmp_path / "devices.xlsx"
    di.export_to_excel([di.normalize_device(d) for d in DEVICES], str(path))
    imported = {d["name"]: d for d in di.import_excel(str(path))}
    assert imported["Spare"]["enabled"] is False
    assert imported["Core1"]["enabled"] is True
    assert imported["Core1"]["tag"] == ["core", "dc1"]
    assert imported["Spare"]["ssh_port"] == 2222
    inventory = {"defaults": {}, "devices": []}
    di.merge_imported(inventory, list(imported.values()))
    assert [d["name"] for d in di.get_enabled_devices(inventory)] == ["Core1"]


@pytest.mark.parametrize("value, expected", [
    ("FALSE", False), ("false", False), ("0", False), ("no", False), ("미사용", False), (0, False),
    ("TRUE", True), ("1", True), ("사용", True), (1, True), (None, True), ("", True), ("모름", True),
])
def test_enabled_values_from_files(value, expected):
    assert di.normalize_device({"name": "x", "enabled": value})["enabled"] is expected


def test_saved_string_flags_are_fixed_on_load(tmp_path):
    path = tmp_path / "device_inventory.yaml"
    path.write_text(yaml.safe_dump({"defaults": {}, "devices": [
        {"name": "Core1", "enabled": "TRUE"}, {"name": "Spare", "enabled": "FALSE"}]}), encoding="utf-8")
    inventory = di.load_inventory(str(path))
    assert [d["name"] for d in di.get_enabled_devices(inventory)] == ["Core1"]


def test_overwrite_import_keeps_values_the_file_does_not_have(tmp_path):
    inventory = {"defaults": {}, "devices": [di.normalize_device(d) for d in DEVICES]}
    path = tmp_path / "update.csv"
    path.write_text("name,management_ip,password,role\nSpare,10.0.0.99,,spine\n", encoding="utf-8")
    result = di.merge_imported(inventory, di.import_csv(str(path)), overwrite=True)
    assert result == {"added": 1, "skipped": 0}
    spare = next(d for d in inventory["devices"] if d["name"] == "Spare")
    assert spare["management_ip"] == "10.0.0.99" and spare["role"] == "spine"     # 파일에 있던 값은 바뀐다
    assert spare["password"] == "pw9"                                             # 빈 칸은 기존 값 유지
    assert spare["key_content"].startswith("-----BEGIN")                          # 없는 열도 유지
    assert spare["enabled"] is False and spare["ssh_port"] == 2222
    assert all("_provided" not in d for d in inventory["devices"])


def test_new_devices_from_a_file_are_stored_without_helper_fields(tmp_path):
    path = tmp_path / "new.yaml"
    path.write_text(yaml.safe_dump({"devices": [{"name": "Agg1", "management_ip": "10.0.1.1"}]}), encoding="utf-8")
    inventory = {"defaults": {}, "devices": []}
    di.merge_imported(inventory, di.import_yaml(str(path)))
    assert inventory["devices"][0]["name"] == "Agg1" and "_provided" not in inventory["devices"][0]


def test_copying_between_profiles_still_copies_whole_devices():
    src = {"defaults": {}, "devices": [di.normalize_device(DEVICES[1])]}
    dst = {"defaults": {}, "devices": [di.normalize_device({"name": "Spare", "password": "old"})]}
    di.copy_from_inventory(src, dst, overwrite=True)
    assert dst["devices"][0]["password"] == "pw9"
