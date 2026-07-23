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

    def read_log_file(self, path):
        """경로 검증 후 원문 반환 — raw_logs/ 또는 labs/{project}/terminal_sessions/ 하위만 허용."""
        try:
            project_id = self._project()
        except RuntimeError:
            project_id = None
        abs_path = os.path.abspath(path)
        allowed = _allowed_log_roots(project_id)
        if not any(abs_path.startswith(root + os.sep) or abs_path == root for root in allowed):
            return {"error": "허용되지 않은 경로입니다."}
        if not os.path.isfile(abs_path):
            return {"error": "파일이 존재하지 않습니다."}
        with open(abs_path, encoding="utf-8", errors="replace") as f:
            return {"text": f.read()}

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
