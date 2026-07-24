"""LogViewerApiMixin — 점검 로그(.txt) 열람 + 수집 범위 요약(어떤 장비의 어떤 커맨드가
언제 수집됐는지)만 담당. raw_logs/(수집 파이프라인), labs/{project}/terminal_sessions/
(세션 터미널 점검, 기존 Reports·Findings·AI분석이 읽는 경로), data/<고객사>/<프로파일>/00_orignal_log/
(신규 원본 로그 저장소) 세 위치 모두를 대상으로 한다."""
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


class LogViewerApiMixin:
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

    def run_log_analysis(self):
        """'Log Analysis' 탭 — 00_orignal_log를 FSM으로 분석해 01_problem_log에 저장.
        반환: {"ok": True, "results": [{"source","problem_count","output"}, ...]}."""
        profile_paths = self._active_profile_log_paths()
        if not profile_paths:
            return {"error": "활성 프로파일이 없습니다."}
        from engine import log_analysis
        results = log_analysis.run_analysis(profile_paths["original"], profile_paths["problem"])
        return {"ok": True, "results": results}

    def run_ai_log_analysis(self, ai_mode):
        """'Log Analysis' 탭 — 'Run Local AI Analysis' / 'Run Cloud AI Analysis' 버튼.
        00_orignal_log의 각 .txt를 설정된 AI(local NPU 또는 cloud API)에게 그대로 분석시켜
        01_problem_log에 {원본파일명}_problems.txt로 저장(규칙기반 결과가 있으면 덮어씀).
        반환: {"ok": True, "results": [{"source","output"}, ...]} 또는 {"error": ...}."""
        if ai_mode not in ("local", "cloud"):
            return {"error": f"알 수 없는 AI 모드: {ai_mode}"}
        profile_paths = self._active_profile_log_paths()
        if not profile_paths:
            return {"error": "활성 프로파일이 없습니다."}

        print(f"[AI 분석] 시작 mode={ai_mode}")

        if ai_mode == "local":
            api_cfg = self.get_local_ai_config()
            endpoint = api_cfg.get("endpoint")
            model = api_cfg.get("model")
            print(f"[AI 분석] 로컬 모델 준비 중: endpoint={endpoint} model={model}")
            ready = self.ensure_lemonade_model_loaded(endpoint, model)
            if not ready.get("ok"):
                print(f"[AI 분석] 로컬 모델 준비 실패: {ready.get('detail', '')}")
                return {"error": f"로컬 AI 모델 준비 실패: {ready.get('detail', '')}"}
            print(f"[AI 분석] 로컬 모델 준비 완료: {ready.get('detail', '')}")
        else:
            local_cfg = self._load_ai_config()
            node = next((p for p in local_cfg.get("providers", []) if p.get("type") == "cloud_apis"), None)
            entry = next((e for e in (node or {}).get("entries", []) if e.get("enabled") and e.get("api_key")), None)
            if entry is None:
                print("[AI 분석] 사용 가능한(체크되고 키가 등록된) 클라우드 API가 없음")
                return {"error": "Cloud AI 설정이 없습니다. 설정 탭에서 API 키를 등록하고 체크하세요."}
            api_cfg = entry
            print(f"[AI 분석] 클라우드 API 사용: provider={entry.get('provider')} name={entry.get('name')}")

        from ai_analysis.router import analyze_raw_log_text

        original_dir = profile_paths["original"]
        problem_dir = profile_paths["problem"]
        if not original_dir or not os.path.isdir(original_dir):
            return {"error": "00_orignal_log 폴더가 없습니다."}

        results = []
        os.makedirs(problem_dir, exist_ok=True)
        for path in sorted(glob.glob(os.path.join(original_dir, "*.txt"))):
            raw_text = _read_text_auto(path)
            analysis_text = analyze_raw_log_text(raw_text, ai_mode, api_cfg)
            if analysis_text.startswith("[AI 분석 오류]"):
                print(f"[AI 분석] 실패: {os.path.basename(path)} -> {analysis_text}")
            out_name = os.path.splitext(os.path.basename(path))[0] + "_problems.txt"
            out_path = os.path.join(problem_dir, out_name)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(analysis_text)
            results.append({"source": os.path.basename(path), "output": out_name})
        return {"ok": True, "results": results}

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
