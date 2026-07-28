"""TerminalInspectionApiMixin — 열려있는 터미널 세션들에 커맨드 카탈로그를 순서대로 실행하고
장비별 결과를 파일로 저장. 세션 레지스트리는 terminal_session_api.py의 모듈 전역을 그대로 사용한다.
"""
import os
import time
import threading
import datetime

from core.ansi_sanitizer import clean_terminal_log
from api.terminal_session_api import _sessions, _sessions_lock, _wait_for_settled_output
from core.paths import AppPaths

_inspection_job = {"running": False, "done": False, "log": [], "cancel_requested": False, "discard_on_cancel": False}
_inspection_lock = threading.Lock()


def _run_one_session_inspection(session_id, commands, results_dirs):
    with _sessions_lock:
        sess = _sessions.get(session_id)
    if sess is None:
        return
    device = sess["device"]
    channel = sess["channel"]
    
    with sess["read_lock"]:
        sess["inspect_buffer"] = []
    _wait_for_settled_output(sess, settle_sec=0.3, max_wait_sec=2.0)
    with sess["read_lock"]:
        sess["inspect_buffer"] = []

    with _inspection_lock:
        cancelled = _inspection_job["cancel_requested"]
        
    output_lines = []
    if cancelled:
        output_lines.append("=== 점검 중단(사용자 요청) ===")
        with _inspection_lock:
            _inspection_job["log"].append(f"[{device}] 중지됨(사용자 요청)")
    else:
        bulk_text = "\n!\n".join(commands) + "\n!\n"
        try:
            channel.send(bulk_text)
            
            collected = ""
            while True:
                with _inspection_lock:
                    if _inspection_job["cancel_requested"]:
                        break
                with sess["read_lock"]:
                    if sess["inspect_buffer"]:
                        collected += "".join(sess["inspect_buffer"])
                        sess["inspect_buffer"] = []
                import time
                time.sleep(0.1)

            output_lines.append(clean_terminal_log(collected))
            with _inspection_lock:
                _inspection_job["log"].append(f"[{device}] 수동 점검 종료됨 - 로그 저장")
        except (OSError, EOFError) as e:
            output_lines.append(f"오류: {e}")
            with _inspection_lock:
                _inspection_job["log"].append(f"[{device}] 전송 실패: {e}")

    with _inspection_lock:
        discard = _inspection_job["cancel_requested"] and _inspection_job["discard_on_cancel"]
    if discard:
        with _inspection_lock:
            _inspection_job["log"].append(f"[{device}] 중지 — 결과 폐기됨(저장 안 함)")
        return

    text = "\n".join(output_lines)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    saved_paths = []
    for results_dir in results_dirs:
        os.makedirs(results_dir, exist_ok=True)
        fname = os.path.join(results_dir, f"AutoCheck_{device}_{stamp}.txt")
        with open(fname, "w", encoding="utf-8") as f:
            f.write(text)
        saved_paths.append(fname)
    with _inspection_lock:
        _inspection_job["log"].append(f"[{device}] 결과 저장됨: {', '.join(saved_paths)}")


class TerminalInspectionApiMixin:
    def run_terminal_inspection(self, session_ids):
        """열려있는 세션들에 커맨드 카탈로그의 활성 커맨드를 순서대로 입력하고, 장비별 결과를 파일로 저장."""
        with _inspection_lock:
            if _inspection_job["running"]:
                return {"error": "이미 점검이 진행 중입니다."}
        try:
            paths = self._paths()
            project_id = self._project()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}
        catalog = self._load_catalog(paths)
        from engine import command_catalog as cc
        commands = cc.get_enabled_commands(catalog)
        if not commands:
            return {"error": "활성화된 점검 커맨드가 없습니다 (커맨드 카탈로그에서 확인)"}
        valid_ids = [sid for sid in session_ids if sid in _sessions]
        if not valid_ids:
            return {"error": "연결된 세션이 없습니다. 먼저 접속하세요."}

        from engine import log_storage
        customer_name, profile_name = self.resolve_active_customer_profile_names()
        log_paths = log_storage.get_profile_log_paths(customer_name, profile_name)
        log_storage.save_config_snapshot(log_paths["root"], {
            "commands_catalog": paths.get("commands_catalog"),
            "lab_meta": paths.get("lab_meta"),
        })

        with _inspection_lock:
            _inspection_job.update({"running": True, "done": False, "cancel_requested": False, "discard_on_cancel": False,
                                     "log": [f"점검 시작 — 대상 {len(valid_ids)}개 세션, 커맨드 {len(commands)}개"]})

        def worker():
            # 기존 labs/{project}/terminal_sessions/(Reports·Findings·AI분석이 읽는 경로)와
            # 신규 data/<고객사>/<프로파일>/00_orignal_log/ 양쪽에 동일한 원본을 저장 —
            # 새 로그 저장소 요구사항을 만족시키면서 기존 보고서 파이프라인은 그대로 유지.
            results_dirs = [log_paths["original"], str(AppPaths.terminal_sessions_dir(project_id))]
            threads = [threading.Thread(target=_run_one_session_inspection, args=(sid, commands, results_dirs)) for sid in valid_ids]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            with _inspection_lock:
                _inspection_job["running"] = False
                _inspection_job["done"] = True
                _inspection_job["log"].append("전체 점검 완료")

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def get_terminal_inspection_status(self):
        with _inspection_lock:
            return {"running": _inspection_job["running"], "done": _inspection_job["done"],
                     "log": list(_inspection_job["log"])}

    def stop_terminal_inspection(self, discard=False):
        """진행 중인 점검에 중지를 요청한다. discard=True면 지금까지 수집된 결과를 저장하지 않고 버린다."""
        with _inspection_lock:
            if not _inspection_job["running"]:
                return {"error": "진행 중인 점검이 없습니다."}
            _inspection_job["cancel_requested"] = True
            _inspection_job["discard_on_cancel"] = bool(discard)
            _inspection_job["log"].append("중지 요청됨 — 진행 중인 커맨드 완료 후 중단합니다.")
        return {"ok": True}
