"""
LogManager — run 폴더(runs/<run_id>/) 안의 로그 파일 저장·목록.

폴더 자체(raw/masked/parsed/analysis)는 RunManager.create_run() -> StorageService.create_run() 이
만든다. 실제 쓰기(원자적 쓰기, 충돌 시 버전 파일명, 로깅)는 StorageService 에 위임한다.

쓰는 곳은 둘이다: 워크스페이스 탭의 최근 로그 목록(list_logs)과 SSH 수집(engine/collector.py 의
save_raw_log — 원본을 sha256 sidecar 와 함께 남긴다). 예전에 함께 있던 마스킹·파싱·분석 저장,
압축, 두 run 비교, 무결성 검증은 호출하는 곳이 없어 지웠다(IMPROVEMENT_PLAN 3-2). 점검 로그의
분석·마스킹 결과는 engine/log_storage 의 problem/masked 폴더 규칙을 따른다.
"""
import hashlib
from pathlib import Path

from core.paths import safe_filename
from core.storage_service import storage_service


class LogManagerError(Exception):
    """LogManager 연산이 실패했을 때(잘못된 target 등) 던지는 공통 예외."""


def _sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class LogManager:
    """run 의 raw/masked/parsed/analysis 폴더 안 로그 파일 저장(원본)·목록 조회."""

    def __init__(self, storage=None):
        self._storage = storage or storage_service

    # ---- 경로/카테고리 헬퍼 -------------------------------------------------------

    @staticmethod
    def _category_dir(run, category: str) -> Path:
        mapping = {
            "raw": run.raw_dir, "masked": run.masked_dir,
            "parsed": run.parsed_dir, "analysis": run.analysis_dir,
        }
        if category not in mapping:
            raise LogManagerError(f"알 수 없는 로그 카테고리: {category} (raw/masked/parsed/analysis 중 하나)")
        return mapping[category]

    @staticmethod
    def _device_filename(device_name: str, suffix: str = ".txt") -> str:
        # 장비명이 곧 파일명이다 — 'a/b' 가 하위 폴더를, 'SW:1' 이 NTFS 대체 스트림을 만들지 않게 다듬는다.
        name = safe_filename(device_name)
        return name if name.endswith(suffix) else f"{name}{suffix}"

    # ---- RAW: 원본 SSH 출력, 절대 수정하지 않는다 -------------------------------------

    def save_raw_log(self, run, device_name: str, content: str, *, overwrite: bool = False) -> Path:
        """raw/<device_name>.txt로 원본 그대로 저장하고, sha256 체크섬을 sidecar로 함께 남긴다.
        overwrite=False(기본)라 같은 run에서 같은 장비를 두 번 수집해도 원본이 덮어써지지 않고
        버전 파일명으로 분리 저장된다 — "raw 로그는 절대 수정하지 않는다"를 저장 계층에서도 보장."""
        stem = Path(self._device_filename(device_name)).stem
        path = self._storage.save_log(run, stem, content, overwrite=overwrite)
        self._storage.save_text(run, f"raw/{path.stem}.sha256", _sha256_of_text(content), overwrite=True)
        return path

    # ---- 조회 ---------------------------------------------------------------

    def list_logs(self, run, category: str = None) -> dict:
        """category를 지정하면 해당 폴더의 파일명 리스트, 지정하지 않으면
        {"raw": [...], "masked": [...], "parsed": [...], "analysis": [...]} 전체를 반환.
        체크섬 sidecar(.sha256)는 로그 목록에서 제외한다."""
        categories = [category] if category else ["raw", "masked", "parsed", "analysis"]
        result = {}
        for cat in categories:
            d = self._category_dir(run, cat)
            result[cat] = sorted(p.name for p in d.iterdir() if p.is_file() and p.suffix != ".sha256") \
                if d.is_dir() else []
        return result[category] if category else result

# 상태를 들고 있지 않는 서비스이므로(모든 상태는 run의 session.json/파일에 있음) 앱 전역에서
# 공유되는 단일 인스턴스로 충분하다.
log_manager = LogManager()
