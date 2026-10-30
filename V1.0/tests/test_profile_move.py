"""정기점검 옮기기 — 내보내기(zip)·가져오기, IMPROVEMENT_PLAN 5-3(D5).

고객사/정기점검 창에서 정기점검의 정의(labs/<id>)를 zip 으로 내보내고 다른 PC 에서 가져온다. 예전엔 이 두 기능이
부르는 곳 없는 옛 창(openProfileModal) 안에만 있었고, 가져온 프로젝트는 고객사 트리에 잇는 일 없이 놓였다 — 같은
고객사에 같은 이름의 정기점검이 있으면 두 프로파일이 data 폴더 하나를 함께 썼고, 트리 캐시도 비우지 않았다.
"""
import sys
import zipfile

import pytest
import yaml

from core import secret_vault as vault
from core.paths import AppPaths
from test_round_compare import root  # noqa: F401 — 임시 데이터 폴더(labs·고객사 목록 포함)


class _Window:
    result = None

    def create_file_dialog(self, *args, **kwargs):
        return self.result


@pytest.fixture
def api(root, monkeypatch):
    from api import window_ref
    from api.customer_profile_api import CustomerProfileApiMixin
    from api.project_core_api import ProjectCoreApiMixin

    class Api(CustomerProfileApiMixin, ProjectCoreApiMixin):
        pass

    instance = Api()
    instance.window = _Window()
    monkeypatch.setattr(window_ref, "_window", instance.window)
    return instance


def _tree(api):
    return {c["name"]: {p["profile_name"]: p["id"] for p in c["profiles"]} for c in api.get_customer_profiles()}


def _profile_with_device(api, customer, name, password="pw-1"):
    from engine import device_inventory as di
    from engine import project_manager as pm
    if customer not in _tree(api):
        api.create_customer(customer)
    cid = next(c["id"] for c in api.get_customer_profiles() if c["name"] == customer)
    assert api.create_inspection_profile(cid, name)["ok"] is True
    pid = _tree(api)[customer][name]
    di.save_inventory({"defaults": {}, "devices": [{"name": "Core1", "management_ip": "10.0.0.1",
                                                    "password": password}]}, pm.project_paths(pid)["device_inventory"])
    return pid


def _export(api, tmp_path, project_id, include=False):
    out = tmp_path / f"{project_id}.zip"
    api.window.result = str(out)
    assert api.export_project(project_id, include)["ok"] is True
    return out


def _import(api, path):
    api.window.result = [str(path)]
    return api.import_project()


def _zip(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return path


def test_a_moved_profile_gets_its_own_name_and_data_folder(api, tmp_path):
    from engine import device_inventory as di
    from engine import project_manager as pm
    pid = _profile_with_device(api, "ABC", "2026-10")
    package = _export(api, tmp_path, pid)

    first = _import(api, package)                       # 같은 PC — 같은 고객사에 같은 이름이 있다
    assert first["ok"] is True and first["project_id"] != pid
    assert (first["customer"], first["profile_name"], first["renamed"]) == ("ABC", "2026-10 (가져옴)", True)
    assert _tree(api)["ABC"] == {"2026-10": pid, "2026-10 (가져옴)": first["project_id"]}      # 캐시도 비웠다
    assert (AppPaths.data_root() / "ABC" / "2026-10 (가져옴)" / "profile" / "profile.json").is_file()
    assert _import(api, package)["profile_name"] == "2026-10 (가져옴 2)"
    devices = di.load_inventory(pm.project_paths(first["project_id"])["device_inventory"])["devices"]
    assert [d["name"] for d in devices] == ["Core1"] and devices[0]["password"] == ""   # 비우고 내보냈다


def test_the_customer_is_found_by_name_ignoring_case_or_created(api, tmp_path):
    _profile_with_device(api, "ABC", "P1")
    result = _import(api, _zip(tmp_path / "a.zip", {"projA/project_meta.yaml": yaml.safe_dump(
        {"customer_name": "abc", "profile_name": "p1"}, allow_unicode=True)}))
    assert (result["customer"], result["profile_name"], result["renamed"]) == ("ABC", "p1 (가져옴)", True)
    fresh = _import(api, _zip(tmp_path / "b.zip", {"projB/project_meta.yaml": yaml.safe_dump(
        {"customer_name": "새고객", "profile_name": "회차1"}, allow_unicode=True)}))
    assert (fresh["customer"], fresh["profile_name"], fresh["renamed"]) == ("새고객", "회차1", False)
    assert _tree(api)["새고객"] == {"회차1": fresh["project_id"]}
    assert sorted(_tree(api)) == ["ABC", "새고객"]


def test_a_profile_that_cannot_be_linked_is_rolled_back(api, tmp_path):
    from engine import customer_manager as cm
    _profile_with_device(api, "ABC", "P1")
    labs = AppPaths.user_data_root() / "labs"
    before, customers = sorted(p.name for p in labs.iterdir()), cm.load()
    result = _import(api, _zip(tmp_path / "bad.zip", {"projC/project_meta.yaml": yaml.safe_dump(
        {"customer_name": "XYZ", "profile_name": "a|b"})}))       # 폴더 이름으로 쓸 수 없다
    assert "되돌렸습니다" in result["error"]
    assert sorted(p.name for p in labs.iterdir()) == before
    assert cm.load() == customers and not (AppPaths.data_root() / "XYZ").exists()


@pytest.mark.skipif(sys.platform != "win32" or not vault.available(), reason="DPAPI 봉인은 Windows 에서만")
def test_passwords_carried_in_a_package_are_sealed_again_on_arrival(api, tmp_path):
    from engine import device_inventory as di
    from engine import project_manager as pm
    pid = _profile_with_device(api, "ABC", "P1", password="pw-move-1")
    package = _export(api, tmp_path, pid, include=True)
    with zipfile.ZipFile(package) as archive:
        assert any(b"pw-move-1" in archive.read(name) for name in archive.namelist())     # 평문으로 담았다
    result = _import(api, package)
    stored = (AppPaths.user_data_root() / "labs" / result["project_id"] / "device_inventory.yaml").read_text(
        encoding="utf-8")
    assert "pw-move-1" not in stored and vault.PREFIX in stored
    devices = di.load_inventory(pm.project_paths(result["project_id"])["device_inventory"])["devices"]
    assert devices[0]["password"] == "pw-move-1"
