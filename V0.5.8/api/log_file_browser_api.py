"""LogFileBrowserApiMixin — 점검 로그(.txt) 목록/열람/삭제/폴더열기 + 수집 범위 요약.

목록의 출처는 **활성 프로파일의 로그 저장소 하나뿐**이다:
data/<고객사>/<프로파일>/runs/<run_id>/{raw,problem,masked} (+ 과거 버전이 만든 cache/·0x_ 폴더).
예전에는 여기에 labs/{project}/terminal_sessions/와 raw_logs/{project}/까지 섞어서 훑었는데,
그 두 곳은 프로파일과 무관한 앱 전역 레거시 폴더라 **프로파일에 점검 데이터가 하나도 없어도
예전 랩의 로그가 목록에 떠 있었다**. 이제 그 두 폴더는 목록/집계에서 제외한다.
어떤 폴더를 훑을지는 engine/log_storage.py의 iter_log_dirs()가 단독으로 정한다.
AI 분석 실행은 log_analysis_run_api.py 참고.
"""
import os
import glob
import datetime
import shutil
import re

from api.report_api import _latest_terminal_logs_by_device
from core.paths import AppPaths
from core.log_naming import device_from_log_name, parse_inspection_log_name
from core.text_io import read_log_text


def _allowed_log_roots(extra_dirs=()):
    """열람/삭제를 허용할 루트 — 활성 프로파일 폴더와 CRT 로그 폴더뿐.
    labs/·raw_logs/는 더 이상 목록에 나오지 않으므로 허용 루트에서도 뺀다."""
    roots = [os.path.abspath(d) for d in extra_dirs if d]
    roots.append(os.path.abspath(AppPaths.crt_log_root()))
    return roots


def _read_text_auto(abs_path):
    """UTF-8(BOM 포함) -> cp949 -> 치환 순으로 로그를 읽는다.

    구현은 core/text_io.py 로 옮겼다(같은 규칙이 세 곳에 중복돼 있었고, 그중 하나는
    engine 이 api 를 참조하는 역방향 import 로 이 함수를 쓰고 있었다). 기존 호출부가
    많아 이름은 그대로 두고 위임한다.
    """
    return read_log_text(abs_path)


def _parse_terminal_session_filename(fname):
    """파일명 -> 장비명. 규칙은 core/log_naming.py 단일 출처(호출부가 많아 이름만 유지)."""
    return device_from_log_name(fname)


class LogFileBrowserApiMixin:
    def _active_names(self):
        """활성 프로파일의 (고객사명, 프로파일명). 활성 프로젝트가 없으면 (None, None)."""
        try:
            self._project()
        except RuntimeError:
            return None, None
        return self.resolve_active_customer_profile_names()

    def _active_profile_log_paths(self, create=False):
        """현재 활성 프로파일의 최신 run 경로 dict {root, run_id, original, problem, masking, reports}.
        점검 이력(run)이 없으면 None. create=True는 이미 있는 run의 하위 폴더만 보장한다."""
        customer_name, profile_name = self._active_names()
        if not customer_name:
            return None
        from engine import log_storage
        return log_storage.get_profile_log_paths(customer_name, profile_name, create=create)

    def _active_log_dirs(self, kind):
        """활성 프로파일에서 kind 로그가 들어있을 수 있는 실존 폴더들 — log_storage가 결정."""
        customer_name, profile_name = self._active_names()
        if not customer_name:
            return []
        from engine import log_storage
        return log_storage.iter_log_dirs(customer_name, profile_name, kind)

    def _active_original_log_dir(self):
        """CRT 로그 인제스트가 원본을 넣을 폴더 — 최신 run의 raw/.
        점검 회차가 아직 없으면 이 CRT 로그들을 담을 회차를 새로 만든다."""
        paths = self._active_profile_log_paths(create=True)
        if paths:
            return paths["original"]
        customer_name, profile_name = self._active_names()
        if not customer_name:
            return None
        from engine import log_storage
        return log_storage.generate_new_run_dir(customer_name, profile_name,
                                                execution_mode="crt_ingest")["original"]

    def _log_copy_dirs(self):
        """같은 점검 로그의 사본이 존재할 수 있는 디렉터리 전부(모든 run + 과거 폴더)."""
        return [entry["path"] for entry in self._active_log_dirs("original")]

    def _derived_output_paths(self, fname):
        """원본 로그 fname에서 파생된 결과 파일들(분석 problem/*, 마스킹 masked/*)의 경로.

        원본만 지우고 파생 결과를 남기면 'Log Analysis'/'Log Masking' 탭에는 이미 없는 로그의
        분석 결과가 계속 보인다 — 원본이 사라지면 그 결과도 함께 사라져야 한다.
        분석 결과 이름 규칙은 engine/log_analysis.run_analysis()·start_ai_log_analysis()의
        "{접두어}{stamp}_{device}_problems.txt", 마스킹은 "{원본이름}_masked.txt"."""
        device, stamp = parse_inspection_log_name(fname)

        targets = []
        problem_names = []
        if stamp and device:
            problem_names = [f"{prefix}{stamp}_{device}_problems.txt"
                             for prefix in ("RuleCheck_", "LocalAI_", "CloudAI_")]
        for entry in self._active_log_dirs("problem"):
            for name in problem_names:
                targets.append(os.path.join(entry["path"], name))
        for entry in self._active_log_dirs("masking"):
            targets.append(os.path.join(entry["path"], f"{body}_masked.txt"))
            for name in problem_names:
                targets.append(os.path.join(entry["path"],
                                            f"{name[:-len('.txt')]}_masked.txt"))
        return targets

    def _collect_txt_files(self, kind, source_label=None):
        """kind 로그 폴더 전체의 .txt를 한 목록으로 — 같은 파일명은 1행으로 합치고
        사본 경로를 paths에 모은다. 사본 하나만 지워져도 목록이 늘어나지 않는다."""
        grouped = {}
        for entry in self._active_log_dirs(kind):
            label = source_label or (f"Run {entry['run_id']}" if entry["run_id"] else "이전 버전 폴더")
            for path in glob.glob(os.path.join(entry["path"], "*.txt")):
                fname = os.path.basename(path)
                try:
                    st = os.stat(path)
                except OSError:
                    continue  # 목록을 만드는 사이에 지워진 파일 — 없는 것으로 취급
                row = grouped.get(fname)
                if row is None:
                    grouped[fname] = {"path": path, "paths": [path], "name": fname,
                                      "device": _parse_terminal_session_filename(fname),
                                      "source": label, "run_id": entry["run_id"],
                                      "mtime": st.st_mtime, "size": st.st_size}
                    continue
                row["paths"].append(path)
                if st.st_mtime > row["mtime"]:
                    # 대표 행은 가장 최근 사본 기준 — 표시되는 경로/시각이 왔다갔다 하지 않게.
                    row.update({"path": path, "source": label, "run_id": entry["run_id"],
                                "mtime": st.st_mtime, "size": st.st_size})
        files = sorted(grouped.values(), key=lambda f: f["mtime"], reverse=True)
        for f in files:
            f["mtime_str"] = datetime.datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
        return files

    def list_log_files(self):
        """점검 로그 탭 — 활성 프로파일의 원본 점검 로그(.txt) 목록.
        {path, paths, device, source, run_id, mtime, mtime_str, size} — 최신순 정렬.
        점검 이력이 없으면 빈 목록(예전처럼 다른 랩의 레거시 로그로 채우지 않는다)."""
        return self._collect_txt_files("original")

    def scan_crt_log_directory(self):
        """CRTlog 폴더를 스캔하여 활성 프로파일의 인벤토리 장비명과 일치하는 로그를 복사한다.
        {device}_{timestamp}.txt 형태에서 호스트명을 파싱하거나, 실패 시 파일 앞 10줄에서
        프롬프트(예: Core1#)를 찾아 매핑한다."""
        try:
            project_id = self._project()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}

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
        # 복사할 파일이 하나라도 있을 때만 run 폴더를 만든다 — 매칭되는 CRT 로그가 없는데
        # 감시 주기마다 빈 run 폴더가 쌓이면 '점검 이력 있음'으로 오인된다.
        original_dir = None

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
                if original_dir is None:
                    original_dir = self._active_original_log_dir()
                    if not original_dir:
                        return {"error": "로그 원본 폴더(runs/<run_id>/raw) 경로를 찾을 수 없습니다."}
                # 파일명 충돌을 피하기 위해 원본 파일명을 그대로 유지한다
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
        """path가 활성 프로파일 폴더(또는 CRT 로그 폴더) 하위의 실제 파일인지 검증.
        허용되면 절대경로, 아니면 None."""
        abs_path = os.path.abspath(path)
        customer_name, profile_name = self._active_names()
        extra_dirs = ()
        if customer_name:
            from engine import log_storage
            pdir = log_storage.existing_profile_dir(customer_name, profile_name)
            if pdir is not None:
                extra_dirs = (str(pdir),)
        allowed = _allowed_log_roots(extra_dirs)
        if not any(abs_path.startswith(root + os.sep) or abs_path == root for root in allowed):
            return None
        return abs_path

    def read_log_file(self, path):
        """경로 검증 후 원문 반환 — 활성 프로파일 폴더(또는 CRT 로그 폴더) 하위만 허용."""
        abs_path = self._validate_log_path(path)
        if abs_path is None:
            return {"error": "허용되지 않은 경로입니다."}
        if not os.path.isfile(abs_path):
            return {"error": "파일이 존재하지 않습니다."}
        return {"text": _read_text_auto(abs_path)}

    def delete_log_files(self, paths):
        """로그 뷰어 다중 선택 삭제 — 허용된 경로의 .txt 파일만 삭제.
        반환: {"deleted": [path,...], "errors": {path: message}}.

        같은 파일명의 사본(다른 run 폴더·과거 버전 폴더)을 **전부** 지운다.
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
            # 지울 대상: 요청 경로 + 사본이 있을 수 있는 모든 디렉터리의 같은 이름
            #            + 이 원본에서 파생된 분석/마스킹 결과.
            targets = ([abs_path] + [os.path.join(d, fname) for d in copy_dirs]
                       + self._derived_output_paths(fname))
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
        folder: 'root'(프로파일 루트) | 'original'(raw) | 'problem' | 'masking'(masked) — 최신 run 기준.
        점검 이력이 없으면 프로파일 루트를 연다(빈 run 폴더를 만들지 않는다)."""
        customer_name, profile_name = self._active_names()
        if not customer_name:
            return {"error": "활성 프로파일이 없습니다."}
        from engine import log_storage
        log_paths = log_storage.get_profile_log_paths(customer_name, profile_name)
        if not log_paths:
            target = log_storage.get_profile_dir(customer_name, profile_name)
        else:
            target = log_paths.get(folder) or log_paths["root"]
        os.makedirs(target, exist_ok=True)
        log_storage.open_in_file_explorer(target)
        return {"ok": True, "path": target}

    def list_original_log_files(self):
        """'Log Masking' 탭의 원본 소스 선택 — 원본 로그 목록(점검 로그 탭과 동일 소스)."""
        return self.list_log_files()

    def list_problem_log_files(self):
        """'Log Analysis' 탭 / 'Log Masking' 탭의 필터링된 소스 선택 — problem 로그 .txt 목록."""
        return self._collect_txt_files("problem")

    def list_masking_log_files(self):
        """'Log Masking' 탭 — 마스킹 결과(.txt) 목록."""
        return self._collect_txt_files("masking")

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
