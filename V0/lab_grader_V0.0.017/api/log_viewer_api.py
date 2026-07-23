"""LogViewerApiMixin — 점검 로그(.txt) 열람 + 수집 범위 요약(어떤 장비의 어떤 커맨드가
언제 수집됐는지)만 담당. raw_logs/(수집 파이프라인)와 labs/{project}/terminal_sessions/
(세션 터미널 점검) 두 위치 모두를 대상으로 한다."""
import os
import glob
import datetime

from api.report_api import _latest_terminal_logs_by_device


def _allowed_log_roots(project_id):
    roots = []
    if project_id:
        roots.append(os.path.abspath(os.path.join("labs", project_id, "terminal_sessions")))
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


class LogViewerApiMixin:
    def list_log_files(self):
        """점검 로그 탭 — 두 로그 저장소(raw_logs/, terminal_sessions/)의 .txt 파일 목록.
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

        files.sort(key=lambda f: f["mtime"], reverse=True)
        for f in files:
            f["mtime_str"] = datetime.datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
        return files

    def _validate_log_path(self, path):
        """path가 raw_logs/ 또는 labs/{project}/terminal_sessions/ 하위의 실제 파일인지 검증.
        허용되면 절대경로, 아니면 None."""
        try:
            project_id = self._project()
        except RuntimeError:
            project_id = None
        abs_path = os.path.abspath(path)
        allowed = _allowed_log_roots(project_id)
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
