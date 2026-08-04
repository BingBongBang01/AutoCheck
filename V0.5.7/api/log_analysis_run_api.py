"""LogAnalysisRunApiMixin — 'Log Analysis' 탭의 규칙기반/AI 분석 실행.
최신 점검 회차(runs/<run_id>)의 raw -> problem 파이프라인이며, 파일 목록/열람은
log_file_browser_api.py 참고.
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
import datetime
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
            # 저장된 숨김/고정을 생성 즉시 넣는다 — 프로그램을 켠 직후 첫 폴링부터 반영되어야
            # 하고, 자동시작(autostart_realtime_baseline_watch)은 UI보다 먼저 돌기 때문에
            # 프론트엔드가 get_realtime_filter()를 부를 때까지 기다릴 수 없다.
            try:
                self._realtime_monitor_obj.set_filter(self._normalized_realtime_filter())
            except Exception as exc:
                print(f"[실시간 Diff] 필터 초기 적용 실패: {exc}")
            # 프로그램을 껐다 켜도 이 프로파일에서 찾았던 오류가 그대로 보여야 한다 —
            # 감시를 시작하기 전(첫 폴링)부터 복원해 둔다.
            self._restore_realtime_state()
        return self._realtime_monitor_obj

    # ---------- 프로파일별 실시간 감시 상태 보존 ----------
    # 저장 위치는 프로파일 폴더다. 고객사/회차마다 장비도 Baseline도 다르므로 상태를 공유하면
    # 다른 회차의 경고가 섞인다. 프로파일이 바뀌면 쓰던 것을 저장하고 새 프로파일 것을 읽는다.
    def _realtime_state_path(self, customer=None, profile=None):
        from engine import log_storage
        if customer is None or profile is None:
            customer, profile = self.resolve_active_customer_profile_names()
        if not customer or not profile:
            return None
        return os.path.join(log_storage.get_profile_dir(customer, profile), "realtime_state.json")

    def _save_realtime_state(self, force=False):
        """감시 상태를 파일로 남긴다. 0.3초 tick마다 쓰면 디스크가 갈리므로 10초로 묶는다."""
        monitor = getattr(self, "_realtime_monitor_obj", None)
        if monitor is None:
            return False
        now = time.time()
        if not force and now - getattr(self, "_realtime_state_saved_at", 0) < 10:
            return False
        path = getattr(self, "_realtime_state_key_path", None) or self._realtime_state_path()
        if not path:
            return False
        from engine import realtime_monitor as rtm
        self._realtime_state_saved_at = now
        return rtm.save_snapshot(path, monitor.snapshot())

    def _restore_realtime_state(self):
        """지금 프로파일의 저장본을 monitor에 얹는다. 없으면 아무 일도 하지 않는다."""
        monitor = getattr(self, "_realtime_monitor_obj", None)
        if monitor is None:
            return 0
        path = self._realtime_state_path()
        self._realtime_state_key_path = path
        if not path:
            return 0
        from engine import realtime_monitor as rtm
        snapshot = rtm.load_snapshot(path)
        if not snapshot:
            return 0
        # 저장본에 있던 장비를 감시 대상으로 인정해야 복원이 의미가 있다 — reset() 전(프로그램
        # 시작 직후)에는 monitor의 장비 목록이 비어 있어서 전부 걸러지기 때문이다.
        monitor.adopt_devices(snapshot.get("devices") or [], snapshot.get("baseline_devices") or [])
        return monitor.restore(snapshot)

    def _sync_realtime_profile(self):
        """활성 프로파일이 바뀌었으면 쓰던 상태를 저장하고 새 프로파일 상태로 갈아끼운다.

        패널 폴링(0.8초)마다 불리므로, 프로파일 경로를 알아내는 파일 읽기는 3초로 묶는다.
        """
        now = time.time()
        if now - getattr(self, "_realtime_profile_checked_at", 0) < 3:
            return False
        self._realtime_profile_checked_at = now
        path = self._realtime_state_path()
        if path == getattr(self, "_realtime_state_key_path", None):
            return False
        watcher = getattr(self, "_baseline_stream_watcher", None)
        if watcher is not None and watcher.is_running():
            # 감시 중에는 프로파일을 따라 바꾸지 않는다 — 지금 tail 중인 판정 기준(Baseline)과
            # 화면이 어긋난다. 감시를 멈췄다 다시 시작하면 새 프로파일 상태로 열린다.
            return False
        self._save_realtime_state(force=True)
        monitor = getattr(self, "_realtime_monitor_obj", None)
        if monitor is not None:
            monitor.reset([], ())
        self._restore_realtime_state()
        return True

    def load_realtime_baseline(self):
        """활성 프로파일의 00_orignal_log를 읽어 Baseline 스냅샷을 메모리에 로드.

        수동 CRT 세션 로그는 기준에서 제외된다(engine/baseline_store.py의 load_baseline 주석
        참고) — 감시 중인 세션이 자기 자신의 Baseline이 되면 감시가 조용히 무력화된다.
        """
        paths = self._active_profile_log_paths()
        if not paths:
            return {"error": "점검 이력이 없습니다. 먼저 점검을 1회 수행하세요."}
        customer, profile = self.resolve_active_customer_profile_names()
        result = self._baseline_store().load_baseline(customer, profile, original_dir=paths["original"])
        if result.get("loaded", 0) == 0:
            return {"error": "사전 점검 로그가 없습니다. 먼저 점검을 1회 수행하세요."}
        result["summary"] = self._baseline_store().summary()
        return result

    def refresh_realtime_baseline_after_inspection(self):
        """점검(run_terminal_inspection)이 00_orignal_log에 결과를 다 쓴 직후 호출된다.

        감시를 멈추지 않는다 — BaselineDiffEngine은 BaselineStore '객체'를 들고 있으므로
        스냅샷을 갈아끼우면 다음 줄부터 새 기준으로 대조한다. 감시 스레드를 재시작하면
        CRTStreamWatcher의 파일 오프셋이 초기화되어 점검 중 쌓인 로그가 통째로 재판정되고,
        그때 나오는 경고 폭풍은 전부 이미 지나간 일이다.

        호출부: api/terminal_inspection_api.py의 worker() 마지막.
        점검 자체를 실패시키지 않아야 하므로 모든 예외를 삼키고 결과만 알린다.
        """
        try:
            loaded = self.load_realtime_baseline()
        except Exception as exc:
            return {"ok": False, "error": f"Baseline 갱신 실패: {exc}"}
        if loaded.get("error"):
            return {"ok": False, "error": loaded["error"]}

        store = self._baseline_store()
        gained = self._realtime_monitor().set_baseline_devices(store.device_names())
        watcher = getattr(self, "_baseline_stream_watcher", None)
        running = bool(watcher and watcher.is_running())

        if running:
            paths = self._active_profile_log_paths()
            if paths and "original" in paths:
                watcher.set_watch_dir(paths["original"])
                engine = getattr(self, "_baseline_diff_engine", None)
                if engine is not None:
                    engine.reset_context()
                
            self._push_baseline_refreshed({
                "devices": store.device_names(),
                "gained": gained,
                "source_kind": store.source_kind,
                "loaded": loaded.get("loaded", 0),
            })
        return {"ok": True, "running": running, "gained": gained,
                "source_kind": store.source_kind, "loaded": loaded.get("loaded", 0)}

    def _push_baseline_refreshed(self, payload):
        import json
        from api import window_ref
        try:
            window = window_ref.get_window()
        except RuntimeError:
            return
        try:
            window.evaluate_js("window.onRealtimeBaselineRefreshed && "
                               f"window.onRealtimeBaselineRefreshed({json.dumps(payload, ensure_ascii=False)})")
        except Exception as exc:
            print(f"[실시간 Diff] Baseline 갱신 push 실패: {exc}")

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
        monitor = self._realtime_monitor()
        monitor.reset(watched, store.device_names())
        # reset()은 필터를 건드리지 않지만, 사용자가 다른 창/YAML에서 고쳤을 수 있으므로
        # 감시를 새로 시작할 때 파일에서 다시 읽어 맞춘다.
        monitor.set_filter(self._normalized_realtime_filter())
        # reset()이 비운 자리에 이 프로파일의 지난 감시 결과를 다시 얹는다 — '감시 시작'이
        # 이전 회차에서 찾은 오류를 지우는 버튼이 되면 안 된다(그건 '초기화' 버튼의 일이다).
        restored = self._restore_realtime_state()

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
                "restored_devices": restored,
                "watch_dir": str(AppPaths.crt_log_root())}

    def stop_realtime_baseline_watch(self):
        watcher = getattr(self, "_baseline_stream_watcher", None)
        if watcher is not None:
            watcher.stop()
        engine = getattr(self, "_baseline_diff_engine", None)
        if engine is not None:
            engine.reset_context()
        # 감시를 멈추는 순간이 상태를 남길 마지막 기회다(프로그램 종료가 바로 뒤따르는 경우가 많다).
        self._save_realtime_state(force=True)
        return {"ok": True}

    def get_realtime_baseline_status(self):
        """UI 토글 상태 복원용 — 감시 여부/Baseline 장비/누적 경고 수/자동시작 설정."""
        watcher = getattr(self, "_baseline_stream_watcher", None)
        monitor = self._realtime_monitor()
        return {
            "running": bool(watcher and watcher.is_running()),
            "devices": self._baseline_store().device_names(),
            "watched": sorted(getattr(self, "_realtime_watch_targets", set())),
            # 화면의 '실시간 감시 중 (N)' 배지는 '지금 문제인 건수'여야 한다 —
            # 복구된 것과 숨긴 것을 세면 다 고쳐 놓고도 배지가 안 내려간다.
            "alert_count": len([a for a in monitor.alerts(limit=10000, include_hidden=False)
                                if not a.get("resolved")]),
            "resolved_count": len([a for a in monitor.alerts(limit=10000) if a.get("resolved")]),
            "autostart": self.get_realtime_watch_autostart().get("autostart", False),
            "watcher": watcher.status() if watcher else None,
            "watch_dir": str(AppPaths.crt_log_root()),
            # "mixed"면 Baseline에 수동 CRT 로그가 섞였다는 뜻 — 감시 신뢰도가 떨어지므로 알린다.
            "baseline_source_kind": self._baseline_store().source_kind,
        }

    def get_realtime_monitor_state(self, tail=120):
        """연결 탭 하단 3분할 패널 폴링용 — 장비별 실시간 로그 + 체크리스트 + 오류 분석."""
        watcher = getattr(self, "_baseline_stream_watcher", None)
        monitor = self._realtime_monitor()
        # 프로파일을 바꾼 뒤 이 탭을 열면 그 프로파일의 지난 감시 결과가 보여야 한다.
        self._sync_realtime_profile()
        state = monitor.state(tail=int(tail or 120))
        state["running"] = bool(watcher and watcher.is_running())
        # 감시가 도는데도 화면이 비어 있을 때 원인을 바로 보여준다 — 대개 파일-장비 매칭 실패다.
        status = watcher.status() if watcher else {}
        state["unmatched_files"] = status.get("unmatched", [])
        state["tracked_files"] = status.get("tracked_files", 0)
        # 장비별로 지금 열고 있는 로그 파일 + 세션 재접속으로 파일이 바뀐 이력.
        state["device_files"] = status.get("device_files", {})
        state["rollovers"] = status.get("rollovers", [])
        state["watch_dir"] = str(AppPaths.crt_log_root())
        state["ok"] = True
        return state

    def get_realtime_alerts(self, device=None, limit=100):
        """토스트 클릭 시 열리는 세부 Diff 모달용 — 최신순 경고 이력."""
        return {"ok": True, "alerts": self._realtime_monitor().alerts(device, int(limit or 100))}

    def clear_realtime_alerts(self):
        """경고 이력과 체크리스트 판정을 함께 비운다 — 화면의 '초기화'는 둘 다를 뜻한다."""
        self._realtime_monitor().clear_alerts()
        # 비운 결과도 즉시 저장한다 — 안 그러면 다음 실행에서 지웠던 경고가 되살아난다.
        self._save_realtime_state(force=True)
        return {"ok": True}

    def probe_realtime_log_files(self):
        """'왜 감시가 안 되나'를 화면에서 확인하기 위한 진단 — CRTlog의 각 파일이 어떤 근거로
        어느 장비에 매칭되는지 보여준다. 감시를 시작하지 않은 상태에서도 호출할 수 있다.

        감시는 장비 1대당 '가장 최근 로그 파일' 하나만 따라가므로(CRTStreamWatcher.latest_only),
        각 행에 지금 실제로 추적 중인지(tracked)와 그 장비의 최신 파일인지(latest)를 함께 넘긴다.
        감시가 아직 안 돌고 있으면 tracked는 전부 False이고, latest만으로 '감시를 시작하면
        어느 파일이 추적될지'를 미리 보여준다."""
        from engine.stream_device_matcher import StreamDeviceMatcher
        from core.crt_stream_watcher import DEFAULT_EXTENSIONS

        root = str(AppPaths.crt_log_root())
        matcher = StreamDeviceMatcher(self._realtime_inventory_targets())

        watcher = getattr(self, "_baseline_stream_watcher", None)
        status = watcher.status() if watcher else {}
        tracked = {os.path.normcase(os.path.abspath(p)) for p in (status.get("active_paths") or [])}

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
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                row = matcher.probe(path, head)
                row["size"] = size
                row["mtime"] = mtime
                row["mtime_str"] = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                row["tracked"] = os.path.normcase(os.path.abspath(path)) in tracked
                rows.append(row)

        # 같은 장비의 파일 중 mtime이 가장 큰 것 하나에만 latest=True — 감시 대상 선정 규칙과 같다.
        newest = {}
        for row in rows:
            device = row.get("resolved")
            if not device:
                continue
            current = newest.get(device)
            if current is None or row["mtime"] > current["mtime"]:
                newest[device] = row
        latest_paths = {id(row) for row in newest.values()}
        for row in rows:
            row["latest"] = id(row) in latest_paths

        rows.sort(key=lambda r: r["mtime"], reverse=True)
        return {"ok": True, "watch_dir": root, "known_devices": matcher.known_names(),
                "files": rows, "watching": bool(status.get("running")),
                "tracked_count": len(tracked)}

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

    # realtime_filter 기본 구조 — 키가 없어도 UI가 항상 같은 모양을 받게 한다.
    _FILTER_KEYS = ("hidden_rules", "hidden_devices", "hidden_keywords", "pinned_items")

    def _write_realtime_watch_config(self, updates):
        """부분 갱신 — 자동시작 토글이 화면 비율 설정을 날리지 않게 기존 값을 읽어 병합한다.

        realtime_filter는 한 단계 더 들어간 dict라 cfg.update()로는 통째로 덮인다.
        (우클릭 '규칙 숨기기'가 고정 항목을 날리는 버그가 되므로) 이 키만 하위 병합한다.
        """
        from core.atomic_io import dump_yaml_atomic
        cfg = self._read_realtime_watch_config()
        incoming_filter = updates.pop("realtime_filter", None)
        cfg.update(updates)
        if incoming_filter is not None:
            merged = dict(cfg.get("realtime_filter") or {})
            merged.update(incoming_filter)
            cfg["realtime_filter"] = merged
        path = self._realtime_watch_config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        dump_yaml_atomic(cfg, path)
        return cfg

    # ---------- 화면 필터 / 고정 항목 (Module 4) ----------
    def _normalized_realtime_filter(self, cfg=None):
        """YAML에서 읽은 realtime_filter를 UI/엔진이 쓰는 고정 형태로 정규화.

        YAML은 사람이 직접 고칠 수 있으므로 타입을 신뢰하지 않는다 — 문자열 하나만 적어도
        (hidden_devices: Access2) 리스트로 받아들인다. pinned_items는 순서가 화면 순서다.
        """
        raw = (cfg if cfg is not None else self._read_realtime_watch_config()).get("realtime_filter") or {}
        if not isinstance(raw, dict):
            raw = {}
        out = {}
        for key in ("hidden_rules", "hidden_devices", "hidden_keywords"):
            value = raw.get(key)
            if isinstance(value, str):
                value = [value]
            seen, items = set(), []
            for v in (value or []):
                s = str(v).strip()
                if s and s not in seen:
                    seen.add(s)
                    items.append(s)
            out[key] = items
        pinned, seen_pairs = [], set()
        value = raw.get("pinned_items")
        for item in (value or []):
            if not isinstance(item, dict):
                continue
            device = str(item.get("device") or "").strip()
            check_id = str(item.get("check_id") or "").strip()
            if not device or not check_id or (device, check_id) in seen_pairs:
                continue
            seen_pairs.add((device, check_id))
            pinned.append({"device": device, "check_id": check_id})
        out["pinned_items"] = pinned
        return out

    def get_realtime_filter(self):
        """실시간 감시 화면의 숨김/고정 설정. 엔진에도 즉시 적용한다 —
        프로그램을 켠 직후 첫 폴링에서도 저장된 숨김이 반영되어야 한다."""
        filter_cfg = self._normalized_realtime_filter()
        self._realtime_monitor().set_filter(filter_cfg)
        return {"ok": True, **filter_cfg}

    def save_realtime_filter(self, hidden_rules=None, hidden_devices=None,
                             hidden_keywords=None, pinned_items=None):
        """부분 갱신 — None으로 준 항목은 기존 값을 유지한다.
        빈 리스트([])는 '전부 해제'라는 뜻이므로 None과 구별해야 한다('숨김 모두 해제' 동작)."""
        current = self._normalized_realtime_filter()
        incoming = {"hidden_rules": hidden_rules, "hidden_devices": hidden_devices,
                    "hidden_keywords": hidden_keywords, "pinned_items": pinned_items}
        merged = {k: (current[k] if v is None else v) for k, v in incoming.items()}
        self._write_realtime_watch_config({"realtime_filter": merged})
        return self.get_realtime_filter()

    def toggle_realtime_filter_entry(self, kind, value, enabled=None):
        """우클릭 메뉴용 단건 토글 — 'Hide Rule' / 'Filter Out Device' / 키워드 숨기기.

        kind: 'rule' | 'device' | 'keyword'. enabled=None이면 있으면 빼고 없으면 넣는다.
        리스트 전체를 보내는 save_realtime_filter()와 따로 둔 이유: 우클릭은 항목 하나만
        아는데, 전체 리스트를 만들려면 프론트엔드가 서버 상태를 정확히 복제하고 있어야 한다.
        폴링 사이에 다른 곳에서 바뀌면 그 복제가 낡아 다른 항목을 되살린다.
        """
        key = {"rule": "hidden_rules", "device": "hidden_devices",
               "keyword": "hidden_keywords"}.get(kind)
        if not key:
            return {"error": f"알 수 없는 필터 종류: {kind}"}
        value = str(value or "").strip()
        if not value:
            return {"error": "숨길 대상이 비어 있습니다."}
        current = self._normalized_realtime_filter()
        items = list(current[key])
        want_on = (value not in items) if enabled is None else bool(enabled)
        if want_on and value not in items:
            items.append(value)
        elif not want_on and value in items:
            items.remove(value)
        return self.save_realtime_filter(**{key: items})

    def toggle_realtime_pin(self, device, check_id, pinned=None):
        """체크리스트 항목을 상단 '고정 카드'로 올리거나 내린다."""
        device, check_id = str(device or "").strip(), str(check_id or "").strip()
        if not device or not check_id:
            return {"error": "고정할 장비/점검항목이 비어 있습니다."}
        items = list(self._normalized_realtime_filter()["pinned_items"])
        exists = next((i for i in items if i["device"] == device and i["check_id"] == check_id), None)
        want_on = (exists is None) if pinned is None else bool(pinned)
        if want_on and exists is None:
            items.append({"device": device, "check_id": check_id})
        elif not want_on and exists is not None:
            items.remove(exists)
        return self.save_realtime_filter(pinned_items=items)

    def clear_realtime_filter(self):
        """'Unhide All' — 숨김만 전부 해제한다. 고정 항목은 숨김과 성격이 다르므로 남긴다."""
        return self.save_realtime_filter(hidden_rules=[], hidden_devices=[], hidden_keywords=[])

    def get_realtime_checklist_catalog(self):
        """설정 모달의 계층 체크박스 트리를 그리기 위한 목록 —
        장비(인벤토리) x 점검항목(CHECK_ITEMS) + 규칙 엔진의 서명 id들."""
        from engine.realtime_monitor import CHECK_ITEMS
        from engine.log_rule_engine import load_rules
        rules = load_rules()
        rule_ids = sorted({str(s.get("id")) for s in (rules.get("signatures") or []) if s.get("id")}
                          | {str(c.get("id")) for c in (rules.get("correlation_rules") or []) if c.get("id")}
                          | {str(s.get("id")) for s in (rules.get("suppressions") or []) if s.get("id")})
        return {
            "ok": True,
            "devices": [t.get("name") for t in self._realtime_inventory_targets() if t.get("name")],
            "checks": [{"key": key, "label": label} for key, label, _types in CHECK_ITEMS],
            "rule_ids": rule_ids,
            **self._normalized_realtime_filter(),
        }

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

        is_history=True는 '감시 시작 전에 이미 기록돼 있던 부분'이다. 프로그램을 켜기 전부터
        SecureCRT가 열려 있던 경우가 흔해서, 이 구간을 판정에서 빼면 이미 벌어진 설정 삭제·링크
        DOWN을 모른 채 '이상 없음'이라고 표시하게 된다 — 그래서 판정은 돌린다.
        다만 **토스트는 띄우지 않는다**: 어제 친 'no vlan 100'이 오늘 감시를 켠 순간 CRITICAL
        팝업으로 튀어나오면 알림을 신뢰할 수 없게 된다. 경고에는 history=True를 붙여 화면에서
        '지금 들어온 것'과 구분할 수 있게 한다.
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
        alerts = engine.analyze_stream(device, text)
        for alert in alerts:
            alert["source_file"] = os.path.basename(path)
            if is_history:
                alert["history"] = True
        if alerts:
            monitor.apply_alerts(alerts)

        # 복구 이벤트로 취소된 경고 — alerts보다 먼저 반영해야 한다. 같은 tick 안에서
        # '내렸다 올렸다'가 함께 들어오면(터미널 에코가 몰려 도착) 순서가 뒤바뀌면 안 된다.
        resolved = monitor.resolve_alerts(engine.drain_resolutions())

        # 숨긴 규칙/장비는 토스트도 띄우지 않는다 — 우클릭으로 숨긴 뒤에도 토스트가 계속
        # 뜨면 '숨김'이 동작하지 않는 것으로 읽힌다. 이력에는 그대로 남는다.
        visible = [] if is_history else [a for a in alerts if not monitor.is_hidden(a)]
        if visible:
            self._push_realtime_alerts(visible)
        if resolved:
            self._push_realtime_resolutions(resolved)

        # 판정이 바뀐 tick에서만 저장을 시도한다(내부에서 10초로 다시 묶는다) — 프로그램이
        # 비정상 종료돼도 잃는 것은 마지막 10초뿐이다.
        if alerts or resolved:
            self._save_realtime_state()

    def _push_realtime_alerts(self, alerts):
        self._push_js("onRealtimeDiffAlert", alerts)

    def _push_realtime_resolutions(self, resolutions):
        """Module 2 — 복구가 감지된 경고를 UI에서 지우거나 '해제'로 표시하게 한다.

        alert_id 하나당 한 번씩 부른다(요구된 시그니처가
        window.onRealtimeDiffAlertResolved(alert_id)이므로). 상세 정보는 두 번째 인자로
        같이 넘겨서, 수신부가 '무엇 때문에 해제됐는지'를 보여줄 수 있게 한다.
        """
        import json
        for res in resolutions:
            alert_id = res.get("alert_id")
            if not alert_id:
                continue
            self._push_js_raw(f"window.onRealtimeDiffAlertResolved && window.onRealtimeDiffAlertResolved("
                              f"{json.dumps(alert_id, ensure_ascii=False)}, "
                              f"{json.dumps(res, ensure_ascii=False)})")

    def _push_js(self, fn_name, payload):
        import json
        self._push_js_raw(f"window.{fn_name} && window.{fn_name}({json.dumps(payload, ensure_ascii=False)})")

    def _push_js_raw(self, script):
        from api import window_ref
        try:
            window = window_ref.get_window()
        except RuntimeError:
            return
        try:
            window.evaluate_js(script)
        except Exception as exc:
            print(f"[실시간 Diff] UI push 실패: {exc}")

    # ---------- 규칙기반 분석 ----------
    def start_log_analysis(self):
        """'Log Analysis' 탭 — '분석 실행' 버튼. 즉시 반환하고 백그라운드 스레드에서 진행되며,
        진행률은 get_analysis_jobs_status()['program']으로 폴링한다."""
        if self._jobs().is_running("program"):
            return {"error": "이미 규칙기반 분석이 진행 중입니다."}
        # problem/ 폴더에 결과를 쓰므로 create=True. 점검 이력(run)이 아예 없으면 분석할 원본도 없다.
        profile_paths = self._active_profile_log_paths(create=True)
        if not profile_paths:
            return {"error": "활성 프로파일이 없습니다."}
        if not glob.glob(os.path.join(profile_paths["original"], "*.txt")):
            return {"error": "분석할 점검 로그가 없습니다. 먼저 점검을 1회 수행하세요."}

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
        profile_paths = self._active_profile_log_paths(create=True)
        if not profile_paths:
            return {"error": "활성 프로파일이 없습니다."}
        original_dir = profile_paths["original"]
        if not glob.glob(os.path.join(original_dir, "*.txt")):
            return {"error": "분석할 점검 로그가 없습니다. 먼저 점검을 1회 수행하세요."}

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
