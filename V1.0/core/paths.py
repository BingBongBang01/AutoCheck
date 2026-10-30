"""
애플리케이션 전역 경로 해석을 한 곳에 모은 중앙 모듈.

이전에는 app-root 판단(exe 패키징 대응)과 폴더명 안전화 로직이
engine/log_storage.py와 engine/project_manager.py에 따로따로(그리고 서로 다른 규칙으로)
구현돼 있었다. 이 모듈이 그 중복을 없애는 단일 출처(Single Source of Truth)다.

프로그램 코드/자산(패키지 내부)과 고객사별 데이터(data/)를 분리하는 것이 목적이므로,
data_root()가 가리키는 트리 밖의 파일은 절대 프로그램 코드로 취급하지 않는다.
"""
import os
import re
import shutil
import sys
from pathlib import Path

# 이 환경변수가 있으면 사용자 데이터 폴더를 통째로 그곳으로 옮긴다(테스트·개발용).
USER_DATA_ROOT_ENV = "AUTOCHECK_HOME"

_INVALID_NAME_CHARS_RE = re.compile(r'[\\/:*?"<>|]')
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class AppPaths:
    """실행 파일(exe)/스크립트 위치를 기준으로 앱 루트와 최상위 디렉터리를 계산.

    exe로 패키징된 경우 프로그램 코드 자산(web_ui 등)은 sys.executable(또는 bundle_root)에서 읽고,
    사용자의 작업 데이터/설정/로그는 Windows 사용자 문서 폴더(Documents/AutoCheck)에 저장하여
    exe 파일이 있는 폴더(바탕화면 등)가 지저분해지는 것을 방지한다.
    """

    _root = None
    _user_data_root = None
    # 이미 mkdir 을 호출한 경로들 — _ensure() 가 프로세스당 한 번만 시스콜을 내게 한다.
    _ensured = set()
    # 하위 폴더 접근자가 돌려줄 Path 객체 캐시. mkdir 을 건너뛰어도 매 호출 Path 를 새로
    # 조립하면(user_data_root() / "data") 여전히 3 us 가 든다 — 객체까지 캐시해야 0.2 us 가 된다.
    _subdir_cache = {}

    @classmethod
    def app_root(cls) -> Path:
        if cls._root is None:
            if getattr(sys, "frozen", False):
                cls._root = Path(sys.executable).resolve().parent
            else:
                cls._root = Path(__file__).resolve().parent.parent
        return cls._root

    @classmethod
    def bundle_root(cls) -> Path:
        """PyInstaller로 번들링된 자산(web_ui, static assets 등)의 위치.
        frozen 상태면 sys._MEIPASS(또는 app_root), 개발 상태면 app_root()를 반환한다."""
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS)
        return cls.app_root()

    @classmethod
    def user_data_root(cls) -> Path:
        """사용자의 작업 데이터/설정/로그 최상위 루트 디렉터리. 아래 순서로 정한다:

        1. 환경변수 AUTOCHECK_HOME — 데이터 폴더를 통째로 옮길 때. conftest.py 가 테스트마다
           임시 폴더로 잡아서, 테스트가 실제 사용자 데이터를 만들거나 고치지 않게 한다.
        2. 포터블: app_root() 아래 autocheck_data 폴더나 portable.txt 가 있으면 그 폴더.
        3. 기본값: C:\\Users\\<사용자>\\Documents\\AutoCheck
        """
        if cls._user_data_root is None:
            override = os.environ.get(USER_DATA_ROOT_ENV, "").strip()
            portable_dir = cls.app_root() / "autocheck_data"
            portable_flag = cls.app_root() / "portable.txt"
            if override:
                cls._user_data_root = Path(override).expanduser()
            elif portable_dir.exists() or portable_flag.exists():
                cls._user_data_root = portable_dir
            else:
                cls._user_data_root = Path.home() / "Documents" / "AutoCheck"
            
            cls._ensure(cls._user_data_root)
            cls._init_user_data_templates(cls._user_data_root)
        return cls._user_data_root

    @classmethod
    def _init_user_data_templates(cls, user_root: Path):
        """최초 실행 시 bundled 템플릿 설정 파일(ai_settings.yaml 등) 및 기본 config 폴더를 복사한다.

        ai_config.yaml 은 복사하지 않는다 — 클라우드 AI API 키가 들어 있는 파일이라, 빌드한 PC 의
        파일이 번들에 섞이면 EXE 를 받은 모든 사용자에게 그 키가 퍼진다(AutoCheck.spec 도 담지 않는다).
        없으면 앱은 빈 설정으로 시작하고, 설정 화면이 사용자 폴더에 새로 만든다."""
        bundle = cls.bundle_root()

        # 복사 대상 루트 파일들
        for filename in ("ai_settings.yaml", "connection.yaml"):
            target_file = user_root / filename
            src_file = bundle / filename
            if not target_file.exists() and src_file.exists():
                try:
                    shutil.copy2(src_file, target_file)
                except OSError:
                    pass

        # config/ 에서는 사용자가 고칠 수 있는 템플릿만 복사한다(core/app_config.LAYERED) — 고치라고 주는
        # 사본이고, 읽을 때는 번들 기본값 위에 얹힌다. 앱이 소유하는 규칙(log_rules.json 등)은 번들에서만
        # 읽으므로 복사하지 않는다(예전엔 config/ 를 통째로 복사해서, 고쳐도 반영되지 않는 사본이 생겼다).
        # 폴더가 이미 있어도 빠진 템플릿은 채운다 — 예전엔 폴더가 없을 때만 복사했다.
        from core.app_config import LAYERED
        for name in LAYERED:
            src_file = bundle / "config" / name
            target_file = user_root / "config" / name
            if not target_file.exists() and src_file.exists():
                try:
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, target_file)
                except OSError:
                    pass

        # 최초 실행 시 CRTlog 폴더 자동 생성 보장
        cls._ensure(user_root / "CRTlog")

    @staticmethod
    def _ensure(path: Path) -> Path:
        """폴더를 보장하고 돌려준다 — 같은 경로에 대해 mkdir 은 프로세스당 한 번만 호출한다.

        왜 캐시하는가: data_root() / config_root() / logs_root() / crt_log_root() 같은 접근자는
        호출될 때마다 mkdir(parents=True, exist_ok=True) 시스콜을 냈다. 실측 9.2~9.8 us/call
        (Linux) / 64.8 us/call (이전 측정, Windows) 인데, 이미 캐시돼 있는 app_root() 는
        0.1 us 다. 폴링 경로(0.8초 주기의 get_realtime_monitor_state 등)가 이 접근자를 여러 번
        부르므로, 앱이 유휴 상태에서도 계속 디스크에 쓰기를 시도하고 있었다.

        절감폭 자체는 작다(폴링 1회당 수십 us). 이 변경의 진짜 값은 "아무 일도 안 할 때
        디스크를 건드리지 않는다"는 것이다 — 과대평가하지 말 것.

        받아들이는 대가: 앱이 도는 중에 사용자가 탐색기에서 폴더를 지우면 이 함수는 다시
        만들어 주지 않는다. 실제 쓰기 경로는 각자 makedirs 를 하므로(core/atomic_io.py,
        engine/realtime_monitor.save_snapshot, engine/log_storage.open_in_file_explorer)
        데이터가 유실되지는 않고, 읽기/감시 경로는 폴더가 없으면 조용히 건너뛴다.
        """
        key = str(path)
        if key not in AppPaths._ensured:
            path.mkdir(parents=True, exist_ok=True)
            AppPaths._ensured.add(key)
        return path

    @classmethod
    def forget_ensured(cls):
        """폴더 생성 캐시를 비운다 — 테스트가 임시 디렉터리로 루트를 바꿀 때 필요하다.

        운영 코드에서 부를 일은 없다. 캐시를 비우면 다음 접근자 호출이 다시 mkdir 을 한다.
        """
        cls._ensured.clear()
        cls._subdir_cache.clear()

    @classmethod
    def _subdir(cls, name: str) -> Path:
        """user_data_root()/<name> 을 보장하고 돌려준다 — Path 객체까지 캐시한다.

        이 접근자들은 폴링 경로에서 반복 호출되므로(0.8초 주기) mkdir 뿐 아니라 Path 조립
        비용도 없애야 의미가 있다. 실측: mkdir 만 캐시 3.1 us -> Path 까지 캐시 0.2 us.
        """
        cached = cls._subdir_cache.get(name)
        if cached is None:
            cached = cls._ensure(cls.user_data_root() / name)
            cls._subdir_cache[name] = cached
        return cached

    @classmethod
    def data_root(cls) -> Path:
        """고객사별 데이터(프로파일/인벤토리/커맨드/베이스라인/실행기록) 최상위 루트."""
        return cls._subdir("data")

    @classmethod
    def labs_root(cls) -> Path:
        """(레거시) 채점 프로젝트 정의(stages.yaml/target_state.yaml 등) 저장 위치."""
        return cls._subdir("labs")

    @classmethod
    def config_root(cls) -> Path:
        return cls._subdir("config")

    @classmethod
    def logs_root(cls) -> Path:
        return cls._subdir("logs")

    @classmethod
    def raw_logs_root(cls) -> Path:
        """(레거시) 수집 파이프라인이 남긴 원본 로그. 읽기 폴백 전용이라 없으면 만들지 않는다."""
        return cls.user_data_root() / "raw_logs"

    @classmethod
    def crt_log_root(cls) -> Path:
        """SecureCRT 세션 로그 자동 연동용 글로벌 저장소 위치.
        Documents/AutoCheck/CRTlog/ 에 쌓인 로그들을 활성 프로파일로 자동 매핑/복사한다."""
        return cls._subdir("CRTlog")


def sanitize_component(name: str) -> str:
    """경로 구분자 등 위험 문자만 치환하는 최소 방어용 함수(기존 폴더와의 호환 목적).
    사용자가 새로 입력한 이름을 검증할 때는 validate_name()을 쓸 것."""
    name = (name or "").strip()
    name = _INVALID_NAME_CHARS_RE.sub("_", name)
    return name or "미지정"


def safe_join(base, *parts) -> Path:
    """base 아래 경로를 만든다 — 결과가 base 밖(.., 절대경로, 다른 드라이브)을 가리키면 ValueError.

    외부에서 온 이름(JS 가 넘긴 id·파일명, 장비명, zip 안의 경로)을 경로에 붙이는 곳은 이걸 쓴다.
    `base / name` 만으로는 name 이 '..\\..\\x' 나 'C:\\x' 일 때 base 밖을 가리키고, 그 경로로
    삭제(rmtree)·쓰기가 그대로 일어났다. 검사는 실제 위치(resolve)로 하되, 돌려주는 값은 예전과
    같은 형태(resolve 하지 않은 base/parts)라 정상 경로의 동작은 바뀌지 않는다."""
    joined = Path(base).joinpath(*(str(part) for part in parts))
    root = Path(base).resolve()
    target = joined.resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"허용되지 않는 경로입니다: {'/'.join(str(part) for part in parts)}")
    return joined


def safe_child(base, name) -> Path:
    """base 의 '바로 아래' 항목 하나 — 삭제처럼 범위가 정확해야 하는 곳용.
    빈 이름, '.', '..', 구분자·드라이브 문자가 든 이름은 거부한다(ValueError)."""
    name = str(name if name is not None else "")
    if not name.strip() or name in (".", "..") or any(ch in name for ch in "/\\:"):
        raise ValueError(f"허용되지 않는 이름입니다: {name!r}")
    return safe_join(base, name)


def safe_filename(name, fallback="unnamed") -> str:
    """외부 문자열(장비명 등)을 파일명 한 조각으로 쓸 수 있게 다듬는다.

    경로 구분자·Windows 금지 문자·제어문자는 '_' 로, 끝의 점·공백은 지우고, '.'/'..' 는 fallback,
    Windows 예약어(CON, COM1 …)는 앞에 '_' 를 붙인다. 정상 이름(Core1, Spine-01)은 그대로라서
    이미 저장된 파일명과 어긋나지 않는다."""
    cleaned = _INVALID_NAME_CHARS_RE.sub("_", str(name if name is not None else ""))
    cleaned = re.sub(r"[\x00-\x1f]", "_", cleaned).strip().rstrip(". ")
    if not cleaned or cleaned in (".", ".."):
        return fallback
    if cleaned.split(".")[0].upper() in _RESERVED_WINDOWS_NAMES:
        cleaned = "_" + cleaned
    return cleaned


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
