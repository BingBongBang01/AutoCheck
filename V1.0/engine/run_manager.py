"""
RunManager — 점검 실행(Run) 생명주기의 유일한 관리자.

기존 워크스페이스 아키텍처(engine/profile_manager.py의 Profile/RunHandle,
core/storage_service.py의 StorageService)는 그대로 두고, 그 위에 "실행 하나"의
생성/진행/종료/중단/조회를 한 곳으로 모은다(재개·재시도·삭제·보관은 쓰는 화면이 없어 지웠다).

이전에는 engine/collector.py, api/terminal_inspection_api.py, api/log_analysis_run_api.py가
각자 진행률/성공-실패-스킵 카운트/상태를 서로 다른 방식(인메모리 dict, ad-hoc manifest.json 등)으로
들고 있었다. 이제 그 상태는 모두 RunHandle의 session.json/metadata.json에 저장되고, 이 파일이
그 상태를 읽고 쓰는 유일한 경로다 — 어떤 모듈도 runs/<run_id>/ 폴더를 직접 만들거나
session.json/metadata.json을 직접 덮어써서는 안 된다.

session.json (RunSession): Run ID, Customer, Profile, Start Time, End Time, Duration,
    Current Status, Progress, Success/Failed/Skipped Count, User, Application Version.
metadata.json (RunMetadata): Device Count, Command Count, Platform, Execution Mode,
    Report Version, Parser Version, Analyzer Version.

비정상 종료 후 재시작 복구: 앱이 죽었을 때 status가 RUNNING/PAUSED로 남은 run은
recover_all_incomplete_runs()가 시작 시(main.py) 스캔해서 ABORTED로 마무리하고 이유를 남긴다.
"""
import datetime
import getpass
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional

from core.paths import safe_child
from core.process_lock import is_locked, profile_lock_path
from core.storage_service import storage_service
from core.utils import format_iso
from core.version import app_version
from engine.profile_manager import Profile, profile_manager, RunHandle


# session.json 을 읽을 수 없는 run 의 목록 표시용 상태(RunStatus 가 아니다 — 저장하지 않는다).
CORRUPTED_STATUS = "CORRUPTED"
INTERRUPTED_REASON = "앱이 실행 도중 종료되어 중단됨(다음 시작에서 정리)"


class RunStatus(str, Enum):
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    COMPLETED = "COMPLETED"


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
        try:
            # run_id 는 화면에서 넘어온다 — '..' 같은 값으로 프로파일 밖을 가리키지 못하게 한다.
            run_path = safe_child(profile.path / "runs", run_id)
        except ValueError as exc:
            raise RunManagerError(str(exc)) from exc
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
            start_time=format_iso(), status=RunStatus.READY.value,
            user=user or getpass.getuser(), app_version=app_version(),
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

    def run_handle(self, customer: str, profile_name: str, run_id: str) -> RunHandle:
        """파일을 읽지 않고 run 핸들만 — 메타데이터가 깨진 run 도 폴더는 열 수 있게."""
        return self._get_run_handle(customer, profile_name, run_id)

    def load_run_metadata(self, customer: str, profile_name: str, run_id: str):
        """(RunHandle, RunMetadata) — session.json 은 읽지 않는다.

        list_runs() 가 이미 run 마다 session.json 을 읽어 요약을 돌려주므로, 그 뒤에
        load_run() 을 부르면 같은 파일을 두 번 읽는다. 목록을 훑으면서 metadata 만 더
        필요한 호출부(api/workspace_api.py 의 워크스페이스 스냅샷)를 위한 것이다.
        """
        run = self._get_run_handle(customer, profile_name, run_id)
        return run, self._read_metadata(run)

    def list_runs(self, customer: str, profile_name: str) -> list:
        """최신 run이 앞에 오도록 session.json 요약 목록을 반환한다.

        session.json 이 깨진 run 은 status 'CORRUPTED' 와 error 만 든 요약으로 남긴다(목록에서 보이게).
        예전엔 RunManagerError 만 잡아서, JSON 이 깨진 run 하나가 Workspace 탭 전체를 오류로 만들었다."""
        run_ids = self._profile_manager.list_runs(customer, profile_name)
        summaries = []
        for run_id in run_ids:
            try:
                run = self._get_run_handle(customer, profile_name, run_id)
            except RunManagerError:
                continue
            try:
                summaries.append(self._read_session(run).to_dict())
            except Exception as exc:
                summaries.append({"run_id": run_id, "customer": customer, "profile": profile_name,
                                  "status": CORRUPTED_STATUS, "error": f"{type(exc).__name__}: {exc}"[:300]})
        summaries.sort(key=lambda s: s.get("run_id", ""), reverse=True)
        return summaries

    def get_active_run(self, customer: str, profile_name: str,
                       summaries: list = None) -> Optional[RunHandle]:
        """모듈들이 '지금 진행 중인 run'에 접근하는 표준 경로. 캐시에 없으면 session.json에서
        RUNNING/PAUSED 상태인 가장 최근 run을 찾아 복구한다(예: 프로세스 재시작 후 재접근).

        summaries: list_runs() 결과를 이미 갖고 있으면 넘긴다(최신 먼저 정렬 가정).
        그러면 session.json 을 다시 읽지 않고 그 요약으로 판정한다.

        왜 필요한가: 진행 중인 run 이 **없으면** 캐시가 채워지지 않으므로 호출마다 모든 run 의
        session.json 을 훑는다. 워크스페이스 탭은 대개 그 상태에서 폴링되므로(작업 중이 아님)
        run 개수만큼의 파일 읽기가 폴링마다 반복됐다. 판정 규칙(RUNNING/PAUSED 중 최신)을
        호출부로 복사하지 않기 위해 인자를 받는 쪽을 택했다 — 규칙이 두 곳에 있으면 어긋난다.
        """
        key = self._key(customer, profile_name)
        if key in self._active_runs:
            return self._active_runs[key]

        if summaries is not None:
            for summary in summaries:      # list_runs()는 최신 먼저
                if summary.get("status") not in (RunStatus.RUNNING.value, RunStatus.PAUSED.value):
                    continue
                try:
                    run = self._get_run_handle(customer, profile_name, summary["run_id"])
                except RunManagerError:
                    continue
                self._active_runs[key] = run
                return run
            return None

        for run_id in reversed(self._profile_manager.list_runs(customer, profile_name)):
            try:
                run = self._get_run_handle(customer, profile_name, run_id)
                session = self._read_session(run)
            except Exception:      # 깨진 session.json 은 '진행 중'이 아니다
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
        session = self._close_session(run, status)
        self._active_runs.pop(self._key(run.profile.customer, run.profile.name), None)
        return session

    def _close_session(self, run: RunHandle, status: RunStatus) -> RunSession:
        """session.json 에 종료 상태·시각·소요 시간을 쓴다(활성 run 캐시는 건드리지 않는다)."""
        session = self._read_session(run)
        session.status = status.value
        session.end_time = format_iso()
        try:
            start = datetime.datetime.fromisoformat(session.start_time)
            session.duration_sec = (datetime.datetime.fromisoformat(session.end_time) - start).total_seconds()
        except (ValueError, TypeError):
            session.duration_sec = None
        self._write_session(run, session)
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

    # ---- 비정상 종료 복구 ---------------------------------------------------------

    def recover_all_incomplete_runs(self) -> list:
        """앱 시작 때 부른다(main.py). 실행 중 상태는 프로세스를 넘어 살아남지 않으므로, 시작 시점에
        RUNNING/PAUSED 로 남은 run 은 이전 실행이 도중에 끝난 흔적이다 — ABORTED 로 마무리하고
        이유를 metadata.json 에 남긴다. 예전엔 이 정리가 어디에도 연결되지 않아서 그런 run 이
        영구히 '현재 Run'(get_active_run)으로 붙어 있었다. 재개(resume) 기능은 화면에 없다.

        data/<고객사>/<프로파일>/runs 를 직접 훑는다 — 시작 경로에서 프로파일 폴더를 만들거나
        고치지(repair) 않기 위해서다."""
        root = self._profile_manager.data_root
        recovered = []
        if not root.is_dir():
            return recovered
        for cdir in sorted(p for p in root.iterdir() if p.is_dir()):
            for pdir in sorted(p for p in cdir.iterdir() if p.is_dir() and (p / "runs").is_dir()):
                recovered.extend(self._recover_profile_runs(Profile(customer=cdir.name, name=pdir.name, path=pdir)))
        return recovered

    def _recover_profile_runs(self, profile: Profile) -> list:
        recovered = []
        active = self._active_runs.get(self._key(profile.customer, profile.name))
        runs_dir = profile.path / "runs"
        if not runs_dir.is_dir():
            return recovered
        # 다른 프로세스(작업 스케줄러가 띄운 무인 정기점검)가 이 프로파일을 점검하는 중이면 그 run 은 살아 있다 —
        # 앱을 여는 순간 '중단'으로 바꾸면 안 된다(IMPROVEMENT_PLAN 4-3, core/process_lock.py).
        if is_locked(profile_lock_path(profile.path)):
            return recovered
        for run_path in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
            if active is not None and active.run_id == run_path.name:
                continue          # 이 프로세스가 지금 돌리고 있는 run
            run = RunHandle(profile=profile, run_id=run_path.name, path=run_path)
            try:
                session = self._read_session(run)
                if session.status not in (RunStatus.RUNNING.value, RunStatus.PAUSED.value):
                    continue
                closed = self._close_session(run, RunStatus.ABORTED)
                metadata = storage_service.load_json(run, "metadata.json", default={}) or {}
                storage_service.save_json(run, "metadata.json",
                                          {**metadata, "failure_reason": INTERRUPTED_REASON})
            except Exception:
                continue          # 깨진 run 은 목록에서 CORRUPTED 로 보인다(list_runs)
            recovered.append(closed.to_dict())
        return recovered


# 상태는 self._active_runs 캐시뿐이고 그 외엔 전부 session.json/metadata.json에서 읽으므로,
# 앱 전역에서 공유되는 단일 인스턴스로 충분하다.
run_manager = RunManager()
