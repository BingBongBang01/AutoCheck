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
from core.paths import AppPaths
from api.job_runner import JobRunner

_JOB_KINDS = ("program", "local", "cloud")


class LogAnalysisRunApiMixin:
    def _jobs(self):
        if not hasattr(self, "_analysis_job_runner"):
            self._analysis_job_runner = JobRunner(_JOB_KINDS)
        return self._analysis_job_runner

    def get_analysis_jobs_status(self):
        """상단바 진행바 폴링용 — 3개 분석 종류(program/local/cloud)의 현재 상태를 한 번에 반환.
        각 항목: {status, current, total, message, elapsed_sec, eta_sec, error}."""
        return self._jobs().status_all()

    def start_crt_log_watcher(self):
        """CRTlog 폴더에 새 로그가 기록되면 1.5초 디바운스 후 자동으로 UI에 알리거나 분석을 트리거한다."""
        if hasattr(self, "_crt_watcher_running") and self._crt_watcher_running:
            return {"error": "Watcher가 이미 실행 중입니다."}
            
        self._crt_watcher_running = True
        
        def watcher_loop():
            crt_dir = AppPaths.crt_log_root()
            file_states = {} # {filename: (last_mtime, last_notified_mtime)}
            
            while getattr(self, "_crt_watcher_running", False):
                time.sleep(0.5)
                
                if not os.path.isdir(crt_dir):
                    continue
                    
                now = time.time()
                triggered = False
                
                try:
                    for fname in os.listdir(crt_dir):
                        if not fname.lower().endswith(".txt"):
                            continue
                            
                        abs_path = os.path.join(crt_dir, fname)
                        try:
                            mtime = os.path.getmtime(abs_path)
                        except OSError:
                            continue
                            
                        state = file_states.get(fname)
                        if not state:
                            # 최초 발견 시
                            file_states[fname] = (mtime, 0)
                            continue
                            
                        last_mtime, last_notified_mtime = state
                        
                        # mtime 갱신 (파일이 아직 쓰여지고 있는 중)
                        if mtime != last_mtime:
                            file_states[fname] = (mtime, last_notified_mtime)
                            continue
                            
                        # 파일이 수정된 지 1.5초(debounce window) 경과 && 아직 처리안됨
                        if mtime == last_mtime and mtime != last_notified_mtime and (now - mtime) > 1.5:
                            file_states[fname] = (mtime, mtime)
                            triggered = True
                            
                except OSError:
                    pass
                    
                if triggered:
                    # 매핑 및 00_orignal_log 복사 실행
                    res = self.scan_crt_log_directory()
                    if res.get("copied", 0) > 0:
                        # UI 업데이트 이벤트 (pywebview window 객체가 존재할 경우)
                        if hasattr(self, "_window") and self._window:
                            try:
                                self._window.evaluate_js("if(window.onCrtLogIngested) window.onCrtLogIngested(); else if(window.refreshLogs) window.refreshLogs();")
                            except Exception:
                                pass
                                
        t = threading.Thread(target=watcher_loop, daemon=True, name="CRTLogWatcher")
        t.start()
        return {"ok": True}
        
    def stop_crt_log_watcher(self):
        self._crt_watcher_running = False
        return {"ok": True}

    # ---------- 실시간 Baseline Diff (스트리밍) ----------
    # start_crt_log_watcher()는 '파일이 다 쓰였는지'를 보고 00_orignal_log로 복사하는 인제스트 경로다.
    # 아래는 목적이 달라 별도로 돈다 — 작업자가 명령을 치는 순간(0.3초)의 차분만 읽어 Baseline과 대조한다.
    # 결과가 흘러가는 곳은 두 군데:
    #   1) 토스트: evaluate_js로 즉시 push (놓치면 안 되는 알림이므로 push)
    #   2) 연결 탭 하단 '자동 실시간 감시' 3분할 패널: engine/realtime_monitor.py에 상태를 쌓고
    #      프론트엔드가 get_realtime_monitor_state()로 폴링 (0.3초마다 화면 전체를 push하면
    #      evaluate_js 호출이 과도해지고 대량 로그에서 UI가 밀린다)

    def _baseline_store(self):
        if not hasattr(self, "_baseline_store_obj"):
            from engine.baseline_store import BaselineStore
            self._baseline_store_obj = BaselineStore()
        return self._baseline_store_obj

    def _realtime_monitor(self):
        if not hasattr(self, "_realtime_monitor_obj"):
            from engine.realtime_monitor import RealtimeMonitor
            self._realtime_monitor_obj = RealtimeMonitor()
        return self._realtime_monitor_obj

    def load_realtime_baseline(self):
        """활성 프로파일의 00_orignal_log를 읽어 Baseline 스냅샷을 메모리에 로드."""
        paths = self._active_profile_log_paths()
        if not paths:
            return {"error": "활성 프로파일이 없습니다."}
        customer, profile = self.resolve_active_customer_profile_names()
        result = self._baseline_store().load_baseline(customer, profile, original_dir=paths["original"])
        if result.get("loaded", 0) == 0:
            return {"error": "00_orignal_log에 사전 점검 로그가 없습니다. 먼저 점검을 1회 수행하세요."}
        result["summary"] = self._baseline_store().summary()
        return result

    def _realtime_inventory_targets(self):
        """장비 목록(Device Inventory)의 활성 장비 [{name, ip, port}...]."""
        try:
            return self.get_terminal_targets() or []
        except Exception:
            return []

    def _realtime_watch_devices(self, device_names=None):
        """감시 대상 장비명 — 인자로 받은 목록(실시간 감시 탭에서 체크된 장비)이 없으면
        장비 목록에서 활성화된 전체를 쓴다."""
        if device_names:
            return [str(n) for n in device_names if n]
        return [t["name"] for t in self._realtime_inventory_targets()]

    def start_realtime_baseline_watch(self, interval=0.3, device_names=None):
        """CRTlog tail + Baseline Diff 시작. Baseline이 아직 없으면 자동으로 먼저 로드한다.

        Baseline이 없어도(사전 점검 이력이 없는 첫 방문) 감시 자체는 진행한다 —
        설정 삭제/DOWN syslog 판정은 기준 없이도 유효하고, Baseline 대조가 필요한 항목만
        체크리스트에서 '기준 없음'으로 남는다.
        """
        watcher = getattr(self, "_baseline_stream_watcher", None)
        if watcher is not None and watcher.is_running():
            return {"error": "실시간 감시가 이미 실행 중입니다."}

        store = self._baseline_store()
        baseline_warning = None
        try:
            customer, profile = self.resolve_active_customer_profile_names()
            if store.key != (customer, profile) or not store.device_names():
                loaded = self.load_realtime_baseline()
                if loaded.get("error"):
                    baseline_warning = loaded["error"]
        except Exception as exc:
            baseline_warning = f"Baseline 로드 실패: {exc}"

        from core.crt_stream_watcher import CRTStreamWatcher
        from engine.baseline_diff_engine import BaselineDiffEngine
        from engine.stream_device_matcher import StreamDeviceMatcher

        watched = self._realtime_watch_devices(device_names)
        self._realtime_watch_targets = {name.lower() for name in watched}
        self._baseline_diff_engine = BaselineDiffEngine(store)
        self._realtime_monitor().reset(watched, store.device_names())

        # 로그 파일 -> 장비 판정. SecureCRT가 파일명을 접속 IP로 남기는 환경이 많아
        # 인벤토리의 IP까지 넘겨야 매칭된다(engine/stream_device_matcher.py 주석 참고).
        inventory = self._realtime_inventory_targets()
        watched_lower = self._realtime_watch_targets
        picked = [t for t in inventory if (t.get("name") or "").lower() in watched_lower] or inventory
        self._realtime_device_matcher = StreamDeviceMatcher(picked)

        watcher = CRTStreamWatcher(
            AppPaths.crt_log_root(),
            self._on_crt_stream_delta,
            interval=float(interval or 0.3),
            device_resolver=self._realtime_device_matcher.resolve,
            on_error=lambda exc: print(f"[실시간 Diff] 감시 오류: {exc}"),
        )
        self._baseline_stream_watcher = watcher
        watcher.start()
        return {"ok": True, "devices": watched, "baseline_devices": store.device_names(),
                "interval": watcher.interval, "warning": baseline_warning,
                "watch_dir": str(AppPaths.crt_log_root())}

    def stop_realtime_baseline_watch(self):
        watcher = getattr(self, "_baseline_stream_watcher", None)
        if watcher is not None:
            watcher.stop()
        engine = getattr(self, "_baseline_diff_engine", None)
        if engine is not None:
            engine.reset_context()
        return {"ok": True}

    def get_realtime_baseline_status(self):
        """UI 토글 상태 복원용 — 감시 여부/Baseline 장비/누적 경고 수/자동시작 설정."""
        watcher = getattr(self, "_baseline_stream_watcher", None)
        monitor = self._realtime_monitor()
        return {
            "running": bool(watcher and watcher.is_running()),
            "devices": self._baseline_store().device_names(),
            "watched": sorted(getattr(self, "_realtime_watch_targets", set())),
            "alert_count": len(monitor.alerts(limit=10000)),
            "autostart": self.get_realtime_watch_autostart().get("autostart", False),
            "watcher": watcher.status() if watcher else None,
            "watch_dir": str(AppPaths.crt_log_root()),
        }

    def get_realtime_monitor_state(self, tail=120):
        """연결 탭 하단 3분할 패널 폴링용 — 장비별 실시간 로그 + 체크리스트 + 오류 분석."""
        watcher = getattr(self, "_baseline_stream_watcher", None)
        state = self._realtime_monitor().state(tail=int(tail or 120))
        state["running"] = bool(watcher and watcher.is_running())
        # 감시가 도는데도 화면이 비어 있을 때 원인을 바로 보여준다 — 대개 파일-장비 매칭 실패다.
        status = watcher.status() if watcher else {}
        state["unmatched_files"] = status.get("unmatched", [])
        state["tracked_files"] = status.get("tracked_files", 0)
        state["watch_dir"] = str(AppPaths.crt_log_root())
        state["ok"] = True
        return state

    def get_realtime_alerts(self, device=None, limit=100):
        """토스트 클릭 시 열리는 세부 Diff 모달용 — 최신순 경고 이력."""
        return {"ok": True, "alerts": self._realtime_monitor().alerts(device, int(limit or 100))}

    def clear_realtime_alerts(self):
        """경고 이력과 체크리스트 판정을 함께 비운다 — 화면의 '초기화'는 둘 다를 뜻한다."""
        self._realtime_monitor().clear_alerts()
        return {"ok": True}

    def probe_realtime_log_files(self):
        """'왜 감시가 안 되나'를 화면에서 확인하기 위한 진단 — CRTlog의 각 파일이 어떤 근거로
        어느 장비에 매칭되는지 보여준다. 감시를 시작하지 않은 상태에서도 호출할 수 있다."""
        from engine.stream_device_matcher import StreamDeviceMatcher
        from core.crt_stream_watcher import DEFAULT_EXTENSIONS

        root = str(AppPaths.crt_log_root())
        matcher = StreamDeviceMatcher(self._realtime_inventory_targets())
        rows = []
        if os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                path = os.path.join(root, name)
                if not os.path.isfile(path) or not name.lower().endswith(DEFAULT_EXTENSIONS):
                    continue
                try:
                    with open(path, "rb") as f:
                        head = f.read(4096).decode("utf-8", errors="replace")
                    size = os.path.getsize(path)
                except OSError:
                    continue
                row = matcher.probe(path, head)
                row["size"] = size
                rows.append(row)
        return {"ok": True, "watch_dir": root, "known_devices": matcher.known_names(), "files": rows}

    # ---------- 자동 시작 설정(앱 전역) ----------
    def _realtime_watch_config_path(self):
        return str(AppPaths.config_root() / "realtime_watch.yaml")

    _LAYOUT_DEFAULTS = {"split_ratio": 0.52, "right_ratio": 0.45, "view_mode": "split"}

    def _read_realtime_watch_config(self):
        import yaml
        path = self._realtime_watch_config_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            return {}

    def _write_realtime_watch_config(self, updates):
        """부분 갱신 — 자동시작 토글이 화면 비율 설정을 날리지 않게 기존 값을 읽어 병합한다."""
        from core.atomic_io import dump_yaml_atomic
        cfg = self._read_realtime_watch_config()
        cfg.update(updates)
        path = self._realtime_watch_config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        dump_yaml_atomic(cfg, path)
        return cfg

    def get_realtime_watch_autostart(self):
        """프로그램 실행 시 실시간 감시를 자동으로 켤지 여부."""
        return {"autostart": bool(self._read_realtime_watch_config().get("autostart", False))}

    def set_realtime_watch_autostart(self, enabled):
        self._write_realtime_watch_config({"autostart": bool(enabled)})
        return {"ok": True, "autostart": bool(enabled)}

    def get_realtime_watch_layout(self):
        """실시간 감시 화면의 구분선 비율·보기 모드·선택 장비.

        localStorage가 아니라 파일에 두는 이유: pywebview는 file:// 오리진으로 UI를 띄우는데
        WebView 구현/정책에 따라 file:// localStorage가 비활성이거나 세션마다 초기화된다.
        '재실행해도 비율이 유지된다'를 보장하려면 앱이 직접 저장해야 한다.
        """
        cfg = self._read_realtime_watch_config()
        layout = dict(self._LAYOUT_DEFAULTS)
        for key in layout:
            if cfg.get(key) is not None:
                layout[key] = cfg[key]
        layout["split_ratio"] = _clamp_ratio(layout["split_ratio"], self._LAYOUT_DEFAULTS["split_ratio"])
        layout["right_ratio"] = _clamp_ratio(layout["right_ratio"], self._LAYOUT_DEFAULTS["right_ratio"])
        if layout["view_mode"] not in ("tabs", "split"):
            layout["view_mode"] = "split"
        layout["selected_devices"] = [str(n) for n in (cfg.get("selected_devices") or [])]
        return layout

    def save_realtime_watch_layout(self, split_ratio=None, right_ratio=None,
                                    view_mode=None, selected_devices=None):
        updates = {}
        if split_ratio is not None:
            updates["split_ratio"] = _clamp_ratio(split_ratio, self._LAYOUT_DEFAULTS["split_ratio"])
        if right_ratio is not None:
            updates["right_ratio"] = _clamp_ratio(right_ratio, self._LAYOUT_DEFAULTS["right_ratio"])
        if view_mode in ("tabs", "split"):
            updates["view_mode"] = view_mode
        if selected_devices is not None:
            updates["selected_devices"] = [str(n) for n in selected_devices if n]
        if updates:
            self._write_realtime_watch_config(updates)
        return {"ok": True, **self.get_realtime_watch_layout()}

    def autostart_realtime_baseline_watch(self):
        """__main__에서 창 생성 직후 호출 — 설정이 켜져 있을 때만 감시를 시작한다."""
        if not self.get_realtime_watch_autostart().get("autostart"):
            return {"ok": True, "started": False}
        result = self.start_realtime_baseline_watch()
        return {"ok": True, "started": not result.get("error"), "detail": result}

    # ---------- 스트림 수신 ----------
    def _on_crt_stream_delta(self, device, text, path, is_history=False):
        """CRTStreamWatcher 콜백(워커 스레드) — 차분 텍스트를 화면 버퍼에 넣고 판정해 push.

        is_history=True는 '감시 시작 전에 이미 기록돼 있던 부분'이다. 화면 좌측에는 채워 넣지만
        Diff 판정은 돌리지 않는다 — 어제 친 'no vlan 100'이 오늘 감시를 켠 순간 CRITICAL 토스트로
        튀어나오면 알림을 신뢰할 수 없게 된다.
        """
        engine = getattr(self, "_baseline_diff_engine", None)
        if engine is None:
            return
        # 감시 대상으로 고른 장비만 본다(CRTlog 폴더에는 다른 회차/다른 장비 로그도 섞여 있다).
        targets = getattr(self, "_realtime_watch_targets", None)
        if targets and (device or "").lower() not in targets:
            return

        monitor = self._realtime_monitor()
        monitor.append_lines(device, text, is_history=is_history)
        if is_history:
            return
        alerts = engine.analyze_stream(device, text)
        if not alerts:
            return
        for alert in alerts:
            alert["source_file"] = os.path.basename(path)
        monitor.apply_alerts(alerts)
        self._push_realtime_alerts(alerts)

    def _push_realtime_alerts(self, alerts):
        import json
        from api import window_ref
        try:
            window = window_ref.get_window()
        except RuntimeError:
            return
        payload = json.dumps(alerts, ensure_ascii=False)
        try:
            window.evaluate_js(
                f"window.onRealtimeDiffAlert && window.onRealtimeDiffAlert({payload})")
        except Exception as exc:
            print(f"[실시간 Diff] UI push 실패: {exc}")

    # ---------- 규칙기반 분석 ----------
    def start_log_analysis(self):
        """'Log Analysis' 탭 — '분석 실행' 버튼. 즉시 반환하고 백그라운드 스레드에서 진행되며,
        진행률은 get_analysis_jobs_status()['program']으로 폴링한다."""
        if self._jobs().is_running("program"):
            return {"error": "이미 규칙기반 분석이 진행 중입니다."}
        profile_paths = self._active_profile_log_paths()
        if not profile_paths:
            return {"error": "활성 프로파일이 없습니다."}

        def worker():
            from engine import log_analysis

            def on_progress(done, total, filename):
                self._jobs().set("program", current=done, total=total, message=filename)

            return log_analysis.run_analysis(profile_paths["original"], profile_paths["problem"],
                                              progress_callback=on_progress)

        self._jobs().start("program", worker)
        return {"ok": True}

    # ---------- AI 분석(로컬/클라우드) ----------
    _AI_MODE_PREFIXES = {"local": "LocalAI_", "cloud": "CloudAI_"}

    def start_ai_log_analysis(self, ai_mode):
        """'Log Analysis' 탭 — 'Run Local AI Analysis' / 'Run Cloud AI Analysis' 버튼.
        즉시 반환하고 백그라운드 스레드에서 진행되며, 진행률은
        get_analysis_jobs_status()[ai_mode]로 폴링한다."""
        if ai_mode not in self._AI_MODE_PREFIXES:
            return {"error": f"알 수 없는 AI 모드: {ai_mode}"}
        if self._jobs().is_running(ai_mode):
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
                self._jobs().set("local", message="로컬 모델 준비 중...")
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
            from engine.log_analysis import extract_suspicious_context

            problem_dir = profile_paths["problem"]
            results = []
            os.makedirs(problem_dir, exist_ok=True)
            paths = sorted(glob.glob(os.path.join(original_dir, "*.txt")))
            total = len(paths)
            self._jobs().set(ai_mode, total=total, current=0, message="분석 중...")
            for i, path in enumerate(paths):
                raw_text = _read_text_auto(path)
                
                # 원문 전체가 아닌, 의심되는 블록 주위 텍스트만 추출하여 AI에 전달 (비용 및 품질 최적화)
                context_text = extract_suspicious_context(raw_text)
                
                if not context_text.strip():
                    analysis_text = "이 로그에서는 이상 징후를 발견하지 못했습니다."
                else:
                    if ai_mode == "local":
                        # 로컬 AI용 경량화 구조화 템플릿 (토큰 페이로드 축소 및 500 오류/타임아웃 방지)
                        fname = os.path.basename(path)
                        payload_text = (
                            f"[DEVICE_NAME]: {fname}\n"
                            f"[PLATFORM]: Arista vEOS-lab (Virtual Platform)\n"
                            f"[FILTERED_PRE_CHECK_OUTPUT]:\n{context_text}\n"
                            f"[ANALYSIS_TASK]: Focus strictly on operational failures (Reloads, Interface/MLAG down, BGP/EVPN, STP changes). Ignore expected virtual limitations (show module/environment unavailable)."
                        )
                        analysis_text = analyze_raw_log_text(payload_text, ai_mode, api_cfg)
                    else:
                        # 클라우드 AI 모드는 기존 컨텍스트 텍스트 전달 (vEOS 프롬프트 덧붙임)
                        analysis_text = analyze_raw_log_text(context_text, ai_mode, api_cfg)
                
                if analysis_text.startswith("[AI 분석 오류]"):
                    print(f"[AI 분석] 실패: {os.path.basename(path)} -> {analysis_text}")
                
                fname = os.path.basename(path)
                body = fname[:-len(".txt")] if fname.endswith(".txt") else fname
                if body.startswith("AutoCheck_"):
                    body_no_prefix = body[len("AutoCheck_"):]
                    parts = body_no_prefix.rsplit("_", 2)
                    if len(parts) == 3:
                        device, stamp = parts[0], f"{parts[1]}_{parts[2]}"
                    else:
                        device, stamp = body_no_prefix, "unknown_time"
                else:
                    parts = body.split("_", 3)
                    if len(parts) == 4:
                        stamp, device = f"{parts[0]}_{parts[1]}", parts[3]
                    else:
                        stamp, device = "unknown_time", body
                
                ai_mode_str = "LocalAI" if ai_mode == "local" else "CloudAI"
                out_name = f"{ai_mode_str}_{stamp}_{device}_problems.txt"
                out_path = os.path.join(problem_dir, out_name)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(analysis_text)
                results.append({"source": os.path.basename(path), "output": out_name})
                self._jobs().set(ai_mode, current=i + 1, message=os.path.basename(path))
            return results

        self._jobs().start(ai_mode, worker)
        return {"ok": True}


def _clamp_ratio(value, fallback):
    """구분선 비율은 0.18~0.82로 제한 — 한쪽 패널이 0폭이 되어 다시 드래그할 수 없게 되는 것을 막는다."""
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return fallback
    return min(0.82, max(0.18, ratio))
