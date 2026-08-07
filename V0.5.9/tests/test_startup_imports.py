"""시작 경로가 무거운 의존성을 끌어오지 않는지 — OPTIMIZATION_PLAN 1-1 의 회귀 방지.

pandas / paramiko / openpyxl 은 각각 665 / 339 / 269 ms 를 쓴다(이전 측정(Windows)).
셋 다 실제로는 특정 기능에서만 필요한데 모듈 최상단 import 때문에 앱을 켤 때마다 로드됐다:

    api/base.py                  -> engine/command_catalog.py    -> pandas
    api/terminal_session_api.py  -> engine/ssh_client.py          -> paramiko
    api/inspection_report_api.py -> engine/inspection_report_builder.py
                                 -> report/inspection_excel.py    -> openpyxl

이 테스트는 그 체인이 되살아나면 실패한다. 누군가 편의를 위해 최상단으로 import 를 올리는
것은 자연스러운 실수이고, 그러면 이 항목의 이득(약 1.27초)이 조용히 사라진다.

주의: 이미 다른 테스트가 import 해 둔 모듈이 sys.modules 에 남아 있으면 오탐이 나므로,
검사는 **자식 프로세스**에서 새 인터프리터로 수행한다.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# main.py 가 Api 클래스를 합성할 때 import 하는 믹스인 전체.
API_MIXINS = [
    "api.base", "api.project_api", "api.dashboard_api", "api.grade_api",
    "api.report_api", "api.inspection_report_api", "api.catalog_api",
    "api.inventory_api", "api.connection_api", "api.knowledge_api",
    "api.settings_api", "api.terminal_api", "api.log_viewer_api",
    "api.masking_api", "api.logs_api", "api.workspace_api",
]

# 시작 경로에 실려서는 안 되는 모듈.
FORBIDDEN_AT_STARTUP = ["pandas", "paramiko", "openpyxl"]


def _run(code):
    """깨끗한 인터프리터에서 코드를 돌려 (stdout, returncode) 를 돌려준다."""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    return completed.stdout.strip(), completed.stderr.strip(), completed.returncode


def test_api_mixins_import_without_heavy_dependencies():
    """16개 믹스인을 전부 import 해도 pandas/paramiko/openpyxl 이 로드되지 않아야 한다."""
    code = (
        "import sys, importlib\n"
        f"for name in {API_MIXINS!r}:\n"
        "    importlib.import_module(name)\n"
        f"loaded = [m for m in {FORBIDDEN_AT_STARTUP!r} if m in sys.modules]\n"
        "print(','.join(loaded))\n"
    )
    stdout, stderr, code_returned = _run(code)
    assert code_returned == 0, f"믹스인 import 실패:\n{stderr}"
    assert stdout == "", f"시작 경로가 무거운 모듈을 끌어왔다: {stdout}"


@pytest.mark.parametrize("module", API_MIXINS)
def test_each_mixin_imports_standalone(module):
    """믹스인 하나씩 단독 import — 어느 파일이 체인을 되살렸는지 바로 알 수 있게."""
    code = (
        f"import importlib, sys\n"
        f"importlib.import_module({module!r})\n"
        f"print(','.join(m for m in {FORBIDDEN_AT_STARTUP!r} if m in sys.modules))\n"
    )
    stdout, stderr, code_returned = _run(code)
    assert code_returned == 0, f"{module} import 실패:\n{stderr}"
    assert stdout == "", f"{module} 가 무거운 모듈을 끌어왔다: {stdout}"


def test_heavy_modules_are_still_reachable_when_needed():
    """지연 import 가 '기능을 없앤 것'이 아님을 확인 — 진입점이 그대로 살아있다.

    실제 호출은 하지 않는다(그러려면 패키지가 설치돼 있어야 한다). 확인하는 것은
    함수/속성이 여전히 존재하고, 호출 시점에 import 를 시도하는 구조인지다.
    """
    code = (
        "from engine import ssh_client, command_catalog\n"
        "from engine import inspection_report_builder as builder\n"
        "assert callable(ssh_client.connect)\n"
        "assert callable(ssh_client.load_private_key)\n"
        "assert callable(ssh_client._key_classes)\n"
        "assert callable(builder._excel)\n"
        "assert callable(command_catalog.load_catalog)\n"
        "print('ok')\n"
    )
    stdout, stderr, code_returned = _run(code)
    assert code_returned == 0, stderr
    assert stdout == "ok"


def test_status_constants_have_no_openpyxl_dependency():
    """판정 상태 문자열만 쓰려는 쪽이 openpyxl 을 끌어오지 않아야 한다.

    이 분리가 1-1 의 openpyxl 절감을 가능하게 한 핵심이다 — 원래 STATUS_OK 는
    report/inspection_excel.py 안에 있어서 "정상"이라는 문자열 하나 때문에 openpyxl 전체가
    로드됐다.
    """
    code = (
        "import sys\n"
        "from report.inspection_status import STATUS_OK, STATUS_WARN, STATUS_NA, "
        "STATUS_SKIP, STATUS_UNREACHABLE, ALL_STATUSES, NOT_JUDGED_STATUSES\n"
        "assert STATUS_OK == '정상'\n"
        "assert ALL_STATUSES == (STATUS_OK, STATUS_WARN, STATUS_NA, STATUS_SKIP, "
        "STATUS_UNREACHABLE)\n"
        "assert NOT_JUDGED_STATUSES == {STATUS_NA, STATUS_SKIP, STATUS_UNREACHABLE}\n"
        "print('openpyxl' in sys.modules)\n"
    )
    stdout, stderr, code_returned = _run(code)
    assert code_returned == 0, stderr
    assert stdout == "False"


def test_ssh_client_key_classes_are_computed_lazily():
    """_KEY_CLASSES 를 모듈 레벨에서 계산하던 것을 함수로 내렸는지.

    이게 남아 있으면 ssh_client 를 import 하는 것만으로 paramiko 가 로드된다.
    """
    code = (
        "import sys\n"
        "from engine import ssh_client\n"
        "assert not hasattr(ssh_client, '_KEY_CLASSES'), '모듈 레벨 상수가 남아 있다'\n"
        "assert ssh_client._key_classes_cache is None, '캐시가 미리 채워져 있다'\n"
        "print('paramiko' in sys.modules)\n"
    )
    stdout, stderr, code_returned = _run(code)
    assert code_returned == 0, stderr
    assert stdout == "False"
