"""워크스페이스 모델 — 프로파일·실행(run) 폴더를 가리키는 핸들과 표준 하위 폴더 이름.

core.storage_service(쓰기)와 engine.profile_manager·run_manager(관리)가 함께 쓰는 값이라 core 에 둔다.
예전엔 engine/profile_manager.py 에 있어서 core/storage_service.py 가 engine 을 지역 import 로 거꾸로
불렀다(지역 import 는 순환을 피할 뿐 의존을 없애지 않는다 — IMPROVEMENT_PLAN 3-6). engine.profile_manager 는
하위 호환을 위해 같은 이름을 그대로 내보낸다.
"""
from dataclasses import dataclass
from pathlib import Path

__all__ = ["PROFILE_SUBDIRS", "RUN_SUBDIRS", "Profile", "RunHandle"]

PROFILE_SUBDIRS = ("profile", "inventory", "commands", "baselines", "runs", "history", "cache",
                    "archive", "reports")
# problem/ = 이상 징후 블록만 뽑아낸 텍스트(01_problem_log의 새 위치). analysis/는 JSON 산출물용이라
# 텍스트 로그를 섞지 않고 별도 폴더로 둔다 — engine/log_storage.py의 RUN_DIR_NAMES와 짝을 맞춘다.
RUN_SUBDIRS = ("raw", "masked", "problem", "parsed", "analysis", "reports", "exports")


@dataclass(frozen=True)
class Profile:
    """고객사/프로파일 워크스페이스 하나를 가리키는 핸들.
    core.storage_service.StorageService의 모든 연산은 하드코딩된 경로 문자열 대신 이 객체
    (또는 그 실행 단위인 RunHandle)를 받는다 — ProfileManager.get_profile()로만 만들어야 한다."""
    customer: str
    name: str
    path: Path

    @property
    def root_path(self) -> Path:
        return self.path


@dataclass(frozen=True)
class RunHandle:
    """profile의 runs/<run_id>/ 하나를 가리키는 핸들 — StorageService.create_run()이 만들어준다."""
    profile: Profile
    run_id: str
    path: Path

    @property
    def root_path(self) -> Path:
        return self.path

    @property
    def raw_dir(self) -> Path:
        return self.path / "raw"

    @property
    def masked_dir(self) -> Path:
        return self.path / "masked"

    @property
    def parsed_dir(self) -> Path:
        return self.path / "parsed"

    @property
    def analysis_dir(self) -> Path:
        return self.path / "analysis"

    @property
    def reports_dir(self) -> Path:
        return self.path / "reports"

    @property
    def exports_dir(self) -> Path:
        return self.path / "exports"
