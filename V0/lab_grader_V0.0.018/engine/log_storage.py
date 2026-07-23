"""
점검 원본 로그 3단 저장소 — data/<고객사>/<정기점검 프로파일>/ 아래에
00_orignal_log(원본) / 01_problem_log(이상탐지 결과) / 02_masking_log(마스킹 결과)
3개 폴더를 실행 파일 기준 절대경로로 생성한다.

exe로 패키징된 경우 CWD가 실행 위치와 다를 수 있으므로, 상대경로(labs/ 등 기존 관례)
대신 sys.executable(또는 이 파일 기준 프로젝트 루트) 기준 절대경로를 쓴다 —
어디서 실행하든 항상 같은 data/ 트리에 쌓이게 하기 위함.
"""
import os
import re
import sys
import shutil

ORIGINAL_LOG_DIR = "00_orignal_log"
PROBLEM_LOG_DIR = "01_problem_log"
MASKING_LOG_DIR = "02_masking_log"

_INVALID_FS_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


def get_app_root():
    """패키징된 exe면 exe가 있는 폴더, 아니면(개발 중 python으로 실행) 프로젝트 루트."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def safe_folder_name(name):
    """고객사/프로파일 이름을 폴더명으로 안전하게(경로 구분자·와일드카드 제거)."""
    name = (name or "").strip()
    name = _INVALID_FS_CHARS_RE.sub("_", name)
    return name or "미지정"


def get_profile_log_paths(customer_name, profile_name):
    """L0(data)~L3(3종 로그 폴더)까지 전부 생성(exist_ok=True로 경쟁조건 안전)하고 경로 dict 반환."""
    data_root = os.path.join(get_app_root(), "data")
    customer_dir = os.path.join(data_root, safe_folder_name(customer_name))
    profile_dir = os.path.join(customer_dir, safe_folder_name(profile_name))
    original_dir = os.path.join(profile_dir, ORIGINAL_LOG_DIR)
    problem_dir = os.path.join(profile_dir, PROBLEM_LOG_DIR)
    masking_dir = os.path.join(profile_dir, MASKING_LOG_DIR)
    for d in (data_root, customer_dir, profile_dir, original_dir, problem_dir, masking_dir):
        os.makedirs(d, exist_ok=True)
    return {"root": profile_dir, "original": original_dir, "problem": problem_dir, "masking": masking_dir}


def save_config_snapshot(profile_dir, config_paths):
    """L2 프로파일 폴더에 설정 파일 스냅샷 복사.
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
