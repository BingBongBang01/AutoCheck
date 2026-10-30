"""경로 경계 검사 — IMPROVEMENT_PLAN 1-4.

화면(JS)이 넘기는 id·파일명, 장비가 돌려준 hostname, 받은 zip 의 멤버 이름이 그대로 경로에 붙던 곳들.
'..' 하나로 labs 전체나 사용자 데이터 폴더를 rmtree 하거나, zip 하나로 폴더 밖에 파일을 쓸 수 있었다.

  1. core.paths 의 safe_join / safe_child / safe_filename 자체.
  2. 지우는 곳(프로젝트·run)은 '바로 아래 한 단계'만 지울 수 있다.
  3. 쓰는 곳(StorageService, 지식 문서, 보고서 파일명, 장비별 로그 파일명)은 폴더 밖을 가리키지 못한다.
  4. 프로젝트 zip 가져오기: zip slip·zip bomb 을 거르고, 거를 때는 아무것도 쓰지 않는다.
"""
import datetime
import os
import zipfile
from pathlib import Path

import pytest

from core.paths import safe_child, safe_filename, safe_join

OUTSIDE_NAMES = ["..", "../x", "..\\x", "a/../../x", "C:\\Windows\\x", "C:/x", "/etc/x"]


# --------------------------------------------------------------------------- 1. 공통 함수

def test_safe_join_keeps_paths_inside_base(tmp_path):
    assert safe_join(tmp_path, "raw", "Core1.txt") == tmp_path / "raw" / "Core1.txt"
    assert safe_join(tmp_path, "raw/sub/../Core1.txt") == tmp_path / "raw/sub/../Core1.txt"   # 안에서 도는 '..' 는 허용
    assert safe_join(tmp_path) == tmp_path


@pytest.mark.parametrize("name", OUTSIDE_NAMES)
def test_safe_join_rejects_paths_leaving_base(tmp_path, name):
    with pytest.raises(ValueError):
        safe_join(tmp_path / "base", name)


@pytest.mark.parametrize("name", ["", " ", ".", "..", "a/b", "a\\b", "C:", "SW:1", None])
def test_safe_child_accepts_only_one_plain_name(tmp_path, name):
    with pytest.raises(ValueError):
        safe_child(tmp_path, name)


def test_safe_child_returns_the_child(tmp_path):
    assert safe_child(tmp_path, "proj_1") == tmp_path / "proj_1"


@pytest.mark.parametrize("name, expected", [
    ("Core1", "Core1"),                     # 정상 이름은 그대로 — 이미 저장된 파일명과 어긋나지 않게
    ("Spine-01.lab", "Spine-01.lab"),
    ("SW:1", "SW_1"),                       # ':' 는 NTFS 대체 스트림을 만든다
    ("../../x", ".._.._x"),
    ("a\\b", "a_b"),
    ("CON", "_CON"), ("com1.txt", "_com1.txt"),
    ("bad\x00name\n", "bad_name_"),
    ("trail. ", "trail"),
    ("", "unnamed"), ("..", "unnamed"), (None, "unnamed"),
])
def test_safe_filename(name, expected):
    assert safe_filename(name) == expected


# --------------------------------------------------------------------------- 2. 지우는 곳

@pytest.fixture
def labs(tmp_path, monkeypatch):
    from engine import project_manager as pm
    labs_dir = tmp_path / "data" / "labs"
    (labs_dir / "proj1").mkdir(parents=True)
    (labs_dir / "proj1" / "project_meta.yaml").write_text("display_name: P1\n", encoding="utf-8")
    (tmp_path / "data" / "keep.txt").write_text("사용자 데이터", encoding="utf-8")
    monkeypatch.setattr(pm, "LABS_DIR", str(labs_dir))
    return labs_dir


@pytest.mark.parametrize("project_id", ["", "..", ".", "../data", "proj1/.."])
def test_delete_project_rejects_ids_outside_labs(labs, project_id):
    from engine import project_manager as pm
    with pytest.raises(ValueError):
        pm.delete_project(project_id)
    assert (labs / "proj1" / "project_meta.yaml").exists()
    assert (labs.parent / "keep.txt").exists()


@pytest.mark.parametrize("project_id", ["..", "../x", "a\\b"])
def test_set_active_and_rename_reject_ids_outside_labs(labs, project_id):
    from engine import project_manager as pm
    with pytest.raises(ValueError):
        pm.set_active_project(project_id)
    with pytest.raises(ValueError):
        pm.rename_project(project_id, "새 이름")


def test_run_manager_rejects_run_ids_outside_runs(tmp_path):
    from engine.run_manager import RunManager, RunManagerError

    class FakeProfiles:
        def get_profile(self, customer, profile_name):
            return type("Profile", (), {"path": tmp_path})()

    with pytest.raises(RunManagerError):
        RunManager(profile_mgr=FakeProfiles())._get_run_handle("c", "p", "..")


# --------------------------------------------------------------------------- 3. 쓰는 곳

def test_storage_resolve_rejects_relative_paths_leaving_the_target(tmp_path):
    from core.storage_service import PathTarget, StorageOperationError, StorageService
    run = PathTarget(path=tmp_path / "run")
    service = StorageService()
    assert service.resolve(run, "raw/Core1.txt") == tmp_path / "run" / "raw" / "Core1.txt"
    for rel in ("../x.txt", "raw/../../x.txt", "C:/x.txt"):
        with pytest.raises(StorageOperationError):
            service.save_text(run, rel, "x")
    assert not (tmp_path / "x.txt").exists()


def test_device_names_become_one_file_name():
    from core.log_naming import build_inspection_log_name, parse_inspection_log_name
    from engine.log_manager import LogManager
    when = datetime.datetime(2026, 10, 27, 9, 0, 0)
    assert build_inspection_log_name("Core1", when=when) == "20261027_090000_raw_Core1.txt"
    assert parse_inspection_log_name("20261027_090000_raw_Core1.txt") == ("Core1", "20261027_090000")
    for device in ("../../Startup/evil", "SW:1", "a\\b"):
        for name in (build_inspection_log_name(device, when=when), LogManager._device_filename(device)):
            assert not any(ch in name for ch in "/\\:"), name
    assert LogManager._device_filename("Core1") == "Core1.txt"
    assert LogManager._device_filename("Core1.txt") == "Core1.txt"


def test_report_filename_from_the_screen_stays_in_reports(tmp_path, monkeypatch):
    from engine import inspection_report_builder as builder
    reports = tmp_path / "reports"
    reports.mkdir()

    class FakeWorkbook:
        def save(self, path):
            Path(path).write_bytes(b"xlsx")

        def close(self):
            pass

    monkeypatch.setattr(builder, "build_context", lambda *a, **k: {"inspection_date": "2026-10-27", "devices": []})
    monkeypatch.setattr(builder, "_excel", lambda: type("X", (), {"build_workbook": staticmethod(lambda c, **kw: FakeWorkbook())}))
    monkeypatch.setattr(builder, "reports_dir", lambda c, p: reports)
    monkeypatch.setattr(builder, "save_snapshot", lambda *a, **k: None)
    result = builder.export("고객사", "2026-10", "excel", filename="..\\..\\evil")
    assert Path(result["path"]).parent == reports
    assert result["filename"] == ".._.._evil.xlsx"
    assert not (tmp_path / "evil.xlsx").exists()


class _KnowledgeStub:
    def __init__(self, project_dir):
        self.project_dir = project_dir

    def _paths(self):
        return {"target_state": str(self.project_dir / "target_state.yaml")}


@pytest.fixture
def knowledge(tmp_path):
    from api.knowledge_api import KnowledgeApiMixin
    project = tmp_path / "proj"
    project.mkdir()
    (project / "target_state.yaml").write_text("secret: 1\n", encoding="utf-8")
    sibling = project / "knowledge2"     # 예전 startswith 검사가 통과시키던 '옆 폴더'
    sibling.mkdir()
    (sibling / "secret.md").write_text("옆 폴더", encoding="utf-8")
    api = type("Stub", (_KnowledgeStub, KnowledgeApiMixin), {})(project)
    return api, project


def test_knowledge_docs_round_trip(knowledge):
    api, project = knowledge
    assert api.save_knowledge_doc("stp", "# STP") is True
    assert (project / "knowledge" / "stp.md").read_text(encoding="utf-8") == "# STP"
    assert api.get_knowledge_doc("stp.md") == "# STP"
    assert [d["name"] for d in api.list_knowledge_docs()] == ["stp.md"]
    assert api.delete_knowledge_doc("stp.md") is True
    assert not (project / "knowledge" / "stp.md").exists()


@pytest.mark.parametrize("name", ["../evil", "..\\evil.md", "C:\\evil.md", "sub/evil.md", "../../x.md"])
def test_knowledge_save_rejects_names_outside_the_folder(knowledge, name):
    api, project = knowledge
    assert api.save_knowledge_doc(name, "x") is False
    assert sorted(p.name for p in project.iterdir()) == ["knowledge", "knowledge2", "target_state.yaml"]
    assert os.listdir(project / "knowledge") == []


def test_knowledge_read_and_delete_stay_in_the_folder(knowledge):
    api, project = knowledge
    assert api.get_knowledge_doc("../target_state.yaml") == ""
    assert api.get_knowledge_doc("../knowledge2/secret.md") == ""
    assert api.delete_knowledge_doc("../knowledge2/secret.md") is False
    assert api.delete_knowledge_doc("../target_state.yaml") is False
    assert (project / "knowledge2" / "secret.md").exists()
    assert (project / "target_state.yaml").exists()


# --------------------------------------------------------------------------- 4. 프로젝트 zip

class _FakeWindow:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def create_file_dialog(self, *args, **kwargs):
        self.calls += 1
        return self.result


@pytest.fixture
def project_api(labs, monkeypatch):
    """앱과 같은 조합(고객사 트리 + 프로젝트) — 가져오기는 가져온 것을 고객사 트리에 잇는다(IMPROVEMENT_PLAN 5-3).
    고객사 목록·데이터 폴더는 labs 옆의 임시 폴더."""
    from api import window_ref
    from api.customer_profile_api import CustomerProfileApiMixin
    from api.project_core_api import ProjectCoreApiMixin
    from core.context_cache import workspace_cache
    from core.paths import AppPaths
    from engine import customer_manager as cm
    from engine import project_manager as pm
    original = AppPaths._user_data_root
    AppPaths._user_data_root = labs.parent
    AppPaths.forget_ensured()
    monkeypatch.setattr(cm, "CUSTOMERS_PATH", str(labs.parent / "config" / "customers.yaml"))
    monkeypatch.setattr(pm, "STATE_FILE", str(labs.parent / "config" / "active_project.yaml"))
    workspace_cache.invalidate()

    class Api(CustomerProfileApiMixin, ProjectCoreApiMixin):
        pass

    def use_dialog_result(result):
        window = _FakeWindow(result)
        monkeypatch.setattr(window_ref, "_window", window)
        return window

    yield Api(), use_dialog_result
    workspace_cache.invalidate()
    AppPaths._user_data_root = original
    AppPaths.forget_ensured()


def _write_zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_project_export_and_import_round_trip(project_api, labs, tmp_path):
    api, use_dialog_result = project_api
    (labs / "proj1" / "sub").mkdir()
    (labs / "proj1" / "sub" / "x.yaml").write_text("a: 1\n", encoding="utf-8")
    out = tmp_path / "proj1.zip"
    use_dialog_result(str(out))
    assert api.export_project("proj1") == {"ok": True, "path": str(out)}

    use_dialog_result([str(out)])
    result = api.import_project()
    assert result["ok"] is True and result["project_id"].startswith("proj1_")   # 같은 id 가 있으면 새 id
    # 원본과 같은 고객사·이름 — '(가져옴)'을 붙여 data 폴더를 따로 쓴다(옮기기 자체는 tests/test_profile_move.py)
    assert (result["customer"], result["profile_name"], result["renamed"]) == ("P1", "P1 (가져옴)", True)
    imported = labs / result["project_id"]
    assert (imported / "sub" / "x.yaml").read_text(encoding="utf-8") == "a: 1\n"


@pytest.mark.parametrize("project_id", ["..", "", "../data"])
def test_project_export_rejects_ids_before_opening_the_dialog(project_api, project_id):
    api, use_dialog_result = project_api
    window = use_dialog_result("unused.zip")
    assert "error" in api.export_project(project_id)
    assert window.calls == 0


@pytest.mark.parametrize("members", [
    {"proj9/project_meta.yaml": "a", "proj9/../../evil.txt": "x"},          # zip slip
    {"proj9/project_meta.yaml": "a", "proj9/C:/evil.txt": "x"},             # 드라이브 경로
    {"../evil.txt": "x"},                                                   # 맨 앞 멤버가 밖
    {"_customers/registry.json": "{}"},                                     # 앱 내부 폴더 자리
    {"evil.txt": "x"},                                                      # 프로젝트 폴더가 없음
])
def test_project_import_rejects_unsafe_zips_without_writing(project_api, labs, tmp_path, members):
    api, use_dialog_result = project_api
    use_dialog_result([str(_write_zip(tmp_path / "in.zip", members))])
    before = sorted(p.name for p in labs.iterdir())
    assert "error" in api.import_project()
    assert sorted(p.name for p in labs.iterdir()) == before
    assert not (tmp_path / "evil.txt").exists()
    assert not (labs.parent / "evil.txt").exists()


def test_project_import_rejects_zip_bombs(project_api, labs, tmp_path, monkeypatch):
    from api import project_core_api
    api, use_dialog_result = project_api
    monkeypatch.setattr(project_core_api, "_IMPORT_MAX_BYTES", 1000)
    use_dialog_result([str(_write_zip(tmp_path / "big.zip", {"proj9/big.txt": "0" * 5000}))])
    assert "MB" in api.import_project()["error"]
    assert not (labs / "proj9").exists()
