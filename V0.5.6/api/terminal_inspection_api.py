"""TerminalInspectionApiMixin — 열려있는 터미널 세션들에 커맨드 카탈로그를 순서대로 실행하고
장비별 결과를 파일로 저장. 세션 레지스트리는 terminal_session_api.py의 모듈 전역을 그대로 사용한다.
"""
import os
import glob
import time
import shutil
import threading
import datetime

from core.ansi_sanitizer import clean_terminal_log, strip_ansi
from api.terminal_session_api import _sessions, _sessions_lock, _wait_for_settled_output, _PROMPT_TAIL_RE
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
        idle_start = time.time()

        while True:
            with _inspection_lock:
                if _inspection_job["cancel_requested"]:
                    break

            has_new = False
            with sess["read_lock"]:
                if sess["inspect_buffer"]:
                    has_new = True
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

            now = time.time()
            if has_new:
                idle_start = now

            # 완료 판단: 모든 커맨드가 출력에서 확인되고 프롬프트로 돌아왔거나 출력이 1초간 정적일 때
            if current_cmd_idx >= len(commands):
                tail_prompt = bool(_PROMPT_TAIL_RE.search(strip_ansi(collected[-400:]))) if collected else False
                if tail_prompt or (now - idle_start >= 1.0):
                    break

            # 안전망: 무한 대기 방지 (20초 동안 무응답 시 자동 종료)
            if now - idle_start >= 20.0:
                with _inspection_lock:
                    if current_cmd_idx < len(commands):
                        _inspection_job["completed_commands"] += (len(commands) - current_cmd_idx)
                        current_cmd_idx = len(commands)
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
        log_paths = log_storage.generate_new_run_dir(
            customer_name, profile_name, device_count=len(valid_ids),
            command_count=len(commands), execution_mode="terminal_inspection")
        run_handle = log_paths.get("run_handle")
        # 설정 스냅샷은 이 회차(run) 폴더 안에 남긴다 — 프로파일 루트에 쌓으면 어느 점검 때
        # 쓰인 카탈로그인지 알 수 없고, 프로파일 루트가 점검 산출물로 지저분해진다.
        log_storage.save_config_snapshot(log_paths["run_dir"], {
            "commands_catalog": paths.get("commands_catalog"),
            "lab_meta": paths.get("lab_meta"),
        })

        with _inspection_lock:
            _inspection_job.update({"running": True, "done": False, "cancel_requested": False,
                                     "log": [f"점검 시작 — 대상 {len(valid_ids)}개 세션, 커맨드 {len(commands)}개"],
                                     "total_commands": len(valid_ids) * len(commands),
                                     "completed_commands": 0})

        from engine.run_manager import run_manager
        if run_handle is not None:
            run_manager.start_run(run_handle)

        def worker():
            # 점검 로그 원본은 이 회차 폴더(runs/<run_id>/raw/)에만 저장한다.
            results_dirs = [log_paths["original"]]
            threads = [threading.Thread(target=_run_one_session_inspection, args=(sid, commands, results_dirs)) for sid in valid_ids]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            saved = glob.glob(os.path.join(log_paths["original"], "*.txt"))
            if not saved:
                # 결과 파일이 하나도 없으면(전부 실패/즉시 취소) 이 회차 폴더를 지운다 — 빈 run이
                # 남으면 대시보드·Workspace가 '점검 이력 있음'으로 판단해 데이터 없는데 회차·수치가 생긴다.
                shutil.rmtree(log_paths["run_dir"], ignore_errors=True)
            elif run_handle is not None:
                try:
                    cancelled = _cancel_requested()
                    run_manager.update_progress(run_handle, progress=100.0 if not cancelled else None,
                                                success_count=len(saved))
                    run_manager.abort_run(run_handle) if cancelled else run_manager.finish_run(run_handle)
                except Exception as exc:
                    print(f"[점검] run 상태 기록 실패: {exc}")

            # 점검 결과가 run 폴더의 raw/에 다 쓰인 다음이어야 한다 — join 뒤에 두는 이유가 그것이다.
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

            try:
                watcher = getattr(self, "_baseline_stream_watcher", None)
                if watcher and watcher.is_running():
                    monitor = self._realtime_monitor()
                    started_at = getattr(monitor, "_started_at", 0) or 0
                    if time.time() - started_at <= 600:
                        from engine import log_analysis
                        from api.log_file_browser_api import _parse_terminal_session_filename, _read_text_auto
                        import uuid

                        log_analysis.run_analysis(log_paths["original"], log_paths["problem"])

                        new_alerts = []
                        paths = sorted(glob.glob(os.path.join(log_paths["original"], "*.txt")))
                        for path in paths:
                            device = _parse_terminal_session_filename(os.path.basename(path))
                            raw_text = _read_text_auto(path)

                            findings = log_analysis.analyze_text(raw_text)
                            for finding in findings:
                                new_alerts.append({
                                    "device": device,
                                    "type": finding.get("rule_id") or "LOG_ANALYSIS",
                                    "rule_id": finding.get("rule_id"),
                                    "severity": (finding.get("severity") or "major").upper(),
                                    "message": finding.get("reason", "이상 징후"),
                                    "raw_line": finding["block"][0] if finding.get("block") else "",
                                    "ts": time.strftime("%H:%M:%S"),
                                    "alert_id": f"log_analysis_{uuid.uuid4().hex[:8]}"
                                })
                        
                        if new_alerts:
                            monitor.apply_alerts(new_alerts)
                            visible_alerts = [a for a in new_alerts if not monitor.is_hidden(a)]
                            if visible_alerts:
                                self._push_realtime_alerts(visible_alerts)
                            msg += f" (자동 로그 분석 완료 및 실시간 감시 패널 반영됨: {len(new_alerts)}건)"
            except Exception as e:
                msg += f" (자동 로그 분석/반영 실패: {e})"

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
