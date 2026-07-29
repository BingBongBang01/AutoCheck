"""
StorageService — 모든 파일시스템 연산(생성/읽기/갱신/삭제/이동/복사/내보내기)의 유일한 출처.

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
from typing import TYPE_CHECKING, Any, Dict, List, Optional, TypeVar, Union

if TYPE_CHECKING:
    from engine.profile_manager import RunHandle
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("storage_service")
if not logger.handlers:
    # 애플리케이션이 별도 로깅 설정을 하지 않은 채로(예: 단위 테스트, 스크립트 실행) 쓰여도
    # 최소한 콘솔에는 남도록 기본 핸들러 하나를 붙여둔다. 상위에서 logging.basicConfig()로
    # 이미 설정했다면 이 핸들러는 중복 출력 없이 propagate=True로 함께 동작한다.
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[%(asctime)s] %(name)s %(levelname)s: %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


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
        return _require_root_path(target) / Path(relative_path)

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
        logger.info("%s -> %s%s", op, path, f" ({extra})" if extra else "")

    # ---- 범용 텍스트/바이너리 --------------------------------------------

    def save_text(self, target, relative_path, text: str, *, overwrite: bool = True) -> Path:
        path = self._prepare_target(self.resolve(target, relative_path), overwrite)
        self._atomic_write_text(path, text)
        self._log_op("save_text", path, f"{len(text)}자")
        return path

    def load_text(self, target, relative_path, default: str = None) -> str:
        path = self.resolve(target, relative_path)
        if not path.exists():
            self._log_op("load_text(미존재)", path)
            return default
        with path.open("rb") as f:
            raw = f.read()
        for encoding in ("utf-8-sig", "cp949"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
        self._log_op("load_text", path)
        return text

    def save_bytes(self, target, relative_path, data: bytes, *, overwrite: bool = True) -> Path:
        path = self._prepare_target(self.resolve(target, relative_path), overwrite)
        self._atomic_write_bytes(path, data)
        self._log_op("save_bytes", path, f"{len(data)}B")
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

    def load_csv(self, target, relative_path) -> list:
        path = self.resolve(target, relative_path)
        if not path.exists():
            self._log_op("load_csv(미존재)", path)
            return []
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self._log_op("load_csv", path, f"{len(rows)}행")
        return rows

    # ---- Excel -------------------------------------------------------------

    def save_excel(self, target, relative_path, rows, *, headers=None, sheet_name: str = "Sheet1",
                   overwrite: bool = True) -> Path:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = sheet_name
        if headers:
            ws.append(list(headers))
        for row in rows or []:
            ws.append(list(row.values()) if isinstance(row, dict) else list(row))

        path = self._prepare_target(self.resolve(target, relative_path), overwrite)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        wb.save(tmp_path)
        os.replace(tmp_path, path)
        self._log_op("save_excel", path, f"{len(rows or [])}행")
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

    def save_masked(self, run, name: str, content: str, *, overwrite: bool = False) -> Path:
        """마스킹 결과 저장: masked/<name>."""
        return self.save_text(run, f"masked/{name}", content, overwrite=overwrite)

    def save_parsed(self, run, name: str, data, *, overwrite: bool = True) -> Path:
        """파서 결과 저장: parsed/<name>(.json). data가 str이면 텍스트로, 아니면 JSON으로 저장."""
        rel = f"parsed/{name}"
        if isinstance(data, str):
            return self.save_text(run, rel, data, overwrite=overwrite)
        return self.save_json(run, rel if rel.endswith(".json") else f"{rel}.json", data, overwrite=overwrite)

    def save_analysis(self, run, name: str, data, *, overwrite: bool = True) -> Path:
        """AI/규칙기반 분석 결과 저장: analysis/<name>(.json)."""
        rel = f"analysis/{name}"
        if isinstance(data, str):
            return self.save_text(run, rel, data, overwrite=overwrite)
        return self.save_json(run, rel if rel.endswith(".json") else f"{rel}.json", data, overwrite=overwrite)

    def save_report(self, run, name: str, content, *, overwrite: bool = False) -> Path:
        """사람이 보는 보고서 저장: reports/<name>. content가 bytes면 그대로, 아니면 텍스트로 저장."""
        rel = f"reports/{name}"
        if isinstance(content, (bytes, bytearray)):
            return self.save_bytes(run, rel, bytes(content), overwrite=overwrite)
        return self.save_text(run, rel, content, overwrite=overwrite)

    def save_session(self, run, session_data: dict, *, overwrite: bool = True) -> Path:
        """실행 단위 세션 메타(session.json) — run 폴더 바로 밑에 저장."""
        return self.save_json(run, "session.json", session_data, overwrite=overwrite)

    def save_run_metadata(self, run, metadata: dict, *, overwrite: bool = True) -> Path:
        """실행 단위 메타데이터(metadata.json) — run 폴더 바로 밑에 저장."""
        return self.save_json(run, "metadata.json", metadata, overwrite=overwrite)

    def export_files(self, run, files: dict, *, overwrite: bool = False) -> list:
        """exports/ 아래로 파일을 모아 내보낸다.
        files: {저장할 파일명: 원본 경로(str/Path) 또는 텍스트/바이트 내용}.
        원본 경로가 실제 파일이면 복사, 문자열/바이트 내용이면 그대로 새 파일로 쓴다.
        반환: 실제로 만들어진 Path 리스트(값이 비었거나 원본이 없으면 건너뜀)."""
        written = []
        for name, value in (files or {}).items():
            if value is None:
                continue
            rel = f"exports/{name}"
            if isinstance(value, (str, Path)) and Path(value).is_file():
                src = Path(value)
                dst = self._prepare_target(self.resolve(run, rel), overwrite)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                self._log_op("export_files(복사)", dst, f"원본={src}")
                written.append(dst)
            elif isinstance(value, (bytes, bytearray)):
                written.append(self.save_bytes(run, rel, bytes(value), overwrite=overwrite))
            else:
                written.append(self.save_text(run, rel, str(value), overwrite=overwrite))
        return written

    # ---- 실행(run) 생명주기 ---------------------------------------------------

    def create_run(self, profile) -> "RunHandle":
        """runs/ 아래 타임스탬프 Run 폴더 + 표준 서브폴더(raw/masked/parsed/analysis/reports/exports)를
        만들고 session.json/metadata.json 뼈대까지 채운 RunHandle을 반환한다.
        서로 다른 실행의 산출물이 섞이지 않도록 항상 새 폴더를 만들고, 기존 Run은 절대 건드리지 않는다."""
        import datetime
        from engine.profile_manager import RUN_SUBDIRS, RunHandle

        pdir = _require_root_path(profile)
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        run_path = pdir / "runs" / ts
        while run_path.exists():
            ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
            run_path = pdir / "runs" / ts
        for sub in RUN_SUBDIRS:
            (run_path / sub).mkdir(parents=True, exist_ok=True)

        run = RunHandle(profile=profile, run_id=ts, path=run_path)
        now = datetime.datetime.now().isoformat(timespec="seconds")
        self.save_session(run, {
            "run_id": ts,
            "customer": getattr(profile, "customer", None),
            "profile": getattr(profile, "name", None),
            "started_at": now,
        })
        self.save_run_metadata(run, {"run_id": ts, "status": "running"})
        self._log_op("create_run", run_path)
        return run

    def archive_run(self, profile, run_id: str) -> Path:
        """runs/<run_id>를 archive/<run_id>로 옮긴다(이동 — 원본은 runs/ 아래에서 사라짐)."""
        pdir = _require_root_path(profile)
        src = pdir / "runs" / run_id
        if not src.exists():
            raise StorageOperationError(f"보관할 실행 폴더가 없습니다: {src}")
        dst = pdir / "archive" / run_id
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst = self._versioned_path(dst)
        shutil.move(str(src), str(dst))
        self._log_op("archive_run", dst, f"원본={src}")
        return dst

    def delete_run(self, profile, run_id: str) -> None:
        """runs/<run_id>를 완전히 삭제한다 — 되돌릴 수 없으므로 archive_run()으로 먼저 보관하는 걸 권장."""
        pdir = _require_root_path(profile)
        target = pdir / "runs" / run_id
        if target.exists():
            shutil.rmtree(target)
            self._log_op("delete_run", target)

    def move_run(self, profile, run_id: str, target_profile, new_run_id: str = None) -> Path:
        """run 폴더 전체를 다른 프로파일의 runs/ 아래로 옮긴다(예: 잘못 기록된 프로파일에서 이관)."""
        src_pdir = _require_root_path(profile)
        dst_pdir = _require_root_path(target_profile)
        src = src_pdir / "runs" / run_id
        if not src.exists():
            raise StorageOperationError(f"이동할 실행 폴더가 없습니다: {src}")
        dst = dst_pdir / "runs" / (new_run_id or run_id)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst = self._versioned_path(dst)
        shutil.move(str(src), str(dst))
        self._log_op("move_run", dst, f"원본={src}")
        return dst

    def copy_run(self, profile, run_id: str, target_profile, new_run_id: str = None) -> Path:
        """run 폴더 전체를 다른 프로파일의 runs/ 아래로 복제한다(원본은 그대로 남음)."""
        src_pdir = _require_root_path(profile)
        dst_pdir = _require_root_path(target_profile)
        src = src_pdir / "runs" / run_id
        if not src.exists():
            raise StorageOperationError(f"복사할 실행 폴더가 없습니다: {src}")
        dst = dst_pdir / "runs" / (new_run_id or run_id)
        if dst.exists():
            dst = self._versioned_path(dst)
        shutil.copytree(src, dst)
        self._log_op("copy_run", dst, f"원본={src}")
        return dst


# 상태가 없는 서비스이므로 대부분의 호출부는 이 공유 인스턴스 하나로 충분하다.
storage_service = StorageService()
