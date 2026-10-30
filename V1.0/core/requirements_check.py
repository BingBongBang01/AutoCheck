"""main.py 시작 전 실행 환경 점검 — Python 버전과 requirements.txt 의 패키지.

의존 패키지가 하나도 없는 PC 에서도 import 돼야 하므로 표준 라이브러리만 쓴다.

예전에는 빠진 패키지를 발견하면 실행할 때마다 pip install 을 자동으로 돌렸다. 사내망·비관리자
PC 에서는 그 설치가 실패하거나 오래 걸렸고, 검증되지 않은 최신판이 깔렸으며, 버전 조건(>=)은
보지 않았다. 이제는 무엇이 빠졌거나 낮은지와 설치 명령을 알려 주고 멈춘다. 예전처럼 자동
설치를 원하면 환경변수 AUTOCHECK_AUTO_INSTALL=1 을 켠다(개발용).
"""
import importlib.metadata
import os
import re
import subprocess
import sys

# engine/topology_svg.py 가 PEP 701 f-string(3.12+)을 쓴다. 그 아래 버전에서는 구성도 탭을 여는
# 순간 SyntaxError 로 죽으므로, 시작할 때 이유를 알리고 멈춘다.
MIN_PYTHON = (3, 12)
AUTO_INSTALL_ENV = "AUTOCHECK_AUTO_INSTALL"

_NAME_SPLIT_RE = re.compile(r"[<>=!~;\[\s]")
_MIN_VERSION_RE = re.compile(r">=\s*([0-9][0-9A-Za-z.\-]*)")


def check_python_version(version_info=None):
    """지원하지 않는 Python 이면 안내하고 종료한다(SystemExit)."""
    current = tuple(version_info or sys.version_info)[:3]
    if current[:2] < MIN_PYTHON:
        need = ".".join(map(str, MIN_PYTHON))
        have = ".".join(map(str, current))
        sys.exit(f"[!] AutoCheck 는 Python {need} 이상이 필요합니다(현재 {have}). "
                 "python.org 에서 새 버전을 설치한 뒤 다시 실행하세요.")


def parse_requirements(text):
    """requirements.txt 내용 -> [(패키지명, 최소버전 또는 None)].
    주석·빈 줄·옵션 줄(-r 등)은 건너뛴다. 버전 조건은 '>=' 만 본다(이 프로젝트가 쓰는 형식)."""
    result = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = _NAME_SPLIT_RE.split(line, 1)[0].strip()
        if not name:
            continue
        match = _MIN_VERSION_RE.search(line)
        result.append((name, match.group(1) if match else None))
    return result


def version_tuple(version):
    """'3.1.5' -> (3, 1, 5). 앞쪽 숫자 마디만 본다('1.5.0rc1' -> (1, 5, 0)) — 최소 버전 비교용."""
    parts = []
    for piece in str(version).split("."):
        match = re.match(r"\d+", piece)
        if not match:
            break
        parts.append(int(match.group()))
        if match.end() != len(piece):
            break
    return tuple(parts)


def _installed_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def find_problems(requirements, version_of=_installed_version):
    """설치되지 않았거나 최소 버전보다 낮은 패키지 -> (missing, outdated).
    outdated 의 원소는 (이름, 설치된 버전, 필요한 최소 버전)."""
    missing, outdated = [], []
    for name, minimum in requirements:
        installed = version_of(name)
        if installed is None:
            missing.append(name)
        elif minimum and version_tuple(installed) < version_tuple(minimum):
            outdated.append((name, installed, minimum))
    return missing, outdated


def ensure_requirements(req_path, version_of=_installed_version, environ=None, run=subprocess.run):
    """requirements.txt 를 검사한다. 문제가 없으면 조용히 돌아온다.

    문제가 있으면 기본은 안내 후 종료(SystemExit). AUTOCHECK_AUTO_INSTALL=1 이면 pip install 을
    돌린 뒤 계속 진행한다. EXE(frozen)에서는 패키지가 번들돼 있으므로 검사하지 않는다."""
    if getattr(sys, "frozen", False) or not os.path.exists(req_path):
        return
    environ = os.environ if environ is None else environ
    try:
        with open(req_path, encoding="utf-8") as f:
            requirements = parse_requirements(f.read())
    except OSError as exc:
        print(f"[!] requirements.txt 를 읽지 못했습니다(검사 없이 진행): {exc}")
        return

    missing, outdated = find_problems(requirements, version_of)
    if not missing and not outdated:
        return
    # 안내는 한 덩어리로 낸다 — print(stdout)와 sys.exit(stderr)로 나눠 쓰면 버퍼링 차이로
    # '설치 명령'이 '무엇이 문제인지'보다 먼저 찍히는 콘솔이 있다.
    lines = []
    if missing:
        lines.append(f"[안내] 설치되지 않은 패키지: {', '.join(missing)}")
    lines.extend(f"[안내] 버전이 낮은 패키지: {name} {have} (필요: {need} 이상)"
                 for name, have, need in outdated)

    install_cmd = [sys.executable, "-m", "pip", "install", "-r", req_path]
    if environ.get(AUTO_INSTALL_ENV) == "1":
        lines.append(f"[안내] {AUTO_INSTALL_ENV}=1 — requirements.txt 자동 설치를 진행합니다...")
        print("\n".join(lines), flush=True)
        result = run(install_cmd)
        if result.returncode != 0:
            print(f"[!] pip install 반환 코드: {result.returncode}. 실행을 시도합니다.")
        return

    lines += [
        "[안내] 아래 명령으로 설치한 뒤 다시 실행하세요:",
        f"    {subprocess.list2cmdline(install_cmd)}",
        f"(실행할 때마다 자동 설치하려면 환경변수 {AUTO_INSTALL_ENV}=1 을 설정)",
    ]
    sys.exit("\n".join(lines))
