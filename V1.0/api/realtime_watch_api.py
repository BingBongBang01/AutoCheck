"""RealtimeWatchApiMixin — 연결 탭 '자동 실시간 감시'(SecureCRT 세션 로그 tail + Baseline 대조)의 화면 API.

상태와 로직은 engine/realtime_watch_service.py 의 RealtimeWatchService 에 있고, 이 클래스는 같은 이름의
공개 메서드로 위임만 한다(IMPROVEMENT_PLAN 3-5 — 예전엔 log_analysis_run_api.py 1,200줄 안에 분석 실행과
함께 섞여 있었다). 서비스가 Api 에서 빌려 쓰는 것(활성 프로파일·원본 로그 폴더·장비 목록·화면 push)은
_ApiHost 가 넘긴다 — 그래서 이 믹스인은 CustomerProfileApiMixin(_resolve_active_customer_profile_names),
LogFileBrowserApiMixin(_active_profile_log_paths), 터미널 API(get_terminal_targets/_resolve_terminal_targets)와
함께 Api 에 조합돼야 한다.

결과가 화면으로 가는 길은 둘이다:
  1) 토스트: evaluate_js 로 즉시 push(놓치면 안 되는 알림이므로 push)
  2) 연결 탭 하단 3분할 패널: RealtimeMonitor 에 상태를 쌓고 화면이 get_realtime_monitor_state()로 폴링
     (0.3초마다 화면 전체를 push 하면 evaluate_js 호출이 과도해지고 대량 로그에서 UI가 밀린다)
"""
import threading

from core.paths import AppPaths

_SERVICE_LOCK = threading.Lock()   # 서비스를 처음 만들 때 — 자동 시작 스레드와 화면 폴링이 겹친다


def flush_realtime_state(api):
    """보고서를 만들기 직전에 — 실시간 감시가 아직 묶어 둔(10초) 변경을 프로파일 저장본에 남긴다. 보고서의 '실시간
    감시 이력' 부록(engine/realtime_audit)이 그 저장본을 읽기 때문이다. 감시를 쓰지 않았으면 아무것도 하지 않는다
    (서비스를 새로 만들지 않는다). 저장이 실패해도 보고서는 만든다."""
    service = getattr(api, "__dict__", {}).get("_realtime_service")
    if service is None:
        return False
    try:
        return service.flush_state()
    except Exception as exc:
        print(f"[실시간 Diff] 보고서 전에 감시 상태를 저장하지 못했습니다: {exc}")
        return False


class _ApiHost:
    """RealtimeWatchService 가 Api 에서 빌려 쓰는 것."""

    def __init__(self, api):
        self._api = api

    def active_names(self):
        return self._api._resolve_active_customer_profile_names()

    def profile_log_paths(self):
        return self._api._active_profile_log_paths()

    def inventory_targets(self):
        return self._api.get_terminal_targets()

    def connect_targets(self):
        return self._api._resolve_terminal_targets()

    def push_js(self, fn_name, *args):
        self._api._push_js(fn_name, *args)


class RealtimeWatchApiMixin:
    def _realtime(self):
        """이 Api 의 실시간 감시 서비스 — 처음 쓸 때 만든다."""
        service = self.__dict__.get("_realtime_service")
        if service is None:
            with _SERVICE_LOCK:
                service = self.__dict__.get("_realtime_service")
                if service is None:
                    from engine.realtime_watch_service import RealtimeWatchService
                    service = RealtimeWatchService(_ApiHost(self))
                    self._realtime_service = service
        return service

    # ---------- 화면 push ----------
    def _push_js(self, fn_name, *args):
        import json
        params = ", ".join(json.dumps(arg, ensure_ascii=False) for arg in args)
        self._push_js_raw(f"window.{fn_name} && window.{fn_name}({params})")

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

    # ---------- 다른 믹스인·main.py 가 부르는 훅 ----------
    def _load_realtime_baseline(self):
        return self._realtime().load_baseline()

    def _refresh_realtime_baseline_after_inspection(self):
        return self._realtime().refresh_baseline_after_inspection()

    def _flush_realtime_state(self):
        return self._realtime().flush_state()

    def _notify_active_profile_moving(self, before):
        return self._realtime().notify_profile_moving(before)

    def _notify_active_profile_changed(self):
        return self._realtime().notify_profile_changed()

    def _get_realtime_watch_autostart(self):
        """프로그램 실행 시 실시간 감시를 자동으로 켤지 여부."""
        return {"autostart": self._realtime().config.autostart()}

    def _autostart_realtime_baseline_watch(self):
        return self._realtime().autostart_watch()

    # ---------- 감시 ----------
    def start_realtime_baseline_watch(self, interval=0.3, device_names=None):
        return self._realtime().start_watch(interval=interval, device_names=device_names)

    def stop_realtime_baseline_watch(self):
        return self._realtime().stop_watch()

    def get_realtime_state_poll(self):
        return self._realtime().state_poll_settings()

    def set_realtime_state_poll(self, enabled=None, interval_sec=None):
        return self._realtime().set_state_poll(enabled=enabled, interval_sec=interval_sec)

    def get_realtime_baseline_status(self):
        return self._realtime().baseline_status()

    def get_realtime_monitor_state(self, tail=120, since=None):
        return self._realtime().monitor_state(tail=tail, since=since)

    def get_realtime_watch_health(self):
        """'감시 건강' 창 — 장비별 세션 로그·syslog·명령·상태 폴링의 마지막 시각과 문제 목록."""
        return {"ok": True, **self._realtime().watch_health(detail=True)}

    def get_realtime_alerts(self, device=None, limit=100):
        """토스트 클릭 시 열리는 세부 Diff 모달용 — 최신순 경고 이력."""
        return {"ok": True, "alerts": self._realtime().monitor().alerts(device, int(limit or 100))}

    def clear_realtime_alerts(self):
        return self._realtime().clear_alerts()

    def resolve_realtime_finding(self, alert_ids, note=""):
        return self._realtime().resolve_findings(alert_ids, note)

    def ignore_realtime_finding(self, alert_ids, note=""):
        return self._realtime().ignore_findings(alert_ids, note)

    # ---------- 감시 로그 파일(진단 모달) ----------
    def probe_realtime_log_files(self):
        return self._realtime().probe_log_files()

    def open_realtime_log_folder(self):
        """'CRT 로그 파일 진단' 모달의 '폴더 열기' — CRTlog 폴더를 OS 탐색기로 연다."""
        from engine import log_storage
        root = str(AppPaths.crt_log_root())
        log_storage.open_in_file_explorer(root)
        return {"ok": True, "path": root}

    def delete_realtime_log_files(self, filenames):
        return self._realtime().delete_log_files(filenames)

    # ---------- 화면 필터·고정·설정 ----------
    def get_realtime_filter(self):
        return self._realtime().get_filter()

    def save_realtime_filter(self, hidden_rules=None, hidden_devices=None,
                             hidden_keywords=None, pinned_items=None):
        return self._realtime().save_filter(hidden_rules=hidden_rules, hidden_devices=hidden_devices,
                                            hidden_keywords=hidden_keywords, pinned_items=pinned_items)

    def toggle_realtime_filter_entry(self, kind, value, enabled=None):
        return self._realtime().toggle_filter_entry(kind, value, enabled)

    def toggle_realtime_pin(self, device, check_id, pinned=None):
        return self._realtime().toggle_pin(device, check_id, pinned)

    def clear_realtime_filter(self):
        """'Unhide All' — 숨김만 전부 해제한다. 고정 항목은 숨김과 성격이 다르므로 남긴다."""
        return self._realtime().save_filter(hidden_rules=[], hidden_devices=[], hidden_keywords=[])

    def get_realtime_checklist_catalog(self):
        return self._realtime().checklist_catalog()

    def set_realtime_watch_autostart(self, enabled):
        self._realtime().config.update({"autostart": bool(enabled)})
        return {"ok": True, "autostart": bool(enabled)}

    def get_realtime_watch_layout(self):
        """실시간 감시 화면의 구분선 비율·보기 모드·선택 장비.

        localStorage가 아니라 파일에 두는 이유: pywebview는 file:// 오리진으로 UI를 띄우는데
        WebView 구현/정책에 따라 file:// localStorage가 비활성이거나 세션마다 초기화된다.
        '재실행해도 비율이 유지된다'를 보장하려면 앱이 직접 저장해야 한다.
        """
        return self._realtime().config.layout()

    def save_realtime_watch_layout(self, split_ratio=None, right_ratio=None,
                                    view_mode=None, selected_devices=None):
        return self._realtime().save_layout(split_ratio=split_ratio, right_ratio=right_ratio,
                                            view_mode=view_mode, selected_devices=selected_devices)
