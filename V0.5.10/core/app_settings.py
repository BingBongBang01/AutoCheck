"""
환경 설정 파일들의 경로 — 여기 있는 것은 전부 **앱 전역** 설정이다.

즉 고객사가 몇 곳이든, 정기점검 프로파일이 몇 개든 하나의 값을 공유한다.
AI 제공자 우선순위·API 키·로컬 모델·터미널 우클릭 동작·SSH 공통값은
"이 PC에서 이 프로그램을 쓰는 방식"이지 "이번 점검 회차의 내용"이 아니기 때문이다.
프로파일마다 달라야 하는 것(장비 목록, 커맨드, 점검 항목)은 여기 두지 않는다 —
그건 labs/<project_id>/ 와 data/<고객사>/<프로파일>/ 에 있다.

경로는 반드시 AppPaths(core/paths.py)로 계산한다. 예전엔 "ai_config.yaml" 같은
CWD 상대경로 리터럴이라, 앱을 다른 디렉터리에서 실행하거나 exe로 패키징하면
CWD가 달라져 설정이 전부 기본값으로 되돌아간 것처럼 보이고(읽기 실패),
저장하면 엉뚱한 폴더에 새 파일이 생겼다. project_manager 등 나머지 모듈은
이미 AppPaths로 옮겨졌는데 환경 설정만 남아 있었다.
"""
from core.paths import AppPaths


def ai_config_path():
    """로컬 AI 파라미터 + 클라우드 API 키 목록 (LocalAiApiMixin / CloudAiApiMixin 공유)."""
    return str(AppPaths.user_data_root() / "ai_config.yaml")


def ai_order_path():
    """AI 제공자 우선순위(클라우드 → 로컬 → 규칙기반 순서)."""
    return str(AppPaths.user_data_root() / "ai_settings.yaml")


def connection_path():
    """SSH 공통 설정(포트/타임아웃/재시도/병렬 워커 수)."""
    return str(AppPaths.user_data_root() / "connection.yaml")


def terminal_ui_path():
    """터미널 우클릭 동작(메뉴 표시 / 바로 붙여넣기)."""
    return str(AppPaths.config_root() / "terminal_ui.yaml")
