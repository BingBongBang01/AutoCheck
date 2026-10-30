"""
StorageService — data/ 아래 파일 쓰기(텍스트·JSON·CSV·zip)·읽기와 run 폴더 생성의 단일 출처.

이전에는 engine/collector.py, engine/log_masking.py, engine/session_timeline.py,
api/terminal_inspection_api.py 등 여러 모듈이 각자 open()/os.makedirs()/shutil.*를 직접
호출했다. 이제 이 서비스를 거치면 다음을 한 곳에서 보장한다:

  - 상위 폴더 자동 생성 (pathlib.Path.mkdir(parents=True, exist_ok=True))
  - 원자적 쓰기 — 같은 폴더에 임시 파일로 먼저 쓰고 os.replace()로 교체하므로,
    쓰는 도중 프로세스가 죽어도 대상 파일이 반쯤 쓰인 상태로 남지 않는다.
  - 실수로 덮어쓰기 방지 — overwrite=False(기본은 연산별로 다름)면 대상이 이미 있을 때
    "<이름>_v2<확장자>"처럼 버전 파일명을 자동으로 찾아 저장한다.
  - 모든 연산에 대한 로깅(logging 모듈, logger name="storage_service").

모든 연산은 "어디에 쓸지"를 문자열 경로로 직접 받지 않고, Profile 또는 RunHandle
(engine/profile_manager.py) 객체 + 그 안에서의 상대경로만 받는다. 그래서 "고객사/프로파일/
실행(run) 폴더 규칙"이 이 파일 하나에서만 pathlib으로 계산되고, 호출부는 절대 경로 문자열을
조립하지 않는다.
"""
import csv
import io
import json
import logging
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from core.paths import safe_join
from core.workspace_model import RUN_SUBDIRS, RunHandle

# 기록은 앱 로그 파일로 간다 — main.py 가 core.app_logger.install_logging_bridge() 로 logging 을 앱 로그에
# 잇는다. 예전엔 여기서 콘솔(stderr) 핸들러만 붙여서, 콘솔이 없는 EXE(console=False)에서는 전부 사라졌다.
# 연산 추적(_log_op)은 DEBUG — 파일에만 남고 화면 로그(INFO 이상)를 채우지 않는다(워크스페이스 폴링이
# run 마다 load_json 을 부른다). 핸들러가 없는 스크립트·테스트에서는 WARNING 이상만 콘솔에 나온다.
logger = logging.getLogger("storage_service")
logger.setLevel(logging.DEBUG)


class StorageOperationError(Exception):
    """StorageService 연산이 실패했을 때(잘못된 target 타입 등) 던지는 공통 예외."""


@dataclass(frozen=True)
class PathTarget:
    """Profile/RunHandle이 없는 레거시 호출부(예: 고객사/프로파일 미지정 상태의 채점 파이프라인)를
    위한 최소 어댑터. root_path만 노출해서 StorageService가 요구하는 "target" 프로토콜을 만족시킨다.
    신규 코드는 절대 이걸 직접 만들지 말고 ProfileManager.get_profile()/create_run()이 돌려주는
    Profile/RunHandle을 쓸 것 — 이 클래스는 순수 하위 호환 통로다."""
    path: Path

    @property
    def root_path(self) -> Path:
        return self.path


def _require_root_path(target) -> Path:
    root = getattr(target, "root_path", None)
    if root is None:
        raise StorageOperationError(
            "StorageService 연산은 root_path를 제공하는 Profile 또는 RunHandle 객체가 필요합니다 "
            f"(받은 값: {type(target).__name__}). 하드코딩된 경로 문자열은 전달할 수 없습니다."
        )
    return Path(root)


class StorageService:
    """생성자 인자 없이 바로 쓸 수 있는 무상태(stateless) 서비스 — 내부 상태를 전혀 들고 있지 않고
    매 연산이 target(Profile/RunHandle)에서 경로를 계산할 뿐이라 인스턴스를 몇 개를 만들어도 무방하다."""

    # ---- 경로 계산 / 충돌 처리 --------------------------------------------

    @staticmethod
    def resolve(target, relative_path) -> Path:
        """target 아래의 상대경로. 상대경로가 target 밖(.., 절대경로)을 가리키면 StorageOperationError —
        상대경로에는 장비명처럼 외부에서 온 이름이 섞이므로, 이 한 곳에서 막으면 모든 저장 연산이 안전하다."""
        try:
            return safe_join(_require_root_path(target), relative_path)
        except ValueError as exc:
            raise StorageOperationError(str(exc)) from exc

    @staticmethod
    def _versioned_path(path: Path) -> Path:
        """path가 이미 있으면 "<이름>_v2<확장자>", "_v3" ... 순으로 비어있는 이름을 찾아 반환."""
        if not path.exists():
            return path
        stem, suffix = path.stem, path.suffix
        n = 2
        while True:
            candidate = path.with_name(f"{stem}_v{n}{suffix}")
            if not candidate.exists():
                return candidate
            n += 1

    @classmethod
    def _prepare_target(cls, path: Path, overwrite: bool) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not overwrite:
            versioned = cls._versioned_path(path)
            logger.info("충돌 방지: %s 이미 존재 -> %s 로 저장", path, versioned)
            return versioned
        return path

    # ---- 원자적 쓰기 --------------------------------------------------------

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)  # 같은 파일시스템 내 rename은 원자적 — 중간에 죽어도 원본/신규본 둘 중 하나만 존재
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)

    @classmethod
    def _atomic_write_text(cls, path: Path, text: str, encoding: str = "utf-8") -> None:
        cls._atomic_write_bytes(path, text.encode(encoding))

    def _log_op(self, op: str, path: Path, extra: str = "") -> None:
        logger.debug("%s -> %s%s", op, path, f" ({extra})" if extra else "")

    # ---- 범용 텍스트/바이너리 --------------------------------------------

    def save_text(self, target, relative_path, text: str, *, overwrite: bool = True) -> Path:
        path = self._prepare_target(self.resolve(target, relative_path), overwrite)
        self._atomic_write_text(path, text)
        self._log_op("save_text", path, f"{len(text)}자")
        return path

    def save_zip(self, target, relative_path, entries, *, overwrite: bool = True) -> Path:
        """entries([(zip 안 이름, 원본 파일 경로)])를 zip 하나로 저장한다.

        zip 을 메모리(BytesIO)에 다 만든 뒤 한 번에 쓰면 워크스페이스 크기만큼 메모리를 쓴다.
        같은 폴더의 임시 파일에 바로 압축해 쓰고 os.replace 로 교체한다 — 원자성은 다른 쓰기와 같다.
        entries 는 임시 파일을 만들기 전에 목록으로 확정한다(그 폴더를 훑는 중이어도 임시 파일이 끼지 않게)."""
        entries = list(entries)
        path = self._prepare_target(self.resolve(target, relative_path), overwrite)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as zf:
                    for arcname, source in entries:
                        zf.write(source, arcname=arcname)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        self._log_op("save_zip", path, f"{len(entries)}개 파일")
        return path

    # ---- JSON ------------------------------------------------------------

    def save_json(self, target, relative_path, data, *, overwrite: bool = True, indent: int = 2) -> Path:
        path = self._prepare_target(self.resolve(target, relative_path), overwrite)
        self._atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=indent))
        self._log_op("save_json", path)
        return path

    def load_json(self, target, relative_path, default=None):
        path = self.resolve(target, relative_path)
        if not path.exists():
            self._log_op("load_json(미존재)", path)
            return default
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        self._log_op("load_json", path)
        return data

    # ---- CSV -------------------------------------------------------------

    def save_csv(self, target, relative_path, rows, *, fieldnames=None, overwrite: bool = True) -> Path:
        """rows: [dict, ...] (fieldnames 생략 시 첫 행 키에서 자동 추론) 또는 [[..], ...]."""
        rows = list(rows or [])
        buf = io.StringIO()
        if rows and isinstance(rows[0], dict):
            fieldnames = fieldnames or list(rows[0].keys())
            writer = csv.DictWriter(buf, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        else:
            writer = csv.writer(buf)
            if fieldnames:
                writer.writerow(fieldnames)
            writer.writerows(rows)
        path = self._prepare_target(self.resolve(target, relative_path), overwrite)
        self._atomic_write_text(path, buf.getvalue())
        self._log_op("save_csv", path, f"{len(rows)}행")
        return path

    # ---- 실행(run)-스코프 도메인 연산 -----------------------------------------
    # run은 반드시 RunHandle(또는 root_path를 제공하는 호환 객체)이어야 하고,
    # 아래 메서드들은 run.root_path 기준 상대경로(raw/, masked/, parsed/, analysis/, reports/, exports/)에
    # 만 쓴다 — 폴더 규칙을 호출부가 다시 조립하지 못하게 여기 한 곳에 고정.

    def save_log(self, run, device_name: str, content: str, *, overwrite: bool = False) -> Path:
        """원본 SSH 수집 로그 1건 저장: raw/<device_name>.txt."""
        path = self.save_text(run, f"raw/{device_name}.txt", content, overwrite=overwrite)
        self._log_op("save_log", path)
        return path

    def save_session(self, run, session_data: dict, *, overwrite: bool = True) -> Path:
        """실행 단위 세션 메타(session.json) — run 폴더 바로 밑에 저장."""
        return self.save_json(run, "session.json", session_data, overwrite=overwrite)

    def save_run_metadata(self, run, metadata: dict, *, overwrite: bool = True) -> Path:
        """실행 단위 메타데이터(metadata.json) — run 폴더 바로 밑에 저장."""
        return self.save_json(run, "metadata.json", metadata, overwrite=overwrite)

    # ---- 실행(run) 생명주기 ---------------------------------------------------

    def create_run(self, profile) -> "RunHandle":
        """runs/ 아래 타임스탬프 Run 폴더 + 표준 서브폴더(raw/masked/parsed/analysis/reports/exports)를
        만들고 session.json/metadata.json 뼈대까지 채운 RunHandle을 반환한다.
        서로 다른 실행의 산출물이 섞이지 않도록 항상 새 폴더를 만들고, 기존 Run은 절대 건드리지 않는다."""
        from core.utils import format_run_id, format_iso, current_datetime

        pdir = _require_root_path(profile)
        (pdir / "runs").mkdir(parents=True, exist_ok=True)
        ts = format_run_id()
        while True:
            run_path = pdir / "runs" / ts
            try:
                # exist_ok=False 로 '만들기'와 '있는지 확인'을 한 번에 한다. 예전엔 exists() 로 확인한 뒤
                # exist_ok=True 로 만들어서, 같은 초에 시작한 두 실행이 한 run 폴더를 함께 쓸 수 있었다.
                run_path.mkdir(exist_ok=False)
                break
            except FileExistsError:
                ts = current_datetime().strftime("%Y-%m-%d_%H%M%S_%f")
        for sub in RUN_SUBDIRS:
            (run_path / sub).mkdir(parents=True, exist_ok=True)

        run = RunHandle(profile=profile, run_id=ts, path=run_path)
        now = format_iso()
        self.save_session(run, {
            "run_id": ts,
            "customer": getattr(profile, "customer", None),
            "profile": getattr(profile, "name", None),
            "started_at": now,
        })
        self.save_run_metadata(run, {"run_id": ts, "status": "running"})
        self._log_op("create_run", run_path)
        return run

# 상태가 없는 서비스이므로 대부분의 호출부는 이 공유 인스턴스 하나로 충분하다.
storage_service = StorageService()
