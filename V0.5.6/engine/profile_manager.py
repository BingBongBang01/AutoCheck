"""
ProfileManager — 고객사/정기점검 프로파일 데이터 저장소의 단일 관리자.

data/<고객사>/<프로파일>/ 아래에 완전히 독립적인 워크스페이스를 만든다:

    profile/        profile.json, customer.json, settings.json, variables.json, (credential.json)
    inventory/       장비 목록
    commands/        커맨드 카탈로그
    baselines/       비교 기준(target state) 스냅샷
    runs/            실행마다 타임스탬프 폴더 생성 (raw/masked/parsed/analysis/reports/exports + session.json/metadata.json)
    history/         실행 간 누적되는 기록(예: running-config 스냅샷 이력)
    cache/           재계산 가능한 중간 산출물(예: 마스킹 전/후 원본 로그)
    archive/         보관용(더 이상 활성이 아닌 산출물을 옮겨두는 곳)
    reports/         정기점검 보고서 엑셀 출력물(+ 다음 회차의 '전월 점검값'이 되는 _snapshot.json)
                     — 고객사/프로파일을 만들 때 다른 서브폴더와 함께 자동 생성된다.

이 클래스가 engine/log_storage.py·engine/customer_manager.py·api/customer_profile_api.py에
흩어져 있던 "고객사/프로파일 폴더를 어디에 어떤 이름으로 만들지" 로직의 유일한 출처다.
경로는 전부 pathlib.Path로 다루고, 이름 검증은 core.paths.validate_name()에 위임한다.
"""
import datetime
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from core.paths import AppPaths, validate_name

PROFILE_SUBDIRS = ("profile", "inventory", "commands", "baselines", "runs", "history", "cache",
                    "archive", "reports")
# problem/ = 이상 징후 블록만 뽑아낸 텍스트(01_problem_log의 새 위치). analysis/는 JSON 산출물용이라
# 텍스트 로그를 섞지 않고 별도 폴더로 둔다 — engine/log_storage.py의 RUN_DIR_NAMES와 짝을 맞춘다.
RUN_SUBDIRS = ("raw", "masked", "problem", "parsed", "analysis", "reports", "exports")

# 프로파일 메타 파일들이 놓이는 profile/ 하위 이름
PROFILE_META_FILE = "profile.json"
CUSTOMER_META_FILE = "customer.json"
SETTINGS_FILE = "settings.json"
VARIABLES_FILE = "variables.json"
CREDENTIAL_FILE = "credential.json"


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


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


class ProfileManager:
    """고객사/프로파일 워크스페이스에 대한 Create/Delete/Rename/Copy/Duplicate/Load/Save/Validate."""

    def __init__(self, data_root: Path = None):
        # data_root를 주입 가능하게 해서 테스트나 다른 데이터 루트 지정 시에도 재사용 가능하게 함.
        self._data_root = Path(data_root) if data_root is not None else None

    @property
    def data_root(self) -> Path:
        return self._data_root if self._data_root is not None else AppPaths.data_root()

    # ---- 경로 계산 -------------------------------------------------------

    def customer_dir(self, customer_name: str) -> Path:
        return self.data_root / validate_name(customer_name)

    def profile_dir(self, customer_name: str, profile_name: str) -> Path:
        return self.customer_dir(customer_name) / validate_name(profile_name)

    # ---- 내부 JSON 헬퍼 ----------------------------------------------------

    @staticmethod
    def _read_json(path: Path, default):
        if not path.exists():
            return default
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _write_json(path: Path, data) -> None:
        """core.storage_service.StorageService와 동일한 원자적 쓰기 방식(임시파일 + os.replace)을
        쓴다. 예전에는 path.open("w")로 바로 썼는데, 쓰는 도중 프로세스가 죽으면
        profile.json/settings.json 같은 메타 파일이 잘린 채로 남는 문제가 있었다."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)

    # ---- 폴더 구조 보장/복구 -----------------------------------------------

    def repair_profile(self, customer_name: str, profile_name: str) -> Path:
        """필수 서브폴더가 없으면 만들고, profile/*.json 메타가 없으면 기본값으로 채운다.
        기존(리팩터링 이전) data/<고객사>/<프로파일> 폴더도 이 메서드 한 번으로 새 구조에
        맞게 자동 편입된다 — 하위 호환의 핵심."""
        pdir = self.profile_dir(customer_name, profile_name)
        for sub in PROFILE_SUBDIRS:
            (pdir / sub).mkdir(parents=True, exist_ok=True)

        meta_path = pdir / "profile" / PROFILE_META_FILE
        if not meta_path.exists():
            now = _now()
            self._write_json(meta_path, {
                "id": uuid.uuid4().hex, "name": profile_name, "description": "",
                "inspection_date": "", "mode": "grading", "status": "준비",
                "created_at": now, "updated_at": now,
            })

        customer_path = pdir / "profile" / CUSTOMER_META_FILE
        if not customer_path.exists():
            self._write_json(customer_path, {"name": customer_name})

        for filename, default in ((SETTINGS_FILE, {}), (VARIABLES_FILE, {})):
            p = pdir / "profile" / filename
            if not p.exists():
                self._write_json(p, default)

        return pdir

    # ---- CRUD --------------------------------------------------------------

    def create_profile(self, customer_name: str, profile_name: str, *,
                        description: str = "", inspection_date: str = "", mode: str = "grading") -> dict:
        validate_name(customer_name)
        validate_name(profile_name)
        pdir = self.profile_dir(customer_name, profile_name)
        if (pdir / "profile" / PROFILE_META_FILE).exists():
            raise FileExistsError(f"이미 존재하는 프로파일입니다: {customer_name}/{profile_name}")

        self.repair_profile(customer_name, profile_name)
        now = _now()
        profile_meta = {
            "id": uuid.uuid4().hex, "name": profile_name, "description": description,
            "inspection_date": inspection_date, "mode": mode, "status": "준비",
            "created_at": now, "updated_at": now,
        }
        self._write_json(pdir / "profile" / PROFILE_META_FILE, profile_meta)
        self._write_json(pdir / "profile" / CUSTOMER_META_FILE, {"name": customer_name})
        return profile_meta

    def load_profile(self, customer_name: str, profile_name: str) -> dict:
        pdir = self.repair_profile(customer_name, profile_name)
        result = {
            "path": pdir,
            "profile": self._read_json(pdir / "profile" / PROFILE_META_FILE, default={}),
            "customer": self._read_json(pdir / "profile" / CUSTOMER_META_FILE, default={"name": customer_name}),
            "settings": self._read_json(pdir / "profile" / SETTINGS_FILE, default={}),
            "variables": self._read_json(pdir / "profile" / VARIABLES_FILE, default={}),
        }
        credential_path = pdir / "profile" / CREDENTIAL_FILE
        result["credential"] = self._read_json(credential_path, default=None) if credential_path.exists() else None
        return result

    def save_profile(self, customer_name: str, profile_name: str, *, profile: dict = None,
                      customer: dict = None, settings: dict = None, variables: dict = None,
                      credential: dict = None) -> Path:
        pdir = self.repair_profile(customer_name, profile_name)
        if profile is not None:
            profile = dict(profile)
            profile["updated_at"] = _now()
            self._write_json(pdir / "profile" / PROFILE_META_FILE, profile)
        if customer is not None:
            self._write_json(pdir / "profile" / CUSTOMER_META_FILE, customer)
        if settings is not None:
            self._write_json(pdir / "profile" / SETTINGS_FILE, settings)
        if variables is not None:
            self._write_json(pdir / "profile" / VARIABLES_FILE, variables)
        if credential is not None:
            self._write_json(pdir / "profile" / CREDENTIAL_FILE, credential)
        return pdir

    def validate_profile(self, customer_name: str, profile_name: str) -> list:
        """복구를 시도하지 않고 있는 그대로 검사만 한다. 반환된 리스트가 비어있으면 정상."""
        pdir = self.profile_dir(customer_name, profile_name)
        errors = []
        if not pdir.is_dir():
            errors.append(f"프로파일 폴더가 없습니다: {pdir}")
            return errors
        for sub in PROFILE_SUBDIRS:
            if not (pdir / sub).is_dir():
                errors.append(f"필수 폴더 누락: {sub}/")
        if not (pdir / "profile" / PROFILE_META_FILE).is_file():
            errors.append(f"필수 파일 누락: profile/{PROFILE_META_FILE}")
        return errors

    def delete_profile(self, customer_name: str, profile_name: str) -> None:
        pdir = self.profile_dir(customer_name, profile_name)
        if pdir.exists():
            shutil.rmtree(pdir)

    def archive_profile(self, customer_name: str, profile_name: str) -> Path:
        """프로파일 전체(runs/history/cache 포함)를 고객사 아래 _archived_profiles/<이름>으로
        옮긴다 — delete_profile()과 달리 되돌릴 수 있다(GUI의 'Archive Profile' 대응).
        이름 충돌 시 타임스탬프를 붙여 기존 보관본을 덮어쓰지 않는다."""
        src = self.profile_dir(customer_name, profile_name)
        if not src.exists():
            raise FileNotFoundError(f"프로파일이 없습니다: {customer_name}/{profile_name}")
        archive_root = self.customer_dir(customer_name) / "_archived_profiles"
        archive_root.mkdir(parents=True, exist_ok=True)
        dst = archive_root / profile_name
        if dst.exists():
            dst = archive_root / f"{profile_name}_{_now().replace(':', '').replace('-', '')}"
        shutil.move(str(src), str(dst))
        return dst

    def rename_profile(self, customer_name: str, profile_name: str, new_profile_name: str) -> Path:
        new_profile_name = validate_name(new_profile_name)
        old_dir = self.profile_dir(customer_name, profile_name)
        new_dir = self.profile_dir(customer_name, new_profile_name)
        if not old_dir.exists():
            raise ValueError(f"원본 프로파일이 없습니다: {customer_name}/{profile_name}")
        if old_dir != new_dir and new_dir.exists():
            raise FileExistsError(f"이미 존재하는 프로파일입니다: {customer_name}/{new_profile_name}")
        if old_dir != new_dir:
            old_dir.rename(new_dir)
        meta = self._read_json(new_dir / "profile" / PROFILE_META_FILE, default={})
        meta["name"] = new_profile_name
        meta["updated_at"] = _now()
        self._write_json(new_dir / "profile" / PROFILE_META_FILE, meta)
        return new_dir

    def duplicate_profile(self, customer_name: str, profile_name: str, new_profile_name: str) -> Path:
        """같은 고객사 안에서 프로파일 전체(runs/history/cache/archive 포함)를 새 이름으로 복제.
        과거 실행 기록까지 그대로 이어받아야 하는 경우(예: 담당자 인수인계) 사용."""
        new_profile_name = validate_name(new_profile_name)
        src = self.profile_dir(customer_name, profile_name)
        if not src.exists():
            raise ValueError(f"원본 프로파일이 없습니다: {customer_name}/{profile_name}")
        dst = self.profile_dir(customer_name, new_profile_name)
        if dst.exists():
            raise FileExistsError(f"이미 존재하는 프로파일입니다: {customer_name}/{new_profile_name}")

        shutil.copytree(src, dst)
        meta = self._read_json(dst / "profile" / PROFILE_META_FILE, default={})
        now = _now()
        meta.update({"id": uuid.uuid4().hex, "name": new_profile_name, "created_at": now, "updated_at": now})
        self._write_json(dst / "profile" / PROFILE_META_FILE, meta)
        return dst

    def copy_profile(self, customer_name: str, profile_name: str,
                      target_customer_name: str, new_profile_name: str = None) -> Path:
        """프로파일의 '틀'(인벤토리/커맨드/베이스라인/설정/변수)만 다른 고객사·이름으로 복사한다.
        runs/history/cache/archive의 과거 실행 기록은 옮기지 않는다 — 새 고객사 워크스페이스에
        이전 실행 로그가 섞여 들어가지 않도록 하기 위함."""
        new_profile_name = validate_name(new_profile_name or profile_name)
        validate_name(target_customer_name)
        src = self.profile_dir(customer_name, profile_name)
        if not src.exists():
            raise ValueError(f"원본 프로파일이 없습니다: {customer_name}/{profile_name}")
        dst = self.profile_dir(target_customer_name, new_profile_name)
        if dst.exists():
            raise FileExistsError(f"이미 존재하는 프로파일입니다: {target_customer_name}/{new_profile_name}")

        self.repair_profile(target_customer_name, new_profile_name)
        for sub in ("inventory", "commands", "baselines"):
            src_sub = src / sub
            if src_sub.exists():
                shutil.copytree(src_sub, dst / sub, dirs_exist_ok=True)

        settings = self._read_json(src / "profile" / SETTINGS_FILE, default={})
        variables = self._read_json(src / "profile" / VARIABLES_FILE, default={})
        self.save_profile(target_customer_name, new_profile_name, settings=settings, variables=variables)

        meta = self._read_json(dst / "profile" / PROFILE_META_FILE, default={})
        now = _now()
        meta.update({"name": new_profile_name, "updated_at": now})
        meta.setdefault("id", uuid.uuid4().hex)
        meta.setdefault("created_at", now)
        self._write_json(dst / "profile" / PROFILE_META_FILE, meta)
        return dst

    # ---- 고객사 단위 조작 -------------------------------------------------------

    def delete_customer(self, customer_name: str) -> None:
        cdir = self.customer_dir(customer_name)
        if cdir.exists():
            shutil.rmtree(cdir)

    def rename_customer(self, customer_name: str, new_customer_name: str) -> Path:
        new_customer_name = validate_name(new_customer_name)
        old_dir = self.customer_dir(customer_name)
        new_dir = self.customer_dir(new_customer_name)
        if not old_dir.exists():
            raise ValueError(f"고객사 폴더가 없습니다: {customer_name}")
        if old_dir != new_dir:
            if new_dir.exists():
                raise FileExistsError(f"이미 존재하는 고객사입니다: {new_customer_name}")
            old_dir.rename(new_dir)
        for entry in new_dir.iterdir():
            if not entry.is_dir():
                continue
            cust_path = entry / "profile" / CUSTOMER_META_FILE
            if cust_path.exists():
                data = self._read_json(cust_path, default={})
                data["name"] = new_customer_name
                self._write_json(cust_path, data)
        return new_dir

    # ---- Profile 핸들 -------------------------------------------------------

    def get_profile(self, customer_name: str, profile_name: str) -> Profile:
        """StorageService에 넘길 Profile 핸들을 반환한다(폴더 구조는 필요하면 복구까지 함께 수행)."""
        pdir = self.repair_profile(customer_name, profile_name)
        return Profile(customer=customer_name, name=profile_name, path=pdir)

    # ---- 조회 ---------------------------------------------------------------

    def list_customers(self) -> list:
        if not self.data_root.exists():
            return []
        return sorted(p.name for p in self.data_root.iterdir() if p.is_dir())

    def list_profiles(self, customer_name: str) -> list:
        cdir = self.customer_dir(customer_name)
        if not cdir.exists():
            return []
        result = []
        for entry in sorted(p.name for p in cdir.iterdir() if p.is_dir()):
            pdir = self.repair_profile(customer_name, entry)
            meta = self._read_json(pdir / "profile" / PROFILE_META_FILE, default={})
            result.append({"name": entry, **meta})
        return result

    # ---- 실행(run) ------------------------------------------------------------

    def create_run(self, customer_name: str, profile_name: str) -> Path:
        """runs/ 아래 Run 디렉터리를 새로 만든다. 실제 폴더 생성/상태 관리는
        engine.run_manager.RunManager에 위임한다(중복 로직 제거 — Run의 생명주기를 아는 코드는
        이제 RunManager 한 곳에만 있음). 호출부(레거시 호환) 편의를 위해 Path만 반환한다 —
        RunHandle이 필요하면 get_run_handle()을, 진행률/상태 갱신이 필요하면
        engine.run_manager.run_manager를 직접 쓸 것."""
        return self.get_run_handle(customer_name, profile_name).path

    def get_run_handle(self, customer_name: str, profile_name: str) -> "RunHandle":
        from engine.run_manager import run_manager
        return run_manager.create_run(customer_name, profile_name)

    def list_runs(self, customer_name: str, profile_name: str) -> list:
        runs_dir = self.profile_dir(customer_name, profile_name) / "runs"
        if not runs_dir.exists():
            return []
        return sorted(p.name for p in runs_dir.iterdir() if p.is_dir())

    # ---- 자격증명 마이그레이션 --------------------------------------------------

    def migrate_credentials_from_yaml(self, customer_name: str, profile_name: str, source_yaml_path) -> bool:
        """레거시 ip_allocation.yaml 등에 남아있는 default_credentials를 profile/credential.json
        으로 1회 이전한다. credential.json이 이미 있으면 아무것도 하지 않는다(재이전 방지)."""
        pdir = self.repair_profile(customer_name, profile_name)
        cred_path = pdir / "profile" / CREDENTIAL_FILE
        if cred_path.exists():
            return False
        source_yaml_path = Path(source_yaml_path)
        if not source_yaml_path.exists():
            return False
        import yaml
        with source_yaml_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        creds = data.get("default_credentials")
        if not creds:
            return False
        self._write_json(cred_path, creds)
        return True


# 대부분의 호출부는 앱 전역에서 공유되는 단일 인스턴스면 충분하므로 기본 인스턴스를 노출한다.
profile_manager = ProfileManager()
