"""TerminalInspectionApiMixin — 열려있는 터미널 세션들에 커맨드 카탈로그를 순서대로 실행하고
장비별 결과를 파일로 저장. 세션 레지스트리는 terminal_session_api.py의 모듈 전역을 그대로 사용한다.

진행 상태·중지·진행 로그는 앱 공용 JobRunner(api/job_runner.py)의 'inspection' 슬롯이 맡는다. 예전엔
여기서 dict+잠금으로 따로 관리해서, 작업 함수가 예외로 끝나면 '진행 중'이 영영 남아 다시 점검할 수
없었다(IMPROVEMENT_PLAN 3-4).
"""
import os
import re
import glob
import time
import shutil
import threading

from core import vendors
from core.log_naming import build_inspection_log_name
from core.ansi_sanitizer import clean_terminal_log, strip_ansi
from api.job_runner import JobRunner
from api.terminal_session_api import _sessions, _sessions_lock, _wait_for_settled_output, _PROMPT_TAIL_RE

# 세션 레지스트리처럼 모듈 전역이다 — 세션별 점검 스레드(_run_one_session_inspection)가 함께 센다.
_inspection_jobs = JobRunner(("inspection",))
_KIND = "inspection"
# 중지를 요청할 때 진행 로그에 남기는 말 — 연결 탭의 '중지'와 상태바 작업 센터의 '취소'가 같은 말을 쓴다.
STOP_MESSAGE = "중지 요청됨 — 진행 중인 커맨드 완료 후 저장합니다."

_PASSWORD_PROMPT_RE = re.compile(r"(?i)password:\s*$")
_NO_ENABLE_SECRET = "장비 목록에서 enable 비밀번호(또는 기본 enable 비밀번호)를 입력하세요"


def _cancel_requested():
    return _inspection_jobs.cancel_requested(_KIND)


def inspection_job_runner():
    """세션 점검의 JobRunner — 상태바 작업 센터(api/job_center_api.py)가 함께 보여 준다."""
    return _inspection_jobs


def _log(line):
    _inspection_jobs.log(_KIND, line)


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


def _prompt_mode(text):
    """출력 끝의 프롬프트 글자 — '#'(특권 모드)·'>'(사용자 모드). 프롬프트로 끝나지 않으면 None."""
    match = _PROMPT_TAIL_RE.search(strip_ansi((text or "")[-400:]))
    return match.group(2) if match else None


def _wait_for_reply(sess, max_wait_sec=15.0, poll_interval=0.05):
    """enable·비밀번호를 보낸 뒤 장비가 프롬프트('>'/'#')나 'Password:' 에서 멈출 때까지 모은 출력.

    조용해지기만 기다리면(_wait_for_settled_output) AAA(TACACS) 응답이 느린 장비에서 비밀번호 묻기가 오기 전에
    돌아가고, 그다음 보낸 명령 줄이 비밀번호로 들어간다."""
    collected, waited = "", 0.0
    while waited < max_wait_sec:
        with sess["read_lock"]:
            if sess["inspect_buffer"]:
                collected += "".join(sess["inspect_buffer"])
                sess["inspect_buffer"] = []
        tail = strip_ansi(collected[-400:])
        if _PASSWORD_PROMPT_RE.search(tail) or _PROMPT_TAIL_RE.search(tail):
            return collected
        time.sleep(poll_interval)
        waited += poll_interval
    return collected


def _enter_enable(sess):
    """카탈로그의 'enable' 을 세션에서 처리한다 — 반환: None(특권 모드에 있음) 또는 실패 이유.

    enable 을 다른 명령과 한 덩어리로 보내면, 장비가 'Password:' 를 물을 때 뒤의 명령 줄이 비밀번호로 들어가 점검
    전체가 어긋난다(Cisco 는 로그인 뒤 '>' 에서 시작한다 — IMPROVEMENT_PLAN 4-5). 그래서 따로 보내고 응답을 보며 enable
    비밀번호를 넣는다. 비밀번호가 틀리면 빈 줄로 답해 묻기를 끝낸다(IOS 는 세 번 물은 뒤 '>' 로 돌아간다). 비밀번호 없이
    enable 이 되지 않는 플랫폼(Cisco)은 비밀번호가 없으면 시도하지 않는다 — 장비에 인증 실패만 남는다."""
    channel = sess["channel"]
    mode = _prompt_mode(sess.get("accumulated_tail"))
    if mode is None:     # 프롬프트를 아직 못 봤다(배너가 길었거나 출력이 끊김) — 빈 줄로 프롬프트를 다시 받는다
        channel.send("\n")
        mode = _prompt_mode(_wait_for_reply(sess))
    if mode == "#":
        return None
    secret = sess.get("enable_secret") or ""
    if not secret and not vendors.enable_without_secret(sess.get("platform", vendors.DEFAULT_PLATFORM)):
        return f"enable 비밀번호가 없어 특권 모드로 들어가지 않았습니다 — {_NO_ENABLE_SECRET}"
    channel.send("enable\n")
    reply = _wait_for_reply(sess)
    answers = 0
    while answers < 3 and _PASSWORD_PROMPT_RE.search(strip_ansi(reply[-200:])):
        channel.send((secret if answers == 0 else "") + "\n")
        answers += 1
        reply = _wait_for_reply(sess)
    if _prompt_mode(reply) == "#":
        return None
    hint = "enable 비밀번호가 맞는지 확인하세요" if secret else _NO_ENABLE_SECRET
    return f"enable(특권 모드)로 들어가지 못했습니다 — {hint}"


def _run_one_session_inspection(session_id, commands, results_dirs, sync_name=None):
    """세션 하나에 명령을 보내고 출력을 장비별 원본 로그로 저장한다.
    sync_name: 저장하기 전에 장비 이름을 호스트명으로 맞추는 함수(TerminalSessionApiMixin._sync_session_name)."""
    with _sessions_lock:
        sess = _sessions.get(session_id)
    if sess is None:
        return
    device = sess["device"]
    channel = sess["channel"]
    if sess.get("platform", vendors.DEFAULT_PLATFORM) is None:
        # 모르는 벤더에는 점검 명령(Arista 명령)을 보내지 않는다 — 터미널로는 그대로 쓸 수 있다.
        _inspection_jobs.advance(_KIND, len(commands))
        _log(f"[{device}] {vendors.UNSUPPORTED_TEXT} — 점검 명령을 보내지 않았습니다")
        return
    # 'enable' 은 장비가 비밀번호를 물을 수 있어 따로 처리한다(_enter_enable) — 나머지만 한 덩어리로 보낸다.
    wants_enable = any(c.strip().lower() == "enable" for c in commands)
    commands = [c for c in commands if c.strip().lower() != "enable"]

    with sess["read_lock"]:
        sess["inspect_buffer"] = []
    cancelled = _drain_before_send(sess)
    with sess["read_lock"]:
        sess["inspect_buffer"] = []

    if cancelled:
        # 커맨드를 보내기도 전에 중지된 세션 — 저장할 내용이 없다. 여기서 파일을 만들면
        # 본문이 중단 표시 한 줄뿐인 빈 로그가 '점검 로그' 목록에 쌓인다.
        _log(f"[{device}] 시작 전 중지됨 — 저장할 내용 없음")
        return

    if wants_enable:
        try:
            problem = _enter_enable(sess)
        except (OSError, EOFError) as e:
            problem = f"enable 전송 실패: {e}"
        _inspection_jobs.advance(_KIND)
        if problem:
            # 사용자 모드로 나머지를 보내면 권한 오류만 남은 로그가 점검 결과로 저장된다 — 이 장비는 점검하지 않는다.
            _inspection_jobs.advance(_KIND, len(commands))
            _log(f"[{device}] {problem} — 이 장비는 점검하지 않았습니다")
            return
        with sess["read_lock"]:
            sess["inspect_buffer"] = []
    if not commands:
        _log(f"[{device}] enable 밖에 보낼 명령이 없어 저장하지 않음")
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
            if _cancel_requested():
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
                            _inspection_jobs.advance(_KIND)
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
                if current_cmd_idx < len(commands):
                    _inspection_jobs.advance(_KIND, len(commands) - current_cmd_idx)
                    current_cmd_idx = len(commands)
                break

            time.sleep(0.1)

        cleaned = clean_terminal_log(collected)
        collected_any = bool(cleaned.strip())
        output_lines.append(cleaned)
        _log(f"[{device}] 수동 점검 종료됨 - 로그 저장")
    except (OSError, EOFError) as e:
        # 전송 실패는 원인을 남겨야 하므로 내용이 없어도 저장한다.
        collected_any = True
        output_lines.append(f"오류: {e}")
        _log(f"[{device}] 전송 실패: {e}")

    if not collected_any:
        _log(f"[{device}] 수집된 출력이 없어 저장하지 않음")
        return

    if sync_name is not None:
        # 파일 이름은 장비 이름이다 — 접속한 장비의 호스트명으로 맞춘 다음 짓는다. 점검이 끝난 지금은 장비가 프롬프트로
        # 돌아와 있다. 화면의 출력 폴링이 점검 중에 이미 바꿨으면 그 이름이다(아래에서 세션 이름을 다시 읽는다).
        renamed = sync_name(session_id, settled=True)
        if renamed and renamed.get("to"):
            _log(f"[{device}] 장비 이름을 호스트명 {renamed['to']}(으)로 바꿈 — 옛 이름은 별칭으로 남김")
        elif renamed and renamed.get("note"):
            _log(renamed["note"])
    device = sess["device"]

    text = "\n".join(output_lines)
    # 파일명 규칙(만드는 쪽)도 core/log_naming.py 를 쓴다 — 읽는 쪽과 같은 출처여야 한다.
    log_name = build_inspection_log_name(device)
    saved_paths = []
    for results_dir in results_dirs:
        os.makedirs(results_dir, exist_ok=True)
        fname = os.path.join(results_dir, log_name)
        with open(fname, "w", encoding="utf-8") as f:
            f.write(text)
        saved_paths.append(fname)
    _log(f"[{device}] 결과 저장됨: {', '.join(saved_paths)}")


class TerminalInspectionApiMixin:
    def run_terminal_inspection(self, session_ids):
        """열려있는 세션들에 커맨드 카탈로그의 활성 커맨드를 순서대로 입력하고, 장비별 결과를 파일로 저장."""
        if _inspection_jobs.is_running(_KIND):
            return {"error": "이미 점검이 진행 중입니다."}
        try:
            paths = self._paths()
            project_id = self._project()
        except RuntimeError:
            return {"error": "활성 프로파일이 없습니다."}
        catalog = self._load_catalog(paths)
        from engine import command_catalog as cc
        from engine import vendor_commands
        # 작업자의 세션에 그대로 입력되므로 읽기 전용 정책을 통과한 명령만 보낸다 — 예전 기본
        # 카탈로그의 `conf` 가 켜진 채 남아 있으면, 점검이 끝난 터미널이 설정 모드에 머물렀다.
        commands, skipped = cc.split_enabled_commands(catalog)
        if not commands:
            return {"error": "활성화된 점검 커맨드가 없습니다 (커맨드 카탈로그에서 확인)"}
        valid_ids = [sid for sid in session_ids if sid in _sessions]
        if not valid_ids:
            return {"error": "연결된 세션이 없습니다. 먼저 접속하세요."}
        # 세션마다 그 장비 플랫폼의 명령을 보낸다 — Arista 는 카탈로그 명령 그대로, Cisco 는 같은 체크ID 의 Cisco 명령
        # (engine/vendor_commands). 모르는 벤더의 세션은 빈 목록이다(_run_one_session_inspection 이 알리고 보내지 않는다).
        platforms = {sid: _sessions[sid].get("platform", vendors.DEFAULT_PLATFORM) for sid in valid_ids}
        plans = {platform: vendor_commands.commands_for(catalog, platform)
                 for platform in set(platforms.values()) if platform in vendor_commands.TRANSLATED_PLATFORMS}
        session_commands = {sid: plans[platform][0] if platform in plans else (commands if platform else [])
                            for sid, platform in platforms.items()}

        from engine import log_storage
        customer_name, profile_name = self._resolve_active_customer_profile_names()
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

        start_log = [f"점검 시작 — 대상 {len(valid_ids)}개 세션, 커맨드 {len(commands)}개"]
        start_log += [f"실행하지 않음: {command} — {reason}" for command, reason in skipped]
        for platform, (send, _blocked, unmapped) in sorted(plans.items()):
            start_log.append(f"{vendors.label(platform)} 세션: 같은 목적의 명령 {len(send)}개로 바꿔 보냄"
                             + (f" — 해당 명령이 없어 보내지 않음: {', '.join(unmapped)}" if unmapped else ""))

        from engine.run_manager import run_manager
        if run_handle is not None:
            run_manager.start_run(run_handle)

        def worker():
            # 점검 로그 원본은 이 회차 폴더(runs/<run_id>/raw/)에만 저장한다.
            results_dirs = [log_paths["original"]]
            threads = [threading.Thread(target=_run_one_session_inspection,
                                        args=(sid, session_commands[sid], results_dirs, self._sync_session_name))
                       for sid in valid_ids]
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
                refreshed = self._refresh_realtime_baseline_after_inspection()
                msg = (f"Baseline 자동 갱신 — 장비 {refreshed.get('loaded', 0)}대"
                       + (f", 신규 기준 {len(refreshed['gained'])}대" if refreshed.get("gained") else "")
                       if refreshed.get("ok") else
                       f"Baseline 자동 갱신 건너뜀: {refreshed.get('error', '알 수 없음')}")
            except Exception as exc:
                msg = f"Baseline 자동 갱신 실패: {exc}"

            try:
                realtime = self._realtime()
                if realtime.started_within(600):
                    from engine import log_analysis
                    from api.log_file_browser_api import _parse_terminal_session_filename
                    from engine import log_cache
                    import uuid

                    log_analysis.run_analysis(log_paths["original"], log_paths["problem"])

                    new_alerts = []
                    paths = sorted(glob.glob(os.path.join(log_paths["original"], "*.txt")))
                    for path in paths:
                        device = _parse_terminal_session_filename(os.path.basename(path))
                        # run_analysis() 가 바로 위에서 이 파일들을 이미 파싱했다.
                        # 캐시를 거치면 두 번째 파싱이 일어나지 않는다.
                        findings = log_cache.cached_findings(path)
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
                        realtime.apply_external_alerts(new_alerts)
                        msg += f" (자동 로그 분석 완료 및 실시간 감시 패널 반영됨: {len(new_alerts)}건)"
            except Exception as e:
                msg += f" (자동 로그 분석/반영 실패: {e})"

            _log(msg)
            _log("전체 점검 완료")
            return {"saved": len(saved)}

        total = sum(len(session_commands[sid]) for sid in valid_ids)
        if not _inspection_jobs.start(_KIND, worker, total=total, log=start_log):
            # 위의 확인과 여기 사이에 다른 점검이 먼저 시작됐다(버튼을 연달아 누름) — 만들어 둔 빈 run 을 치운다.
            if run_handle is not None:
                run_manager.abort_run(run_handle)
            shutil.rmtree(log_paths["run_dir"], ignore_errors=True)
            return {"error": "이미 점검이 진행 중입니다."}
        return {"ok": True}

    def get_terminal_inspection_status(self):
        """점검 진행 상태 — {running, done, status, error, log, total, current}. done 은 이번 점검이 끝났다는
        뜻이다(완료·중지·오류 모두) — status 로 셋을 구분한다(cancelled = 중지 요청 후 저장하고 끝남)."""
        job = _inspection_jobs.status(_KIND)
        return {"running": job["status"] == "running",
                "done": job["status"] in ("done", "cancelled", "error"),
                "status": job["status"], "error": job["error"], "log": job["log"],
                "total": job["total"], "current": job["current"]}

    def stop_terminal_inspection(self):
        """진행 중인 점검에 중지를 요청한다 — 지금까지 수집된 결과는 항상 저장한다.

        예전에는 discard=True로 결과를 버리는 경로가 있었고 UI가 prompt()로 저장/폐기를
        물어봤다. 중지를 누르는 상황에서 원하는 건 사실상 항상 저장이라 폐기 경로를 없앴다
        (지우고 싶으면 '점검 로그' 탭에서 삭제 — 되돌릴 수 있는 방향으로)."""
        if not _inspection_jobs.request_cancel(_KIND, STOP_MESSAGE):
            return {"error": "진행 중인 점검이 없습니다."}
        return {"ok": True}
