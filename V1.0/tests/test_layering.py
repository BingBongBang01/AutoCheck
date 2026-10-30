"""계층 의존 방향 — IMPROVEMENT_PLAN 3-6 (docs/ARCHITECTURE.md 'Dependency direction').

    api -> engine / ai_analysis -> report / parsers -> core

아래 층이 위 층을 import 하면 실패한다. 모듈 최상단만이 아니라 **함수 안 import** 까지 본다 — 지역 import 는
순환을 피할 뿐 의존을 없애지 않는다. 예전엔 core/storage_service.py 가 engine.profile_manager 를,
core/crt_stream_watcher.py 가 engine.baseline_store 를, report/inspection_excel.py 가 engine.log_analysis 를
지역 import 로 거꾸로 불렀고, docs/ARCHITECTURE.md 의 '역방향 의존 없음' 서술과 어긋났다.
(engine 이 api 를 부르지 않는다는 규칙은 OPTIMIZATION_PLAN 3-2 에서 tests/test_text_io.py 가 먼저 지켰다 —
여기로 합쳤다.)
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# 층 -> import 하면 안 되는 최상위 패키지
FORBIDDEN = {
    "core": {"api", "engine", "ai_analysis", "report", "parsers"},
    "report": {"api", "engine", "ai_analysis"},
    "parsers": {"api", "engine", "ai_analysis", "report"},
    "engine": {"api"},
    "ai_analysis": {"api"},
}


def _imports(path):
    """(줄, 모듈) — 절대 import 만. 함수·클래스 안의 것도 포함한다."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.lineno, node.module


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_lower_layers_do_not_import_upper_layers(layer):
    offenders = []
    for path in sorted((ROOT / layer).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for line, module in _imports(path):
            if module.split(".")[0] in FORBIDDEN[layer]:
                offenders.append(f"{path.relative_to(ROOT).as_posix()}:{line} -> {module}")
    assert offenders == [], f"{layer}/ 가 위 층을 import 한다(역방향 의존): {offenders}"


def test_the_scan_sees_function_level_imports(tmp_path):
    """검사기 자체 확인 — 함수 안에 숨긴 import 도 잡아야 이 테스트가 의미가 있다."""
    sample = tmp_path / "sample.py"
    sample.write_text("def f():\n    from engine.profile_manager import RunHandle\n", encoding="utf-8")
    assert list(_imports(sample)) == [(2, "engine.profile_manager")]
