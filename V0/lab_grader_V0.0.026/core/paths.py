"""
애플리케이션 전역 경로 해석을 한 곳에 모은 중앙 모듈.

이전에는 app-root 판단(exe 패키징 대응)과 폴더명 안전화 로직이
engine/log_storage.py와 engine/project_manager.py에 따로따로(그리고 서로 다른 규칙으로)
구현돼 있었다. 이 모듈이 그 중복을 없애는 단일 출처(Single Source of Truth)다.

프로그램 코드/자산(패키지 내부)과 고객사별 데이터(data/)를 분리하는 것이 목적이므로,
data_root()가 가리키는 트리 밖의 파일은 절대 프로그램 코드로 취급하지 않는다.
"""
import re
import sys
from pathlib import Path

_INVALID_NAME_CHARS_RE = re.compile(r'[\\/:*?"<>|]')
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class AppPaths:
    """실행 파일(exe)/스크립트 위치를 기준으로 앱 루트와 최상위 디렉터리를 계산.

    exe로 패키징된 경우 현재 작업 디렉터리(CWD)가 실행 위치와 다를 수 있으므로,
    상대경로 대신 sys.executable(또는 이 파일 기준 프로젝트 루트)로 앱 루트를 고정한다 —
    어디서 실행하든 항상 같은 폴더 트리를 가리키게 하기 위함. 계산된 루트는 캐시해서
    반복 호출해도 항상 같은 Path 인스턴스를 반환한다.
    """

    _root = None

    @classmethod
    def app_root(cls) -> Path:
        if cls._root is None:
            if getattr(sys, "frozen", False):
                cls._root = Path(sys.executable).resolve().parent
            else:
                cls._root = Path(__file__).resolve().parent.parent
        return cls._root

    @staticmethod
    def _ensure(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def data_root(cls) -> Path:
        """고객사별 데이터(프로파일/인벤토리/커맨드/베이스라인/실행기록) 최상위 루트."""
        return cls._ensure(cls.app_root() / "data")

    @classmethod
    def labs_root(cls) -> Path:
        """(레거시) 채점 프로젝트 정의(stages.yaml/target_state.yaml 등) 저장 위치."""
        return cls._ensure(cls.app_root() / "labs")

    @classmethod
    def config_root(cls) -> Path:
        return cls._ensure(cls.app_root() / "config")

    @classmethod
    def history_root(cls) -> Path:
        """(레거시) 채점 모드(grading) 세션 히스토리."""
        return cls._ensure(cls.app_root() / "history")

    @classmethod
    def logs_root(cls) -> Path:
        return cls._ensure(cls.app_root() / "logs")

    @classmethod
    def raw_logs_root(cls) -> Path:
        """(레거시) 수집 파이프라인이 남긴 원본 로그. 읽기 폴백 전용이라 없으면 만들지 않는다."""
        return cls.app_root() / "raw_logs"

    @classmethod
    def terminal_sessions_dir(cls, project_id) -> Path:
        """세션 터미널이 저장하는 원본 로그(AutoCheck_{장비}_{시각}.txt).

        보고서·Findings·AI 분석·점검 로그 목록이 전부 여기를 읽는다. 예전엔 호출부마다
        os.path.join("labs", project_id, "terminal_sessions")로 직접 조립했는데, CWD 상대경로라
        앱을 다른 폴더에서 실행하면 로그가 멀쩡히 있는데도 "장비 없음"으로 보였다.
        """
        return cls.labs_root() / str(project_id) / "terminal_sessions"


def sanitize_component(name: str) -> str:
    """경로 구분자 등 위험 문자만 치환하는 최소 방어용 함수(기존 폴더와의 호환 목적).
    사용자가 새로 입력한 이름을 검증할 때는 validate_name()을 쓸 것."""
    name = (name or "").strip()
    name = _INVALID_NAME_CHARS_RE.sub("_", name)
    return name or "미지정"


def validate_name(name: str) -> str:
    """고객사명/프로파일명 등 사용자 입력을 폴더명으로 쓰기 전에 엄격히 검증한다.
    유효하면 앞뒤 공백을 제거한 이름을 그대로 반환하고, 아니면 ValueError를 낸다."""
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("이름을 입력하세요.")
    match = _INVALID_NAME_CHARS_RE.search(cleaned)
    if match:
        raise ValueError(f'다음 문자는 사용할 수 없습니다: \\ / : * ? " < > |  (입력값: "{cleaned}")')
    if cleaned in (".", "..") or cleaned.upper() in _RESERVED_WINDOWS_NAMES:
        raise ValueError(f'"{cleaned}"은(는) 예약된 이름이라 사용할 수 없습니다.')
    if cleaned.endswith(".") or cleaned.endswith(" "):
        raise ValueError("이름 끝에 마침표나 공백을 쓸 수 없습니다.")
    return cleaned
