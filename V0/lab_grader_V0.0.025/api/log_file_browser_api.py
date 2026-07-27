"""LogFileBrowserApiMixin — 점검 로그(.txt) 목록/열람/삭제/폴더열기 + 수집 범위 요약.
raw_logs/(수집 파이프라인), labs/{project}/terminal_sessions/(세션 터미널 점검, 기존
Reports·Findings·AI분석이 읽는 경로), data/<고객사>/<프로파일>/00_orignal_log/(신규 원본 로그
저장소) 세 위치 모두를 대상으로 한다. AI 분석 실행은 log_analysis_run_api.py 참고.
"""
import os
import glob
import datetime

from api.report_api import _latest_terminal_logs_by_device


def _allowed_log_roots(project_id, extra_dirs=()):
    roots = []
    if project_id:
        roots.append(os.path.abspath(os.path.join("labs", project_id, "terminal_sessions")))
    for d in extra_dirs:
        if d:
            roots.append(os.path.abspath(d))
    roots.append(os.path.abspath("raw_logs"))
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
    """AutoCheck_{device}_{YYYYMMDD}_{HHMMSS}.txt -> device (report_api.py의 파싱 규칙과 동일)."""
    body = fname[len("AutoCheck_"):-len(".txt")] if fname.startswith("AutoCheck_") else fname[:-len(".txt")]
    parts = body.rsplit("_", 2)
    return parts[0] if len(parts) == 3 else body


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

    def list_log_files(self):
        """점검 로그 탭 — raw_logs/, terminal_sessions/, 00_orignal_log/ 세 로그 저장소의 .txt 파일 목록.
        {path, device, source, mtime, size} — 최신순 정렬."""
        try:
            project_id = self._project()
        except RuntimeError:
            project_id = None

        files = []

        session_dir = os.path.join("labs", project_id, "terminal_sessions") if project_id else None
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
            raw_dir = os.path.join("raw_logs", lab_name)
            if os.path.isdir(raw_dir):
                for path in glob.glob(os.path.join(raw_dir, "**", "*.txt"), recursive=True):
                    fname = os.path.basename(path)
                    device = fname[:-4] if fname.endswith(".txt") else fname
                    st = os.stat(path)
                    files.append({"path": path, "device": device, "source": "수집 파이프라인",
                                  "mtime": st.st_mtime, "size": st.st_size, "name": fname})

        # 세션 터미널 점검 결과는 data/<고객사>/<프로파일>/00_orignal_log/에도 동일 파일명으로
        # 사본이 저장되므로(run_terminal_inspection이 두 곳에 동시 저장), 같은 이름이 목록에
        # 2개씩 보이는 걸 막기 위해 00_orignal_log 쪽에 있는 이름은 그 사본(세션 터미널 점검)을
        # 제외하고 00_orignal_log 쪽 1개만 남긴다.
        original_names = {f["name"] for f in files if f["source"] == "00_orignal_log"}
        files = [f for f in files if not (f["source"] == "세션 터미널 점검" and f["name"] in original_names)]

        files.sort(key=lambda f: f["mtime"], reverse=True)
        for f in files:
            f["mtime_str"] = datetime.datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
        return files

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
        반환: {"deleted": [path,...], "errors": {path: message}}."""
        deleted, errors = [], {}
        for path in paths or []:
            abs_path = self._validate_log_path(path)
            if abs_path is None:
                errors[path] = "허용되지 않은 경로입니다."
                continue
            if not abs_path.lower().endswith(".txt"):
                errors[path] = "txt 파일만 삭제할 수 있습니다."
                continue
            try:
                os.remove(abs_path)
                deleted.append(path)
            except OSError as e:
                errors[path] = str(e)
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
