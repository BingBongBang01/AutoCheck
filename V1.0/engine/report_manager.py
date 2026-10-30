"""
ReportManager — run·워크스페이스 내보내기(exports/·archive/)의 관리자.

정기점검 보고서 생성은 여기 없다 — engine/inspection_report_builder.py(build_context + 포맷 레지스트리,
프로파일 reports/)가 앱의 유일한 보고서 경로다(IMPROVEMENT_PLAN 3-3). 예전 이 클래스의 리포트 생성
(Markdown/Word/HTML/PDF/Excel/JSON)은 채점 결과(scored)를 그리는 것이라 채점 파이프라인 퇴역 후
빈 보고서만 만들었다.

여기 남은 것: 로그 묶음(exports/logs_<run>.zip 또는 목록 CSV), run 전체 zip, 워크스페이스 전체 zip
(계정 정보는 기본 제외), 그리고 화면의 '최근 내보내기' 목록. 모든 쓰기는
core.storage_service.StorageService(원자적 쓰기 + 버전 충돌 처리 + 로깅)를 거친다.
"""
import datetime
from pathlib import Path

from core.storage_service import storage_service
from engine.log_manager import log_manager
from engine.profile_manager import CREDENTIAL_FILE


class ReportManagerError(Exception):
    """ReportManager 연산이 실패했을 때(지원하지 않는 형식, 대상 없음 등) 던지는 공통 예외."""


def _is_temp_file(rel: Path) -> bool:
    """StorageService 가 쓰는 중인 임시 파일(.<이름>.….tmp) — 내보내기에 담지 않는다."""
    return rel.name.startswith(".") and rel.name.endswith(".tmp")


def _is_export_output(rel: Path) -> bool:
    """워크스페이스 내보내기에 다시 넣지 않을 것: 이전 내보내기 결과와 임시 파일.
    예전엔 archive/ 의 이전 워크스페이스 zip 까지 담아서 내보낼 때마다 크기가 두 배가 됐다.
    보관된 실행(archive/<run_id>/)은 이력이라 그대로 담고, 그 안의 exports/ 만 뺀다."""
    parts = rel.parts
    if len(parts) >= 3 and parts[0] in ("runs", "archive") and parts[2] == "exports":
        return True
    if len(parts) == 2 and parts[0] == "archive" and parts[1].startswith("workspace_export_"):
        return True
    return _is_temp_file(rel)


def _holds_credentials(rel: Path) -> bool:
    """계정 정보가 평문으로 담기는 자리 — profile/credential.json(기본 계정)과 inventory/(장비 목록
    사본: 엑셀·YAML 에 장비별 비밀번호·개인키 열이 있다). 워크스페이스 내보내기에서 기본으로 뺀다."""
    return rel.parts[:2] == ("profile", CREDENTIAL_FILE) or rel.parts[:1] == ("inventory",)


class ReportManager:
    """run·워크스페이스 내보내기(Export *)의 단일 진입점."""

    def __init__(self, storage=None, log_mgr=None):
        self._storage = storage or storage_service
        self._log_manager = log_mgr or log_manager

    # ---- 조회(GUI의 Recent Exports 패널용) ---------------------------------------

    def list_exports(self, run) -> list:
        """exports/의 파일명 목록(최근 생성 순 — mtime 내림차순)."""
        if not run.exports_dir.is_dir():
            return []
        return [p.name for p in sorted(run.exports_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
                if p.is_file()]

    # ---- Export Logs -----------------------------------------------------------------

    def export_logs(self, run, *, categories=("raw", "masked", "parsed"), devices: list = None,
                     fmt: str = "zip") -> Path:
        """개별 로그(카테고리별 raw/masked/parsed)를 exports/로 묶어 내보낸다.
        fmt="zip"(기본)이면 하나의 zip으로, fmt="csv"면 파일 목록(이름/카테고리/크기)만 CSV로."""
        category_dirs = {"raw": run.raw_dir, "masked": run.masked_dir, "parsed": run.parsed_dir,
                          "analysis": run.analysis_dir}
        entries = []  # (arcname, absolute_path)
        for category in categories:
            names = self._log_manager.list_logs(run, category)
            for name in names:
                if devices and Path(name).stem not in devices:
                    continue
                if _is_temp_file(Path(name)):
                    continue
                entries.append((f"{category}/{name}", category_dirs[category] / name))

        if fmt == "csv":
            rows = [{"category": arc.split("/")[0], "file": arc.split("/")[1], "size_bytes": p.stat().st_size}
                    for arc, p in entries]
            return self._storage.save_csv(run, "exports/logs_manifest.csv", rows)
        if fmt != "zip":
            raise ReportManagerError(f"export_logs는 zip/csv만 지원합니다: {fmt}")

        return self._storage.save_zip(run, f"exports/logs_{run.run_id}.zip",
                                      [(arcname, path) for arcname, path in entries if path.is_file()])

    # ---- Export Run / Workspace -----------------------------------------------------

    def export_run(self, run) -> Path:
        """run 전체(raw/masked/parsed/analysis/reports — exports/ 자기 자신은 재귀 방지로 제외)를
        exports/run_<run_id>.zip 하나로 묶는다(고객 전달/이관용 단일 파일)."""
        entries = []
        for path in sorted(run.path.rglob("*")):
            rel = path.relative_to(run.path)
            if path.is_file() and rel.parts[0] != "exports" and not _is_temp_file(rel):
                entries.append((rel.as_posix(), path))
        return self._storage.save_zip(run, f"exports/run_{run.run_id}.zip", entries)

    @staticmethod
    def _workspace_files(profile):
        """워크스페이스 내보내기 후보: (상대경로, 절대경로) — 이전 내보내기 결과·임시 파일은 뺀다."""
        for path in sorted(profile.path.rglob("*")):
            rel = path.relative_to(profile.path)
            if path.is_file() and not _is_export_output(rel):
                yield rel, path

    def credential_files(self, profile) -> list:
        """워크스페이스에서 계정 정보가 평문으로 담긴 파일('/' 구분 상대경로) — 내보내기에서 기본으로 빠진다."""
        return [rel.as_posix() for rel, _ in self._workspace_files(profile) if _holds_credentials(rel)]

    def export_workspace(self, profile, *, include_credentials: bool = False) -> Path:
        """프로파일 전체(profile/inventory/commands/baselines/runs/history/cache/archive)를
        archive/workspace_export_<타임스탬프>.zip으로 묶는다("Complete Workspace" 내보내기).
        exports/는 run 하나에만 있으므로(프로파일 레벨엔 없음) 이미 존재하는 archive/ 서브폴더에
        저장한다 — 새 폴더를 만들지 않는다.

        이전 내보내기 결과(archive/workspace_export_*.zip, run 의 exports/)는 담지 않는다.
        계정 정보(credential_files())는 include_credentials=True 일 때만 담는다 — 그때는 화면이 경고한다."""
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        entries = [(rel.as_posix(), path) for rel, path in self._workspace_files(profile)
                   if include_credentials or not _holds_credentials(rel)]
        return self._storage.save_zip(profile, f"archive/workspace_export_{ts}.zip", entries)


# 상태 없는 서비스(모든 상태는 파일에 있음)이므로 앱 전역 공유 인스턴스로 충분하다.
report_manager = ReportManager()
