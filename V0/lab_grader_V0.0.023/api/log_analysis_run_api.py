"""LogAnalysisRunApiMixin — 'Log Analysis' 탭의 규칙기반/AI 분석 실행.
00_orignal_log -> 01_problem_log 파이프라인이며, 파일 목록/열람은 log_file_browser_api.py 참고.
이 클래스는 LogFileBrowserApiMixin(_active_profile_log_paths)과 SettingsApiMixin
(get_local_ai_config/ensure_lemonade_model_loaded/_load_ai_config)이 이미 조합된
Api 인스턴스 위에서만 동작한다.

3가지 분석(program=규칙기반, local=로컬AI, cloud=클라우드AI)은 모두 백그라운드 스레드에서
실행되어 탭을 이동해도 계속 진행되며, get_analysis_jobs_status()로 진행률/경과·예상 시간을
폴링해 상단바 진행바에 표시한다(web_ui/js/analysis-progress.js 참고).
"""
import os
import glob
import time
import threading

from api.log_file_browser_api import _read_text_auto

_JOB_KINDS = ("program", "local", "cloud")


class LogAnalysisRunApiMixin:
    def _jobs(self):
        if not hasattr(self, "_analysis_jobs"):
            self._analysis_jobs = {
                kind: {
                    "status": "idle",  # idle | running | done | error
                    "current": 0,
                    "total": 0,
                    "message": "",
                    "start_ts": None,
                    "end_ts": None,
                    "error": None,
                    "results": None,
                }
                for kind in _JOB_KINDS
            }
            self._analysis_jobs_lock = threading.Lock()
        return self._analysis_jobs

    def _set_job(self, kind, **fields):
        jobs = self._jobs()
        with self._analysis_jobs_lock:
            jobs[kind].update(fields)

    def get_analysis_jobs_status(self):
        """상단바 진행바 폴링용 — 3개 분석 종류(program/local/cloud)의 현재 상태를 한 번에 반환.
        각 항목: {status, current, total, message, elapsed_sec, eta_sec, error}."""
        jobs = self._jobs()
        now = time.time()
        out = {}
        with self._analysis_jobs_lock:
            for kind, job in jobs.items():
                elapsed = None
                eta = None
                if job["start_ts"] is not None:
                    end = job["end_ts"] if job["end_ts"] is not None else now
                    elapsed = end - job["start_ts"]
                    if job["status"] == "running" and job["current"] > 0 and job["total"] > 0:
                        per_item = elapsed / job["current"]
                        eta = max(0.0, per_item * (job["total"] - job["current"]))
                out[kind] = {
                    "status": job["status"],
                    "current": job["current"],
                    "total": job["total"],
                    "message": job["message"],
                    "elapsed_sec": elapsed,
                    "eta_sec": eta,
                    "error": job["error"],
                }
        return out

    def _run_job_thread(self, kind, worker):
        self._set_job(kind, status="running", current=0, total=0, message="준비 중...",
                       start_ts=time.time(), end_ts=None, error=None, results=None)
        try:
            results = worker()
            self._set_job(kind, status="done", end_ts=time.time(), message="완료", results=results)
        except Exception as e:
            self._set_job(kind, status="error", end_ts=time.time(), error=str(e), message="오류")

    # ---------- 규칙기반 분석 ----------
    def start_log_analysis(self):
        """'Log Analysis' 탭 — '분석 실행' 버튼. 즉시 반환하고 백그라운드 스레드에서 진행되며,
        진행률은 get_analysis_jobs_status()['program']으로 폴링한다."""
        if self._jobs()["program"]["status"] == "running":
            return {"error": "이미 규칙기반 분석이 진행 중입니다."}
        profile_paths = self._active_profile_log_paths()
        if not profile_paths:
            return {"error": "활성 프로파일이 없습니다."}

        def worker():
            from engine import log_analysis

            def on_progress(done, total, filename):
                self._set_job("program", current=done, total=total, message=filename)

            return log_analysis.run_analysis(profile_paths["original"], profile_paths["problem"],
                                              progress_callback=on_progress)

        threading.Thread(target=self._run_job_thread, args=("program", worker), daemon=True).start()
        return {"ok": True}

    # ---------- AI 분석(로컬/클라우드) ----------
    _AI_MODE_PREFIXES = {"local": "LocalAI_", "cloud": "CloudAI_"}

    def start_ai_log_analysis(self, ai_mode):
        """'Log Analysis' 탭 — 'Run Local AI Analysis' / 'Run Cloud AI Analysis' 버튼.
        즉시 반환하고 백그라운드 스레드에서 진행되며, 진행률은
        get_analysis_jobs_status()[ai_mode]로 폴링한다."""
        if ai_mode not in self._AI_MODE_PREFIXES:
            return {"error": f"알 수 없는 AI 모드: {ai_mode}"}
        if self._jobs()[ai_mode]["status"] == "running":
            return {"error": "이미 해당 AI 분석이 진행 중입니다."}
        profile_paths = self._active_profile_log_paths()
        if not profile_paths:
            return {"error": "활성 프로파일이 없습니다."}
        original_dir = profile_paths["original"]
        if not original_dir or not os.path.isdir(original_dir):
            return {"error": "00_orignal_log 폴더가 없습니다."}

        # 클라우드는 API 키 등록 여부를 즉시 확인해 사용자에게 바로 알림(로컬은 모델 준비에
        # 시간이 걸릴 수 있으므로 백그라운드 스레드 안에서 확인).
        if ai_mode == "cloud":
            local_cfg = self._load_ai_config()
            node = next((p for p in local_cfg.get("providers", []) if p.get("type") == "cloud_apis"), None)
            entry = next((e for e in (node or {}).get("entries", []) if e.get("enabled") and e.get("api_key")), None)
            if entry is None:
                return {"error": "Cloud AI 설정이 없습니다. 설정 탭에서 API 키를 등록하고 체크하세요."}

        def worker():
            print(f"[AI 분석] 시작 mode={ai_mode}")
            if ai_mode == "local":
                self._set_job("local", message="로컬 모델 준비 중...")
                api_cfg = self.get_local_ai_config()
                endpoint = api_cfg.get("endpoint")
                model = api_cfg.get("model")
                ready = self.ensure_lemonade_model_loaded(endpoint, model)
                if not ready.get("ok"):
                    raise RuntimeError(f"로컬 AI 모델 준비 실패: {ready.get('detail', '')}")
            else:
                local_cfg = self._load_ai_config()
                node = next((p for p in local_cfg.get("providers", []) if p.get("type") == "cloud_apis"), None)
                entry = next((e for e in (node or {}).get("entries", []) if e.get("enabled") and e.get("api_key")), None)
                if entry is None:
                    raise RuntimeError("Cloud AI 설정이 없습니다. 설정 탭에서 API 키를 등록하고 체크하세요.")
            api_cfg = self.get_local_ai_config() if ai_mode == "local" else entry

            from ai_analysis.router import analyze_raw_log_text

            problem_dir = profile_paths["problem"]
            results = []
            os.makedirs(problem_dir, exist_ok=True)
            paths = sorted(glob.glob(os.path.join(original_dir, "*.txt")))
            total = len(paths)
            self._set_job(ai_mode, total=total, current=0, message="분석 중...")
            for i, path in enumerate(paths):
                raw_text = _read_text_auto(path)
                analysis_text = analyze_raw_log_text(raw_text, ai_mode, api_cfg)
                if analysis_text.startswith("[AI 분석 오류]"):
                    print(f"[AI 분석] 실패: {os.path.basename(path)} -> {analysis_text}")
                prefix = self._AI_MODE_PREFIXES[ai_mode]
                out_name = prefix + os.path.splitext(os.path.basename(path))[0] + "_problems.txt"
                out_path = os.path.join(problem_dir, out_name)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(analysis_text)
                results.append({"source": os.path.basename(path), "output": out_name})
                self._set_job(ai_mode, current=i + 1, message=os.path.basename(path))
            return results

        threading.Thread(target=self._run_job_thread, args=(ai_mode, worker), daemon=True).start()
        return {"ok": True}
