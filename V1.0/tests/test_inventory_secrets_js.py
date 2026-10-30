"""장비 목록 화면의 비밀 칸 처리(web_ui/js/inventory.js) — IMPROVEMENT_PLAN 4-1.

화면은 저장된 비밀값을 받지 않는다. 새로 입력한 값만 들고 있다가 저장이 성공하면 버리고 '저장됨' 표시로 바꾼다.
저장 응답을 기다리는 사이에 또 입력한 값은 버리면 안 된다(다음 저장이 보내야 한다). node 가 없으면 건너뛴다.
"""
import json
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

import pytest

INVENTORY_JS = Path(__file__).resolve().parent.parent / "web_ui" / "js" / "inventory.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node 가 없어 JS 동작 검증을 건너뜁니다")


def _source(name):
    text = INVENTORY_JS.read_text(encoding="utf-8")
    start = text.index(f"function {name}(") if f"function {name}(" in text else text.index(f"async function {name}(")
    if text[start - 6:start] == "async ":
        start -= 6
    lines = text[start:].splitlines(keepends=True)
    collected = []
    for line in lines:
        collected.append(line)
        if line.rstrip("\r\n") == "}":
            return "".join(collected)
    raise AssertionError(f"{name} 의 끝을 찾지 못했다")


def run(body):
    text = INVENTORY_JS.read_text(encoding="utf-8")
    fields = next(line for line in text.splitlines() if line.startswith("const INVENTORY_SECRET_FIELDS"))
    script = "\n".join([fields] + [_source(n) for n in ("secretPlaceholder", "setSecret", "markSecretCleared",
                                                         "saveInventoryRows")]) + "\n" + textwrap.dedent(body)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        path = handle.name
    try:
        done = subprocess.run(["node", path], capture_output=True, timeout=30)
    finally:
        Path(path).unlink()
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return json.loads(done.stdout.decode("utf-8"))


def test_saved_secrets_are_forgotten_but_values_typed_meanwhile_are_kept():
    result = run("""
        const typedLater = { id: 'a', name: 'Core1' };
        const plain = { id: 'b', name: 'Agg1' };
        const cleared = { id: 'c', name: 'Acc1', has_password: true };
        setSecret(typedLater, 'password', 'first');
        setSecret(plain, 'password', 'p2');
        markSecretCleared(cleared, 'password');
        const rows = [typedLater, plain, cleared];
        function collectInventoryRows() { return rows; }
        let sent = null;
        async function call(fn, payload) {
          sent = JSON.parse(JSON.stringify(payload));
          setSecret(typedLater, 'password', 'second');      // 응답을 기다리는 사이에 또 입력
          return true;
        }
        saveInventoryRows().then(saved => console.log(JSON.stringify({ saved, sent, rows })));
    """)
    assert result["saved"] is True
    assert [r.get("password") for r in result["sent"]] == ["first", "p2", None]
    assert result["sent"][2]["clear_secrets"] == ["password"]
    typed_later, plain, cleared = result["rows"]
    assert typed_later["password"] == "second"                   # 다음 저장이 보낸다
    assert "password" not in plain and plain["has_password"] is True
    assert "clear_secrets" not in cleared and cleared["has_password"] is False


def test_a_failed_save_keeps_everything_and_placeholders_explain_the_state():
    result = run("""
        const d = { id: 'a', name: 'Core1', has_key_passphrase: true, unreadable_secrets: ['key_content'] };
        setSecret(d, 'password', 'p1');
        setSecret(d, 'password', '');                            // 빈 입력은 무시(저장된 값을 지우지 않는다)
        function collectInventoryRows() { return [d]; }
        async function call() { return null; }
        saveInventoryRows().then(saved => console.log(JSON.stringify({
          saved, password: d.password,
          placeholders: ['password', 'key_content', 'key_passphrase'].map(f => secretPlaceholder(d, f, '기본값')),
        })));
    """)
    assert result["saved"] is None and result["password"] == "p1"
    assert result["placeholders"] == ["새 값 입력됨 — 저장 대기", "이 PC에서 풀 수 없음 — 다시 입력",
                                      "저장됨 — 바꾸려면 입력"]
