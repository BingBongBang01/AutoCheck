"""
ProfileManager — 고객사/정기점검 프로파일 데이터 저장소의 단일 관리자.

data/<고객사>/<프로파일>/ 아래에 완전히 독립적인 워크스페이스를 만든다:

    profile/        profile.json, customer.json, settings.json, variables.json
                    (credential.json — 예전 버전이 만든 기본 계정 사본. 지금은 만들지도 읽지도 않고, 남은 것은 봉인된다)
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
import json
import os
import shutil
import uuid
from pathlib import Path

from core.paths import AppPaths, validate_name
from core.utils import format_iso
# 핸들·표준 하위 폴더는 core 에 있다(core.storage_service 도 쓰므로) — 예전 import 경로를 위해 다시 내보낸다.
from core.workspace_model import PROFILE_SUBDIRS, RUN_SUBDIRS, Profile, RunHandle  # noqa: F401

# 프로파일 메타 파일들이 놓이는 profile/ 하위 이름
PROFILE_META_FILE = "profile.json"
CUSTOMER_META_FILE = "customer.json"
SETTINGS_FILE = "settings.json"
VARIABLES_FILE = "variables.json"
CREDENTIAL_FILE = "credential.json"


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
            now = format_iso()
            self._write_json(meta_path, {
                "id": uuid.uuid4().hex, "name": profile_name, "description": "",
                "inspection_date": "", "mode": "grading", "status": "준비",
                "is_virtual": False,
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
                        description: str = "", inspection_date: str = "", mode: str = "grading",
                        is_virtual: bool = False) -> dict:
        validate_name(customer_name)
        validate_name(profile_name)
        pdir = self.profile_dir(customer_name, profile_name)
        if (pdir / "profile" / PROFILE_META_FILE).exists():
            raise FileExistsError(f"이미 존재하는 프로파일입니다: {customer_name}/{profile_name}")

        self.repair_profile(customer_name, profile_name)
        now = format_iso()
        profile_meta = {
            "id": uuid.uuid4().hex, "name": profile_name, "description": description,
            "inspection_date": inspection_date, "mode": mode, "status": "준비",
            "is_virtual": bool(is_virtual),
            "created_at": now, "updated_at": now,
        }
        self._write_json(pdir / "profile" / PROFILE_META_FILE, profile_meta)
        self._write_json(pdir / "profile" / CUSTOMER_META_FILE, {"name": customer_name})
        return profile_meta

    def read_meta(self, customer_name: str, profile_name: str) -> dict:
        """profile/profile.json 을 있는 그대로 읽는다(없으면 빈 dict).
        폴더를 복구하지도, 파일을 만들지도 않는다 — 보고서/AI 분석처럼
        '읽기만 하는' 경로가 조회만으로 프로파일 폴더를 만들어 버리면 안 되기 때문."""
        return self._read_json(self.profile_dir(customer_name, profile_name) / "profile" / PROFILE_META_FILE,
                                default={})

    def is_virtual(self, customer_name: str, profile_name: str) -> bool:
        """이 프로파일이 '가상환경'(vEOS-lab / EVE-NG 등 실습·검증망)인가.

        가상환경에서는 PSU/FAN/온도센서/모듈이 물리적으로 존재하지 않으므로, 그 점검 항목은
        '확인필요'가 아니라 '해당없음'이다. 예전에는 이 판단을 로그 출력 문구
        ("There seem to be no power supplies" 등)로 사후 추론하기만 했는데, 문구가 벤더/버전마다
        달라 놓치는 경우가 있었다 — 프로파일에 명시된 값이 있으면 그것을 먼저 믿는다.

        이름 규칙에 맞지 않는 레거시 프로파일(validate_name 실패)이나 깨진 메타 파일은
        '가상환경 아님'으로 본다 — 조회 한 번이 목록 전체를 죽이지 않게."""
        try:
            return bool(self.read_meta(customer_name, profile_name).get("is_virtual"))
        except (ValueError, OSError, json.JSONDecodeError):
            return False

    def set_virtual(self, customer_name: str, profile_name: str, is_virtual: bool) -> dict:
        """가상환경 플래그만 갱신하고 갱신된 메타를 반환한다."""
        pdir = self.repair_profile(customer_name, profile_name)
        meta = self._read_json(pdir / "profile" / PROFILE_META_FILE, default={})
        meta["is_virtual"] = bool(is_virtual)
        meta["updated_at"] = format_iso()
        self._write_json(pdir / "profile" / PROFILE_META_FILE, meta)
        return meta

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
            dst = archive_root / f"{profile_name}_{format_iso().replace(':', '').replace('-', '')}"
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
        meta["updated_at"] = format_iso()
        self._write_json(new_dir / "profile" / PROFILE_META_FILE, meta)
        return new_dir

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
        """고객사 폴더의 프로파일 목록(폴더 이름 + profile.json). 조회만 한다.
        예전엔 항목마다 repair_profile 을 불러 조회할 때마다 폴더·메타를 만들었고, 보관 폴더
        (_archived_profiles)까지 가짜 프로파일로 고쳐 넣었다. '_' 로 시작하는 폴더는 프로파일이 아니다."""
        cdir = self.customer_dir(customer_name)
        if not cdir.exists():
            return []
        result = []
        for entry in sorted(p.name for p in cdir.iterdir() if p.is_dir() and not p.name.startswith("_")):
            try:
                meta = self._read_json(cdir / entry / "profile" / PROFILE_META_FILE, default={})
            except (OSError, ValueError):
                meta = {}
            result.append({"name": entry, **(meta if isinstance(meta, dict) else {})})
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


# 대부분의 호출부는 앱 전역에서 공유되는 단일 인스턴스면 충분하므로 기본 인스턴스를 노출한다.
profile_manager = ProfileManager()
