"""실행 환경 점검(core/requirements_check.py)과 의존성 목록 — IMPROVEMENT_PLAN 0-5.

예전 main.py 는 빠진 패키지를 발견하면 실행할 때마다 pip install 을 자동으로 돌렸고, 버전
조건(>=)은 보지 않았다. 이제는 안내하고 멈추며, 자동 설치는 AUTOCHECK_AUTO_INSTALL=1 일 때만 한다.
"""
from pathlib import Path

import pytest

from core import requirements_check as rc
from tools import lock_requirements

ROOT = Path(__file__).resolve().parent.parent

REQS = """
# 주석
pywebview>=4.0.0
pyyaml>=6.0   # 줄 끝 주석
netmiko
-r other.txt
"""


def _req_file(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_bytes(REQS.encode("utf-8"))
    return str(path)


def test_parse_requirements_reads_names_and_minimums():
    assert rc.parse_requirements(REQS) == [("pywebview", "4.0.0"), ("pyyaml", "6.0"), ("netmiko", None)]


@pytest.mark.parametrize("version, expected", [
    ("3.1.5", (3, 1, 5)), ("2.2.0.post1", (2, 2, 0)), ("1.5.0rc1", (1, 5, 0)), ("6.0", (6, 0)),
])
def test_version_tuple(version, expected):
    assert rc.version_tuple(version) == expected


def test_find_problems_reports_missing_and_outdated():
    installed = {"pywebview": "3.9.0", "pyyaml": "6.0.3"}
    missing, outdated = rc.find_problems(rc.parse_requirements(REQS), installed.get)
    assert missing == ["netmiko"]
    assert outdated == [("pywebview", "3.9.0", "4.0.0")]


def test_problems_stop_with_install_hint_and_never_run_pip(tmp_path):
    """기본 동작은 안내 후 종료 — 사용자 PC 에서 몰래 pip install 을 돌리지 않는다."""
    calls = []
    with pytest.raises(SystemExit) as exc:
        rc.ensure_requirements(_req_file(tmp_path), version_of={}.get, environ={}, run=calls.append)
    assert calls == [], "자동 설치 옵션 없이 pip 를 돌렸다"
    message = str(exc.value.code)
    assert message.index("netmiko") < message.index("pip install -r"), "무엇이 문제인지가 먼저 나와야 한다"


def test_auto_install_env_runs_pip_and_continues(tmp_path):
    calls = []

    class Finished:
        returncode = 0

    def fake_run(cmd):
        calls.append(cmd)
        return Finished()

    rc.ensure_requirements(_req_file(tmp_path), version_of={}.get,
                           environ={rc.AUTO_INSTALL_ENV: "1"}, run=fake_run)
    assert len(calls) == 1
    assert calls[0][1:5] == ["-m", "pip", "install", "-r"]


def test_nothing_happens_when_all_satisfied(tmp_path):
    installed = {"pywebview": "6.2.1", "pyyaml": "6.0.3", "netmiko": "4.8.0"}
    rc.ensure_requirements(_req_file(tmp_path), version_of=installed.get, environ={}, run=None)


@pytest.mark.parametrize("version", [(3, 9, 18), (3, 11, 9)])
def test_old_python_is_rejected(version):
    with pytest.raises(SystemExit):
        rc.check_python_version(version)


def test_current_python_is_accepted():
    rc.check_python_version()


def test_runtime_requirements_exclude_build_tools():
    """빌드 도구는 requirements-build.txt 에만 둔다 — main.py 가 검사하는 목록에 섞이면
    최종 사용자 PC 에 pyinstaller 를 요구하게 된다."""
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    names = {name.lower() for name, _ in rc.parse_requirements(text)}
    assert names and not names & {"pyinstaller", "pillow"}


def test_lock_covers_every_runtime_requirement():
    """requirements.lock 이 requirements.txt 의 모든 패키지를 고정하고 있어야 한다
    (requirements.txt 를 바꾸고 lock 을 다시 만들지 않으면 실패한다)."""
    from packaging.utils import canonicalize_name

    runtime = lock_requirements.top_level_names((ROOT / "requirements.txt").read_text(encoding="utf-8"))
    pinned = {
        canonicalize_name(line.split("==", 1)[0])
        for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if "==" in line and not line.startswith("#")
    }
    assert {canonicalize_name(name) for name in runtime} <= pinned
