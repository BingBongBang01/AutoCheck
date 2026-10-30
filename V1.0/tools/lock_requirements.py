"""requirements.lock 생성기 — requirements.txt 의 패키지와 그 전이 의존성을 '지금 설치된 버전'으로 고정한다.

네트워크를 쓰지 않는다(importlib.metadata 로 설치된 배포판의 메타데이터만 읽는다). 그래서 lock 은
이 명령을 돌린 PC 의 실제 조합이다 — 테스트가 통과한 개발 PC 에서 돌려 '검증된 조합'을 남긴다.
플랫폼 조건(sys_platform 등)은 이 PC 기준으로 평가된다(Windows 전용 의존성이 들어간다).
packaging 패키지가 필요하다(pytest 의존성이라 requirements-dev.txt 를 설치했으면 있다).

사용:
    python -m tools.lock_requirements      # requirements.txt -> requirements.lock
"""
import datetime
import platform
import sys
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent


def top_level_names(text):
    """requirements 파일 내용 -> 패키지 이름 목록(주석·옵션 줄 제외)."""
    names = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            names.append(Requirement(line).name)
    return names


def resolve_installed(names):
    """이름들의 전이 의존성까지 따라가 {정규화 이름: (표시 이름, 설치 버전)} 과 미설치 목록을 돌려준다.
    extra 로만 필요한 의존성과 이 플랫폼에 해당하지 않는 의존성은 건너뛴다."""
    pins, missing = {}, []
    queue = list(names)
    while queue:
        name = queue.pop()
        key = canonicalize_name(name)
        if key in pins:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
            continue
        pins[key] = (dist.metadata["Name"], dist.version)
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                queue.append(req.name)
    return pins, sorted(set(missing))


def render(pins):
    header = [
        "# 자동 생성 파일 — 직접 고치지 말고 `python -m tools.lock_requirements` 로 다시 만든다.",
        "# requirements.txt 의 패키지와 전이 의존성을 이 PC 에 설치된 버전으로 고정한 것이다.",
        f"# 생성: {datetime.date.today().isoformat()} · {platform.system()} {platform.release()}"
        f" · Python {platform.python_version()}",
        "# 같은 조합 설치:  pip install -r requirements.lock",
        "",
    ]
    return "\n".join(header + [f"{name}=={version}" for _, (name, version) in sorted(pins.items())]) + "\n"


def main():
    names = top_level_names((ROOT / "requirements.txt").read_text(encoding="utf-8"))
    pins, missing = resolve_installed(names)
    if missing:
        sys.exit(f"설치되지 않은 패키지가 있어 lock 을 만들 수 없다: {', '.join(missing)}")
    (ROOT / "requirements.lock").write_bytes(render(pins).encode("utf-8"))
    print(f"requirements.lock — {len(pins)}개 패키지 고정")


if __name__ == "__main__":
    main()
