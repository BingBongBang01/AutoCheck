"""RealtimeRuleStream — 규칙 엔진(config/log_rules.json)을 실시간 스트림에 붙인다.

왜 필요한가: engine/realtime_monitor.py 는 처음부터 '규칙 경고'를 전제로 만들어져 있다 —
경고를 rule_id 로 묶고(_rule_key), 우클릭 '이 규칙 숨기기'가 rule_id 를 보고, 고정 항목의
check_id 로 규칙 서명 id 를 받는다. 그런데 그 rule_id 를 만들어 주는 곳이 실시간 경로에는
없었다. 유일한 공급원이 점검 완료 직후의 1회 배치(api/terminal_inspection_api.py)여서,
config/log_rules.json 의 서명 수십 개가 실시간에서는 사장돼 있었다.

BaselineDiffEngine 과 역할이 다르다:
  * BaselineDiffEngine — **작업자가 입력한 명령**을 Baseline 과 대조한다(무엇을 바꿨나).
  * 이 클래스        — **장비가 출력한 텍스트**를 규칙과 대조한다(장비가 무엇을 말하나).
    'MLAG state: Inactive', '비정상 재기동 이력', '인터페이스 oper down' 처럼 명령 한 줄로는
    알 수 없고 출력을 읽어야 아는 것들이다.

노이즈를 거르는 세 가지 장치(실측으로 정해졌다 — 실제 CRT 세션 로그 2,977줄 기준):
  1. major/critical 만 올린다. minor/info 까지 올리면 `% Invalid input`(오타) 31건,
     '설정모드 진입' 로그 31건이 실시간 목록을 채운다 — 이력에는 이미 남고, 여기는 지금
     조치할 것을 고르는 화면이다.
  2. ContextTracker 가 running-config 구간과 `?` 도움말 출력을 끊는다(is_config / is_help).
     이 둘 없이는 판정 70건 중 66건이 설정 문구와 도움말 사전이었다.
  3. 같은 (장비, 규칙, 문구)는 dedupe 창 안에서 한 번만 올린다. `show mlag` 를 네 번 치면
     같은 'state: Inactive' 가 네 번 들어온다.
"""
import itertools
import re
import threading
import time

from core.ansi_sanitizer import strip_ansi

# 규칙 엔진 severity(소문자) -> 실시간 감시 severity. minor/info 는 기본적으로 올리지 않지만
# (MIN_SEVERITY) 매핑은 남겨 둔다 — 설정으로 문턱을 낮출 수 있어야 한다.
_SEVERITY_MAP = {"critical": "CRITICAL", "major": "MAJOR", "minor": "WARNING", "info": "WARNING"}
_SEVERITY_ORDER = ("info", "minor", "major", "critical")
# 실시간 목록에 올릴 최소 심각도. 낮추면 오타·정상 운영 로그가 함께 올라온다.
MIN_SEVERITY = "major"
# 같은 판정을 접는 시간(초). 조회 명령을 반복해서 치는 것이 흔하므로 diff 엔진(10초)보다 길다.
DEDUPE_WINDOW = 60.0
# 숫자만 다른 같은 줄을 한 판정으로 묶기 위한 정규화(카운터 값이 1씩 오르는 표 등).
_DIGITS_RE = re.compile(r"\d+")


class RealtimeRuleStream:
    """차분 텍스트를 규칙 엔진에 흘려 alert dict 리스트를 만든다.

    반환 alert 는 BaselineDiffEngine 의 것과 같은 모양이라 RealtimeMonitor.apply_alerts()에
    그대로 넣을 수 있다. component_id 는 붙이지 않는다 — 이 판정들은 '복구 이벤트'가 정의되지
    않아(무엇을 보면 해제인지 알 수 없다) StateTracker 로 자동 취소할 수 없고, 사용자가
    '해결/무시'로 닫는다.
    """

    def __init__(self, rule_engine=None, min_severity=MIN_SEVERITY,
                 dedupe_window=DEDUPE_WINDOW, clock=time.time):
        self._engine = rule_engine
        self._min_rank = _SEVERITY_ORDER.index(min_severity)
        self.dedupe_window = dedupe_window
        self._clock = clock
        self._trackers = {}   # {device: ContextTracker} — 세션이 이어지므로 장비마다 유지한다
        self._recent = {}     # {(device, rule_id, 정규화 문구): 마지막 발행 시각}
        self._ids = itertools.count(1)
        self._lock = threading.RLock()

    # ---------- 진입점 ----------
    def analyze(self, device, text):
        """차분 텍스트를 판정해 alert 리스트를 반환. 이상 없으면 빈 리스트."""
        engine = self._rule_engine()
        if engine is None:
            return []
        with self._lock:
            tracker = self._tracker(device)
            out = []
            for raw_line in strip_ansi(text or "").splitlines():
                line = raw_line.rstrip()
                # feed()가 True면 그 줄 자체는 명령/구분선/헤더다(판정 대상 아님).
                if tracker.feed(line):
                    continue
                if not line.strip():
                    continue
                try:
                    verdict = engine.evaluate(line, tracker)
                except Exception:
                    # 규칙 하나가 터져도 감시는 계속돼야 한다.
                    continue
                if not verdict:
                    continue
                alert = self._to_alert(device, line, verdict)
                if alert is not None:
                    out.append(alert)
            return out

    def reset(self, device=None):
        """세션이 끊겼을 때 출력 문맥을 비운다 — 새 세션의 첫 줄이 지난 명령의 출력으로 읽히면 안 된다."""
        with self._lock:
            if device is None:
                self._trackers.clear()
                self._recent.clear()
            else:
                self._trackers.pop(device, None)
                for key in [k for k in self._recent if k[0] == device]:
                    self._recent.pop(key, None)

    # ---------- 내부 ----------
    def _rule_engine(self):
        if self._engine is None:
            try:
                from engine.log_rule_engine import get_engine
                self._engine = get_engine()
            except Exception:
                return None
        return self._engine

    def _tracker(self, device):
        tracker = self._trackers.get(device)
        if tracker is None:
            from engine.log_rule_engine import ContextTracker
            tracker = self._trackers[device] = ContextTracker()
        return tracker

    def _to_alert(self, device, line, verdict):
        severity = (verdict.get("severity") or "info").lower()
        if severity not in _SEVERITY_ORDER or _SEVERITY_ORDER.index(severity) < self._min_rank:
            return None
        rule_id = verdict.get("rule_id") or "rule"
        if not self._accept(device, rule_id, line):
            return None
        return {
            "alert_id": f"{device or 'unknown'}#rule{next(self._ids)}",
            "device": device,
            "severity": _SEVERITY_MAP.get(severity, "WARNING"),
            # type 에도 rule_id 를 넣는다 — 우클릭 '이 규칙 숨기기'와 고정 항목이 rule_id/type
            # 둘 다를 보므로(realtime_monitor 의 _is_hidden_locked / _rule_key) 같은 값이어야
            # '숨긴 규칙'과 '목록 한 줄'이 1:1로 대응한다.
            "type": rule_id,
            "rule_id": rule_id,
            "message": verdict.get("reason") or rule_id,
            "raw_line": line.strip(),
            "target": f"rule:{rule_id}",
            # 상태 추적 대상이 아니다(복구 이벤트가 정의되지 않는다) — 자동 취소되지 않고
            # 사용자가 '해결/무시'로 닫는다.
            "component_id": None,
            "category_tag": verdict.get("category_tag") or "general",
            "ts": time.strftime("%H:%M:%S"),
            "from_rules": True,
        }

    def _accept(self, device, rule_id, line):
        key = (device, rule_id, _DIGITS_RE.sub("#", line.strip())[:160])
        now = self._clock()
        last = self._recent.get(key)
        if last is not None and (now - last) < self.dedupe_window:
            return False
        self._recent[key] = now
        if len(self._recent) > 2000:
            cutoff = now - self.dedupe_window
            self._recent = {k: v for k, v in self._recent.items() if v >= cutoff}
        return True
