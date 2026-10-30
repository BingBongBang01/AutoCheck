"""RealtimeWatchService — 실시간 감시(SecureCRT 세션 로그 tail + Baseline 대조 + 능동 상태 폴링)의 상태와 로직.

예전엔 api/log_analysis_run_api.py(1,200줄)의 믹스인 메서드들이 Api 객체에 getattr(self, "_x", 기본값)
속성을 흩뿌려 상태를 들고 있었고, 분석 실행·감시·상태 보존·상태 폴링·감시 로그 파일·화면 설정 여섯 가지
관심사가 한 파일에 섞여 있었다(IMPROVEMENT_PLAN 3-5). 이제 감시 상태는 이 객체 하나에 있고, 생성자에서
모두 정해진다. api/realtime_watch_api.py 는 같은 이름의 공개 메서드로 여기에 위임만 한다.

Api 에서 빌려 쓰는 것은 host 하나로 받는다(활성 프로파일 이름, 원본 로그 폴더, 장비 목록, 화면 push):
    host.active_names() -> (고객사, 프로파일)
    host.profile_log_paths() -> {"original": ...} | None
    host.inventory_targets() -> [{name, ip, port}]        화면용(비밀번호 없음)
    host.connect_targets() -> [{name, ip, ..., password}]  상태 폴링 접속용
    host.push_js(fn_name, *args)                           window.<fn_name>(*args) 호출

동시성 — 판정 파이프라인(분석 -> 반영 -> 해제)은 pipeline_lock 하나로 묶는다. 감시 스레드와 상태 폴링
스레드가 같은 엔진·모니터를 쓰기 때문이다(잠금이 없으면 한쪽이 drain 한 해제가 다른 쪽 경고보다 먼저
반영돼 유실되거나, 반쯤 반영된 상태가 화면에 보였다). 예전에 '플래그'로 막던 두 곳은 잠금으로 바꿨다:
  - 프로파일 전환 재진입(전환 중 다시 전환): _switch_lock 을 기다리지 않고 잡아 본다.
  - 상태 저장과 프로파일 폴더 이동: _state_lock — 저장 중에 폴더 이동이 시작되거나, 이동 표시를 보기
    직전에 저장이 끼어드는 일이 없다(예전엔 '이동 중' 플래그를 본 뒤 쓰는 사이가 비어 있었다).
화면 설정(realtime_watch.yaml)의 읽기-수정-쓰기는 RealtimeWatchConfig 의 잠금이 묶는다.
"""
import datetime
import os
import threading
import time

from core.paths import AppPaths

_LAYOUT_DEFAULTS = {"split_ratio": 0.52, "right_ratio": 0.45, "view_mode": "split"}
# 너무 짧으면 장비에 부담이고, 너무 길면 감시라고 부를 수 없다. 기본은 꺼짐 — 주기적으로 운영 장비에 SSH
# 접속을 만드는 외부 동작이므로 사용자가 켠다.
_STATE_POLL_DEFAULTS = {"enabled": False, "interval_sec": 60}
_STATE_SAVE_INTERVAL = 10       # 감시 상태 파일 저장 간격(초) — 0.3초 tick 마다 쓰면 디스크가 갈린다
_PROFILE_CHECK_INTERVAL = 3     # 패널 폴링(0.8초)마다 활성 프로파일 경로를 다시 읽지 않는다
# 따라가는 세션 로그가 이만큼 늘지 않으면 감시 건강에 '로그 기록 멈춤'으로 알린다. 쉬는 세션도 늘지 않으므로
# 끊겼다고 단정하지 않고 중립색으로만 보인다 — 짧게 잡으면 점검 중 잠깐 다른 장비를 보는 사이에도 뜬다.
LOG_STALE_SEC = 20 * 60
# 감시 건강의 문제 종류와 수준 — 화면에 이 순서(심각한 것부터)로 보인다.
_HEALTH_ISSUES = (
    ("poll_auth", "bad"),       # 상태 폴링 인증 실패 — 계정·비밀번호를 고쳐야 한다
    ("host_key", "bad"),        # 호스트키가 바뀌어 폴링이 로그인하지 않았다(4-6)
    ("poll_connect", "warn"),   # 상태 폴링 접속 실패
    ("no_log", "warn"),         # 감시 대상인데 따라갈 세션 로그가 없다
    ("log_stopped", "info"),    # 세션 로그가 LOG_STALE_SEC 넘게 늘지 않았다
)


def rules_status():
    from engine.log_rule_engine import get_engine, rules_status as _status
    get_engine()                       # 규칙 파일이 바뀌었으면 여기서 다시 읽고 상태를 갱신한다
    return _status()


def rules_warning():
    """규칙 파일을 읽지 못했을 때 화면에 띄울 문장. 문제가 없으면 None."""
    status = rules_status()
    if status.get("ok"):
        return None
    return f"규칙 파일(log_rules.json)을 읽지 못해 기본 키워드만으로 판정합니다 — {status.get('error')}"


def _fingerprint(value):
    """폴링 델타에서 섹션이 바뀌었는지 볼 짧은 지문(engine/realtime_monitor 의 섹션 지문과 같은 방식)."""
    import hashlib
    import json
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(encoded.encode("utf-8")).hexdigest()[:16]


def clamp_ratio(value, fallback):
    """구분선 비율은 0.18~0.82로 제한 — 한쪽 패널이 0폭이 되어 다시 드래그할 수 없게 되는 것을 막는다."""
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return fallback
    return min(0.82, max(0.18, ratio))


def _found_in_subdir(root, name):
    """이 파일명이 감시 폴더의 **하위** 폴더에 있는지(최상위에는 없을 때만 묻는다)."""
    from core.crt_stream_watcher import iter_session_log_files
    return any(os.path.basename(p) == name and os.path.dirname(p) != root
               for p in iter_session_log_files(root))


class RealtimeWatchConfig:
    """앱 전역 감시 화면 설정(config/realtime_watch.yaml) — 자동 시작, 상태 폴링, 화면 비율, 숨김·고정 필터.

    읽기-수정-쓰기는 잠금 하나로 묶는다. 예전엔 잠금 없이 읽고 합쳐 썼기 때문에, 우클릭 숨기기와 화면 비율
    저장처럼 거의 동시에 들어온 두 변경 중 하나가 사라질 수 있었다."""

    # realtime_filter 기본 구조 — 키가 없어도 UI가 항상 같은 모양을 받게 한다.
    FILTER_KEYS = ("hidden_rules", "hidden_devices", "hidden_keywords", "pinned_items")

    def __init__(self, path=None):
        self._path = path
        self._lock = threading.RLock()

    @property
    def path(self):
        return str(self._path or (AppPaths.config_root() / "realtime_watch.yaml"))

    def read(self):
        import yaml
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            return {}

    @staticmethod
    def filter_key():
        """숨김·고정 필터를 가르는 키 — 활성 프로파일(프로젝트) id. 이름이 아니라 id 라서 이름을 바꿔도 따라간다."""
        try:
            from engine import project_manager as pm
            return pm.get_active_project() or None
        except Exception:
            return None

    def update(self, updates):
        """부분 갱신 — 자동시작 토글이 화면 비율 설정을 날리지 않게 기존 값을 읽어 병합한다.

        realtime_filter는 한 단계 더 들어간 dict라 cfg.update()로는 통째로 덮인다.
        (우클릭 '규칙 숨기기'가 고정 항목을 날리는 버그가 되므로) 이 키만 하위 병합한다.
        """
        from core.atomic_io import dump_yaml_atomic
        updates = dict(updates)
        with self._lock:
            cfg = self.read()
            incoming_filter = updates.pop("realtime_filter", None)
            cfg.update(updates)
            if incoming_filter is not None:
                key = self.filter_key()
                if key:
                    # 프로파일(프로젝트 id)별로 저장한다 — 예전엔 하나뿐이라 고객사 A 에서 숨긴 장비·규칙이
                    # B 에서도 숨겨졌다. 처음 저장할 때는 예전 공용 값에서 시작한다.
                    filters = dict(cfg.get("realtime_filters") or {})
                    merged = dict(filters.get(key) or cfg.get("realtime_filter") or {})
                    merged.update(incoming_filter)
                    filters[key] = merged
                    cfg["realtime_filters"] = filters
                else:
                    merged = dict(cfg.get("realtime_filter") or {})
                    merged.update(incoming_filter)
                    cfg["realtime_filter"] = merged
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            dump_yaml_atomic(cfg, self.path)
            return cfg

    def normalized_filter(self, cfg=None):
        """YAML에서 읽은 realtime_filter를 UI/엔진이 쓰는 고정 형태로 정규화.

        YAML은 사람이 직접 고칠 수 있으므로 타입을 신뢰하지 않는다 — 문자열 하나만 적어도
        (hidden_devices: Access2) 리스트로 받아들인다. pinned_items는 순서가 화면 순서다.
        """
        cfg = cfg if cfg is not None else self.read()
        filters = cfg.get("realtime_filters") if isinstance(cfg.get("realtime_filters"), dict) else {}
        key = self.filter_key()
        # 이 프로파일의 필터가 아직 없으면 예전 공용 값(realtime_filter)을 쓴다.
        raw = (filters.get(key) if key and key in filters else cfg.get("realtime_filter")) or {}
        if not isinstance(raw, dict):
            raw = {}
        out = {}
        for name in ("hidden_rules", "hidden_devices", "hidden_keywords"):
            value = raw.get(name)
            if isinstance(value, str):
                value = [value]
            seen, items = set(), []
            for v in (value or []):
                s = str(v).strip()
                if s and s not in seen:
                    seen.add(s)
                    items.append(s)
            out[name] = items
        pinned, seen_pairs = [], set()
        for item in (raw.get("pinned_items") or []):
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

    def edit_filter(self, change):
        """지금 필터를 읽어 change(필터) -> 바꿀 키 dict 를 적용하고 저장한다 — 읽기부터 쓰기까지 한 잠금 안에서.
        우클릭 토글 두 개가 같은 목록을 따로 읽고 따로 쓰면 하나가 사라진다."""
        with self._lock:
            changed = change(self.normalized_filter())
            if changed:
                self.update({"realtime_filter": changed})
            return self.normalized_filter()

    def state_poll(self):
        """{enabled, interval_sec} — 주기는 15~900초로 제한한다."""
        cfg = self.read().get("state_poll") or {}
        out = dict(_STATE_POLL_DEFAULTS)
        if isinstance(cfg, dict):
            out["enabled"] = bool(cfg.get("enabled", out["enabled"]))
            try:
                out["interval_sec"] = min(900, max(15, int(cfg.get("interval_sec") or out["interval_sec"])))
            except (TypeError, ValueError):
                pass
        return out

    def autostart(self):
        return bool(self.read().get("autostart", False))

    def layout(self):
        cfg = self.read()
        layout = dict(_LAYOUT_DEFAULTS)
        for key in layout:
            if cfg.get(key) is not None:
                layout[key] = cfg[key]
        layout["split_ratio"] = clamp_ratio(layout["split_ratio"], _LAYOUT_DEFAULTS["split_ratio"])
        layout["right_ratio"] = clamp_ratio(layout["right_ratio"], _LAYOUT_DEFAULTS["right_ratio"])
        if layout["view_mode"] not in ("tabs", "split"):
            layout["view_mode"] = "split"
        layout["selected_devices"] = [str(n) for n in (cfg.get("selected_devices") or [])]
        return layout


class RealtimeWatchService:
    """실시간 감시 한 벌 — Api 하나에 하나(api/realtime_watch_api.py 가 처음 쓸 때 만든다)."""

    def __init__(self, host, config=None):
        self._host = host
        self.config = config or RealtimeWatchConfig()
        self.pipeline_lock = threading.RLock()
        self._init_lock = threading.Lock()     # 구성 요소를 처음 만들 때 — 자동 시작 스레드와 화면 폴링이 겹친다
        self._switch_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._store = None
        self._monitor = None
        self.watcher = None              # CRTStreamWatcher — 감시 중일 때
        self.diff_engine = None          # BaselineDiffEngine
        self.rule_stream = None          # RealtimeRuleStream
        self.device_matcher = None       # StreamDeviceMatcher
        self.state_poller = None         # StatePoller(두 번째 입력원)
        self.watch_targets = set()       # 감시 대상 장비명(소문자)
        self.generation = 0              # 시작·중지·프로파일 전환 때 올린다 — 이전 세대의 늦은 결과를 버린다
        self._state_key_path = None      # 감시 상태를 저장할 파일(지금 프로파일의 realtime_state.json)
        self._state_saved_at = 0.0
        self._state_dirty = False
        self._state_moving = False       # 활성 프로파일 폴더가 옮겨지는 중
        self._flush_timer = None
        self._profile_checked_at = 0.0

    # ---------- 구성 요소 -----------------------------------------------------------------

    def baseline_store(self):
        if self._store is None:
            with self._init_lock:
                if self._store is None:
                    from engine.baseline_store import BaselineStore
                    self._store = BaselineStore()
        return self._store

    def monitor(self):
        if self._monitor is None:
            with self._init_lock:
                if self._monitor is None:
                    from engine.realtime_monitor import RealtimeMonitor
                    monitor = RealtimeMonitor()
                    # 저장된 숨김/고정을 생성 즉시 넣는다 — 프로그램을 켠 직후 첫 폴링부터 반영되어야
                    # 하고, 자동시작(autostart_watch)은 UI보다 먼저 돌기 때문에
                    # 프론트엔드가 get_realtime_filter()를 부를 때까지 기다릴 수 없다.
                    try:
                        monitor.set_filter(self.config.normalized_filter())
                    except Exception as exc:
                        print(f"[실시간 Diff] 필터 초기 적용 실패: {exc}")
                    # 프로그램을 껐다 켜도 이 프로파일에서 찾았던 오류가 그대로 보여야 한다 —
                    # 감시를 시작하기 전(첫 폴링)부터 복원해 둔다. 다 채운 뒤에 내놓는다 — 다른
                    # 스레드가 반쯤 만든 모니터를 받지 않게.
                    self.restore_state(monitor)
                    self._monitor = monitor
        return self._monitor

    def is_running(self):
        return bool(self.watcher is not None and self.watcher.is_running())

    # ---------- 프로파일별 실시간 감시 상태 보존 ----------
    # 저장 위치는 프로파일 폴더다. 고객사/회차마다 장비도 Baseline도 다르므로 상태를 공유하면
    # 다른 회차의 경고가 섞인다. 프로파일이 바뀌면 쓰던 것을 저장하고 새 프로파일 것을 읽는다.

    def state_path(self, customer=None, profile=None):
        from engine import log_storage
        if customer is None or profile is None:
            customer, profile = self._host.active_names()
        if not customer or not profile:
            return None
        return os.path.join(log_storage.get_profile_dir(customer, profile), "realtime_state.json")

    def save_state(self, force=False):
        """감시 상태를 파일로 남긴다. 0.3초 tick마다 쓰면 디스크가 갈리므로 10초로 묶는다.

        묶인 요청은 버리지 않는다 — '저장할 것 있음'으로 표시해 두고 10초가 지나면 저장한다(후행 저장).
        예전엔 그냥 버려서, 경고 직후 자동 복구된 상태가 다음 판정 변화가 올 때까지 저장되지 않았다.
        그 사이 프로그램을 닫으면 재실행 때 이미 복구된 CRITICAL 이 미해결로 되살아났다."""
        monitor = self._monitor
        if monitor is None:
            return False
        with self._state_lock:
            if self._state_moving:
                return False   # 활성 프로파일 폴더가 옮겨지는 중 — notify_profile_moving 참고
            now = time.time()
            wait = _STATE_SAVE_INTERVAL - (now - self._state_saved_at)
            if not force and wait > 0:
                self._state_dirty = True
                self._schedule_flush(wait)
                return False
            path = self._state_key_path or self.state_path()
            if not path:
                return False
            from engine import realtime_monitor as rtm
            self._state_saved_at = now
            self._state_dirty = False
            return rtm.save_snapshot(path, monitor.snapshot())

    def _schedule_flush(self, delay):
        timer = self._flush_timer
        if timer is not None and timer.is_alive():
            return
        timer = threading.Timer(max(delay, 0.05), self.flush_state)
        timer.daemon = True
        self._flush_timer = timer
        timer.start()

    def flush_state(self):
        """저장할 것이 남아 있으면 지금 저장한다 — 후행 저장 타이머와 창 닫기(main.py)가 부른다."""
        if self._state_dirty:
            return self.save_state(force=True)
        return False

    def restore_state(self, monitor=None):
        """지금 프로파일의 저장본을 monitor(기본: 지금 모니터)에 얹는다. 없으면 아무 일도 하지 않는다."""
        monitor = monitor or self._monitor
        if monitor is None:
            return 0
        path = self.state_path()
        with self._state_lock:
            self._state_key_path = path
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

    def notify_profile_moving(self, before):
        """활성 프로파일(또는 그 고객사)의 data 폴더 이름이 바뀌기 직전(before=True)·직후(False).

        직전: 지금 감시 상태를 저장해 폴더와 함께 옮겨지게 하고, 옮기는 동안은 저장을 멈춘다.
        직후: 저장 위치만 새 경로로 바꾼다. 그냥 두면 다음 동기화가 '프로파일이 바뀌었다'고 보고
        옛 경로에 저장해서 옛 이름의 폴더가 다시 생기고, 최신 감시 내역이 그 고아 폴더로 갔다.
        저장과 '이동 중' 표시는 같은 잠금 안에서 바뀐다 — 그 사이에 다른 스레드의 저장이 끼어들지 못한다."""
        with self._state_lock:
            if before:
                self.save_state(force=True)
                self._state_moving = True
                return
            self._state_moving = False
            if self._monitor is not None:
                self._state_key_path = self.state_path()

    def notify_profile_changed(self):
        """프로파일 전환 직후 호출되는 훅(api/project_core_api.py의 _activated_profile).

        폴링 경로에도 같은 동기화가 있지만 3초로 묶여 있어서, 전환 직후 프론트엔드가 화면을
        다시 그리는 시점에는 아직 이전 프로파일의 감시 내역이 남아 있다. 그 순간이 사용자가
        '프로파일을 바꿨는데 이전 내역이 그대로'라고 읽는 지점이므로 여기서 즉시 갈아끼운다.
        """
        # 아직 감시를 한 번도 쓰지 않았으면 갈아끼울 상태가 없다 — 다음 첫 접근이 새 프로파일
        # 저장본을 읽는다(monitor()가 생성 직후 restore_state()를 부른다).
        if self._monitor is None:
            self._profile_checked_at = 0
            return False
        return self.sync_profile(force=True)

    def sync_profile(self, force=False):
        """활성 프로파일이 바뀌었으면 쓰던 상태를 저장하고 새 프로파일 상태로 갈아끼운다.

        패널 폴링(0.8초)마다 불리므로, 프로파일 경로를 알아내는 파일 읽기는 3초로 묶는다.
        force=True는 프로파일을 실제로 바꾼 직후 즉시 갈아끼우기 위한 것이다.
        """
        now = time.time()
        if not force and now - self._profile_checked_at < _PROFILE_CHECK_INTERVAL:
            return False
        # 아래에서 start_watch()를 다시 부르는데, 그 경로가 다시 이 함수로 돌아오거나 다른 스레드(패널
        # 폴링·프로파일 전환 훅)가 동시에 들어오면 감시를 겹쳐 재시작한다 — 이미 전환 중이면 물러난다.
        if not self._switch_lock.acquire(blocking=False):
            return False
        try:
            self._profile_checked_at = now
            path = self.state_path()
            if path == self._state_key_path:
                return False
            # 이전 프로파일의 감시 내역을 그 프로파일 폴더에 남긴다 — key_path가 아직 이전 것이다.
            # 그 전에 세대를 올려, 지금 도는 감시·폴링이 늦게 내는 결과가 새 프로파일에 섞이지 않게 한다.
            self.bump_generation()
            with self.pipeline_lock:
                self.save_state(force=True)
            running = self.is_running()
            interval = getattr(self.watcher, "interval", 0.3) if running else 0.3
            if running:
                # 감시 중에도 프로파일을 따라간다. 예전에는 여기서 그냥 물러났는데(tail 중인
                # 판정 기준과 화면이 어긋나는 것을 피하려고), 자동시작을 켜 두면 감시가 항상
                # 돌고 있어서 프로파일을 바꿔도 이전 프로파일의 감시 내역이 화면에 그대로
                # 남았고, 새로 들어온 경고까지 이전 프로파일 파일에 쌓였다.
                # 기준(Baseline)·감시 대상 장비·저장 위치를 함께 갈아야 하므로 감시를 멈추고
                # 새 프로파일 기준으로 다시 시작한다.
                self.stop_watch()
            monitor = self._monitor
            if monitor is not None:
                monitor.reset([], ())
            # 저장 위치(_state_key_path)를 새 프로파일로 먼저 옮긴다 — 아래 재시작이
            # 실패해도 새 프로파일의 감시 내역이 이전 프로파일 파일로 새지 않게 한다.
            self.restore_state()
            if monitor is not None:
                monitor.set_filter(self.config.normalized_filter())   # 필터도 프로파일별이다
            if running:
                # 감시 대상 장비는 넘기지 않는다 — 새 프로파일의 장비 목록에서 다시 고른다
                # (프로파일마다 장비가 다르므로 이전 프로파일에서 체크한 이름은 의미가 없다).
                self.start_watch(interval=interval)
            return True
        finally:
            self._switch_lock.release()

    # ---------- Baseline ---------------------------------------------------------------

    def load_baseline(self):
        """활성 프로파일의 원본 점검 로그를 읽어 Baseline 스냅샷을 메모리에 로드.

        수동 CRT 세션 로그는 기준에서 제외된다(engine/baseline_store.py의 load_baseline 주석
        참고) — 감시 중인 세션이 자기 자신의 Baseline이 되면 감시가 조용히 무력화된다.
        """
        paths = self._host.profile_log_paths()
        if not paths:
            return {"error": "점검 이력이 없습니다. 먼저 점검을 1회 수행하세요."}
        customer, profile = self._host.active_names()
        store = self.baseline_store()
        result = store.load_baseline(customer, profile, original_dir=paths["original"])
        if result.get("loaded", 0) == 0:
            return {"error": "사전 점검 로그가 없습니다. 먼저 점검을 1회 수행하세요."}
        result["summary"] = store.summary()
        return result

    def refresh_baseline_after_inspection(self):
        """점검(run_terminal_inspection)이 원본 폴더에 결과를 다 쓴 직후 호출된다.

        감시를 멈추지 않는다 — BaselineDiffEngine은 BaselineStore '객체'를 들고 있으므로
        스냅샷을 갈아끼우면 다음 줄부터 새 기준으로 대조한다. 감시 스레드를 재시작하면
        CRTStreamWatcher의 파일 오프셋이 초기화되어 점검 중 쌓인 로그가 통째로 재판정되고,
        그때 나오는 경고 폭풍은 전부 이미 지나간 일이다.

        호출부: api/terminal_inspection_api.py의 worker() 마지막.
        점검 자체를 실패시키지 않아야 하므로 모든 예외를 삼키고 결과만 알린다.
        """
        try:
            loaded = self.load_baseline()
        except Exception as exc:
            return {"ok": False, "error": f"Baseline 갱신 실패: {exc}"}
        if loaded.get("error"):
            return {"ok": False, "error": loaded["error"]}

        store = self.baseline_store()
        gained = self.monitor().set_baseline_devices(store.device_names())
        running = self.is_running()

        if running:
            # 감시 폴더(CRTlog)와 판정 문맥은 여기서 건드리지 않는다.
            #
            # 예전에는 watcher.set_watch_dir(paths["original"]) 로 감시 대상을 점검 결과
            # 폴더(runs/<run>/raw)로 바꿨고, 되돌리는 코드가 없었다. 그래서 점검을 한 번
            # 하면 작업자의 SecureCRT 세션 감시가 앱 재시작 때까지 영구히 멈췄고, 대신
            # 점검 출력이 '지금 들어온 입력'으로 재판정됐다 — `show reload cause` 의 출력
            # 헤더 'Reload Cause:' 가 CRITICAL '위험 명령 실행'으로 잡힌 실제 사례가 있다
            # (data/*/realtime_state.json 에 그 경고 1건만 남아 있었다).
            # 점검은 별도 SSH 세션이라 CRT 세션 로그에 아무것도 쓰지 않으므로, 스트림은
            # 끊기지 않았고 갈아끼울 것은 Baseline 스냅샷뿐이다(BaselineDiffEngine 이
            # store 객체를 들고 있으므로 다음 줄부터 새 기준으로 대조된다).
            #
            # reset_context() 도 함께 뺐다 — 그건 StateTracker 까지 비우므로, 점검이
            # 끝나는 순간 열려 있던 장애들이 '취소 불가' 상태가 된다(작업자가 나중에
            # no shutdown 을 쳐도 해제되지 않는다).
            self._push("onRealtimeBaselineRefreshed", {
                "devices": store.device_names(),
                "gained": gained,
                "source_kind": store.source_kind,
                "loaded": loaded.get("loaded", 0),
            })
        return {"ok": True, "running": running, "gained": gained,
                "source_kind": store.source_kind, "loaded": loaded.get("loaded", 0)}

    def started_within(self, seconds):
        """감시가 돌고 있고, 시작한 지 seconds 초 안인가 — 점검 직후 자동 로그 분석을 패널에 얹을지 정할 때."""
        if not self.is_running():
            return False
        started_at = getattr(self.monitor(), "_started_at", 0) or 0
        return time.time() - started_at <= seconds

    def apply_external_alerts(self, alerts):
        """감시 밖에서 만든 경고(점검 직후의 규칙 분석)를 패널에 얹고, 숨기지 않은 것은 토스트로 알린다."""
        if not alerts:
            return 0
        monitor = self.monitor()
        monitor.apply_alerts(alerts)
        visible = [a for a in alerts if not monitor.is_hidden(a)]
        if visible:
            # 점검 직후 분석 결과는 화면 앞의 작업자가 방금 돌린 것이다 — 외부 알림 채널로는 보내지 않는다.
            self.push_alerts(visible, notify=False)
        return len(alerts)

    # ---------- 감시 시작/중지 ------------------------------------------------------------

    def watch_dir(self):
        """화면에 '무엇을 감시하고 있다'고 말할 때 쓸 폴더 — 감시가 돌고 있으면 **실측값**.

        예전에는 이 자리에 늘 AppPaths.crt_log_root() 상수를 넣었다. 그래서 감시 폴더가
        엉뚱한 곳으로 바뀌어도(그런 버그가 있었다) 화면은 계속 CRTlog 를 가리켰고, 진단
        모달의 '추적 중 0건'이 원인을 설명하지 못했다. 불일치는 화면이 드러내야 한다.
        """
        if self.watcher is not None and getattr(self.watcher, "watch_dir", None):
            return str(self.watcher.watch_dir)
        return str(AppPaths.crt_log_root())

    def inventory_targets(self):
        """장비 목록(Device Inventory)의 활성 장비 [{name, ip, port}...]."""
        try:
            return self._host.inventory_targets() or []
        except Exception:
            return []

    def watch_devices(self, device_names=None):
        """감시 대상 장비명 — 인자로 받은 목록(실시간 감시 탭에서 체크된 장비)이 없으면
        장비 목록에서 활성화된 전체를 쓴다."""
        if device_names:
            return [str(n) for n in device_names if n]
        return [t["name"] for t in self.inventory_targets()]

    def start_watch(self, interval=0.3, device_names=None):
        """CRTlog tail + Baseline Diff 시작. Baseline이 아직 없으면 자동으로 먼저 로드한다.

        Baseline이 없어도(사전 점검 이력이 없는 첫 방문) 감시 자체는 진행한다 —
        설정 삭제/DOWN syslog 판정은 기준 없이도 유효하고, Baseline 대조가 필요한 항목만
        체크리스트에서 '기준 없음'으로 남는다.
        """
        if self.is_running():
            return {"error": "실시간 감시가 이미 실행 중입니다."}

        store = self.baseline_store()
        baseline_warning = None
        try:
            customer, profile = self._host.active_names()
            if store.key != (customer, profile) or not store.device_names():
                loaded = self.load_baseline()
                if loaded.get("error"):
                    baseline_warning = loaded["error"]
        except Exception as exc:
            baseline_warning = f"Baseline 로드 실패: {exc}"

        from core.crt_stream_watcher import CRTStreamWatcher
        from engine.baseline_diff_engine import BaselineDiffEngine
        from engine.realtime_rule_stream import RealtimeRuleStream
        from engine.stream_device_matcher import StreamDeviceMatcher

        watched = self.watch_devices(device_names)
        self.watch_targets = {name.lower() for name in watched}
        self.diff_engine = BaselineDiffEngine(store)
        self.rule_stream = RealtimeRuleStream()
        monitor = self.monitor()
        monitor.reset(watched, store.device_names())
        # reset()은 필터를 건드리지 않지만, 사용자가 다른 창/YAML에서 고쳤을 수 있으므로
        # 감시를 새로 시작할 때 파일에서 다시 읽어 맞춘다.
        monitor.set_filter(self.config.normalized_filter())
        # reset()이 비운 자리에 이 프로파일의 지난 감시 결과를 다시 얹는다 — '감시 시작'이
        # 이전 회차에서 찾은 오류를 지우는 버튼이 되면 안 된다(그건 '초기화' 버튼의 일이다).
        restored = self.restore_state()
        # 지난 감시에서 열려 있던 장애를 새 엔진의 상태표에 다시 세운다 — 그래야 재시작 뒤에 온 복구 줄
        # (no shutdown, LINEPROTO up, 폴링의 up)이 되살린 경고를 해제한다(IMPROVEMENT_PLAN 4-8).
        self.diff_engine.restore_open_conditions(monitor.open_conditions())

        # 로그 파일 -> 장비 판정. SecureCRT가 파일명을 접속 IP로 남기는 환경이 많아
        # 인벤토리의 IP까지 넘겨야 매칭된다(engine/stream_device_matcher.py 주석 참고).
        inventory = self.inventory_targets()
        picked = [t for t in inventory if (t.get("name") or "").lower() in self.watch_targets] or inventory
        self.device_matcher = StreamDeviceMatcher(picked)

        self.bump_generation()
        watcher = CRTStreamWatcher(
            AppPaths.crt_log_root(),
            self.callback(self.on_stream_delta),
            interval=float(interval or 0.3),
            device_resolver=self.device_matcher.resolve,
            on_error=lambda exc: print(f"[실시간 Diff] 감시 오류: {exc}"),
        )
        self.watcher = watcher
        watcher.start()

        # 두 번째 입력원 — 켜져 있을 때만 시작한다(운영 장비에 SSH 를 만드는 동작이므로).
        poll = self.state_poll_settings()
        if poll["enabled"]:
            try:
                self._start_state_poller(poll["interval_sec"])
            except Exception as exc:
                print(f"[상태 폴링] 시작 실패: {exc}")

        # 규칙 파일이 깨져 기본 키워드만으로 판정 중이면 시작 알림에 같이 띄운다(조용히 넘어가지 않게).
        warning = " / ".join(w for w in (baseline_warning, rules_warning()) if w) or None
        return {"ok": True, "devices": watched, "baseline_devices": store.device_names(),
                "interval": watcher.interval, "warning": warning,
                "restored_devices": restored,
                "state_poll": self.state_poll_settings(),
                "watch_dir": str(AppPaths.crt_log_root())}

    def stop_watch(self):
        self.bump_generation()
        self._stop_state_poller()
        if self.watcher is not None:
            self.watcher.stop()
        if self.diff_engine is not None:
            self.diff_engine.reset_context()
        if self.rule_stream is not None:
            self.rule_stream.reset()
        # 감시를 멈추는 순간이 상태를 남길 마지막 기회다(프로그램 종료가 바로 뒤따르는 경우가 많다).
        self.save_state(force=True)
        return {"ok": True}

    def autostart_watch(self):
        """__main__에서 창 생성 직후 호출 — 설정이 켜져 있을 때만 감시를 시작한다."""
        if not self.config.autostart():
            return {"ok": True, "started": False}
        result = self.start_watch()
        return {"ok": True, "started": not result.get("error"), "detail": result}

    # ---------- 세대 ------------------------------------------------------------------

    def bump_generation(self):
        """감시 '세대'를 올린다 — 시작·중지·프로파일 전환 때. 이전 세대의 늦은 결과를 버리기 위한 표시.
        판정 파이프라인 잠금 안에서 올린다 — 지금 돌고 있는 콜백이 끝난 뒤에 바뀌므로, 세대 확인을
        통과한 콜백이 바뀐 뒤에 결과를 반영하는 일이 없다."""
        with self.pipeline_lock:
            self.generation += 1

    def callback(self, handler):
        """감시기·폴러에 넘길 콜백. 만든 시점의 세대를 기억해 두고, 판정 파이프라인 잠금 안에서 세대가
        그대로일 때만 handler 를 부른다. 프로파일을 바꾸는 동안 이전 감시가 마지막으로 읽은 줄이 새
        프로파일의 엔진·화면에 섞이던 것을 막는다(감시기 join 이 시간 초과로 끝나도 그 스레드의 결과는 버려진다)."""
        generation = self.generation

        def guarded(*args, **kwargs):
            with self.pipeline_lock:
                if generation != self.generation:
                    return None
                return handler(*args, **kwargs)
        return guarded

    # ---------- 능동 상태 폴링(두 번째 입력원) ----------
    # 세션 로그 tail 로는 링크·인접·MLAG 를 알 수 없다 — 그건 syslog 에서만 나오고, syslog 는
    # 세션에 `terminal monitor` 가 걸려 있어야 에코된다(실제 워크스페이스의 CRT 로그에는 한 줄도
    # 없었다). 이 폴러는 우리가 직접 읽기 전용 조회를 돌려 그 의존을 끊는다.

    def state_poll_settings(self):
        out = self.config.state_poll()
        poller = self.state_poller
        out["running"] = bool(poller and poller.is_running())
        out["status"] = poller.status() if poller else None
        return out

    def set_state_poll(self, enabled=None, interval_sec=None):
        """토글/주기 변경 — 감시가 돌고 있으면 즉시 반영한다(다음 감시 시작까지 기다리지 않는다)."""
        current = self.config.state_poll()
        self.config.update({"state_poll": {
            "enabled": current["enabled"] if enabled is None else bool(enabled),
            "interval_sec": current["interval_sec"] if interval_sec is None else interval_sec,
        }})
        settings = self.config.state_poll()
        if self.is_running():
            self._stop_state_poller()
            if settings["enabled"]:
                self._start_state_poller(settings["interval_sec"])
        return {"ok": True, **self.state_poll_settings()}

    def _start_state_poller(self, interval):
        from engine.state_poller import StatePoller

        def targets():
            """매 주기 대상을 다시 읽는다 — 감시 대상 선택이 바뀌면 따라가야 한다.
            접속용 목록(비밀번호 포함)을 쓴다 — 화면용 목록은 비밀번호를 뺀 것이라 접속할 수 없다."""
            watched = self.watch_targets or set()
            try:
                inventory = self._host.connect_targets()
            except Exception:
                return []
            return [t for t in inventory
                    if not watched or (t.get("name") or "").lower() in watched]

        poller = StatePoller(targets, self.callback(self.on_state_events), interval=interval,
                             on_error=lambda exc: print(f"[상태 폴링] 오류: {exc}"))
        self.state_poller = poller
        poller.start()
        return poller

    def _stop_state_poller(self):
        if self.state_poller is not None:
            self.state_poller.stop()
            self.state_poller.reset()

    def on_state_events(self, device, events):
        """StatePoller 콜백(워커 스레드) — 상태 전이를 판정 파이프라인에 넣는다.

        전이가 없어도(조용한 폴링) 불린다. 그 사실 자체가 '봤고 정상이다'라는 근거이므로
        관측 카운트를 갱신해 체크리스트를 '판정 불가'에서 풀어 준다.
        """
        engine = self.diff_engine
        if engine is None:
            return
        if self.watch_targets and (device or "").lower() not in self.watch_targets:
            return
        monitor = self.monitor()
        alerts = engine.ingest_state_events(device, events)
        for alert in alerts:
            alert["source_file"] = "(상태 폴링)"
        if alerts:
            monitor.apply_alerts(alerts)
        monitor.set_observations(engine.observations())
        monitor.bump_repeats(engine.drain_repeats())
        resolved = monitor.resolve_alerts(engine.drain_resolutions())
        monitor.set_open_conditions(engine.open_conditions())
        visible = [a for a in alerts if not a.get("history") and not monitor.is_hidden(a)]
        if visible:
            self.push_alerts(visible)
        if resolved:
            self.push_resolutions(resolved)
        if alerts or resolved:
            self.save_state()

    # ---------- 스트림 수신 ----------------------------------------------------------------

    def on_stream_delta(self, device, text, path, is_history=False):
        """CRTStreamWatcher 콜백(워커 스레드) — 차분 텍스트를 화면 버퍼에 넣고 판정해 push.

        is_history=True는 '감시 시작 전에 이미 기록돼 있던 부분'이다. 프로그램을 켜기 전부터
        SecureCRT가 열려 있던 경우가 흔해서, 이 구간을 판정에서 빼면 이미 벌어진 설정 삭제·링크
        DOWN을 모른 채 '이상 없음'이라고 표시하게 된다 — 그래서 판정은 돌린다.
        다만 **토스트는 띄우지 않는다**: 어제 친 'no vlan 100'이 오늘 감시를 켠 순간 CRITICAL
        팝업으로 튀어나오면 알림을 신뢰할 수 없게 된다. 경고에는 history=True를 붙여 화면에서
        '지금 들어온 것'과 구분할 수 있게 한다.
        """
        engine = self.diff_engine
        if engine is None:
            return
        # 감시 대상으로 고른 장비만 본다(CRTlog 폴더에는 다른 회차/다른 장비 로그도 섞여 있다).
        if self.watch_targets and (device or "").lower() not in self.watch_targets:
            return

        monitor = self.monitor()
        monitor.append_lines(device, text, is_history=is_history)
        alerts = engine.analyze_stream(device, text)
        # 규칙 엔진(config/log_rules.json)도 같은 스트림을 본다. 역할이 나뉘어 있다 —
        # 위(diff)는 '작업자가 무엇을 바꿨나', 아래(규칙)는 '장비가 무엇을 말하나'.
        # 예전에는 아래쪽 공급원이 점검 직후 1회 배치뿐이어서 서명 수십 개가 사장돼 있었다.
        if self.rule_stream is not None:
            # 같은 줄을 diff 엔진이 이미 판정했으면(링크 DOWN 처럼 상태가 있는 사건) 규칙 경고는 버린다.
            # 규칙 경고는 복구로 해제되지 않아서(component_id 없음), 장비가 복구된 뒤에도 같은 사건이
            # 미해결로 하나 더 남았다. analyze() 는 그래도 부른다 — 명령 문맥 추적이 끊기면 안 된다.
            covered = {(a.get("device"), (a.get("raw_line") or "").strip()) for a in alerts}
            alerts = alerts + [a for a in self.rule_stream.analyze(device, text)
                               if (a.get("device"), (a.get("raw_line") or "").strip()) not in covered]
        # '무엇을 볼 수 있었는가'를 화면 쪽으로 넘긴다 — 이 장비에서 syslog 를 한 줄도 못 봤다면
        # 링크/STP/MLAG 체크리스트를 '정상'이라고 적을 수 없다(engine/realtime_monitor.py의
        # _blocked_reason_locked). 엔진이 단일 출처이므로 매번 최신 사본을 통째로 넘긴다.
        # 되짚어 읽은 구간은 '마지막 수신 시각'을 바꾸지 않는다(감시 건강).
        monitor.set_observations(engine.observations(), live=not is_history)
        for alert in alerts:
            alert["source_file"] = os.path.basename(path)
            if is_history:
                alert["history"] = True
            if alert.get("history"):
                # ts 는 '판정을 돌린 시각'이라 되짚어 읽은 구간에서는 거짓말이 된다 — 어제 친
                # 명령이 '지금 15:54에 발생'으로 찍힌다. 좌측 로그 줄과 같은 표기를 쓴다.
                alert["ts"] = "--:--:--"
        if alerts:
            monitor.apply_alerts(alerts)
        # 중복 억제로 접은 재발 횟수를 원래 경고에 더한다 — 조용히 버리면 열 번 흔들린 링크가
        # '한 번 내려갔다'로 읽힌다.
        monitor.bump_repeats(engine.drain_repeats())

        # 복구 이벤트로 취소된 경고 — alerts보다 먼저 반영해야 한다. 같은 tick 안에서
        # '내렸다 올렸다'가 함께 들어오면(터미널 에코가 몰려 도착) 순서가 뒤바뀌면 안 된다.
        resolved = monitor.resolve_alerts(engine.drain_resolutions())
        # 열린 장애 사본 — 화면의 '열린 장애' 줄과 저장본이 읽는다. 감시를 멈추면 엔진은 비지만 이 사본은 남는다.
        monitor.set_open_conditions(engine.open_conditions())

        # 숨긴 규칙/장비는 토스트도 띄우지 않는다 — 우클릭으로 숨긴 뒤에도 토스트가 계속
        # 뜨면 '숨김'이 동작하지 않는 것으로 읽힌다. 이력에는 그대로 남는다.
        visible = [] if is_history else [a for a in alerts if not monitor.is_hidden(a)]
        if visible:
            self.push_alerts(visible)
        if resolved:
            self.push_resolutions(resolved)

        # 판정이 바뀐 tick에서만 저장을 시도한다(내부에서 10초로 다시 묶는다) — 프로그램이
        # 비정상 종료돼도 잃는 것은 마지막 10초뿐이다.
        if alerts or resolved:
            self.save_state()

    def _push(self, fn_name, *args):
        try:
            self._host.push_js(fn_name, *args)
        except Exception as exc:
            print(f"[실시간 Diff] UI push 실패: {exc}")

    def push_alerts(self, alerts, *, notify=True):
        """화면에 토스트로 알리고, 알림 채널(Teams·Slack·웹훅·메일 — 4-4)이 있으면 기준 심각도 이상을 백그라운드로
        보낸다. 받는 쪽은 숨기지 않은, 지금 들어온 경고뿐이다(부르는 쪽이 history·숨김을 걸러 넘긴다)."""
        self._push("onRealtimeDiffAlert", alerts)
        if not notify:
            return
        try:
            from engine.notifications import notifier
            customer, profile = self._host.active_names() if hasattr(self._host, "active_names") else ("", "")
            notifier.notify_realtime(alerts, customer or "", profile or "")
        except Exception as exc:          # 알림이 실패해도 감시는 계속한다
            print(f"[실시간 Diff] 알림 채널로 보내지 못했습니다: {exc}")

    def push_resolutions(self, resolutions):
        """복구가 감지된 경고를 UI에서 지우거나 '해제'로 표시하게 한다.

        alert_id 하나당 한 번씩 부른다(요구된 시그니처가
        window.onRealtimeDiffAlertResolved(alert_id)이므로). 상세 정보는 두 번째 인자로
        같이 넘겨서, 수신부가 '무엇 때문에 해제됐는지'를 보여줄 수 있게 한다.
        """
        for res in resolutions:
            if res.get("alert_id"):
                self._push("onRealtimeDiffAlertResolved", res["alert_id"], res)

    # ---------- 화면 조회·조작 ---------------------------------------------------------

    def baseline_status(self):
        """UI 토글 상태 복원용 — 감시 여부/Baseline 장비/누적 경고 수/자동시작 설정."""
        monitor = self.monitor()
        store = self.baseline_store()
        return {
            "running": self.is_running(),
            "devices": store.device_names(),
            "watched": sorted(self.watch_targets),
            # 화면의 '실시간 감시 중 (N)' 배지는 '지금 문제인 건수'여야 한다 —
            # 복구된 것과 숨긴 것을 세면 다 고쳐 놓고도 배지가 안 내려간다.
            "alert_count": len([a for a in monitor.alerts(limit=10000, include_hidden=False)
                                if not a.get("resolved")]),
            "resolved_count": len([a for a in monitor.alerts(limit=10000) if a.get("resolved")]),
            "autostart": self.config.autostart(),
            "watcher": self.watcher.status() if self.watcher else None,
            "rules": rules_status(),
            "watch_dir": self.watch_dir(),
            # "mixed"면 Baseline에 수동 CRT 로그가 섞였다는 뜻 — 감시 신뢰도가 떨어지므로 알린다.
            "baseline_source_kind": store.source_kind,
        }

    def monitor_state(self, tail=120, since=None):
        """연결 탭 하단 3분할 패널 폴링용 — 장비별 실시간 로그 + 체크리스트 + 오류 분석.

        since: 프론트엔드가 마지막으로 받은 위치({epoch, devices:{장비:seq}, versions:{...}}).
               넘기면 **바뀐 것만** 돌려준다(OPTIMIZATION_PLAN 3-1). 실측(장비 30대, tail=160):
               전체 701.7 KB -> 델타 6.8 KB, 0.8초 폴링 기준 877 KB/s -> 8.5 KB/s.
               생략하면 예전과 똑같이 전체를 돌려준다 — 프론트엔드가 강제 재동기화할 때 쓴다.
        """
        monitor = self.monitor()
        # 프로파일을 바꾼 뒤 이 탭을 열면 그 프로파일의 지난 감시 결과가 보여야 한다.
        self.sync_profile()
        state = monitor.state(tail=int(tail or 120), since=since)
        watcher = self.watcher
        state["running"] = bool(watcher and watcher.is_running())
        # 감시가 도는데도 화면이 비어 있을 때 원인을 바로 보여준다 — 대개 파일-장비 매칭 실패다.
        status = watcher.status() if watcher else {}
        state["unmatched_files"] = status.get("unmatched", [])
        state["tracked_files"] = status.get("tracked_files", 0)
        # 장비별로 지금 열고 있는 로그 파일 + 세션 재접속으로 파일이 바뀐 이력.
        state["device_files"] = status.get("device_files", {})
        state["rollovers"] = status.get("rollovers", [])
        state["watch_dir"] = self.watch_dir()
        # 감시 건강 — 바뀌지 않았으면 델타에서 뺀다(지문은 늘 보낸다). 시각만 담고 '몇 분째'는 화면이 만든다.
        health = self.watch_health(watcher_status=status)
        version = _fingerprint(health)
        state.setdefault("versions", {})["health"] = version
        known = ((since or {}).get("versions") or {}).get("health")
        state["health"] = None if since is not None and known == version else health
        state["ok"] = True
        return state

    def watch_health(self, detail=False, watcher_status=None):
        """감시가 지금 '보고 있는지' — 감시 건강 배너(IMPROVEMENT_PLAN 4-8).

        경고가 없다는 것이 '이상 없음'인지 '못 보고 있음'인지는 경고 목록만으로 알 수 없다. 세 가지를 본다:
          * 세션 로그가 자라고 있나 — SecureCRT 세션이 끊기거나 로그 기록이 멈추면 그 장비는 조용히 안 보인다.
          * syslog 를 마지막으로 언제 받았나 — 링크·인접·MLAG 판정의 근거.
          * 상태 폴링이 실패하고 있나 — 인증 실패·호스트키 바뀜은 다시 해도 같으므로 사람이 고쳐야 한다.
        반환: {running, poll_enabled, level, syslog_at, stale_after_sec, issues:[{kind, level, devices:[...]}]
               [, devices: 장비별 행 — detail=True]}. issues 는 심각한 것부터이고 level 은 그 첫째의 수준이다.
        watcher_status: 부르는 쪽이 방금 읽은 감시기 상태(monitor_state — 폴링마다 두 번 읽지 않게).
        """
        monitor = self.monitor()
        running = self.is_running()
        status = ((watcher_status if watcher_status is not None else self.watcher.status())
                  if running else None) or {}
        files = {str(d).lower(): f for d, f in (status.get("device_files") or {}).items()}
        growth = {str(d).lower(): t for d, t in (status.get("device_growth") or {}).items()}
        poller = self.state_poller
        poll_on = bool(poller and poller.is_running())
        poll = (poller.status() if poller else None) or {}
        errors = {str(d).lower(): m for d, m in (poll.get("errors") or {}).items()}
        kinds = {str(d).lower(): k for d, k in (poll.get("error_kinds") or {}).items()}
        polled = {str(d).lower(): t for d, t in (poll.get("last_poll") or {}).items()}
        seen = monitor.last_seen()
        now = time.time()
        found = {}
        rows = []
        for device in monitor.watched_devices():
            key = device.lower()
            mine = seen.get(device) or {}
            row = {"device": device, "log_file": files.get(key), "log_grew_at": growth.get(key),
                   "syslog_at": mine.get("syslog"), "command_at": mine.get("commands"),
                   "poll_at": polled.get(key) or mine.get("polled"),
                   "poll_error": errors.get(key) if poll_on else None,
                   "poll_error_kind": kinds.get(key, "connect") if poll_on and errors.get(key) else None}
            rows.append(row)
            if running and status.get("first_tick_done"):
                if not row["log_file"]:
                    found.setdefault("no_log", []).append({"device": device})
                elif row["log_grew_at"] and now - row["log_grew_at"] >= LOG_STALE_SEC:
                    found.setdefault("log_stopped", []).append({"device": device, "at": row["log_grew_at"]})
            if row["poll_error"]:
                kind = {"auth": "poll_auth", "host_key": "host_key"}.get(row["poll_error_kind"], "poll_connect")
                found.setdefault(kind, []).append({"device": device, "detail": row["poll_error"]})
        issues = [{"kind": kind, "level": level, "devices": found[kind]}
                  for kind, level in _HEALTH_ISSUES if found.get(kind)]
        out = {"running": running, "poll_enabled": poll_on, "stale_after_sec": LOG_STALE_SEC,
               "level": issues[0]["level"] if issues else "ok",
               "syslog_at": max((r["syslog_at"] for r in rows if r["syslog_at"]), default=None),
               "issues": issues}
        if detail:
            out["devices"] = rows
        return out

    def open_conditions(self):
        """지금 열려 있는 장애(화면 사본) — 구성도 탭이 링크 상태로 읽는다. 감시를 쓴 적이 없으면 빈 목록."""
        monitor = self._monitor
        return monitor.open_conditions() if monitor is not None else []

    def unresolved_alert_counts(self):
        """장비별 미해결 경고 수(복구·무시·숨김 제외) — 구성도의 장비 배지. 감시를 쓴 적이 없으면 빈 dict."""
        monitor = self._monitor
        counts = {}
        if monitor is None:
            return counts
        for alert in monitor.alerts(limit=400, include_hidden=False):
            device = alert.get("device")
            if device and not alert.get("resolved") and not alert.get("ignored"):
                counts[device] = counts.get(device, 0) + 1
        return counts

    def clear_alerts(self):
        """경고 이력과 체크리스트 판정을 함께 비운다 — 화면의 '초기화'는 둘 다를 뜻한다.
        열린 장애도 비운다(엔진의 상태표까지) — 매달린 경고가 없는 상태만 남으면 화면이 설명할 수 없다."""
        with self.pipeline_lock:
            self.monitor().clear_alerts()
            if self.diff_engine is not None:
                self.diff_engine.clear_open_conditions()
        # 비운 결과도 즉시 저장한다 — 안 그러면 다음 실행에서 지웠던 경고가 되살아난다.
        self.save_state(force=True)
        return {"ok": True}

    def resolve_findings(self, alert_ids, note=""):
        """실시간 오류분석 목록의 finding에 '해결' 버튼을 눌렀을 때 — 조치를 완료했다는 뜻이므로
        StateTracker의 자동 복구와 같은 경로(resolve_alerts)를 태운다. 체크리스트 항목도
        '복구'로 바뀐다(RealtimeMonitor._unmark)."""
        ids = [i for i in (alert_ids or []) if i]
        if not ids:
            return {"error": "대상 경고가 없습니다."}
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        with self.pipeline_lock:
            # 그 경고가 매달린 열린 장애도 닫는다 — 안 닫으면 화면은 '해결'인데 열린 장애 줄·구성도는 그대로다.
            # 같은 장애에 매달린 다른 경고(shutdown 에코 + LINEPROTO DOWN)도 함께 해결한다 — 자동 복구와 같은 뜻.
            # 화면 사본을 먼저 닫는다: 엔진의 상태는 화면 경고와 별칭으로 갈린 새 id 를 들고 있을 수 있는데,
            # 사본이 돌려주는 것이 바로 그 id 들이다.
            monitor = self.monitor()
            linked = monitor.close_open_conditions(ids)
            if self.diff_engine is not None:
                linked += self.diff_engine.discard_open_conditions(ids + linked)
            targets = list(dict.fromkeys(ids + linked))
            resolutions = [{"alert_id": i, "resolved_by": note or "수동 해결", "ts": now, "duration_sec": None}
                           for i in targets]
            applied = monitor.resolve_alerts(resolutions)
        self.save_state(force=True)
        return {"ok": True, "resolved": len(applied)}

    def ignore_findings(self, alert_ids, note=""):
        """'무시' 버튼 — 장애가 복구됐다는 보장은 없지만 지금 조치 목록에서 뺀다. 체크리스트
        상태(fail/warn 배지)는 그대로 두고, 실시간 오류분석 목록/카운트에서만 사라진다."""
        ids = [i for i in (alert_ids or []) if i]
        if not ids:
            return {"error": "대상 경고가 없습니다."}
        applied = self.monitor().ignore_alerts(ids, ignored_by="user", note=note or "")
        self.save_state(force=True)
        return {"ok": True, "ignored": len(applied)}

    def get_filter(self):
        """실시간 감시 화면의 숨김/고정 설정. 엔진에도 즉시 적용한다 —
        프로그램을 켠 직후 첫 폴링에서도 저장된 숨김이 반영되어야 한다."""
        filter_cfg = self.config.normalized_filter()
        self.monitor().set_filter(filter_cfg)
        return {"ok": True, **filter_cfg}

    def save_filter(self, hidden_rules=None, hidden_devices=None, hidden_keywords=None, pinned_items=None):
        """부분 갱신 — None으로 준 항목은 기존 값을 유지한다.
        빈 리스트([])는 '전부 해제'라는 뜻이므로 None과 구별해야 한다('숨김 모두 해제' 동작)."""
        incoming = {"hidden_rules": hidden_rules, "hidden_devices": hidden_devices,
                    "hidden_keywords": hidden_keywords, "pinned_items": pinned_items}
        self.config.edit_filter(lambda current: {k: (current[k] if v is None else v) for k, v in incoming.items()})
        return self.get_filter()

    def toggle_filter_entry(self, kind, value, enabled=None):
        """우클릭 메뉴용 단건 토글 — 'Hide Rule' / 'Filter Out Device' / 키워드 숨기기.

        kind: 'rule' | 'device' | 'keyword'. enabled=None이면 있으면 빼고 없으면 넣는다.
        리스트 전체를 보내는 save_filter()와 따로 둔 이유: 우클릭은 항목 하나만
        아는데, 전체 리스트를 만들려면 프론트엔드가 서버 상태를 정확히 복제하고 있어야 한다.
        폴링 사이에 다른 곳에서 바뀌면 그 복제가 낡아 다른 항목을 되살린다.
        """
        key = {"rule": "hidden_rules", "device": "hidden_devices", "keyword": "hidden_keywords"}.get(kind)
        if not key:
            return {"error": f"알 수 없는 필터 종류: {kind}"}
        value = str(value or "").strip()
        if not value:
            return {"error": "숨길 대상이 비어 있습니다."}

        def change(current):
            items = list(current[key])
            want_on = (value not in items) if enabled is None else bool(enabled)
            if want_on and value not in items:
                items.append(value)
            elif not want_on and value in items:
                items.remove(value)
            return {key: items}

        self.config.edit_filter(change)
        return self.get_filter()

    def toggle_pin(self, device, check_id, pinned=None):
        """체크리스트 항목을 상단 '고정 카드'로 올리거나 내린다."""
        device, check_id = str(device or "").strip(), str(check_id or "").strip()
        if not device or not check_id:
            return {"error": "고정할 장비/점검항목이 비어 있습니다."}

        def change(current):
            items = list(current["pinned_items"])
            exists = next((i for i in items if i["device"] == device and i["check_id"] == check_id), None)
            want_on = (exists is None) if pinned is None else bool(pinned)
            if want_on and exists is None:
                items.append({"device": device, "check_id": check_id})
            elif not want_on and exists is not None:
                items.remove(exists)
            return {"pinned_items": items}

        self.config.edit_filter(change)
        return self.get_filter()

    def checklist_catalog(self):
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
            "devices": [t.get("name") for t in self.inventory_targets() if t.get("name")],
            "checks": [{"key": key, "label": label, "sources": list(sources)}
                       for key, label, _types, sources in CHECK_ITEMS],
            "rule_ids": rule_ids,
            **self.config.normalized_filter(),
        }

    def save_layout(self, split_ratio=None, right_ratio=None, view_mode=None, selected_devices=None):
        updates = {}
        if split_ratio is not None:
            updates["split_ratio"] = clamp_ratio(split_ratio, _LAYOUT_DEFAULTS["split_ratio"])
        if right_ratio is not None:
            updates["right_ratio"] = clamp_ratio(right_ratio, _LAYOUT_DEFAULTS["right_ratio"])
        if view_mode in ("tabs", "split"):
            updates["view_mode"] = view_mode
        if selected_devices is not None:
            updates["selected_devices"] = [str(n) for n in selected_devices if n]
        if updates:
            self.config.update(updates)
        return {"ok": True, **self.config.layout()}

    # ---------- 감시 로그 파일(진단 모달) ---------------------------------------------------

    def _tracked_paths(self):
        status = self.watcher.status() if self.watcher else {}
        return status, {os.path.normcase(os.path.abspath(p)) for p in (status.get("active_paths") or [])}

    def probe_log_files(self):
        """'왜 감시가 안 되나'를 화면에서 확인하기 위한 진단 — CRTlog의 각 파일이 어떤 근거로
        어느 장비에 매칭되는지 보여준다. 감시를 시작하지 않은 상태에서도 호출할 수 있다.

        감시는 장비 1대당 '가장 최근 로그 파일' 하나만 따라가므로(CRTStreamWatcher.latest_only),
        각 행에 지금 실제로 추적 중인지(tracked)와 그 장비의 최신 파일인지(latest)를 함께 넘긴다.
        감시가 아직 안 돌고 있으면 tracked는 전부 False이고, latest만으로 '감시를 시작하면
        어느 파일이 추적될지'를 미리 보여준다."""
        from engine.stream_device_matcher import StreamDeviceMatcher
        from core.crt_stream_watcher import iter_session_log_files

        root = self.watch_dir()
        matcher = StreamDeviceMatcher(self.inventory_targets())
        status, tracked = self._tracked_paths()

        rows = []
        # 감시 스레드와 **같은 순회 함수**를 쓴다 — 진단이 감시가 보는 것과 다른 목록을 보여주면
        # '감시는 추적 중인데 진단에는 없다'가 되어 원인을 못 찾는다(하위 폴더 로깅 환경).
        for path in sorted(iter_session_log_files(root)):
            try:
                with open(path, "rb") as f:
                    head = f.read(4096).decode("utf-8", errors="replace")
                size = os.path.getsize(path)
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            row = matcher.probe(path, head)
            # 하위 폴더의 파일은 이름만으로는 가리킬 수 없다 — 목록에 상대 경로를 함께 준다
            # (삭제는 최상위 파일만 다루므로 UI가 이 값으로 구분해 보여줄 수 있어야 한다).
            row["rel"] = os.path.relpath(path, root).replace(os.sep, "/")
            row["in_subdir"] = "/" in row["rel"]
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

    def delete_log_files(self, filenames):
        """'CRT 로그 파일 진단' 모달의 다중 삭제 — probe_log_files()와 같은 폴더
        (CRTlog 최상위, 하위 폴더 없음)의 파일만 지운다.

        filenames: 파일명 목록(경로 아님) — os.path.basename()으로만 다뤄 경로 조작을 막는다.
        지금 감시가 tail 중인 파일(watcher의 active_paths)은 건너뛴다. 열려서 오프셋을 추적
        중인 파일을 지우면 다음 tick에 파일이 사라진 것으로 보여 감시가 그 장비를 놓치거나,
        곧바로 같은 이름의 새 세션 로그가 생겼을 때 오프셋이 꼬일 수 있다 — 지우려면 먼저
        감시를 멈추라고 안내한다.

        반환: {"deleted": [파일명,...], "errors": {파일명: 메시지}}.
        """
        from core.crt_stream_watcher import DEFAULT_EXTENSIONS
        root = os.path.abspath(str(AppPaths.crt_log_root()))
        _status, tracked = self._tracked_paths()

        deleted, errors = [], {}
        for raw in filenames or []:
            name = os.path.basename(str(raw or "").strip())
            if not name:
                continue
            if not name.lower().endswith(DEFAULT_EXTENSIONS):
                errors[name or raw] = "txt/log 파일만 삭제할 수 있습니다."
                continue
            abs_path = os.path.join(root, name)
            if os.path.normcase(abs_path) in tracked:
                errors[name] = "지금 감시가 추적 중인 파일입니다 — 감시를 멈춘 뒤 삭제하세요."
                continue
            if not os.path.isfile(abs_path):
                # 진단 목록은 하위 폴더까지 보여주는데(감시와 같은 순회) 삭제는 최상위만
                # 다룬다. 그 차이를 '존재하지 않습니다'로 뭉개면 사용자가 목록에 보이는 파일을
                # 지우려다 원인을 알 수 없게 된다.
                if _found_in_subdir(root, name):
                    errors[name] = "하위 폴더의 파일입니다 — '폴더 열기'에서 삭제하세요."
                else:
                    errors[name] = "파일이 존재하지 않습니다."
                continue
            try:
                os.remove(abs_path)
                deleted.append(name)
            except OSError as e:
                errors[name] = str(e)
        return {"ok": True, "deleted": deleted, "errors": errors}
