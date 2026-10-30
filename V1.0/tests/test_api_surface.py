"""pywebview 에 노출되는 API 목록 고정 — IMPROVEMENT_PLAN 3-2.

pywebview 는 js_api 객체에서 `_` 로 시작하지 않는 메서드를 모두 화면(JS)에 내보낸다. 예전엔 화면이
부르지 않는 메서드가 40개 넘게 열려 있었다 — 채점·CRT 인제스트·연결 설정처럼 죽은 경로와, 다른 Python
코드만 부르는 내부 메서드(활성 고객사 해석, 실시간 감시 저장 등)였다. 이 테스트가 지키는 것:

  1. 공개 메서드는 모두 화면(web_ui 의 .js/.html)이 이름으로 가리킨다. 아직 화면이 없는 것은
     RESERVED 에 이유와 함께 적는다. 내부에서만 쓰는 메서드는 이름을 `_` 로 시작한다.
  2. 화면이 call('이름') / pywebview.api.이름 으로 부르는 메서드는 모두 있다 — API 를 지우거나
     이름을 바꿨는데 화면이 아직 부르면 여기서 걸린다.

main 을 import 하면 패키지 검사·webview import 가 일어나므로, main.py 의 `class Api` 베이스 목록을
AST 로 읽어 같은 믹스인 조합을 만든다.
"""
import ast
import importlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 화면이 아직 부르지 않지만 열어 두는 API — 이유를 함께 적는다. 화면이 쓰기 시작하면 여기서 뺀다.
RESERVED = {}   # 보관함 API 3개는 4-10 의 보관함 화면(web_ui/js/core-archive-modal.js)이 쓴다

_CALL_RE = re.compile(r"""\bcall\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]""")
_FUNCTION_RE = re.compile(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", re.MULTILINE)
_DIRECT_RE = re.compile(r"\bpywebview\.api\.([A-Za-z_][A-Za-z0-9_]*)")


def _api_class():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    modules = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("api."):
            for alias in node.names:
                modules[alias.asname or alias.name] = node.module
    api = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Api")
    bases = tuple(getattr(importlib.import_module(modules[base.id]), base.id) for base in api.bases)
    return type("Api", bases, {})


def _exposed():
    api = _api_class()
    return {name for name in dir(api) if not name.startswith("_") and callable(getattr(api, name))}


def _web_text():
    paths = sorted((ROOT / "web_ui").rglob("*.js")) + sorted((ROOT / "web_ui").rglob("*.html"))
    return "\n".join(path.read_text(encoding="utf-8") for path in paths)


def test_every_exposed_method_is_used_by_the_ui_or_reserved():
    web = _web_text()
    unused = sorted(name for name in _exposed() - set(RESERVED)
                    if not re.search(rf"\b{re.escape(name)}\b", web))
    assert not unused, ("화면이 부르지 않는 공개 API 가 있다 — 내부용이면 이름을 _ 로 시작하고, 쓰지 않으면 "
                        f"지우고, 곧 화면이 쓸 것이면 RESERVED 에 이유를 적는다: {unused}")


def test_reserved_entries_are_still_needed():
    web = _web_text()
    exposed = _exposed()
    missing = sorted(set(RESERVED) - exposed)
    assert not missing, f"RESERVED 에 있는데 API 가 없다: {missing}"
    now_used = sorted(name for name in RESERVED if re.search(rf"\b{re.escape(name)}\b", web))
    assert not now_used, f"이제 화면이 쓰므로 RESERVED 에서 뺀다: {now_used}"


def test_every_ui_call_targets_an_existing_method():
    web = _web_text()
    called = set(_CALL_RE.findall(web)) | set(_DIRECT_RE.findall(web))
    assert len(called) > 100, f"화면 호출을 거의 못 찾았다({len(called)}개) — 정규식이 화면 코드와 어긋났다"
    missing = sorted(called - _exposed())
    assert not missing, f"화면이 부르는데 Api 에 없는 메서드: {missing}"


def test_no_unreachable_ui_function_talks_to_the_bridge():
    """브리지를 부르는 화면 함수를 아무도 부르지 않으면 위 검사는 통과해도 그 기능에 갈 길이 없다 — 프로파일 zip
    내보내기·가져오기가 부르는 곳 없는 옛 창(openProfileModal) 안에 그렇게 숨어 있었다(IMPROVEMENT_PLAN D5·5-3).
    함수의 몸은 다음 함수 선언까지로 본다(대략이면 충분하다 — 가리키는 곳이 있는지가 요점이다)."""
    web = _web_text()
    unreachable = []
    for path in sorted((ROOT / "web_ui" / "js").glob("*.js")):
        text = path.read_text(encoding="utf-8")
        matches = list(_FUNCTION_RE.finditer(text))
        for index, match in enumerate(matches):
            body = text[match.end(): matches[index + 1].start() if index + 1 < len(matches) else len(text)]
            if _CALL_RE.search(body) and len(re.findall(rf"\b{re.escape(match.group(1))}\b", web)) < 2:
                unreachable.append(f"{path.name}: {match.group(1)}")
    assert not unreachable, f"브리지를 부르지만 아무도 부르지 않는 화면 함수 — 화면에 잇거나 지운다: {unreachable}"
