"""
점검 원본 로그 저장소 — data/<고객사>/<정기점검 프로파일>/ 아래 각 용도별 폴더의 경로를 반환한다.

실제 폴더 트리·이름 검증·app-root 판단은 전부 engine.profile_manager.ProfileManager와
core.paths.AppPaths로 이전되었다(중복 로직 제거). 이 모듈은 그 위에 얹힌 얇은 호환 계층으로,
기존 호출부(engine/collector.py, api/*_api.py)가 써오던 함수 시그니처를 그대로 유지한다.

경로 매핑(신규 구조 -> 기존 함수명):
    00_orignal_log / 01_problem_log / 02_masking_log  -> runs/<run_id>/{raw,problem,masked}
      (점검 1회 = run 1개. 예전에는 프로파일당 1벌만 누적하던 cache/ 폴더였다.)
    03_CMD (커맨드 카탈로그 export/import)              -> commands/
    04_device_list (장비목록 export/import)             -> inventory/
    99_log (수집 원본 로그·세션)                          -> runs/ (실행마다 타임스탬프 하위 폴더)

**중요 — 폴더를 만드는 시점**: 조회 계열 함수는 폴더를 절대 만들지 않는다(`create=False`).
예전에는 목록 조회만 해도 repair_profile()이 프로파일 트리를 만들고 cache/ 하위까지 mkdir 해서,
실제 점검 데이터가 하나도 없는데도 UI가 "빈 폴더는 있으니 데이터가 있는 상태"처럼 보였다.
기록하는 쪽(점검 실행/분석 실행/마스킹 실행/폴더 열기)만 create=True로 명시해 만든다.
"""
import json
import os
import re
import sys
import shutil

from core.paths import AppPaths
from engine.profile_manager import profile_manager as _pm

ORIGINAL_LOG_DIR = "original_log"
PROBLEM_LOG_DIR = "problem_log"
MASKING_LOG_DIR = "masking_log"

# run 폴더(runs/<run_id>/) 안의 정식 하위 폴더 이름 — engine.profile_manager.RUN_SUBDIRS와 같은 이름을 쓴다.
RUN_DIR_NAMES = {"original": "raw", "problem": "problem", "masking": "masked", "reports": "reports"}
# 같은 용도로 과거 버전이 만들었던 폴더 이름들. 읽을 때만 후보로 본다(새로 만들지는 않는다).
RUN_DIR_LEGACY_NAMES = {"original": (), "problem": ("problem_log", "01_problem_log"),
                        "masking": ("masking_log", "02_masking_log"), "reports": ()}
# 프로파일 루트에 남아있는 과거 폴더(run 개념이 없던 시절) — 읽기 전용 하위 호환.
LEGACY_PROFILE_DIRS = {
    "original": (("cache", ORIGINAL_LOG_DIR), ("00_orignal_log",)),
    "problem": (("cache", PROBLEM_LOG_DIR), ("01_problem_log",)),
    "masking": (("cache", MASKING_LOG_DIR), ("02_masking_log",)),
    "reports": (("reports",),),
}
LOG_KINDS = tuple(RUN_DIR_NAMES)


def get_profile_dir(customer_name, profile_name):
    return str(_pm.profile_dir(customer_name, profile_name))


def get_cmd_catalog_dir(customer_name, profile_name):
    """명령어 카탈로그 export/import 기본 위치: data/<고객사>/<프로파일>/commands."""
    pdir = _pm.repair_profile(customer_name, profile_name)
    return str(pdir / "commands")


def get_device_list_dir(customer_name, profile_name):
    """장비목록 export/import 기본 위치: data/<고객사>/<프로파일>/inventory."""
    pdir = _pm.repair_profile(customer_name, profile_name)
    return str(pdir / "inventory")


def existing_profile_dir(customer_name, profile_name):
    """프로파일 폴더가 **이미 있으면** Path, 없으면 None — 폴더를 만들지 않는다.
    조회 경로가 repair_profile()을 부르면 데이터가 없는데 폴더만 생겨 UI가 오해한다."""
    if not customer_name or not profile_name:
        return None
    try:
        pdir = _pm.profile_dir(customer_name, profile_name)
    except ValueError:
        return None
    return pdir if pdir.is_dir() else None


def resolve_names_from_project(project_id):
    """labs/<project_id>/project_meta.yaml -> (고객사명, 프로파일명). 판단 규칙은
    api/customer_profile_api.py의 _fetch_customer_profiles()/_uncached_resolve_active_names()와 동일.
    Api 인스턴스 없이(모듈 함수에서) 프로파일 경로를 계산해야 할 때 쓴다."""
    if not project_id:
        return None, None
    import yaml
    meta_path = AppPaths.labs_root() / str(project_id) / "project_meta.yaml"
    meta = {}
    if meta_path.is_file():
        try:
            with open(meta_path, encoding="utf-8") as stream:
                meta = yaml.safe_load(stream) or {}
        except (OSError, yaml.YAMLError):
            meta = {}
    customer_name = meta.get("customer_name") or meta.get("display_name") or "미분류 고객사"
    profile_name = meta.get("profile_name") or meta.get("display_name") or str(project_id)
    return customer_name, profile_name


def _run_kind_dir(run_dir, kind, create=False):
    """run 폴더 안에서 kind('original'/'problem'/'masking'/'reports')에 해당하는 폴더 Path.
    정식 이름이 없고 과거 이름 폴더가 있으면 그쪽을 쓴다(하위 호환)."""
    canonical = run_dir / RUN_DIR_NAMES[kind]
    if not canonical.is_dir():
        for legacy in RUN_DIR_LEGACY_NAMES[kind]:
            if (run_dir / legacy).is_dir():
                return run_dir / legacy
    if create:
        canonical.mkdir(parents=True, exist_ok=True)
    return canonical


def _run_paths(pdir, run_dir, create=False):
    paths = {"root": str(pdir), "run_id": run_dir.name, "run_dir": str(run_dir)}
    for kind in LOG_KINDS:
        paths[kind] = str(_run_kind_dir(run_dir, kind, create=create))
    return paths


def list_run_dirs(customer_name, profile_name):
    """runs/<run_id> 경로 dict 목록 — 최신순(run_id 내림차순). 폴더가 없으면 빈 리스트."""
    pdir = existing_profile_dir(customer_name, profile_name)
    if pdir is None:
        return []
    runs_dir = pdir / "runs"
    if not runs_dir.is_dir():
        return []
    runs = sorted((d for d in runs_dir.iterdir() if d.is_dir()),
                  key=lambda d: d.name, reverse=True)
    return [_run_paths(pdir, d) for d in runs]


def generate_new_run_dir(customer_name, profile_name, *, device_count=0, command_count=0,
                          execution_mode=None):
    """점검 1회분을 담을 새 runs/<run_id>를 만들고 경로 dict를 반환한다.

    폴더 생성은 RunManager.create_run()에 위임한다 — 그래야 session.json/metadata.json까지
    함께 만들어져 Workspace 탭의 Run History에도 이 점검 회차가 잡힌다. 예전에는 여기서
    직접 mkdir만 해서, 점검을 해도 Workspace 탭에는 아무 회차도 보이지 않았다."""
    from engine.run_manager import run_manager

    run = run_manager.create_run(customer_name, profile_name, device_count=device_count,
                                  command_count=command_count, execution_mode=execution_mode)
    paths = _run_paths(run.profile.path, run.path, create=True)
    paths["run_handle"] = run
    return paths


_RUN_ID_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(\d{2})")


def run_label(run_id):
    """화면에 보일 점검 시각 — '2026-10-30_101638' -> '2026-10-30 10:16:38'(모르는 모양이면 그대로)."""
    match = _RUN_ID_TIME_RE.match(str(run_id or ""))
    return f"{match.group(1)} {match.group(2)}:{match.group(3)}:{match.group(4)}" if match else str(run_id or "")


def list_log_runs(customer_name, profile_name):
    """원본 로그가 있는 점검 회차 [{run_id, label, log_count}] — 최신순. 원본로그분석·마스킹·보고서가 '언제 점검한
    로그를 쓸지' 고르는 목록이다(원본 로그가 없는 회차는 고를 것이 없다)."""
    runs = []
    for run in list_run_dirs(customer_name, profile_name):
        try:
            count = sum(1 for name in os.listdir(run["original"]) if name.lower().endswith(".txt"))
        except OSError:
            continue
        if count:
            runs.append({"run_id": run["run_id"], "label": run_label(run["run_id"]), "log_count": count})
    return runs


def get_run_log_paths(customer_name, profile_name, run_id=None, create=False):
    """run_id 회차의 경로 dict(get_profile_log_paths 와 같은 모양) — run_id 가 없으면 가장 최근 회차, 없는 회차면 None.
    create=True 는 그 회차의 하위 폴더(problem/masked/reports — 결과를 쓸 곳)만 보장한다."""
    if not run_id:
        return get_profile_log_paths(customer_name, profile_name, create=create)
    run = next((r for r in list_run_dirs(customer_name, profile_name) if r["run_id"] == run_id), None)
    if run is not None and create:
        for kind in LOG_KINDS:
            os.makedirs(run[kind], exist_ok=True)
    return run


def get_latest_run_dir(customer_name, profile_name):
    """가장 최근 run 의 폴더 경로 dict — run 이 하나도 없으면 None."""
    runs = list_run_dirs(customer_name, profile_name)
    return runs[0] if runs else None


def legacy_profile_log_dirs(customer_name, profile_name, kind):
    """run 개념이 없던 시절 프로파일 루트/cache에 쌓인 kind 폴더 중 **실제로 존재하는** 것들."""
    pdir = existing_profile_dir(customer_name, profile_name)
    if pdir is None:
        return []
    dirs = []
    for parts in LEGACY_PROFILE_DIRS[kind]:
        candidate = pdir.joinpath(*parts)
        if candidate.is_dir():
            dirs.append(candidate)
    return dirs


def iter_log_dirs(customer_name, profile_name, kind):
    """kind 로그가 들어있을 수 있는 **존재하는** 폴더 전부 — 최신 run 우선, 그다음 과거 폴더.
    각 항목: {"path": str, "run_id": str|None}. 목록/삭제/집계가 모두 이 함수를 공유해야
    "한쪽에는 보이는데 다른 쪽에서는 안 지워지는" 유령 로그가 생기지 않는다."""
    result = []
    for run in list_run_dirs(customer_name, profile_name):
        if os.path.isdir(run[kind]):
            result.append({"path": run[kind], "run_id": run["run_id"]})
    for legacy in legacy_profile_log_dirs(customer_name, profile_name, kind):
        result.append({"path": str(legacy), "run_id": None})
    return result


def latest_original_logs(customer_name, profile_name):
    """장비별 최신 점검 원본 로그 1개씩 {device: (mtime, path)} — 모든 run 과 예전 폴더에서.
    대시보드의 데이터 소스다(예전 api/report_api 의 모듈 함수 — 채점 퇴역과 함께 옮겼다)."""
    from core.log_naming import device_from_log_name
    latest = {}
    for entry in iter_log_dirs(customer_name, profile_name, "original"):
        try:
            names = os.listdir(entry["path"])
        except OSError:
            continue
        for fname in names:
            if not fname.lower().endswith(".txt"):
                continue
            device = device_from_log_name(fname)
            if not device:
                continue
            path = os.path.join(entry["path"], fname)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if device not in latest or mtime > latest[device][0]:
                latest[device] = (mtime, path)
    return latest


def get_profile_log_paths(customer_name, profile_name, create=False):
    """기존 호출부 호환용 {root, original, problem, masking, reports} — 최신 run 폴더 기준.

    create=True는 **이미 있는** 최신 run의 하위 폴더(problem/masked/reports)만 보장한다
    (분석·마스킹 결과를 쓸 곳). run이 하나도 없으면 어느 쪽이든 None — 여기서 빈 run을
    만들어 주면 '점검한 적 없는데 회차가 1개 있는' 상태가 되어 UI가 데이터가 있는 것처럼 보인다.
    점검 회차를 새로 만드는 건 generate_new_run_dir()의 몫이다."""
    latest_run = get_latest_run_dir(customer_name, profile_name)
    if not latest_run:
        return None
    if create:
        for kind in LOG_KINDS:
            os.makedirs(latest_run[kind], exist_ok=True)
    return latest_run


def save_config_snapshot(profile_dir, config_paths):
    """프로파일 폴더에 설정 파일 스냅샷 복사.
    config_paths: {표시용 이름: 실제 파일 경로}. 자격증명(비밀번호/키)이 든 파일은
    호출부에서 애초에 넘기지 않아야 한다(이 폴더는 Open Folder로 그대로 열람/공유되는 곳)."""
    for src_path in config_paths.values():
        if src_path and os.path.isfile(src_path):
            shutil.copy2(src_path, os.path.join(profile_dir, os.path.basename(src_path)))


def open_in_file_explorer(path):
    """OS 네이티브 파일 탐색기로 폴더 열기."""
    os.makedirs(path, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        import subprocess
        subprocess.run(["open", path], check=False)
    else:
        import subprocess
        subprocess.run(["xdg-open", path], check=False)
