"""RealtimeMonitor — 실시간 감시 탭이 그릴 3개 패널의 상태를 한 곳에 모은 저장소.

패널 구성과 이 클래스의 대응:
  * 좌측(장비별 실시간 로그)      -> stream_buffers  : 장비별 최근 CLI 라인 링버퍼
  * 우측 하단(실시간 체크리스트)  -> checklists      : 장비 x 점검항목 격자의 통과/실패 상태
  * 우측 상단(실시간 오류 분석)   -> analysis        : 체크리스트/경고를 규칙 기반으로 요약

API mixin(api/log_analysis_run_api.py)이 CRTStreamWatcher 콜백에서 push하고, 프론트엔드는
get_realtime_monitor_state()로 폴링해 읽는다. 상태를 API mixin의 인스턴스 속성으로 흩뿌리지 않고
여기 모은 이유는, 세 패널이 같은 이벤트 하나(alert)에서 파생되므로 갱신 시점이 어긋나면
'왼쪽 로그에는 보이는데 체크리스트는 정상'인 화면이 나오기 때문이다.

스레드 안전: 감시 스레드(쓰기)와 pywebview JS 브릿지 스레드(읽기)가 동시에 접근하므로
모든 공개 메서드는 하나의 RLock 안에서 동작하고, 읽기는 항상 복사본을 반환한다.
"""
import threading
import time
from collections import OrderedDict, deque

# 관측 카운터의 축(commands/syslog/output/polled)은 판정 엔진이 단일 출처다 —
# 여기서 따로 나열하면 한쪽만 늘어났을 때 조용히 갈라진다.
from engine.baseline_diff_engine import OBSERVED_KEYS as _OBSERVED_KEYS

# 체크리스트 항목 정의 — (키, 표시명, 이 항목을 실패로 만드는 alert type들, 판정에 필요한 입력원)
#
# 네 번째 칸(sources)이 있는 이유: 항목마다 '무엇을 봐야 판정할 수 있는가'가 다르다.
# 설정 삭제·shutdown·위험 명령은 작업자가 입력한 **명령**에서 읽지만, 링크/STP/MLAG 상태는
# 장비가 뱉는 **syslog** 에서만 알 수 있다. SecureCRT 세션에 `terminal monitor` 가 안 걸려
# 있으면 syslog 는 한 줄도 오지 않으므로(실제 워크스페이스가 그 상태였다) 그 항목들은
# '변경 없음(정상)'이 아니라 '판정 불가'로 남아야 한다 — 이 화면에 '정상'이라고 적힌 것을
# 근거로 점검을 마무리하기 때문이다.
# 입력원 세 가지. polled 는 우리가 직접 SSH 로 물어본 것(engine/state_poller.py) —
# 작업자의 `terminal monitor` 설정에 의존하지 않는 유일한 근거다.
_SRC_COMMAND = "commands"
_SRC_SYSLOG = "syslog"
_SRC_POLL = "polled"
_STATE_SOURCES = (_SRC_SYSLOG, _SRC_POLL)
CHECK_ITEMS = (
    ("vlan", "VLAN 설정 유지", ("CONFIG_REMOVED",), (_SRC_COMMAND,)),
    ("interface", "인터페이스 설정/활성", ("CONFIG_REMOVED", "INTERFACE_SHUTDOWN"), (_SRC_COMMAND,)),
    ("route", "정적 라우팅 유지", ("CONFIG_REMOVED",), (_SRC_COMMAND,)),
    # BGP 네이버 '삭제 명령'은 명령에서 보이지만 '인접 끊김'은 syslog 나 상태 폴링에서만 보인다 —
    # 하나라도 관측 가능하면 판정 가능으로 본다.
    ("neighbor", "라우팅 인접(BGP/OSPF)", ("CONFIG_REMOVED", "NEIGHBOR_DOWN"),
     (_SRC_COMMAND,) + _STATE_SOURCES),
    ("link", "링크 상태", ("LINK_DOWN",), _STATE_SOURCES),
    ("stp_mlag", "STP / MLAG 안정", ("STP_CHANGE", "MLAG_STATE"), _STATE_SOURCES),
    ("ops", "위험 운영 명령 없음", ("DESTRUCTIVE_COMMAND",), (_SRC_COMMAND,)),
)
_ITEM_SOURCES = {key: sources for key, _label, _types, sources in CHECK_ITEMS}

# alert의 target prefix -> 체크리스트 항목 키. target은 diff 엔진이 붙이는 'vlan:100' 형태다.
_TARGET_PREFIX_TO_ITEM = {
    "vlan": "vlan",
    "interface": "interface",
    "shutdown": "interface",
    "noshut": "interface",
    "route": "route",
    "bgp": "neighbor",
    "router-bgp": "neighbor",
    "peer": "neighbor",
    "link": "link",
    "mlag": "stp_mlag",
    "cmd": "ops",
}
_TYPE_TO_ITEM = {
    "INTERFACE_SHUTDOWN": "interface",
    "INTERFACE_NOSHUT": "interface",
    "LINK_DOWN": "link",
    "LINK_UP": "link",
    "NEIGHBOR_DOWN": "neighbor",
    "NEIGHBOR_UP": "neighbor",
    "STP_CHANGE": "stp_mlag",
    "MLAG_STATE": "stp_mlag",
    "MLAG_PEER_DOWN": "stp_mlag",
    "MLAG_PEER_UP": "stp_mlag",
    "CONFIG_RESTORED": "vlan",
    "DESTRUCTIVE_COMMAND": "ops",
}
# 체크리스트 항목 키(_item_key_for가 돌려주는 축)를 왼쪽 목록에 쓸 짧은 기술 분류명으로.
# "VLAN 설정 유지" 같은 체크리스트 문장은 항목 하나의 판정 문구라 목록 라벨로 쓰기엔 길다 —
# 여기는 "VLAN / 3대"처럼 한눈에 읽히는 이름이 필요하다.
_CATEGORY_LABEL = {
    "vlan": "VLAN",
    "interface": "인터페이스",
    "route": "정적 라우팅",
    "neighbor": "BGP/OSPF 인접",
    "link": "링크",
    "stp_mlag": "STP/MLAG",
    "ops": "운영 명령",
    None: "기타",
}
_SEVERITY_RANK = {"WARNING": 1, "MAJOR": 2, "CRITICAL": 3}
_EMPTY_OBSERVED = dict.fromkeys(_OBSERVED_KEYS, 0)
# 한 번에 넘길 finding 수의 상한. finding 은 '장비 x 규칙' 단위라 장비 30대 x 규칙 몇 개면
# 금세 수십 개가 된다 — 화면이 묶은 뒤의 줄 수는 그보다 훨씬 적다(규칙 수만큼).
_FINDING_MAX = 200
# 이 type들은 '복구'라서 체크리스트를 fail로 만들지 않는다.
_RECOVERY_TYPES = ("LINK_UP", "NEIGHBOR_UP", "INTERFACE_NOSHUT", "MLAG_PEER_UP", "CONFIG_RESTORED")

# 좌측 로그 박스는 스크롤 없이 고정 높이로 보여주므로(요구사항), 한 장비당 화면에 들어갈
# 만큼만 남기고 오래된 줄은 버린다. 넉넉히 잡아 UI에서 tail만 잘라 쓴다.
_LINES_PER_DEVICE = 400
_LOG_LINE_MAX = 400  # 아주 긴 배너/설정 덤프 한 줄이 payload를 부풀리지 않게 자름


class RealtimeMonitor:
    def __init__(self, lines_per_device=_LINES_PER_DEVICE):
        self._lock = threading.RLock()
        self._lines_per_device = lines_per_device
        self._buffers = OrderedDict()   # {device: deque[{ts, text, level}]}
        self._checklists = {}           # {device: {item_key: state dict}}
        self._alerts = []               # 최근 경고(분석/이력 공용)
        self._devices = []              # 감시 대상(장비목록에서 체크된 장비)
        self._baseline_devices = set()  # Baseline 스냅샷이 있는 장비
        self._started_at = None
        self._last_activity = {}        # {device: epoch}
        # {device: {"commands": n, "syslog": n, "output": n}} — BaselineDiffEngine 이 센 것.
        # '무엇을 볼 수 있었는가'가 곧 '무엇을 정상이라고 말할 자격이 있는가'다.
        self._observed = {}
        # 이미 본 history 경고의 내용 서명 — 앱을 다시 켤 때마다 같은 사건이 쌓이는 것을 막는다.
        self._history_seen = set()
        self._alert_max = 300
        # --- 델타 전송용 상태 (OPTIMIZATION_PLAN 3-1) ---
        # 로그 줄마다 단조증가 seq 를 붙인다. 프론트엔드가 마지막으로 받은 seq 를 보내면
        # 그 이후 줄만 돌려주므로, 폴링 payload 가 '장비 수 x tail' 에서 '새로 들어온 줄'로 줄어든다.
        self._line_seq = 0
        # 버퍼를 통째로 비우는 일(reset/clear_alerts)이 생기면 seq 가 되감기므로, 프론트엔드의
        # 낡은 seq 를 그대로 믿으면 안 된다. epoch 가 다르면 무조건 전체를 다시 보낸다.
        self._epoch = 1
        # Module 4 — 화면 필터/고정. API가 config/realtime_watch.yaml에서 읽어 set_filter()로 넣는다.
        # 필터를 판정 단계가 아니라 '표시 단계'에 두는 이유: 숨긴 규칙도 이력에는 남아야 하고
        # (숨김 해제하면 다시 보여야 한다), 무엇보다 숨김이 판정을 바꾸면 안 된다.
        self._filter = _empty_filter()

    # ---------- 라이프사이클 ----------
    def reset(self, devices, baseline_devices=()):
        """감시 시작 시 호출 — 대상 장비 목록으로 체크리스트를 '미확인' 상태로 초기화."""
        with self._lock:
            self._devices = list(devices)
            self._baseline_devices = set(baseline_devices)
            self._buffers = OrderedDict((d, deque(maxlen=self._lines_per_device)) for d in self._devices)
            self._checklists = {d: self._fresh_checklist(d) for d in self._devices}
            self._alerts = []
            self._last_activity = {}
            # 관측 카운트도 비운다 — 감시를 새로 시작하면 판정 엔진(BaselineDiffEngine)도
            # 새로 만들어져 0에서 다시 센다. 여기만 남겨 두면 '지난 감시에서는 syslog 를
            # 봤다'는 근거로 이번 감시의 판정 불가가 정상으로 보인다.
            self._observed = {}
            self._history_seen = set()
            self._started_at = time.time()
            # 버퍼를 새로 만들었으므로 프론트엔드가 들고 있는 seq 는 의미가 없다.
            self._line_seq = 0
            self._epoch += 1

    def adopt_devices(self, devices, baseline_devices=()):
        """감시를 시작하지 않은 채로 장비 목록만 등록한다(저장본 복원 직전에 쓴다).

        restore()는 '지금 감시 대상인 장비'만 되살린다. 프로그램을 켠 직후에는 그 목록이 비어
        있어서, 이걸 먼저 부르지 않으면 저장본이 통째로 걸러진다. 이미 있는 장비의 체크리스트는
        건드리지 않는다.
        """
        with self._lock:
            for device in devices or []:
                if device and device not in self._devices:
                    self._devices.append(device)
                self._ensure_device(device)
            self._baseline_devices |= set(baseline_devices or ())

    def set_baseline_devices(self, baseline_devices):
        """점검이 끝나 Baseline이 새로 로드됐을 때 호출 — 감시를 끊지 않고 기준만 갈아끼운다.

        reset()을 부르면 안 된다: 그건 좌측 로그 버퍼와 경고 이력까지 날려서, 점검이 끝나는
        순간 작업자가 보고 있던 화면이 통째로 비워진다. 여기서는 '기준 없음'으로 남아 있던
        체크리스트 항목만 '정상(pending)'으로 승격시킨다.

        반환: 새로 기준이 생긴 장비 목록.
        """
        with self._lock:
            new_set = set(baseline_devices or ())
            gained = sorted(new_set - self._baseline_devices)
            self._baseline_devices = new_set
            for device in self._devices:
                checklist = self._checklists.get(device)
                if checklist is None:
                    continue
                has_baseline = device in new_set
                for item in checklist.values():
                    # 이미 판정이 난 항목(fail/warn/recovered)은 건드리지 않는다 —
                    # Baseline 갱신은 '기준'을 바꾸는 일이고, 이미 관측된 사실을 지우지 않는다.
                    if item["status"] == "unknown" and has_baseline:
                        item["status"] = "pending"
                        item["detail"] = "변경 없음"
                    elif item["status"] == "pending" and not has_baseline:
                        item["status"] = "unknown"
                        item["detail"] = "Baseline 기준 없음"
            return gained

    def _fresh_checklist(self, device):
        has_baseline = device in self._baseline_devices
        return {
            key: {
                "key": key,
                "label": label,
                # Baseline이 없으면 '정상'이라고 말할 근거가 없다 — 판정 불가로 남긴다.
                "status": "pending" if has_baseline else "unknown",
                "detail": "변경 없음" if has_baseline else "Baseline 기준 없음",
                "severity": None,
                "count": 0,
                "last_ts": "",
                "raw_line": "",
                # 이 항목을 fail/warn으로 만든 경고들. 전부 취소되면 항목이 '복구'로 돌아간다.
                "alert_ids": [],
            }
            for key, label, _types, _sources in CHECK_ITEMS
        }

    # ---------- 쓰기 ----------
    def append_lines(self, device, text, is_history=False):
        """CRTStreamWatcher 차분 텍스트를 좌측 로그 패널 버퍼에 넣는다.

        is_history=True는 감시 시작 전부터 파일에 있던 부분이다. 화면이 비어 보이지 않도록
        넣어 주지만, 타임스탬프를 '--:--:--'로 두어 지금 들어온 입력과 구분한다.
        """
        with self._lock:
            buf = self._ensure_device(device)
            stamp = "--:--:--" if is_history else time.strftime("%H:%M:%S")
            for line in (text or "").splitlines():
                line = line.rstrip()
                if not line.strip():
                    continue
                self._line_seq += 1
                buf.append({"ts": stamp, "text": line[:_LOG_LINE_MAX],
                            "history": is_history, "seq": self._line_seq})
            if not is_history:
                self._last_activity[device] = time.time()

    def set_observations(self, observations):
        """BaselineDiffEngine.observations() 를 그대로 받는다({장비: {commands, syslog, output}}).

        누적값을 통째로 갈아끼운다(증분이 아니다) — 엔진이 단일 출처이므로 두 곳에서 세다가
        어긋나는 것보다 매번 최신 사본을 받는 것이 안전하다.
        """
        with self._lock:
            for device, counts in (observations or {}).items():
                if not device or not isinstance(counts, dict):
                    continue
                self._observed[device] = {k: int(counts.get(k) or 0)
                                         for k in _OBSERVED_KEYS}

    def _blocked_reason_locked(self, device, item_key):
        """이 항목을 '정상'이라고 말할 근거가 없으면 그 이유를, 있으면 None.

        판정 근거가 되는 입력원(명령 / syslog) 중 하나도 이 장비에서 관측되지 않았다면
        '변경 없음'이라고 쓸 수 없다. 규칙이 한 번도 걸리지 않은 것과 검사해서 통과한 것은
        다른 이야기이고(_pin_from_alerts_locked 의 같은 판단), 이 화면은 '정상'이라고 적힌 것을
        근거로 점검을 마무리하는 데 쓰인다.
        """
        sources = _ITEM_SOURCES.get(item_key)
        if not sources:
            return None
        counts = self._observed.get(device) or {}
        if any((counts.get(src) or 0) > 0 for src in sources):
            return None
        if sources == _STATE_SOURCES:
            return ("판정 불가 — syslog 미수신(terminal monitor) · 상태 폴링도 꺼져 있습니다")
        return "판정 불가 — 이 장비에서 아직 입력이 관측되지 않았습니다"

    def apply_alerts(self, alerts):
        """판정된 경고를 체크리스트에 반영하고 이력에 쌓는다.

        history 경고(감시 시작 전부터 파일에 있던 구간을 되짚어 판정한 것)는 내용 서명으로
        중복을 막는다. 감시를 시작할 때마다 세션 로그의 마지막 256KB 를 다시 판정하는데
        (CRTStreamWatcher 의 seed), alert_id 는 프로세스마다 새로 발급되므로 저장본의 id 대조로는
        걸러지지 않는다 — 자동시작을 켜 두면 앱을 켤 때마다 어제 친 `no vlan 100` 이 한 건씩
        쌓여서, 같은 사건이 이력에 다섯 번 여섯 번 나타난다.
        """
        with self._lock:
            for alert in alerts:
                if alert.get("history"):
                    signature = _history_signature(alert)
                    if signature in self._history_seen:
                        continue
                    self._history_seen.add(signature)
                device = alert.get("device")
                self._ensure_device(device)
                item_key = _item_key_for(alert)
                if item_key:
                    self._mark(device, item_key, alert)
                self._alerts.append(alert)
            if len(self._alerts) > self._alert_max:
                del self._alerts[:len(self._alerts) - self._alert_max]

    def bump_repeats(self, repeats):
        """BaselineDiffEngine.drain_repeats() 를 반영 — 억제된 재발을 원래 경고에 더한다."""
        if not repeats:
            return
        with self._lock:
            by_id = {a.get("alert_id"): a for a in self._alerts if a.get("alert_id")}
            for alert_id, count in repeats.items():
                alert = by_id.get(alert_id)
                if alert is not None:
                    alert["repeat"] = int(count)

    # ---------- 필터 / 고정 (Module 4) ----------
    def set_filter(self, filter_cfg):
        """config/realtime_watch.yaml의 realtime_filter 구조를 그대로 받는다."""
        with self._lock:
            self._filter = _normalize_filter(filter_cfg)

    def get_filter(self):
        with self._lock:
            return _copy_filter(self._filter)

    def is_hidden(self, alert):
        """토스트 push 여부 판단용 공개 래퍼(api/log_analysis_run_api.py에서 호출)."""
        with self._lock:
            return self._is_hidden_locked(alert)

    def _is_hidden_locked(self, alert):
        """이 경고를 화면에서 감출지. 이력(alerts())에는 남으므로 판정에는 영향이 없다."""
        f = self._filter
        if (alert.get("device") or "") in f["hidden_devices"]:
            return True
        # rule은 두 가지로 지목될 수 있다 — diff 엔진의 alert type(INTERFACE_SHUTDOWN)과
        # 규칙 엔진의 rule_id(errdisable_feature_table). 우클릭 메뉴가 무엇을 집었는지에
        # 따라 달라지므로 둘 다 본다.
        for key in (alert.get("rule_id"), alert.get("type")):
            if key and key in f["hidden_rules"]:
                return True
        haystack = f"{alert.get('message', '')} {alert.get('raw_line', '')}".upper()
        return any(kw in haystack for kw in f["hidden_keywords"])

    def _pin_from_alerts_locked(self, device, check_id, rank):
        """체크리스트에 없는 check_id(규칙 엔진 서명 id 등)를 경고 이력에서 판정한다.

        해당 규칙의 경고가 하나도 없으면 '미관측'으로 둔다 — '정상'이라고 쓰면 안 된다.
        규칙이 한 번도 걸리지 않은 것과 검사해서 통과한 것은 다른 이야기이고, 이 화면은
        '정상'이라고 적힌 것을 근거로 점검을 마무리하는 데 쓰인다.
        """
        hits = [a for a in self._alerts
                if a.get("device") == device
                and check_id in (a.get("rule_id"), a.get("type"))]
        live = [a for a in hits if not a.get("resolved")]
        if not hits:
            status, detail, severity = "unknown", "미관측 — 이 규칙의 경고가 아직 없습니다", None
        elif not live:
            status, detail, severity = "recovered", f"복구됨 ({len(hits)}건 해제)", None
        else:
            worst = max(live, key=lambda a: _SEVERITY_RANK.get(a.get("severity") or "", 0))
            severity = worst.get("severity")
            status = "fail" if severity in ("CRITICAL", "MAJOR") else "warn"
            detail = worst.get("message", "")
        return {
            "key": check_id, "check_id": check_id, "device": device,
            "label": check_id.replace("_", " "), "status": status, "detail": detail,
            "severity": severity, "count": len(hits),
            "last_ts": hits[-1].get("ts", "") if hits else "",
            "raw_line": hits[-1].get("raw_line", "") if hits else "",
            "alert_ids": [a.get("alert_id") for a in live if a.get("alert_id")],
            "pinned": True, "pin_rank": rank, "from_rules": True,
        }

    def _pin_rank_locked(self, device, check_id):
        """고정 목록의 순번. 고정 안 된 항목은 None."""
        for i, item in enumerate(self._filter["pinned_items"]):
            if item.get("device") == device and item.get("check_id") == check_id:
                return i
        return None

    def _mark(self, device, item_key, alert):
        checklist = self._checklists.setdefault(device, self._fresh_checklist(device))
        item = checklist.get(item_key)
        if item is None:
            return
        severity = alert.get("severity") or "WARNING"
        recovered = alert.get("type") in _RECOVERY_TYPES
        item["count"] += 1
        item["last_ts"] = alert.get("ts", "")
        item["raw_line"] = alert.get("raw_line", "")
        if recovered and item["status"] != "fail":
            item["status"] = "recovered"
            item["detail"] = alert.get("message", "")
            item["severity"] = severity
            return
        # 더 심각한 판정만 덮어쓴다 — WARNING 하나가 앞서 잡힌 CRITICAL을 가리면 안 된다.
        if _SEVERITY_RANK.get(severity, 0) >= _SEVERITY_RANK.get(item.get("severity") or "", 0):
            item["severity"] = severity
            item["detail"] = alert.get("message", "")
        item["status"] = "fail" if severity in ("CRITICAL", "MAJOR") else "warn"
        alert_id = alert.get("alert_id")
        if alert_id and alert_id not in item["alert_ids"]:
            item["alert_ids"].append(alert_id)

    # ---------- 취소(복구) 반영 ----------
    def resolve_alerts(self, resolutions):
        """StateTracker가 낸 취소 목록을 이력과 체크리스트에 반영.

        경고를 지우지 않고 resolved 표시만 남기는 이유: '내렸다가 올렸다'는 사실 자체가
        점검 이력이다. 이력 모달에서는 취소선으로 보이고, 화면 상단 카운트에서만 빠진다.
        반환값은 실제로 반영된 취소 목록(UI push는 이걸로 한다 — 이미 없어진 alert_id를
        UI에 보내면 지울 대상이 없어 조용히 실패한다).
        """
        applied = []
        with self._lock:
            by_id = {a.get("alert_id"): a for a in self._alerts if a.get("alert_id")}
            for res in resolutions or []:
                alert = by_id.get(res.get("alert_id"))
                if alert is None or alert.get("resolved"):
                    continue
                alert["resolved"] = True
                alert["resolved_by"] = res.get("resolved_by", "")
                alert["resolved_ts"] = res.get("ts", "")
                alert["duration_sec"] = res.get("duration_sec")
                applied.append(dict(res, message=alert.get("message", ""),
                                    severity=alert.get("severity", "")))
                self._unmark(alert, res)
        return applied

    def ignore_alerts(self, alert_ids, *, ignored_by="", note=""):
        """사용자가 실시간 오류분석 화면에서 '무시'를 누른 finding에 속한 alert들을 표시한다.

        resolve_alerts()와 다른 점: resolved는 '장애가 실제로 복구됐다'는 사실이고, ignore는
        '장애는 그대로일 수 있지만 지금 조치 대상에서 뺀다'는 사용자 판단이다. 그래서 체크리스트
        상태(_checklists)는 건드리지 않는다 — 체크리스트는 여전히 fail/warn을 보여줘야 한다.
        경고 자체는 지우지 않고 ignored 표시만 남긴다(이력에는 계속 남아야 하므로).
        """
        applied = []
        with self._lock:
            by_id = {a.get("alert_id"): a for a in self._alerts if a.get("alert_id")}
            for alert_id in alert_ids or []:
                alert = by_id.get(alert_id)
                if alert is None or alert.get("ignored") or alert.get("resolved"):
                    continue
                alert["ignored"] = True
                alert["ignored_by"] = ignored_by
                alert["ignored_ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
                alert["ignored_note"] = note
                applied.append(alert.get("alert_id"))
        return applied

    def _unmark(self, alert, res):
        """취소된 경고를 체크리스트 항목에서 뺀다. 남은 미해결 경고가 없으면 '복구'로 되돌린다."""
        item_key = _item_key_for(alert)
        if not item_key:
            return
        item = (self._checklists.get(alert.get("device")) or {}).get(item_key)
        if item is None:
            return
        alert_id = alert.get("alert_id")
        if alert_id in item["alert_ids"]:
            item["alert_ids"].remove(alert_id)
        if item["alert_ids"]:
            return   # 같은 항목에 아직 미해결 경고가 남아 있다 — 상태를 되돌리면 안 된다.
        item["status"] = "recovered"
        item["severity"] = None
        detail = res.get("resolved_by") or "복구됨"
        secs = res.get("duration_sec")
        item["detail"] = (f"복구 완료 ({detail})" if secs is None
                          else f"복구 완료 — {secs:g}초간 이상, 조치: {detail}")
        item["last_ts"] = res.get("ts", item.get("last_ts", ""))

    def _ensure_device(self, device):
        """감시 시작 후 장비목록에 없던 장비가 등장해도 패널이 비지 않게 즉석 등록."""
        device = device or "unknown"
        if device not in self._buffers:
            self._buffers[device] = deque(maxlen=self._lines_per_device)
            self._checklists.setdefault(device, self._fresh_checklist(device))
            if device not in self._devices:
                self._devices.append(device)
        return self._buffers[device]

    # ---------- 읽기 ----------
    def state(self, tail=120, since=None):
        """프론트엔드 폴링용 스냅샷. tail: 장비별로 넘겨줄 최근 로그 줄 수.

        since: 프론트엔드가 마지막으로 받은 위치. {"epoch": int, "devices": {장비: seq}} 형태.
               넘기면 **로그 줄은 그 이후 것만** 담아 payload 를 줄인다(OPTIMIZATION_PLAN 3-1).
               생략하면 예전과 똑같이 전체 tail 을 담는다 — 다른 호출부와 '강제 전체 재동기화'가
               그 경로를 쓴다.

        원자성: 델타 절단선과 체크리스트/분석/고정을 **같은 락 블록 안에서** 계산한다.
        이 모듈의 존재 이유가 "세 패널이 같은 이벤트에서 파생되므로 갱신 시점이 어긋나면
        '왼쪽 로그에는 보이는데 체크리스트는 정상'인 화면이 나온다"는 것이므로(상단 주석),
        델타화가 그 불변식을 깨서는 안 된다. 섹션 버전도 이 스냅샷에서만 파생시킨다.

        장비별로 다음을 함께 돌려준다:
          line_seq : 이 응답에 담긴 마지막 줄의 seq(없으면 이전 값 유지용으로 0)
          resync   : True 면 프론트엔드는 그 장비의 로그를 통째로 갈아야 한다
                     (버퍼가 maxlen 을 넘겨 클라이언트가 놓친 구간이 생긴 경우)
        """
        with self._lock:
            client_epoch = (since or {}).get("epoch")
            client_seqs = (since or {}).get("devices") or {}
            # epoch 가 다르면(감시 재시작·초기화) 클라이언트 seq 는 의미가 없다 — 전부 다시 보낸다.
            use_delta = since is not None and client_epoch == self._epoch

            hidden_devices = self._filter["hidden_devices"]
            devices = []
            pinned = []
            for device in self._devices:
                if device in hidden_devices:
                    continue
                buf = self._buffers.get(device) or deque()
                all_lines = list(buf)
                newest_seq = all_lines[-1].get("seq", 0) if all_lines else 0
                resync = not use_delta

                if use_delta:
                    since_seq = client_seqs.get(device)
                    if since_seq is None:
                        # 이 장비를 클라이언트가 아직 모른다(감시 중 새로 등장) — 전체를 보낸다.
                        resync = True
                    else:
                        oldest_seq = all_lines[0].get("seq", 0) if all_lines else 0
                        if all_lines and since_seq < oldest_seq - 1:
                            # 클라이언트가 마지막으로 본 뒤로 버퍼가 밀려 나갔다(maxlen 초과).
                            # 그 사이 줄을 되살릴 수 없으므로 통째로 갈아야 한다.
                            resync = True

                if resync:
                    lines = all_lines[-tail:]
                else:
                    since_seq = client_seqs.get(device, 0)
                    lines = [line for line in all_lines if line.get("seq", 0) > since_seq]

                checklist = []
                for item in (self._checklists.get(device) or {}).values():
                    rank = self._pin_rank_locked(device, item["key"])
                    row = dict(item, pinned=rank is not None)
                    # '아직 아무 경고도 없다(pending)'를 '정상'으로 읽히게 두지 않는다 —
                    # 그 항목을 판정할 입력원이 애초에 도착하지 않았을 수 있다. fail/warn/
                    # recovered 는 건드리지 않는다(관측된 사실이므로). 저장하지 않고 읽는
                    # 시점에 파생시키는 이유: syslog 가 들어오기 시작하면 곧바로 풀려야 한다.
                    if row["status"] == "pending":
                        blocked = self._blocked_reason_locked(device, row["key"])
                        if blocked:
                            row["status"] = "unknown"
                            row["detail"] = blocked
                    checklist.append(row)
                    if rank is not None:
                        pinned.append(dict(row, device=device, check_id=item["key"], pin_rank=rank))
                fails = [c for c in checklist if c["status"] == "fail"]
                warns = [c for c in checklist if c["status"] == "warn"]
                devices.append({
                    "device": device,
                    "has_baseline": device in self._baseline_devices,
                    "lines": lines,
                    "line_count": len(buf),
                    "line_seq": newest_seq,
                    "resync": resync,
                    "checklist": checklist,
                    "fail_count": len(fails),
                    "warn_count": len(warns),
                    "status": "fail" if fails else ("warn" if warns else "ok"),
                    "last_activity": self._last_activity.get(device, 0),
                    # 이 장비에서 무엇을 볼 수 있었는지 — 화면이 '정상'의 근거를 밝히는 데 쓴다.
                    "observed": dict(self._observed.get(device) or _EMPTY_OBSERVED),
                })
            # 고정 항목의 check_id가 체크리스트 키(CHECK_ITEMS)가 아닐 수도 있다 —
            # 설정 예시의 'power_status' / 'mlag_peer_problem'처럼 규칙 엔진의 서명 id를
            # 고정할 수 있어야 한다. 위 루프에서 못 찾은 고정 항목을 여기서 채운다.
            # 채우지 않으면 사용자가 고정했는데 화면에 아무것도 안 나타나 '고장'으로 읽힌다.
            matched = {(p["device"], p["check_id"]) for p in pinned}
            for rank, want in enumerate(self._filter["pinned_items"]):
                key = (want.get("device"), want.get("check_id"))
                if key in matched or key[0] in hidden_devices:
                    continue
                pinned.append(self._pin_from_alerts_locked(key[0], key[1], rank))
            pinned.sort(key=lambda p: p["pin_rank"])
            visible = [a for a in self._alerts if not self._is_hidden_locked(a)]
            analysis = self._analyze_locked()
            alerts = list(reversed(visible))[:80]
            filter_copy = _copy_filter(self._filter)

            payload = {
                "epoch": self._epoch,
                "devices": devices,
                "pinned": pinned,
                "alerts": alerts,
                "analysis": analysis,
                "started_at": self._started_at,
                "filter": filter_copy,
                # 우클릭으로 뭘 숨겼는지 화면에 알려주지 않으면 '경고가 안 뜬다'는 오해가 생긴다.
                "hidden_counts": {
                    "devices": len([d for d in self._devices if d in hidden_devices]),
                    "alerts": len(self._alerts) - len(visible),
                },
                # 우클릭 메뉴가 '이 규칙 숨기기'를 제시할 수 있게 지금 등장한 규칙 목록을 넘긴다.
                "seen_rules": sorted({a.get("rule_id") or a.get("type")
                                      for a in self._alerts if (a.get("rule_id") or a.get("type"))}),
                # 감시 품질 — '경고 0건'이 '이상 없음'인지 '아무것도 못 보고 있음'인지 구분한다.
                "watch_quality": self._watch_quality_locked(devices),
            }

            # 섹션 버전 — 프론트엔드가 바뀐 패널만 다시 그리게 한다. 0.8초마다 DOM 을 통째로
            # 갈아치우던 것이 이 항목의 두 번째 비용이었다(그래서 클릭 고정 강조를 매 렌더마다
            # 복원해야 했다). 값은 **이 스냅샷에서만** 파생시킨다 — 다른 시점의 것을 섞으면
            # 세 패널이 어긋난다.
            versions = {
                "analysis": _fingerprint(analysis),
                "checklist": _fingerprint([(d["device"], d["status"], d["fail_count"],
                                            d["warn_count"], d["has_baseline"],
                                            tuple((c["key"], c["status"], c["severity"], c["count"])
                                                  for c in d["checklist"]))
                                           for d in devices]),
                "pinned": _fingerprint(pinned),
                "filter": _fingerprint(filter_copy),
                # alert_id 만으로는 부족하다. resolve_alerts()/ignore_alerts() 는 경고를 지우지
                # 않고 **제자리에서** resolved/ignored 표시만 남기므로(이력이 점검 자료다) id
                # 목록이 그대로다 — id 로만 지문을 내면 '해결됨'이 프론트엔드에 영원히 전달되지
                # 않는다. 지금은 이 패널이 state["alerts"] 를 읽지 않아 증상이 없지만, 읽는 쪽이
                # 생기는 순간 조용히 틀린 화면이 된다.
                "alerts": _fingerprint([(a.get("alert_id"), a.get("severity"), a.get("device"),
                                         bool(a.get("resolved")), bool(a.get("ignored")))
                                        for a in alerts]),
                "devices": _fingerprint([d["device"] for d in devices]),
            }
            payload["versions"] = versions

            if use_delta:
                # 지문이 같은 섹션은 보내지 않는다(None). 프론트엔드는 자기 DOM 을 그대로 둔다.
                # payload 구성 실측(장비 30대): lines 88% / checklist 6.2% / analysis 2.6% /
                # alerts 2.2% — lines 델타가 주효하고, 나머지 섹션 생략이 남은 몫을 줄인다.
                known = (since or {}).get("versions") or {}
                if known.get("analysis") == versions["analysis"]:
                    payload["analysis"] = None
                if known.get("alerts") == versions["alerts"]:
                    payload["alerts"] = None
                if known.get("pinned") == versions["pinned"]:
                    payload["pinned"] = None
                if known.get("filter") == versions["filter"]:
                    payload["filter"] = None
                if known.get("checklist") == versions["checklist"]:
                    # 장비 목록 자체는 남겨야 한다(로그 델타가 거기 실려 있다) — checklist 만 뺀다.
                    for entry in payload["devices"]:
                        entry["checklist"] = None
            return payload

    def _watch_quality_locked(self, devices):
        """'경고 0건'의 뜻을 화면이 구분할 수 있게 하는 요약.

        세 가지가 전혀 다른 상황인데 예전에는 화면에서 똑같이 '이상 징후 없음'이었다:
          * 명령도 syslog 도 들어오고 있고 아무 문제가 없다        -> 진짜 이상 없음
          * 명령은 보이는데 syslog 가 한 줄도 없다                 -> 링크/STP/MLAG 판정 불가
          * 아무 입력도 없다(파일-장비 매칭 실패, 세션 미접속 등)  -> 감시가 안 되고 있다
        """
        names = [d["device"] for d in devices]
        with_syslog = [n for n in names if (self._observed.get(n) or {}).get("syslog")]
        with_command = [n for n in names if (self._observed.get(n) or {}).get("commands")]
        with_poll = [n for n in names if (self._observed.get(n) or {}).get("polled")]
        silent = [n for n in names
                  if not any((self._observed.get(n) or {}).get(k)
                             for k in _OBSERVED_KEYS)]
        return {
            "devices": len(names),
            "syslog_devices": len(with_syslog),
            "command_devices": len(with_command),
            "polled_devices": len(with_poll),
            "polled_device_names": with_poll,
            "silent_devices": silent,
            "syslog_lines": sum((self._observed.get(n) or {}).get("syslog", 0) for n in names),
            # syslog 를 한 줄도 못 본 장비. 상태 폴링이 도는 장비는 그래도 판정이 가능하므로
            # 화면은 두 값을 함께 봐야 한다(polled_device_names).
            "syslog_missing_devices": [n for n in names if n not in with_syslog],
        }

    def alerts(self, device=None, limit=200, include_hidden=True):
        with self._lock:
            items = list(reversed(self._alerts))
            if device:
                items = [a for a in items if a.get("device") == device]
            if not include_hidden:
                items = [a for a in items if not self._is_hidden_locked(a)]
            return items[:limit]

    def clear_alerts(self):
        with self._lock:
            self._alerts = []
            # '초기화'는 이력을 비우는 것이므로, 다음 seed 가 같은 구간을 다시 판정해 채우는 것이
            # 맞다 — 서명 기억까지 지우지 않으면 초기화 후 화면이 영구히 비어 있게 된다.
            self._history_seen = set()
            for device in list(self._checklists):
                self._checklists[device] = self._fresh_checklist(device)
            # 체크리스트/경고가 통째로 바뀌었다 — 프론트엔드가 부분 갱신으로 따라올 수 없으므로
            # epoch 를 올려 다음 폴링에서 전체를 다시 받게 한다.
            self._epoch += 1

    # ---------- 프로파일별 보존(snapshot / restore) ----------
    # 실시간 감시 상태는 프로그램을 껐다 켜도 남아야 한다. 정기점검은 며칠에 걸쳐 이어지고,
    # '어제 이 장비에서 MLAG가 흔들렸다'는 사실이 프로그램 재실행 한 번에 사라지면 점검 근거가
    # 사라지는 것과 같다. 저장 단위는 프로파일이다(고객사/회차마다 장비도 기준도 다르므로).
    def snapshot(self, lines_per_device=200):
        """JSON으로 직렬화 가능한 현재 상태 사본."""
        with self._lock:
            return {
                "version": 1,
                "saved_at": time.time(),
                "devices": list(self._devices),
                "baseline_devices": sorted(self._baseline_devices),
                "checklists": {d: {k: dict(v) for k, v in items.items()}
                               for d, items in self._checklists.items()},
                "alerts": [dict(a) for a in self._alerts],
                "lines": {d: list(buf)[-lines_per_device:] for d, buf in self._buffers.items()},
                "last_activity": dict(self._last_activity),
                "observed": {d: dict(v) for d, v in self._observed.items()},
                "started_at": self._started_at,
            }

    def restore(self, snapshot):
        """저장된 사본을 지금 상태에 얹는다. reset() 직후에 부르는 것을 전제로 한다.

        지금 감시 대상이 아닌 장비의 기록까지 되살리지는 않는다 — 장비목록에서 빼 놓은 장비가
        화면에 유령처럼 남으면 '지금 무엇을 보고 있는지'가 흐려진다.
        복원된 로그 줄은 전부 history로 표시한다(지금 들어온 입력과 구분).
        """
        if not isinstance(snapshot, dict):
            return 0
        with self._lock:
            allowed = set(self._devices)
            restored = 0
            for device in snapshot.get("devices") or []:
                if device not in allowed:
                    continue
                saved_items = (snapshot.get("checklists") or {}).get(device) or {}
                checklist = self._checklists.setdefault(device, self._fresh_checklist(device))
                for key, item in saved_items.items():
                    if key in checklist and isinstance(item, dict):
                        checklist[key] = {**checklist[key], **item, "key": key}
                buf = self._ensure_device(device)
                for line in (snapshot.get("lines") or {}).get(device) or []:
                    if isinstance(line, dict) and line.get("text"):
                        self._line_seq += 1
                        buf.append({"ts": line.get("ts", "--:--:--"),
                                    "text": line["text"], "history": True,
                                    "seq": self._line_seq})
                activity = (snapshot.get("last_activity") or {}).get(device)
                if activity:
                    self._last_activity[device] = activity
                # 지난 실행에서 syslog 를 봤다는 사실도 되살린다 — 안 그러면 프로그램을 다시
                # 켠 직후 전부 '판정 불가'로 보이고, 지난 회차에서 확인한 항목이 되돌아간다.
                saved_obs = (snapshot.get("observed") or {}).get(device)
                if isinstance(saved_obs, dict) and device not in self._observed:
                    self._observed[device] = {k: int(saved_obs.get(k) or 0)
                                              for k in _OBSERVED_KEYS}
                restored += 1
            known = {a.get("alert_id") for a in self._alerts if a.get("alert_id")}
            for alert in snapshot.get("alerts") or []:
                if not isinstance(alert, dict) or alert.get("device") not in allowed:
                    continue
                if alert.get("alert_id") and alert["alert_id"] in known:
                    continue
                # 저장본에 있던 사건의 서명을 등록해 둔다 — 감시를 시작하면 세션 로그의 같은
                # 구간을 seed 로 다시 판정하므로(새 alert_id 로), 서명이 없으면 어제의 사건이
                # 오늘 한 건 더 생긴다.
                self._history_seen.add(_history_signature(alert))
                self._alerts.append(dict(alert, restored=True))
            if len(self._alerts) > self._alert_max:
                del self._alerts[:len(self._alerts) - self._alert_max]
            return restored

    def _quiet_verdict_locked(self, watched):
        """경고가 하나도 없을 때의 문구 — '이상 없음'과 '못 보고 있음'을 가른다.

        예전에는 셋 다 "이상 징후 없음"이었다. 실제 워크스페이스가 세 번째 상태였고(감시 폴더가
        엉뚱한 곳을 보고 있었다) 화면은 5일간 초록색이었다.
        """
        obs = self._observed
        silent = [d for d in self._devices
                  if not any((obs.get(d) or {}).get(k) for k in _OBSERVED_KEYS)]
        no_syslog = [d for d in self._devices if not (obs.get(d) or {}).get("syslog")]

        if len(silent) == watched:
            return ("unknown", "감시 입력 없음 — 아직 로그가 도착하지 않았습니다",
                    f"장비 {watched}대를 감시 대상으로 잡았지만 세션 로그에서 읽은 줄이 없습니다. "
                    "SecureCRT 세션이 열려 있는지, '파일 진단'에서 로그 파일이 장비로 인식되는지 "
                    "확인하세요. 이 상태에서는 '이상 없음'이라고 말할 수 없습니다.")
        if len(no_syslog) == watched:
            return ("unknown", "명령 감시 중 — 링크/STP/MLAG는 판정 불가",
                    f"장비 {watched}대에서 입력된 명령은 감시하고 있지만, syslog가 한 줄도 "
                    "관측되지 않았습니다. 링크 DOWN·라우팅 인접·STP/MLAG 변화는 세션에 syslog가 "
                    "에코돼야만 알 수 있습니다(장비에서 `terminal monitor`). 해당 체크리스트 "
                    "항목은 '정상'이 아니라 '판정 불가'로 남겨 둡니다.")
        detail = (f" 그중 {len(no_syslog)}대는 syslog가 관측되지 않아 링크/STP/MLAG 판정이 "
                  "보류됩니다." if no_syslog else "")
        return ("ok", "이상 징후 없음",
                f"장비 {watched}대의 세션 로그를 감시 중입니다. Baseline과 다른 변경이 감지되면 "
                f"즉시 여기에 표시됩니다.{detail}")

    # ---------- 실시간 오류 분석(우측 상단) ----------
    def _analyze_locked(self):
        """체크리스트/경고를 규칙 기반으로 요약. AI 호출 없이 즉시 계산된다(0.3초 주기 갱신이므로)."""
        counts = {"CRITICAL": 0, "MAJOR": 0, "WARNING": 0}
        by_device = {}
        # 취소된(복구된)/무시한/숨긴 경고는 '지금 조치할 대상'이 아니므로 요약에서 제외한다 —
        # 이력에는 남아 있고, 여기서 세면 no shutdown으로 고친 장애나 사용자가 이미 확인하고
        # 넘어간(무시한) 장애가 계속 CRITICAL로 잡힌다.
        live = [a for a in self._alerts
                if not a.get("resolved") and not a.get("ignored") and not self._is_hidden_locked(a)]
        for alert in live:
            severity = alert.get("severity") or "WARNING"
            if severity in counts:
                counts[severity] += 1
            by_device.setdefault(alert.get("device"), []).append(alert)

        if not live:
            watched = len(self._devices)
            # 전부 복구/무시된 경우와 처음부터 조용한 경우는 다른 이야기다 — 구분해서 알려준다.
            recovered = len([a for a in self._alerts if a.get("resolved")])
            ignored = len([a for a in self._alerts if a.get("ignored")])
            verdict = "ok"
            if recovered or ignored:
                parts = []
                if recovered:
                    parts.append(f"복구 처리 {recovered}건")
                if ignored:
                    parts.append(f"무시 처리 {ignored}건")
                summary = (f"발생했던 경고 중 {', '.join(parts)}가 반영되었습니다. "
                           "이력은 '세부 이력'에서 확인할 수 있습니다.")
                headline = f"이상 없음 — 경고 {recovered + ignored}건 해제됨"
            elif watched:
                verdict, headline, summary = self._quiet_verdict_locked(watched)
            else:
                # '대상이 없다'는 정상이 아니다 — 초록색으로 칠하면 감시가 되는 것처럼 읽힌다.
                verdict = "unknown"
                summary = "장비 목록에서 감시할 장비를 선택하세요."
                headline = "감시 대상 장비 없음"
            return {
                "verdict": verdict,
                "headline": headline,
                "summary": summary,
                "counts": counts,
                "findings": [],
                "findings_dropped": 0,
                "resolved_count": recovered,
                "ignored_count": ignored,
            }

        findings = []
        # 규칙 이름은 장비별 루프 밖에서 한 번만 정한다 — 같은 규칙인데 장비마다 라벨이 갈리면
        # 화면에서 한 규칙이 두 그룹으로 쪼개진다.
        rule_labels = _rule_labels(live)
        for device, alerts in by_device.items():
            device_findings = self._device_findings(device, alerts, rule_labels)
            # 이 장비의 근거가 전부 '지난 세션/지난 실행'에서 온 것이면 그렇게 표시한다 —
            # 방금 들어온 입력으로 난 오류와 구분되지 않으면 지금 대응할 것을 고를 수 없다.
            if alerts and all(a.get("history") or a.get("restored") for a in alerts):
                for f in device_findings:
                    f["from_history"] = True
            for f in device_findings:
                f["category_label"] = _CATEGORY_LABEL.get(f.get("category"), "기타")
            findings.extend(device_findings)
        # 심각도 -> 발생 건수 순
        findings.sort(key=lambda f: (-_SEVERITY_RANK.get(f["severity"], 0), -f["count"]))
        # 상한을 넘겨 잘라낸 몫은 화면이 밝혀야 한다 — 조용히 자르면 "이게 전부"로 읽힌다.
        dropped = max(0, len(findings) - _FINDING_MAX)

        verdict = "fail" if counts["CRITICAL"] else ("warn" if counts["MAJOR"] or counts["WARNING"] else "ok")
        worst_device = max(by_device.items(), key=lambda kv: len(kv[1]))[0] if by_device else None
        headline = (f"CRITICAL {counts['CRITICAL']}건 — 즉시 확인 필요"
                    if counts["CRITICAL"] else
                    f"주의 {counts['MAJOR'] + counts['WARNING']}건 감지")
        recovered = len([a for a in self._alerts if a.get("resolved")])
        ignored = len([a for a in self._alerts if a.get("ignored")])
        summary = (f"미해결 경고 {len(live)}건이 장비 {len(by_device)}대에서 발생했습니다."
                   + (f" 가장 많은 장비는 {worst_device}({len(by_device[worst_device])}건)입니다." if worst_device else "")
                   + (f" 별도로 {recovered}건은 복구되어 해제되었습니다." if recovered else "")
                   + (f" {ignored}건은 사용자가 무시 처리했습니다." if ignored else ""))
        return {
            "verdict": verdict,
            "headline": headline,
            "summary": summary,
            "counts": counts,
            # 화면이 같은 group_key 끼리 묶어 한 줄로 보여주므로, 12개로 자르면 4대짜리 MLAG
            # 그룹 하나가 목록의 3분의 1을 먹고 나머지 오류가 잘려 나간다 — 묶은 뒤의 줄 수가
            # 기준이어야 한다. finding 을 '장비 x 규칙' 단위로 쪼개면서(같은 분류라도 다른
            # 규칙이면 다른 목록 줄이어야 하므로) 개수가 장비 수 x 규칙 수로 늘어 상한도 올렸다.
            "findings": findings[:_FINDING_MAX],
            "findings_dropped": dropped,
            "resolved_count": recovered,
            "ignored_count": ignored,
        }

    def _device_findings(self, device, alerts, rule_labels=None):
        """한 장비의 경고 묶음에서 '원인 추정 + 권고'를 만든다.

        인과 규칙: 설정 삭제/shutdown 명령이 있고 그 뒤에 링크·인접 DOWN이 따라왔다면,
        DOWN을 개별 장애로 나열하는 대신 '작업 명령이 원인'으로 묶어야 화면이 읽힌다.

        모든 finding에 category(체크리스트 항목 키와 같은 축 — vlan/interface/route/neighbor/
        link/stp_mlag/ops)를 붙인다. 오른쪽 상세와 체크리스트가 같은 축으로 이어지는 근거다.

        왼쪽 목록의 묶음 단위는 category가 아니라 **group_key**다. 예전에는 화면이 category로
        묶었는데, 그러면 성질이 다른 오류가 한 줄에 뭉쳤다:
          * 'MLAG peer-link 이상(split-brain)'과 'STP/MLAG 상태 변화'가 둘 다 stp_mlag이라
            한 줄로 합쳐졌다 — 앞은 즉시 조치, 뒤는 확인 대상이라 같이 볼 것이 아니다.
          * 체크리스트에 매핑되지 않는 규칙 경고는 전부 category=None(기타)이어서, '비정상
            재기동'·'인증 실패'·'카운터 증가'가 "기타 / 7대 · 92건" 한 줄로 사라졌다(보고된 증상).
        그래서 finding마다 '어떤 판정에서 나왔는지'를 group_key로 붙이고, 규칙 기반 경고는
        규칙(rule_id/type)까지 내려가 쪼갠다. 같은 group_key면 장비가 몇 대든 한 줄로 묶이고,
        다른 판정이면 절대 합쳐지지 않는다. group_label은 그 줄에 쓸 이름이다.
        """
        rule_labels = rule_labels or {}
        types = [a.get("type") for a in alerts]
        has_config_change = any(t in ("CONFIG_REMOVED", "INTERFACE_SHUTDOWN") for t in types)
        has_down = any(t in ("LINK_DOWN", "NEIGHBOR_DOWN") for t in types)
        out = []
        consumed_ids = set()

        def consume(group):
            for a in group:
                aid = a.get("alert_id")
                if aid:
                    consumed_ids.add(aid)

        # MLAG peer-link 이상은 split-brain으로 직결되므로 다른 무엇보다 먼저 올린다.
        mlag_peer = [a for a in alerts if a.get("type") == "MLAG_PEER_DOWN"]
        if mlag_peer:
            partial = [a for a in alerts if "partial" in (a.get("raw_line", "") or "").lower()]
            out.append({
                "device": device,
                "severity": "CRITICAL",
                "category": "stp_mlag",
                "group_key": "stp_mlag:mlag_peer_link",
                "group_label": "MLAG peer-link 이상",
                "title": "MLAG peer-link 이상 — split-brain(dual-active) 위험",
                "cause": ("peer-link가 끊기면 두 스위치가 서로를 죽은 것으로 보고 각자 active로 동작합니다"
                          + (f" (멤버 포트 {len(partial)}건이 partial로 승격됨)." if partial else ".")),
                "action": "peer-link 물리 경로와 peer-address 도달성을 먼저 확인하세요. "
                          "dual-active 상태에서 설정을 바꾸면 양쪽 설정이 갈라집니다.",
                "count": len(mlag_peer),
                "evidence": [a.get("raw_line", "") for a in (mlag_peer + partial)[:3]],
                "alert_ids": _ids(mlag_peer + partial),
            })
            consume(mlag_peer + partial)

        if has_config_change and has_down:
            trigger = next(a for a in alerts if a.get("type") in ("CONFIG_REMOVED", "INTERFACE_SHUTDOWN"))
            downs = [a for a in alerts if a.get("type") in ("LINK_DOWN", "NEIGHBOR_DOWN")]
            # diff 엔진이 붙인 root_cause를 우선 신뢰한다 — 그쪽은 시간창(90초)까지 봤고,
            # 여기서는 '이 장비에 둘 다 있다'만 아는 상태다.
            hinted = next((a["root_cause"] for a in downs if a.get("root_cause")), None)
            cause = (f"'{hinted['raw_line']}' 실행 {hinted['elapsed_sec']:g}초 뒤 DOWN 로그 "
                     f"{len(downs)}건이 이어졌습니다."
                     if hinted else
                     f"'{trigger.get('raw_line')}' 실행 직후 DOWN 로그 {len(downs)}건이 이어졌습니다.")
            # 원인(설정 변경)과 결과(DOWN) 중 '지금 눈에 보이는 증상' 기준으로 분류한다 —
            # 링크가 끊긴 건지 라우팅 인접이 끊긴 건지에 따라 다른 담당자가 봐야 한다.
            category = "neighbor" if any(a.get("type") == "NEIGHBOR_DOWN" for a in downs) else "link"
            out.append({
                "device": device,
                "severity": "CRITICAL",
                "category": category,
                "group_key": f"{category}:command_outage",
                "group_label": "작업 명령 → 통신 단절",
                "title": "작업 명령이 통신 단절을 유발한 것으로 보입니다",
                "cause": cause,
                "action": "해당 명령을 되돌리거나(no 형태 복구) Baseline 설정과 대조해 즉시 확인하세요.",
                "count": len(alerts),
                "evidence": [a.get("raw_line", "") for a in downs[:3]],
                "root_cause": hinted,
                "alert_ids": _ids(alerts),
            })
            consume(alerts)
        elif has_config_change:
            removed = [a for a in alerts if a.get("type") == "CONFIG_REMOVED"]
            shut = [a for a in alerts if a.get("type") == "INTERFACE_SHUTDOWN"]
            # 삭제된 설정 target(vlan:/route:/interface:/bgp: 등)의 다수결로 분류한다 — shutdown은
            # 전부 interface지만, CONFIG_REMOVED는 vlan/route/interface/bgp 어디서든 온다.
            category = _majority_category(removed) if removed else "interface"
            out.append({
                "device": device,
                "severity": max((a.get("severity") for a in alerts), key=lambda s: _SEVERITY_RANK.get(s, 0)),
                "category": category,
                "group_key": f"{category}:config_change",
                "group_label": f"{_CATEGORY_LABEL.get(category, '기타')} 설정 변경",
                "title": "Baseline 대비 설정 변경 감지",
                "cause": f"설정 삭제 {len(removed)}건, shutdown {len(shut)}건이 입력됐습니다.",
                "action": "의도된 작업인지 확인하고, 계획에 없던 변경이면 원복하세요.",
                "count": len(alerts),
                "evidence": [a.get("raw_line", "") for a in (removed + shut)[:3]],
                "alert_ids": _ids(alerts),
            })
            consume(alerts)
        elif has_down:
            downs = [a for a in alerts if a.get("type") in ("LINK_DOWN", "NEIGHBOR_DOWN")]
            category = "neighbor" if any(a.get("type") == "NEIGHBOR_DOWN" for a in downs) else "link"
            out.append({
                "device": device,
                "severity": "CRITICAL",
                "category": category,
                "group_key": f"{category}:unexplained_down",
                "group_label": "설정 변경 없는 링크/인접 단절",
                "title": "설정 변경 없이 링크/인접이 끊겼습니다",
                "cause": "입력된 변경 명령 없이 DOWN 로그만 발생 — 대향 장비, 광/케이블, 또는 원격 측 작업이 의심됩니다.",
                "action": "대향 장비의 인터페이스 상태와 물리 경로를 확인하세요.",
                "count": len(downs),
                "evidence": [a.get("raw_line", "") for a in downs[:3]],
                "alert_ids": _ids(downs),
            })
            consume(downs)

        destructive = [a for a in alerts if a.get("type") == "DESTRUCTIVE_COMMAND"]
        if destructive:
            out.append({
                "device": device,
                "severity": "CRITICAL",
                "category": "ops",
                "group_key": "ops:destructive_command",
                "group_label": "위험 운영 명령 실행",
                "title": "위험 운영 명령 실행",
                "cause": f"{', '.join(sorted({a.get('raw_line', '') for a in destructive}))}",
                "action": "reload/write erase는 서비스 중단을 유발합니다 — 작업 승인 여부를 즉시 확인하세요.",
                "count": len(destructive),
                "evidence": [a.get("raw_line", "") for a in destructive[:3]],
                "alert_ids": _ids(destructive),
            })
            consume(destructive)

        stp = [a for a in alerts if a.get("type") in ("STP_CHANGE", "MLAG_STATE")]
        if stp:
            out.append({
                "device": device,
                "severity": "MAJOR",
                "category": "stp_mlag",
                "group_key": "stp_mlag:topology_change",
                "group_label": "STP / MLAG 상태 변화",
                "title": "STP / MLAG 상태 변화",
                "cause": f"토폴로지 변경 로그 {len(stp)}건 — 루프 또는 이중화 절체 가능성.",
                "action": "STP 루트/포트 역할과 MLAG peer 상태를 확인하세요.",
                "count": len(stp),
                "evidence": [a.get("raw_line", "") for a in stp[:3]],
                "alert_ids": _ids(stp),
            })
            consume(stp)

        # 위 어느 분류에도 안 걸린 alert — 규칙 엔진(config/log_rules.json)이 낸 경고와,
        # '복구는 됐지만 취소할 장애가 없었던' 정보성 이벤트(no shutdown/Established 등)다.
        # 여기서 안 만들면 카운트에는 잡히는데 목록에는 하나도 안 보이는 "경고 N건인데 이상 없음"
        # 화면이 나온다(실제로 보고된 버그).
        #
        # 묶는 단위는 **규칙(rule_id/type)** 이다. category로 묶었을 때는 체크리스트에 매핑되지
        # 않는 규칙 경고가 전부 category=None 으로 떨어져서, '비정상 재기동'·'인증 실패'·'카운터
        # 증가'가 "기타 / 7대 · 92건" 한 줄로 합쳐졌다 — 목록을 열어 봐야 안에 여러 오류가
        # 섞여 있다는 것을 알 수 있었다. 규칙이 다르면 다른 줄이어야 한다.
        leftover = [a for a in alerts if a.get("alert_id") not in consumed_ids]
        by_rule = {}
        for a in leftover:
            by_rule.setdefault((_item_key_for(a), _rule_key(a)), []).append(a)
        for (category, rule_key), group in by_rule.items():
            worst = max(group, key=lambda a: _SEVERITY_RANK.get(a.get("severity") or "", 0))
            label = rule_labels.get(rule_key) or _humanize_rule(rule_key)
            out.append({
                "device": device,
                "severity": worst.get("severity") or "WARNING",
                "category": category,
                "group_key": f"{category or 'etc'}:rule:{rule_key}",
                "group_label": label,
                "rule_key": rule_key,
                "title": worst.get("message") or label,
                "cause": f"'{label}' 규칙에 걸린 로그 {len(group)}건"
                         + (f" ({_CATEGORY_LABEL.get(category)} 관련)" if category else "")
                         + " — 추적 중이던 장애를 해제한 것이 아니라 그 자체로 새로 관측된 변화입니다.",
                "action": "의도된 작업인지 확인하세요. 계획에 없던 변경이면 원인을 찾으세요.",
                "count": len(group),
                "evidence": [a.get("raw_line", "") for a in group[:3]],
                "alert_ids": _ids(group),
            })
        return out


def _fingerprint(value):
    """JSON 직렬화 가능한 값의 짧은 지문 — 섹션이 바뀌었는지 판정하는 데만 쓴다.

    해시를 쓰는 이유: 프론트엔드가 이전 값을 그대로 들고 비교하려면 그 값을 다시 받아야 하고,
    그러면 payload 를 줄이려는 목적이 사라진다. 짧은 문자열 하나만 주고받으면 된다.
    md5 를 쓰지만 보안 용도가 아니다(변경 감지) — 충돌하면 화면이 한 번 덜 갱신될 뿐이고
    다음 tick 에 값이 또 바뀌면 지문도 바뀐다.
    """
    import hashlib
    import json

    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.md5(encoded.encode("utf-8")).hexdigest()[:16]


def _history_signature(alert):
    """'같은 사건인가'를 판정하는 내용 서명. alert_id 는 프로세스마다 새로 발급되므로 쓸 수 없다.

    ts 는 넣지 않는다 — 되짚어 판정한 시각은 판정을 돌린 시각이라 실행마다 달라진다. 그것을
    서명에 넣으면 중복 제거가 아예 동작하지 않는다.
    """
    return (alert.get("device"), alert.get("type"), alert.get("target"),
            (alert.get("raw_line") or "").strip())


def _ids(alerts):
    """finding에 붙일 alert_id 목록 — '해결/무시' 버튼이 어떤 alert를 대상으로 할지 알아야 한다."""
    return [a.get("alert_id") for a in alerts if a.get("alert_id")]


def _rule_key(alert):
    """이 경고를 낸 판정의 식별자.

    규칙 엔진 경고는 rule_id(예: unexpected_reload)를, Baseline diff 경고는 type(예: LINK_DOWN)을
    갖는다. 화면 우클릭 메뉴('이 규칙 숨기기')와 고정 항목도 같은 두 값을 본다
    (_is_hidden_locked / _pin_from_alerts_locked) — 목록 묶음도 같은 축을 써야 "숨긴 규칙"과
    "목록 한 줄"이 1:1로 대응한다.
    """
    return alert.get("rule_id") or alert.get("type") or "unknown"


def _humanize_rule(rule_key):
    """규칙 식별자를 목록에 쓸 이름으로. 문구(title)를 못 쓰는 경우의 폴백이다."""
    if rule_key.startswith("keyword_"):
        return f"키워드 '{rule_key[len('keyword_'):]}'"
    if rule_key == "counter_nonzero":
        return "카운터 0 초과"
    return rule_key.replace("_", " ")


def _rule_labels(alerts):
    """규칙 식별자 -> 목록에 쓸 이름. 경고 전체를 한 번 훑어 규칙마다 하나로 정한다.

    규칙 엔진의 서명 경고는 message가 그 규칙의 title 그대로라(engine/log_rule_engine.py의
    _match_signature_uncached) id보다 훨씬 읽힌다 — 'unexpected_reload'가 아니라
    '비정상 재기동 이력'. 반면 키워드/syslog 판정은 줄마다 문구가 달라지므로 id를 다듬어 쓴다.

    장비별로 정하지 않고 여기서 한 번에 정하는 이유: 같은 규칙인데 A장비에서는 문구가 하나뿐
    (=문구를 라벨로 쓰고) B장비에서는 여러 개면(=id를 쓰고) 한 규칙이 화면에서 두 줄로 갈린다.
    """
    messages = {}
    for alert in alerts:
        key = _rule_key(alert)
        message = (alert.get("message") or "").strip()
        if key not in messages:
            messages[key] = message
        elif messages[key] != message:
            messages[key] = ""      # 문구가 갈린다 — id 를 쓴다
    return {key: (message[:60] if message else _humanize_rule(key))
            for key, message in messages.items()}


def _item_key_for(alert):
    """alert -> 체크리스트 항목 키. target prefix를 우선 보고, 없으면 type으로 판단."""
    target = alert.get("target") or ""
    prefix = target.split(":", 1)[0]
    if prefix in _TARGET_PREFIX_TO_ITEM:
        return _TARGET_PREFIX_TO_ITEM[prefix]
    return _TYPE_TO_ITEM.get(alert.get("type"))


def _majority_category(alerts):
    """alert 여러 개 중 가장 많이 나온 category(_item_key_for). 없으면 None(=기타)."""
    counts = {}
    for a in alerts:
        cat = _item_key_for(a)
        counts[cat] = counts.get(cat, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


# ---------- 필터 구조 (config/realtime_watch.yaml의 realtime_filter) ----------
# 문자열 비교를 위해 hidden_rules/devices는 set, hidden_keywords는 대문자 set으로 정규화한다.
# pinned_items는 순서가 곧 화면 순서이므로 list를 유지한다.

def _empty_filter():
    return {"hidden_rules": set(), "hidden_devices": set(), "hidden_keywords": set(),
            "pinned_items": []}


def _normalize_filter(cfg):
    cfg = cfg or {}
    pinned = []
    seen = set()
    for item in cfg.get("pinned_items") or []:
        if not isinstance(item, dict):
            continue
        device, check_id = str(item.get("device") or ""), str(item.get("check_id") or "")
        if not device or not check_id or (device, check_id) in seen:
            continue
        seen.add((device, check_id))
        pinned.append({"device": device, "check_id": check_id})
    return {
        "hidden_rules": {str(v) for v in (cfg.get("hidden_rules") or []) if v},
        "hidden_devices": {str(v) for v in (cfg.get("hidden_devices") or []) if v},
        # 키워드는 대소문자 구분 없이 걸러야 한다(로그는 대문자, 사용자 입력은 소문자가 흔하다).
        "hidden_keywords": {str(v).upper() for v in (cfg.get("hidden_keywords") or []) if v},
        "pinned_items": pinned,
    }


def _copy_filter(f):
    """JS로 넘길 수 있게 set을 정렬된 list로 되돌린다(pywebview는 set을 직렬화하지 못한다)."""
    return {
        "hidden_rules": sorted(f["hidden_rules"]),
        "hidden_devices": sorted(f["hidden_devices"]),
        "hidden_keywords": sorted(f["hidden_keywords"]),
        "pinned_items": [dict(p) for p in f["pinned_items"]],
    }


# ---------- 스냅샷 파일 IO ----------
# 프로파일 폴더에 한 파일(realtime_state.json)로 저장한다. 저장이 실패해도 감시는 계속돼야
# 하므로 여기서 예외를 삼키고 False를 돌려준다 — 디스크 문제로 실시간 감시가 멈추면 안 된다.
def save_snapshot(path, snapshot):
    import json
    import os
    from core.atomic_io import write_text_atomic
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_text_atomic(path, json.dumps(snapshot, ensure_ascii=False))
        return True
    except Exception:
        return False


def load_snapshot(path):
    import json
    import os
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        # 저장 중 종료 등으로 깨진 파일 — 없는 것으로 친다(지우지는 않는다, 진단에 쓸 수 있게).
        return None
    return data if isinstance(data, dict) else None
