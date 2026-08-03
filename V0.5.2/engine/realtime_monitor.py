"""RealtimeMonitor — 실시간 감시 화면(연결 탭 하단)이 그릴 3개 패널의 상태를 한 곳에 모은 저장소.

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

# 체크리스트 항목 정의 — (키, 표시명, 이 항목을 실패로 만드는 alert type들)
CHECK_ITEMS = (
    ("vlan", "VLAN 설정 유지", ("CONFIG_REMOVED",)),
    ("interface", "인터페이스 설정/활성", ("CONFIG_REMOVED", "INTERFACE_SHUTDOWN")),
    ("route", "정적 라우팅 유지", ("CONFIG_REMOVED",)),
    ("neighbor", "라우팅 인접(BGP/OSPF)", ("CONFIG_REMOVED", "NEIGHBOR_DOWN")),
    ("link", "링크 상태", ("LINK_DOWN",)),
    ("stp_mlag", "STP / MLAG 안정", ("STP_CHANGE", "MLAG_STATE")),
    ("ops", "위험 운영 명령 없음", ("DESTRUCTIVE_COMMAND",)),
)

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
    "cmd": "ops",
}
_TYPE_TO_ITEM = {
    "INTERFACE_SHUTDOWN": "interface",
    "LINK_DOWN": "link",
    "LINK_UP": "link",
    "NEIGHBOR_DOWN": "neighbor",
    "NEIGHBOR_UP": "neighbor",
    "STP_CHANGE": "stp_mlag",
    "MLAG_STATE": "stp_mlag",
    "DESTRUCTIVE_COMMAND": "ops",
}
_SEVERITY_RANK = {"WARNING": 1, "MAJOR": 2, "CRITICAL": 3}

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
        self._alert_max = 300

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
            self._started_at = time.time()

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
            }
            for key, label, _types in CHECK_ITEMS
        }

    # ---------- 쓰기 ----------
    def append_lines(self, device, text):
        """CRTStreamWatcher 차분 텍스트를 좌측 로그 패널 버퍼에 넣는다."""
        with self._lock:
            buf = self._ensure_device(device)
            stamp = time.strftime("%H:%M:%S")
            for line in (text or "").splitlines():
                line = line.rstrip()
                if not line.strip():
                    continue
                buf.append({"ts": stamp, "text": line[:_LOG_LINE_MAX]})
            self._last_activity[device] = time.time()

    def apply_alerts(self, alerts):
        """판정된 경고를 체크리스트에 반영하고 이력에 쌓는다."""
        with self._lock:
            for alert in alerts:
                device = alert.get("device")
                self._ensure_device(device)
                item_key = _item_key_for(alert)
                if item_key:
                    self._mark(device, item_key, alert)
                self._alerts.append(alert)
            if len(self._alerts) > self._alert_max:
                del self._alerts[:len(self._alerts) - self._alert_max]

    def _mark(self, device, item_key, alert):
        checklist = self._checklists.setdefault(device, self._fresh_checklist(device))
        item = checklist.get(item_key)
        if item is None:
            return
        severity = alert.get("severity") or "WARNING"
        recovered = alert.get("type") in ("LINK_UP", "NEIGHBOR_UP")
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
    def state(self, tail=120):
        """프론트엔드 폴링용 스냅샷. tail: 장비별로 넘겨줄 최근 로그 줄 수."""
        with self._lock:
            devices = []
            for device in self._devices:
                buf = self._buffers.get(device) or deque()
                lines = list(buf)[-tail:]
                checklist = list((self._checklists.get(device) or {}).values())
                fails = [c for c in checklist if c["status"] == "fail"]
                warns = [c for c in checklist if c["status"] == "warn"]
                devices.append({
                    "device": device,
                    "has_baseline": device in self._baseline_devices,
                    "lines": lines,
                    "line_count": len(buf),
                    "checklist": checklist,
                    "fail_count": len(fails),
                    "warn_count": len(warns),
                    "status": "fail" if fails else ("warn" if warns else "ok"),
                    "last_activity": self._last_activity.get(device, 0),
                })
            return {
                "devices": devices,
                "alerts": list(reversed(self._alerts))[:80],
                "analysis": self._analyze_locked(),
                "started_at": self._started_at,
            }

    def alerts(self, device=None, limit=200):
        with self._lock:
            items = list(reversed(self._alerts))
            if device:
                items = [a for a in items if a.get("device") == device]
            return items[:limit]

    def clear_alerts(self):
        with self._lock:
            self._alerts = []
            for device in list(self._checklists):
                self._checklists[device] = self._fresh_checklist(device)

    # ---------- 실시간 오류 분석(우측 상단) ----------
    def _analyze_locked(self):
        """체크리스트/경고를 규칙 기반으로 요약. AI 호출 없이 즉시 계산된다(0.3초 주기 갱신이므로)."""
        counts = {"CRITICAL": 0, "MAJOR": 0, "WARNING": 0}
        by_device = {}
        for alert in self._alerts:
            severity = alert.get("severity") or "WARNING"
            if severity in counts:
                counts[severity] += 1
            by_device.setdefault(alert.get("device"), []).append(alert)

        if not self._alerts:
            watched = len(self._devices)
            return {
                "verdict": "ok",
                "headline": "이상 징후 없음" if watched else "감시 대상 장비 없음",
                "summary": (f"장비 {watched}대의 세션 로그를 감시 중입니다. Baseline과 다른 변경이 "
                            "감지되면 즉시 여기에 표시됩니다.") if watched
                           else "장비 목록에서 감시할 장비를 선택하세요.",
                "counts": counts,
                "findings": [],
            }

        findings = []
        for device, alerts in by_device.items():
            findings.extend(self._device_findings(device, alerts))
        # 심각도 -> 발생 건수 순
        findings.sort(key=lambda f: (-_SEVERITY_RANK.get(f["severity"], 0), -f["count"]))

        verdict = "fail" if counts["CRITICAL"] else ("warn" if counts["MAJOR"] or counts["WARNING"] else "ok")
        worst_device = max(by_device.items(), key=lambda kv: len(kv[1]))[0] if by_device else None
        headline = (f"CRITICAL {counts['CRITICAL']}건 — 즉시 확인 필요"
                    if counts["CRITICAL"] else
                    f"주의 {counts['MAJOR'] + counts['WARNING']}건 감지")
        summary = (f"경고 {len(self._alerts)}건이 장비 {len(by_device)}대에서 발생했습니다."
                   + (f" 가장 많은 장비는 {worst_device}({len(by_device[worst_device])}건)입니다." if worst_device else ""))
        return {
            "verdict": verdict,
            "headline": headline,
            "summary": summary,
            "counts": counts,
            "findings": findings[:12],
        }

    def _device_findings(self, device, alerts):
        """한 장비의 경고 묶음에서 '원인 추정 + 권고'를 만든다.

        인과 규칙: 설정 삭제/shutdown 명령이 있고 그 뒤에 링크·인접 DOWN이 따라왔다면,
        DOWN을 개별 장애로 나열하는 대신 '작업 명령이 원인'으로 묶어야 화면이 읽힌다.
        """
        types = [a.get("type") for a in alerts]
        has_config_change = any(t in ("CONFIG_REMOVED", "INTERFACE_SHUTDOWN") for t in types)
        has_down = any(t in ("LINK_DOWN", "NEIGHBOR_DOWN") for t in types)
        out = []

        if has_config_change and has_down:
            trigger = next(a for a in alerts if a.get("type") in ("CONFIG_REMOVED", "INTERFACE_SHUTDOWN"))
            downs = [a for a in alerts if a.get("type") in ("LINK_DOWN", "NEIGHBOR_DOWN")]
            out.append({
                "device": device,
                "severity": "CRITICAL",
                "title": "작업 명령이 통신 단절을 유발한 것으로 보입니다",
                "cause": f"'{trigger.get('raw_line')}' 실행 직후 DOWN 로그 {len(downs)}건이 이어졌습니다.",
                "action": "해당 명령을 되돌리거나(no 형태 복구) Baseline 설정과 대조해 즉시 확인하세요.",
                "count": len(alerts),
                "evidence": [a.get("raw_line", "") for a in downs[:3]],
            })
        elif has_config_change:
            removed = [a for a in alerts if a.get("type") == "CONFIG_REMOVED"]
            shut = [a for a in alerts if a.get("type") == "INTERFACE_SHUTDOWN"]
            out.append({
                "device": device,
                "severity": max((a.get("severity") for a in alerts), key=lambda s: _SEVERITY_RANK.get(s, 0)),
                "title": "Baseline 대비 설정 변경 감지",
                "cause": f"설정 삭제 {len(removed)}건, shutdown {len(shut)}건이 입력됐습니다.",
                "action": "의도된 작업인지 확인하고, 계획에 없던 변경이면 원복하세요.",
                "count": len(alerts),
                "evidence": [a.get("raw_line", "") for a in (removed + shut)[:3]],
            })
        elif has_down:
            downs = [a for a in alerts if a.get("type") in ("LINK_DOWN", "NEIGHBOR_DOWN")]
            out.append({
                "device": device,
                "severity": "CRITICAL",
                "title": "설정 변경 없이 링크/인접이 끊겼습니다",
                "cause": "입력된 변경 명령 없이 DOWN 로그만 발생 — 대향 장비, 광/케이블, 또는 원격 측 작업이 의심됩니다.",
                "action": "대향 장비의 인터페이스 상태와 물리 경로를 확인하세요.",
                "count": len(downs),
                "evidence": [a.get("raw_line", "") for a in downs[:3]],
            })

        destructive = [a for a in alerts if a.get("type") == "DESTRUCTIVE_COMMAND"]
        if destructive:
            out.append({
                "device": device,
                "severity": "CRITICAL",
                "title": "위험 운영 명령 실행",
                "cause": f"{', '.join(sorted({a.get('raw_line', '') for a in destructive}))}",
                "action": "reload/write erase는 서비스 중단을 유발합니다 — 작업 승인 여부를 즉시 확인하세요.",
                "count": len(destructive),
                "evidence": [a.get("raw_line", "") for a in destructive[:3]],
            })

        stp = [a for a in alerts if a.get("type") in ("STP_CHANGE", "MLAG_STATE")]
        if stp:
            out.append({
                "device": device,
                "severity": "MAJOR",
                "title": "STP / MLAG 상태 변화",
                "cause": f"토폴로지 변경 로그 {len(stp)}건 — 루프 또는 이중화 절체 가능성.",
                "action": "STP 루트/포트 역할과 MLAG peer 상태를 확인하세요.",
                "count": len(stp),
                "evidence": [a.get("raw_line", "") for a in stp[:3]],
            })
        return out


def _item_key_for(alert):
    """alert -> 체크리스트 항목 키. target prefix를 우선 보고, 없으면 type으로 판단."""
    target = alert.get("target") or ""
    prefix = target.split(":", 1)[0]
    if prefix in _TARGET_PREFIX_TO_ITEM:
        return _TARGET_PREFIX_TO_ITEM[prefix]
    return _TYPE_TO_ITEM.get(alert.get("type"))
