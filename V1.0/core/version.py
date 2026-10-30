"""앱 버전 — VERSION 파일 하나에서 읽는 단일 출처(IMPROVEMENT_PLAN 3-8).

예전엔 읽는 곳마다 따로 구현했다. 화면(api/base.get_app_version)은 번들 폴더를 먼저 보고, run 기록
(engine/run_manager)은 앱 폴더만 봐서, EXE 에서는 run 의 session.json 에 'unknown' 이 남았다 —
PyInstaller onedir 빌드는 VERSION 을 번들 폴더(sys._MEIPASS)에 둔다.
"""
import functools

from core.paths import AppPaths

__all__ = ["app_version"]


@functools.lru_cache(maxsize=1)
def app_version() -> str:
    """번들 폴더 -> 앱 폴더 순으로 VERSION 을 읽는다. 없거나 비어 있으면 'unknown'. 실행 중에는 바뀌지 않으므로 한 번만 읽는다."""
    for folder in (AppPaths.bundle_root(), AppPaths.app_root()):
        try:
            text = (folder / "VERSION").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return "unknown"
