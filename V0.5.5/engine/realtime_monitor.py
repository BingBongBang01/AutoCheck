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
_SEVERITY_RANK = {"WARNING": 1, "MAJOR": 2, "CRITICAL": 3}
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
        self._alert_max = 300
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
            self._started_at = time.time()

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
            for key, label, _types in CHECK_ITEMS
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
                buf.append({"ts": stamp, "text": line[:_LOG_LINE_MAX], "history": is_history})
            if not is_history:
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
    def state(self, tail=120):
        """프론트엔드 폴링용 스냅샷. tail: 장비별로 넘겨줄 최근 로그 줄 수."""
        with self._lock:
            hidden_devices = self._filter["hidden_devices"]
            devices = []
            pinned = []
            for device in self._devices:
                if device in hidden_devices:
                    continue
                buf = self._buffers.get(device) or deque()
                lines = list(buf)[-tail:]
                checklist = []
                for item in (self._checklists.get(device) or {}).values():
                    rank = self._pin_rank_locked(device, item["key"])
                    row = dict(item, pinned=rank is not None)
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
                    "checklist": checklist,
                    "fail_count": len(fails),
                    "warn_count": len(warns),
                    "status": "fail" if fails else ("warn" if warns else "ok"),
                    "last_activity": self._last_activity.get(device, 0),
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
            return {
                "devices": devices,
                "pinned": pinned,
                "alerts": list(reversed(visible))[:80],
                "analysis": self._analyze_locked(),
                "started_at": self._started_at,
                "filter": _copy_filter(self._filter),
                # 우클릭으로 뭘 숨겼는지 화면에 알려주지 않으면 '경고가 안 뜬다'는 오해가 생긴다.
                "hidden_counts": {
                    "devices": len([d for d in self._devices if d in hidden_devices]),
                    "alerts": len(self._alerts) - len(visible),
                },
                # 우클릭 메뉴가 '이 규칙 숨기기'를 제시할 수 있게 지금 등장한 규칙 목록을 넘긴다.
                "seen_rules": sorted({a.get("rule_id") or a.get("type")
                                      for a in self._alerts if (a.get("rule_id") or a.get("type"))}),
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
            for device in list(self._checklists):
                self._checklists[device] = self._fresh_checklist(device)

    # ---------- 실시간 오류 분석(우측 상단) ----------
    def _analyze_locked(self):
        """체크리스트/경고를 규칙 기반으로 요약. AI 호출 없이 즉시 계산된다(0.3초 주기 갱신이므로)."""
        counts = {"CRITICAL": 0, "MAJOR": 0, "WARNING": 0}
        by_device = {}
        # 취소된(복구된) 경고와 숨긴 경고는 '지금 상태'가 아니므로 요약에서 제외한다 —
        # 이력에는 남아 있고, 여기서 세면 no shutdown으로 고친 장애가 계속 CRITICAL로 잡힌다.
        live = [a for a in self._alerts
                if not a.get("resolved") and not self._is_hidden_locked(a)]
        for alert in live:
            severity = alert.get("severity") or "WARNING"
            if severity in counts:
                counts[severity] += 1
            by_device.setdefault(alert.get("device"), []).append(alert)

        if not live:
            watched = len(self._devices)
            # 전부 복구된 경우와 처음부터 조용한 경우는 다른 이야기다 — 구분해서 알려준다.
            recovered = len([a for a in self._alerts if a.get("resolved")])
            if recovered:
                summary = (f"발생했던 경고 {recovered}건이 모두 복구 처리되었습니다. "
                           "이력은 '세부 이력'에서 확인할 수 있습니다.")
                headline = f"이상 없음 — 경고 {recovered}건 자동 해제됨"
            elif watched:
                summary = (f"장비 {watched}대의 세션 로그를 감시 중입니다. Baseline과 다른 변경이 "
                           "감지되면 즉시 여기에 표시됩니다.")
                headline = "이상 징후 없음"
            else:
                summary = "장비 목록에서 감시할 장비를 선택하세요."
                headline = "감시 대상 장비 없음"
            return {
                "verdict": "ok",
                "headline": headline,
                "summary": summary,
                "counts": counts,
                "findings": [],
                "resolved_count": recovered,
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
        recovered = len([a for a in self._alerts if a.get("resolved")])
        summary = (f"미해결 경고 {len(live)}건이 장비 {len(by_device)}대에서 발생했습니다."
                   + (f" 가장 많은 장비는 {worst_device}({len(by_device[worst_device])}건)입니다." if worst_device else "")
                   + (f" 별도로 {recovered}건은 복구되어 해제되었습니다." if recovered else ""))
        return {
            "verdict": verdict,
            "headline": headline,
            "summary": summary,
            "counts": counts,
            "findings": findings[:12],
            "resolved_count": recovered,
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

        # MLAG peer-link 이상은 split-brain으로 직결되므로 다른 무엇보다 먼저 올린다.
        mlag_peer = [a for a in alerts if a.get("type") == "MLAG_PEER_DOWN"]
        if mlag_peer:
            partial = [a for a in alerts if "partial" in (a.get("raw_line", "") or "").lower()]
            out.append({
                "device": device,
                "severity": "CRITICAL",
                "title": "MLAG peer-link 이상 — split-brain(dual-active) 위험",
                "cause": ("peer-link가 끊기면 두 스위치가 서로를 죽은 것으로 보고 각자 active로 동작합니다"
                          + (f" (멤버 포트 {len(partial)}건이 partial로 승격됨)." if partial else ".")),
                "action": "peer-link 물리 경로와 peer-address 도달성을 먼저 확인하세요. "
                          "dual-active 상태에서 설정을 바꾸면 양쪽 설정이 갈라집니다.",
                "count": len(mlag_peer),
                "evidence": [a.get("raw_line", "") for a in (mlag_peer + partial)[:3]],
            })

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
            out.append({
                "device": device,
                "severity": "CRITICAL",
                "title": "작업 명령이 통신 단절을 유발한 것으로 보입니다",
                "cause": cause,
                "action": "해당 명령을 되돌리거나(no 형태 복구) Baseline 설정과 대조해 즉시 확인하세요.",
                "count": len(alerts),
                "evidence": [a.get("raw_line", "") for a in downs[:3]],
                "root_cause": hinted,
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
