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

_inspection_job = {"running": False, "done": False, "log": [], "cancel_requested": False, "total_commands": 0, "completed_commands": 0}
_inspection_lock = threading.Lock()


def _cancel_requested():
    with _inspection_lock:
        return _inspection_job["cancel_requested"]


def _drain_before_send(sess, total_sec=2.0, slice_sec=0.25):
    """커맨드를 보내기 전에 남아있는 출력을 비운다.

    한 번에 total_sec을 기다리지 않고 잘게 나눠서 중간중간 중지 요청을 확인한다 —
    예전에는 통째로 기다려서, 점검시작 직후(최대 2초 안)에 중지를 누르면 이 대기가
    끝날 때까지 아무 반응이 없었다. True를 반환하면 '중지 요청됨'."""
    waited = 0.0
    while waited < total_sec:
        if _cancel_requested():
            return True
        _wait_for_settled_output(sess, settle_sec=slice_sec, max_wait_sec=slice_sec)
        waited += slice_sec
    return _cancel_requested()


def _run_one_session_inspection(session_id, commands, results_dirs):
    with _sessions_lock:
        sess = _sessions.get(session_id)
    if sess is None:
        return
    device = sess["device"]
    channel = sess["channel"]

    with sess["read_lock"]:
        sess["inspect_buffer"] = []
    cancelled = _drain_before_send(sess)
    with sess["read_lock"]:
        sess["inspect_buffer"] = []

    if cancelled:
        # 커맨드를 보내기도 전에 중지된 세션 — 저장할 내용이 없다. 여기서 파일을 만들면
        # 본문이 중단 표시 한 줄뿐인 빈 로그가 '점검 로그' 목록에 쌓인다.
        with _inspection_lock:
            _inspection_job["log"].append(f"[{device}] 시작 전 중지됨 — 저장할 내용 없음")
        return

    output_lines = []
    collected_any = False
    bulk_text = "\n!\n".join(commands) + "\n!\n"
    try:
        channel.send(bulk_text)

        collected = ""
        current_cmd_idx = 0
        while True:
            with _inspection_lock:
                if _inspection_job["cancel_requested"]:
                    break
            with sess["read_lock"]:
                if sess["inspect_buffer"]:
                    new_text = "".join(sess["inspect_buffer"])
                    collected += new_text
                    sess["inspect_buffer"] = []
                    
                    while current_cmd_idx < len(commands):
                        if commands[current_cmd_idx] in collected:
                            current_cmd_idx += 1
                            with _inspection_lock:
                                _inspection_job["completed_commands"] += 1
                        else:
                            break
            time.sleep(0.1)

        cleaned = clean_terminal_log(collected)
        collected_any = bool(cleaned.strip())
        output_lines.append(cleaned)
        with _inspection_lock:
            _inspection_job["log"].append(f"[{device}] 수동 점검 종료됨 - 로그 저장")
    except (OSError, EOFError) as e:
        # 전송 실패는 원인을 남겨야 하므로 내용이 없어도 저장한다.
        collected_any = True
        output_lines.append(f"오류: {e}")
        with _inspection_lock:
            _inspection_job["log"].append(f"[{device}] 전송 실패: {e}")

    if not collected_any:
        with _inspection_lock:
            _inspection_job["log"].append(f"[{device}] 수집된 출력이 없어 저장하지 않음")
        return

    text = "\n".join(output_lines)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    saved_paths = []
    for results_dir in results_dirs:
        os.makedirs(results_dir, exist_ok=True)
        fname = os.path.join(results_dir, f"{stamp}_raw_{device}.txt")
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
            _inspection_job.update({"running": True, "done": False, "cancel_requested": False,
                                     "log": [f"점검 시작 — 대상 {len(valid_ids)}개 세션, 커맨드 {len(commands)}개"],
                                     "total_commands": len(valid_ids) * len(commands),
                                     "completed_commands": 0})

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

            # 점검 결과가 00_orignal_log에 다 쓰인 다음이어야 한다 — join 뒤에 두는 이유가 그것이다.
            # 실시간 감시의 Baseline은 '사전 점검 결과'인데, 점검이 끝나면 그 기준이 낡는다.
            # 여기서 갱신하지 않으면 작업자가 감시 탭에서 수동으로 재시작해야 하고, 재시작하면
            # 파일 오프셋이 초기화되어 지나간 로그가 경고로 쏟아진다.
            # 갱신 실패가 점검 결과 저장을 무효화하면 안 되므로 예외는 로그로만 남긴다.
            try:
                refreshed = self.refresh_realtime_baseline_after_inspection()
                msg = (f"Baseline 자동 갱신 — 장비 {refreshed.get('loaded', 0)}대"
                       + (f", 신규 기준 {len(refreshed['gained'])}대" if refreshed.get("gained") else "")
                       if refreshed.get("ok") else
                       f"Baseline 자동 갱신 건너뜀: {refreshed.get('error', '알 수 없음')}")
            except Exception as exc:
                msg = f"Baseline 자동 갱신 실패: {exc}"

            with _inspection_lock:
                _inspection_job["running"] = False
                _inspection_job["done"] = True
                _inspection_job["log"].append(msg)
                _inspection_job["log"].append("전체 점검 완료")

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True}

    def get_terminal_inspection_status(self):
        with _inspection_lock:
            return {"running": _inspection_job["running"], "done": _inspection_job["done"],
                     "log": list(_inspection_job["log"]),
                     "total": _inspection_job.get("total_commands", 0),
                     "current": _inspection_job.get("completed_commands", 0)}

    def stop_terminal_inspection(self):
        """진행 중인 점검에 중지를 요청한다 — 지금까지 수집된 결과는 항상 저장한다.

        예전에는 discard=True로 결과를 버리는 경로가 있었고 UI가 prompt()로 저장/폐기를
        물어봤다. 중지를 누르는 상황에서 원하는 건 사실상 항상 저장이라 폐기 경로를 없앴다
        (지우고 싶으면 '점검 로그' 탭에서 삭제 — 되돌릴 수 있는 방향으로)."""
        with _inspection_lock:
            if not _inspection_job["running"]:
                return {"error": "진행 중인 점검이 없습니다."}
            _inspection_job["cancel_requested"] = True
            _inspection_job["log"].append("중지 요청됨 — 진행 중인 커맨드 완료 후 저장합니다.")
        return {"ok": True}
