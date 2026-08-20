"""
LogManager — raw/masked/parsed/analysis 로그 산출물에 대한 모든 파일시스템 연산의 유일한 출처.

기존 아키텍처(ProfileManager/StorageService/RunManager)는 그대로 두고 그 위에서 동작한다:
  - 폴더 자체(raw/masked/parsed/analysis)는 RunManager.create_run() -> StorageService.create_run()
    이 이미 만든다. LogManager는 그 폴더들 "안에서" 무엇을 저장/읽고/비교할지만 책임진다.
  - 실제 쓰기/읽기(원자적 쓰기, 충돌 시 버전 파일명, 로깅)는 전부 StorageService에 위임한다.
    LogManager가 open()/os.makedirs()를 직접 호출하는 곳은 없다.

이전에는 engine/log_masking.py(run_masking), report/textfsm_parser.py, ai_analysis/*가
각자 파일을 읽고 쓰는 자기만의 경로 규칙을 가지고 있었다. 이제 그 경로 규칙은 여기 하나로
모이고, 위 모듈들은 "텍스트/딕셔너리를 받아 텍스트/딕셔너리를 돌려주는" 순수 변환 함수로만
LogManager에서 호출된다.

무결성: raw 로그를 저장할 때마다 sha256 체크섬을 sidecar(<name>.sha256)로 함께 저장하고,
읽을 때마다 다시 계산해 비교한다 — 저장 후 파일이 손상/변조됐는지 자동으로 감지한다.
대용량 로그 대응: 체크섬은 파일을 통째로 메모리에 올리지 않고 청크 단위로 스트리밍 계산한다.
"""
import difflib
import gzip
import hashlib
import shutil
from pathlib import Path
from typing import Optional

from core.storage_service import storage_service
from report.textfsm_parser import split_raw_log
from engine.log_masking import apply_masking, MASK_KEY_ORDER

_CHUNK_SIZE = 1024 * 1024  # 1MB — 대용량 로그도 한 번에 메모리에 올리지 않고 스트리밍으로 체크섬 계산

_ANALYSIS_KINDS = ("device_analysis", "summary", "comparison", "health_score")


class LogManagerError(Exception):
    """LogManager 연산이 실패했을 때(잘못된 target, 손상된 로그 등) 던지는 공통 예외."""


class LogIntegrityError(LogManagerError):
    """저장된 체크섬과 실제 파일 내용이 다를 때(손상/변조 감지) 던진다."""


def _sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_of_file(path: Path) -> str:
    """파일 전체를 메모리에 올리지 않고 1MB 청크로 읽어 체크섬을 계산한다(대용량 로그 대비)."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class LogManager:
    """raw/masked/parsed/analysis 로그의 저장/조회/마스킹/파싱/비교/삭제/무결성 검증의 단일 진입점.
    어떤 모듈도 run.raw_dir 등 경로를 직접 조립해 open()하지 않고 전부 이 클래스를 통해야 한다."""

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
        return device_name if device_name.endswith(suffix) else f"{device_name}{suffix}"

    # ---- RAW: 원본 SSH 출력, 절대 수정하지 않는다 -------------------------------------

    def save_raw_log(self, run, device_name: str, content: str, *, overwrite: bool = False) -> Path:
        """raw/<device_name>.txt로 원본 그대로 저장하고, sha256 체크섬을 sidecar로 함께 남긴다.
        overwrite=False(기본)라 같은 run에서 같은 장비를 두 번 수집해도 원본이 덮어써지지 않고
        버전 파일명으로 분리 저장된다 — "raw 로그는 절대 수정하지 않는다"를 저장 계층에서도 보장."""
        stem = Path(self._device_filename(device_name)).stem
        path = self._storage.save_log(run, stem, content, overwrite=overwrite)
        self._storage.save_text(run, f"raw/{path.stem}.sha256", _sha256_of_text(content), overwrite=True)
        return path

    def load_raw_log(self, run, device_name: str, *, verify: bool = True) -> str:
        """raw/<device_name>.txt를 읽는다. verify=True(기본)면 sidecar 체크섬과 비교해서
        불일치하면 LogIntegrityError를 던진다(손상/변조 자동 감지)."""
        name = self._device_filename(device_name)
        text = self._storage.load_text(run, f"raw/{name}")
        if text is None:
            raise LogManagerError(f"raw 로그가 없습니다: {device_name}")
        if verify:
            self._verify_checksum(run, "raw", Path(name).stem, text)
        return text

    # ---- 무결성 검사 --------------------------------------------------------------

    def _verify_checksum(self, run, category: str, stem: str, text: str) -> None:
        expected = self._storage.load_text(run, f"{category}/{stem}.sha256", default=None)
        if expected is None:
            return  # 체크섬이 없던 구(舊) 로그 — 검증 대상에서 제외(하위 호환)
        actual = _sha256_of_text(text)
        if actual != expected.strip():
            raise LogIntegrityError(
                f"{category}/{stem} 손상 감지: 체크섬 불일치(기록={expected.strip()}, 실제={actual})"
            )

    def verify_integrity(self, run, category: str, device_name: str) -> bool:
        """파일 전체를 다시 읽지 않고 스트리밍으로 체크섬만 계산해 sidecar와 비교한다.
        문제 없으면 True, sidecar가 없으면 False(검증 불가)를 반환 — 예외는 던지지 않는다."""
        stem = Path(self._device_filename(device_name)).stem
        file_path = self._category_dir(run, category) / f"{stem}.txt"
        sidecar = self._category_dir(run, category) / f"{stem}.sha256"
        if not file_path.is_file() or not sidecar.is_file():
            return False
        expected = sidecar.read_text(encoding="utf-8").strip()
        return _sha256_of_file(file_path) == expected

    # ---- MASKED: 설정 가능한 규칙으로 마스킹 ------------------------------------------

    def mask_log(self, run, device_name: str, *, selected_keys=None, extra_rules: dict = None,
                 save: bool = True) -> str:
        """raw 로그를 읽어 마스킹 규칙(비밀번호/SNMP community/시크릿·API키·토큰/개인키/IP 등)을
        적용한다. selected_keys를 넘기지 않으면 내장 규칙 전체(MASK_KEY_ORDER)를 적용한다.
        extra_rules로 프로파일별 커스텀 정규식 규칙을 추가할 수 있다(engine/log_masking.py 참고)."""
        raw_text = self.load_raw_log(run, device_name, verify=False)
        keys = selected_keys if selected_keys is not None else list(MASK_KEY_ORDER)
        masked_text = apply_masking(raw_text, keys, extra_rules=extra_rules)
        if save:
            name = self._device_filename(device_name)
            path = self._storage.save_masked(run, name, masked_text, overwrite=True)
            self._storage.save_text(run, f"masked/{Path(name).stem}.sha256", _sha256_of_text(masked_text),
                                     overwrite=True)
            self._log_path = path
        return masked_text

    def load_masked_log(self, run, device_name: str, *, verify: bool = True) -> str:
        name = self._device_filename(device_name)
        text = self._storage.load_text(run, f"masked/{name}")
        if text is None:
            raise LogManagerError(f"masked 로그가 없습니다: {device_name}")
        if verify:
            self._verify_checksum(run, "masked", Path(name).stem, text)
        return text

    # ---- PARSED: 커맨드별로 그룹화된 구조적 JSON ---------------------------------------

    def parse_log(self, run, device_name: str, *, source: str = "raw", save: bool = True) -> dict:
        """raw(기본) 또는 masked 로그를 "--- <command> ---" 구분자로 잘라
        {"show version": "...", "show vlan": "...", ...} 형태로 구조화한다.
        report/textfsm_parser.split_raw_log()를 그대로 재사용 — 파서 로직 중복 없음."""
        if source not in ("raw", "masked"):
            raise LogManagerError(f"parse_log source는 raw/masked만 지원합니다: {source}")
        text = self.load_raw_log(run, device_name, verify=False) if source == "raw" \
            else self.load_masked_log(run, device_name, verify=False)
        sections = split_raw_log(text)
        if save:
            self.save_parsed(run, device_name, sections)
        return sections

    def save_parsed(self, run, device_name: str, data: dict, *, overwrite: bool = True) -> Path:
        """parsed/<device_name>.json으로 저장 — 이미 파싱된 결과를 외부(예: AI 분석기)가
        저장하고 싶을 때도 이 메서드 하나로 들어오게 한다."""
        stem = Path(self._device_filename(device_name)).stem
        return self._storage.save_parsed(run, stem, data, overwrite=overwrite)

    def load_parsed(self, run, device_name: str) -> dict:
        stem = Path(self._device_filename(device_name)).stem
        data = self._storage.load_json(run, f"parsed/{stem}.json", default=None)
        if data is None:
            raise LogManagerError(f"parsed 결과가 없습니다: {device_name}")
        return data

    # ---- ANALYSIS: AI/규칙기반 분석 결과 ------------------------------------------------

    def save_analysis(self, run, kind: str, data, *, overwrite: bool = True) -> Path:
        """kind는 device_analysis/summary/comparison/health_score 중 하나(그 외 값도 허용하되
        경고 없이 자유 이름으로 저장 — 향후 확장 대비). analysis/<kind>.json으로 저장한다."""
        return self._storage.save_analysis(run, kind, data, overwrite=overwrite)

    def save_device_analysis(self, run, device_name: str, analysis: dict) -> Path:
        """analysis/device_analysis.json은 장비별로 누적되는 단일 파일이라, 기존 내용을 읽어
        device_name 키만 갱신하고 다시 저장한다(다른 장비의 기존 분석 결과를 지우지 않음)."""
        current = self._storage.load_json(run, "analysis/device_analysis.json", default={})
        current[device_name] = analysis
        return self.save_analysis(run, "device_analysis", current)

    def load_analysis(self, run, kind: str, default=None):
        return self._storage.load_json(run, f"analysis/{kind}.json", default=default)

    # ---- 조회/삭제 ---------------------------------------------------------------

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

    def delete_logs(self, run, category: str, names: list = None, *, allow_raw: bool = False) -> list:
        """category 폴더에서 names(파일명 리스트, 생략 시 전체)를 삭제하고 sidecar 체크섬도 함께
        지운다. raw는 "절대 수정하지 않는다" 원칙상 allow_raw=True를 명시하지 않으면 거부한다."""
        if category == "raw" and not allow_raw:
            raise LogManagerError("raw 로그는 기본적으로 삭제할 수 없습니다(allow_raw=True로 명시해야 함).")
        d = self._category_dir(run, category)
        if not d.is_dir():
            return []
        targets = names or [p.name for p in d.iterdir() if p.is_file() and p.suffix != ".sha256"]
        deleted = []
        for name in targets:
            path = d / name
            if path.is_file():
                path.unlink()
                deleted.append(name)
            sidecar = d / f"{Path(name).stem}.sha256"
            if sidecar.is_file():
                sidecar.unlink()
        return deleted

    # ---- 압축(보관용) --------------------------------------------------------------

    def compress_log(self, run, category: str, name: str) -> Path:
        """archive_run()으로 보관되기 전/후의 개별 로그 파일을 gzip으로 압축해 용량을 줄인다.
        원본은 압축 후 삭제하고 <name>.gz만 남긴다. 이미 .gz면 그대로 반환(재압축 방지)."""
        if name.endswith(".gz"):
            return self._category_dir(run, category) / name
        src = self._category_dir(run, category) / name
        if not src.is_file():
            raise LogManagerError(f"압축할 파일이 없습니다: {src}")
        dst = src.with_name(src.name + ".gz")
        with src.open("rb") as f_in, gzip.open(dst, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out, length=_CHUNK_SIZE)
        src.unlink()
        return dst

    def decompress_log(self, run, category: str, name: str) -> Path:
        """compress_log()로 만든 <name>.gz를 원래 파일명으로 풀어놓는다(조회/재분석 필요 시)."""
        gz_name = name if name.endswith(".gz") else f"{name}.gz"
        src = self._category_dir(run, category) / gz_name
        if not src.is_file():
            raise LogManagerError(f"압축 파일이 없습니다: {src}")
        dst = src.with_name(gz_name[:-3])
        with gzip.open(src, "rb") as f_in, dst.open("wb") as f_out:
            shutil.copyfileobj(f_in, f_out, length=_CHUNK_SIZE)
        return dst

    # ---- 두 Run 비교 --------------------------------------------------------------

    def compare_runs(self, run_a, run_b, device_name: str) -> dict:
        """두 run의 parsed/<device_name>.json(커맨드별 출력)을 비교한다.
        parsed 결과가 없으면 그 자리에서 parse_log()로 만들어 채운 뒤 비교한다.
        반환: {"device", "commands_only_in_a", "commands_only_in_b", "changed_commands",
               "unchanged_commands"} — 커맨드별 실제 텍스트 diff는 generate_diff()로 별도 조회."""
        try:
            data_a = self.load_parsed(run_a, device_name)
        except LogManagerError:
            data_a = self.parse_log(run_a, device_name)
        try:
            data_b = self.load_parsed(run_b, device_name)
        except LogManagerError:
            data_b = self.parse_log(run_b, device_name)

        keys_a, keys_b = set(data_a), set(data_b)
        changed, unchanged = [], []
        for cmd in keys_a & keys_b:
            (changed if data_a[cmd] != data_b[cmd] else unchanged).append(cmd)

        return {
            "device": device_name,
            "run_a": getattr(run_a, "run_id", None), "run_b": getattr(run_b, "run_id", None),
            "commands_only_in_a": sorted(keys_a - keys_b),
            "commands_only_in_b": sorted(keys_b - keys_a),
            "changed_commands": sorted(changed),
            "unchanged_commands": sorted(unchanged),
        }

    def generate_diff(self, run_a, run_b, device_name: str, command: str) -> str:
        """두 run의 특정 커맨드 출력을 사람이 읽는 unified diff 텍스트로 만든다."""
        data_a = self.load_parsed(run_a, device_name)
        data_b = self.load_parsed(run_b, device_name)
        text_a = str(data_a.get(command, ""))
        text_b = str(data_b.get(command, ""))
        diff = difflib.unified_diff(
            text_a.splitlines(), text_b.splitlines(),
            fromfile=f"{getattr(run_a, 'run_id', 'run_a')}/{command}",
            tofile=f"{getattr(run_b, 'run_id', 'run_b')}/{command}",
            lineterm="",
        )
        return "\n".join(diff)


# 상태를 들고 있지 않는 서비스이므로(모든 상태는 run의 session.json/파일에 있음) 앱 전역에서
# 공유되는 단일 인스턴스로 충분하다.
log_manager = LogManager()
