"""릴리스 점검(tools/release_check) — IMPROVEMENT_PLAN 5-2.

항목마다 무엇을 통과·실패·건너뜀으로 보는지와 그 이유를 본다. 진짜 pytest·벤치마크·EXE 빌드는 여기서 돌리지 않는다
(테스트 안에서 테스트 전체를 다시 돌리게 된다) — 자식 프로세스 자리에 가짜 실행기를 넣는다. EXE 가 쓰는 main.py 의
--self-test 만 소스로 진짜 돌려 본다.
"""
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from tools import release_check as rc

ROOT = Path(__file__).resolve().parent.parent


def done(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeRun:
    """명령에 들어 있는 말 -> 결과(또는 결과를 만드는 함수). 부른 명령을 기억한다."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, cmd, **kwargs):
        cmd = [str(part) for part in cmd]
        self.calls.append((cmd, kwargs))
        line = " ".join(cmd)
        for key, answer in self.answers.items():
            if key in line:
                return answer(cmd, kwargs) if callable(answer) else answer
        raise AssertionError(f"예상하지 않은 명령: {line}")


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


# --------------------------------------------------------------------------- 버전·git

def _release_tree(tmp_path, version="0.6.0", title="## v0.6.0 — 문서 정리", readme_version="0.6.0"):
    _write(tmp_path / "VERSION", f"{version}\n")
    _write(tmp_path / "CHANGELOG.md", f"# 변경 이력\n\n{title}\n\n- 무엇\n\n## v0.5.9 — 예전\n")
    _write(tmp_path / "README.md", f"# AutoCheck (v{readme_version})\n\n> **버전**: v{readme_version}\n")
    return tmp_path


def test_version_must_match_changelog_title_and_readme(tmp_path):
    assert rc.check_version(_release_tree(tmp_path)) == rc.Check("버전", rc.PASS, "v0.6.0")
    unreleased = rc.check_version(_release_tree(tmp_path, title="## 미배포 (v0.6.0 예정) — 4단계"))
    assert unreleased.status == rc.FAIL and "릴리스 제목이 아니다" in unreleased.detail and "미배포" in unreleased.detail
    assert "'## v0.6.0 — …'" in unreleased.detail and "VERSION 을" not in unreleased.detail     # 이미 0.6.0 이다
    ahead = rc.check_version(_release_tree(tmp_path, version="0.5.9", title="## 미배포 (v0.6.0 예정) — 4단계",
                                           readme_version="0.5.9"))
    assert "VERSION 을 0.6.0 로 올리고 그 절의 제목을 '## v0.6.0 — …'로" in ahead.detail
    stale = rc.check_version(_release_tree(tmp_path, title="## v0.5.9 — 예전"))
    assert stale.status == rc.FAIL and "v0.5.9 ≠ VERSION 0.6.0" in stale.detail
    readme = rc.check_version(_release_tree(tmp_path, readme_version="0.5.9"))
    assert readme.status == rc.FAIL and "README 머리의 버전 0.5.9" in readme.detail


def test_git_must_be_clean_and_the_tag_new_or_at_head(tmp_path):
    root = _release_tree(tmp_path)
    clean = {"status --porcelain": done(0, "")}
    dirty = rc.check_git(FakeRun({"status --porcelain": done(0, " M README.md\n?? notes.txt\n")}), root)
    assert dirty.status == rc.FAIL and "커밋하지 않은 변경 2개" in dirty.detail
    broken = rc.check_git(FakeRun({"status --porcelain": done(128, "", "fatal: not a git repository")}), root)
    assert broken.status == rc.FAIL and "git 을 쓸 수 없다" in broken.detail
    new_tag = rc.check_git(FakeRun({**clean, "tag --list v0.6.0": done(0, "")}), root)
    assert new_tag.status == rc.PASS and "git tag v0.6.0" in new_tag.detail
    at_head = rc.check_git(FakeRun({**clean, "tag --list v0.6.0": done(0, "v0.6.0\n"),
                                    "rev-parse v0.6.0": done(0, "abc123\n"), "rev-parse HEAD": done(0, "abc123\n")}), root)
    assert at_head == rc.Check("git", rc.PASS, "작업 트리 깨끗함 — 태그 v0.6.0 = HEAD")
    elsewhere = rc.check_git(FakeRun({**clean, "tag --list v0.6.0": done(0, "v0.6.0\n"),
                                      "rev-parse v0.6.0": done(0, "def4567\n"), "rev-parse HEAD": done(0, "abc123\n")}), root)
    assert elsewhere.status == rc.FAIL and "다른 커밋(def4567)" in elsewhere.detail


# --------------------------------------------------------------------------- 번들 비밀

def test_bundle_check_reads_what_the_spec_bundles(tmp_path):
    current = rc.check_bundle(ROOT)
    assert current.status == rc.PASS and current.detail.startswith("web_ui·config·VERSION")

    _write(tmp_path / "AutoCheck.spec", (ROOT / "AutoCheck.spec").read_text(encoding="utf-8"))
    _write(tmp_path / "web_ui" / "index.html", "<html></html>")
    _write(tmp_path / "VERSION", "0.6.0\n")
    missing = rc.check_bundle(tmp_path)
    assert missing.status == rc.FAIL and "config" in missing.detail
    _write(tmp_path / "config" / "connection.yaml", "ssh:\n  password: hunter2\n")
    leaked = rc.check_bundle(tmp_path)
    assert leaked.status == rc.FAIL and leaked.detail.startswith("1건") and "password" in leaked.detail
    assert "hunter2" not in leaked.detail                                    # 값은 적지 않는다


# --------------------------------------------------------------------------- 테스트·벤치마크

def test_pytest_summary_counts():
    assert rc.pytest_counts("....\n1300 passed in 126.71s (0:02:06)\n") == {"passed": 1300}
    assert rc.pytest_counts("F.s\n2 failed, 1296 passed, 2 skipped, 1 error in 10.00s\n") == \
        {"failed": 2, "passed": 1296, "skipped": 2, "error": 1}
    assert rc.pytest_counts("1 passed, 3 warnings in 0.50s") == {"passed": 1, "warning": 3}
    assert rc.pytest_counts("ImportError while loading conftest") == {}


def test_tests_pass_only_on_windows_with_nothing_failed_or_skipped():
    assert rc.check_tests(FakeRun({}), platform="linux").status == rc.FAIL
    run = FakeRun({"-m pytest tests": done(0, "1300 passed in 1.00s")})
    assert rc.check_tests(run, env={"X": "1"}, platform="win32", node="node") == rc.Check("테스트", rc.PASS, "1300 passed")
    assert run.calls[0][1]["env"] == {"X": "1"}
    skipped = rc.check_tests(FakeRun({"-m pytest": done(0, "1298 passed, 2 skipped in 1.00s")}), platform="win32", node=None)
    assert skipped.status == rc.FAIL and "2 skipped" in skipped.detail and "node" in skipped.detail
    failed = rc.check_tests(FakeRun({"-m pytest": done(1, "1 failed, 1299 passed in 1.00s")}), platform="win32", node="node")
    assert failed == rc.Check("테스트", rc.FAIL, "1 failed, 1299 passed")


def test_bench_outcomes_follow_its_exit_codes():
    from tools import bench_log_analysis as bench
    assert rc.BENCH_EXIT == {bench.EXIT_OK: "ok", bench.EXIT_REGRESSION: "regression",
                             bench.EXIT_NO_BASELINE: "no_baseline", bench.EXIT_INCONCLUSIVE: "inconclusive"}
    ok = rc.check_bench(FakeRun({"bench_log_analysis --check": done(0, "표\n퇴행 없음 (유효 임계값 15.0%).\n")}))
    assert ok == rc.Check("벤치마크", rc.PASS, "퇴행 없음 (유효 임계값 15.0%).")
    assert "--save-baseline" in rc.check_bench(FakeRun({"bench": done(2)})).detail
    assert "판정 불가" in rc.check_bench(FakeRun({"bench": done(3)})).detail
    regressed = rc.check_bench(FakeRun({"bench": done(1, "", "[퇴행] judge_ms: 100 -> 150 (+50.0%)\n\n1개 지표가…")}))
    assert regressed == rc.Check("벤치마크", rc.FAIL, "[퇴행] judge_ms: 100 -> 150 (+50.0%)")


# --------------------------------------------------------------------------- EXE

def test_app_modules_and_the_outside_packages_they_import(tmp_path):
    modules = rc.app_modules(ROOT)
    assert {"api", "engine.workspace_check", "engine.topology_builder", "core.paths", "report.inspection_pdf"} <= set(modules)
    assert not [name for name in modules if name.split(".")[0] in ("tests", "tools")]
    outside = rc.third_party_imports(ROOT)
    assert {"webview", "yaml", "netmiko", "paramiko", "openpyxl"} <= set(outside)
    assert outside["netmiko"] is False                    # try/except 로 감쌌어도 requirements.txt 의 패키지는 필수
    assert not [name for name in outside if name.split(".")[0] in ("json", "os", "engine", "core", "main")]

    _write(tmp_path / "requirements.txt", "pyyaml>=6.0\n")
    _write(tmp_path / "main.py", "import yaml\n")
    _write(tmp_path / "engine" / "__init__.py", "")
    _write(tmp_path / "engine" / "x.py", "\n".join([
        "import json", "try:", "    import fancy_optional", "except ImportError:", "    fancy_optional = None",
        "try:", "    import yaml", "except ImportError:", "    yaml = None",
        "def later():", "    from somepkg.sub import thing", "    return thing"]) + "\n")
    assert rc.app_modules(tmp_path) == ["engine", "engine.x"]
    assert rc.third_party_imports(tmp_path) == {"fancy_optional": True, "somepkg.sub": False, "yaml": False}


def _exe_tree(tmp_path):
    _write(tmp_path / "requirements.txt", "pyyaml>=6.0\n")
    _write(tmp_path / "main.py", "import yaml\n")
    return tmp_path


def _builder(root, guide=True):
    def build(cmd, kwargs):
        exe_dir = root / rc.EXE_DIR
        exe_dir.mkdir(parents=True, exist_ok=True)
        (exe_dir / rc.EXE_NAME).write_bytes(b"MZ")
        if guide:
            (exe_dir / rc.USER_GUIDE).write_text("안내", encoding="utf-8")
        return done(0, "[OK] EXE 빌드 성공!")
    return build


def _self_test(failures=None, missing=(), returncode=0):
    def smoke(cmd, kwargs):
        report = Path(cmd[cmd.index("--self-test") + 1])
        names = Path(cmd[cmd.index("--modules") + 1]).read_text(encoding="utf-8").split()
        report.write_text(json.dumps({"frozen": True, "checked": len(names), "failures": failures or {},
                                      "missing_optional": list(missing)}), encoding="utf-8")
        return done(1 if failures else returncode)
    return smoke


def test_exe_check_builds_then_imports_everything_inside_the_bundle(tmp_path):
    root = _exe_tree(tmp_path / "repo")
    assert rc.check_exe(build=False).status == rc.SKIP
    assert "requirements-build" in rc.check_exe(FakeRun({}), root=root, have_pyinstaller=False).detail

    run = FakeRun({"build_exe.py": _builder(root), "--self-test": _self_test(missing=["fancy"])})
    ok = rc.check_exe(run, env={"AUTOCHECK_HOME": "x"}, root=root, workdir=tmp_path / "work", have_pyinstaller=True)
    assert ok.status == rc.PASS and "1개 import" in ok.detail and "없는 선택 패키지: fancy" in ok.detail
    smoke_cmd, smoke_kwargs = run.calls[1]
    assert smoke_cmd[0] == str(root / rc.EXE_DIR / rc.EXE_NAME) and smoke_kwargs["env"] == {"AUTOCHECK_HOME": "x"}
    assert (tmp_path / "work" / "modules.txt").read_text(encoding="utf-8") == "yaml\n"

    broken = rc.check_exe(FakeRun({"build_exe.py": done(1, "", "PyInstaller 오류")}), root=tmp_path / "empty",
                          workdir=tmp_path / "w2", have_pyinstaller=True)
    assert broken.status == rc.FAIL and "빌드 실패" in broken.detail and "PyInstaller 오류" in broken.detail
    no_guide = rc.check_exe(FakeRun({"build_exe.py": _builder(tmp_path / "g", guide=False)}), root=tmp_path / "g",
                            workdir=tmp_path / "w3", have_pyinstaller=True)
    assert no_guide.status == rc.FAIL and rc.USER_GUIDE in no_guide.detail
    failing = rc.check_exe(FakeRun({"build_exe.py": _builder(root),
                                    "--self-test": _self_test({"netmiko": "ModuleNotFoundError: No module named 'unittest'"})}),
                           root=root, workdir=tmp_path / "w4", have_pyinstaller=True)
    assert failing.status == rc.FAIL and "import 실패 1개" in failing.detail and "unittest" in failing.detail
    silent = rc.check_exe(FakeRun({"build_exe.py": _builder(root), "--self-test": done(1, "", "Traceback …")}),
                          root=root, workdir=tmp_path / "w5", have_pyinstaller=True)
    assert silent.status == rc.FAIL and "결과가 없다" in silent.detail


def test_main_self_test_imports_the_listed_modules_without_a_window(tmp_path):
    """EXE 가 부르는 바로 그 모드 — 소스의 main.py 로. 데이터 폴더는 conftest 의 AUTOCHECK_HOME(물려받는다)."""
    def self_test(lines):
        modules, report = tmp_path / "modules.txt", tmp_path / "report.json"
        modules.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = subprocess.run([sys.executable, str(ROOT / "main.py"), "--self-test", str(report), "--modules",
                                 str(modules)], cwd=str(ROOT), capture_output=True, timeout=180,
                                env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        return result.returncode, json.loads(report.read_text(encoding="utf-8"))

    code, report = self_test(["core.paths", "engine.workspace_check", "?surely_not_an_installed_package"])
    assert code == 0 and report == {"frozen": False, "checked": 3, "failures": {},
                                    "missing_optional": ["surely_not_an_installed_package"]}
    code, report = self_test(["core.paths", "surely_missing_required_package"])
    assert code == 1 and list(report["failures"]) == ["surely_missing_required_package"]
    assert report["failures"]["surely_missing_required_package"].startswith("ModuleNotFoundError")


# --------------------------------------------------------------------------- 전체

def test_verdict_and_the_report(monkeypatch):
    assert rc.verdict([rc.Check("a", rc.PASS), rc.Check("b", rc.PASS)])[0] == 0
    assert rc.verdict([rc.Check("a", rc.PASS), rc.Check("b", rc.SKIP)]) == (2, "실패는 없지만 확인하지 않은 항목이 있다: b")
    assert rc.verdict([rc.Check("a", rc.FAIL), rc.Check("b", rc.SKIP)]) == (1, "릴리스하면 안 된다 — 실패: a")

    seen = {}

    def no_call(*args, **kwargs):
        raise AssertionError("건너뛴 항목을 돌렸다")

    def exe(env=None, build=True, workdir=None):
        seen["home"], seen["build"] = Path(env["AUTOCHECK_HOME"]), build
        assert seen["home"].parent == Path(workdir) and Path(workdir).is_dir()
        return rc.Check("EXE", rc.SKIP, "--no-build")

    monkeypatch.setattr(rc, "check_version", lambda: rc.Check("버전", rc.PASS, "v0.6.0"))
    monkeypatch.setattr(rc, "check_git", lambda: rc.Check("git", rc.FAIL, "커밋하지 않은 변경 1개"))
    monkeypatch.setattr(rc, "check_bundle", lambda: rc.Check("번들 비밀", rc.PASS, "비밀 없음"))
    monkeypatch.setattr(rc, "check_tests", no_call)
    monkeypatch.setattr(rc, "check_bench", no_call)
    monkeypatch.setattr(rc, "check_exe", exe)
    out = io.StringIO()
    assert rc.main(["--no-tests", "--no-bench", "--no-build"], out=out) == 1
    lines = out.getvalue().splitlines()
    assert lines[0].startswith("릴리스 점검 — AutoCheck v")
    assert [line.split("]")[0].strip() for line in lines[1:7]] == ["[통과", "[실패", "[통과", "[건너뜀", "[건너뜀", "[건너뜀"]
    assert lines[1] == "  [통과]   버전       v0.6.0" and lines[3] == "  [통과]   번들 비밀  비밀 없음"   # 한글 두 칸으로 맞춘다
    assert lines[-1] == "결과: 릴리스하면 안 된다 — 실패: git"
    assert seen["build"] is False and not seen["home"].parent.exists()        # 임시 데이터 폴더는 지운다
