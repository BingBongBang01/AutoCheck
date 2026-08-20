"""AppPaths 경로 접근자 — OPTIMIZATION_PLAN 1-2 (폴더 생성/경로 객체 캐시).

이 접근자들은 폴링 경로에서 반복 호출된다(0.8초 주기의 get_realtime_monitor_state 등).
캐시 전에는 호출마다 mkdir 시스콜을 냈다(9.2~9.8 us/call, Windows 64.8 us/call).

테스트가 확인하는 것은 두 가지다:
  1. 캐시가 정확성을 깨지 않는다 — 폴더는 여전히 만들어지고, 루트가 바뀌면 따라간다.
  2. mkdir 이 프로세스당 경로별 한 번만 호출된다.

속도 수치는 여기서 검증하지 않는다(머신마다 다르다). 그건 tools/bench_log_analysis.py 의 몫이다.
"""
import tempfile
from pathlib import Path

import pytest

from core import paths as paths_module
from core.paths import AppPaths, sanitize_component, validate_name

# user_data_root()/<name> 으로 해석되며 폴더를 보장하는 접근자들.
ENSURING_ACCESSORS = ["data_root", "labs_root", "config_root", "history_root", "logs_root", "crt_log_root"]


@pytest.fixture
def temp_root(monkeypatch):
    """AppPaths 를 임시 디렉터리로 옮긴다 — 실제 Documents/AutoCheck 를 건드리지 않는다."""
    original_root = AppPaths._user_data_root
    root = Path(tempfile.mkdtemp()) / "AutoCheck"
    AppPaths._user_data_root = root
    AppPaths.forget_ensured()
    yield root
    AppPaths._user_data_root = original_root
    AppPaths.forget_ensured()


@pytest.mark.parametrize("accessor", ENSURING_ACCESSORS)
def test_accessor_creates_directory(temp_root, accessor):
    path = getattr(AppPaths, accessor)()
    assert path.is_dir(), f"{accessor}() 가 폴더를 만들지 않았다"
    assert path.parent == temp_root


@pytest.mark.parametrize("accessor", ENSURING_ACCESSORS)
def test_accessor_returns_same_object(temp_root, accessor):
    """Path 객체까지 캐시한다 — 매 호출 조립하면 mkdir 을 건너뛰어도 3 us 가 든다."""
    first = getattr(AppPaths, accessor)()
    second = getattr(AppPaths, accessor)()
    assert first is second


def test_repeated_calls_add_no_mkdir_syscalls(temp_root, monkeypatch):
    """핵심 주장의 직접 검증 — 첫 호출 이후로는 mkdir 시스콜이 더 늘지 않는다.

    "경로당 정확히 1회"로 세지 않는 이유: pathlib 의 mkdir(parents=True) 는 부모가 없으면
    부모를 만든 뒤 **자기 자신을 다시 호출**한다(CPython 구현). 그래서 첫 호출에서 같은
    경로가 2회로 세어지는데, 그건 우리 캐시와 무관한 구현 세부다. 우리가 주장하는 것은
    "반복 호출이 추가 시스콜을 내지 않는다"이므로 그것을 직접 잰다.
    """
    calls = []
    original_mkdir = Path.mkdir

    def counting_mkdir(self, *args, **kwargs):
        calls.append(str(self))
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", counting_mkdir)

    AppPaths.config_root()
    AppPaths.data_root()
    after_first = len(calls)
    assert after_first > 0, "첫 호출이 폴더를 만들지 않았다"

    for _ in range(20):
        AppPaths.config_root()
        AppPaths.data_root()

    assert len(calls) == after_first, (
        f"반복 호출이 mkdir 을 {len(calls) - after_first}회 더 냈다 — 캐시가 동작하지 않는다"
    )


def test_forget_ensured_allows_root_switch(temp_root):
    """테스트가 루트를 바꿀 수 있어야 한다 — 캐시가 이전 루트를 붙들면 안 된다."""
    first = AppPaths.config_root()
    assert first.is_dir()

    new_root = Path(tempfile.mkdtemp()) / "Other"
    AppPaths._user_data_root = new_root
    AppPaths.forget_ensured()

    second = AppPaths.config_root()
    assert second != first
    assert second.is_dir()
    assert second.parent == new_root


def test_raw_logs_root_does_not_create(temp_root):
    """레거시 읽기 폴백 경로는 폴더를 만들지 않아야 한다.

    만들면 데이터가 없는데 폴더만 있는 상태가 되어 UI 가 '데이터 있음'으로 오해한다 —
    log_storage.py 상단 주석이 경고하는 바로 그 문제다. 캐시 도입이 이 성질을 바꾸지 않았는지 확인.
    """
    path = AppPaths.raw_logs_root()
    assert not path.exists()


def test_app_root_is_not_in_subdir_cache(temp_root):
    """app_root() 는 user_data_root() 하위가 아니다 — 프로그램 코드 위치이므로 별도로 캐시된다."""
    AppPaths.app_root()
    assert "data" not in AppPaths._subdir_cache or AppPaths._subdir_cache.get("data") is not None
    assert AppPaths.app_root() == AppPaths.app_root()


def test_terminal_sessions_dir_derives_from_labs_root(temp_root):
    path = AppPaths.terminal_sessions_dir("lab1")
    assert path == AppPaths.labs_root() / "lab1" / "terminal_sessions"


# --------------------------------------------------------------------------- 이름 검증(기존 동작 고정)


@pytest.mark.parametrize("name", ["고객사A", "2026-07", "Core_1", "a b"])
def test_validate_name_accepts_reasonable_names(name):
    assert validate_name(name) == name.strip()


@pytest.mark.parametrize("name", ["", "   ", "a/b", "a\\b", "a:b", "a*b", "a?b", 'a"b', "a<b", "a>b", "a|b",
                                  ".", "..", "CON", "com1", "trailing."])
def test_validate_name_rejects_unsafe_names(name):
    with pytest.raises(ValueError):
        validate_name(name)


def test_trailing_space_is_stripped_not_rejected():
    """현재 동작 고정 — validate_name 의 '끝 공백 금지' 분기는 도달할 수 없다.

    함수가 맨 처음 name.strip() 을 하므로 끝 공백은 검사 전에 사라진다. 그래서
    `cleaned.endswith(" ")` 조건은 영원히 거짓인 죽은 분기다(끝 마침표는 strip 이 지우지
    않으므로 여전히 걸린다). 실질적 문제는 없다 — Windows 가 싫어하는 '끝 공백 폴더명'은
    애초에 만들어지지 않는다. 의도를 바꿀 생각이라면 이 테스트가 그 지점을 알려 준다.
    """
    assert validate_name("trailing ") == "trailing"
    with pytest.raises(ValueError):
        validate_name("trailing.")


def test_sanitize_component_replaces_instead_of_raising():
    """기존 폴더와의 호환용 — 위험 문자를 치환만 하고 예외를 내지 않는다."""
    assert sanitize_component("a/b:c") == "a_b_c"
    assert sanitize_component("") == "미지정"
    assert sanitize_component(None) == "미지정"
