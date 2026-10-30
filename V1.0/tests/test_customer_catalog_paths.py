"""고객사 공용 카탈로그 위치 — IMPROVEMENT_PLAN 2-2.

commands_catalog 는 고객사 단위로 공유된다(labs/_customers/<…>/commands_catalog.yaml). 그 폴더 이름이
고객사 이름 슬러그였는데 _slugify 가 비ASCII 를 지워서, 한글 고객사는 전부 'project' 를,
'NC서비스망'·'NC소프트'는 둘 다 'nc' 를 함께 썼다. A사 카탈로그를 고치면 B사 점검 명령이 바뀌었다.
이제는 고객사 id 로 가르고, 예전 공용 위치의 카탈로그는 각 고객사로 한 번 복사한다.
"""
import tempfile
from pathlib import Path

import pytest
import yaml

from core.paths import AppPaths

ID_A = "a" * 32
ID_B = "b" * 32


@pytest.fixture
def labs(monkeypatch):
    original = AppPaths._user_data_root
    root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths._user_data_root = root
    AppPaths.forget_ensured()
    from engine import project_manager as pm
    monkeypatch.setattr(pm, "LABS_DIR", str(root / "labs"))
    yield root / "labs"
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def make_project(labs, pid, customer_name, customer_id=None, catalog="items: [own]\n"):
    folder = labs / pid
    folder.mkdir(parents=True)
    meta = {"display_name": pid, "profile_name": pid, "customer_name": customer_name}
    if customer_id:
        meta["customer_id"] = customer_id
    (folder / "project_meta.yaml").write_text(yaml.safe_dump(meta, allow_unicode=True), encoding="utf-8")
    (folder / "commands_catalog.yaml").write_text(catalog, encoding="utf-8")


def catalog_of(pid):
    from engine import project_manager as pm
    return Path(pm.project_paths(pid)["commands_catalog"])


def test_korean_customers_no_longer_share_a_catalog(labs):
    make_project(labs, "p_a", "NC서비스망", ID_A)
    make_project(labs, "p_b", "NC소프트", ID_B)
    path_a, path_b = catalog_of("p_a"), catalog_of("p_b")
    assert path_a != path_b
    assert path_a.parent.name == ID_A and path_b.parent.name == ID_B
    path_a.write_text("items: [changed]\n", encoding="utf-8")
    assert path_b.read_text(encoding="utf-8") == "items: [own]\n"


def test_profiles_of_one_customer_share_its_catalog(labs):
    make_project(labs, "p1", "고객사A", ID_A)
    make_project(labs, "p2", "고객사A", ID_A, catalog="items: [second]\n")
    assert catalog_of("p1") == catalog_of("p2")
    assert catalog_of("p2").read_text(encoding="utf-8") == "items: [own]\n"   # 먼저 올라간 것이 공용


def test_the_old_shared_catalog_is_copied_to_each_customer_once(labs):
    legacy = labs / "_customers" / "nc"
    legacy.mkdir(parents=True)
    (legacy / "commands_catalog.yaml").write_text("items: [shared-before]\n", encoding="utf-8")
    make_project(labs, "p_a", "NC서비스망", ID_A)
    make_project(labs, "p_b", "NC소프트", ID_B)
    assert catalog_of("p_a").read_text(encoding="utf-8") == "items: [shared-before]\n"
    catalog_of("p_a").write_text("items: [only-a]\n", encoding="utf-8")
    assert catalog_of("p_b").read_text(encoding="utf-8") == "items: [shared-before]\n"
    assert catalog_of("p_a").read_text(encoding="utf-8") == "items: [only-a]\n"   # 다시 덮어쓰지 않는다


@pytest.mark.parametrize("customer_id", [None, "../evil", "A" * 32])
def test_metas_without_a_valid_id_keep_the_old_location(labs, customer_id):
    make_project(labs, "p1", "Acme", customer_id)
    assert catalog_of("p1") == labs / "_customers" / "acme" / "commands_catalog.yaml"


def test_legacy_shared_inventory_is_still_found(labs):
    legacy = labs / "_customers" / "project"
    legacy.mkdir(parents=True)
    (legacy / "device_inventory.yaml").write_text("devices: []\n", encoding="utf-8")
    make_project(labs, "p1", "한글고객사", ID_A)                        # 슬러그가 비어 'project' 였다
    from engine import project_manager as pm
    inventory = Path(pm.project_paths("p1")["device_inventory"])
    assert inventory.read_text(encoding="utf-8") == "devices: []\n"
