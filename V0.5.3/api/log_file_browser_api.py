"""LogFileBrowserApiMixin — 점검 로그(.txt) 목록/열람/삭제/폴더열기 + 수집 범위 요약.
raw_logs/(수집 파이프라인), labs/{project}/terminal_sessions/(세션 터미널 점검, 기존
Reports·Findings·AI분석이 읽는 경로), data/<고객사>/<프로파일>/00_orignal_log/(신규 원본 로그
저장소) 세 위치 모두를 대상으로 한다. AI 분석 실행은 log_analysis_run_api.py 참고.
"""
import os
import glob
import datetime
import shutil
import re

from api.report_api import _latest_terminal_logs_by_device
from core.paths import AppPaths


def _allowed_log_roots(project_id, extra_dirs=()):
    roots = []
    if project_id:
        roots.append(os.path.abspath(AppPaths.terminal_sessions_dir(project_id)))
    for d in extra_dirs:
        if d:
            roots.append(os.path.abspath(d))
    roots.append(os.path.abspath(AppPaths.raw_logs_root()))
    roots.append(os.path.abspath(AppPaths.crt_log_root()))
    return roots


def _read_text_auto(abs_path):
    """UTF-8(BOM 포함)으로 우선 시도하고, 과거에 시스템 기본 인코딩(cp949 등)으로 저장된
    레거시 로그 파일이면 cp949로 재시도한다. 둘 다 실패하면 깨진 문자를 치환해서라도 반환."""
    with open(abs_path, "rb") as f:
        raw = f.read()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_terminal_session_filename(fname):
    """새 형식: {YYYYMMDD}_{HHMMSS}_{Type}_{device}.txt -> device
    기존 형식: AutoCheck_{device}_{YYYYMMDD}_{HHMMSS}.txt -> device (하위 호환)"""
    body = fname[:-len(".txt")] if fname.endswith(".txt") else fname
    if body.startswith("AutoCheck_"):
        body_no_prefix = body[len("AutoCheck_"):]
        parts = body_no_prefix.rsplit("_", 2)
        return parts[0] if len(parts) == 3 else body_no_prefix
    else:
        parts = body.split("_", 3)
        return parts[3] if len(parts) == 4 else body


def _list_dir_txt_files(dir_path):
    """dir_path의 .txt 목록 — {path, name, mtime, mtime_str, size}, mtime 내림차순."""
    if not dir_path or not os.path.isdir(dir_path):
        return []
    files = []
    for path in glob.glob(os.path.join(dir_path, "*.txt")):
        st = os.stat(path)
        files.append({"path": path, "name": os.path.basename(path), "mtime": st.st_mtime, "size": st.st_size})
    files.sort(key=lambda f: f["mtime"], reverse=True)
    for f in files:
        f["mtime_str"] = datetime.datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
    return files


class LogFileBrowserApiMixin:
    def _active_profile_log_paths(self):
        """현재 활성 프로파일의 {original, problem, masking} 폴더 경로 dict(없으면 None)."""
        try:
            self._project()
        except RuntimeError:
            return None
        from engine import log_storage
        customer_name, profile_name = self.resolve_active_customer_profile_names()
        return log_storage.get_profile_log_paths(customer_name, profile_name)

    def _active_original_log_dir(self):
        """현재 활성 프로파일의 data/<고객사>/<프로파일>/00_orignal_log/ 경로(없으면 None)."""
        paths = self._active_profile_log_paths()
        return paths["original"] if paths else None

    def _log_copy_dirs(self):
        """같은 점검 로그의 사본이 존재할 수 있는 디렉터리 전부.
        점검 1회는 run_terminal_inspection()이 00_orignal_log/와 terminal_sessions/ 두 곳에
        같은 파일명으로 저장한다(보고서·대시보드가 terminal_sessions/를 읽기 때문에 둘 다 필요).
        목록에서 하나로 합치는 기준과 삭제할 때 지우는 대상이 이 함수 하나를 공유해야 한다."""
        try:
            project_id = self._project()
        except RuntimeError:
            project_id = None
        dirs = []
        original_dir = self._active_original_log_dir()
        if original_dir:
            dirs.append(original_dir)
        if project_id:
            dirs.append(str(AppPaths.terminal_sessions_dir(project_id)))
        return dirs

    def list_log_files(self):
        """점검 로그 탭 — raw_logs/, terminal_sessions/, 00_orignal_log/ 세 로그 저장소의 .txt 파일 목록.
        {path, paths, device, source, mtime, size} — 최신순 정렬.

        같은 파일명의 사본은 1행으로 합친다. 예전에는 '00_orignal_log에 같은 이름이 있으면
        terminal_sessions 쪽을 숨긴다'는 단방향 규칙이었는데, 그래서 00_orignal_log 사본만
        지워지고 terminal_sessions 사본이 남으면 숨겨 주던 근거가 사라져서 **이미 삭제한 로그가
        '세션 터미널 점검' 행으로 목록에 다시 나타났다**(디스크에는 장비당 1개인데 목록에는
        과거 로그까지 보이던 증상). 이제 이름으로 묶고 사본 경로를 전부 paths에 담아서,
        delete_log_files()가 한 행을 지울 때 사본까지 같이 지우도록 한다."""
        try:
            project_id = self._project()
        except RuntimeError:
            project_id = None

        files = []

        session_dir = str(AppPaths.terminal_sessions_dir(project_id)) if project_id else None
        if session_dir and os.path.isdir(session_dir):
            for path in glob.glob(os.path.join(session_dir, "*.txt")):
                fname = os.path.basename(path)
                device = _parse_terminal_session_filename(fname)
                st = os.stat(path)
                files.append({"path": path, "device": device, "source": "세션 터미널 점검",
                              "mtime": st.st_mtime, "size": st.st_size, "name": fname})

        original_dir = self._active_original_log_dir()
        if original_dir and os.path.isdir(original_dir):
            for path in glob.glob(os.path.join(original_dir, "*.txt")):
                fname = os.path.basename(path)
                device = _parse_terminal_session_filename(fname)
                st = os.stat(path)
                files.append({"path": path, "device": device, "source": "00_orignal_log",
                              "mtime": st.st_mtime, "size": st.st_size, "name": fname})

        lab_name = project_id
        if lab_name:
            raw_dir = str(AppPaths.raw_logs_root() / lab_name)
            if os.path.isdir(raw_dir):
                for path in glob.glob(os.path.join(raw_dir, "**", "*.txt"), recursive=True):
                    fname = os.path.basename(path)
                    device = fname[:-4] if fname.endswith(".txt") else fname
                    st = os.stat(path)
                    files.append({"path": path, "device": device, "source": "수집 파이프라인",
                                  "mtime": st.st_mtime, "size": st.st_size, "name": fname})

        # 파일명이 같으면 같은 점검 결과의 사본이다 — 1행으로 합치고 사본 경로를 paths에 모은다.
        # 어느 사본이 남아있든 항상 1행이므로, 사본 하나만 지워져도 목록이 늘어나지 않는다.
        source_priority = {"00_orignal_log": 0, "세션 터미널 점검": 1, "수집 파이프라인": 2}
        grouped = {}
        for f in files:
            row = grouped.get(f["name"])
            if row is None:
                grouped[f["name"]] = dict(f, paths=[f["path"]])
                continue
            row["paths"].append(f["path"])
            # 대표 행은 우선순위가 높은 저장소 것으로 — 표시되는 source/path가 왔다갔다 하지 않게.
            if source_priority.get(f["source"], 9) < source_priority.get(row["source"], 9):
                row.update({"path": f["path"], "source": f["source"], "device": f["device"]})
            row["mtime"] = max(row["mtime"], f["mtime"])

        files = sorted(grouped.values(), key=lambda f: f["mtime"], reverse=True)
        for f in files:
            f["mtime_str"] = datetime.datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
        return files

    def scan_crt_log_directory(self):
        """CRTlog 폴더를 스캔하여 활성 프로파일의 인벤토리 장비명과 일치하는 로그를 복사한다.
        {device}_{timestamp}.txt 형태에서 호스트명을 파싱하거나, 실패 시 파일 앞 10줄에서
        프롬프트(예: Core1#)를 찾아 매핑한다."""
        try:
            project_id = self._project()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}

        original_dir = self._active_original_log_dir()
        if not original_dir:
            return {"error": "로그 원본 폴더(00_orignal_log) 경로를 찾을 수 없습니다."}

        from engine import project_manager as pm
        from engine import device_inventory as di

        paths = pm.project_paths(project_id)
        if not paths:
            return {"error": "프로젝트 경로를 찾을 수 없습니다."}
        inv = self._load_inventory(paths)
        enabled_devices = di.get_enabled_devices(inv)
        valid_hostnames = {d["name"] for d in enabled_devices}

        crt_dir = AppPaths.crt_log_root()
        if not os.path.isdir(crt_dir):
            return {"ok": True, "copied": 0, "message": "CRTlog 폴더가 존재하지 않습니다."}

        copied_files = []
        prompt_regex = re.compile(r'^([A-Za-z0-9_-]+)[#>]$')

        for fname in os.listdir(crt_dir):
            if not fname.lower().endswith(".txt"):
                continue

            abs_path = os.path.join(crt_dir, fname)
            # 1. 파일명 기반 매핑 ({device}_{timestamp}.txt 또는 _로 구분된 첫 번째 단어)
            matched_device = None
            candidate = fname.split("_")[0] if "_" in fname else fname[:-4]
            if candidate in valid_hostnames:
                matched_device = candidate

            # 2. 내용 기반 매핑 (파일명 매핑 실패 시)
            if not matched_device:
                try:
                    content = _read_text_auto(abs_path)
                    lines = content.splitlines()[:10]
                    for line in lines:
                        match = prompt_regex.search(line.strip())
                        if match and match.group(1) in valid_hostnames:
                            matched_device = match.group(1)
                            break
                except (OSError, UnicodeDecodeError):
                    continue

            # 매핑 성공 시 복사
            if matched_device:
                # 00_orignal_log 안에 저장될 때 어떤 이름으로 복사할지 결정
                # 파일명 충돌을 피하기 위해 원본 파일명을 유지하거나 매핑된 장비명을 넣는다
                target_path = os.path.join(original_dir, fname)
                
                try:
                    # mtime과 크기가 같으면 이미 동기화된 파일로 취급
                    if os.path.exists(target_path):
                        src_stat = os.stat(abs_path)
                        tgt_stat = os.stat(target_path)
                        if src_stat.st_mtime == tgt_stat.st_mtime and src_stat.st_size == tgt_stat.st_size:
                            continue
                    
                    shutil.copy2(abs_path, target_path)
                    copied_files.append(fname)
                except OSError:
                    # 파일 쓰기 중 잠금(lock) 등의 이유로 복사가 실패하면 무시 (다음 debounced 때 재시도)
                    pass

        return {"ok": True, "copied": len(copied_files), "files": copied_files}

    def _validate_log_path(self, path):
        """path가 raw_logs/, labs/{project}/terminal_sessions/, 00_orignal_log/, 01_problem_log/,
        02_masking_log/ 중 하나의 실제 파일인지 검증. 허용되면 절대경로, 아니면 None."""
        try:
            project_id = self._project()
        except RuntimeError:
            project_id = None
        abs_path = os.path.abspath(path)
        profile_paths = self._active_profile_log_paths()
        extra_dirs = (profile_paths["original"], profile_paths["problem"], profile_paths["masking"]) if profile_paths else ()
        allowed = _allowed_log_roots(project_id, extra_dirs)
        if not any(abs_path.startswith(root + os.sep) or abs_path == root for root in allowed):
            return None
        return abs_path

    def read_log_file(self, path):
        """경로 검증 후 원문 반환 — raw_logs/ 또는 labs/{project}/terminal_sessions/ 하위만 허용."""
        abs_path = self._validate_log_path(path)
        if abs_path is None:
            return {"error": "허용되지 않은 경로입니다."}
        if not os.path.isfile(abs_path):
            return {"error": "파일이 존재하지 않습니다."}
        return {"text": _read_text_auto(abs_path)}

    def delete_log_files(self, paths):
        """로그 뷰어 다중 선택 삭제 — 허용된 경로의 .txt 파일만 삭제.
        반환: {"deleted": [path,...], "errors": {path: message}}.

        같은 파일명의 사본(00_orignal_log / terminal_sessions)을 **전부** 지운다.
        예전에는 원본->사본 한 방향으로 1개만 지웠고, 사본이 남으면 list_log_files()의
        중복 숨김 근거가 사라져서 이미 삭제한 로그가 목록에 다시 나타났다."""
        copy_dirs = [os.path.abspath(d) for d in self._log_copy_dirs()]

        deleted, errors = [], {}
        for path in paths or []:
            abs_path = self._validate_log_path(path)
            if abs_path is None:
                errors[path] = "허용되지 않은 경로입니다."
                continue
            if not abs_path.lower().endswith(".txt"):
                errors[path] = "txt 파일만 삭제할 수 있습니다."
                continue
            fname = os.path.basename(abs_path)
            # 지울 대상: 요청 경로 + 사본이 있을 수 있는 모든 디렉터리의 같은 이름.
            targets = [abs_path] + [os.path.join(d, fname) for d in copy_dirs]
            primary_ok, primary_err, seen = False, None, set()
            for target in targets:
                key = os.path.normcase(target)
                if key in seen:
                    continue
                seen.add(key)
                if not os.path.isfile(target):
                    # 요청한 파일이 이미 없으면 '삭제됨'으로 취급 — 목록이 디스크보다 앞서 있던
                    # 경우(탐색기에서 먼저 지운 경우)에 굳이 에러를 띄우지 않는다.
                    if target == abs_path:
                        primary_ok = True
                    continue
                try:
                    os.remove(target)
                    if target == abs_path:
                        primary_ok = True
                except OSError as e:
                    if target == abs_path:
                        primary_err = str(e)
            if primary_ok:
                deleted.append(path)
            else:
                errors[path] = primary_err or "삭제하지 못했습니다."
        return {"deleted": deleted, "errors": errors}

    def open_inspection_log_folder(self, folder="root"):
        """'점검 로그' 카드의 'Open Folder' 버튼 — 현재 선택된 중첩 탭(원본 로그/Log Analysis/
        Log Masking)에 맞는 폴더를 OS 네이티브 파일 탐색기로 연다.
        folder: 'root'(프로파일 루트) | 'original'(00_orignal_log) | 'problem'(01_problem_log) | 'masking'(02_masking_log)."""
        try:
            self._project()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}
        from engine import log_storage
        customer_name, profile_name = self.resolve_active_customer_profile_names()
        log_paths = log_storage.get_profile_log_paths(customer_name, profile_name)
        target = log_paths.get(folder) or log_paths["root"]
        os.makedirs(target, exist_ok=True)
        log_storage.open_in_file_explorer(target)
        return {"ok": True, "path": target}

    def list_original_log_files(self):
        """'Log Masking' 탭의 원본 소스 선택 — 00_orignal_log의 .txt 목록."""
        profile_paths = self._active_profile_log_paths()
        return _list_dir_txt_files(profile_paths["original"] if profile_paths else None)

    def list_problem_log_files(self):
        """'Log Analysis' 탭 / 'Log Masking' 탭의 필터링된 소스 선택 — 01_problem_log의 .txt 목록."""
        profile_paths = self._active_profile_log_paths()
        return _list_dir_txt_files(profile_paths["problem"] if profile_paths else None)

    def list_masking_log_files(self):
        """'Log Masking' 탭 — 02_masking_log의 .txt 목록(마스킹 실행 결과)."""
        profile_paths = self._active_profile_log_paths()
        return _list_dir_txt_files(profile_paths["masking"] if profile_paths else None)

    def get_collection_summary(self):
        """점검 로그 탭 상단 — 장비별 '무엇을 수집했는지(커맨드 목록) + 언제' 구조화 요약.
        report/textfsm_parser.split_raw_log()를 재사용해 새 파싱 로직 없이 커맨드 구간만 나눈다."""
        try:
            project_id = self._project()
        except RuntimeError:
            return []
        raw_logs = _latest_terminal_logs_by_device(project_id)
        if not raw_logs:
            return []
        from report.textfsm_parser import split_raw_log
        summary = []
        for device, text in raw_logs.items():
            sections = split_raw_log(text)
            summary.append({
                "device": device,
                "commands": list(sections.keys()),
                "command_count": len(sections),
            })
        return sorted(summary, key=lambda r: r["device"])
