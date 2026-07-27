"""
점검 원본 로그 저장소 — data/<고객사>/<정기점검 프로파일>/ 아래 각 용도별 폴더의 경로를 반환한다.

실제 폴더 트리·이름 검증·app-root 판단은 전부 engine.profile_manager.ProfileManager와
core.paths.AppPaths로 이전되었다(중복 로직 제거). 이 모듈은 그 위에 얹힌 얇은 호환 계층으로,
기존 호출부(engine/collector.py, api/*_api.py)가 써오던 함수 시그니처를 그대로 유지한다.

경로 매핑(신규 구조 -> 기존 함수명):
    00_orignal_log / 01_problem_log / 02_masking_log  -> cache/original_log, cache/problem_log, cache/masking_log
      (실행마다 새로 생기는 runs/와 달리, 터미널 점검 UI가 계속 누적 기록하는 상시 폴더라 cache/에 둠)
    03_CMD (커맨드 카탈로그 export/import)              -> commands/
    04_device_list (장비목록 export/import)             -> inventory/
    99_log (수집 원본 로그·세션)                          -> runs/ (실행마다 타임스탬프 하위 폴더)
"""
import os
import sys
import shutil

from core.paths import AppPaths, sanitize_component
from engine.profile_manager import profile_manager as _pm

ORIGINAL_LOG_DIR = "original_log"
PROBLEM_LOG_DIR = "problem_log"
MASKING_LOG_DIR = "masking_log"


def get_app_root():
    """패키징된 exe면 exe가 있는 폴더, 아니면(개발 중 python으로 실행) 프로젝트 루트."""
    return str(AppPaths.app_root())


def safe_folder_name(name):
    """고객사/프로파일 이름을 폴더명으로 안전하게(경로 구분자·와일드카드 제거)."""
    return sanitize_component(name)


def get_customer_dir(customer_name):
    return str(_pm.customer_dir(customer_name))


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


def get_general_log_dir(customer_name, profile_name):
    """00~04번 전용 폴더에 속하지 않는 그 외 모든 로그(수집/채점 원본 등)의 기본 저장 위치:
    data/<고객사>/<프로파일>/runs. 실행마다 새 타임스탬프 폴더를 만드는 건 호출부(collector) 몫이다."""
    pdir = _pm.repair_profile(customer_name, profile_name)
    return str(pdir / "runs")


def get_profile_log_paths(customer_name, profile_name):
    """cache/ 아래 3종 로그 폴더까지 전부 생성(exist_ok=True로 경쟁조건 안전)하고 경로 dict 반환."""
    pdir = _pm.repair_profile(customer_name, profile_name)
    cache_dir = pdir / "cache"
    original_dir = cache_dir / ORIGINAL_LOG_DIR
    problem_dir = cache_dir / PROBLEM_LOG_DIR
    masking_dir = cache_dir / MASKING_LOG_DIR
    for d in (original_dir, problem_dir, masking_dir):
        d.mkdir(parents=True, exist_ok=True)
    return {"root": str(pdir), "original": str(original_dir), "problem": str(problem_dir), "masking": str(masking_dir)}


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
