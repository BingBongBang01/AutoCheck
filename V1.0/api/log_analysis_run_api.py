"""LogAnalysisRunApiMixin — 'Log Analysis' 탭의 규칙기반/AI 분석 실행.
최신 점검 회차(runs/<run_id>)의 raw -> problem 파이프라인이며, 파일 목록/열람은
log_file_browser_api.py 참고.
이 클래스는 LogFileBrowserApiMixin(_active_profile_log_paths)과 SettingsApiMixin
(get_local_ai_config/_ensure_lemonade_model_loaded/_load_ai_config)이 이미 조합된
Api 인스턴스 위에서만 동작한다.

3가지 분석(program=규칙기반, local=로컬AI, cloud=클라우드AI)은 모두 백그라운드 스레드에서
실행되어 탭을 이동해도 계속 진행되며, get_analysis_jobs_status()로 진행률/경과·예상 시간을
폴링해 상단바 진행바에 표시한다(web_ui/js/analysis-progress.js 참고).

이 PC 밖으로 나가는 AI 분석은 보낼 내용을 미리 보여 주고 승인받은 뒤에만 보낸다 —
preview_ai_transmission → (승인) → start_ai_log_analysis(mode, preview_id). IMPROVEMENT_PLAN 4-2.

실시간 감시는 예전에 이 파일에 함께 있었다(1,200줄, 관심사 6개). 지금은 api/realtime_watch_api.py
(화면 API)와 engine/realtime_watch_service.py(상태·로직)에 있다 — IMPROVEMENT_PLAN 3-5.
"""
import os
import glob

from api.job_runner import JobRunner

_JOB_KINDS = ("program", "local", "cloud")
# 화면에서 고른 점검 회차(run_id)가 없어졌다 — 그사이 지웠거나 다른 프로파일로 바꿨다.
NO_RUN_ERROR = "고른 점검 회차를 찾을 수 없습니다(지워졌을 수 있습니다) — 화면을 다시 열어 회차를 고르세요."


class LogAnalysisRunApiMixin:
    def _jobs(self):
        if not hasattr(self, "_analysis_job_runner"):
            self._analysis_job_runner = JobRunner(_JOB_KINDS)
        return self._analysis_job_runner

    def get_analysis_jobs_status(self):
        """상단바 진행바 폴링용 — 3개 분석 종류(program/local/cloud)의 현재 상태를 한 번에 반환.
        각 항목: {status, current, total, message, elapsed_sec, eta_sec, error}."""
        return self._jobs().status_all()

    # ---------- 규칙기반 분석 ----------
    def start_log_analysis(self, run_id=None):
        """'Log Analysis' 탭 — '분석 실행' 버튼. run_id: 분석할 점검 회차(화면에서 고른 것, 없으면 최신 회차).
        즉시 반환하고 백그라운드 스레드에서 진행되며, 진행률은 get_analysis_jobs_status()['program']으로 폴링한다."""
        if self._jobs().is_running("program"):
            return {"error": "이미 규칙기반 분석이 진행 중입니다."}
        # problem/ 폴더에 결과를 쓰므로 create=True. 점검 이력(run)이 아예 없으면 분석할 원본도 없다.
        profile_paths = self._active_profile_log_paths(create=True, run_id=run_id)
        if not profile_paths:
            return {"error": NO_RUN_ERROR if run_id else "활성 프로파일이 없습니다."}
        if not glob.glob(os.path.join(profile_paths["original"], "*.txt")):
            return {"error": "분석할 점검 로그가 없습니다. 먼저 점검을 1회 수행하세요."}

        def worker():
            from engine import log_analysis

            def on_progress(done, total, filename):
                self._jobs().set("program", current=done, total=total, message=filename)

            # 작업 센터의 취소(IMPROVEMENT_PLAN 4-9) — 지금 파일을 마치고 멈춘다. 분석한 파일의 결과는 남는다.
            return log_analysis.run_analysis(profile_paths["original"], profile_paths["problem"],
                                              progress_callback=on_progress,
                                              should_stop=lambda: self._jobs().cancel_requested("program"))

        self._jobs().start("program", worker)
        return {"ok": True}

    # ---------- AI 분석(로컬/클라우드) ----------
    # 이 PC 밖으로 나가는 분석(클라우드, 다른 PC 의 로컬 AI)은 두 단계다 — preview_ai_transmission 이 보낼
    # 내용을 만들어 서버에 맡기고 요약을 보여 주면, 사용자가 승인한 미리보기 id 로 start_ai_log_analysis 가
    # 바로 그 내용만 보낸다(IMPROVEMENT_PLAN 4-2, D3 — ai_analysis/transmission.py).
    _AI_MODE_PREFIXES = {"local": "LocalAI_", "cloud": "CloudAI_"}
    _NO_CLOUD_KEY = "Cloud AI 설정이 없습니다. 설정 탭에서 API 키를 등록하고 체크하세요."

    def _ai_previews(self):
        if not hasattr(self, "_ai_preview_store"):
            from ai_analysis.transmission import PreviewStore
            self._ai_preview_store = PreviewStore()
        return self._ai_preview_store

    def _ai_run_inputs(self, ai_mode, run_id=None):
        """AI 분석 공통 준비 — {profile_paths, paths, is_virtual, names} 또는 {error}. run_id: 분석할 점검 회차(없으면 최신)."""
        if ai_mode not in self._AI_MODE_PREFIXES:
            return {"error": f"알 수 없는 AI 모드: {ai_mode}"}
        if self._jobs().is_running(ai_mode):
            return {"error": "이미 해당 AI 분석이 진행 중입니다."}
        profile_paths = self._active_profile_log_paths(create=True, run_id=run_id)
        if not profile_paths:
            return {"error": NO_RUN_ERROR if run_id else "활성 프로파일이 없습니다."}
        paths = sorted(glob.glob(os.path.join(profile_paths["original"], "*.txt")))
        if not paths:
            return {"error": "분석할 점검 로그가 없습니다. 먼저 점검을 1회 수행하세요."}
        # 프로파일의 '가상환경' 옵션에 따라 AI 프롬프트를 바꾼다 — 가상환경이면 하드웨어/센서
        # 미지원 출력을 무시하고, 실기 장비면 그것을 실제 장애로 보고하게 한다.
        # 워커 스레드가 아니라 여기서 읽는다(스레드 안에서 활성 프로파일이 바뀔 수 있다).
        from engine.profile_manager import profile_manager
        names = self._active_names()
        is_virtual = bool(names[0]) and profile_manager.is_virtual(*names)
        return {"profile_paths": profile_paths, "paths": paths, "is_virtual": is_virtual, "names": names}

    def _ai_ring(self, ai_mode):
        """이번 분석에 쓸 설정들 — 로컬은 하나, 클라우드는 사용 체크된 첫 항목과 같은 곳으로 가는 키들."""
        if ai_mode == "local":
            return [self.get_local_ai_config()]
        from ai_analysis.transmission import cloud_key_ring
        return cloud_key_ring(self._load_ai_config())

    def _inventory_names(self):
        """활성 프로파일 장비 목록의 장비명 -> (이름들, 경고). 외부로 나가는 AI 요청에서 모두 가린다 — 설명·LLDP 에
        이웃으로 적힌 장비명은 그 로그의 장비명이 아니라서 장비 목록에서 알아 와야 한다. 못 읽으면 경고로 알린다."""
        from ai_analysis.transmission import neighbor_names
        try:
            names = neighbor_names(self._load_inventory(self._paths())["devices"])
        except Exception as exc:
            print(f"[AI 전송] 장비 목록을 읽지 못했습니다: {exc}")
            return (), ["장비 목록을 읽지 못해 다른 장비의 이름(설명·LLDP 의 이웃 장비명)은 가리지 못했습니다 — "
                        "'내용 보기'로 확인하세요."]
        if not names:
            return (), ["장비 목록에 가릴 장비명이 없어 다른 장비의 이름(설명·LLDP 의 이웃 장비명)은 가리지 못했습니다 — "
                        "'내용 보기'로 확인하세요."]
        return names, []

    def preview_ai_transmission(self, ai_mode, run_id=None):
        """'Run Cloud/Local AI Analysis' 버튼의 첫 단계 — 이 PC 밖으로 나갈 내용을 만들어 요약을 돌려준다.
        보낼 내용은 서버에 미리보기 id 로 맡겨 두고(get_ai_transmission_part 로 본다), 승인하면
        start_ai_log_analysis(ai_mode, preview_id) 가 그 내용만 보낸다. 로컬 AI 가 이 PC 안이면
        {needs_approval: False} — 데이터가 밖으로 나가지 않으니 바로 시작한다."""
        from ai_analysis import raw_log_analyzer, transmission
        inputs = self._ai_run_inputs(ai_mode, run_id)
        if "error" in inputs:
            return inputs
        ring = self._ai_ring(ai_mode)
        if not ring:
            return {"error": self._NO_CLOUD_KEY}
        if not raw_log_analyzer.leaves_this_pc(ai_mode, ring[0]):
            return {"needs_approval": False}
        known_names, warnings = self._inventory_names()
        plan = transmission.build_plan(inputs["paths"], ai_mode, ring[0], inputs["is_virtual"], known_names)
        profile_paths = inputs["profile_paths"]
        preview = {
            "mode": ai_mode,
            "plan": plan,
            "is_virtual": inputs["is_virtual"],
            "known_names": len(known_names),
            "warnings": warnings,
            "destination": transmission.destination(ai_mode, ring),
            "signature": transmission.ring_signature(ai_mode, ring),
            "sha256": transmission.payload_digest(plan),
            "customer": inputs["names"][0],
            "profile": inputs["names"][1],
            "run_root": profile_paths.get("root"),
            "run_id": profile_paths.get("run_id"),
        }
        preview_id = self._ai_previews().put(ai_mode, preview)
        return transmission.preview_view(preview_id, preview)

    def get_ai_transmission_part(self, preview_id, index):
        """전송 미리보기에서 파일 하나가 실제로 보낼 내용(가린 뒤의 프롬프트 전체)."""
        from ai_analysis import transmission
        preview = self._ai_previews().get(preview_id)
        if preview is None:
            return {"error": "미리보기가 만료되었습니다. 분석 버튼을 다시 눌러 확인하세요."}
        try:
            item = preview["plan"][int(index)]
        except (TypeError, ValueError, IndexError):
            return {"error": "없는 항목입니다."}
        return {"source": item["source"], "text": transmission.part_text(item)}

    def start_ai_log_analysis(self, ai_mode, preview_id=None, run_id=None):
        """'Log Analysis' 탭 — 'Run Local AI Analysis' / 'Run Cloud AI Analysis'(미리보기 승인 뒤).
        이 PC 밖으로 나가는 분석은 preview_id 로 승인한 미리보기의 내용만 보내고, 승인과 결과를 승인 이력에
        남긴다. 즉시 반환하고 백그라운드 스레드에서 진행되며, 진행률은 get_analysis_jobs_status()[ai_mode]로
        폴링한다."""
        from ai_analysis import raw_log_analyzer, transmission
        inputs = self._ai_run_inputs(ai_mode, run_id)
        if "error" in inputs:
            return inputs
        ring = self._ai_ring(ai_mode)
        if not ring:
            return {"error": self._NO_CLOUD_KEY}
        profile_paths = inputs["profile_paths"]
        approved = None
        if raw_log_analyzer.leaves_this_pc(ai_mode, ring[0]):
            if not preview_id:
                return {"error": "보낼 내용을 먼저 확인하세요 — 전송 미리보기에서 승인해야 분석을 시작합니다."}
            approved = self._ai_previews().take(preview_id, ai_mode)
            if approved is None:
                return {"error": "전송 미리보기가 만료되었거나 이미 사용되었습니다. 분석 버튼을 다시 눌러 확인하세요."}
            if approved["signature"] != transmission.ring_signature(ai_mode, ring):
                return {"error": "미리보기를 본 뒤 AI 설정(항목·모델·주소)이 바뀌었습니다. 분석 버튼을 다시 눌러 확인하세요."}
            if (approved["run_root"], approved["run_id"]) != (profile_paths.get("root"), profile_paths.get("run_id")):
                return {"error": "미리보기를 본 뒤 활성 프로파일(점검 회차)이 바뀌었습니다. 분석 버튼을 다시 눌러 확인하세요."}

        def worker():
            print(f"[AI 분석] 시작 mode={ai_mode}")
            if approved:
                from core.app_logger import log_event
                record = transmission.approval_record(preview_id, approved)
                transmission.append_record(record)
                log_event(f"[AI 전송] 승인 — {record['provider']}/{record['model']} → {record['host']}, "
                          f"파일 {len(record['files'])}개 · 요청 {record['requests']}건 · {record['chars']}자",
                          source="ai_transmission")
            # 작업 센터의 취소(IMPROVEMENT_PLAN 4-9) — 파일과 파트 사이, 재시도·쉼을 기다리는 동안 본다. 보내는 중인
            # 요청 하나는 끝까지 기다린다. 분석한 파일(멈춘 파일은 받은 파트까지 — 남은 파트는 '중단'으로 적는다)은 남는다.
            def stopping():
                return self._jobs().cancel_requested(ai_mode)

            if ai_mode == "local":
                self._jobs().set("local", message="로컬 모델 준비 중...")
                ready = self._ensure_lemonade_model_loaded(ring[0].get("endpoint"), ring[0].get("model"))
                if not ready.get("ok"):
                    raise RuntimeError(f"로컬 AI 모델 준비 실패: {ready.get('detail', '')}")
            plan = approved["plan"] if approved else transmission.build_plan(
                inputs["paths"], ai_mode, ring[0], inputs["is_virtual"])
            keys = raw_log_analyzer.KeyRing(ring, ai_mode, should_stop=stopping)
            problem_dir = profile_paths["problem"]
            os.makedirs(problem_dir, exist_ok=True)
            totals = {"sent": 0, "failed": 0, "keys_used": []}
            results = []
            cancelled = False
            self._jobs().set(ai_mode, total=len(plan), current=0, message="분석 중...")
            try:
                for i, item in enumerate(plan):
                    if stopping():
                        cancelled = True
                        break
                    if item["requests"] is None:
                        analysis_text = transmission.NO_FINDINGS_TEXT
                    else:
                        sent = raw_log_analyzer.send_prompts(item["requests"], ai_mode, keys)
                        analysis_text = sent.text
                        totals["sent"] += sent.sent
                        totals["failed"] += sent.failed
                        totals["keys_used"] += [k for k in sent.keys_used if k not in totals["keys_used"]]
                        if sent.failed:
                            print(f"[AI 분석] {item['source']}: {sent.failed}개 파트를 분석하지 못했습니다.")
                        cancelled = sent.stopped
                    with open(os.path.join(problem_dir, item["out_name"]), "w", encoding="utf-8") as f:
                        f.write(analysis_text)
                    results.append({"source": item["source"], "output": item["out_name"],
                                    **({"stopped": True} if cancelled else {})})
                    self._jobs().set(ai_mode, current=i + 1, message=item["source"])
                    if cancelled:
                        break
            except Exception as exc:
                if approved:
                    transmission.append_record(transmission.finished_record(
                        preview_id, sent=totals["sent"], failed=totals["failed"],
                        keys_used=totals["keys_used"], error=exc))
                raise
            if approved:
                transmission.append_record(transmission.finished_record(
                    preview_id, sent=totals["sent"], failed=totals["failed"], keys_used=totals["keys_used"],
                    cancelled=cancelled))
            return results

        if not self._jobs().start(ai_mode, worker):
            return {"error": "이미 해당 AI 분석이 진행 중입니다."}
        return {"ok": True}
