"""
RunManager — 점검 실행(Run) 생명주기의 유일한 관리자.

기존 워크스페이스 아키텍처(engine/profile_manager.py의 Profile/RunHandle,
core/storage_service.py의 StorageService)는 그대로 두고, 그 위에 "실행 하나"의
생성/재개/종료/중단/재시도/조회/삭제/보관을 한 곳으로 모은다.

이전에는 engine/collector.py, api/terminal_inspection_api.py, api/log_analysis_run_api.py가
각자 진행률/성공-실패-스킵 카운트/상태를 서로 다른 방식(인메모리 dict, ad-hoc manifest.json 등)으로
들고 있었다. 이제 그 상태는 모두 RunHandle의 session.json/metadata.json에 저장되고, 이 파일이
그 상태를 읽고 쓰는 유일한 경로다 — 어떤 모듈도 runs/<run_id>/ 폴더를 직접 만들거나
session.json/metadata.json을 직접 덮어써서는 안 된다.

session.json (RunSession): Run ID, Customer, Profile, Start Time, End Time, Duration,
    Current Status, Progress, Success/Failed/Skipped Count, User, Application Version.
metadata.json (RunMetadata): Device Count, Command Count, Platform, Execution Mode,
    Report Version, Parser Version, Analyzer Version.

비정상 종료 후 재시작 복구: 앱이 죽었을 때 status가 RUNNING으로 남은 run은
recover_incomplete_runs()가 시작 시 스캔해서 FAILED(복구 가능)로 표시하고,
사용자가 resume_run()으로 이어서 진행할 수 있게 한다.
"""
import datetime
import getpass
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

from core.paths import AppPaths
from core.storage_service import storage_service
from engine.profile_manager import profile_manager, RunHandle


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _new_run_id() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _app_version() -> str:
    version_file = AppPaths.app_root() / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


class RunStatus(str, Enum):
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    COMPLETED = "COMPLETED"


_TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.ABORTED}


class RunManagerError(Exception):
    """RunManager 연산이 실패했을 때(존재하지 않는 run, 잘못된 상태 전이 등) 던지는 공통 예외."""


@dataclass
class RunSession:
    """session.json 스키마 — run 하나의 실행 상태."""
    run_id: str
    customer: str
    profile: str
    start_time: str
    end_time: Optional[str] = None
    duration_sec: Optional[float] = None
    status: str = RunStatus.READY.value
    progress: float = 0.0
    success_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    user: Optional[str] = None
    app_version: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RunSession":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


@dataclass
class RunMetadata:
    """metadata.json 스키마 — run 하나의 실행 구성."""
    device_count: int = 0
    command_count: int = 0
    platform: Optional[str] = None
    execution_mode: Optional[str] = None
    report_version: Optional[str] = None
    parser_version: Optional[str] = None
    analyzer_version: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RunMetadata":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


class RunManager:
    """실행(Run) 생명주기 전체(Create/Resume/Finish/Abort/Retry/Load/List/Delete/Archive)의
    단일 진입점. 폴더 생성은 StorageService.create_run()에만 위임하고, 이 클래스는 그 위에서
    session.json/metadata.json의 의미(상태 전이·진행률·카운트)를 강제한다."""

    def __init__(self, profile_mgr=None):
        self._profile_manager = profile_mgr or profile_manager
        # 프로파일별 "현재 활성 run" — 모듈들이 하드코딩 경로 대신 이 캐시를 통해 접근한다.
        self._active_runs: dict[tuple, RunHandle] = {}

    # ---- 내부 헬퍼 ---------------------------------------------------------

    @staticmethod
    def _key(customer: str, profile_name: str) -> tuple:
        return (customer, profile_name)

    def _get_run_handle(self, customer: str, profile_name: str, run_id: str) -> RunHandle:
        profile = self._profile_manager.get_profile(customer, profile_name)
        run_path = profile.path / "runs" / run_id
        if not run_path.is_dir():
            raise RunManagerError(f"실행을 찾을 수 없습니다: {customer}/{profile_name}/runs/{run_id}")
        return RunHandle(profile=profile, run_id=run_id, path=run_path)

    def _read_session(self, run: RunHandle) -> RunSession:
        data = storage_service.load_json(run, "session.json", default=None)
        if data is None:
            raise RunManagerError(f"session.json이 없습니다: {run.path}")
        return RunSession.from_dict(data)

    def _write_session(self, run: RunHandle, session: RunSession) -> None:
        storage_service.save_session(run, session.to_dict())

    def _read_metadata(self, run: RunHandle) -> RunMetadata:
        return RunMetadata.from_dict(storage_service.load_json(run, "metadata.json", default={}))

    def _write_metadata(self, run: RunHandle, metadata: RunMetadata) -> None:
        storage_service.save_run_metadata(run, metadata.to_dict())

    # ---- Create -------------------------------------------------------------

    def create_run(self, customer: str, profile_name: str, *, user: str = None,
                    device_count: int = 0, command_count: int = 0, platform: str = None,
                    execution_mode: str = None, report_version: str = None,
                    parser_version: str = None, analyzer_version: str = None) -> RunHandle:
        """runs/<YYYY-MM-DD_HHMMSS>/ 를 새로 만들고 session.json/metadata.json을 채운다.
        이 메서드가 유일한 run 폴더 생성 경로다 — 다른 모듈은 직접 mkdir하지 않는다."""
        profile = self._profile_manager.get_profile(customer, profile_name)
        run = storage_service.create_run(profile)  # 폴더 생성은 StorageService에 위임(중복 제거)

        session = RunSession(
            run_id=run.run_id, customer=customer, profile=profile_name,
            start_time=_now_iso(), status=RunStatus.READY.value,
            user=user or getpass.getuser(), app_version=_app_version(),
        )
        self._write_session(run, session)

        metadata = RunMetadata(
            device_count=device_count, command_count=command_count, platform=platform,
            execution_mode=execution_mode, report_version=report_version,
            parser_version=parser_version, analyzer_version=analyzer_version,
        )
        self._write_metadata(run, metadata)

        self._active_runs[self._key(customer, profile_name)] = run
        return run

    # ---- Load / List ----------------------------------------------------------

    def load_run(self, customer: str, profile_name: str, run_id: str):
        """(RunHandle, RunSession, RunMetadata) 튜플로 저장된 상태를 그대로 읽어온다."""
        run = self._get_run_handle(customer, profile_name, run_id)
        return run, self._read_session(run), self._read_metadata(run)

    def list_runs(self, customer: str, profile_name: str) -> list:
        """최신 run이 앞에 오도록 session.json 요약 목록을 반환한다."""
        run_ids = self._profile_manager.list_runs(customer, profile_name)
        summaries = []
        for run_id in run_ids:
            try:
                run = self._get_run_handle(customer, profile_name, run_id)
                session = self._read_session(run)
                summaries.append(session.to_dict())
            except RunManagerError:
                continue
        summaries.sort(key=lambda s: s.get("run_id", ""), reverse=True)
        return summaries

    def get_active_run(self, customer: str, profile_name: str) -> Optional[RunHandle]:
        """모듈들이 '지금 진행 중인 run'에 접근하는 표준 경로. 캐시에 없으면 session.json에서
        RUNNING/PAUSED 상태인 가장 최근 run을 찾아 복구한다(예: 프로세스 재시작 후 재접근)."""
        key = self._key(customer, profile_name)
        if key in self._active_runs:
            return self._active_runs[key]
        for run_id in reversed(self._profile_manager.list_runs(customer, profile_name)):
            try:
                run = self._get_run_handle(customer, profile_name, run_id)
                session = self._read_session(run)
            except RunManagerError:
                continue
            if session.status in (RunStatus.RUNNING.value, RunStatus.PAUSED.value):
                self._active_runs[key] = run
                return run
        return None

    # ---- 진행 중 상태 갱신 --------------------------------------------------------

    def start_run(self, run: RunHandle) -> RunSession:
        """READY -> RUNNING. 이미 RUNNING/PAUSED면 그대로 이어간다(중복 호출 안전)."""
        session = self._read_session(run)
        if session.status not in (RunStatus.RUNNING.value, RunStatus.PAUSED.value):
            session.status = RunStatus.RUNNING.value
            self._write_session(run, session)
        self._active_runs[self._key(run.profile.customer, run.profile.name)] = run
        return session

    def update_progress(self, run: RunHandle, *, progress: float = None, success_count: int = None,
                         failed_count: int = None, skipped_count: int = None,
                         status: RunStatus = None) -> RunSession:
        """실행 도중 진행률/카운트/상태를 갱신한다. 넘기지 않은 필드는 그대로 유지."""
        session = self._read_session(run)
        if progress is not None:
            session.progress = max(0.0, min(100.0, progress))
        if success_count is not None:
            session.success_count = success_count
        if failed_count is not None:
            session.failed_count = failed_count
        if skipped_count is not None:
            session.skipped_count = skipped_count
        if status is not None:
            session.status = RunStatus(status).value
        self._write_session(run, session)
        return session

    def increment_counts(self, run: RunHandle, *, success: int = 0, failed: int = 0, skipped: int = 0) -> RunSession:
        """증분 방식으로 카운트를 올린다 — 병렬 수집처럼 완료되는 대로 하나씩 반영할 때 사용."""
        session = self._read_session(run)
        session.success_count += success
        session.failed_count += failed
        session.skipped_count += skipped
        self._write_session(run, session)
        return session

    # ---- 종료 상태 전이 ------------------------------------------------------

    def _finalize(self, run: RunHandle, status: RunStatus) -> RunSession:
        session = self._read_session(run)
        session.status = status.value
        session.end_time = _now_iso()
        try:
            start = datetime.datetime.fromisoformat(session.start_time)
            session.duration_sec = (datetime.datetime.fromisoformat(session.end_time) - start).total_seconds()
        except (ValueError, TypeError):
            session.duration_sec = None
        self._write_session(run, session)
        self._active_runs.pop(self._key(run.profile.customer, run.profile.name), None)
        return session

    def finish_run(self, run: RunHandle) -> RunSession:
        """정상 완료 처리: RUNNING/PAUSED -> COMPLETED."""
        return self._finalize(run, RunStatus.COMPLETED)

    def abort_run(self, run: RunHandle) -> RunSession:
        """사용자 중단: 어떤 상태에서든 -> ABORTED."""
        return self._finalize(run, RunStatus.ABORTED)

    def fail_run(self, run: RunHandle, reason: str = None) -> RunSession:
        """예외로 인한 실패 종료: -> FAILED. reason은 metadata.json에 남긴다."""
        session = self._finalize(run, RunStatus.FAILED)
        if reason:
            metadata = self._read_metadata(run)
            storage_service.save_json(run, "metadata.json", {**metadata.to_dict(), "failure_reason": reason})
        return session

    def pause_run(self, run: RunHandle) -> RunSession:
        """일시 중지: RUNNING -> PAUSED. resume_run()으로 이어서 진행 가능."""
        return self.update_progress(run, status=RunStatus.PAUSED)

    # ---- Resume / Retry -------------------------------------------------------

    def resume_run(self, customer: str, profile_name: str, run_id: str) -> RunHandle:
        """PAUSED 또는 (비정상 종료로 남은) RUNNING 상태의 run을 이어서 진행할 수 있게 한다.
        이미 COMPLETED/ABORTED로 끝난 run은 재개할 수 없다 — retry_run()으로 새 run을 만들 것."""
        run = self._get_run_handle(customer, profile_name, run_id)
        session = self._read_session(run)
        if session.status in (RunStatus.COMPLETED.value, RunStatus.ABORTED.value):
            raise RunManagerError(f"이미 종료된 실행은 재개할 수 없습니다(status={session.status}). retry_run()을 사용하세요.")
        session.status = RunStatus.RUNNING.value
        self._write_session(run, session)
        self._active_runs[self._key(customer, profile_name)] = run
        return run

    def retry_run(self, customer: str, profile_name: str, run_id: str) -> RunHandle:
        """실패/중단된 run과 동일한 실행 구성(metadata.json)으로 새 Run을 만든다.
        원본 run은 그대로 보존되어 실패 원인 비교/재현이 가능하다."""
        old_run = self._get_run_handle(customer, profile_name, run_id)
        old_metadata = self._read_metadata(old_run)
        new_run = self.create_run(
            customer, profile_name,
            device_count=old_metadata.device_count, command_count=old_metadata.command_count,
            platform=old_metadata.platform, execution_mode=old_metadata.execution_mode,
            report_version=old_metadata.report_version, parser_version=old_metadata.parser_version,
            analyzer_version=old_metadata.analyzer_version,
        )
        storage_service.save_json(new_run, "metadata.json",
                                   {**self._read_metadata(new_run).to_dict(), "retry_of": run_id})
        return new_run

    # ---- Delete / Archive -------------------------------------------------------

    def delete_run(self, customer: str, profile_name: str, run_id: str) -> None:
        profile = self._profile_manager.get_profile(customer, profile_name)
        storage_service.delete_run(profile, run_id)
        self._active_runs.pop(self._key(customer, profile_name), None)

    def archive_run(self, customer: str, profile_name: str, run_id: str) -> Path:
        profile = self._profile_manager.get_profile(customer, profile_name)
        dst = storage_service.archive_run(profile, run_id)
        self._active_runs.pop(self._key(customer, profile_name), None)
        return dst

    # ---- 비정상 종료 복구 ---------------------------------------------------------

    def recover_incomplete_runs(self, customer: str, profile_name: str) -> list:
        """앱 시작 시 호출: status가 RUNNING인 채로 남은 run(=비정상 종료 후보)을 찾아
        PAUSED로 내려놓고 목록을 돌려준다. 사용자가 그중 하나를 골라 resume_run()하면 이어진다."""
        recovered = []
        for run_id in self._profile_manager.list_runs(customer, profile_name):
            try:
                run = self._get_run_handle(customer, profile_name, run_id)
                session = self._read_session(run)
            except RunManagerError:
                continue
            if session.status == RunStatus.RUNNING.value:
                session.status = RunStatus.PAUSED.value
                self._write_session(run, session)
                recovered.append(session.to_dict())
        return recovered


# 상태는 self._active_runs 캐시뿐이고 그 외엔 전부 session.json/metadata.json에서 읽으므로,
# 앱 전역에서 공유되는 단일 인스턴스로 충분하다.
run_manager = RunManager()
