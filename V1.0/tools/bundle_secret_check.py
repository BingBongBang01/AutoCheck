"""EXE 에 담길 파일에서 비밀(API 키·계정)을 찾는다 — AutoCheck.spec 이 빌드 전에 부른다.

번들된 설정 파일은 첫 실행 때 사용자 폴더로 복사된다(core/paths.AppPaths._init_user_data_templates).
개발 PC 의 ai_config.yaml(클라우드 AI 키)이나 장비 계정이 번들에 섞이면 EXE 를 받은 모든 사용자에게
퍼진다. 그래서 빌드를 멈춘다:

  - 이름만으로 거부: ai_config.yaml, credential.json, portable.txt, autocheck_data/ 아래 전부
  - 모든 텍스트 파일: 제공자 키 모양(sk-…, AIza…, nvapi-… — core.secret_redaction.PROVIDER_KEY_RE)
  - 설정 파일(.yaml/.yml/.json): api_key·password 같은 필드에 값이 들어 있으면

단독 실행: python -m tools.bundle_secret_check web_ui config VERSION
"""
import re
import sys
from pathlib import Path

from core.secret_redaction import PROVIDER_KEY_RE

FORBIDDEN_NAMES = frozenset({"ai_config.yaml", "credential.json", "portable.txt"})
FORBIDDEN_DIRS = frozenset({"autocheck_data"})
CONFIG_SUFFIXES = (".yaml", ".yml", ".json")
_MAX_SCAN_BYTES = 5 * 1024 * 1024

# "api_key": "…", password: …, key_content: |… — 값의 첫 토큰만 본다(값 자체는 보고하지 않는다)
_SECRET_FIELD_RE = re.compile(
    r"""["']?\b(api[_-]?key|password|default_password|key_content|key_passphrase|secret|token)\b["']?"""
    r"""[ \t]*[:=][ \t]*["']?([^"'\s,}\]#]*)""",
    re.IGNORECASE,
)
_EMPTY_VALUES = frozenset({"", "null", "none", "~", "|", ">", "[]", "{}"})


def _placeholder(value):
    return value.lower() in _EMPTY_VALUES or value.startswith(("<", "$", "{{"))


def _text_of(path):
    try:
        if path.stat().st_size > _MAX_SCAN_BYTES:
            return None
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None   # 바이너리(xlsx·이미지·폰트)는 내용 검사 대상이 아니다


def _files(source):
    source = Path(source)
    if source.is_dir():
        yield from (p for p in sorted(source.rglob("*")) if p.is_file())
    elif source.is_file():
        yield source


def scan_file(path):
    """파일 하나의 문제 목록(사람이 읽는 문장). 비밀 값 자체는 문장에 넣지 않는다."""
    path = Path(path)
    if path.name.lower() in FORBIDDEN_NAMES or FORBIDDEN_DIRS & {part.lower() for part in path.parts}:
        return [f"{path}: 번들하면 안 되는 파일입니다(계정·API 키 저장 위치)"]
    text = _text_of(path)
    if text is None:
        return []
    problems = []
    is_config = path.suffix.lower() in CONFIG_SUFFIXES
    for lineno, line in enumerate(text.splitlines(), 1):
        if PROVIDER_KEY_RE.search(line):
            problems.append(f"{path}:{lineno}: API 키 모양의 문자열")
        if is_config:
            for match in _SECRET_FIELD_RE.finditer(line):
                if not _placeholder(match.group(2)):
                    problems.append(f"{path}:{lineno}: '{match.group(1)}' 필드에 값이 들어 있습니다")
    return problems


def spec_bundle_sources(spec_text):
    """AutoCheck.spec 이 담는 것 — (늘 담는 이름들, 있으면 담는 이름들). 테스트와 릴리스 점검(tools/release_check)이
    spec 을 따로 해석하지 않게 한 곳에서 읽는다."""
    fixed = re.findall(r"^\s+\('([^']+)', '[^']*'\),$", spec_text, re.MULTILINE)
    optional = re.search(r"for optional_file in \[([^\]]*)\]", spec_text)
    return fixed, re.findall(r"'([^']+)'", optional.group(1)) if optional else []


def find_bundle_secrets(sources):
    """sources(파일·폴더 경로 목록) 아래 모든 파일의 문제 목록."""
    problems = []
    for source in sources:
        for path in _files(source):
            problems.extend(scan_file(path))
    return problems


def main(argv):
    problems = find_bundle_secrets(argv or ["."])
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
