"""릴리스 점검 — 릴리스하기 전에 이것 하나를 돌린다(IMPROVEMENT_PLAN 5-2, docs/DEVELOPER_GUIDE.md 'Build & release').

  버전        VERSION = CHANGELOG 맨 위 절의 제목 = README 머리의 버전
  git         작업 트리가 깨끗하고, 태그 vX.Y.Z 가 아직 없거나 HEAD 를 가리킨다(있는데 다른 커밋이면 버전을 안 올렸다)
  번들 비밀   EXE 에 담길 파일에 비밀이 없다(tools/bundle_secret_check — 빌드도 같은 검사로 멈춘다)
  테스트      Windows 에서 pytest 전체 — 0 failed, 0 skipped(node 가 없으면 JS 테스트가 건너뛰어진다)
  벤치마크    python -m tools.bench_log_analysis --check — 이 PC 의 기준선 대비 퇴행 없음
  EXE         build_exe.py 로 빌드하고 AutoCheck.exe --self-test 로 번들 안에서 앱의 모든 모듈과 앱이 import 하는 바깥
              패키지를 하나씩 import 해 본다 — spec 의 excludes(unittest 등)가 의존 패키지를 깨지 않는지, 번들에 빠진
              모듈이 없는지(1-6 에서 확인하지 못한 것). 실행방법.txt 가 EXE 옆에 있는지도 본다

  python -m tools.release_check                      # 전부 — 테스트·벤치마크·EXE 빌드로 몇 분 걸린다
  python -m tools.release_check --no-build           # EXE 빌드·시험을 건너뛴다
  python -m tools.release_check --no-tests --no-bench --no-build   # 빠른 것만

종료 코드: 0 = 전부 통과(릴리스해도 된다), 1 = 실패가 있다, 2 = 실패는 없지만 건너뛴 항목이 있다.
자식 프로세스(테스트·벤치마크·EXE)는 모두 임시 데이터 폴더(AUTOCHECK_HOME)에서 돈다 — 사용자의 데이터 폴더를 건드리지
않는다. 벤치마크 기준선(tools/bench_baseline.json)은 PC 마다 다르므로 만들어 주지 않는다 — 기준이 될 커밋에서
`python -m tools.bench_log_analysis --save-baseline` 으로 한 번 만든다.
"""
import argparse
import ast
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_PACKAGES = ("api", "engine", "parsers", "core", "report", "ai_analysis")   # AutoCheck.spec 의 collect_submodules
EXE_DIR = Path("dist") / "AutoCheck"
EXE_NAME = "AutoCheck.exe"
USER_GUIDE = "실행방법.txt"
PASS, FAIL, SKIP = "통과", "실패", "건너뜀"
# tools/bench_log_analysis 의 종료 코드(EXIT_OK, EXIT_REGRESSION, EXIT_NO_BASELINE, EXIT_INCONCLUSIVE) — 그 모듈을
# import 하면 판정 엔진까지 올라와서 숫자만 옮겨 둔다(테스트가 같은지 본다)
BENCH_EXIT = {0: "ok", 1: "regression", 2: "no_baseline", 3: "inconclusive"}
_VERSION_RE = re.compile(r"\bv(\d+\.\d+\.\d+)\b")


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


def run_process(cmd, *, env=None, timeout=None, cwd=None):
    """자식 프로세스 — 출력은 UTF-8 로 읽는다(text=True 는 시스템 코드 페이지(cp949)로 읽어 한글에서 깨진다)."""
    return subprocess.run([str(part) for part in cmd], cwd=str(cwd or ROOT), env=env, timeout=timeout,
                          capture_output=True, encoding="utf-8", errors="replace")


def _tail(text, lines=6):
    return " / ".join(line.strip() for line in (text or "").strip().splitlines()[-lines:] if line.strip())


# ---------------------------------------------------------------------------- 버전·git
def check_version(root=ROOT):
    root = Path(root)
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    title = next((line for line in changelog.splitlines() if line.startswith("## ")), "")
    problems = []
    match = re.match(r"##\s+v?(\d+\.\d+\.\d+)\b", title)
    if not match:
        planned = next(iter(_VERSION_RE.findall(title)), version)       # '## 미배포 (v0.6.0 예정)' 의 예정 버전
        bump = f"VERSION 을 {planned} 로 올리고 " if planned != version else ""
        problems.append(f"CHANGELOG 맨 위 절이 릴리스 제목이 아니다('{title[3:40]}…') — 릴리스할 때 {bump}"
                        f"그 절의 제목을 '## v{planned} — …'로 바꾼다")
    elif match.group(1) != version:
        problems.append(f"CHANGELOG 맨 위 v{match.group(1)} ≠ VERSION {version}")
    head = "\n".join((root / "README.md").read_text(encoding="utf-8").splitlines()[:5])
    readme = set(_VERSION_RE.findall(head))
    if readme != {version}:
        problems.append(f"README 머리의 버전 {', '.join(sorted(readme)) or '없음'} ≠ VERSION {version}")
    return Check("버전", FAIL, "; ".join(problems)) if problems else Check("버전", PASS, f"v{version}")


def check_git(run=run_process, root=ROOT):
    version = (Path(root) / "VERSION").read_text(encoding="utf-8").strip()
    status = run(["git", "status", "--porcelain"], cwd=root)
    if status.returncode != 0:
        return Check("git", FAIL, f"git 을 쓸 수 없다 — {_tail(status.stderr, 2)}")
    changes = [line for line in status.stdout.splitlines() if line.strip()]
    if changes:
        return Check("git", FAIL, f"커밋하지 않은 변경 {len(changes)}개 — 릴리스는 커밋된 것만")
    tag = f"v{version}"
    if not run(["git", "tag", "--list", tag], cwd=root).stdout.strip():
        return Check("git", PASS, f"작업 트리 깨끗함 — 태그 {tag} 는 아직 없다(점검이 끝나면 git tag {tag})")
    tagged = run(["git", "rev-parse", f"{tag}^{{commit}}"], cwd=root).stdout.strip()
    head = run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    if tagged != head:
        return Check("git", FAIL, f"태그 {tag} 가 다른 커밋({tagged[:7]})을 가리킨다 — VERSION 을 올리지 않았다")
    return Check("git", PASS, f"작업 트리 깨끗함 — 태그 {tag} = HEAD")


# ---------------------------------------------------------------------------- 번들 비밀
def check_bundle(root=ROOT):
    from tools.bundle_secret_check import find_bundle_secrets, spec_bundle_sources
    fixed, optional = spec_bundle_sources((Path(root) / "AutoCheck.spec").read_text(encoding="utf-8"))
    sources = [Path(root) / name for name in fixed] + [Path(root) / name for name in optional
                                                        if (Path(root) / name).exists()]
    missing = [str(path.name) for path in sources if not path.exists()]
    if not fixed or missing:
        return Check("번들 비밀", FAIL, f"spec 이 담는 것을 찾지 못했다: {', '.join(missing) or '목록이 비었다'}")
    problems = find_bundle_secrets(sources)
    if problems:
        return Check("번들 비밀", FAIL, f"{len(problems)}건 — " + "; ".join(problems[:3]))
    return Check("번들 비밀", PASS, f"{'·'.join(path.name for path in sources)} 에 비밀 없음")


# ---------------------------------------------------------------------------- 테스트·벤치마크
def pytest_counts(output):
    """pytest -q 의 마지막 요약 줄 -> {'passed': n, 'failed': n, 'skipped': n, 'error': n, …}."""
    summary = next((line for line in reversed((output or "").splitlines()) if re.search(r"\bin [\d.]+s\b", line)), "")
    counts = {}
    for number, word in re.findall(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|deselected|warnings?)",
                                    summary):
        counts[word.rstrip("s") if word in ("errors", "warnings") else word] = int(number)
    return counts


def check_tests(run=run_process, env=None, platform=sys.platform, node=None):
    if platform != "win32":
        return Check("테스트", FAIL, "Windows 에서 돌려야 한다(대상 플랫폼이다)")
    result = run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"], env=env, timeout=3600)
    counts = pytest_counts(result.stdout)
    if result.returncode == 0 and counts.get("passed") and not any(counts.get(k) for k in ("failed", "error", "skipped")):
        return Check("테스트", PASS, f"{counts['passed']} passed")
    detail = ", ".join(f"{value} {key}" for key, value in counts.items()) or _tail(result.stdout + result.stderr)
    if counts.get("skipped") and not node:
        detail += " — node 가 없어 JS 테스트가 건너뛰어졌다(winget install OpenJS.NodeJS.LTS)"
    return Check("테스트", FAIL, detail)


def check_bench(run=run_process, env=None):
    result = run([sys.executable, "-m", "tools.bench_log_analysis", "--check"], env=env, timeout=1800)
    outcome = BENCH_EXIT.get(result.returncode)
    if outcome == "ok":
        return Check("벤치마크", PASS, next((line for line in result.stdout.splitlines() if "퇴행 없음" in line),
                                        "퇴행 없음").strip())
    if outcome == "no_baseline":
        return Check("벤치마크", FAIL, "이 PC 의 기준선이 없다 — 기준이 될 커밋에서 "
                                       "python -m tools.bench_log_analysis --save-baseline 으로 한 번 만든다")
    if outcome == "inconclusive":
        return Check("벤치마크", FAIL, "판정 불가 — 측정 편차가 크다. 다른 작업을 멈추고 다시 돌린다")
    regressions = [line.strip() for line in result.stderr.splitlines() if line.startswith("[퇴행]")]
    return Check("벤치마크", FAIL, "; ".join(regressions) or _tail(result.stdout + result.stderr))


# ---------------------------------------------------------------------------- EXE
def app_modules(root=ROOT):
    """앱 모듈 이름(AutoCheck.spec 이 collect_submodules 로 담는 패키지들의 .py 전부)."""
    names = []
    for package in APP_PACKAGES:
        for path in sorted((Path(root) / package).rglob("*.py")):
            parts = path.relative_to(root).with_suffix("").parts
            names.append(".".join(parts[:-1] if parts[-1] == "__init__" else parts))
    return names


def _catches_import_error(handler):
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(t, ast.Name) and t.id in ("ImportError", "ModuleNotFoundError", "Exception") for t in types)


def _required_top_modules(root=ROOT):
    """requirements.txt 의 런타임 패키지가 설치하는 최상위 모듈 이름(pywebview -> webview, pyyaml -> yaml …)."""
    from core.requirements_check import parse_requirements
    wanted = {name.lower() for name, _ in parse_requirements((Path(root) / "requirements.txt").read_text(encoding="utf-8"))}
    return {top for top, dists in importlib.metadata.packages_distributions().items()
            if any(dist.lower() in wanted for dist in dists)}


def third_party_imports(root=ROOT):
    """앱 코드(main.py 와 앱 패키지)가 import 하는 바깥 모듈 -> optional 여부. 함수 안의 늦은 import 까지 본다 — 창을 띄우는
    것만으로는 닿지 않는 것들이다. try/except ImportError 안에서만 import 하는 것은 선택(없어도 앱이 돈다) — 단
    requirements.txt 의 패키지는 감싸 두었어도 필수다(수집기는 netmiko 가 없으면 안내만 하고 아무것도 수집하지 못한다)."""
    local = set(APP_PACKAGES) | {"main", "tools", "tests"}
    required = _required_top_modules(root)
    found = {}

    def visit(node, optional):
        if isinstance(node, ast.Try):
            guarded = optional or any(_catches_import_error(h) for h in node.handlers)
            for child in node.body:
                visit(child, guarded)
            for child in node.handlers + node.orelse + node.finalbody:
                visit(child, optional)
            return
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        for name in names:
            top = name.split(".")[0]
            if top not in local and top not in sys.stdlib_module_names:
                found[name] = found.get(name, True) and optional and top not in required
        for child in ast.iter_child_nodes(node):
            visit(child, optional)

    files = [Path(root) / "main.py"] + [p for package in APP_PACKAGES for p in sorted((Path(root) / package).rglob("*.py"))]
    for path in files:
        visit(ast.parse(path.read_text(encoding="utf-8")), False)
    return dict(sorted(found.items()))


def check_exe(run=run_process, env=None, build=True, root=ROOT, workdir=None, have_pyinstaller=None):
    if not build:
        return Check("EXE", SKIP, "--no-build — 빌드·번들 import 시험을 하지 않았다")
    if have_pyinstaller is None:
        have_pyinstaller = importlib.util.find_spec("PyInstaller") is not None
    if not have_pyinstaller:
        return Check("EXE", FAIL, "PyInstaller 가 없다 — pip install -r requirements-build.txt")
    built = run([sys.executable, "build_exe.py"], env=env, timeout=3600, cwd=root)
    exe = Path(root) / EXE_DIR / EXE_NAME
    if built.returncode != 0 or not exe.is_file():
        return Check("EXE", FAIL, f"빌드 실패 — {_tail(built.stdout + built.stderr)}")
    if not (exe.parent / USER_GUIDE).is_file():
        return Check("EXE", FAIL, f"{USER_GUIDE} 가 {EXE_NAME} 옆에 없다(build_exe.py 가 복사한다)")
    workdir = Path(workdir or tempfile.mkdtemp(prefix="autocheck_selftest_"))
    workdir.mkdir(parents=True, exist_ok=True)
    modules = workdir / "modules.txt"
    third = third_party_imports(root)
    modules.write_text("\n".join(app_modules(root) + [("?" if optional else "") + name
                                                       for name, optional in third.items()]) + "\n", encoding="utf-8")
    report = workdir / "self_test.json"
    smoke = run([exe, "--self-test", report, "--modules", modules], env=env, timeout=600, cwd=root)
    data = json.loads(report.read_text(encoding="utf-8")) if report.is_file() else None
    if not data:
        return Check("EXE", FAIL, f"--self-test 결과가 없다(종료 코드 {smoke.returncode}) — {_tail(smoke.stderr)}")
    if smoke.returncode != 0 or data.get("failures"):
        failures = data.get("failures") or {}
        shown = "; ".join(f"{name}: {why}" for name, why in list(failures.items())[:5])
        return Check("EXE", FAIL, f"번들 안에서 import 실패 {len(failures)}개 — {shown}")
    detail = f"번들 안에서 {data['checked']}개 import(앱 모듈 + 바깥 패키지 {len(third)}개)"
    if data.get("missing_optional"):
        detail += f", 없는 선택 패키지: {', '.join(data['missing_optional'])}"
    return Check("EXE", PASS, detail)


# ---------------------------------------------------------------------------- 실행
def _pad(text, width):
    """한글은 두 칸을 차지한다 — 이름 칸을 화면 너비로 맞춘다."""
    used = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(width - used, 0)


def verdict(checks):
    """(종료 코드, 한 줄 결론)."""
    failed = [c.name for c in checks if c.status == FAIL]
    skipped = [c.name for c in checks if c.status == SKIP]
    if failed:
        return 1, f"릴리스하면 안 된다 — 실패: {', '.join(failed)}"
    if skipped:
        return 2, f"실패는 없지만 확인하지 않은 항목이 있다: {', '.join(skipped)}"
    return 0, "릴리스해도 된다 — 전부 통과"


def main(argv=None, out=None):
    parser = argparse.ArgumentParser(description="릴리스 점검(버전·git·번들 비밀·테스트·벤치마크·EXE)")
    parser.add_argument("--no-tests", action="store_true", help="pytest 를 건너뛴다")
    parser.add_argument("--no-bench", action="store_true", help="벤치마크를 건너뛴다")
    parser.add_argument("--no-build", action="store_true", help="EXE 빌드·번들 import 시험을 건너뛴다")
    args = parser.parse_args(argv)
    out = out or sys.stdout

    # 자식 프로세스가 사용자의 실제 데이터 폴더를 만들거나 고치지 않게 — 테스트는 conftest 가 또 임시 폴더를 잡는다
    scratch = Path(tempfile.mkdtemp(prefix="autocheck_release_"))
    env = dict(os.environ, AUTOCHECK_HOME=str(scratch / "home"), PYTHONIOENCODING="utf-8")
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    print(f"릴리스 점검 — AutoCheck v{version}", file=out, flush=True)
    steps = [
        lambda: check_version(),
        lambda: check_git(),
        lambda: check_bundle(),
        lambda: Check("테스트", SKIP, "--no-tests") if args.no_tests
        else check_tests(env=env, node=shutil.which("node")),
        lambda: Check("벤치마크", SKIP, "--no-bench") if args.no_bench else check_bench(env=env),
        lambda: check_exe(env=env, build=not args.no_build, workdir=scratch),
    ]
    checks = []
    try:
        for step in steps:
            check = step()
            checks.append(check)
            print(f"  {_pad('[' + check.status + ']', 8)} {_pad(check.name, 10)} {check.detail}", file=out, flush=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    code, message = verdict(checks)
    print(f"결과: {message}", file=out, flush=True)
    return code


if __name__ == "__main__":
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    sys.exit(main())
